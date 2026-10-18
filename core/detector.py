# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/detector.py
 文件说明 : 智能水印自动检测模块（算法层 / core）。
            负责在「不借助人工涂抹」的前提下，自动定位图片中的水印区域，
            并生成单通道 uint8（0/255）掩码，交给 core.inpainter.inpaint() 修复。

            本模块覆盖三类典型水印：
              · 半透明水印   : 叠加在图片上的半透明 LOGO / 平铺文字，
                               通过「残差 + 局部归一化阈值」定位。
              · 角落文字LOGO : 位于图片四角或正中的图标/文字，
                               优先用 OCR 取框，降级用 MSER/连通域取框。
              · 边缘小字     : 贴在图片上下左右边缘的一行小字（域名、参数条等），
                               用水平/垂直投影 + OCR 取框。

            设计要点：
              1. 检测本质是「用图像特征规则去猜哪里是水印」，不可能 100% 准确，
                 因此检测结果只是「建议掩码」，界面上必须允许用户预览并手动修正。
              2. 三类策略各自独立开关，互不依赖；任意一项失败都不会拖垮整体。
              3. 大图自动降采样后再检测（水印是低频结构，降采样不影响定位），
                 掩码再最近邻放大回原图尺寸，兼顾速度与精度。
              4. OCR（EasyOCR）为可选依赖：装了精度更高，没装自动降级为
                 OpenCV 原生检测，全程不报错。

            本模块只依赖 numpy + OpenCV，不依赖 torch / Gradio，可独立测试。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
            本工具专用于去除使用者本人生成的 AI 图片上的可见水印 / 署名标识；
            严禁用于清除他人版权作品的水印或署名。
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

import config
from utils.exceptions import DetectionError
from utils.logger import get_logger
from core.detection_presets import get_preset

log = get_logger("core.detector")


# ==============================================================================
# 第 1 节：延迟依赖加载（cv2 / EasyOCR）
# ------------------------------------------------------------------------------
# 说明：把重依赖放在函数内部导入，避免在「仅做掩码逻辑单测」时强依赖；
#       同时让 UI 层能在没有 torch 的环境里正常 import 本模块。
# ==============================================================================

def _require_cv2():
    """
    延迟导入 cv2。

    返回值：
        cv2 模块对象。

    异常：
        ImportError : 未安装 OpenCV 时抛出中文提示。
    """
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "缺少 OpenCV 依赖（cv2）。请先执行：pip install -r requirements.txt"
        ) from exc
    return cv2


# OCR 阅读器缓存（EasyOCR 初始化很重，避免重复加载）。
_OCR_READER = None
_OCR_AVAILABLE = None  # None=尚未探测；True/False=探测结果


def _ocr_available() -> bool:
    """
    探测 EasyOCR 是否可用（仅探测一次，结果缓存）。

    返回值：
        True 表示 easyocr 包已安装且 DETECT_USE_OCR 开启，可启用 OCR 增强。
    """
    global _OCR_AVAILABLE
    if _OCR_AVAILABLE is not None:
        return _OCR_AVAILABLE
    if not config.DETECT_USE_OCR:
        _OCR_AVAILABLE = False
        return False
    try:
        import easyocr  # type: ignore  # noqa: F401
        _OCR_AVAILABLE = True
    except Exception as exc:  # 任何异常都视为不可用（包未装 / 版本不兼容）
        log.info("EasyOCR 不可用，文字检测将降级为 OpenCV 方案：%s", exc)
        _OCR_AVAILABLE = False
    return _OCR_AVAILABLE


def _get_ocr_reader():
    """
    获取（并缓存）EasyOCR 阅读器实例。

    返回值：
        EasyOCR Reader 对象；若不可用返回 None。

    设备选择（阶段 220）
    --------------------
    此前这里把 `gpu=False` **硬编码**，注释理由是「与本项目默认 CPU 一致」。
    **该前提早已不成立**：`DEVICE_MODE=auto` 下 LaMa 已在 cuda:0 上跑，
    而本模块的 OCR 仍留在 CPU —— 同一条流水线里「一半吃 GPU、一半纯 CPU」，
    实测 OCR 段占全链路 13.9s（阶段 217/218 归因）。

    现改为**跟随 `core.device_manager.select_device()`**（`auto` 语义：探测不到就
    退回 CPU），并带**三重安全回退**，任何一步失败都只降级、绝不中断检测：
        1. `DETECT_OCR_USE_GPU=False` → 直接 CPU；
        2. 设备探测 / `select_device` 抛异常 → CPU；
        3. `gpu=True` 初始化失败（显存不足 / 算子不兼容）→ **再试一次 CPU**。

    为什么不用 `select_device("cuda")`：那是「强制 CUDA」语义，**无卡时会抛
    RuntimeError**（见 device_manager）。OCR 是**可选增强**通路（关掉只影响文字
    召回，不影响整体检测），绝不能因没 GPU 就让整条检测挂掉。

    精度影响（阶段 219 实测）：GPU 与 CPU 的**包围盒逐位相同**（⇒ 掩码不变）；
    识别置信度有浮点路径差异，但**均高于 `DETECT_OCR_MIN_CONFIDENCE`、判定不翻转**。
    且本项目 OCR 的价值在「定位」而非「识别」（见 `_ocr_boxes_in_patch` 注释）。
    """
    global _OCR_READER
    if not _ocr_available():
        return None
    if _OCR_READER is None:
        try:
            import easyocr  # type: ignore

            want_gpu = bool(getattr(config, "DETECT_OCR_USE_GPU", True))
            if want_gpu:
                try:
                    from core.device_manager import select_device

                    handle = select_device()  # auto 语义：无卡自动退 CPU，不抛
                    want_gpu = getattr(handle.info, "kind", "cpu") == "cuda"
                except Exception as exc:
                    log.info("OCR 设备探测失败，回退 CPU：%s", exc)
                    want_gpu = False

            if want_gpu:
                try:
                    _OCR_READER = easyocr.Reader(
                        ["en", "ch_sim"], gpu=True, verbose=False
                    )
                    log.info("EasyOCR 阅读器初始化完成（GPU 模式）。")
                except Exception as exc:
                    # GPU 不可用（显存不足 / 算子不兼容）→ 回退 CPU，绝不因此中断检测。
                    log.warning("OCR 的 GPU 初始化失败，回退 CPU：%s", exc)
                    _OCR_READER = easyocr.Reader(
                        ["en", "ch_sim"], gpu=False, verbose=False
                    )
                    log.info("EasyOCR 阅读器初始化完成（CPU 模式）。")
            else:
                # 仅检测英文 + 简体中文。
                # 注：此处 gpu=False 是**显式声明**（配置要求 CPU 或探测不到 GPU），
                # 而不再是「想当然的默认」。
                _OCR_READER = easyocr.Reader(
                    ["en", "ch_sim"], gpu=False, verbose=False
                )
                log.info("EasyOCR 阅读器初始化完成（CPU 模式）。")
        except Exception as exc:
            log.warning("EasyOCR 初始化失败，文字检测降级：%s", exc)
            _OCR_READER = None
    return _OCR_READER


def warm_up_ocr(blocking: bool = False) -> None:
    """
    预热 EasyOCR 阅读器，消除「首个检测请求」的冷启动卡顿（阶段 238）。

    背景：EasyOCR Reader 首次初始化在 GPU 模式下约需数秒（实测 ~5s，见
    `.scratch/probe_recommend_cost.py` 日志「EasyOCR 阅读器初始化完成（GPU 模式）」）。
    这笔成本在**每个新进程的首个 `detect_watermark_mask` 调用**时支付——对长驻服务
    （Gradio :7860 / REST :7861）而言，等于「第一个用户请求要干等数秒」。

    本函数把成本**前移到服务启动期**（后台线程），让首个真实请求不再被阻塞。
    设计约束（与项目铁律一致）：
      · 输出中性：仅预填充 `_OCR_READER` 缓存，不改变任何检测逻辑/结果；
      · 幂等：已初始化则 `_get_ocr_reader` 直接返回缓存，重复调用零副作用；
      · 失败无害：OCR 是可选增强通路，任何异常都被 `_get_ocr_reader` 内部吞掉
        （降级为 None），绝不中断启动或检测；
      · 非阻塞默认：服务启动用 `blocking=False`，把 ~5s 初始化丢到守护线程，
        调用方立即返回、端口照常监听；CLI 单图场景不调用（首图自然支付，行为不变）。

    参数：
        blocking : True 同步等待初始化完成；False（默认）丢到守护线程后台跑。
    """
    # 与 _get_ocr_reader 同闸门：OCR 总开关关闭时直接 no-op，绝不尝试加载。
    if not config.DETECT_USE_OCR:
        return
    if blocking:
        try:
            _get_ocr_reader()
        except Exception as exc:  # noqa: BLE001 - 兜底，理论上 _get_ocr_reader 已吞异常
            log.debug("OCR 预热（同步）异常（不影响主流程）：%s", exc)
        return
    t = threading.Thread(target=_get_ocr_reader, name="ocr-warmup", daemon=True)
    t.start()


# ==============================================================================
# 第 2 节：检测结果数据结构
# ==============================================================================

@dataclass
class DetectionRegion:
    """
    单个被检测到的水印候选区域。

    字段说明：
        x, y, w, h : 在原图坐标系下的包围盒（左上角 + 宽高，单位像素）。
        kind        : 区域类别，取值见 KIND_* 常量。
        confidence  : 置信度（0~1），由各类策略自行给出，仅用于排序/展示。
    """
    x: int
    y: int
    w: int
    h: int
    kind: str
    confidence: float = 1.0
    # 阶段 82：该区域由几个源碎片聚合而成（1 = 未聚合）。
    # 聚合区域的包围盒是「成员碎片」的并集框，中心点可能落在成员之间的空隙上
    # （不在掩码内），因此合并过滤阶段对这类区域改用 anchors 逐点判活。
    merged_from: int = 1
    anchors: Optional[List[Tuple[int, int]]] = None
    # 阶段 P1：全幅平铺水印的「全帧区域」标记 —— bbox 覆盖整幅（供门禁区域匹配 /
    # 产品提示「水印铺满全图」），但掩码默认仍为稀疏水印像素（避免整图重绘毁图）。
    # 合并过滤阶段对该类区域豁免「中心点须在掩码内」与面积上下限。
    full_frame: bool = False

    def as_tuple(self) -> Tuple[int, int, int, int]:
        """返回 (x, y, w, h) 元组，方便外部绘制。"""
        return (self.x, self.y, self.w, self.h)


# 区域类别常量。
KIND_TRANSLUCENT = "translucent"   # 半透明水印
KIND_CORNER_LOGO = "corner_logo"   # 角落文字/LOGO
KIND_EDGE_TEXT = "edge_text"       # 边缘小字
KIND_OCR_TEXT = "ocr_text"         # OCR 直接识别出的文字框
KIND_GRID = "grid_watermark"       # 网格状平铺水印（阶段 22 新增）
KIND_GRADIENT = "gradient_watermark"  # 全局渐变叠加水印（阶段 22 新增）
KIND_LOGO = "logo_watermark"       # 实体 LOGO 轮廓水印（阶段 23 新增）


@dataclass
class DetectionStats:
    """
    检测「命中率」统计（阶段 22 新增），供界面展示与评估。

    字段说明：
        mask_pixels   : 最终掩码中的非零像素数。
        coverage_ratio: 掩码像素占整图比例（0~1）。
        region_count  : 保留的区域总数。
        by_kind       : 各区域类别（KIND_*）的数量统计。
        used_ocr      : 本次检测是否实际启用了 OCR。
    """
    mask_pixels: int = 0
    coverage_ratio: float = 0.0
    region_count: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    used_ocr: bool = False


@dataclass
class DetectionResult:
    """
    一次完整检测的返回结果。

    字段说明：
        mask    : 合并后的单通道 uint8 掩码（0/255），尺寸与原图一致。
        regions : 检测到的各候选区域列表（已过滤面积，按类别标注）。
        used_ocr: 本次检测是否实际启用了 OCR（用于界面提示）。
        stats   : 检测命中率统计（阶段 22 新增，可能为空）。
    """
    mask: np.ndarray
    regions: List[DetectionRegion] = field(default_factory=list)
    used_ocr: bool = False
    stats: Optional[DetectionStats] = None


def compute_detection_stats(
    mask: np.ndarray,
    regions: List[DetectionRegion],
    area_total: int,
    used_ocr: bool,
) -> DetectionStats:
    """
    根据最终掩码与区域列表计算「命中率」统计（纯 numpy，无 gradio / torch 依赖）。

    参数说明：
        mask      : (H, W) uint8 0/255 掩码。
        regions   : 保留的区域列表。
        area_total: 整图像素面积（H*W）。
        used_ocr  : 本次是否用了 OCR。

    返回值：
        DetectionStats。
    """
    pixels = int(np.count_nonzero(mask > 0)) if mask is not None else 0
    by_kind: Dict[str, int] = {}
    for r in regions:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    return DetectionStats(
        mask_pixels=pixels,
        coverage_ratio=(pixels / area_total) if area_total > 0 else 0.0,
        region_count=len(regions),
        by_kind=by_kind,
        used_ocr=used_ocr,
    )


# ==============================================================================
# 第 3 节：通用工具函数
# ==============================================================================

def _ensure_rgb(image: np.ndarray) -> np.ndarray:
    """
    把任意形态的输入统一成 (H, W, 3) uint8 RGB 数组。

    参数说明：
        image : 可能是 (H,W,3) / (H,W,4) / (H,W) 的 uint8 数组。

    返回值：
        (H, W, 3) uint8 数组。

    异常：
        DetectionError : 输入形态非法时抛出。
    """
    cv2 = _require_cv2()
    if image is None or image.size == 0:
        raise DetectionError("输入图像为空，无法检测。")
    arr = image.astype(np.uint8)
    if arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    if arr.ndim == 3:
        c = arr.shape[2]
        if c == 4:
            # 去掉透明通道（检测基于可见 RGB）。
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
        if c == 3:
            return arr
    raise DetectionError(f"无法识别的图像维度：ndim={arr.ndim}，shape={arr.shape}。")


def _components_to_regions(
    binary: np.ndarray,
    kind: str,
    min_area: int,
    max_area: int,
    pad: int = 0,
) -> List[DetectionRegion]:
    """
    把一张二值掩码里的连通域转成 DetectionRegion 列表，并按面积过滤。

    参数说明：
        binary    : (H, W) uint8 二值图（0/255）。
        kind      : 这些区域统一标注的类别。
        min_area  : 小于该像素面积的连通域直接丢弃（过滤噪点）。
        max_area  : 大于该像素面积的连通域丢弃（防止误把整图当水印）。
        pad       : 每个包围盒向外扩展的像素数（让掩码更宽松包裹水印）。

    返回值：
        DetectionRegion 列表。
    """
    cv2 = _require_cv2()
    # 形态学开运算：去掉面积极小的噪点连通域。
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)

    num, _, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    regions: List[DetectionRegion] = []
    for i in range(1, num):  # 0 号是背景，跳过。
        x, y, w, h, area = stats[i]
        if area < min_area or area > max_area:
            continue
        px = max(0, x - pad)
        py = max(0, y - pad)
        pw = w + 2 * pad
        ph = h + 2 * pad
        regions.append(
            DetectionRegion(
                x=px, y=py, w=pw, h=ph, kind=kind,
                confidence=min(1.0, 0.4 + area / max_area),
            )
        )
    return regions


def _regions_to_mask(
    regions: List[DetectionRegion], h: int, w: int
) -> np.ndarray:
    """
    把区域列表画成一张 (H, W) 的 0/255 掩码。

    参数说明：
        regions : 区域列表。
        h, w    : 目标掩码高宽。

    返回值：
        (H, W) uint8 掩码。
    """
    mask = np.zeros((h, w), dtype=np.uint8)
    for r in regions:
        x2 = min(w, r.x + r.w)
        y2 = min(h, r.y + r.h)
        if r.x >= x2 or r.y >= y2:
            continue
        mask[r.y:y2, r.x:x2] = 255
    return mask


def _evidence_is_degenerate(evidence: np.ndarray) -> bool:
    """证据场退化 ⇔ 全图一个证据像素都没有（判据在本图上不可用）。

    用途：`_refine_regions_to_mask` 的前置守卫。若整幅图都取不到证据，说明是
    「证据判据看不见」（水印比 `DETECT_MIN_DELTA` 还淡），而不是「图上没有水印」——
    此时必须回退整框填充，否则会静默漏检。
    """
    return (
        evidence is None
        or getattr(evidence, "size", 0) == 0
        or not np.any(evidence > 0)
    )


def _refine_regions_to_mask(
    regions: List[DetectionRegion],
    evidence: np.ndarray,
    h: int,
    w: int,
    pad: int = 2,
) -> Tuple[np.ndarray, int]:
    """
    框内「像素级二次确认」：只取区域框里的**有证据像素**当掩码（阶段 103）。

    为什么需要它
        只有区域框、没有形状掩码的策略（角落 LOGO、边缘小字）此前退化为
        `_regions_to_mask` —— **整框填满**。但文字/标识在框内是稀疏的：一个
        「文字行」的包围盒里，笔画像素往往只占 5%~15%。实测（10 张真实底图 ×
        4 类合成水印，逐策略隔离，见 `%TEMP%/align_probe.py`）：
        `edge_text` 通路在 4 类上分别涂 10.0 / 6.8 / 9.0 / 7.0% 图面，而
        mask% ≈ bbox% ⇒ 几乎全是矩形填充，精确率仅 0.009~0.166。
        这就是「检测框」与「水印实体」之间缺失的那一步对齐。

    与 `_components_mask` 的分工
        `_components_mask` 处理的是策略**自己已有候选二值图**的情况（按连通域真实
        形状填充，而非包围盒）；本函数处理策略**没有候选图**的情况——此时退而用
        全局证据场（与半透明通路同一套 `_residual_fields` + `_evidence_gate`）
        在框内做二次确认。两者互补，不重叠。

    参数说明：
        regions  : 区域列表（坐标须已是目标尺度）。
        evidence : 证据二值图（0/255），尺度须与 h/w 一致。
        h, w     : 输出掩码高宽。
        pad      : 证据形状外的膨胀半径（覆盖抗锯齿/羽化边缘）；0 = 不膨胀。

    返回值：
        (mask, dropped) —— mask 为 (H, W) uint8 0/255；
        dropped 为「框内无任何证据、被整框丢弃」的区域数（供调用方记录）。
    """
    cv2 = _require_cv2()
    mask = np.zeros((h, w), dtype=np.uint8)
    dropped = 0
    for r in regions:
        y0, x0 = max(0, r.y), max(0, r.x)
        y1, x1 = min(h, r.y + r.h), min(w, r.x + r.w)
        if y1 <= y0 or x1 <= x0:
            continue
        hit = evidence[y0:y1, x0:x1] > 0
        if not hit.any():
            # 框内一点证据都没有 ⇒ 这个框没有实体支撑，整框丢弃（不涂任何像素）。
            dropped += 1
            continue
        sub = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
        sub[hit] = 255
        if pad > 0:
            dk = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (2 * pad + 1, 2 * pad + 1)
            )
            sub = cv2.dilate(sub, dk, iterations=1)
        mask[y0:y1, x0:x1] |= sub
    return mask, dropped


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """
    对二值掩码做孔洞填充（避免水印内部镂空导致修复不彻底）。

    做法：给掩码加 1px 黑色边框，从 (0,0) 灌水（floodFill），
    凡是「灌水到不了」的像素即为被掩码包围的孔洞，直接置 255。

    参数说明：
        mask : (H, W) uint8 0/255 掩码。

    返回值：
        填充后的掩码。
    """
    cv2 = _require_cv2()
    padded = np.pad(mask, 1, mode="constant", constant_values=0)
    flood = padded.copy()
    mask_flood = np.zeros(
        (padded.shape[0] + 2, padded.shape[1] + 2), dtype=np.uint8
    )
    cv2.floodFill(flood, mask_flood, (0, 0), 255)
    flood = flood[1:-1, 1:-1]  # 去掉边框，回到原尺寸
    filled = mask.copy()
    # flood==0 处即「被掩码包围、外部灌水到不了」的孔洞。
    filled[flood == 0] = 255
    return filled


# ==============================================================================
# 第 3.5 节：阶段 49 —— 绝对证据门限工具集
# ------------------------------------------------------------------------------
# 背景：修复前半透明 / 平铺 / 网格三条通路都用「分位数」取阈值，导致无论图上
#       有没有水印都恒定选中固定比例像素（sensitivity=0.55 时恒为 46.8%），
#       检测器因此完全丧失「判定无水印」的能力。
#
# 本小节提供三个可复用原语：
#   · _residual_fields : 一次算齐残差场，消除三处重复实现；
#   · _robust_sigma    : MAD 稳健噪声估计（对水印污染不敏感）；
#   · _evidence_gate   : 「绝对幅度 ∧ 相对显著」双条件证据门限；
#   · _components_mask : 按连通域真实形状生成掩码（替代包围盒填充）。
# ==============================================================================

def _residual_fields(
    rgb: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    计算半透明检测共用的残差场（阶段 49 抽取，消除三处重复实现）。

    原理：半透明水印是对原图的低频平滑叠加 I_obs = (1−α)·I_bg + α·W。
    用中值滤波近似 I_bg（中值会抹掉水印这类局部结构、保留大块背景），
    残差 I_obs − median(I_obs) 即近似 α·W，呈现水印形状。

    参数说明：
        rgb : (H, W, 3) uint8 RGB 数组。

    返回值：
        (residual_abs, signed_norm, gray) ——
        residual_abs : (H, W) float32，绝对残差（灰阶单位）；
        signed_norm  : (H, W) float32，局部归一化后的带符号残差（无量纲）；
        gray         : (H, W) float32 灰度图。
    """
    cv2 = _require_cv2()
    h, w = rgb.shape[:2]
    # 注意：cv2.medianBlur 要求 uint8 输入，故先取 uint8 灰度，再转浮点运算。
    gray_u8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = gray_u8.astype(np.float32)

    # 中值核：约短边的 1/24，下限 15，取奇数。
    ksize = max(15, min(h, w) // 24)
    ksize = ksize + 1 if ksize % 2 == 0 else ksize
    bg = cv2.medianBlur(gray_u8, ksize).astype(np.float32)
    residual = gray - bg

    # 局部归一化：用 boxFilter 估局部均值与均值平方 → 局部方差 → 局部标准差。
    # 除以局部标准差可抑制纹理丰富区域的误报，只保留「相对周边异常」的叠加结构。
    block = max(15, min(h, w) // 16)
    block = block + 1 if block % 2 == 0 else block
    local_mean = cv2.boxFilter(gray, ddepth=-1, ksize=(block, block))
    local_sq = cv2.boxFilter(gray * gray, ddepth=-1, ksize=(block, block))
    local_var = np.clip(local_sq - local_mean * local_mean, 0.0, None)
    local_std = np.sqrt(local_var) + 1e-3

    return np.abs(residual), residual / local_std, gray


def _robust_sigma(signed_norm: np.ndarray) -> float:
    """
    用 MAD（中位绝对偏差）稳健估计归一化残差的噪声标准差。

    为什么用 MAD 而不是 std：
        标准差会被水印像素本身抬高（水印也是「离群值」），形成自我 masking——
        水印越强，门槛越高，最终反而检测不到。MAD 基于中位数，可容忍接近 50%
        的污染，因此 σ 始终反映「背景噪声」而非「水印强度」。

    参数说明：
        signed_norm : (H, W) float32 归一化带符号残差。

    返回值：
        正的 float 噪声标准差估计（最小 1e-6，避免除零）。
    """
    finite = signed_norm[np.isfinite(signed_norm)]
    if finite.size == 0:
        return 1e-6
    med = float(np.median(finite))
    mad = float(np.median(np.abs(finite - med)))
    # 1.4826 是正态分布的 MAD → σ 换算系数。
    return 1.4826 * mad + 1e-6


def _evidence_gate(
    residual_abs: np.ndarray,
    signed_norm: np.ndarray,
    min_delta: float,
    k_sigma: float,
) -> np.ndarray:
    """
    「绝对幅度 ∧ 相对显著」双条件证据门限（阶段 49 核心）。

    两个条件各自堵住一类失效模式，缺一不可：
      (1) 绝对幅度 |残差| ≥ min_delta（灰阶）
          堵住「平滑图 σ≈0」的失效：纯渐变/纯色背景理论上残差为 0，
          但浮点噪声会让相对判据把微小涨落当成多倍 σ。
      (2) 相对显著 |归一化残差| ≥ k_sigma × 稳健σ
          堵住「纹理丰富图」的失效：纹理本身残差很大，必须用图自身的
          噪声尺度来衡量，才不会把整片纹理误判为水印。

    参数说明：
        residual_abs : 绝对残差（灰阶）。
        signed_norm  : 归一化带符号残差。
        min_delta    : 最小可感知灰阶差。
        k_sigma      : 相对显著倍数。

    返回值：
        (H, W) uint8 二值图（0 / 255）。
    """
    sigma = _robust_sigma(signed_norm)
    passed = (residual_abs >= float(min_delta)) & (
        np.abs(signed_norm) >= float(k_sigma) * sigma
    )
    return passed.astype(np.uint8) * 255


def _suppress_natural_light(
    cand: np.ndarray,
    residual_abs: np.ndarray,
    scale: float,
) -> np.ndarray:
    """
    阶段 82 · 自然光影守卫：抑制「平滑过渡型」误报候选（F1 收敛）。

    背景（docs/A2 真实图门禁 F1）：translucent 通路在真实照片上把自然光影
    （水面反光、云层、树叶、渐变天空）判成半透明水印，占真实图误报 92.5%。
    根因是「局部对比度残差」判据无法区分「人工半透明叠加」与「自然明暗过渡」。

    区分特征（30 张真实图 + 合成门禁用例双口径标定，docs/82）：
    **人工叠加的边缘锐利，自然光影的过渡平滑**。对每个候选连通域测：
        · edge_sharp = 残差场 Sobel 梯度在「候选像素 ∪ 膨胀 2px」上的 P90
          （绝对锐度，随水印振幅走）。**刻意不在 bbox 全域取分位**——全域
          分位依赖「边缘像素占比」，跃变像素 <10% 时统计量恒为 0，对大而
          稀疏的框式水印结构性失效（阶段 82 单元测试抓出过该缺陷）；
        · ratio      = edge_sharp / 候选像素 mean|residual|（相对锐度，与振幅
          解耦——专为保护低振幅但边缘干净的合成水印，如极淡平铺文字）。
    保留条件 = edge_sharp ≥ EDGE×scale OR ratio ≥ RATIO；未通过者整块置 0。

    在证据门限之后、连通域过滤之前调用，保证掩码与区域始终一致。

    参数说明：
        cand         : (H, W) uint8 候选二值掩码（0/255）。
        residual_abs : (H, W) float32 绝对残差场。
        scale        : max(h, w) / 640（阈值按 640 等效尺度锚定，随图缩放）。

    返回值：
        抑制后的候选掩码（新数组）。
    """
    if not config.DETECT_TRANSLUCENT_NATURAL_GUARD:
        return cand
    cv2 = _require_cv2()
    num, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    t_edge = float(config.DETECT_NATURAL_GUARD_EDGE) * float(scale)
    t_ratio = float(config.DETECT_NATURAL_GUARD_RATIO)
    h, w = cand.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    # 阶段 211（性能）：循环内**只收集保留的连通域编号**，不做像素写回。
    # 旧写法 `out[labels == i] = 255` 是 O(keep × H × W)：真实图 area≥16 的候选
    # 实测达 1589 个，2048² 全图比较 + 写回合计占本守卫 63% 耗时（语句级实测
    # cmp 45.2% + assign 18.2%），是检测阶段（1.45s）的最大单点瓶颈。
    # 改为「编号 → 值」LUT 一次查表：O(H × W) 一次遍历，输出**逐位等价**
    # （同为 uint8 0/255；实测 np.array_equal 为 True，见 tests/test_perf_guard_rewrite.py）。
    keep_ids: list = []
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        if area < 16:
            continue  # 过小的交给既有 min_area 过滤，不值得测特征。
        x1, y1 = min(w, x + bw), min(h, y + bh)
        crop = residual_abs[y:y1, x:x1]
        if crop.size == 0:
            continue
        comp = (labels[y:y1, x:x1] == i).astype(np.uint8)
        sel = cv2.dilate(comp, kernel) > 0  # 候选像素 ∪ 2px 邻域
        gx = cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3)
        gmag = np.sqrt(gx * gx + gy * gy)
        edge_sharp = float(np.percentile(gmag[sel], 90))
        # ratio 分母 = bbox 全域均值（刻意不用候选像素均值）：bbox 含空隙稀释，
        # 「锐利边缘 / 稀疏振幅」的比值会把低振幅但边缘干净的稀疏水印
        # （如极淡平铺文字，ratio≈10.8）与平滑光影（ratio≈3~7）区分开；
        # 若用候选像素均值，极淡平铺 ratio 掉到 4.5，会落进光影区间被误杀。
        inner_mean = float(crop.mean())
        if edge_sharp >= t_edge or edge_sharp / max(inner_mean, 1e-6) >= t_ratio:
            keep_ids.append(i)
    kept = len(keep_ids)
    dropped = num - 1 - kept
    if dropped > 0:
        log.debug("自然光影守卫：抑制 %d/%d 个平滑候选块（edge<%.0f 且 ratio<%.1f）。",
                  dropped, num - 1, t_edge, t_ratio)
    if not keep_ids:
        return np.zeros_like(cand)
    lut = np.zeros(num, dtype=np.uint8)
    lut[np.asarray(keep_ids, np.intp)] = 255
    return lut[labels]


def _suppress_texture_like(
    cand: np.ndarray,
    residual_abs: np.ndarray,
) -> np.ndarray:
    """
    阶段 84 · 纹理稠密守卫：抑制「处处高频」的候选（真实图 F1 残余收敛）。

    背景（docs/82 遗留缺口）：自然光影守卫按「边缘是否锐利」放行，但真实照片上的
    **锐利自然纹理**（树叶、枝干、草地、织物、砖墙）边缘本身就锐利 → 整片纹理仍
    通过 → 占真实图残余 FP 的大头（每图约 60 个区域，且多为「实心大块」）。

    区分特征（30 张真实图 + 合成双口径标定，docs/84）：**水印是结构化图形，
    纹理是处处高频**。对每个候选连通域，在「候选像素 ∪ 膨胀 2px」上计算残差场
    梯度的**稠密度**：

        density = #{p : gmag(p) > SHARE × mean(gmag)} / #p

      · 水印笔画内部平坦、只在轮廓处陡变 → 梯度集中在少数像素 → density 低；
      · 自然纹理处处都是梯度 → density 高。

    density 是**比值**，与残差振幅、图像尺度、分辨率全部解耦（不使用任何绝对量）
    ——这正是它优于「梯度均值」类判据的地方，也是它不需要 `scale` 参数的原因。

    与自然光影守卫的关系：**正交且串联**。前者管「平不平滑」（过渡 vs 陡变），
    后者管「稠不稠密」（处处高频 vs 集中）。一个候选必须**同时**通过两者才保留。

    在自然光影守卫之后、连通域过滤之前调用，保证掩码与区域始终一致。

    参数说明：
        cand         : (H, W) uint8 候选二值掩码（0/255）。
        residual_abs : (H, W) float32 绝对残差场。

    返回值：
        抑制后的候选掩码（新数组）。
    """
    if not config.DETECT_TRANSLUCENT_TEXTURE_GUARD:
        return cand
    cv2 = _require_cv2()
    t_density = float(config.DETECT_TEXTURE_GUARD_DENSITY)
    t_share = float(config.DETECT_TEXTURE_GUARD_SHARE)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    h, w = cand.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    # 阶段 211（性能）：同自然光影守卫——循环内只收集编号，LUT 一次查表写回，
    # 消除 O(keep × H × W) 的全图比较（逐位等价，见 tests/test_perf_guard_rewrite.py）。
    keep_ids: list = []
    for i in range(1, num):
        x, y, bw, bh, area = stats[i]
        x1, y1 = min(w, x + bw), min(h, y + bh)
        crop = residual_abs[y:y1, x:x1]
        if crop.size == 0:
            continue
        comp = (labels[y:y1, x:x1] == i).astype(np.uint8)
        sel = cv2.dilate(comp, kernel) > 0  # 候选像素 ∪ 2px 邻域
        gx = cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3)
        gmag = np.sqrt(gx * gx + gy * gy)
        g_sel = gmag[sel]
        gmean = float(g_sel.mean())
        if gmean <= 1e-6:
            # 残差完全平坦：无梯度可分，交给既有面积过滤，本守卫不表态。
            keep_ids.append(i)
            continue
        density = float((g_sel > t_share * gmean).mean())
        if density <= t_density:
            keep_ids.append(i)
    kept = len(keep_ids)
    dropped = num - 1 - kept
    if dropped > 0:
        log.debug("纹理稠密守卫：抑制 %d/%d 个稠密候选块（density>%.2f）。",
                  dropped, num - 1, t_density)
    if not keep_ids:
        return np.zeros_like(cand)
    lut = np.zeros(num, dtype=np.uint8)
    lut[np.asarray(keep_ids, np.intp)] = 255
    return lut[labels]


def _sensitivity_to_ksigma(sensitivity: float) -> float:
    """
    把「敏感度」滑块（0~1）映射成相对显著倍数 k_sigma（阶段 49）。

    修复前 sensitivity 控制的是分位数，语义扭曲：调低敏感度不是「更严格」，
    而是「只保留分数最高的前 X%」，在根本没有水印的图上照样选出固定比例像素。
    现在改为直接控制统计门限，语义回归直觉：敏感度越高 → 门限越低 → 越灵敏。

    映射公式：k_sigma = DETECT_SIGMA_K × (1.5 − sensitivity)
      · sensitivity = 0.5（多数预设的取值区间）→ k = DETECT_SIGMA_K（锚点值 2.5）
      · sensitivity = 1.0 → k = 0.5×锚点（1.25，极激进）
      · sensitivity = 0.0 → k = 1.5×锚点（3.75，极保守）

    参数说明：
        sensitivity : 0~1 的敏感度（越界会被裁剪）。

    返回值：
        正的 float 相对显著倍数。
    """
    s = max(0.0, min(1.0, float(sensitivity)))
    return max(0.5, config.DETECT_SIGMA_K * (1.5 - s))


def _periodic_strength(
    signed_norm: np.ndarray,
    exclude: int = 8,
) -> Tuple[float, int, int]:
    """
    用二维 FFT 自相关度量「平铺周期性」（阶段 49 新增）。

    原理：规则平铺水印会让残差场在平铺周期处出现强自相关峰；而纹理、噪声的
    自相关从 lag=0 起单调衰减，在远离原点处不形成显著峰值。
    用 FFT 计算 acf = IFFT(|FFT(x)|²) 可一次性得到全部 lag，开销仅 O(N log N)。

    为什么不用「二值图行/列投影的一维自相关」（阶段 23 的做法）：
        阈值化后的二值图丢掉了幅度信息，其行/列投影对随机散点也会给出很高的
        相关值——实测干净纹理图达 0.55~0.60（阈值 0.35），几乎无区分度。
        连续残差场保留了幅度，同批数据上干净图仅 0.043~0.052、真实网格水印
        0.119~0.188，分离度约 2.3~4.4 倍。

    参数说明：
        signed_norm : (H, W) float32 归一化带符号残差（连续场，非二值）。
        exclude     : 排除原点附近该半径内的 lag（lag=0 恒为峰值，无意义）。

    返回值：
        (peak, period_y, period_x) ——
        peak      : 归一化自相关峰值（判定「是否存在平铺结构」用）；
        period_y  : 纵向周期，沿 acf[:, 0] 单独取峰得到；
        period_x  : 横向周期，沿 acf[0, :] 单独取峰得到。
        无有效峰时返回 (0.0, 0, 0)。

    为什么周期要沿两轴分别求，而不是直接用二维峰的位置：
        可分离图案 g(x,y)=f(x)·f(y) 的自相关也可分离，于是
            acf(56, 0) = a·1 = a   而   acf(56, 56) = a·a = a²
        因 a<1，二维峰必然落在坐标轴上（实测为 (0, 56)，而非真实的 (56, 56)）。
        若直接拿二维峰当周期去折叠，只能折出一维（11 个 tile，增益 √11），
        而沿两轴分别求可得到 (56, 56) → 121 个 tile，增益 √121 = 11 倍。
    """
    field = np.nan_to_num(signed_norm, nan=0.0, posinf=0.0, neginf=0.0)
    x = field - float(field.mean())
    denom = float(np.sum(x * x))
    if denom <= 1e-12:
        return 0.0, 0, 0

    # rfft2 / irfft2：实输入的对称优化，省一半计算量与内存。
    spec = np.fft.rfft2(x)
    acf = np.fft.irfft2(spec * np.conj(spec), x.shape) / denom

    h, w = acf.shape
    # 自相关关于原点对称，只需考察半个平面（含正 lag）。
    half_h, half_w = max(1, h // 2), max(1, w // 2)
    sub = acf[:half_h, :half_w]
    if sub.size == 0:
        return 0.0, 0, 0
    # 屏蔽原点附近，避免把 lag≈0 的必然高相关当成周期峰。
    masked = np.array(sub, dtype=np.float64, copy=True)
    masked[:exclude, :exclude] = -np.inf
    if not np.isfinite(masked).any():
        return 0.0, 0, 0
    peak = float(np.max(masked))

    # 沿两轴分别求周期：自相关关于原点对称，acf[:, 0] 即「纯纵向 lag」的
    # 相关曲线，acf[0, :] 即「纯横向 lag」的相关曲线。
    period_y = _axis_peak(acf[:, 0], exclude, half_h)
    period_x = _axis_peak(acf[0, :], exclude, half_w)
    return peak, period_y, period_x


def _axis_peak(curve: np.ndarray, exclude: int, hi: int) -> int:
    """
    在一维自相关曲线上取「首个显著峰」对应的 lag（供折叠用）。

    参数说明：
        curve   : 一维自相关曲线（index 即 lag）。
        exclude : 跳过该半径内的 lag（原点附近的必然高相关）。
        hi      : 考察的最大 lag。

    返回值：
        峰所在的 lag；无有效峰时返回 0。
    """
    if hi <= exclude:
        return 0
    win = np.asarray(curve[exclude:hi], dtype=np.float64)
    if win.size == 0 or not np.isfinite(win).any():
        return 0
    return int(np.nanargmax(win)) + exclude


def _fold_template(
    field: np.ndarray,
    period_y: int,
    period_x: int,
) -> Tuple[np.ndarray, int]:
    """
    按检测到的周期把残差场折叠成一个 tile 模板（阶段 49 新增）。

    原理：规则平铺水印在每个 tile 里的形态一致，而噪声在 tile 之间互不相关。
    把 N 个 tile 对齐叠加求平均，信号相干叠加（幅度不变）、噪声随机相消
    （幅度降为 1/√N），于是模板的信噪比相对单 tile 提升 √N 倍。
    这个增益是平铺水印唯一可用的「免费午餐」——单个 tile 常常弱到无法与
    纹理区分，但几十个 tile 一起平均后就清楚了。

    参数说明：
        field    : (H, W) float32 连续残差场。
        period_y : 纵向周期（像素）；<=0 表示纵向无周期，此时不做纵向折叠。
        period_x : 横向周期（像素）；<=0 表示横向无周期，此时不做横向折叠。

    返回值：
        (template, n_tiles) —— template 为 (ty, tx) float32 模板，
        n_tiles 为参与平均的 tile 总数（用于估算增益）。
    """
    h, w = field.shape
    ty = period_y if period_y and period_y > 0 else h
    tx = period_x if period_x and period_x > 0 else w
    ty = int(max(1, min(h, ty)))
    tx = int(max(1, min(w, tx)))

    ny = max(1, h // ty)
    nx = max(1, w // tx)
    # 只取整数个完整 tile，避免边缘的不完整 tile 把均值拉偏。
    sub = field[: ny * ty, : nx * tx]
    template = sub.reshape(ny, ty, nx, tx).mean(axis=(0, 2))
    return template.astype(np.float32), int(ny * nx)


def _tile_out(
    template_mask: np.ndarray,
    h: int,
    w: int,
) -> np.ndarray:
    """
    把一个 tile 掩码按周期平铺回整幅尺寸（阶段 49 新增）。

    参数说明：
        template_mask : (ty, tx) uint8 0/255 的单个 tile 掩码。
        h, w          : 目标尺寸。

    返回值：
        (h, w) uint8 掩码。
    """
    ty, tx = template_mask.shape[:2]
    reps_y = h // ty + 1
    reps_x = w // tx + 1
    tiled = np.tile(template_mask, (reps_y, reps_x))
    return tiled[:h, :w]


def _components_mask(
    binary: np.ndarray,
    min_area: int,
    max_area: int,
    h: int,
    w: int,
    dilate: int = 0,
) -> np.ndarray:
    """
    按连通域「真实形状」生成掩码（阶段 49 新增，替代包围盒填充）。

    为什么要替代 _regions_to_mask：
        包围盒填充会把一个斜向笔画的矩形外框整块涂黑，外框面积可能是笔画自身
        的数十倍。实测候选密度 46.8% 时，包围盒填充后的覆盖率被放大到 93.8%。
        本函数只填连通域自身像素，再按需向外膨胀，精确率显著提升。

    参数说明：
        binary   : (H, W) uint8 二值候选图（0/255）。
        min_area : 小于该像素面积的连通域丢弃（滤噪点）。
        max_area : 大于该像素面积的连通域丢弃（防整图被吞）。
        h, w     : 输出掩码尺寸（通常与 binary 一致）。
        dilate   : 膨胀半径（像素），让掩码略宽松地包裹水印边缘。

    返回值：
        (H, W) uint8 掩码（0/255）。
    """
    cv2 = _require_cv2()
    # 开运算：先抹掉面积极小的噪点连通域。
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)

    num, labels, stats, _ = cv2.connectedComponentsWithStats(
        cleaned, connectivity=8
    )

    # 收集通过面积过滤的连通域编号（0 号是背景，跳过）。
    keep = [
        i
        for i in range(1, num)
        if min_area <= int(stats[i, cv2.CC_STAT_AREA]) <= max_area
    ]

    mask = np.zeros((h, w), dtype=np.uint8)
    if not keep:
        return mask
    # 逐个把「连通域自身像素」置 255，而非其包围盒。
    # 阶段 211（性能）：LUT 查表一次等价替代「循环内逐域全图比较」。
    # 旧写法 mask[labels == i] 是 O(keep × H × W)；本函数是所有掩码生成的必经
    # 路径，真实图 keep 常达数千 ⇒ 单此一行即可吃掉检测阶段大量时间。
    # 输出逐位等价（同为 uint8 0/255；实测 np.array_equal 为 True）。
    lut = np.zeros(num, dtype=np.uint8)
    lut[np.asarray(keep, np.intp)] = 255
    mask = lut[labels]

    if dilate > 0:
        dk = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * dilate + 1, 2 * dilate + 1)
        )
        mask = cv2.dilate(mask, dk, iterations=1)
    return mask


# ==============================================================================
# 第 4 节：策略一 —— 半透明水印检测
# ------------------------------------------------------------------------------
# 原理：半透明水印本质是对原图做了一个「低频平滑叠加」：
#         I_obs = (1 - α) · I_bg + α · W        （α 为透明度，W 为水印图）
#       对 I_obs 做一次强中值滤波可近似得到 I_bg（中值会抹掉水印这种
#       局部结构、保留大块背景），于是：
#         residual = I_obs - median(I_obs)
#       在「有水印」的区域内，residual 近似等于 α·W，呈现水印的形状。
#       再对 residual 做「局部归一化」（除以局部标准差），可抑制纹理丰富
#       区域的误报，只保留「相对周边异常平滑却持续存在」的叠加结构。
# ==============================================================================

def _min_area_px_floor(min_area_ratio: float, relax: float = 1.0) -> int:
    """把「相对面积门限」锚定成参考分辨率下的绝对像素下限（阶段 57）。

    背景：各策略的 min_area = 占比 × 整图面积，纯相对量会随输入变小而趋近 0
    （256² 上半透明通路 13px、tiled/grid 通路仅 3px）。纹理噪点是**绝对像素
    尺寸**（与分辨率无关），于是小图上噪点成群通过门限 → 干净图误报率随尺寸
    飙升（textured 底图 896² 0.39% → 256² 16.97%）。

    做法：把下限固定为「参考边长（config.DETECT_MIN_AREA_REF，默认 640，与
    benchmark/质量门禁口径一致）的图像上、同一占比对应的像素数」。效果：
        · 输入边长 ≥ 锚点：相对值已 ≥ 下限 → 行为零变化（640 基准逐通路等价）；
        · 输入边长 < 锚点：被钳到同一绝对门限 → 小图不再放行更小的噪点。

    返回 min_area 应取到的绝对像素下限（≥1）。
    """
    ref = config.DETECT_MIN_AREA_REF
    return max(1, int(ref * ref * min_area_ratio * relax))


def detect_translucent(
    rgb: np.ndarray,
    sensitivity: float,
    area_total: int,
    min_area_ratio: float,
    max_area_ratio: float,
) -> Tuple[np.ndarray, List[DetectionRegion]]:
    """
    半透明水印检测。

    参数说明：
        rgb             : (H, W, 3) uint8 RGB 数组（已统一格式）。
        sensitivity     : 敏感度 0~1，越高越灵敏（也越容易误报）。
                          阶段 49 起它直接决定统计门限（见 _sensitivity_to_ksigma），
                          不再参与分位数计算。
        area_total      : 整图像素面积（H*W），用于面积阈值换算。
        min_area_ratio  : 最小面积占比（来自 config）。
        max_area_ratio  : 最大面积占比（来自 config）。

    返回值：
        (combined_mask, regions) —— combined_mask 为该类单独产出的 0/255 掩码；
        regions 为对应区域列表。
    """
    h, w = rgb.shape[:2]
    residual_abs, signed_norm, _ = _residual_fields(rgb)

    # 阶段 49：用「绝对证据门限」取代修复前的分位数阈值。
    # 分位数恒选固定比例像素（sensitivity=0.55 时为 46.8%），与图上是否有水印无关，
    # 这正是过度检测的根因；绝对门限则让「无水印」成为可能的结果。
    k_sigma = _sensitivity_to_ksigma(sensitivity)
    cand = _evidence_gate(
        residual_abs, signed_norm, config.DETECT_MIN_DELTA, k_sigma
    )

    # 阶段 82 · 自然光影守卫：抑制平滑过渡型候选（真实照片 F1 误报大头）。
    # 阈值锚定 640 等效尺度；标定与反例见 docs/82。
    cand = _suppress_natural_light(
        cand, residual_abs, max(h, w) / float(max(1, config.DETECT_MIN_AREA_REF))
    )

    # 阶段 84 · 纹理稠密守卫：抑制「处处高频」型候选（F1 残余——锐利自然纹理）。
    # 与上一个守卫正交且串联：前者管「平不平滑」，后者管「稠不稠密」。
    # density 是比值 → 与振幅/尺度解耦，刻意不接 scale。标定见 docs/84。
    cand = _suppress_texture_like(cand, residual_abs)

    # 连通域过滤 → 区域列表（区域仍用包围盒表示，供界面框选与日志展示）。
    min_area = max(_min_area_px_floor(min_area_ratio),
                   int(area_total * min_area_ratio))
    max_area = max(min_area + 1, int(area_total * max_area_ratio))
    regions = _components_to_regions(
        cand, KIND_TRANSLUCENT, min_area, max_area, pad=4
    )

    # 掩码按连通域真实形状填充（包围盒填充会把覆盖率放大约 2 倍）。
    if config.DETECT_SHAPE_AWARE_MASK:
        mask = _components_mask(
            cand, min_area, max_area, h, w,
            dilate=config.DETECT_TRANSLUCENT_MASK_DILATE,
        )
    else:
        mask = _regions_to_mask(regions, h, w)
    return mask, regions


# ==============================================================================
# 第 4.5 节：策略一增强 —— 平铺半透明水印检测（阶段 20 新增）
# ------------------------------------------------------------------------------
# 原理：AI 生成图常见的「半透明平铺 logo」有两个特点，使常规 detect_translucent
#       容易漏检：
#         (a) 单个 tile 单元面积很小（如一个 logo 20×20 平铺满图），
#             被 DETECT_MIN_AREA_RATIO 过滤掉；
#         (b) 透明度很低（α 小），残差接近噪声，常规分位阈值够不到。
#       本增强用「更激进的分位阈值 + 更低的 min_area（保留单个小 tile 单元）
#       + 形态学闭运算把相邻 tile 单元连成片」来提升召回。
#       它作为 detect_translucent 的补充，在 enhance_tiled=True 时与其结果取并集。
# ==============================================================================

def detect_tiled_translucent(
    rgb: np.ndarray,
    sensitivity: float,
    area_total: int,
    min_area_ratio: float,
    max_area_ratio: float,
    *,
    guard: Optional[bool] = None,
) -> Tuple[np.ndarray, List[DetectionRegion]]:
    """
    平铺半透明水印增强检测（与 detect_translucent 同接口，但参数更激进）。

    参数：
        guard : 是否施加「纹理稠密」连通域守卫（阶段 104）。None = 用
                `config.DETECT_TILED_PATH_GUARD`。**低分辨率兜底臂显式传 False**
                —— 理由见下方调用点注释（本守卫的阈值标定在原生 640 尺度上，
                兜底臂测的是「放大后的小图」，残差被重采样平滑与有损块效应主导）。

    返回值：
        (combined_mask, regions) —— combined_mask 为该类单独产出的 0/255 掩码；
        regions 为对应区域列表（已按原图尺度标注 kind=KIND_TRANSLUCENT）。
    """
    cv2 = _require_cv2()
    h, w = rgb.shape[:2]
    residual_abs, signed_norm, _ = _residual_fields(rgb)

    # 阶段 49：本通路「激进」的部分只剩「保留更小的连通域」（下面的 min_area/4）
    # 与「闭运算把相邻单元连成片」，统计门限与基础通路保持一致。
    #
    # 为什么不再下调统计门限：实测过两种做法（合成诊断集，纹理/照片底图）——
    #   · 门限下调到 0.8×（k≈1.56）：极淡平铺召回 47%，但干净图候选密度达 11.9%，
    #     经膨胀后覆盖率约 28%，即「只盖住不到一半水印，却要毁掉近三分之一画面」；
    #   · 门限与基础一致（k≈2.5）：极淡平铺召回降到个位数，但干净图候选密度仅 1%。
    # 前者是彻头彻尾的亏本买卖，故取后者。极淡平铺水印在高纹理图上本就低于本
    # 特征的检测极限（实测其扰动仅约 9 个灰阶，见图 docs/49-*），诚实的失败方式
    # 是「什么都没检测到」，而不是「把画面重绘三分之一」。
    k_sigma = _sensitivity_to_ksigma(sensitivity)
    # 下限保护：即便预设把敏感度拉到很高（ai_faint_tiled 的 0.72 会算出 k≈1.95），
    # 也不允许低于 DETECT_TILED_MIN_SIGMA_K。实测把门限从 2.2 放松到 1.95，
    # 要多付 2.3 个百分点的误报，只换来 4.3 个百分点的召回，不划算（详见 config）。
    k_sigma = max(k_sigma, config.DETECT_TILED_MIN_SIGMA_K)
    cand = _evidence_gate(
        residual_abs, signed_norm, config.DETECT_MIN_DELTA, k_sigma
    )
    # 阶段 104：接上连通域守卫 —— 但**只接「纹理稠密」一道，不接「自然光影」**。
    #
    # 阶段 49 曾刻意一道都不接，理由是「本通路 k=2.2 比 k_sigma 更严，候选集中在最强残差
    # 像素上，bbox 均值被结构性抬高 → ratio 跌破阈值 → 极淡平铺的补隙组件被误杀」。
    # 阶段 104 用 10 张真实底图 + 6 个合成场景重新量测后**推翻**了该判断（守卫测的是
    # 连通域形态，与门限高低无关），但量测同时给出了「只接一道」的更优解。
    #
    # 单守卫隔离实验（%TEMP%/s104_guard_iso.py、s104_iso_b.py）：
    #   · 真实照片 10 张，并集口径涂改面积（无守卫 → 只自然光影 → 只纹理稠密 → 两道全接）：
    #       ai_faint_tiled  9.619% → 7.122 → **2.605** → 2.362   （干净图纯误报 3.207→0.865→0.803）
    #       ai_grid        10.188% → 7.977 → **3.198** → 2.853   （干净图纯误报 3.168→0.770→0.689）
    #     ⇒ 纹理稠密守卫单独拿到 **72.9% / 68.6%** 的削减，两道全接只多 2.5 / 3.4 个点。
    #   · 合成 6 场景的平铺通路 IoU 之和：无守卫 2.479 / 只自然光影 2.214 /
    #     **只纹理稠密 2.319** / 两道全接 1.882。
    #   · 两道全接会**误伤**两条通路：「纹理 + 极淡平铺」被整类抹零（旧行为 IoU 本就只有
    #     0.012，但抹零会让 `test_run_case_textured_faint_tiled_low_coverage` 与低分辨率
    #     兜底用例 `256-webp75-textured-grid` 双双变空）；「渐变 + 网格」被砍掉 28% 召回。
    #     只接纹理守卫时前者非零、后者**逐位不变**。
    #
    # 机制解释（为什么纹理守卫才是这条通路的那一道）：本通路门限比基础通路更严（k=2.2），
    # 弱而软的**自然光影**梯度本就够不到门限，能穿过严门限的恰恰是**锐利自然纹理**
    # ——正是纹理守卫的设计靶子。自然光影守卫的靶子已由始终生效的基础通路接住，
    # 在本通路上重复接它只会把代价压在弱信号档上（合成侧 IoU 合计 2.319 → 1.882）。
    #
    # 口径说明：本通路仍是显式 opt-in（仅 ai_tiled / ai_faint_tiled / ai_grid 预设
    # 开启），基础通路 detect_translucent 始终同时生效，故「非结构化叠加物」仍有
    # 基础通路兜底；守卫只是让本通路不再重复引入已被基础通路否掉的像素。
    #
    # 豁免（阶段 104 追加）：**低分辨率兜底臂不施加本守卫**（由 detect_watermark_mask
    # 以 guard=False 显式传入）。兜底臂把长边 <640 的输入用 INTER_CUBIC 放大到 640 再检测，
    # 其残差被「重采样平滑 + 有损编码块效应」主导——正好落在纹理稠密守卫的抑制带上，
    # 会把整类抹零，兜底臂「救回黑屏」的契约随之失效（实测 `256²/webp75/textured/grid`）。
    # 而兜底臂本身就是显式 opt-in（`DETECT_LOWRES_RESCUE` 默认 False，阶段 67 已声明
    # 「宁可滥杀，也要救回黑屏」），与守卫的取舍方向相反，故在臂内豁免。
    _guard = config.DETECT_TILED_PATH_GUARD if guard is None else bool(guard)
    if _guard:
        cand = _suppress_texture_like(cand, residual_abs)

    # 单个 tile 单元小，放宽 min_area（取基础的 1/4）以保留小 tile。
    # 阶段 57：放宽后仍要有绝对下限（见 _min_area_px_floor），防止小图上
    # 门限趋近 0 而放行绝对像素尺寸的纹理噪点。
    min_area = max(_min_area_px_floor(min_area_ratio, relax=0.25),
                   int(area_total * min_area_ratio * 0.25))
    max_area = max(min_area + 1, int(area_total * max_area_ratio))
    regions = _components_to_regions(cand, KIND_TRANSLUCENT, min_area, max_area, pad=4)

    if config.DETECT_SHAPE_AWARE_MASK:
        # 膨胀半径取可配置（DETECT_TILED_MASK_DILATE），默认 0：本通路靠闭运算（5×5）已足够
        # 把相邻 tile 单元连成片；本通路会产生大量小连通域（干净纹理图上实测可达 700+ 个），
        # 膨胀是「逐块外扩」，块越小相对涨幅越大——半径 3 时覆盖率可翻 2.35 倍。
        # 阶段 54：默认从 1 收敛为 0，因为「周期性点阵」（网格水印）上 dilate=1 会把网格点
        # 的平滑过渡区几何扩张（cov 1.2%→2.5%），精确率仅 48~65%，合并后把 grid 策略的
        # 100% 精确稀释到约 30%；dilate=0 可把该假阳性收回，且不损伤极淡平铺/edge_text 召回。
        mask = _components_mask(
            cand, min_area, max_area, h, w, dilate=config.DETECT_TILED_MASK_DILATE
        )
    else:
        mask = _regions_to_mask(regions, h, w)

    # 闭运算把相邻 tile 单元连成片，避免修复后出现零散残留点。
    # 阶段 49：核由 7×7/2 次降为 5×5/1 次——原参数会把掩码向外糊一大圈，
    # 是「候选密度 46.8% → 覆盖率 98.4%」这一放大的主要来源之一。
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    return mask, regions


# ==============================================================================
# 第 5 节：策略二 —— 角落文字 / LOGO 检测
# ------------------------------------------------------------------------------
# 原理：绝大多数水印位于四角或正中。先圈定「重点搜索区」（角落方块 /
#       中心方块），在区内用 OCR 取文字框（最准）；若 OCR 不可用，则用
#       MSER（最大稳定极值区域）或 Otsu 阈值 + 连通域取「疑似图标」框。
# ==============================================================================

def _corner_patches(
    h: int, w: int, ratio: float, search_bottom: bool = False
) -> List[Tuple[int, int, int, int]]:
    """
    生成四角 + 中心的「重点搜索区」包围盒列表。

    参数说明：
        h, w  : 图片高宽。
        ratio : 角落方块边长占短边的比例（来自 config.DETECT_CORNER_RATIO）。
        search_bottom : 是否额外覆盖「底部居中条带」（阶段 20 新增，针对 AI 署名栏）。

    返回值：
        每个元素为 (x, y, ww, hh) 的搜索区。
    """
    side = int(min(h, w) * ratio)
    side = max(8, side)
    patches = [
        (0, 0, side, side),                       # 左上
        (w - side, 0, side, side),                # 右上
        (0, h - side, side, side),                # 左下
        (w - side, h - side, side, side),         # 右下
        (w // 2 - side // 2, h // 2 - side // 2, side, side),  # 中心
    ]
    if search_bottom:
        # AI 平台署名常位于底部居中条带：取底部中央一个稍宽的块。
        bw = int(side * 2.2)
        bh = side
        bx = max(0, w // 2 - bw // 2)
        by = max(0, h - bh)
        patches.append((bx, by, min(bw, w - bx), bh))
    return patches


def _apply_plate_completion_prior(
    all_regions: "List[DetectionRegion]",
    mask_parts: "List[np.ndarray]",
    h: int, w: int,
    corner_ratio: float,
    search_bottom: bool,
    preset: "str | None" = None,
) -> None:
    """阶段 179：生成器感知的牌体补全先验（信号锚定，可逆杠杆控制）。

    当某「角搜索区」内有 `translucent`/`edge_text` 证据但无 `corner_logo` 命中时，
    在该角按豆包规范化足迹（340x128 @2048²，随短边等比缩放）补发一个**实心**
    `corner_logo` 区域 + 实心掩码块，使低对比底上也能重建完整牌体。

    为什么直接写实心掩码块（而非只加区域框）：合并阶段对「只产框」的策略会跑
    「框内证据二次确认」（阶段 103），而低对比牌内部恰恰**没有可被证据场捕获的
    强信号**——若只加框，会被裁空甚至整框丢弃，补全失效。故这里把实心框直接
    并入 `mask_parts`，并保留区域供可视化。

    约束：
      · 仅补全**右下角**：豆包角标是固定 340x128 圆角板、**恒在右下角**（阶段 172-A
        客观实测），故补全只针对右下角搜索区——既贴合不变式，也避免在其他角因
        招牌/纹理的 translucent/edge_text 信号误发整牌（会膨胀预测面积、拉低 precision）；
      · 若右下角内已有 `corner_logo` 命中则不重复补全（避免与既通路叠加回归）；
      · 牌内证据锚定（阶段 182）：至少一个 translucent/edge_text 证据区域与牌足迹
        自身重叠 ≥ `DETECT_PLATE_PRIOR_ANCHOR_OVERLAP`（默认 0.50）× 区域面积——
        「豆包 logo 在牌内」的几何不变式；把右下角远离足迹的文字/招牌信号
        （180/181 实测的干净图误报源）挡在门外。阈值置 0 关闭守卫（回 179 行为）；
      · 由 `config.DETECT_PLATE_COMPLETE_FROM_SIGNAL` 控制，默认关闭。
    """
    if not config.DETECT_PLATE_COMPLETE_FROM_SIGNAL:
        return
    # 仅对 committed 预设（general 或不指定）生效，避免污染未承诺预设的面积监控。
    # 阶段 179 闭环：补全曾在 ai_text_heavy 等非承诺预设上触发 mask_coverage 面积漂移
    # 误报（非承诺预设本不该被补全逻辑改动）。
    if preset not in (None, "general"):
        return

    def _overlap(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return (max(ax, bx) < min(ax + aw, bx + bw)
                and max(ay, by) < min(ay + ah, by + bh))

    # 规范化足迹（贴右下角外侧）。基准 2048² 上 340x128。
    fw = max(8, int(round(340 * min(h, w) / 2048.0)))
    fh = max(8, int(round(128 * min(h, w) / 2048.0)))

    patches = _corner_patches(h, w, corner_ratio, search_bottom=search_bottom)
    signal = [r for r in all_regions if r.kind in (KIND_TRANSLUCENT, KIND_EDGE_TEXT)]
    plate = [r for r in all_regions if r.kind == KIND_CORNER_LOGO]
    bx, by = w - fw, h - fh          # 贴右下角外侧（足迹不随搜索区变化）
    # 阶段 182：牌内证据锚定守卫（可逆，阈值 0 = 关闭守卫回到 179 行为）。
    # 至少一个 translucent/edge_text 证据区域与**牌足迹自身**重叠 ≥ 阈值 × 区域面积，
    # 编码「豆包 logo 证据在牌内」的几何不变式。实测分离面（n=15）：真实豆包牌
    # max fp_ov ≥ 0.54，干净图右下角文字/招牌证据 max fp_ov ≤ 0.46（180 误报源）。
    anchor_min = float(config.DETECT_PLATE_PRIOR_ANCHOR_OVERLAP)
    if anchor_min > 0.0:
        anchored = False
        for r in signal:
            ox = max(0, min(int(r.x) + int(r.w), bx + fw) - max(int(r.x), bx))
            oy = max(0, min(int(r.y) + int(r.h), by + fh) - max(int(r.y), by))
            if ox > 0 and oy > 0 and (ox * oy) >= anchor_min * max(1, int(r.w) * int(r.h)):
                anchored = True
                break
        if not anchored:
            return
    for (px, py, pw, ph) in patches:
        # 只处理右下角搜索区（豆包不变式）。
        if not (px + pw >= w and py + ph >= h):
            continue
        if not any(_overlap((px, py, pw, ph), (r.x, r.y, r.w, r.h)) for r in signal):
            continue
        if any(_overlap((px, py, pw, ph), (r.x, r.y, r.w, r.h)) for r in plate):
            continue
        all_regions.append(DetectionRegion(
            x=bx, y=by, w=fw, h=fh, kind=KIND_CORNER_LOGO, confidence=0.55,
        ))
        part = np.zeros((h, w), dtype=np.uint8)
        part[by:by + fh, bx:bx + fw] = 255
        mask_parts.append(part)


# 【OCR 单图识别超时护栏】以独立 daemon 线程 + join(timeout) 实现。
# 真实图上偶有图像触发 EasyOCR 内部极慢路径（CPU 模式，torch/cv2 均释放 GIL），
# 单 patch 可卡数分钟，使整批门禁/检测挂起（实测某真实图令 general 全量跑出 918.8s）。
# daemon 线程后台跑完不阻塞解释器退出；join(timeout) 在 GIL 释放型调用上可靠返回，
# 超时即丢弃该 patch 的 OCR 结果（降级走板块/图标通路）。timeout <= 0 时关闭护栏。
def _ocr_readtext(reader, bgr, timeout: float):
    """在独立线程跑 EasyOCR.readtext；超时即视为该 patch 无 OCR 结果。"""
    if not timeout or timeout <= 0:
        return reader.readtext(bgr)
    import threading
    box: dict = {}

    def _run():
        try:
            box["r"] = reader.readtext(bgr)
        except Exception as exc:  # 主线程重新抛出，走 _ocr_boxes_in_patch 既有 except
            box["e"] = exc

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        log.warning("OCR 单图识别超时（>%.1fs），该 patch 视为无 OCR 结果", timeout)
        return []
    if "e" in box:
        raise box["e"]
    return box.get("r", [])


def _ocr_boxes_in_patch(
    rgb_patch: np.ndarray, px: int, py: int,
    accept_low_conf: bool = False,
) -> List[Tuple[int, int, int, int]]:
    """
    在单个裁剪块上调用 EasyOCR，返回映射到「原图坐标」的文字框包围盒。

    参数说明：
        rgb_patch : 从原图裁剪出的搜索区 (ph, pw, 3)。
        px, py    : 该搜索区在原图的左上角坐标（用于坐标平移）。
        accept_low_conf : 是否启用「角落低置信回退」（仅角落 patch 传 True）。

    返回值：
        包围盒列表 [(x, y, w, h), ...]（原图坐标系）。无 OCR 时返回空。

    精度增强（阶段 22）：
        - 预处理放大：当 config.DETECT_OCR_UPSCALE 为真且裁剪块较小（长边 < 640）
          时，先把块 2x 立方插值放大再识别，最后把识别框按比例回缩到原块坐标，
          显著提升小字召回率。
        - 置信度过滤：丢弃 item[2] < config.DETECT_OCR_MIN_CONFIDENCE（默认 0.25）
          的低置信框，抑制背景误报。
        - 角落低置信回退（阶段审计）：高置信路径零产出、但 OCR 仍「框到」某区域时，
          接受几何「类文本行」（宽高比合理）的候选作定位框。OCR 在角落水印上的价值
          是**定位**而非**识别**——检测框几何常正确，识别失败只拉低置信度。
    """
    reader = _get_ocr_reader()
    if reader is None:
        return []
    try:
        cv2 = _require_cv2()
        bgr = cv2.cvtColor(rgb_patch, cv2.COLOR_RGB2BGR)
        # 预处理放大：小图放大识别，坐标随后按比例回缩。
        scale = 1.0
        if config.DETECT_OCR_UPSCALE:
            ph, pw = bgr.shape[:2]
            if max(ph, pw) < 640:
                scale = 2.0
                bgr = cv2.resize(bgr, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_CUBIC)
        results = _ocr_readtext(reader, bgr, config.DETECT_OCR_TIMEOUT)
    except Exception as exc:
        log.warning("OCR 在角落区域识别失败：%s", exc)
        return []

    min_conf = config.DETECT_OCR_MIN_CONFIDENCE
    raw: List[Tuple[float, int, int, int, int]] = []
    for item in results:
        # item: (polygon_4pts, text, confidence)
        conf = item[2] if len(item) >= 3 else 0.0
        polygon = item[0]
        xs = [int(p[0] / scale) for p in polygon]
        ys = [int(p[1] / scale) for p in polygon]
        x0, y0 = max(0, min(xs)), max(0, min(ys))
        x1, y1 = min(rgb_patch.shape[1], max(xs)), min(rgb_patch.shape[0], max(ys))
        bw, bh = x1 - x0, y1 - y0
        if bw > 2 and bh > 2:
            raw.append((conf, px + x0, py + y0, bw, bh))
    boxes = [(x, y, w, h) for (c, x, y, w, h) in raw if c >= min_conf]
    if (not boxes and accept_low_conf
            and config.DETECT_OCR_CORNER_FALLBACK and raw):
        # 角落回退：高置信路径零产出，但 OCR 仍「框到」了某区域——
        # 角落里出现文字框本身是强水印信号（角落罕有合法密集文字）。
        # 接受几何「类文本行」的候选作定位框，将「识别失败」与「定位」解耦。
        raw.sort(key=lambda t: t[0], reverse=True)
        for (c, x, y, w, h) in raw:
            ar = max(w, h) / max(1, min(w, h))
            if 1.3 <= ar <= 25 and w >= 12 and h >= 6:
                boxes.append((x, y, w, h))
                break
    return boxes


def _mser_boxes_in_patch(
    rgb_patch: np.ndarray, px: int, py: int, patch_area: int
) -> List[Tuple[int, int, int, int]]:
    """
    OCR 不可用时的降级方案：用 MSER（文字区域检测）或连通域取「疑似图标」框。

    参数说明：
        rgb_patch   : 搜索区 (ph, pw, 3)。
        px, py      : 搜索区在原图左上角坐标。
        patch_area  : 搜索区面积（用于剔除「占满整块」的误报）。

    返回值：
        包围盒列表（原图坐标系）。
    """
    cv2 = _require_cv2()
    gray = cv2.cvtColor(rgb_patch, cv2.COLOR_RGB2GRAY)

    boxes: List[Tuple[int, int, int, int]] = []
    try:
        mser = cv2.MSER_create()
        regions, _ = mser.detectRegions(gray)
        for pts in regions:
            x0, y0, bw, bh = cv2.boundingRect(pts)
            # 过滤：太小、太大、或宽高比极端（文字/图标通常是中等紧凑形状）。
            if bw < 8 or bh < 8:
                continue
            if bw * bh > patch_area * 0.85:
                continue  # 几乎占满整块，八成是背景，丢弃
            if max(bw, bh) / max(1, min(bw, bh)) > 12:
                continue  # 细长条，通常不是 LOGO
            boxes.append((px + x0, py + y0, bw, bh))
    except Exception as exc:
        # MSER 在某些 OpenCV 构建里缺失，降级为 Otsu + 连通域。
        log.info("MSER 不可用，角落检测降级为 Otsu 阈值：%s", exc)
        _, bin_patch = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
        num, _, stats, _ = cv2.connectedComponentsWithStats(bin_patch, 8)
        for i in range(1, num):
            x, y, bw, bh, area = stats[i]
            if area < patch_area * 0.01 or area > patch_area * 0.85:
                continue
            boxes.append((px + x, py + y, bw, bh))
    return boxes


def _plate_boxes_in_patch(
    rgb_patch: np.ndarray,
    px: int,
    py: int,
    area_total: int,
    min_side: int,
    lift_patch: "np.ndarray | None" = None,
    mask_source: str = "white",
) -> List[Tuple[int, int, int, int]]:
    """
    白底圆角板块判据（阶段 86）——角落策略的第二候选来源。

    背景
    ----
    真实 AI 图最常见的角标是「白色圆角牌 + 深色描边 + 深色字形」（左上 AI 徽标）。
    MSER / Otsu 通路找的是「稳定极值区」，与该形态不匹配：实测 30 张真实 1024²
    AI 图上该通路对白角标的最佳 IoU 均值仅 0.042（最大 0.521，且无对齐）。
    本函数用一条与内容无关的**绝对量**判据补齐：
        「够亮（V ≥ 阈值）且够灰（S ≤ 阈值）」的紧凑连通域 = 一块白牌。

    为什么不加形态学闭运算
    ----------------------
    牌的深色描边本身就是「牌内部」与「底图」的天然分隔带。闭运算会跨过这条
    2px 描边把两者连通，于是牌不再是「不与搜索区边界相交」的孤立块而被丢弃
    ——实测 `CLOSE(5,5)` 会把真实图命中从 30/30 打到 16/30。**刻意不做闭运算**。

    参数说明
    --------
        rgb_patch : 角落搜索区 (ph, pw, 3) uint8 RGB。
        px, py    : 该搜索区在原图（或降采样图）中的左上角坐标。
        area_total: 整图面积，用于面积占比门限。
        min_side  : 最小边长（已按 DETECT_MIN_AREA_REF 锚点换算到当前尺度）。

    返回值
    ------
        包围盒列表 [(x, y, w, h), ...]（与 rgb_patch 同一坐标系）。
    """
    cv2 = _require_cv2()
    if rgb_patch.size == 0 or not config.DETECT_PLATE_ENABLED:
        return []

    ph, pw = rgb_patch.shape[:2]
    # 阶段 176：两种「亮区来源」，各自跑完形状过滤后**并框**（不是并掩码）。
    #   · "white"（默认）：`(V>=V_MIN)&(S<=S_MAX)` 白色判据（管亮底上的白牌）；
    #   · "lift" ：局部提亮 `lift = V - blur(V, k)`（管中低对比底上的半透明牌）。
    #   实测（docs/176）：把 lift **并进掩码**会造出「混合连通域」、打伤原本命中的图
    #   （mask_recall 0.562 → 0.388）；正确姿势是各跑各的过滤、只并**框**。
    if mask_source == "lift":
        if lift_patch is None:
            return []
        bin_c = lift_patch.astype(np.uint8)
    else:
        hsv = cv2.cvtColor(rgb_patch, cv2.COLOR_RGB2HSV)
        v = hsv[:, :, 2].astype(np.int16)
        s = hsv[:, :, 1].astype(np.int16)
        bin_c = ((v >= config.DETECT_PLATE_V_MIN) &
                 (s <= config.DETECT_PLATE_S_MAX)).astype(np.uint8)
    _fill = float(bin_c.mean())        # 闭运算条件门只看**本来源**的亮像素占比
    if not bin_c.any():
        return []
    # 阶段 173：闭运算桥接「被板上深色字形 / 头像切开的亮块」。
    #   不开时，亮块被墨迹切成碎片 ⇒ 连通域的 bbox 只覆盖牌的一部分
    #   （实测 10 张真实豆包角标图上掩码只覆盖真值框 35%）。
    #   条件门：亮底图上闭运算会把牌与背景并成一片（反而丢框），故仅当亮像素
    #   占比不高时才闭运算（`DETECT_PLATE_CLOSE_MAX_FILL`）。
    #   核大小按图像长边等比（`DETECT_PLATE_CLOSE_FRAC`），否则小图上会把
    #   「32x25 的小白牌」与底图一起并掉（阶段 86 记载的「闭运算毁检出」正是此因）。
    if (config.DETECT_PLATE_CLOSE_FRAC > 0
            and _fill <= config.DETECT_PLATE_CLOSE_MAX_FILL):
        _long = float(area_total) ** 0.5
        _k = max(1, int(round(config.DETECT_PLATE_CLOSE_FRAC * _long)))
        _ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * _k + 1, 2 * _k + 1))
        bin_c = cv2.morphologyEx(bin_c, cv2.MORPH_CLOSE, _ker)

    gray = cv2.cvtColor(rgb_patch, cv2.COLOR_RGB2GRAY)
    num, labels, stats, _cent = cv2.connectedComponentsWithStats(bin_c, 8)

    a_min = config.DETECT_PLATE_MIN_AREA_RATIO
    a_max = config.DETECT_PLATE_MAX_AREA_RATIO
    max_aspect = config.DETECT_PLATE_MAX_ASPECT
    solid_min = config.DETECT_PLATE_SOLIDITY
    ink_min = config.DETECT_PLATE_INK_MIN
    ink_ratio = config.DETECT_PLATE_INK_RATIO

    boxes: List[Tuple[int, int, int, int]] = []
    for i in range(1, num):
        x, y, bw, bh, area = (int(t) for t in stats[i])
        if bw < min_side or bh < min_side:
            continue
        # 触碰搜索区边界 → 该区域「无界」（天空 / 墙面 / 地面），不是一块牌。
        if x <= 0 or y <= 0 or x + bw >= pw or y + bh >= ph:
            continue
        if max(bw, bh) / max(1, min(bw, bh)) > max_aspect:
            continue
        if not (a_min <= (bw * bh) / float(area_total) <= a_max):
            continue
        if area / float(bw * bh) < solid_min:
            continue
        # 墨迹存在性闸门：牌上确实有字 / 图形（角标必然如此，纯亮块不会）。
        comp = labels[y:y + bh, x:x + bw] == i
        if not comp.any():
            continue
        sub = gray[y:y + bh, x:x + bw]
        ref = float(np.median(sub[comp]))
        if ref <= 0.0:
            continue
        ink = float((sub <= ink_ratio * ref).mean())
        if ink < ink_min:
            continue
        boxes.append((px + x, py + y, bw, bh))
    return boxes


def detect_corner_logo(
    rgb: np.ndarray,
    ratio: float,
    area_total: int,
    use_ocr: bool,
    search_bottom: bool = False,
) -> List[DetectionRegion]:
    """
    角落文字 / LOGO 检测。

    候选来源共三条（阶段 100 登记，随开关组合生效）：
        ① 白底圆角板块判据（阶段 86，受 `config.DETECT_PLATE_ENABLED` 控制）
           —— 30 张真实图实测 32 框 / 30 命中 / 平均 IoU 0.79，是角标的主力通路；
        ② OCR 文字框（`use_ocr` 且 OCR 可用时）；
        ③ MSER 降级通路（受 `config.DETECT_CORNER_MSER_ENABLED` 控制，**默认关闭**）
           —— 30 图实测 2569 框 / 15 命中（误报率 99.4%），且其真命中与自然
           纹理块在实心度 / 边缘锐度 / 亮度上**不可分**（docs/100 §3），故作为
           显式 opt-in 放行；关闭时该块若无 OCR 输出则不产出任何候选。

    参数说明：
        rgb       : (H, W, 3) uint8 RGB。
        ratio     : 角落搜索区占比（来自 config）。
        area_total: 整图面积。
        use_ocr   : 是否允许使用 OCR（来自 config）。

    返回值：
        DetectionRegion 列表（类别 corner_logo 或 ocr_text）。
    """
    h, w = rgb.shape[:2]
    patches = _corner_patches(h, w, ratio, search_bottom=search_bottom)
    regions: List[DetectionRegion] = []
    # 阶段 86：白底圆角板块通路的最小边长，按与面积门限同一锚点体系换算。
    _ms = max(h, w) / float(max(1, config.DETECT_MIN_AREA_REF))
    plate_min_side = max(2, int(round(config.DETECT_PLATE_MIN_SIDE * _ms)))

    # 阶段 176：整图算一次「局部提亮」，再按角搜索区切片传给判据（核随长边等比）。
    _lift = None
    if config.DETECT_PLATE_ENABLED and config.DETECT_PLATE_LIFT_FRAC > 0:
        _cv2 = _require_cv2()      # 本模块 cv2 一律延迟导入（见 `_require_cv2`）
        _lk = max(3, int(round(config.DETECT_PLATE_LIFT_FRAC * max(h, w))))
        if _lk % 2 == 0:
            _lk += 1
        _v = _cv2.cvtColor(rgb, _cv2.COLOR_RGB2HSV)[:, :, 2].astype(np.float32)
        _lift = (_v - _cv2.blur(_v, (_lk, _lk))) > float(config.DETECT_PLATE_LIFT_DELTA)

    for (px, py, pw, ph) in patches:
        patch = rgb[py:py + ph, px:px + pw]
        if patch.size == 0:
            continue
        # 阶段 86：白底圆角板块通路（OCR / MSER 之外的独立候选来源）。
        # 放在 OCR 之前：它不依赖 OCR，也不与 MSER 竞争——两者取并集，
        # 「并集恒优于替换」是阶段 67 的既定取舍。
        if config.DETECT_PLATE_ENABLED:
            # 阶段 176：白色判据（主）+ 局部提亮（并列源）各自过滤，**并框**。
            _boxes = list(_plate_boxes_in_patch(
                patch, px, py, area_total, plate_min_side))
            if _lift is not None:
                _boxes += _plate_boxes_in_patch(
                    patch, px, py, area_total, plate_min_side,
                    lift_patch=_lift[py:py + ph, px:px + pw],
                    mask_source="lift")
            for (bx, by, bw, bh) in _boxes:
                regions.append(DetectionRegion(
                    x=bx, y=by, w=bw, h=bh, kind=KIND_CORNER_LOGO,
                    confidence=0.7,
                ))
        # 优先 OCR。
        if use_ocr and _ocr_available():
            boxes = _ocr_boxes_in_patch(patch, px, py, accept_low_conf=True)
            if boxes:
                # 阶段 100：此处原有 `used_ocr_here = True`，但它从未被读取——
                # `detect_corner_logo` 只返回 regions，调用方按 `r.kind == KIND_OCR_TEXT`
                # 自行判定（属死变量，注册后删除）。
                for (bx, by, bw, bh) in boxes:
                    regions.append(DetectionRegion(
                        x=bx, y=by, w=bw, h=bh, kind=KIND_OCR_TEXT,
                        confidence=0.9,
                    ))
                continue  # 该块已用 OCR 处理
        # 降级：MSER / 连通域（阶段 100：默认关闭，见 config.DETECT_CORNER_MSER_ENABLED）。
        # 控制流注意——本分支在「OCR 可用但该块无输出」时**同样会执行**，因此它并非
        # 严格的「OCR 不可用时的降级」（修复前的注释如此描述，属「声明 ≠ 实现」）。
        # 该语义差异正是阶段 100 登记并加开关的原因之一：真实照片上 OCR 常年无输出
        # ⇒ MSER 实际常驻，而它在 30 张真实图上误报率 99.4%（2569 框 / 15 命中）。
        if config.DETECT_CORNER_MSER_ENABLED:
            boxes = _mser_boxes_in_patch(patch, px, py, pw * ph)
            for (bx, by, bw, bh) in boxes:
                regions.append(DetectionRegion(
                    x=bx, y=by, w=bw, h=bh, kind=KIND_CORNER_LOGO, confidence=0.6
                ))
    return regions


# ==============================================================================
# 第 6 节：策略三 —— 边缘小字检测
# ------------------------------------------------------------------------------
# 原理：贴边小字（域名、拍摄参数条）通常是一行「高度很低的密集墨迹」。
#       取图片上下左右四条边向内一定厚度的长条作为搜索区，在区内用
#       OCR 取框；或做「水平/垂直投影」找墨迹密集的行/列，取其包围盒。
# ==============================================================================

def _edge_bands(h: int, w: int, band_ratio: float) -> List[Tuple[int, int, int, int]]:
    """
    生成上下左右四条边缘搜索带。

    参数说明：
        h, w       : 图片高宽。
        band_ratio : 带宽占短边比例（来自 config.DETECT_EDGE_BAND_RATIO）。

    返回值：
        四条带的包围盒 [(x, y, ww, hh), ...]。
    """
    t = max(4, int(min(h, w) * band_ratio))
    return [
        (0, 0, w, t),                  # 上边
        (0, h - t, w, t),             # 下边
        (0, 0, t, h),                 # 左边
        (w - t, 0, t, h),             # 右边
    ]


def _boxes_from_hit(
    binb: np.ndarray,
    hit: np.ndarray,
    orientation: str,
    bh: int,
    bw: int,
) -> List[Tuple[int, int, int, int]]:
    """
    给定「哪些行/列属于文字」的布尔判据，套用尺度约束产出包围盒。

    阶段 52 把「判据」与「约束」解耦：判据可以是相对的（Otsu 通路用
    mean+1.5σ），也可以是绝对的（残差通路用行填充率下限），而尺度约束
    （收缩 / 最小最大尺度 / 不得铺满整条带）是两者共用的几何常识。

    参数说明：
        binb        : 0/255 二值图（墨迹为白），用于交叉轴收缩。
        hit         : 长度 = 主轴长度的布尔数组，True 表示该行/列属于文字。
        orientation : "horizontal" 横条（找文字行）/ "vertical" 竖条（找文字列）。
        bh, bw      : 二值图高宽。

    返回值：包围盒列表 (x, y, w, h)（相对 band 左上角）。
    """
    if orientation == "horizontal":
        axis_len, cross_len = bh, bw
    else:
        axis_len, cross_len = bw, bh
    # 把连续的命中段合并成段（沿主轴的起止）。
    segments = []
    start = None
    for i in range(axis_len):
        if hit[i] and start is None:
            start = i
        elif not hit[i] and start is not None:
            segments.append((start, i))
            start = None
    if start is not None:
        segments.append((start, axis_len))

    # ------------------------------------------------------------------
    # 阶段 49：两道约束，修掉「整条暗带被当成一行文字」的误报。
    #
    # 修复前每个命中段都直接生成一个「沿交叉轴铺满整条带」的包围盒，
    # 于是实测会产出 640×6（整幅宽的细条）与 8×640（整幅高的竖条）这类
    # 绝不可能是文字的框，在干净纹理/照片图上造成 0.8%~3.4% 的误报；
    # 而真正的底部小字水印反而没被这条策略命中（photo 上召回仅 14%，
    # 阶段 52 的绝对残差通路正是为修这个数字而来）。
    #
    #   约束一（收缩）：在命中段内再做一次交叉轴投影，把框收缩到墨迹的
    #                  实际横向范围，而不是无脑铺满整条带。
    #   约束二（尺度）：文字行有合理的高宽范围——太细是投影噪声，
    #                  太高/太宽是画面本身的暗部。
    # ------------------------------------------------------------------
    # 尺度必须参照「带的长边」而非短边：横带的长边即图宽、竖带的长边即图高，
    # 两者都约等于原图长边，是文字尺寸的合理参照。若误用短边（640px 图的横带
    # 短边仅约 77px），上限会小到 13px，连真实 22px 高的水印都会被一并拒掉。
    long_side = max(1, max(bh, bw))
    min_extent = max(4, int(long_side * 0.006))              # 640px 图 → 4px
    max_extent = max(min_extent + 1, int(long_side * 0.15))  # 640px 图 → 96px
    max_cross = 0.85 * cross_len                   # 不允许横向铺满整条带

    out = []
    for (s, e) in segments:
        length = e - s
        if length < min_extent or length > max_extent:
            continue
        # 交叉轴投影：定位墨迹在该段内的真实横向范围。
        if orientation == "horizontal":
            sub = binb[s:e, :]
            cross_proj = np.sum(sub, axis=0)
        else:
            sub = binb[:, s:e]
            cross_proj = np.sum(sub, axis=1)
        ink_cols = np.nonzero(cross_proj > 0)[0]
        if ink_cols.size == 0:
            continue
        c0, c1 = int(ink_cols[0]), int(ink_cols[-1]) + 1
        cross_extent = c1 - c0
        if cross_extent > max_cross:
            # 墨迹铺满整条带 → 更可能是画面暗部而非一行文字，丢弃。
            continue
        if orientation == "horizontal":
            out.append((c0, s, cross_extent, length))
        else:
            out.append((s, c0, length, cross_extent))
    return out


def _proj_from_bin(
    binb: np.ndarray, orientation: str, bh: int, bw: int
) -> List[Tuple[int, int, int, int]]:
    """
    Otsu 通路判据：投影超过「均值 + 1.5×标准差」的行/列视为有文字。

    这是阶段 49 起既有的**相对**判据，保留给 Otsu 二值图使用。

    参数说明：同 _boxes_from_hit。

    返回值：包围盒列表。
    """
    if orientation == "horizontal":
        proj = np.sum(binb, axis=1)   # 每行墨迹像素数
    else:
        proj = np.sum(binb, axis=0)   # 每列墨迹像素数
    mean = proj.mean()
    std = proj.std() + 1e-6
    hit = proj > (mean + 1.5 * std)
    return _boxes_from_hit(binb, hit, orientation, bh, bw)


def _abs_rows_from_bin(
    binb: np.ndarray, orientation: str, bh: int, bw: int, min_fill: float
) -> List[Tuple[int, int, int, int]]:
    """
    绝对残差通路判据：行（列）内墨迹「填充率」达到下限即视为文字行。

    为什么不用相对判据（mean+1.5σ）：实测扫描 abs_delta 时，相对判据给出的召回
    随门限剧烈跳变（14%→42%→14%→51%，见 docs/52），原因是它把「是否成行」建立在
    **本带自身的投影统计量**上——二值图稍有变化，均值/标准差随之抖动，整行判定
    就会翻转。这与阶段 49 修掉的「分位数恒定选中固定比例像素」是同一类缺陷：
    判据随内容自适应，而不是对内容做绝对要求。

    改用绝对填充率：文字笔画在一行内是**密集**的（同一行中相当比例的像素被点亮），
    而纹理散点是稀疏的。填充率 = 该行墨迹像素数 / 交叉轴长度，是与图片内容无关的
    绝对量，随 abs_delta 单调变化，因而可调、可预测、可复现。

    参数说明：
        min_fill : 行填充率下限（0~1，来自 config.DETECT_EDGE_ABS_ROW_FILL）。
        其余同 _boxes_from_hit。

    返回值：包围盒列表。
    """
    cv2 = _require_cv2()
    # 形态学开运算：抹掉孤立单像素噪点，避免稀疏噪点凑够填充率。
    if orientation == "horizontal":
        kernel = np.ones((3, 1), np.uint8)   # 沿主轴（纵向）腐蚀再膨胀
    else:
        kernel = np.ones((1, 3), np.uint8)
    opened = cv2.morphologyEx(binb, cv2.MORPH_OPEN, kernel)
    if orientation == "horizontal":
        proj = np.sum(opened, axis=1)
        cross_len = bw
    else:
        proj = np.sum(opened, axis=0)
        cross_len = bh
    # 填充率：该行被点亮的像素占整行（沿交叉轴）的比例。
    fill = (proj.astype(np.float32) / 255.0) / max(1, cross_len)
    hit = fill >= float(min_fill)
    return _boxes_from_hit(opened, hit, orientation, bh, bw)


def _absolute_residual_bin(
    band_gray: np.ndarray, abs_delta: float, blur_sigma: float
) -> np.ndarray:
    """
    绝对残差二值图（阶段 52 新增的第二条通路）。

    Otsu 是「全局单阈值」，在纹理/照片底图上会同时咬住背景纹理与文字，
    导致字模的相对投影被背景方差淹没。本通路改以「与局部邻域的偏离程度」
    为判据：先对边缘带做高斯平滑得到局部基线，再取 |灰度 − 基线|。

    为什么这能把字模从纹理里分出来：
        · 清晰字模与紧邻背景有固定且可观的**绝对**对比（白字通常 100+ 灰阶）；
        · 纹理经过高斯平滑后残差被显著抑制（纹理是宽带信号，大部分能量落在
          基线里），只有字模这类强局部跳变才留下大残差。

    参数说明：
        band_gray  : 灰度边缘带。
        abs_delta  : 残差门限（灰阶，来自 config.DETECT_EDGE_ABS_DELTA）。
        blur_sigma : 基线模糊半径（来自 config.DETECT_EDGE_ABS_BLUR）。

    返回值：0/255 uint8 二值图（残差超过门限处为 255）。
    """
    cv2 = _require_cv2()
    g = band_gray.astype(np.float32)
    # ksize=(0,0) 让 OpenCV 依据 sigma 自动选核，避免不同图片尺寸下核尺寸不一致。
    base = cv2.GaussianBlur(
        g, (0, 0), sigmaX=float(blur_sigma), sigmaY=float(blur_sigma)
    )
    resid = np.abs(g - base)
    _, binb = cv2.threshold(resid, float(abs_delta), 255.0, cv2.THRESH_BINARY)
    return binb.astype(np.uint8)


def _dedup_boxes(
    boxes: List[Tuple[int, int, int, int]], iou_thr: float = 0.5
) -> List[Tuple[int, int, int, int]]:
    """
    合并「两条通路重复命中同一块文字」的包围盒。

    同一行文字常同时被 Otsu 通路与绝对残差通路击中。若不去重，会产生两枚几乎
    重合的 DetectionRegion，在后续合并阶段各自膨胀，使掩码面积虚增、覆盖率失真。

    参数说明：
        boxes   : 包围盒列表 (x, y, w, h)。
        iou_thr : IoU 超过该值视为同一块；按面积从大到小处理，保留更大的那个。

    返回值：去重后的包围盒列表。
    """
    if not boxes:
        return []
    order = sorted(
        range(len(boxes)), key=lambda i: boxes[i][2] * boxes[i][3], reverse=True
    )
    kept: List[Tuple[int, int, int, int]] = []
    for i in order:
        x, y, w, h = boxes[i]
        ax0, ay0, ax1, ay1 = x, y, x + w, y + h
        dup = False
        for (bx, by, bw2, bh2) in kept:
            ix0, iy0 = max(ax0, bx), max(ay0, by)
            ix1, iy1 = min(ax1, bx + bw2), min(ay1, by + bh2)
            iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
            inter = iw * ih
            if inter <= 0:
                continue
            union = w * h + bw2 * bh2 - inter
            if union > 0 and inter / union > iou_thr:
                dup = True
                break
        if not dup:
            kept.append((x, y, w, h))
    return kept


def _projection_boxes(
    band_gray: np.ndarray,
    orientation: str,
    abs_delta: float = 0.0,
    blur_sigma: float = 11.0,
    min_fill: float = 0.04,
) -> List[Tuple[int, int, int, int]]:
    """
    用投影法在边缘带内找「墨迹密集行/列」的包围盒（OCR 不可用时的降级）。

    参数说明：
        band_gray   : 灰度边缘带 (bh, bw)。
        orientation : "horizontal" 表示带是横条（找文字行）；
                      "vertical"   表示带是竖条（找文字列）。
        abs_delta   : >0 时启用「绝对残差」第二通路（阶段 52 新增）。
                      0 或负数表示关闭，行为与阶段 51 及之前完全一致。
        blur_sigma  : 绝对残差通路求局部基线时的高斯模糊半径。
        min_fill    : 绝对残差通路的行填充率下限。

    返回值：
        包围盒列表（相对 band 左上角坐标）。
    """
    cv2 = _require_cv2()
    bh, bw = band_gray.shape[:2]

    # 通路一：Otsu 二值化（反相：墨迹为白）——阶段 49 起的既有行为。
    _, binb = cv2.threshold(band_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    boxes = _proj_from_bin(binb, orientation, bh, bw)

    # 通路二（阶段 52）：绝对残差二值化 + 绝对填充率判据。
    # 专治 texture/photo 底图上「字模的绝对对比被背景相对方差淹没」导致的漏检。
    # 两条通路的框合并后去重，避免同一行文字被计两次。
    if abs_delta and abs_delta > 0:
        abs_bin = _absolute_residual_bin(band_gray, abs_delta, blur_sigma)
        boxes = _dedup_boxes(
            boxes + _abs_rows_from_bin(abs_bin, orientation, bh, bw, min_fill)
        )
    return boxes


def detect_edge_text(
    rgb: np.ndarray,
    band_ratio: float,
    area_total: int,
    use_ocr: bool,
) -> List[DetectionRegion]:
    """
    边缘小字检测。

    参数说明：
        rgb        : (H, W, 3) uint8 RGB。
        band_ratio : 边缘带带宽占比（来自 config）。
        area_total : 整图面积。
        use_ocr    : 是否允许 OCR。

    返回值：
        DetectionRegion 列表（类别 edge_text 或 ocr_text）。
    """
    cv2 = _require_cv2()
    h, w = rgb.shape[:2]
    bands = _edge_bands(h, w, band_ratio)
    regions: List[DetectionRegion] = []

    for (bx, by, bw, bh) in bands:
        band = rgb[by:by + bh, bx:bx + bw]
        if band.size == 0:
            continue
        band_gray = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
        orientation = "horizontal" if bh <= bw else "vertical"

        if use_ocr and _ocr_available():
            ocr_boxes = _ocr_boxes_in_patch(band, bx, by)
            if ocr_boxes:
                for (x0, y0, w0, h0) in ocr_boxes:
                    regions.append(DetectionRegion(
                        x=x0, y=y0, w=w0, h=h0, kind=KIND_OCR_TEXT, confidence=0.9
                    ))
                continue
        # 降级：投影法。
        # 阶段 52：额外传入绝对残差门限，启用第二条通路——在纹理/照片底图上
        # Otsu 的相对投影会被背景方差淹没，绝对残差通路能找回字模（详见
        # _absolute_residual_bin 与 config.DETECT_EDGE_ABS_DELTA 的注释）。
        proj_boxes = _projection_boxes(
            band_gray, orientation,
            abs_delta=float(config.DETECT_EDGE_ABS_DELTA),
            blur_sigma=float(config.DETECT_EDGE_ABS_BLUR),
            min_fill=float(config.DETECT_EDGE_ABS_ROW_FILL),
        )
        for (x0, y0, w0, h0) in proj_boxes:
            regions.append(DetectionRegion(
                x=bx + x0, y=by + y0, w=w0, h=h0,
                kind=KIND_EDGE_TEXT, confidence=0.6
            ))
    return regions


# ==============================================================================
# 第 6.5 节：策略四 —— 网格状平铺水印检测（阶段 22 新增）
# ------------------------------------------------------------------------------
# 原理：AI 生成图常见的「网格状平铺水印」是规则排布的小标识（点阵 / 网格 logo），
#       每个单元面积很小、透明度低，常规 detect_translucent 容易漏。本策略：
#         1) 同 translucent 思路取残差二值图（有水印处数值较大）；
#         2) 对二值图做「行列投影自相关」找周期 —— 规则网格会在行/列投影上
#            表现出强周期性，从而确认「这是网格」并定位单元间距；
#         3) 仅在周期性强（行、列方向都找到稳定周期）时才标记，避免把随机纹理
#            误判为网格，误报可控。
#       该策略仅在 enable_grid=True（如 ai_grid 预设）时启用，不影响默认检测。
# ==============================================================================

def _dominant_period(proj: np.ndarray) -> Tuple[float, float]:
    """
    用一维自相关在投影序列里找「主导周期」。

    参数说明：
        proj : 沿某轴的「标记像素计数」投影（1D）。

    返回值：
        (period, strength)：period 为估计周期（像素），strength∈[0,1] 为
        自相关相对强度（越高越像规则周期）。找不到时 period=-1.0。
    """
    n = proj.size
    if n < 8 or float(np.sum(proj)) < 1.0:
        return -1.0, 0.0
    p = proj.astype(np.float32)
    p = p - p.mean()
    if p.std() < 1e-6:
        return -1.0, 0.0
    ac = np.correlate(p, p, mode="full")
    ac = ac[ac.size // 2:]  # 取 lags 0..n-1
    if ac[0] <= 0:
        return -1.0, 0.0
    min_lag = max(4, n // 40)
    hi = min(n, min_lag * 20)
    if hi <= min_lag:
        return -1.0, 0.0
    ac_win = ac[min_lag:hi]
    lag = int(np.argmax(ac_win)) + min_lag
    strength = float(ac[lag] / (ac[0] + 1e-6))
    return float(lag), strength


def detect_grid_watermark(
    rgb: np.ndarray,
    sensitivity: float,
    area_total: int,
    min_area_ratio: float,
    max_area_ratio: float,
) -> Tuple[np.ndarray, List[DetectionRegion]]:
    """
    网格状平铺水印检测（与 detect_translucent 同接口）。

    返回值：
        (combined_mask, regions) —— 命中网格时返回每个网格单元的区域；
        否则返回空掩码与空列表。
    """
    cv2 = _require_cv2()
    h, w = rgb.shape[:2]

    # 阶段 49：与半透明通路共用残差场与绝对证据门限，取代分位数阈值。
    # 网格单元比普通水印更淡，故相对显著倍数打 9 折、最小灰阶差与通用值一致。
    residual_abs, signed_norm, _ = _residual_fields(rgb)
    k_sigma = _sensitivity_to_ksigma(sensitivity) * 0.9
    marks = _evidence_gate(
        residual_abs, signed_norm, config.DETECT_MIN_DELTA, k_sigma
    )

    # 开运算去噪点。
    kopen = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    marks = cv2.morphologyEx(marks, cv2.MORPH_OPEN, kopen, iterations=1)
    # 阶段 53：保留「单图证据门限」掩码，用于收敛折叠模板铺满全图的假响应。
    evidence = marks

    # 误报抑制（阶段 23）：网格标记像素占比需在合理带内。
    #   · 太稀疏（接近噪点）→ 不像规则平铺；
    #   · 太密集（几乎整图都是标记）→ 更像全局纹理而非平铺水印。
    cov = float(np.count_nonzero(marks > 0)) / float(max(1, marks.size))
    if cov < config.DETECT_GRID_MIN_COVERAGE or cov > config.DETECT_GRID_MAX_COVERAGE:
        return np.zeros((h, w), dtype=np.uint8), []

    # 周期检测（阶段 49 重写）：在「连续残差场」上做二维 FFT 自相关。
    #
    # 修复前的做法是「二值图 → 行/列投影 → 一维自相关」，判据几乎无区分度：
    # 阈值化丢掉了幅度信息，随机散点的行/列投影同样能给出 0.55~0.60 的相关值，
    # 而阈值只有 0.35，于是干净的纹理/照片底图被稳定判为「有网格」，
    # 实测误报率 13~14%。
    #
    # 现在改为在连续残差场上取自相关峰值：干净图 0.043~0.052，
    # 真实网格水印 0.119~0.188，分离度约 2.3~4.4 倍（详见 config 注释）。
    # 阈值来自 config.DETECT_GRID_MIN_ACF（默认 0.09，取在两簇之间）。
    acf_peak, period_y, period_x = _periodic_strength(signed_norm)

    def _ok(p: int, ax_len: int) -> bool:
        # 最小周期取 config.DETECT_GRID_MIN_PERIOD（默认 16，2026-10-18 从 6 抬升）：
        # JPEG 的 8×8 DCT 块会在残差场产生强 8px 伪周期（doubao 图 10/10 误触发，
        # ACF 0.19~0.57 远超 0.09 阈）；合成网格实测周期=56（56 的倍数），JPEG q90
        # 重编码不改变该周期（lag8 峰始终远小于 lag56 峰）⇒ 抬到 16 杀 JPEG 伪周期
        # 且不漏真实网格（探针 3 底图×3 seed 正样本 9/9 不受影响）。
        return p > 0 and config.DETECT_GRID_MIN_PERIOD <= p <= 0.6 * ax_len

    # 峰值足够强 + 周期尺度合理（不可能是 1~2 px 的噪声，也不能超过图幅大半）
    # → 判定为网格。
    #
    # 注意：只要求「至少一个方向」有合理周期，不能要求两个方向都有。
    # 可分离网格图案（g(x,y)=f(x)·f(y)）的自相关也可分离：
    #   acf(38,0) = acf_x(38)·acf_y(0) = a·1 = a
    #   acf(38,38) = acf_x(38)·acf_y(38) = a²
    # 因 a<1，轴上峰值必然高于对角峰值——实测规则网格的最强峰落在 (38, 0)。
    # 要求两方向同时非零会把这类最常见的网格全部漏掉。
    is_grid = (
        acf_peak >= config.DETECT_GRID_MIN_ACF
        and (_ok(period_y, h) or _ok(period_x, w))
    )
    if not is_grid:
        return np.zeros((h, w), dtype=np.uint8), []

    # ------------------------------------------------------------------
    # 网格命中：用「周期折叠模板」而不是「全部候选像素」来生成掩码（阶段 49）。
    #
    # 修复前的做法是：一旦判定为网格，就把所有通过门限的候选像素原样标记。
    # 问题在于门限在纹理图上总会漏过 1%~2% 的纹理噪声，这些噪声与真正的
    # 网格单元混在一起被一并标记——实测照片底图上误报率达 13.3%，
    # IoU 反而比「不启用网格策略」时更低（8.3% vs 27.9%）。
    #
    # 改为折叠模板后：只有「在每个 tile 里都重复出现」的结构才会保留，
    # 纹理噪声在 N 个 tile 平均后被压低 √N 倍，从而被门限滤掉。
    # ------------------------------------------------------------------
    template, n_tiles = _fold_template(signed_norm, period_y, period_x)
    # 门限直接取「模板自身的稳健 σ × 倍数」。
    #
    # 注意不要再额外除以折叠增益 √N：_robust_sigma(template) 测到的已经是
    # 「平均之后的噪声水平」σ_field/√N，再除一次等于双重折扣——实测会让
    # 门限低到形同虚设，模板里几乎处处超标，覆盖率被推到 28%。
    # 增益已经体现在 σ_t 变小上了，无需重复计入。
    t_sigma = _robust_sigma(template)
    # 阶段 106：模板门限乘一个余量倍数（`DETECT_GRID_TMPL_K_MULT`，默认 1.3）。
    # 此前只乘 `k`，于是「纹理经 N 个 tile 平均后未压干净的伪结构」勉强越过门限、
    # 平铺后铺满全图 —— 实测占网格通路假阳性面积的 82%（FP 0.76% vs 真值 0.63%），
    # 而真网格节点折叠后幅度远高于门限 ⇒ 抬到 1.3 时 **FP −76%、TP 逐位不变**。
    # 标定表与「为什么这不是阶段 99/100 否决过的那种抬阈值」见 config 同名注释。
    k_tmpl = _sensitivity_to_ksigma(sensitivity) * float(
        config.DETECT_GRID_TMPL_K_MULT
    )
    tmpl_mask = (np.abs(template) >= k_tmpl * t_sigma).astype(np.uint8) * 255
    # tile 太少时折叠增益有限（√N 接近 1），模板仍被噪声主导，
    # 此时宁可不标——小图上的「网格」判定本就不可靠。
    if n_tiles < 4:
        return np.zeros((h, w), dtype=np.uint8), []

    if not np.any(tmpl_mask):
        return np.zeros((h, w), dtype=np.uint8), []

    marks = _tile_out(tmpl_mask, h, w)
    # 阶段 53：与单图证据门限相交，收敛折叠模板在纹理/照片底图上「铺满全图」的假响应。
    # 真实网格点在单图证据门限上必然显著，纹理平均残留的伪结构则不显著，故相交后
    # 仅保留真正的网格节点，精确率从 ≤11.6% 拉到 95%+；召回仅 textured 略降（信息论极限）。
    if config.DETECT_GRID_EVIDENCE_AND:
        marks = cv2.bitwise_and(marks, evidence)
    # 网格通路的 min_area 取「相对值与参考分辨率绝对上限的较小者」（阶段 58）：
    #
    # 阶段 57 曾给 grid 加绝对下限被回退——网格经 ACF 周期判据确认后，证据相交掩码呈
    # 碎片状（<20px），speck 口径的下限会把真网格滤空（360²/12px 单元强网格即如此）。
    #
    # 但纯相对量在**大图**上会反向失效：min_area 随面积平方增长，而真实网格叠加层是
    # 绝对像素绘制（不随分辨率缩放），896² 时门限已涨到 40px，而网格点仅 ~28px →
    # 真网格被整体滤空、召回归零（896² gradient/textured 召回 0%，1024 AI 图静默漏检）。
    # 故以「参考边长(640)图像上同一占比的像素数」为**上限**夹紧：<640 维持纯相对
    # （不破坏既有小图行为与单元测试），≥640 恒为 20px，绝对尺寸的网格点全程可过。
    min_area = max(1, min(int(area_total * min_area_ratio * 0.25),
                          _min_area_px_floor(min_area_ratio, relax=0.25)))
    max_area = max(min_area + 1, int(area_total * max_area_ratio))
    regions = _components_to_regions(marks, KIND_GRID, min_area, max_area, pad=2)
    if config.DETECT_SHAPE_AWARE_MASK:
        mask = _components_mask(marks, min_area, max_area, h, w,
                                dilate=config.DETECT_GRID_MASK_DILATE)
    else:
        mask = _regions_to_mask(regions, h, w)
    return mask, regions


# ==============================================================================
# 第 6.6 节：策略五 —— 全局渐变叠加水印检测（阶段 22 新增）
# ------------------------------------------------------------------------------
# 原理：部分 AI 生成图会叠加一层「全局平滑渐变」（如整图轻微变亮 / 变暗的渐变署名
#       层）。它是低频平面信号，遍布整图。检测思路：
#         1) 把灰度图粗采样到小尺寸，对全体像素最小二乘拟合一个双线性平面
#            z = a·x + b·y + c；
#         2) 若平面幅度（√(a²+b²)）较大、且平面解释了图像低频方差的很大一部分
#            （explained 高）、且残差平滑（细节少），说明存在「占主导的全局渐变」
#            → 判定为渐变水印，整图标记（全局渐变影响每一处）。
#       该策略仅在 enable_gradient=True（如 ai_gradient 预设）时启用，且会整图标
#       记，需谨慎使用（普通带渐变的照片也可能触发，故默认关闭、仅预设主动开启）。
# ==============================================================================

def detect_gradient_watermark(
    rgb: np.ndarray,
    area_total: int,
    min_area_ratio: float,
    max_area_ratio: float,
) -> Tuple[np.ndarray, List[DetectionRegion]]:
    """
    全局渐变叠加水印检测（与 detect_translucent 同接口，无需 sensitivity）。

    返回值：
        (combined_mask, regions) —— 命中时返回整图掩码 + 一个全图区域；
        否则返回空掩码与空列表。
    """
    cv2 = _require_cv2()
    h, w = rgb.shape[:2]
    gray_u8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # 粗采样（提速 + 抑制细节，突出低频平面）。
    ds = 256
    small = cv2.resize(gray_u8, (ds, ds), interpolation=cv2.INTER_AREA).astype(np.float32)
    sh, sw = small.shape[:2]
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    xn = xs / max(1, sw - 1)
    yn = ys / max(1, sh - 1)
    # 设计矩阵 [1, x, y]，最小二乘拟合平面。
    A = np.stack([np.ones_like(xn.ravel()), xn.ravel(), yn.ravel()], axis=1)
    z = small.ravel()
    try:
        coeff, *_ = np.linalg.lstsq(A, z, rcond=None)
    except Exception:
        return np.zeros((h, w), dtype=np.uint8), []
    c, a, b = coeff[0], coeff[1], coeff[2]
    plane = (c + a * xn + b * yn)
    residual = small - plane

    var_gray = float(np.var(small))
    var_res = float(np.var(residual))
    if var_gray < 1e-6:
        return np.zeros((h, w), dtype=np.uint8), []
    explained = max(0.0, min(1.0, 1.0 - var_res / var_gray))
    plane_mag = float(np.sqrt(a * a + b * b))
    res_std = float(np.sqrt(var_res))

    # 判据：平面幅度足够 + 解释率高（来自 config.DETECT_GRADIENT_MIN_EXPLAINED，
    #   阶段 23，调高可抑制普通带渐变照片的误报）+ 残差平滑。
    is_gradient = (
        plane_mag > 12.0
        and explained > config.DETECT_GRADIENT_MIN_EXPLAINED
        and res_std < 32.0
    )
    if not is_gradient:
        return np.zeros((h, w), dtype=np.uint8), []

    confidence = max(0.5, min(1.0, explained))
    mask = np.full((h, w), 255, dtype=np.uint8)
    regions = [DetectionRegion(
        x=0, y=0, w=w, h=h, kind=KIND_GRADIENT, confidence=confidence
    )]
    return mask, regions


# ==============================================================================
# 第 6.7 节：策略六 —— 实体 LOGO 轮廓水印检测（阶段 23 新增）
# ------------------------------------------------------------------------------
# 原理：AI 生成图常在角落带「实心品牌标识」（如圆形图标、实心字母组合）。它与
#       文字/线条水印的区别在于：形状是「实心填充」的——轮廓（Canny 边缘得到的
#       闭合边界）所包围的面积，与它的凸包面积之比（solidity，实心率）接近 1；
#       而文字笔画、镂空图案、纹理的实心率明显偏低。
#       检测步骤：
#         1) 灰度图 Canny 边缘 + 轻微膨胀闭合成轮廓；
#         2) 取外轮廓，算每个轮廓的 面积 / 凸包面积 = solidity；
#         3) solidity ≥ DETECT_LOGO_SOLIDITY 且面积在 [min,max]、宽高比不过分细长 → 判为 LOGO；
#         4) 用原轮廓 fillPoly 得到精确掩码，登记为 KIND_LOGO 区域。
#       该策略默认关闭（仅 ai_logo 预设开启），因为对「实心大块物体」（如人脸色块、
#       纯色衬衫）也可能误报；面积上限（默认整图 15%）与宽高比上限已做抑制。
# ==============================================================================

def _canny_adaptive_pair(gray: np.ndarray) -> Optional[Tuple[int, int]]:
    """
    噪声锚定的自适应 Canny 阈值对（阶段 69）；无需补充通路时返回 None。

    规则（只降不升，上限即主通路值）：
        lo = clip(round(med + K × MAD), DETECT_LOGO_ADAPT_LO_MIN, DETECT_LOGO_CANNY_LO)
        hi = 3 × lo

    其中 med / MAD 是梯度幅度图的中位数与中位绝对偏差（稳健统计量，与阶段 49 的
    「绝对门限」原则一致：以**噪声水平**为锚，而不是恒定比例选像素）。
    当 med + K×MAD 已经不低于主通路阈值时，说明图像梯度充足（未被平滑削弱），
    返回 None —— 调用方跳过补充通路，**行为与阶段 68 及以前完全一致**。
    """
    if not config.DETECT_LOGO_CANNY_ADAPT:
        return None
    cv2 = _require_cv2()
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    med = float(np.median(mag))
    mad = float(np.median(np.abs(mag - med)))
    lo = int(round(med + config.DETECT_LOGO_ADAPT_SIGMA_K * mad))
    lo_max = int(config.DETECT_LOGO_CANNY_LO)
    lo = int(min(max(lo, int(config.DETECT_LOGO_ADAPT_LO_MIN)), lo_max))
    if lo >= lo_max:
        return None
    hi = min(int(config.DETECT_LOGO_CANNY_HI), 3 * lo)
    return lo, max(hi, lo + 1)


def detect_logo_watermark(
    rgb: np.ndarray,
    area_total: int,
    min_area_ratio: float,
    max_area_ratio: float,
    solidity_threshold: float = config.DETECT_LOGO_SOLIDITY,
    max_aspect: float = config.DETECT_LOGO_MAX_ASPECT,
) -> Tuple[np.ndarray, List[DetectionRegion]]:
    """
    实体 LOGO 轮廓水印检测（与 detect_translucent 同接口）。

    返回值：
        (combined_mask, regions) —— 命中 LOGO 时返回每个实心轮廓的区域；
        否则返回空掩码与空列表。

    阶段 69：除固定阈值（`DETECT_LOGO_CANNY_LO/HI`）主通路外，若图像梯度被有损编码 /
    重采样整体压低，会**额外**用一组噪声锚定的自适应阈值再取一次轮廓，两者**取并集**
    （并集严格 ⊇ 主通路，故对原本通过的用例零影响；替换式则会在别的组合上制造新崩塌）。
    """
    cv2 = _require_cv2()
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # 阶段 57：绝对下限统一（防止小图上实体 LOGO 通路放行噪点轮廓）。
    min_area = max(_min_area_px_floor(min_area_ratio),
                   int(area_total * min_area_ratio))
    max_area = max(min_area + 1, int(area_total * max_area_ratio))

    ek = np.ones((3, 3), np.uint8)

    def _extract(edges: np.ndarray) -> List[DetectionRegion]:
        """从边缘图取轮廓 → 面积/实心率/长宽比过滤 → 返回区域列表。"""
        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        found: List[DetectionRegion] = []
        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            if area < min_area or area > max_area:
                continue
            hull = cv2.convexHull(cnt)
            hull_area = float(cv2.contourArea(hull))
            if hull_area <= 1e-6:
                continue
            solidity = area / hull_area
            if solidity < solidity_threshold:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw <= 0 or bh <= 0:
                continue
            aspect = max(bw, bh) / float(min(bw, bh))
            if aspect > max_aspect:
                continue
            found.append((cnt, DetectionRegion(
                x=int(x), y=int(y), w=int(bw), h=int(bh),
                kind=KIND_LOGO, confidence=float(solidity),
            )))
        return found

    def _edges(lo: int, hi: int) -> np.ndarray:
        """Canny 拿闭合边界，dilate 把断口连起来，避免轮廓破碎导致实心率失真。"""
        return cv2.dilate(
            cv2.Canny(gray, lo, hi), ek, iterations=1
        )

    base_found = _extract(_edges(int(config.DETECT_LOGO_CANNY_LO),
                                 int(config.DETECT_LOGO_CANNY_HI)))
    hits = list(base_found)

    pair = _canny_adaptive_pair(gray)
    if pair is not None:
        for cnt, reg in _extract(_edges(pair[0], pair[1])):
            # 与主通路结果去重（同一轮廓在两套阈值下都会出现）。
            dup = False
            for _, prev in hits:
                ix = max(0, min(reg.x + reg.w, prev.x + prev.w) - max(reg.x, prev.x))
                iy = max(0, min(reg.y + reg.h, prev.y + prev.h) - max(reg.y, prev.y))
                inter = ix * iy
                if inter <= 0:
                    continue
                union = reg.w * reg.h + prev.w * prev.h - inter
                if union > 0 and inter / float(union) > 0.5:
                    dup = True
                    break
            if not dup:
                hits.append((cnt, reg))

    mask = np.zeros((h, w), dtype=np.uint8)
    regions: List[DetectionRegion] = []
    for cnt, reg in hits:
        regions.append(reg)
        # 用原始轮廓填进掩码，保留 LOGO 精确形状（而非仅包围盒）。
        cv2.fillPoly(mask, [cnt], 255)

    return mask, regions


# ==============================================================================
# 第 7 节：总入口 —— 整合多种策略
# ==============================================================================

# ==============================================================================
# 第 6.5 节：阶段 82 —— 同 kind 相邻碎片聚合
# ------------------------------------------------------------------------------
# 背景（docs/A2 真实图门禁 F2）：真实 AI 平台图的「AI生成」深色角标会被
# translucent 通路打成多个小碎片，每个碎片与真值框 IoU≈0.10 < 0.3 → 整个
# 水印计 FN（17/30 图召回被压在 0.5）。本节把「同 kind、间隙小、合并后不超限」
# 的碎片做传递性合并，让评估口径与修复掩码口径都回到「一个水印 = 一个区域」。
#
# 设计要点（标定数据与反例见 docs/82）：
#   1. **仅同 kind**：跨 kind 合并会把不同策略的证据语义搅在一起（网格单元若
#      被并入聚合，阶段 53 收敛的网格精确率会被稀释），默认只聚合 translucent。
#   2. **贪心按间隙升序**：先把贴着的碎片并起来，再考虑远一点的配对，
#      让紧邻证据优先成簇。
#   3. **双约束缺一不可**（真实图实测的反例教训）：
#        · MAX_SIDE：合并后包围盒最长边上限——挡「链式拖拽」（真值区域通过与
#          它相触的误报区传递性合并，包围盒被拖大后 IoU 掉破匹配阈值，反而丢 TP）；
#        · FILL：合并 bbox 面积 ≤ FILL × 成员面积之和——挡「跨空合并」（两个
#          各自紧凑的碎片群隔着大片空白被拖到一起）。
#      无约束的纯间隙合并实测会把召回从 91.7% 拖回 85~86.7%。
#   4. **阈值锚定 640 等效尺度**（与 DETECT_MIN_AREA_REF 同一锚点体系），
#      随 max(h, w) / 640 线性缩放——碎片间距与水印物理尺寸同向缩放。
# ==============================================================================

# 默认参与聚合的区域类别：F2 的碎片产生于 translucent 通路。
# 其他通路（网格有独立周期语义、渐变整图标记、OCR 框是逐行文字）刻意不参与。
_FRAGMENT_MERGE_KINDS = (KIND_TRANSLUCENT,)


def _rect_gap(
    a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]
) -> int:
    """两个包围盒 (x, y, w, h) 的分离距离（切比雪夫口径，相交/相触返回 0）。"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = max(0, max(ax, bx) - min(ax + aw, bx + bw))
    dy = max(0, max(ay, by) - min(ay + ah, by + bh))
    return max(dx, dy)


def _merge_adjacent_fragments(
    regions: List[DetectionRegion],
    gap_px: int,
    max_side_px: int,
    fill_guard: float,
    kinds: Tuple[str, ...] = _FRAGMENT_MERGE_KINDS,
) -> List[DetectionRegion]:
    """
    对指定类别的区域做「带双约束的贪心碎片聚合」，其余类别原样保留。

    参数说明：
        regions     : 全部候选区域（任一坐标系，只要 gap/cap 阈值与之同尺度）。
        gap_px      : 允许合并的最大包围盒间隙（像素）。
        max_side_px : 合并后包围盒最长边上限（像素），挡链式拖拽。
        fill_guard  : 合并 bbox 面积 / 成员面积之和的上限，挡跨空合并；
                      <= 0 表示关闭该守卫。
        kinds       : 参与聚合的类别元组。

    返回值：
        新的区域列表（未参与聚合的区域为原对象；聚合区域 merged_from > 1
        且带 anchors）。
    """
    sel_idx = [i for i, r in enumerate(regions) if r.kind in kinds]
    if len(sel_idx) <= 1:
        return list(regions)

    n = len(sel_idx)
    parent = list(range(n))
    boxes = [
        (int(regions[i].x), int(regions[i].y), int(regions[i].w), int(regions[i].h))
        for i in sel_idx
    ]

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _cluster_box(root: int) -> Tuple[Tuple[int, int, int, int], List[int]]:
        members = [j for j in range(n) if _find(j) == root]
        x0 = min(boxes[j][0] for j in members)
        y0 = min(boxes[j][1] for j in members)
        x1 = max(boxes[j][0] + boxes[j][2] for j in members)
        y1 = max(boxes[j][1] + boxes[j][3] for j in members)
        return (x0, y0, x1 - x0, y1 - y0), members

    # 候选配对按间隙升序：紧邻证据优先成簇。
    pairs: List[Tuple[int, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            g = _rect_gap(boxes[i], boxes[j])
            if g <= gap_px:
                pairs.append((g, i, j))
    pairs.sort(key=lambda p: p[0])

    for g, i, j in pairs:
        ri, rj = _find(i), _find(j)
        if ri == rj:
            continue
        (bx, members_i) = _cluster_box(ri)
        (by, members_j) = _cluster_box(rj)
        x0 = min(bx[0], by[0])
        y0 = min(bx[1], by[1])
        x1 = max(bx[0] + bx[2], by[0] + by[2])
        y1 = max(bx[1] + bx[3], by[1] + by[3])
        w, h = x1 - x0, y1 - y0
        if max(w, h) > max_side_px:
            continue  # 拖拽守卫：并起来的框太大，宁可保持碎片也不拖大。
        if fill_guard > 0:
            member_area = sum(
                boxes[m][2] * boxes[m][3] for m in members_i + members_j
            )
            if w * h > fill_guard * member_area:
                continue  # 填充守卫：并起来的框大多是空白，两组其实不相干。
        parent[ri] = rj

    groups: Dict[int, List[int]] = {}
    for j in range(n):
        groups.setdefault(_find(j), []).append(j)

    out: List[DetectionRegion] = []
    for root, members in groups.items():
        src = [regions[sel_idx[m]] for m in members]
        if len(src) == 1:
            # 未参与合并的单体：原对象原样保留（语义与顺序零改动）。
            out.append(src[0])
            continue
        x0 = min(s.x for s in src)
        y0 = min(s.y for s in src)
        x1 = max(s.x + s.w for s in src)
        y1 = max(s.y + s.h for s in src)
        # 锚点 = 各成员中心：聚合框中心可能落在成员之间的空隙上（掩码外），
        # 合并过滤阶段的「区域判活」改用锚点逐点判定。
        anchors = [
            (int(s.x + s.w // 2), int(s.y + s.h // 2)) for s in src
        ]
        out.append(
            DetectionRegion(
                x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0),
                kind=src[0].kind,
                confidence=max(s.confidence for s in src),
                merged_from=len(src),
                anchors=anchors,
            )
        )
    for i, r in enumerate(regions):
        if r.kind not in kinds:
            out.append(r)
    return out


def _consolidate_fullframe(
    regions: List[DetectionRegion],
    w: int,
    h: int,
    kinds: Tuple[str, ...],
    spread_ratio: float,
    min_regions: int,
) -> Optional[DetectionRegion]:
    """全幅平铺水印的「全帧区域」合成（阶段 P1）。

    当**同类**区域已铺满全幅（联合包围盒宽、高**均** ≥ spread_ratio，且数量 ≥ min_regions）
    时，返回一个 bbox=(0,0,w,h) 的全幅区域（kind=出现最多的该类）；否则返回 None。

    设计要点
    --------
    - **只扩 region、不填 mask**：全幅 region 让门禁的区域级匹配能认作「铺满全幅」
      （bbox-IoU 与全图真值一致），而掩码仍保留策略算出的**稀疏水印像素**——
      产品侧只重绘真实水印、不整图重绘。需要 mask 也铺满时由
      `DETECT_FULLFRAME_FILL_MASK` 显式开启（供 mask_recall 实验，代价是整图重绘）。
    - 仅对手动开启的平铺预设生效（`DETECT_FULLFRAME_CONSOLIDATE`），general 恒关，
      避免把普通照片里散布的误报凑成整幅。
    """
    eligible = [r for r in regions if r.kind in kinds]
    if len(eligible) < max(1, int(min_regions)):
        return None
    if w <= 0 or h <= 0:
        return None
    x0 = min(r.x for r in eligible)
    y0 = min(r.y for r in eligible)
    x1 = max(r.x + r.w for r in eligible)
    y1 = max(r.y + r.h for r in eligible)
    if (x1 - x0) / float(w) < spread_ratio or (y1 - y0) / float(h) < spread_ratio:
        return None
    # 主导类：出现次数最多的 eligible kind（平局取先出现者，dict 保序）。
    counts: Dict[str, int] = {}
    for r in eligible:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    dominant = max(counts.items(), key=lambda kv: kv[1])[0]
    return DetectionRegion(
        x=0, y=0, w=int(w), h=int(h),
        kind=dominant,
        confidence=max(r.confidence for r in eligible),
        full_frame=True,
    )


def detect_watermark_mask(
    image: np.ndarray,
    *,
    sensitivity: Optional[float] = None,
    enable_translucent: bool = config.DETECT_TRANSLUCENT_ENABLED,
    enable_corner: bool = config.DETECT_CORNER_LOGO_ENABLED,
    enable_edge: bool = config.DETECT_EDGE_TEXT_ENABLED,
    enable_grid: bool = config.DETECT_GRID_ENABLED,
    enable_gradient: bool = config.DETECT_GRADIENT_ENABLED,
    enable_logo: bool = config.DETECT_LOGO_ENABLED,
    enable_fullframe: Optional[bool] = None,
    corner_ratio: float = config.DETECT_CORNER_RATIO,
    edge_band_ratio: float = config.DETECT_EDGE_BAND_RATIO,
    min_area_ratio: float = config.DETECT_MIN_AREA_RATIO,
    max_area_ratio: float = config.DETECT_MAX_AREA_RATIO,
    use_ocr: bool = config.DETECT_USE_OCR,
    preset: Optional[str] = None,
    enhance_tiled: bool = config.DETECT_ENHANCE_TILED,
    search_bottom: bool = config.DETECT_SEARCH_BOTTOM,
    detect_scale: int = config.DETECT_MAX_SIDE,
    lowres_rescue: Optional[bool] = None,
    progress=None,
    _rescue_depth: int = 0,
) -> DetectionResult:
    """
    智能水印自动检测总入口。

    参数说明：
        image              : 输入图，形态不限（RGB / RGBA / 灰度），uint8。
        sensitivity        : 半透明检测敏感度（0~1）；None 用 config 默认值。
        enable_translucent : 是否启用半透明检测。
        enable_corner      : 是否启用角落 LOGO 检测。
        enable_edge        : 是否启用边缘小字检测。
        enable_grid        : 是否启用网格状平铺水印检测（阶段 22）。
        enable_gradient    : 是否启用全局渐变叠加水印检测（阶段 22）。
        enable_logo        : 是否启用实体 LOGO 轮廓水印检测（阶段 23）。
        corner_ratio       : 角落搜索区占比。
        edge_band_ratio    : 边缘带带宽占比。
        min_area_ratio     : 最小面积占比过滤。
        max_area_ratio     : 最大面积占比过滤。
        use_ocr            : 是否允许 OCR 增强。
        detect_scale       : 检测用的最大边长；超过则降采样后再检测，提速。
        lowres_rescue      : 是否启用「低分辨率兜底重检」（阶段 67）；None 用 config 默认值。
                             长边小于 DETECT_LOWRES_RESCUE_SIDE 时，把输入提升到该尺度
                             再检测一次并与主结果取并集——修复小图缩水导致的「完全空掩码」。
                             仅影响长边 < 锚点的输入，≥640 的既有口径逐位不变。
        progress           : 进度回调，签名 (p: float, msg: str)。

    返回值：
        DetectionResult（含合并掩码 + 区域列表 + 是否用了 OCR）。

    说明：
        三类策略各自独立；任一策略抛异常都会被捕获并记录，不影响其余策略，
        保证「能检出多少算多少」，绝不因局部失败整体崩溃。
    """
    cv2 = _require_cv2()

    def _prog(p: float, msg: str) -> None:
        if progress is not None:
            progress(p, msg)

    # ---- 阶段 20：检测预设解析 ----
    # 预设只覆盖其显式设置（非 None）的字段，其余字段沿用本函数默认值，
    # 从而保证向后兼容（不传 preset 时行为与旧版完全一致）。
    if preset is not None:
        _preset = get_preset(preset)
        if _preset is not None:
            _pk = _preset.resolve(
                sensitivity if sensitivity is not None
                else config.DETECT_TRANSLUCENT_SENSITIVITY
            )
            sensitivity = _pk.get("sensitivity", sensitivity)
            if "enable_translucent" in _pk:
                enable_translucent = bool(_pk["enable_translucent"])
            if "enable_corner" in _pk:
                enable_corner = bool(_pk["enable_corner"])
            if "enable_edge" in _pk:
                enable_edge = bool(_pk["enable_edge"])
            if "enable_grid" in _pk:
                enable_grid = bool(_pk["enable_grid"])
            if "enable_gradient" in _pk:
                enable_gradient = bool(_pk["enable_gradient"])
            if "enable_logo" in _pk:
                enable_logo = bool(_pk["enable_logo"])
            if "enable_fullframe" in _pk:
                enable_fullframe = bool(_pk["enable_fullframe"])
            if "enhance_tiled" in _pk:
                enhance_tiled = bool(_pk["enhance_tiled"])
            if "search_bottom" in _pk:
                search_bottom = bool(_pk["search_bottom"])
            if "use_ocr" in _pk:
                use_ocr = bool(_pk["use_ocr"])
        else:
            log.warning("未知检测预设 %s，已忽略，使用默认参数。", preset)

    # 阶段 P1：全幅平铺合并默认值（预设未显式设置时回落到 config）。
    if enable_fullframe is None:
        enable_fullframe = bool(config.DETECT_FULLFRAME_CONSOLIDATE)

    _prog(0.0, "准备图像……")
    try:
        rgb = _ensure_rgb(image)
    except DetectionError as exc:
        raise

    h, w = rgb.shape[:2]
    area_total = h * w

    # 大图降采样：水印是低频结构，降采样不影响定位，但能显著提速。
    scale = 1.0
    if max(h, w) > detect_scale:
        scale = detect_scale / float(max(h, w))
        dh, dw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
        rgb_small = cv2.resize(rgb, (dw, dh), interpolation=cv2.INTER_AREA)
    else:
        rgb_small = rgb
        dh, dw = rgb_small.shape[:2]

    all_regions: List[DetectionRegion] = []
    # 阶段 49：收集各策略自己产出的「形状感知」掩码，供合并阶段直接使用。
    # 修复前合并阶段只用区域包围盒重建掩码，把策略内部算出的精确形状重新糊成矩形——
    # 这是过度检测的最后一级放大器（候选密度 8.8% 能被放大到覆盖率 30%）。
    mask_parts: List[np.ndarray] = []
    used_ocr = False
    _ev_full: Optional[np.ndarray] = None

    def _full_evidence() -> np.ndarray:
        """框内二次确认用的全尺度证据场（懒算一次，只有「框型策略」会用到）。

        与半透明通路同一套判据（`_residual_fields` + `_evidence_gate`），
        因此「什么算证据」在全项目只有一个定义；但**相对门限乘一个缩放因子**
        （`DETECT_BOX_REFINE_K_SCALE`，标定见 config 同名注释）——
        框内已有「这里存在水印」的先验，用全域那套高门限会把极淡水印的子集剔掉。
        """
        nonlocal _ev_full
        if _ev_full is None:
            _ra, _sn, _ = _residual_fields(rgb)
            _k = _sensitivity_to_ksigma(
                sensitivity if sensitivity is not None
                else config.DETECT_TRANSLUCENT_SENSITIVITY
            ) * float(config.DETECT_BOX_REFINE_K_SCALE)
            _ev_full = _evidence_gate(
                _ra, _sn, config.DETECT_MIN_DELTA, _k
            )
        return _ev_full


    def _collect(
        mask_small: Optional[np.ndarray],
        regions: List[DetectionRegion],
    ) -> None:
        """
        收集一个策略的掩码（小图尺度）与区域（原图尺度）。

        参数说明：
            mask_small : 策略返回的掩码；None 或空表示「该策略只产出区域框」。
            regions    : 已换算回原图尺度的区域列表。

        行为说明：
            拿到掩码时，按需放大到原图尺度后收集；拿不到时（如角落/边缘这类
            基于搜索区与区域框的策略），用**框内证据二次确认**生成形状掩码
            （阶段 103）；仅当证据场退化或该能力被关闭时，才退回整框填充。
            区域为空时直接跳过，避免把「未命中」记成一张空掩码参与后续逻辑。
        """
        if not regions:
            return
        if mask_small is not None and mask_small.size > 0:
            m = mask_small
            if m.shape[:2] != (h, w):
                m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
        else:
            # 阶段 103：策略只给了区域框时，不再**整框填满**，改为框内像素级二次确认。
            # 归因依据见 _refine_regions_to_mask 的 docstring 与 config 同名开关注释。
            _ev = _full_evidence()
            if config.DETECT_BOX_EVIDENCE_REFINE and not _evidence_is_degenerate(_ev):
                m, _dropped = _refine_regions_to_mask(
                    regions, _ev, h, w, config.DETECT_BOX_REFINE_PAD
                )
                if _dropped:
                    log.debug(
                        "框内证据二次确认：%d/%d 个区域框内无任何证据，已整框丢弃。",
                        _dropped, len(regions),
                    )
            else:
                # 回退：证据场退化（或该能力被显式关闭）⇒ 保持整框填充。
                # 理由：整幅图取不到证据 ⇔ 证据判据在本图上不可用，而不是「没有水印」；
                # 此时若照样按证据裁剪，会把整幅图裁空 = 静默漏检。
                m = _regions_to_mask(regions, h, w)
        mask_parts.append((m > 0).astype(np.uint8) * 255)

    # ---------- 策略一：半透明水印 ----------
    if enable_translucent:
        _prog(0.15, "检测半透明水印……")
        try:
            s = sensitivity if sensitivity is not None else config.DETECT_TRANSLUCENT_SENSITIVITY
            t_mask, t_regions = detect_translucent(
                rgb_small, s, dh * dw, min_area_ratio, max_area_ratio
            )
            # 平铺半透明增强：用更激进的 detect_tiled_translucent 补充区域，
            # 提升 AI 生成图低透明度平铺水印的召回（阶段 20）。
            if enhance_tiled:
                t2_mask, t_regions2 = detect_tiled_translucent(
                    rgb_small, s, dh * dw, min_area_ratio, max_area_ratio,
                    # 阶段 104：兜底臂（_rescue_depth > 0）豁免纹理稠密守卫 ——
                    # 该臂是对「放大后的小图」做检测，残差被重采样平滑与有损块效应
                    # 主导，守卫会把整类抹零，与兜底臂「宁可滥杀也要救回黑屏」的
                    # opt-in 契约直接冲突（详见 detect_tiled_translucent 内部注释）。
                    #
                    # ⚠ **阶段 107 修正**：这里此前写的是 `guard=(_rescue_depth == 0)`，
                    # 即主臂恒传 `True`；而 `detect_tiled_translucent` 内部是
                    #     `_guard = config.DETECT_TILED_PATH_GUARD if guard is None else bool(guard)`
                    # ⇒ **实参遮蔽了 config** ⇒ `DETECT_TILED_PATH_GUARD` 从未被读取，
                    # 阶段 104 宣称的「注入式反例（`WME_DETECT_TILED_PATH_GUARD=0`，可逆、
                    # 不改代码）」自诞生起就是**无效**的（守卫行为本身正确，坏的是开关）。
                    # 实测（3 张真实图，%TEMP%/s107_guard_check.py）：
                    #   guard=None+config=True 12957px / guard=None+config=False 41764px
                    #   guard=True+config=False 12957px（与上面第一项**逐位相同**）
                    # 现改为「主臂传 None（交回 config）、兜底臂传 False（保留豁免）」。
                    guard=(None if _rescue_depth == 0 else False),
                )
                t_regions = t_regions + t_regions2
                # 阶段 49：平铺增强的掩码一并并入（形状感知模式下它是连通域真实
                # 形状），否则合并阶段会退化成包围盒、让本策略的精确形状前功尽弃。
                t_mask = np.bitwise_or(t_mask, t2_mask)
            # 把区域坐标放大回原图尺度。
            for r in t_regions:
                r.x = int(round(r.x / scale)) if scale != 1.0 else r.x
                r.y = int(round(r.y / scale)) if scale != 1.0 else r.y
                r.w = int(round(r.w / scale)) if scale != 1.0 else r.w
                r.h = int(round(r.h / scale)) if scale != 1.0 else r.h
            all_regions.extend(t_regions)
            _collect(t_mask, t_regions)
        except Exception as exc:
            log.warning("半透明水印检测失败，已跳过：%s", exc)
    else:
        _prog(0.15, "半透明检测已关闭，跳过。")

    # ---------- 策略二：角落 LOGO ----------
    if enable_corner:
        _prog(0.45, "检测角落文字 / LOGO……")
        try:
            c_regions = detect_corner_logo(
                rgb_small, corner_ratio, dh * dw, use_ocr, search_bottom=search_bottom
            )
            for r in c_regions:
                if scale != 1.0:
                    r.x = int(round(r.x / scale))
                    r.y = int(round(r.y / scale))
                    r.w = int(round(r.w / scale))
                    r.h = int(round(r.h / scale))
            used_ocr = used_ocr or any(r.kind == KIND_OCR_TEXT for r in c_regions)
            all_regions.extend(c_regions)
            # 该策略只产出区域框（无独立掩码），_collect 会退化为包围盒填充。
            _collect(None, c_regions)
        except Exception as exc:
            log.warning("角落 LOGO 检测失败，已跳过：%s", exc)
    else:
        _prog(0.45, "角落检测已关闭，跳过。")

    # ---------- 策略三：边缘小字 ----------
    if enable_edge:
        _prog(0.70, "检测边缘小字……")
        try:
            e_regions = detect_edge_text(rgb_small, edge_band_ratio, dh * dw, use_ocr)
            for r in e_regions:
                if scale != 1.0:
                    r.x = int(round(r.x / scale))
                    r.y = int(round(r.y / scale))
                    r.w = int(round(r.w / scale))
                    r.h = int(round(r.h / scale))
            used_ocr = used_ocr or any(r.kind == KIND_OCR_TEXT for r in e_regions)
            all_regions.extend(e_regions)
            # 同角落策略：只产出区域框，退化为包围盒填充。
            _collect(None, e_regions)
        except Exception as exc:
            log.warning("边缘小字检测失败，已跳过：%s", exc)
    else:
        _prog(0.70, "边缘检测已关闭，跳过。")

    # ---------- 策略四：网格状平铺水印（阶段 22） ----------
    if enable_grid:
        _prog(0.78, "检测网格状平铺水印……")
        try:
            s = sensitivity if sensitivity is not None else config.DETECT_TRANSLUCENT_SENSITIVITY
            g_mask, g_regions = detect_grid_watermark(
                rgb_small, s, dh * dw, min_area_ratio, max_area_ratio
            )
            for r in g_regions:
                if scale != 1.0:
                    r.x = int(round(r.x / scale))
                    r.y = int(round(r.y / scale))
                    r.w = int(round(r.w / scale))
                    r.h = int(round(r.h / scale))
            all_regions.extend(g_regions)
            _collect(g_mask, g_regions)
        except Exception as exc:
            log.warning("网格水印检测失败，已跳过：%s", exc)
    else:
        _prog(0.78, "网格检测已关闭，跳过。")

    # ---------- 策略五：全局渐变叠加水印（阶段 22） ----------
    if enable_gradient:
        _prog(0.82, "检测全局渐变水印……")
        try:
            gr_mask, gr_regions = detect_gradient_watermark(
                rgb_small, dh * dw, min_area_ratio, max_area_ratio
            )
            for r in gr_regions:
                if scale != 1.0:
                    r.x = int(round(r.x / scale))
                    r.y = int(round(r.y / scale))
                    r.w = int(round(r.w / scale))
                    r.h = int(round(r.h / scale))
            all_regions.extend(gr_regions)
            _collect(gr_mask, gr_regions)
        except Exception as exc:
            log.warning("渐变水印检测失败，已跳过：%s", exc)
    else:
        _prog(0.82, "渐变检测已关闭，跳过。")

    # ---------- 策略六：实体 LOGO 轮廓水印（阶段 23） ----------
    if enable_logo:
        _prog(0.86, "检测实体 LOGO 轮廓……")
        try:
            l_mask, l_regions = detect_logo_watermark(
                rgb_small,
                dh * dw,
                config.DETECT_LOGO_MIN_AREA_RATIO,
                config.DETECT_LOGO_MAX_AREA_RATIO,
            )
            for r in l_regions:
                if scale != 1.0:
                    r.x = int(round(r.x / scale))
                    r.y = int(round(r.y / scale))
                    r.w = int(round(r.w / scale))
                    r.h = int(round(r.h / scale))
            all_regions.extend(l_regions)
            _collect(l_mask, l_regions)
        except Exception as exc:
            log.warning("LOGO 轮廓检测失败，已跳过：%s", exc)
    else:
        _prog(0.86, "LOGO 检测已关闭，跳过。")

    # ---------- 阶段 179：生成器感知牌体补全先验（信号锚定） ----------
    if enable_corner:
        _apply_plate_completion_prior(
            all_regions, mask_parts, h, w, corner_ratio, search_bottom, preset=preset
        )

    # ---------- 阶段 P1：全幅平铺水印 → 全帧区域 ----------
    # 背景：benchmark 的 bbox-IoU(≥0.3) 匹配是为局部水印设计的；grid / tiled-translucent
    # 铺满整幅时检测器只吐小单元，区域级召回机械性为 0（见 benchmark_real/README §5）。
    # 处置：同类区域已铺满全幅时，追加一个 bbox=(0,0,W,H) 的全帧区域。
    #   ① 只扩 region、不填 mask（产品安全，见 _consolidate_fullframe docstring）；
    #   ② 仅在显式开启的平铺预设生效（general 恒关）；
    #   ③ FILL_MASK=True 时掩码也铺满（供 mask_recall 实验，代价是整图重绘）。
    if enable_fullframe:
        try:
            _ff = _consolidate_fullframe(
                all_regions, w, h,
                config.DETECT_FULLFRAME_KINDS,
                float(config.DETECT_FULLFRAME_SPREAD_RATIO),
                int(config.DETECT_FULLFRAME_MIN_REGIONS),
            )
            if _ff is not None:
                all_regions.append(_ff)
                if config.DETECT_FULLFRAME_FILL_MASK:
                    mask_parts.append(np.full((h, w), 255, dtype=np.uint8))
                log.debug("全幅平铺：合成全帧区域 kind=%s（%dx%d）。", _ff.kind, w, h)
        except Exception as exc:
            log.warning("全幅平铺区域合成失败，已跳过：%s", exc)

    # ---------- 合并 + 形态学收口 ----------
    _prog(0.85, "合并检测结果……")
    if not all_regions:
        mask = np.zeros((h, w), dtype=np.uint8)
    else:
        # 阶段 49：直接用各策略收集的「形状感知」掩码取并集，而不是用区域包围盒
        # 重建。包围盒会把斜向笔画、零散单元的外框整块涂满，是覆盖率虚高的主因。
        # 兜底：若没有任何策略提供掩码（理论上不会发生），退回包围盒行为。
        if mask_parts:
            mask = mask_parts[0].copy()
            for part in mask_parts[1:]:
                np.bitwise_or(mask, part, out=mask)
        else:
            mask = _regions_to_mask(all_regions, h, w)
        # 膨胀一点点，把水印羽化边缘也包进来，避免修复后残留。
        # 阶段 49：(5,5)×2 会让每个区域向四周各涨约 10px；当区域数量很多时
        # （例如网格/平铺水印被拆成上百个小单元），这点「一点点」被成百次重复叠加，
        # 实测可把 5% 的候选密度放大到 30%+，是过度检测的最后一级放大器。
        # 收敛为 (3,3)×1（约 1px），既能包住羽化边缘，又不至于失控。
        # 阶段 106：半径提为可配置（`DETECT_MERGE_DILATE`，默认 1 = 本注释描述的既有行为；
        # 0 可关闭本步用于标定对照——它是**作用于并集**的几何放大器，对上百个小单元
        # 组成的网格/平铺掩码，相对涨幅 ≈ 2k/特征宽度，实测把 grid 通路 0.81% 放大到 2.17%）。
        _mrg = int(config.DETECT_MERGE_DILATE)
        if _mrg > 0:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (2 * _mrg + 1, 2 * _mrg + 1)
            )
            mask = cv2.dilate(mask, kernel, iterations=1)
        mask = _fill_holes(mask)
        # 二次过滤：保留「各区域原始类别（kind）」，让可视化按类型着色、命中率
        # 统计按类型细分。判定以「区域自身面积（按类别放宽）」为准，而非合并后
        # 的连通域面积——否则小区域（如网格单元）会被其他策略产生的大连通域
        # 「吞并」，再因超过 max_area 被整体丢弃（阶段 22 修复）。
        # 特例：全局渐变水印（KIND_GRADIENT）整图标记，不受 max_area 约束。
        kept_regions: List[DetectionRegion] = []
        for r in all_regions:
            # 阶段 P1：全帧区域（全幅平铺）豁免「中心点须在掩码内」与面积上下限——
            # 它的 bbox 覆盖整幅、中心点可能落在无水印像素上；其语义是「空间范围」，
            # 面积判据对它无意义。
            if getattr(r, "full_frame", False):
                kept_regions.append(r)
                continue
            cx, cy = r.x + r.w // 2, r.y + r.h // 2
            # 中心必须落在掩码内，否则该区域已被吞并/丢弃。
            if not (0 <= cy < h and 0 <= cx < w and mask[cy, cx] > 0):
                continue
            if r.kind == KIND_GRADIENT:
                kept_regions.append(r)
                continue
            # 各类别最小面积：网格单元更小，放宽到 1/4（与 detect_grid_watermark 一致）；
            # LOGO 用其专属的面积上下限（默认比通用更小，且上限更紧）。
            kind_min = max(
                1,
                int(area_total * (
                    min_area_ratio * 0.25
                    if r.kind == KIND_GRID else
                    config.DETECT_LOGO_MIN_AREA_RATIO
                    if r.kind == KIND_LOGO else
                    min_area_ratio
                )),
            )
            kind_max = max(
                kind_min + 1,
                int(area_total * (
                    config.DETECT_LOGO_MAX_AREA_RATIO
                    if r.kind == KIND_LOGO else
                    max_area_ratio
                )),
            )
            # 用区域包围盒面积（策略层已做首轮面积过滤）做判定，避免被大块吸收误删。
            if kind_min <= (r.w * r.h) <= kind_max:
                kept_regions.append(r)
        # 最终掩码：仅取「保留区域」bbox 内的合并掩码——既保留精确形状，
        # 又丢弃被其他策略大块吞并的误报区域（如边缘检测在网格图上误标的整圈边框）。
        final_mask = np.zeros((h, w), dtype=np.uint8)
        for r in kept_regions:
            # 阶段 P1：全帧区域是「区域级报告」——FILL_MASK=False 时**不得参与掩码裁剪**。
            # 否则它的全图 bbox 会把本该被小区域 bbox 裁掉的稀疏掩码像素全部保留，
            # 造成面积漂移（实测 ai_faint_tiled mean +24.6% / p90 +39.9%，触 real_gate 漂移守卫）。
            # FILL_MASK=True 时才参与（此时 mask_parts 已并入整幅掩码，裁剪到全图即铺满）。
            if getattr(r, "full_frame", False) and not config.DETECT_FULLFRAME_FILL_MASK:
                continue
            y0, x0 = max(0, r.y), max(0, r.x)
            y1, x1 = min(h, r.y + r.h), min(w, r.x + r.w)
            if y1 > y0 and x1 > x0:
                final_mask[y0:y1, x0:x1] |= mask[y0:y1, x0:x1]
        mask = final_mask
        all_regions = kept_regions

    # ---------- 阶段 67：低分辨率兜底重检 ----------
    # 输入被平台缩小（缩略图 / 社交平台再压缩）后，水印的**绝对像素尺寸**随之缩水，
    # 而各策略内部的判据（残差绝对幅度、面积门限、周期判据等）都按标定尺度（640）
    # 设计——实测小图上会出现「完全空掩码」：320² JPEG 的底部小字 / 角标、256² 网格
    # 召回直接为 0（逐策略归因确认是策略内部判据失败，而非共享面积门限），而界面只会
    # 显示「未检测到水印」，属**静默漏检**。
    # 处置：把长边小于锚点的输入提升到锚点尺度再完整检测一次，与主结果**取并集**。
    #   - 并集而非替换：实测替换会让「photo + WebP + 角标」出现新的崩塌（100%→14%），
    #     并集的召回恒 ≥ 两条通路各自的结果（实测平均召回 73.8% → 91.5%，4 例黑屏全消）；
    #   - 仅对长边 < 锚点生效 → ≥640 的既有口径逐位不变（基线零回归）；
    #   - **默认关闭**（`DETECT_LOWRES_RESCUE=False`，部署级放行闸）：兜底会把原生小图的
    #     纹理噪点一并放大，实测干净图四预设平均误报 256² textured 0.80% → 7.98%
    #     （默认预设 general 亦 0.0% → 4.66%），破坏阶段 57「小图误报 ≤ 2%」的保证。
    #     按项目铁律「宁可漏检，不可滥杀」（阶段 49：误解修复不可逆，漏检只是可见瑕疵），
    #     该能力作为显式 opt-in 放行，供「确认输入是被平台缩小/压缩的副本」时启用。
    #     两种口径的量化对照见 docs/67-*。
    do_rescue = (
        config.DETECT_LOWRES_RESCUE if lowres_rescue is None else bool(lowres_rescue)
    )
    if (
        _rescue_depth == 0
        and do_rescue
        and config.DETECT_LOWRES_RESCUE_SIDE > 0
        and max(h, w) < config.DETECT_LOWRES_RESCUE_SIDE
    ):
        try:
            _side = config.DETECT_LOWRES_RESCUE_SIDE
            _s_up = _side / float(max(h, w))
            _nw = max(1, int(round(w * _s_up)))
            _nh = max(1, int(round(h * _s_up)))
            _prog(0.95, "低分辨率兜底重检……")
            _sub = detect_watermark_mask(
                cv2.resize(rgb, (_nw, _nh), interpolation=cv2.INTER_CUBIC),
                sensitivity=sensitivity,
                enable_translucent=enable_translucent,
                enable_corner=enable_corner,
                enable_edge=enable_edge,
                enable_grid=enable_grid,
                enable_gradient=enable_gradient,
                enable_logo=enable_logo,
                corner_ratio=corner_ratio,
                edge_band_ratio=edge_band_ratio,
                min_area_ratio=min_area_ratio,
                max_area_ratio=max_area_ratio,
                use_ocr=use_ocr,
                preset=preset,
                enhance_tiled=enhance_tiled,
                search_bottom=search_bottom,
                detect_scale=detect_scale,
                lowres_rescue=False,
                _rescue_depth=1,
            )
            _sub_mask = _sub.mask[:, :, 0] if _sub.mask.ndim == 3 else _sub.mask
            if _sub_mask.shape[:2] != (h, w):
                _sub_mask = cv2.resize(
                    _sub_mask, (w, h), interpolation=cv2.INTER_NEAREST
                )
            _added = int(np.count_nonzero((_sub_mask > 0) & (mask == 0)))
            if _added > 0:
                mask = np.bitwise_or(mask, _sub_mask)
                _sx, _sy = w / float(_nw), h / float(_nh)
                for r in _sub.regions:
                    r.x = int(round(r.x * _sx))
                    r.y = int(round(r.y * _sy))
                    r.w = max(1, int(round(r.w * _sx)))
                    r.h = max(1, int(round(r.h * _sy)))
                    all_regions.append(r)
                used_ocr = used_ocr or _sub.used_ocr
                log.debug(
                    "低分辨率兜底重检：长边 %d → %d，新增 %d 像素、%d 个区域。",
                    max(h, w), _side, _added, len(_sub.regions),
                )
        except Exception as exc:
            log.warning("低分辨率兜底重检失败，已跳过：%s", exc)

    # ---------- 最后一道安全闸：全局覆盖率上限（阶段 49） ----------
    # 若合并后的掩码覆盖整图超过上限，说明某条策略已失控。此时按「面积从小到大」
    # 保留区域，直到覆盖率回落到上限内：小区域更像水印，大区域更像误报。
    #
    # 设计依据（产品层面的非对称风险）：把大半个画面交给修复算法重绘，结果是照片
    # 被彻底毁掉、不可逆；而漏掉一小块水印只是残留瑕疵，用户还能手动补。宁可漏检，
    # 不可滥杀。
    #
    # 例外：全局渐变水印（KIND_GRADIENT）按设计整图标记，是用户在预设里主动选择的
    # （ai_gradient），此时不做裁剪，否则该预设会被这道闸彻底清空。
    # 阶段 P1：FILL_MASK=True 时全帧区域会把掩码铺满整幅，必须像渐变一样豁免本闸，
    # 否则「铺满整幅」会被覆盖率闸按 15% 裁掉（自相矛盾）。
    _cap_exempt = any(r.kind == KIND_GRADIENT for r in all_regions) or (
        config.DETECT_FULLFRAME_FILL_MASK
        and any(getattr(r, "full_frame", False) for r in all_regions)
    )
    if all_regions and area_total > 0 and not _cap_exempt:
        cap_px = int(area_total * config.DETECT_MAX_COVERAGE_RATIO)
        covered = int(np.count_nonzero(mask > 0))
        if covered > cap_px:
            log.warning(
                "检测掩码覆盖率 %.1f%% 超过上限 %.1f%%，已按区域面积从小到大裁剪，"
                "保留更可能是水印的小区域。",
                covered / area_total * 100.0,
                config.DETECT_MAX_COVERAGE_RATIO * 100.0,
            )
            # 阶段 P1：全帧区域不贡献掩码（FILL_MASK=False 时已从最终裁剪中排除），
            # 故从面积裁剪循环中剔除、但保留在结果里 —— 否则覆盖率闸会把它当「最大区域」
            # 处理，一张图触发闸门就会把区域级报告弄丢。
            rest = sorted(
                [r for r in all_regions if not getattr(r, "full_frame", False)],
                key=lambda r: r.w * r.h,
            )
            union = np.zeros((h, w), dtype=np.uint8)
            kept: List[DetectionRegion] = [
                r for r in all_regions if getattr(r, "full_frame", False)
            ]
            total = 0
            for r in rest:
                y0, x0 = max(0, r.y), max(0, r.x)
                y1, x1 = min(h, r.y + r.h), min(w, r.x + r.w)
                if y1 <= y0 or x1 <= x0:
                    continue
                sub_mask = mask[y0:y1, x0:x1]
                sub_union = union[y0:y1, x0:x1]
                # 只统计「本次新增」的像素，保证裁剪结果精确、不重复计数。
                add = int(np.count_nonzero((sub_mask > 0) & (sub_union == 0)))
                if total + add > cap_px:
                    break
                sub_union |= sub_mask
                total += add
                kept.append(r)
            mask = union
            all_regions = kept

    # ---------- 阶段 82：同 kind 相邻碎片聚合（最终出口处） ----------
    # 真实图上「AI生成」角标会被 translucent 通路打成小碎片（每个与真值框
    # IoU≈0.10 < 0.3 → 整个水印计 FN，17/30 图召回被压在 0.5）。
    #
    # 位置选择有讲究（探针实测的反例，见 docs/82）：聚合必须放在**全部过滤
    # （二次过滤 + 覆盖率闸）之后**——若放在过滤前，「本该被过滤掉的死区域」
    # 会参与聚合并把好区域拖变形（实测 07 号图：IoU 0.47 的真匹配碎片与下方
    # 死碎片合并后掉到 0.234，整图召回 1.0 → 0.5）；放在出口处则只聚合
    # 「确认存活」的区域，与标定探针的口径完全一致（30 图召回 93.3%）。
    #
    # 阈值按 640 等效尺度标定，随图像尺寸线性缩放（与 DETECT_MIN_AREA_REF
    # 同一锚点体系）：1024 图上 gap=24px / cap=160px。
    if all_regions and config.DETECT_FRAGMENT_MERGE:
        _ms = max(h, w) / float(max(1, config.DETECT_MIN_AREA_REF))
        _gap = max(2, int(round(config.DETECT_FRAGMENT_MERGE_GAP * _ms)))
        _cap = max(24, int(round(config.DETECT_FRAGMENT_MERGE_MAX_SIDE * _ms)))
        try:
            merged = _merge_adjacent_fragments(
                all_regions,
                gap_px=_gap,
                max_side_px=_cap,
                fill_guard=config.DETECT_FRAGMENT_MERGE_FILL,
            )
            if len(merged) != len(all_regions):
                log.debug(
                    "碎片聚合：%d 区域 → %d 区域（gap=%dpx, cap=%dpx, scale=%.2f）。",
                    len(all_regions), len(merged), _gap, _cap, _ms,
                )
                all_regions = merged
                # 掩码按聚合后的区域重建：聚合框内保留原有候选像素
                # （形状感知语义不丢），成员之间的空隙不被填充。
                _m2 = np.zeros((h, w), dtype=np.uint8)
                for r in all_regions:
                    y0, x0 = max(0, r.y), max(0, r.x)
                    y1, x1 = min(h, r.y + r.h), min(w, r.x + r.w)
                    if y1 > y0 and x1 > x0:
                        _m2[y0:y1, x0:x1] |= mask[y0:y1, x0:x1]
                mask = _m2
        except Exception as exc:
            log.warning("碎片聚合失败，按未聚合结果继续：%s", exc)

    # 命中率统计（阶段 22）。
    result_stats = compute_detection_stats(mask, all_regions, area_total, used_ocr)

    _prog(1.0, "检测完成。")
    return DetectionResult(
        mask=mask, regions=all_regions, used_ocr=used_ocr, stats=result_stats
    )


# ==============================================================================
# 第 8 节：可视化辅助（供界面展示「检测到了什么」）
# ==============================================================================

_REGION_COLORS = {
    KIND_TRANSLUCENT: (255, 165, 0),   # 橙
    KIND_CORNER_LOGO: (0, 200, 255),   # 青
    KIND_EDGE_TEXT: (180, 0, 255),     # 紫
    KIND_OCR_TEXT: (0, 255, 120),      # 绿
    KIND_GRID: (255, 0, 200),          # 品红（阶段 22 网格水印）
    KIND_GRADIENT: (255, 230, 0),      # 黄（阶段 22 渐变水印）
    KIND_LOGO: (255, 80, 80),          # 红（阶段 23 实体 LOGO）
}


def visualize_detection(
    image: np.ndarray,
    result: DetectionResult,
    overlay_alpha: float = 0.35,
) -> np.ndarray:
    """
    生成一张「检测可视化」图：原图 + 半透明红色掩码 + 彩色区域框。

    参数说明：
        image         : 原图（任意形态），用于作为底图。
        result        : detect_watermark_mask 的返回结果。
        overlay_alpha : 掩码红层的不透明度。

    返回值：
        (H, W, 3) uint8 RGB 可视化图。
    """
    cv2 = _require_cv2()
    base = _ensure_rgb(image)
    out = base.copy().astype(np.float32)

    # 半透明红色掩码叠加。
    if result.mask is not None and result.mask.shape[:2] == base.shape[:2]:
        red = np.zeros_like(base, dtype=np.float32)
        red[:, :, 0] = 255.0  # R 通道
        m = (result.mask > 0)[..., None]
        out = np.where(m, out * (1 - overlay_alpha) + red * overlay_alpha, out)

    out = out.astype(np.uint8)

    # 区域框。
    for r in result.regions:
        color = _REGION_COLORS.get(r.kind, (255, 255, 0))
        x2 = min(base.shape[1], r.x + r.w)
        y2 = min(base.shape[0], r.y + r.h)
        cv2.rectangle(out, (r.x, r.y), (x2, y2), color, thickness=2)
    return out


# ==============================================================================
# 第 9 节：模块自检
# ==============================================================================
if __name__ == "__main__":
    cv2 = _require_cv2()
    print("开始 detector.py 自检……")

    # 构造一张测试图，并人为叠加「半透明水印」与「角落 LOGO」与「边缘小字」。
    h, w = 400, 600
    img = np.full((h, w, 3), 235, dtype=np.uint8)  # 浅灰背景

    # 半透明水印：在中间写一行半透明白字。
    cv2.putText(
        img, "SAMPLE WATERMARK", (60, 210),
        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (180, 180, 180), 3, cv2.LINE_AA,
    )
    # 角落 LOGO：右下角画一个深色方块。
    cv2.rectangle(img, (w - 90, h - 90), (w - 30, h - 30), (40, 40, 40), -1)
    # 边缘小字：顶部一行深色小字。
    cv2.putText(
        img, "www.example.com", (150, 25),
        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 60), 2, cv2.LINE_AA,
    )

    # 关闭 OCR（沙箱通常不装 easyocr），纯 OpenCV 验证三类策略都能跑通。
    res = detect_watermark_mask(
        img,
        sensitivity=0.55,
        use_ocr=False,
        detect_scale=2000,  # 不触发降采样
    )
    assert res.mask.shape[:2] == (h, w), "掩码尺寸与原图不一致"
    n_pixels = int(np.count_nonzero(res.mask > 0))
    print(f"  检测掩码像素数 = {n_pixels}，区域数 = {len(res.regions)}")
    assert n_pixels > 0, "未检测到任何水印区域（自检失败）"
    assert len(res.regions) > 0, "区域列表为空（自检失败）"

    # 可视化函数不报错。
    vis = visualize_detection(img, res)
    assert vis.shape == (h, w, 3), "可视化尺寸错误"

    # ---- 阶段 20 新增：平铺检测 + 预设解析自检 ----
    cv2 = _require_cv2()
    # 构造一张带「平铺半透明 logo」的测试图：浅灰底上规则平铺小圆点（低透明度）。
    th, tw = 300, 300
    tiled = np.full((th, tw, 3), 240, dtype=np.uint8)
    mark = np.zeros((th, tw), dtype=np.uint8)
    step = 40
    for yy in range(0, th, step):
        for xx in range(0, tw, step):
            cv2.circle(mark, (xx + step // 2, yy + step // 2), 6, 255, -1)
    alpha = 0.25
    tiled = (tiled.astype(np.float32) * (1 - alpha)
             + mark[..., None].astype(np.float32) * alpha).astype(np.uint8)

    tmask, tregions = detect_tiled_translucent(
        tiled, 0.62, th * tw, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO
    )
    assert tmask.shape[:2] == (th, tw), "平铺检测掩码尺寸错误"
    assert len(tregions) > 0, "平铺半透明水印未被检出（增强检测失败）"
    print(f"  平铺增强检测：区域数 = {len(tregions)}，"
          f"掩码像素 = {int(np.count_nonzero(tmask > 0))}")

    # 预设解析：ai_tiled 应正常返回；未知预设应安全回退不报错。
    res_tiled = detect_watermark_mask(tiled, preset="ai_tiled", use_ocr=False, detect_scale=2000)
    assert res_tiled.mask.shape[:2] == (th, tw), "预设检测掩码尺寸错误"
    res_fallback = detect_watermark_mask(img, preset="__no_such__", use_ocr=False, detect_scale=2000)
    assert res_fallback.mask.shape[:2] == (h, w), "未知预设回退掩码尺寸错误"
    print("  预设解析自检通过：ai_tiled 正常、未知预设安全回退。")

    # ---- 阶段 22 新增：网格 / 渐变检测 + 命中率统计自检 ----
    cv2 = _require_cv2()
    # 网格状平铺水印：浅灰底上规则排布小方块。
    gh, gw = 360, 360
    grid = np.full((gh, gw, 3), 235, dtype=np.uint8)
    gstep, gsize = 38, 12
    for gy in range(gstep // 2, gh, gstep):
        for gx in range(gstep // 2, gw, gstep):
            grid[gy - gsize // 2: gy + gsize // 2,
                 gx - gsize // 2: gx + gsize // 2] = 110
    res_grid = detect_watermark_mask(
        grid, enable_grid=True, enable_translucent=False,
        enable_corner=False, enable_edge=False, use_ocr=False, detect_scale=2000,
    )
    assert any(r.kind == KIND_GRID for r in res_grid.regions), "网格水印未被检出"
    gstats = compute_detection_stats(res_grid.mask, res_grid.regions, gh * gw, False)
    print(f"  网格检测：区域数 = {gstats.region_count}，"
          f"覆盖 = {gstats.coverage_ratio:.3f}，by_kind = {gstats.by_kind}")
    assert gstats.mask_pixels > 0, "网格检测掩码为空"

    # 全局渐变叠加水印：均匀灰底 + 平滑左右渐变。
    ph, pw = 200, 320
    base_g = np.full((ph, pw), 200, dtype=np.uint8)
    grad = np.tile(np.linspace(0, 90, pw, dtype=np.float32), (ph, 1))
    grad_img = np.clip(base_g.astype(np.float32) + grad, 0, 255).astype(np.uint8)
    grad_img = cv2.cvtColor(grad_img, cv2.COLOR_GRAY2RGB)
    res_grad = detect_watermark_mask(
        grad_img, enable_gradient=True, enable_translucent=False,
        enable_corner=False, enable_edge=False, use_ocr=False, detect_scale=2000,
    )
    assert any(r.kind == KIND_GRADIENT for r in res_grad.regions), "渐变水印未被检出"
    assert int(np.count_nonzero(res_grad.mask > 0)) == ph * pw, "渐变水印应整图标记"
    print(f"  渐变检测：整图标记像素 = {int(np.count_nonzero(res_grad.mask > 0))}")

    # 命中率统计：首张测试图的 stats 应非空且结构正确。
    assert res.stats is not None, "DetectionResult.stats 为空"
    assert 0.0 <= res.stats.coverage_ratio <= 1.0
    assert res.stats.mask_pixels > 0
    print("  命中率统计自检通过：coverage = %.4f，by_kind = %s"
          % (res.stats.coverage_ratio, res.stats.by_kind))

    # ---- 阶段 23 新增：实体 LOGO 轮廓检测自检 ----
    cv2 = _require_cv2()
    lh, lw = 300, 300
    logo_img = np.full((lh, lw, 3), 245, dtype=np.uint8)
    # 角落实心圆 LOGO（高实心率）。
    cv2.circle(logo_img, (60, 60), 38, (30, 30, 30), -1)
    # 角落实心方块 LOGO。
    cv2.rectangle(logo_img, (lw - 90, lh - 90), (lw - 40, lh - 40), (30, 30, 30), -1)
    res_logo = detect_watermark_mask(
        logo_img, enable_logo=True, enable_translucent=False,
        enable_corner=False, enable_edge=False, enable_grid=False,
        enable_gradient=False, use_ocr=False, detect_scale=2000,
    )
    assert any(r.kind == KIND_LOGO for r in res_logo.regions), "实体 LOGO 未被检出"
    lstats = compute_detection_stats(res_logo.mask, res_logo.regions, lh * lw, False)
    print(f"  LOGO 检测：区域数 = {lstats.region_count}，by_kind = {lstats.by_kind}")

    # 门控默认关闭：不开启 enable_logo 时不应出现 KIND_LOGO 区域（向后兼容）。
    res_logo_off = detect_watermark_mask(
        logo_img, enable_translucent=False, enable_corner=False,
        enable_edge=False, enable_grid=False, enable_gradient=False,
        use_ocr=False, detect_scale=2000,
    )
    assert not any(r.kind == KIND_LOGO for r in res_logo_off.regions), \
        "LOGO 门控默认关闭却检出 LOGO（向后兼容被破坏）"
    print("  LOGO 门控自检通过：默认关闭、开启后正常检出。")

    # ai_logo 预设自检：一次调用应同时开启 LOGO 检测与其它策略且不报错。
    res_ai_logo = detect_watermark_mask(
        logo_img, preset="ai_logo", use_ocr=False, detect_scale=2000
    )
    assert any(r.kind == KIND_LOGO for r in res_ai_logo.regions), \
        "ai_logo 预设未触发 LOGO 检测"
    print("  ai_logo 预设自检通过：LOGO 检测已随预设开启。")

    print("detector.py 自检通过：三类水印检测、平铺增强、预设解析、网格/渐变、"
          "命中率统计、可视化与阶段 23 的 LOGO 轮廓检测均正常。")
