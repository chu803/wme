# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/inpainter.py
 文件说明 : 修复引擎总调度。对外只暴露一个核心函数 inpaint()，
            内部根据配置自动选择 LaMa 深度学习模型或 OpenCV 传统算法，
            并统筹「掩码后处理 → 分块/整图推理 → 降噪 → 强度融合」全流程。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from core.device_manager import select_device, use_fp16, empty_cache
from core.image_utils import (
    dilate_mask,
    feather_mask,
    threshold_mask,
    pad_to_multiple,
    crop_padding,
    split_into_tiles,
    merge_tiles,
    rgb_to_tensor,
    mask_to_tensor,
    tensor_to_rgb,
)
from core.model_loader import load_lama_model, is_model_available
from core import plugins as plugin_manager
from core.quality import no_reference_inpaint_score
from utils.exceptions import EmptyMaskError
from utils.logger import get_logger
from utils.progress import ThrottledProgress, make_scoped

log = get_logger("core.inpainter")


# 进度回调类型：callable(progress: 0~1, message: str) -> None
ProgressCallback = Callable[[float, str], None]


def _noop_progress(p: float, msg: str) -> None:
    """默认的空进度回调，什么都不做。"""
    return None


def _prepare_mask(
    mask: np.ndarray,
    dilate: int,
    feather: int,
    thresh: int,
) -> np.ndarray:
    """
    对原始掩码做「膨胀 → 二值化 → 羽化」三段式后处理，得到可用于修复的平滑掩码。

    参数说明：
        mask    : 用户/检测模块给出的原始掩码（H×W，0/255）。
        dilate  : 膨胀像素数（把水印区撑大，吞噬半透明羽化边缘）。
        feather : 高斯羽化核大小（接缝柔化）。
        thresh  : 二值化阈值（决定灰度边缘哪些算水印）。

    返回值：
        浮点掩码（H×W，值域 0~255，带渐变），用于最后的加权融合。

    阶段 105 · 为什么第 4 步要 `max(soft, binary)`（羽化只许「往外加软坡」）
    -------------------------------------------------------------------
    本函数的返回值 `proc_mask` 直接充当融合权重：
        m3 = proc_mask/255 ;  blended = image*(1-m3*s) + result*(m3*s)
    因此 `proc_mask < 255` 的地方，修复只被**按比例施加**。高斯羽化对**细笔画**是
    毁灭性的：核 5（σ≈1.25）作用在 1~2px 宽的笔画上，峰值只剩约 0.32×255≈82，
    于是细字/网格的融合权重掉到 ~0.3 —— 修了，但只修了三成，留下明显残影。
    实测（阶段 105，`scripts/mask_param_audit.py` 口径，edge_text）：
        D=0,F=5 残留 12.72 / 12.64   ← 羽化把细字冲淡
        D=0,F=0 残留  9.09 /  8.48   ← 不羽化反而更干净
        D=2,F=5 残留  9.95 /  8.44   ← 旧默认：靠**膨胀加粗笔画**把羽化的损失补回来
    也就是说：旧默认 `dilate=2` 的唯一收益其实是在给「羽化冲淡内部」打补丁，
    代价是掩码外 2px 全强度重绘（外溢 29.3%、过度重绘像素 2~5 倍）。
    第 4 步把二值核 `binary` 抬回 255 后，羽化只剩「掩码外侧的软坡」这一个作用，
    细字不再被冲淡 ⇒ 膨胀失去存在理由（默认已收敛为 0，见 config.MASK_DILATE_DEFAULT）。
    实测同一批用例 D=0,F=5：残留 9.10/8.48、过度像素 1498/1515、外溢 0.7%。
    注意：这不改变 `strength` 的语义 —— 掩码内现在是「按 strength 全量融合」，
    掩码外是「weight=0 保持原图」，与公开契约一致。
    """
    # 1) 膨胀：先把区域撑大。
    if dilate > 0:
        mask = dilate_mask(mask, iterations=dilate)
    # 2) 二值化：用阈值明确「修复/不修复」边界。
    binary = threshold_mask(mask, thresh=thresh)
    # 3) 羽化：把硬边界变成柔和的灰度过渡（只在外侧产生软坡）。
    soft = feather_mask(binary, ksize=feather)
    # 4) 阶段 105：羽化不得冲淡掩码内部 —— 二值核一律抬回 255。
    return np.maximum(soft, binary.astype(np.float32))


def _inpaint_opencv(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    使用 OpenCV 传统修复算法处理单块图像。

    参数说明：
        image : H×W×3 RGB uint8。
        mask  : H×W 二值掩码（0/255）。

    返回值：
        H×W×3 RGB uint8 修复结果。

    说明：
        OpenCV 的 cv2.inpaint 要求 BGR 输入、掩码为 8 位单通道且值为 255 表示修复区。
        本函数内部完成 RGB↔BGR 转换与边界补齐（inpaint 要求掩码必须是 8 的倍数边）。
    """
    import cv2

    h, w = image.shape[:2]
    # 补齐到 8 的倍数（cv2.inpaint 对极端的尺寸组合有时不稳定，这里保险）。
    pad_h = (8 - h % 8) % 8
    pad_w = (8 - w % 8) % 8
    if pad_h or pad_w:
        image = cv2.copyMakeBorder(image, 0, pad_h, 0, pad_w, cv2.BORDER_REPLICATE)
        mask = cv2.copyMakeBorder(mask, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT, value=0)

    bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    method = cv2.INPAINT_TELEA if config.OPENCV_INPAINT_METHOD == "telea" else cv2.INPAINT_NS
    result = cv2.inpaint(bgr, mask, config.OPENCV_INPAINT_RADIUS, method)
    rgb = cv2.cvtColor(result, cv2.COLOR_BGR2RGB)

    # 裁回原尺寸。
    if pad_h or pad_w:
        rgb = rgb[:h, :w]
    return rgb


def _run_lama_tile(
    model,
    tile_img: np.ndarray,
    tile_mask: np.ndarray,
    use_half: "bool | None" = None,
) -> np.ndarray:
    """
    对单块图像用 LaMa 模型执行一次修复。

    参数说明：
        model     : 已加载的 LaMa torch.jit 模型。
        tile_img  : 子图 H×W×3 uint8 RGB。
        tile_mask : 子掩码 H×W（0/255）。
        use_half  : **兼容参数**。默认 `None` ⇒ 由**模型 dtype 推导**（阶段 207 起，
                    模型 dtype 是唯一真值来源，见下方说明）。显式传入时必须与模型
                    dtype 一致，否则抛 `ValueError`——宁可响亮失败，也不做静默的
                    dtype 错配（那会以 `RuntimeError: expected scalar type ...` 或
                    更糟的数值垃圾形式表现）。

    为什么改为「跟随模型 dtype」（阶段 207 实测）：
        原先这里独立读 `use_fp16()`，与加载侧（`model_manager.load_lama_model`）
        是否 `.half()` 是**两处真值来源**。当加载侧因「fp16 不安全」保持 float32、
        而这里仍按 `use_fp16()` 转 half 时，就会直接 dtype 不匹配。
        ⇒ 让输入跟随模型即可从结构上消除这类漂移。

    返回值：
        修复后的 H×W×3 uint8 RGB 数组。
    """
    import torch

    _mdtype = next(model.parameters()).dtype
    _model_half = _mdtype == torch.float16
    if use_half is not None and bool(use_half) != _model_half:
        raise ValueError(
            f"use_half={use_half} 与模型 dtype={_mdtype} 不一致："
            f"阶段 207 起以模型 dtype 为唯一真值来源，请传 None（或不传）。"
        )
    use_half = _model_half

    # 先把尺寸补齐到 8 的倍数（LaMa 网络结构要求）。
    padded_img, pad = pad_to_multiple(tile_img, config.MODEL_SIZE_ALIGN)
    padded_mask, _ = pad_to_multiple(tile_mask, config.MODEL_SIZE_ALIGN)

    # 转张量：**保持 [0,1] 值域**（LaMa 官方推理约定）。
    #
    # 阶段 A 真实图审计（见 docs/A3-*.md）修正：此处历史上先 `img_t * 2 - 1` 压到
    # [-1,1]、又在输出端 `* 0.5 + 0.5` 反归一化，等于对同一约定做了两次变换。
    # big-lama.pt 的 TorchScript 导出接受 [0,1] 输入并直接输出 [0,1]，双重变换会让
    # 模型在掩码区饱和成纯白（真实图实测 87% 掩码像素 ≥250，接缝色差 169.5），
    # 这也是阶段 51「OpenCV 8/8 全胜」的真实成因——LaMa 一直在被错误调用。
    # 归位后同一张图接缝色差降至 16.7（比原图 37.6 还低），视觉上无缝续接背景。
    # 掩码语义：{0,1}，非 0 表示待修复；无需预先抹掉掩码区像素（D 变体与 B 等价，
    # 见审计脚本），模型自行依据掩码处理。
    #
    # 阶段 D1（GPU 产品化）：张量直接在模型所在设备（CPU / cuda:N）上构造，
    # 不再「先建 CPU 再 .to(dev)」——省掉 GPU 上每 tile 一次多余的 host→device
    # 拷贝，直接服务 P95<1.5s 的时延目标。device_str 由模型参数所在设备推导：
    # CPU → "cpu"；cuda:0 → "cuda:0"，与 rgb_to_tensor/mask_to_tensor 的契约一致。
    dev = next(model.parameters()).device
    device_str = dev.type if dev.index is None else f"{dev.type}:{dev.index}"
    img_t = rgb_to_tensor(padded_img, device_str=device_str)
    mask_t = mask_to_tensor(padded_mask, device_str=device_str)

    if use_half:
        img_t = img_t.half()
        mask_t = mask_t.half()

    with torch.no_grad():
        out = model(img_t, mask_t)  # 输出已在 [0,1]
    # 仅做值域夹紧（模型输出理论上有界，实际偶发越界）。
    out = out.clamp(0, 1)

    # 转回 numpy 并裁掉补的边。
    result = tensor_to_rgb(out)
    result = crop_padding(result, pad)
    return result


def _inpaint_lama_full(
    image: np.ndarray,
    mask: np.ndarray,
    device,
) -> np.ndarray:
    """
    用 LaMa 处理「单块（或整图）」，不做分块。

    说明：
        分块决策已上移到 inpaint() 的 _tile_if_needed()，使分块对 LaMa / OpenCV
        / 任意插件引擎统一生效，且空显存只在整次修复结束后释放一次。
    """
    model = load_lama_model(device)
    # 阶段 207：不再从 `use_fp16()` 取 half 决策——dtype 由**模型**决定
    # （加载侧已按 `_FP16_UNSAFE_MODELS` 判断 LaMa 必须保持 float32）。
    # 这里保持单一真值来源，避免「两处判断不一致」导致的 dtype 错配。
    return _run_lama_tile(model, image, mask)


def _tile_if_needed(
    eng,
    image: np.ndarray,
    mask: np.ndarray,
    device,
    progress: ProgressCallback,
) -> np.ndarray:
    """
    框架级分块调度：当图像长边超过阈值且开启分块时，把图切成 tile，
    对每块调用 eng.inpaint（含「掩码为空即可跳过」的块），再用加权融合拼回。

    设计意图：
        · 分块逻辑从「仅 LaMa 内部」提到框架层，让 OpenCV / 插件引擎处理大图时
          也能分块，避免一次性把超大图塞进显存/内存导致 OOM；
        · 进度在 [0.2, 0.8] 区间按块推进，并通过 ThrottledProgress 节流，
          避免几百块带来几百次 UI 重绘；
        · 拼接复用 core.image_utils.merge_tiles 的余弦权重窗，保证接缝无痕
          （与历史 LaMa 分块结果一致）。

    参数说明：
        eng     : 已通过 has_engine 校验的引擎插件实例。
        image   : H×W×3 原图。
        mask    : H×W 二值掩码（0/255）。
        device  : 设备句柄。
        progress: 进度回调（0~1）。
    """
    h, w = image.shape[:2]
    max_side = max(h, w)
    # 小图：直接整图送入引擎，不做分块。
    if not (config.ENABLE_TILING and max_side > config.INFER_MAX_SIDE):
        return eng.inpaint(image, mask, device=device, progress=progress)

    log.info("图像长边 %d 超过 %d，启用分块处理。", max_side, config.INFER_MAX_SIDE)
    tiles = split_into_tiles(image, mask, tile_size=config.TILE_SIZE, overlap=config.TILE_OVERLAP)
    if len(tiles) <= 1:
        return eng.inpaint(image, mask, device=device, progress=progress)

    throttled = ThrottledProgress(progress, min_interval=config.PROGRESS_THROTTLE_SEC)
    for i, t in enumerate(tiles):
        if t["skip"]:
            # 无掩码区域直接原样保留，省去一次推理。
            t["result"] = t["img"]
        else:
            # 子块进度映射到 [0.2, 0.8]，再次节流，避免每块刷屏。
            sub = make_scoped(throttled, 0.2, 0.8)
            t["result"] = eng.inpaint(t["img"], t["mask"], device=device, progress=sub)
        # 整体进度在 [0.2, 0.8] 区间按块推进。
        throttled(0.2 + 0.6 * (i + 1) / len(tiles), "修复分块")
    merged = merge_tiles(tiles, h, w, overlap=config.TILE_OVERLAP)
    return merged


def _mask_bbox(mask: np.ndarray) -> "tuple[int, int, int, int] | None":
    """返回掩码非零区域的外接框 (y0, y1, x0, x1)（含端点）；全黑返回 None。"""
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    return int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())


def _should_crop(image: np.ndarray, mask: np.ndarray) -> bool:
    """是否值得裁剪到水印包围盒：开关开 + 图够大 + 掩码够小（三者同时满足）。"""
    if not config.INFER_CROP_TO_MASK:
        return False
    h, w = image.shape[:2]
    if max(h, w) < config.INFER_CROP_MIN_SIDE:
        return False
    if mask.size == 0:
        return False
    ratio = float(np.count_nonzero(mask)) / float(mask.size)
    return ratio < config.INFER_CROP_MAX_MASK_RATIO


def _inpaint_cropped(eng, image, mask, device, progress):
    """裁剪到水印包围盒（+margin）后修复，再贴回原图（引擎无关，包在 _tile_if_needed 外）。

    ⚠ 质量边界（实测）：LaMa 对 crop vs 整图有尺寸相关行为，mask 内修复结果略有差异
    （in>10 最高 ~63%，取决于图）。但**margin 环（mask 外、crop 内）保持原样**——
    只把 mask 区贴回，绝不动 margin 环，故 crop 在 mask 外与 full 逐字节一致
    （LaMa 在 mask 外的固有轻微扰动两种模式同量级、crop 不更差）。仅 mask 内修复略有不同。

    由 INFER_CROP_TO_MASK 门控、**默认开**（crop_quality_probe 全量 58 张判决：干净区安全
    58/58 通过、速度中位 1.51x / 2048² ~3.2x）。如需回退 full 设 INFER_CROP_TO_MASK=0。
    """
    h, w = image.shape[:2]
    bb = _mask_bbox(mask)
    if bb is None:
        return _tile_if_needed(eng, image, mask, device=device, progress=progress)
    y0, y1, x0, x1 = bb
    m = config.INFER_CROP_MARGIN
    cy0, cx0 = max(0, y0 - m), max(0, x0 - m)
    cy1, cx1 = min(h, y1 + m + 1), min(w, x1 + m + 1)
    crop_img = image[cy0:cy1, cx0:cx1]
    crop_mask = mask[cy0:cy1, cx0:cx1]
    log.info("裁剪到水印包围盒 %dx%d（占图 %.1f%%，margin=%d）后修复，跳过整图推理。",
             cx1 - cx0, cy1 - cy0, 100.0 * (cx1 - cx0) * (cy1 - cy0) / (w * h), m)
    cropped = _tile_if_needed(eng, crop_img, crop_mask, device=device, progress=progress)
    out = image.copy()
    # 只把被修复的掩码区贴回；margin 环保持原样（与 full 模式逐字节一致）。
    mm = crop_mask > 0
    out[cy0:cy1, cx0:cx1][mm] = cropped[mm]
    return out


def _denoise(result: np.ndarray, mask: np.ndarray, strength: int, only_masked: bool) -> np.ndarray:
    """
    对修复结果做非局部均值降噪，平滑模型生成的细微噪点。

    参数说明：
        result       : H×W×3 修复结果。
        mask         : H×W 二值掩码（0/255）。
        strength     : 降噪强度 0~30（0 表示关闭）。
        only_masked  : True 只处理掩码区；False 处理整图。

    返回值：
        降噪后的 H×W×3 数组。
    """
    if strength <= 0:
        return result
    import cv2

    # OpenCV 降噪接收 BGR。
    bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
    # 参数含义：h, hForColor, templateWindowSize, searchWindowSize。
    denoised = cv2.fastNlMeansDenoisingColored(
        bgr, None, strength, strength, 7, 21
    )
    denoised_rgb = cv2.cvtColor(denoised, cv2.COLOR_BGR2RGB)

    if only_masked:
        # 只在掩码区域用降噪结果，其余保留原修复结果（保护细节）。
        m = (mask > 0).astype(np.float32)[:, :, None]
        return (result.astype(np.float32) * (1 - m) + denoised_rgb.astype(np.float32) * m).astype(np.uint8)
    return denoised_rgb


def _resolve_auto_engine(preferred: str, model_available: bool) -> str:
    """auto 模式下把「偏好」解析为具体引擎名（纯函数，便于单测）。

    规则（阶段 55 立规则，阶段 A 修正依据）：
      · opencv 永远可用（不需要模型），是大多数水印的稳妥选择；
      · lama 仅在模型就绪时可用，**且需要模型权重与 CPU 推理（1024² 单图秒级→十秒级）**；
      · 默认偏好 opencv，但**理由在阶段 A 已被改写**：原注释称「LaMa 在合成数据上
        多数用例起负作用（阶段 51 benchmark）」——该结论已被证伪，阶段 51 测的是
        一个**值域归一化错误**的 LaMa（掩码区饱和成白板，见 `_run_lama_tile` 注释）。
        修复后重跑同一 benchmark：LaMa 3 / OpenCV 4 / 持平 1，全部差距 ≤4.3 dB。
        因此保留 opencv 默认的理由只剩两条**仍然成立**的：延迟与保守性
        （LaMa 是生成式重建，会"脑补"内容；OpenCV 只做局部扩散，不会凭空造物）。
      · 若偏好 lama 但模型缺失，回退 opencv（保证总能修）。
    """
    preferred = (preferred or "opencv").lower()
    if preferred == "lama" and model_available:
        return "lama"
    # 默认 / 回退：opencv（始终可用）
    return "opencv"


def pick_better_result(
    res_a: np.ndarray,
    res_b: np.ndarray,
    score_a: float,
    score_b: float,
    *,
    eps: "float | None" = None,
) -> np.ndarray:
    """双跑择优判定（纯函数，阶段 74，便于单测）。

    规则：无参考质量分（越高越好）分高者胜；**分差 ≤ eps 视为平局，取主引擎 a**
    （保守：不因噪声换引擎）。eps 默认取 config.INPAINT_DUAL_EPS。
    """
    if eps is None:
        eps = config.INPAINT_DUAL_EPS
    if score_b > score_a + eps:
        return res_b
    return res_a


def _resolve_dual_secondary(primary: str, requested: str) -> "str | None":
    """解析双跑的副引擎（阶段 74）：与主引擎相同/未知/模型不可用时返回 None（跳过双跑）。"""
    secondary = (requested or "").lower()
    if not secondary or secondary == primary or secondary == "auto":
        return None
    if not plugin_manager.has_engine(secondary):
        log.warning("双跑择优：副引擎 %s 不存在，跳过双跑。", secondary)
        return None
    eng = plugin_manager.get_engine(secondary)
    if eng is not None and eng.requires_model and not is_model_available():
        log.warning("双跑择优：副引擎 %s 需要模型但模型不可用，跳过双跑。", secondary)
        return None
    return secondary


def _run_engine_once(
    eng_name: str,
    image: np.ndarray,
    bin_mask: np.ndarray,
    device,
    progress: ProgressCallback,
    *,
    strict_setup: bool,
) -> np.ndarray:
    """取引擎并跑一次修复（setup 失败的处理由 strict_setup 决定）。

    strict_setup=True（主引擎）：初始化失败抛 FileNotFoundError（与旧行为一致）；
    strict_setup=False（双跑副引擎）：初始化失败抛出原异常，由调用方捕获后回退主引擎。
    """
    eng = plugin_manager.get_engine(eng_name)
    if eng is None:
        avail = ", ".join(plugin_manager.get_engine_choices())
        raise ValueError(f"不支持的修复引擎：{eng_name}（可用：{avail}）")
    if eng.requires_model:
        try:
            eng.setup()
        except Exception as exc:  # noqa: BLE001
            if strict_setup:
                raise FileNotFoundError(
                    f"引擎 {eng_name} 初始化失败（可能缺少模型文件）：{exc}"
                ) from exc
            raise
    if _should_crop(image, bin_mask):
        return _inpaint_cropped(eng, image, bin_mask, device, progress)
    return _tile_if_needed(eng, image, bin_mask, device=device, progress=progress)


def _match_region_color(
    result: np.ndarray,
    original: np.ndarray,
    bin_mask: np.ndarray,
    ring: int,
) -> np.ndarray:
    """把修复区(result)的逐通道 mean/std 对齐到「掩码周围一圈原图(original)」统计量。

    目的：消 LaMa 填充与周围的亮度/色差接缝（IOPaint `sd_match_histograms` 同款，
    但用单调的 mean/std 仿射而非全直方图 CDF，避免 warp 分布/产生色带，小填充区更稳健）。
    实测（2026-10-17 探针，真实 doubao 图）修复区-周围均值差最大 [1.0,23.2,22.8] → [0.8,0.4,0.6]。

    铁律：只改 bin_mask 内的像素；bin_mask 外与 result 逐字节相同（强度融合随后也只在
    proc_mask 内生效，故 mask 外全程不被本函数触碰）。

    鲁棒性：核心区或参考环像素 <50 时原样返回；某通道近常量（std<1e-3）时跳过该通道。
    """
    import cv2

    core = bin_mask > 0
    if int(core.sum()) < 50:
        return result
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ring + 1, 2 * ring + 1))
    dilated = cv2.dilate(bin_mask, k, iterations=1)
    ref_mask = cv2.bitwise_and(dilated, cv2.bitwise_not(bin_mask))
    if int((ref_mask > 0).sum()) < 50:
        return result
    src = result[core].astype(np.float32)
    ref = original[ref_mask > 0].astype(np.float32)
    out = result.astype(np.float32)
    for c in range(result.shape[2]):
        mu_s, sd_s = float(src[:, c].mean()), float(src[:, c].std())
        mu_r, sd_r = float(ref[:, c].mean()), float(ref[:, c].std())
        if sd_s < 1e-3:
            continue
        corrected = (out[:, :, c] - mu_s) * (sd_r / sd_s) + mu_r
        out[:, :, c] = np.where(core, corrected, out[:, :, c])
    return np.clip(out, 0, 255).astype(np.uint8)


def inpaint(
    image: np.ndarray,
    mask: np.ndarray,
    *,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    engine: Optional[str] = None,
    device=None,
    progress: Optional[ProgressCallback] = None,
    dual_run: "bool | None" = None,
) -> np.ndarray:
    """
    核心修复入口：输入原图与掩码，输出修复后图像。

    参数说明：
        image     : 原图 H×W×3 uint8 RGB。
        mask      : 掩码 H×W（bool / 0-1 / 0-255 任意值域，语义为「0=保留、非0=修复」）。
                    内部把 {0,1} 与 bool 提升到 0/255；灰度软掩码保持原样、
                    由 threshold 决定二值化边界（阶段 102 前 0/1 掩码会被静默清零）。
        strength  : 修复力度 0~1，默认取 config 值。
        dilate    : 掩码膨胀像素，默认 config。
        feather   : 羽化核大小，默认 config。
        threshold : 掩码阈值，默认 config。
        denoise   : 降噪强度，默认 config。
        engine    : 强制引擎 "lama"/"opencv"/"auto"，默认 config。
        device    : 设备句柄，默认自动选择。
        progress  : 进度回调，默认空操作。
        dual_run  : 双跑择优（阶段 74）。None 取 config.INPAINT_DUAL_RUN；
                    True/False 显式覆盖（UI 复选框用）。开启且副引擎可用时，
                    主/副引擎各跑一次并以无参考质量分择优，耗时约翻倍。

    返回值：
        H×W×3 uint8 RGB 修复结果。

    异常：
        EmptyMaskError : 掩码全黑（没有要修复的区域）时抛出。
    """
    progress = progress or _noop_progress

    # ---- 参数回填默认值 ----
    strength = config.INPAINT_STRENGTH_DEFAULT if strength is None else strength
    dilate = config.MASK_DILATE_DEFAULT if dilate is None else dilate
    feather = config.MASK_FEATHER_DEFAULT if feather is None else feather
    threshold = config.MASK_THRESHOLD_DEFAULT if threshold is None else threshold
    denoise = config.DENOISE_STRENGTH_DEFAULT if denoise is None else denoise
    engine = config.INPAINT_ENGINE if engine is None else engine.lower()

    # 把掩码规整成单通道 uint8（兼容 0/1、0/255、0~255 任意输入）。
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    # 阶段 142 修复：先看**原始**掩码的值域上界，再转 uint8。
    #   公开契约（本函数 docstring）声明 mask 为「任意值域」，但 `astype(np.uint8)`
    #   对超过 255 的浮点掩码会**静默回绕**（300.0 → 44、256.0 → 0），回绕值若低于
    #   threshold 就被整片清零 —— 修复退化成**静默空操作**：不抛异常、输出与输入
    #   逐字节相同（阶段 142 实测：float32 全片 300.0 掩码 → 改动 0 px；
    #   256.0 → 误抛 EmptyMaskError 并解释为「掩码为空」，与实际原因不符）。
    #   这里把「值域越界」单独识别：一旦存在 >255 的值，说明调用方用的不是 0~255
    #   灰度口径，而是「非零即修复」或「0~1 之外的自定义标度」，一律按 0/非0 抬到
    #   0/255（与 bool / {0,1} 分支同语义），并留下可见告警说明原因。
    #   真正落在 0~255 的灰度软掩码**不受影响**：仍由 threshold 决定二值化边界。
    raw_max = int(mask.max()) if mask.size else 0
    mask_u8 = mask.astype(np.uint8)
    if raw_max > 255:
        log.warning(
            "掩码存在超过 255 的值（原始最大值 %d，疑似浮点越界或自定义标度）——"
            "已按「非零即修复」统一抬升到 0/255 值域，避免 uint8 回绕导致修复被静默跳过。"
            "若期望灰度软掩码，请把输入归一化到 0~255。",
            raw_max,
        )
        mask_u8 = (mask > 0).astype(np.uint8) * np.uint8(255)
    # 若掩码全黑，没有需要处理的内容。
    if mask_u8.max() == 0:
        raise EmptyMaskError(
            "掩码为空（没有任何需要修复的区域）。\n"
            "请先用画笔涂抹或矩形框选水印区域，或使用智能自动检测模式。"
        )
    # 阶段 102 修复：把 {0,1} / bool / [0,1] 浮点掩码**提升到 0/255 值域**。
    #   公开契约声明「任意值域，0/非0 即可」，但后续 _prepare_mask 会按
    #   MASK_THRESHOLD_DEFAULT（默认 127）二值化 —— 若不先归一化，0/1 掩码会被
    #   阈值整个清零，修复退化成**静默空操作**：不抛异常、输出与输入逐字节相同。
    #   实测（docs/102）：同一图同一掩码，传 0/255 → 改动 110269 px；传 bool → 0 px。
    #   注意：这里只抬 {0,1} 值域，真正的灰度软掩码（存在 >1 的值）保持原样，
    #   仍由 threshold 参数决定二值化边界，语义不变。
    #   阶段 142 补充：`>255` 的越界值域已在上面先行归一（见 raw_max 分支），
    #   所以本分支只会命中真正的 {0,1} / bool 掩码。
    if mask_u8.max() <= 1:
        mask_u8 = (mask_u8 > 0).astype(np.uint8) * np.uint8(255)

    progress(0.05, "正在预处理掩码...")
    proc_mask = _prepare_mask(mask_u8, dilate, feather, threshold)
    # 二值化版本用于 OpenCV / 模型 mask 输入。
    bin_mask = threshold_mask(proc_mask, thresh=threshold)
    # 阶段 102：把「掩码非空、但二值化后全空」这种**静默空操作**变成可见告警。
    #   触发条件只有一种：调用方给的是**灰度软掩码**且全部像素都低于 threshold
    #   （例如 40% 强度的画笔掩码）。此时修复不会有任何改动，以前既不报错也不提示。
    if int(bin_mask.max()) == 0:
        log.warning(
            "掩码经阈值 %d 二值化后为空（输入掩码最大值 %d）——"
            "本次修复不会产生任何改动。若这是有意的软掩码，请调低 threshold；"
            "若应为硬掩码，请确认它已是 0/255 值域。",
            threshold, int(mask_u8.max()),
        )

    # ---- 选择引擎 ----
    if engine == "auto":
        engine = _resolve_auto_engine(config.INPAINT_AUTO_PREFER, is_model_available())
        if engine == "opencv" and not is_model_available():
            log.warning("模型不可用，自动降级为 OpenCV 算法（仅适合细小水印）。")

    # ---- 双跑择优（阶段 74）：默认关闭（部署级放行闸），副引擎解析失败自动跳过 ----
    do_dual = config.INPAINT_DUAL_RUN if dual_run is None else bool(dual_run)
    secondary = _resolve_dual_secondary(engine, config.INPAINT_DUAL_SECONDARY) \
        if do_dual else None
    if do_dual and secondary is None:
        log.info("双跑择优条件不满足（副引擎与主引擎相同或不可用），按单引擎执行。")

    progress(0.2, f"修复引擎：{engine.upper()}"
             + (f" + {secondary.upper()}（双跑择优）" if secondary else ""))

    if device is None:
        device = select_device()

    if secondary:
        # 主/副引擎各跑一次，按无参考质量分择优；副引擎任何失败都回退主引擎单跑。
        try:
            res_primary = _run_engine_once(engine, image, bin_mask, device,
                                           progress, strict_setup=True)
            res_secondary = _run_engine_once(secondary, image, bin_mask, device,
                                             progress, strict_setup=False)
        except Exception as exc:  # noqa: BLE001 - 副引擎失败不拖垮主链路
            log.warning("双跑择优失败（%s），回退主引擎 %s 单跑：%s",
                        exc, engine.upper(), type(exc).__name__)
            result = _run_engine_once(engine, image, bin_mask, device,
                                      progress, strict_setup=True)
        else:
            s_primary = no_reference_inpaint_score(res_primary, image, bin_mask)
            s_secondary = no_reference_inpaint_score(res_secondary, image, bin_mask)
            result = pick_better_result(res_primary, res_secondary,
                                        s_primary, s_secondary)
            winner = (engine if result is res_primary else secondary)
            progress(0.55, f"双跑择优完成：{winner.upper()} 胜出"
                     f"（{s_primary:.3f} vs {s_secondary:.3f}）")
            log.info("双跑择优：%s=%.4f, %s=%.4f → 采用 %s",
                     engine, s_primary, secondary, s_secondary, winner)
    else:
        # 统一从插件注册表取引擎：内建 lama/opencv 也注册在内，自定义插件引擎同样从此取。
        # 这样「修复引擎」成为单一可扩展入口，新增模型无需改动本文件的分派逻辑。
        result = _run_engine_once(engine, image, bin_mask, device,
                                  progress, strict_setup=True)
    # 修复结束统一释放一次显存（避免分块/批量/视频场景里每帧都同步 empty_cache 掉帧）。
    empty_cache()

    progress(0.8, "正在降噪与融合...")
    # 降噪。
    result = _denoise(result, bin_mask, denoise, config.DENOISE_ONLY_MASKED)

    # 修复区颜色/亮度匹配（2026-10-17）：把 result 在掩码内的逐通道 mean/std 对齐到
    # 周围原图环，消亮度/色差接缝。仅改掩码内（mask 外逐字节不变），可逆开关见 config。
    if config.INPAINT_COLOR_MATCH:
        result = _match_region_color(
            result, image, bin_mask, config.INPAINT_COLOR_MATCH_RING
        )

    # 力度融合：在掩码区域把「原图」与「修复结果」按 strength 混合。
    m3 = (proc_mask.astype(np.float32) / 255.0)[:, :, None]
    blended = (
        image.astype(np.float32) * (1 - m3 * strength)
        + result.astype(np.float32) * (m3 * strength)
    )
    blended = np.clip(blended, 0, 255).astype(np.uint8)

    progress(1.0, "修复完成")
    return blended


# 自检：构造一张带中央黑块的图，验证 opencv 引擎能正常跑通。
if __name__ == "__main__":
    # 触发 OpenCV 可用性检查（缺失时给出明确报错，而非让下游 cv2.error 兜底）。
    __import__("cv2")

    # 用例 1：小图 opencv 修复（走「不分块」路径）。
    test = np.full((300, 300, 3), 200, dtype=np.uint8)
    test[120:180, 120:180] = 50  # 一块明显的水印
    m = np.zeros((300, 300), dtype=np.uint8)
    m[120:180, 120:180] = 255

    out = inpaint(test, m, engine="opencv", strength=1.0, denoise=0)
    # 修复后中央块应不再是 50（被周围 200 填充）。
    assert out[150, 150, 0] != 50, "OpenCV 修复未生效（小图）"
    print("inpainter.py 自检通过(1/2)：OpenCV 引擎可正常修复小图水印区域。")

    # 用例 2：超大图 opencv 修复（触发「分块」路径），中心水印仍应被填掉。
    BIG = 2500
    big = np.full((BIG, BIG, 3), 200, dtype=np.uint8)
    c0, c1 = BIG // 2 - 30, BIG // 2 + 30
    big[c0:c1, c0:c1] = 50
    mb = np.zeros((BIG, BIG), dtype=np.uint8)
    mb[c0:c1, c0:c1] = 255

    out_t = inpaint(big, mb, engine="opencv", strength=1.0, denoise=0)
    assert out_t[BIG // 2, BIG // 2, 0] != 50, "OpenCV 分块修复未生效"
    # 关闭分块再跑一次，中心同样应被填（验证分块与不分块行为一致）。
    old_tiling = config.ENABLE_TILING
    config.ENABLE_TILING = False
    try:
        out_n = inpaint(big, mb, engine="opencv", strength=1.0, denoise=0)
    finally:
        config.ENABLE_TILING = old_tiling
    assert out_n[BIG // 2, BIG // 2, 0] != 50, "OpenCV 不分块修复未生效"
    print("inpainter.py 自检通过(2/2)：超大图分块 / 不分块均能修复水印区域。")
