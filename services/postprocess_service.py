# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/postprocess_service.py
 文件说明 : 结果后处理与画质增强服务（阶段 18 核心服务，服务层）。

            在「核心修复 inpaint」完成后，于 services 层（零 gradio 依赖）
            自动叠加三步增强，显著提升成品观感：

              ① 边缘融合（Edge Blend）
                 在掩码边界做窄带羽化，柔化 inpaint 填充区与原图的接缝台阶，
                 让修复区过渡更自然（仅在提供原图 + 掩码时生效）。
              ② 色彩还原（Color Match）
                 把「被修复区域」的颜色 / 对比度统计向「周围原图」对齐，
                 修正 inpaint 可能残留的轻微色偏（同样依赖原图 + 掩码）。
              ③ 锐化（Sharpen）
                 用 unsharp mask 找回 inpaint 损失的高频细节（整图轻量锐化，
                 不依赖原图）。

            设计约束：
            · 本模块属于「服务层」，严禁 import ui / gradio。
            · 全部基于 cv2 / numpy，不引入新第三方依赖。
            · 三步各自有独立开关（config），可单独关闭；总开关关闭时直接
              透传 inpaint 原始结果，零额外开销。
            · 与 quality_service 联动：evaluate_improvement 复用 evaluate_quality
              做「优化前 / 优化后」质量对比，供批量页与 CLI 反馈提升幅度。

            调用方（三处 inpaint 入口统一接线）：
              · services.auto_pipeline.process_image  （CLI / API / PDF / 视频）
              · ui.tab_auto.process_auto_repair        （单图 / 自动 / 批量 / 历史重处理）
              · ui.tab_single.process_single           （单图手动涂抹）
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from typing import Optional

# ------------------------------------------------------------------------------
# 路径加固：保证无论从哪个目录启动都能 import 到项目根模块。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import cv2  # type: ignore

import config
from utils.logger import get_logger
from services.quality_service import evaluate_quality_arrays
from core.superres import get_superres_backend, superres_upscale, SuperResBackend

log = get_logger("services.postprocess_service")


# ==============================================================================
# 内部工具
# ==============================================================================
def _as_mask(mask) -> "Optional[np.ndarray]":
    """把任意形态掩码规整为单通道 0/255 uint8（非 0 即视为修复区）。"""
    if mask is None:
        return None
    m = mask
    if m.ndim == 3:
        m = m[:, :, 0]
    m = m.astype(np.uint8)
    return (m > 0).astype(np.uint8) * 255


def _has_geom(original_rgb, mask) -> bool:
    """是否具备「原图 + 掩码」，足以做几何类增强（边缘融合 / 色彩还原）。"""
    return original_rgb is not None and mask is not None and mask.sum() > 0


# ==============================================================================
# 三步增强（纯像素运算，互不依赖外部状态）
# ==============================================================================
def _color_match(result: np.ndarray, mask: np.ndarray, original: np.ndarray, strength: float) -> np.ndarray:
    """
    把被修复区域（mask>0）的颜色统计向「紧邻掩码的局部原图环带」对齐。

    阶段 60 修正（两点，见 docs/60-*）：
      1) 上下文只取**紧贴掩码外的窄环**（向内 4px 外、向外 16px 内），不再取全图外部。
         旧实现用整个 mask==0 区域做统计：在渐变/局部亮度变化大的图上，"全图外部均值"
         与 Logo 处的局部背景差异巨大，均值对齐会把修复区拖向错误明暗（实测 corner_logo
         掩码区 PSNR 38.4 → 20.2 dB）；改用局部环带后反而纠正 inpaint 的轻微偏色
         （38.4 → 41.7 dB）。
      2) 只做**均值对齐**，不再按外部/内部 std 之比重缩放对比度——std 缩放会在内容结构
         变化处放大/扭曲明暗（同一用例旧实现 −18 dB 的主因之一）。

    逐通道：mean(环带·原图) 与 mean(结果·内部)，把内部均值向环带对齐，
    再以 strength 比例与原始结果混合。
    """
    maskb = mask > 0
    if maskb.sum() < 100:
        return result
    k = np.ones((3, 3), np.uint8)
    # 紧邻掩码外的局部环带：dilate(far) 之外 − dilate(near) 之外。
    inner = cv2.dilate(maskb.astype(np.uint8), k, iterations=4) > 0
    outer = cv2.dilate(maskb.astype(np.uint8), k, iterations=16) > 0
    ring = outer & (~inner)
    if ring.sum() < 50:
        return result  # 环带过小（如掩码贴满图边），统计不可靠 → 跳过
    out = result.astype(np.float32)
    for c in range(3):
        o_ring = original[:, :, c][ring]
        r_int = result[:, :, c][maskb]
        if o_ring.size == 0 or r_int.size == 0:
            continue
        delta = (float(o_ring.mean()) - float(r_int.mean())) * strength
        out[:, :, c][maskb] = np.clip(r_int.astype(np.float32) + delta, 0, 255)
    return out.astype(np.uint8)


def _edge_blend(result: np.ndarray, mask: np.ndarray, original: np.ndarray, band: int) -> np.ndarray:
    """
    在掩码内边界做窄带柔化，抚平 inpaint 填充区与原图的接缝。

    阶段 60 语义修正：旧实现把 `original` 按 alpha 混回掩码边界带——对"去水印"是
    致命的：掩码边界内侧的原图仍是**水印像素**，混回等于把刚擦掉的边缘重新涂回去
    （实测 corner_logo 掩码区 PSNR 38.4 → 12.3 dB、no-ref −0.06 → −1.88，见
    docs/60-*）。新实现**只用 result 自身的轻模糊**在「掩码内、距边界 ≤ band」的
    窄带平滑接缝，绝不引入原图（水印）像素；掩码外保持原样。

    若 band ≤ 0 或掩码内边界带为空则原样返回。
    """
    if band <= 0:
        return result
    inside = mask > 0
    if inside.sum() < 1:
        return result
    k = 2 * band + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    # 掩码内部核（向内腐蚀 band 圈后剩下的"远离边界"部分）。
    core = cv2.erode(mask, kernel) > 0
    if not np.any(core):
        # 薄/小结构（文字行、网格点、细 LOGO 等）整体都是边界，无可"保留的内部"——
        # 若强行模糊整块掩码会把刚修好的内容抹糊（实测 gradient edge_text/grid
        # 掩码区 PSNR 因此掉 6~7 dB）；此时边界柔化无意义，直接返回。
        return result
    # 掩码内边界带 = 掩码内 − 内部核。
    band_mask = inside & (~core)
    if not np.any(band_mask):
        return result
    # 用 result 自身的盒式模糊柔化接缝（只轻微改写边界带，绝不混入原图水印像素）。
    ksize = 2 * band + 1
    soft = cv2.blur(result, (ksize, ksize))
    alpha = np.zeros(mask.shape[:2], dtype=np.float32)
    alpha[band_mask] = 0.55
    out = (result.astype(np.float32) * (1.0 - alpha[:, :, None])
           + soft.astype(np.float32) * alpha[:, :, None])
    return np.clip(out, 0, 255).astype(np.uint8)


def _sharpen(result: np.ndarray, amount: float) -> np.ndarray:
    """
    unsharp mask 锐化：result + amount * (result - 模糊)。
    amount 越大，模糊核越小（保留越多细节）；amount<=0 直接返回原图。
    """
    if amount <= 0:
        return result
    sigma = max(0.4, 2.0 - amount)  # amount 0.6 → 1.4；amount 2.0 → 0.4
    blurred = cv2.GaussianBlur(result, (0, 0), sigmaX=sigma)
    sharp = result.astype(np.float32) + amount * (result.astype(np.float32) - blurred.astype(np.float32))
    return np.clip(sharp, 0, 255).astype(np.uint8)


# ==============================================================================
# 阶段 61：可选增强的「自动回退」安全网
# ------------------------------------------------------------------------------
# 可选增强（纹理重建 / Poisson / 伪影抑制 / 轻量超分）默认关闭但用户可开启，其中
# 存在本质不安全项（Poisson 会整体替换修复区，实测 −10.9 ~ −26.4 dB，见 docs/61-*）。
# 这里复用阶段 56 的无参考质量分：某一步之后若质量分明显下滑，则撤销该步。
# 保护而非限制——自动回退可通过 POSTPROCESS_AUTO_REVERT=False 关闭。
# ==============================================================================
def _quality_score(img: np.ndarray, source: np.ndarray, mask: np.ndarray):
    """无参考修复质量分（越高越好）；不可用时返回 None。"""
    try:
        from core.quality import no_reference_inpaint_score
        return float(no_reference_inpaint_score(img, source, mask))
    except Exception as exc:  # noqa: BLE001
        log.debug("质量分计算不可用，跳过自动回退判断：%s", exc)
        return None


def _revert_if_worse(pre: np.ndarray, post: np.ndarray, source, mask,
                     step: str) -> np.ndarray:
    """若 post 相对 pre 质量分明显下滑，撤销该步（返回 pre）。"""
    if not config.POSTPROCESS_AUTO_REVERT:
        return post
    if source is None or mask is None:
        return post
    if post.shape != pre.shape:
        # 真实超分等改变画布的步骤不参与（掩码/原图尺寸已不匹配）。
        return post
    s0 = _quality_score(pre, source, mask)
    s1 = _quality_score(post, source, mask)
    if s0 is None or s1 is None:
        return post
    if s1 < s0 - config.POSTPROCESS_AUTO_REVERT_TOL:
        log.warning("后处理步骤「%s」质量分下滑（%.3f → %.3f），已自动回退",
                    step, s0, s1)
        return pre
    return post


# ==============================================================================
# 阶段 21 新增：三道「画质增强深化」pass（纯像素运算，仅原图 + 掩码时生效）
# ==============================================================================
def _artifact_reduce(result: np.ndarray, mask: np.ndarray, amount: float) -> np.ndarray:
    """
    伪影抑制：在掩码区内做边缘保持双边滤波，抑制 inpaint（尤其 LaMa）可能留下的
    网格 / 块状伪影，同时尽量保留已注入的纹理细节。

    仅在掩码内部按 (mask/255 * amount) 软权重混合，掩码外原样保留。
    amount 越大，伪影抑制越强（过大会磨平细节）。
    """
    if amount <= 0:
        return result
    sigma = 15.0 + amount * 60.0  # 15~75，随强度增大
    filt = cv2.bilateralFilter(result, 5, sigma, sigma)
    w = (mask.astype(np.float32) / 255.0) * amount
    w = w[:, :, None]
    out = result.astype(np.float32) * (1.0 - w) + filt.astype(np.float32) * w
    return np.clip(out, 0, 255).astype(np.uint8)


def _texture_transfer(result: np.ndarray, mask: np.ndarray, original: np.ndarray, amount: float) -> np.ndarray:
    """
    纹理细节重建：把「原图外部区域的高频细节」注入修复区，重建纹理，减少肉眼可见的
    「修补块」。默认关闭（`POSTPROCESS_TEXTURE_TRANSFER`，见 config）。

    思路：detail = original − 大核高斯(original)；只取 mask==0（外部）的细节，避免把
    水印本身的高频再注回；然后把这份"外部细节"**延拓进掩码内部**再按 amount 叠加。

    阶段 61 修复：旧实现把 detail 在 mask==0 处保留、在 mask>0 处置零，随后又只在
    mask>0 处加权相加——两者作用域互斥，**乘积恒为 0，该增强从未生效**（实测全 12 用例
    掩码区 PSNR Δ 恒等于 0.00）。现在改用 cv2.inpaint 把 detail 场（平移 +128 后转
    uint8）在掩码区做一次延拓填充，使外部纹理真正流入修复区。
    """
    if amount <= 0:
        return result
    low = cv2.GaussianBlur(original, (0, 0), sigmaX=8.0)
    detail = original.astype(np.float32) - low.astype(np.float32)
    # 仅保留原图外部细节（水印区域本身置 0 的"待延拓"版本）。
    ext = (mask == 0)[:, :, None]
    detail = detail * ext.astype(np.float32)
    if not np.any(mask > 0):
        return result
    # 阶段 61：外围「确实有纹理」才注入——外围本身平滑时（纯渐变底/天空/纯色背景）注入
    # 外来高频等于往干净填充区撒噪声（实测 gradient corner_logo 掩码区 PSNR −12.6 dB）。
    ring = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0
    ring_ext = ring & (mask == 0)
    if ring_ext.sum() < 50 or float(detail[ring_ext].std()) < config.POSTPROCESS_TEXTURE_MIN_DETAIL:
        log.debug("外围纹理不足（高频 std < %.2f），跳过纹理重建",
                  config.POSTPROCESS_TEXTURE_MIN_DETAIL)
        return result
    # 把外部细节延拓进掩码内部（inpaint 需要 uint8，故平移 +128 后还原）。
    d_u8 = np.clip(detail + 128.0, 0, 255).astype(np.uint8)
    filled = cv2.inpaint(d_u8, (mask > 0).astype(np.uint8) * 255,
                         3, cv2.INPAINT_TELEA).astype(np.float32) - 128.0
    w = (mask.astype(np.float32) / 255.0) * amount
    w = w[:, :, None]
    out = result.astype(np.float32) + filled * w
    return np.clip(out, 0, 255).astype(np.uint8)


def _poisson_blend(result: np.ndarray, mask: np.ndarray, original: np.ndarray, strength: float) -> np.ndarray:
    """
    边缘自然融合深化：用 cv2.seamlessClone（Poisson 编辑）把修复区按原图梯度无缝融入
    周围，比窄带羽化更彻底地消除接缝台阶。

    阶段 61 风险标注（docs/61-*）：Poisson 按**边界梯度重建**修复区，在去水印场景会把
    刚填好的内容整体替换——实测 corner_logo/edge_text 掩码区 PSNR −10.9 ~ −26.4 dB
    （textured faint_tiled 上又有 +15.9 dB 的正例，即"极端不稳定"）。因此：
      · 默认关闭（`POSTPROCESS_POISSON_BLEND=False`）；
      · 开启时受"可选增强自动回退"保护（_revert_if_worse）：质量分下滑即撤销。
    不建议在成品去水印流程中启用，保留主要是为通用 inpainting 场景。
    """
    if strength <= 0:
        return result
    ys, xs = np.where(mask > 0)
    if xs.size == 0:
        return result
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    # 留 1px 余量，避免贴边导致 cv2 抛异常。
    if x0 < 1 or y0 < 1 or x1 > mask.shape[1] - 2 or y1 > mask.shape[0] - 2:
        return result
    cx = int((x0 + x1) / 2)
    cy = int((y0 + y1) / 2)
    try:
        blended = cv2.seamlessClone(result, original, mask, (cx, cy), cv2.NORMAL_CLONE)
    except Exception as exc:  # noqa: BLE001
        log.warning("Poisson 融合失败，已回退普通结果：%s", exc)
        return result
    if strength >= 1.0:
        return blended
    w = float(strength)
    out = result.astype(np.float32) * (1.0 - w) + blended.astype(np.float32) * w
    return np.clip(out, 0, 255).astype(np.uint8)


def _superres(result: np.ndarray, mask: np.ndarray, original: np.ndarray, amount: float) -> np.ndarray:
    """
    轻量超分（同分辨率细节重建）：用「原图外部区域」的高频细节（拉普拉斯）注入修复区，
    并叠加 unsharp 强化，抵消 inpaint 造成的模糊 / 细节损失。

    与 _texture_transfer（高斯差细节）的区别：本函数取更细的高频（拉普拉斯），并在纹理
    注入后再叠一次整体 unsharp，等效于「细节超分辨率」的轻量离线近似——无需 AI 模型 /
    torch，纯 cv2 实现。不改变画布尺寸（保持与 evaluate_improvement / 报告「同尺寸对比」
    契约一致）。

    amount 越大细节越锐利（过大会引入噪点）。amount<=0 直接返回原图。
    """
    if amount <= 0:
        return result
    # 1) 高频细节（仅原图外部，避免把水印本身的高频再注回修复区）。
    lap = cv2.Laplacian(original, cv2.CV_32F, ksize=3)
    ext = (mask == 0)[:, :, None].astype(np.float32)
    lap = lap * ext
    w = (mask.astype(np.float32) / 255.0)[:, :, None] * (amount * 0.6)
    out = result.astype(np.float32) + lap * w
    # 2) 整体 unsharp 强化（拿回高频）。
    sigma = max(0.4, 1.6 - amount)  # amount 0.6 → 1.0；amount 1.0 → 0.6
    blurred = cv2.GaussianBlur(out, (0, 0), sigmaX=sigma)
    sharp = out + amount * (out - blurred)
    out = out * (1.0 - 0.5 * amount) + sharp * (0.5 * amount)
    return np.clip(out, 0, 255).astype(np.uint8)


# ==============================================================================
# 对外主入口
# ==============================================================================
def _resolve_backend(real_backend, model_path: str, model_dir: str):
    """
    把 real_backend 参数解析成可用的 SuperResBackend 实例，或 None（回退轻量超分）。

    real_backend 支持三种形态（向后兼容）：
        · SuperResBackend 实例 —— 直接复用（视频场景一次性探测后整段复用，省去逐帧重探）；
        · 字符串（"auto"/"esrgan"/"realesrgan"） —— 当作 kind 交给工厂；
        · None / 其他 —— 回退 config.REAL_SUPERRES_BACKEND。
    """
    if isinstance(real_backend, SuperResBackend):
        return real_backend
    kind = real_backend if isinstance(real_backend, str) and real_backend else config.REAL_SUPERRES_BACKEND
    return get_superres_backend(kind, model_path=model_path, model_dir=model_dir)


def postprocess(
    result_rgb: np.ndarray,
    *,
    original_rgb: Optional[np.ndarray] = None,
    mask: Optional[np.ndarray] = None,
    enable: Optional[bool] = None,
    sharpen: Optional[float] = None,
    color_match: Optional[bool] = None,
    edge_blend: Optional[bool] = None,
    poisson_blend: Optional[bool] = None,
    poisson_strength: Optional[float] = None,
    texture_transfer: Optional[bool] = None,
    texture_amount: Optional[float] = None,
    artifact_reduce: Optional[bool] = None,
    artifact_amount: Optional[float] = None,
    superres: Optional[bool] = None,
    superres_amount: Optional[float] = None,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[object] = None,
    real_model_path: Optional[str] = None,
    real_model_dir: Optional[str] = None,
) -> np.ndarray:
    """
    对 inpaint 结果做后处理增强，返回提升观感的结果图。

    参数说明：
        result_rgb   : inpaint 输出的 H×W×3 uint8（BGR，OpenCV 排布）。
        original_rgb : 修复前的原图（边缘融合 / 色彩还原 / 阶段 21 三道深化需要；
                       缺省则跳过这些几何类步骤）。
        mask         : 0/255 掩码（被修复区域）；缺省则跳过几何类步骤。
        enable       : 总开关覆盖；None → config.POSTPROCESS_ENABLED。
        sharpen      : 锐化强度覆盖（浮点 0~2）；None → config.POSTPROCESS_SHARPEN_AMOUNT。
        color_match  : 色彩还原开关覆盖；None → config.POSTPROCESS_COLOR_MATCH。
        edge_blend   : 边缘融合开关覆盖；None → config.POSTPROCESS_EDGE_BLEND。
        poisson_blend: Poisson 无缝融合开关覆盖；None → config.POSTPROCESS_POISSON_BLEND。
        poisson_strength: Poisson 融合强度（0~1）；None → config.POSTPROCESS_POISSON_BLEND_STRENGTH。
        texture_transfer: 纹理细节重建开关覆盖；None → config.POSTPROCESS_TEXTURE_TRANSFER。
        texture_amount: 纹理重建强度（0~1）；None → config.POSTPROCESS_TEXTURE_TRANSFER_AMOUNT。
        artifact_reduce: 伪影抑制开关覆盖；None → config.POSTPROCESS_ARTIFACT_REDUCE。
        artifact_amount: 伪影抑制强度（0~1）；None → config.POSTPROCESS_ARTIFACT_REDUCE_AMOUNT。
        superres: 轻量超分（细节重建）开关覆盖；None → config.POSTPROCESS_SUPERRES_ENABLED。
        superres_amount: 超分强度（0~1）；None → config.POSTPROCESS_SUPERRES_AMOUNT。
        real_superres: 真·超分（AI 模型放大）开关覆盖；None → config.REAL_SUPERRES_ENABLED。
                       ⚠ 需**原图 + 掩码**才会执行（设计如此，见 docs/32-真·超分模块设计）⇒
                       缺二者时该开关**静默无效**（阶段 122 实测：无 original/mask 时尺寸不变）。
        real_scale    : 真实超分放大倍数（2/4）；None → config.REAL_SUPERRES_SCALE。
        real_backend  : 真实超分后端，可为 SuperResBackend 实例（直接复用）或
                        "auto"/"esrgan"/"realesrgan" 字符串（默认 kind）；None → config.REAL_SUPERRES_BACKEND。
                        视频场景一次性探测后整段复用同一实例，避免逐帧重复探测。
        real_model_path: 自定义 ESRGAN 权重路径；None → config.REAL_SUPERRES_MODEL_PATH。
        real_model_dir : Real-ESRGAN 权重目录；None → config.REAL_SUPERRES_MODEL_DIR。

    返回值：
        增强后的 H×W×3 uint8；总开关关闭时原样返回 result_rgb。
        注意：开启 real_superres 且**工厂产出后端**时，输出尺寸会被真实放大（如 4×）；
        后端不可用（**缺 torch / 缺 realesrgan 包**）时回退轻量超分（同分辨率）。
        ⚠ 阶段 115 起：`esrgan` 后端在**缺训练权重**时仍会**放大画布**（退化为纯双三次
        上采样），**不再**走「回退轻量超分」这条路——判断是否真·放大请看日志 note 或输出尺寸。
        ⚠ 阶段 122 勘误：旧文写「缺权重时自动回退轻量超分（同分辨率）」，与 115 后的实现不符。

    处理顺序：色彩还原 → 边缘融合 → 伪影抑制 → 纹理重建 → Poisson 融合 → 超分（真实优先·轻量回退）→ 锐化。
    阶段 21 三道深化、阶段 29 轻量超分与阶段 32 真·超分默认关闭，未传参时行为与阶段 18 完全一致（向后兼容）。
    真实超分与轻量超分互斥（不叠加），避免细节过锐 / 双重放大。
    """
    enable = config.POSTPROCESS_ENABLED if enable is None else enable
    if not enable:
        return result_rgb
    if not isinstance(result_rgb, np.ndarray):
        return result_rgb

    out = result_rgb
    m = _as_mask(mask)
    has_geom = _has_geom(original_rgb, m)

    # ① 色彩还原（需原图 + 掩码）
    do_cm = config.POSTPROCESS_COLOR_MATCH if color_match is None else color_match
    if do_cm and has_geom:
        out = _color_match(out, m, original_rgb, config.POSTPROCESS_COLOR_MATCH_STRENGTH)

    # ② 边缘融合（需原图 + 掩码）
    do_eb = config.POSTPROCESS_EDGE_BLEND if edge_blend is None else edge_blend
    if do_eb and has_geom:
        out = _edge_blend(out, m, original_rgb, config.POSTPROCESS_EDGE_BLEND_BAND)

    # ③ 阶段 21 · 伪影抑制（需掩码；不依赖原图）
    do_ar = config.POSTPROCESS_ARTIFACT_REDUCE if artifact_reduce is None else artifact_reduce
    ar_amt = config.POSTPROCESS_ARTIFACT_REDUCE_AMOUNT if artifact_amount is None else artifact_amount
    if do_ar and m is not None and m.sum() > 0 and ar_amt > 0:
        pre = out
        out = _artifact_reduce(out, m, ar_amt)
        out = _revert_if_worse(pre, out, original_rgb, m, "伪影抑制")

    # ④ 阶段 21 · 纹理细节重建（需原图 + 掩码）
    do_tt = config.POSTPROCESS_TEXTURE_TRANSFER if texture_transfer is None else texture_transfer
    tt_amt = config.POSTPROCESS_TEXTURE_TRANSFER_AMOUNT if texture_amount is None else texture_amount
    # 阶段 61：部署级放行闸——纹理重建实测净负收益（见 config 注释与 docs/61-*），
    # 仅当 POSTPROCESS_TEXTURE_ALLOW=True 时才可能真正执行。
    if do_tt and not config.POSTPROCESS_TEXTURE_ALLOW:
        log.info("纹理重建未获部署层放行（POSTPROCESS_TEXTURE_ALLOW=False），已跳过")
        do_tt = False
    if do_tt and has_geom and tt_amt > 0:
        pre = out
        out = _texture_transfer(out, m, original_rgb, tt_amt)
        out = _revert_if_worse(pre, out, original_rgb, m, "纹理重建")

    # ⑤ 阶段 21 · Poisson 无缝融合（需原图 + 掩码）
    do_pb = config.POSTPROCESS_POISSON_BLEND if poisson_blend is None else poisson_blend
    pb_str = config.POSTPROCESS_POISSON_BLEND_STRENGTH if poisson_strength is None else poisson_strength
    # 阶段 61：部署级放行闸——Poisson 对去水印不安全（见 config 注释与 docs/61-*），
    # 仅当 POSTPROCESS_POISSON_ALLOW=True 时才可能真正执行。
    if do_pb and not config.POSTPROCESS_POISSON_ALLOW:
        log.info("Poisson 融合未获部署层放行（POSTPROCESS_POISSON_ALLOW=False），已跳过")
        do_pb = False
    if do_pb and has_geom and pb_str > 0:
        pre = out
        out = _poisson_blend(out, m, original_rgb, pb_str)
        out = _revert_if_worse(pre, out, original_rgb, m, "Poisson 融合")

    # ⑥ 阶段 32 · 超分（真实优先，轻量回退）
    #    真实超分：torch + 预训练模型，改变画布尺寸（如 4×）。
    #    开启 real_superres 且后端可用 -> 真实放大；否则回退 阶段 29 轻量超分（同分辨率）。
    #    两者互斥（不叠加），避免细节过锐 / 双重放大。
    do_real = config.REAL_SUPERRES_ENABLED if real_superres is None else real_superres
    do_sr = config.POSTPROCESS_SUPERRES_ENABLED if superres is None else superres
    sr_amt = config.POSTPROCESS_SUPERRES_AMOUNT if superres_amount is None else superres_amount
    real_sr_note = None
    if do_real and has_geom:
        _scale = real_scale or config.REAL_SUPERRES_SCALE
        _mp = real_model_path if real_model_path is not None else config.REAL_SUPERRES_MODEL_PATH
        _md = real_model_dir if real_model_dir is not None else config.REAL_SUPERRES_MODEL_DIR
        backend = _resolve_backend(real_backend, model_path=_mp, model_dir=_md)
        out_sr, _used_real, real_sr_note = superres_upscale(out, backend, _scale)
        if out_sr is None:
            # 真实后端不可用 -> 回退轻量超分（若其开关开启）。
            if do_sr and sr_amt > 0:
                pre = out
                out = _superres(out, m, original_rgb, sr_amt)
                out = _revert_if_worse(pre, out, original_rgb, m, "轻量超分")
            if real_sr_note is None:
                real_sr_note = "真实超分后端不可用（缺 torch/权重），已回退轻量超分"
        else:
            out = out_sr
            log.info("真实超分已应用：%s", real_sr_note)
    elif do_sr and has_geom and sr_amt > 0:
        pre = out
        out = _superres(out, m, original_rgb, sr_amt)
        out = _revert_if_worse(pre, out, original_rgb, m, "轻量超分")

    # ⑦ 锐化（整图轻量，不依赖原图）
    amt = config.POSTPROCESS_SHARPEN_AMOUNT if sharpen is None else sharpen
    if config.POSTPROCESS_SHARPEN and amt > 0:
        pre_sharpen = out
        sharpened = _sharpen(out, amt)
        # 阶段 60：掩码内**不锐化**——inpaint 的填充区是"擦除结果"，unsharp 会放大其
        # 平滑面上的残留/接缝（实测 gradient grid/edge_text 掩码区 PSNR 因此掉 6~7 dB，
        # 见 docs/60-*）；保留 inpaint 原始结果保真度最高。内容区（掩码外）照常锐化。
        # 仅当掩码尺寸与当前画布一致时生效（真实超分改变画布后自动跳过恢复）。
        if m is not None and m.shape[:2] == pre_sharpen.shape[:2]:
            inside = m > 0
            if np.any(inside):
                out = np.where(inside[:, :, None], pre_sharpen, sharpened)
            else:
                out = sharpened
        else:
            out = sharpened

    return out


# ==============================================================================
# 与 quality_service 联动：优化前 / 后 质量对比
# ==============================================================================
def evaluate_improvement(
    original_rgb: np.ndarray,
    mask: Optional[np.ndarray],
    raw_result: np.ndarray,
    improved_result: np.ndarray,
) -> dict:
    """
    对比「原始 inpaint 结果」与「后处理结果」的质量分，量化增强带来的提升。

    参数说明：
        original_rgb   : 原图（评估基准）。
        mask           : 掩码（透传给 evaluate_quality，部分实现会用到）。
        raw_result     : 未后处理的 inpaint 结果。
        improved_result: 后处理后的结果。

    返回值（字典）：
        score_before / score_after : 优化前后的质量分。
        delta        : score_after - score_before。
        better       : delta > 0 即「后处理有提升」。
        detail       : 可直接展示的中文简述。
        level_before / level_after : 优化前后分级。
    """
    qb = evaluate_quality_arrays(original_rgb, raw_result)
    # 阶段 32：真实超分可能放大 improved_result 尺寸；评估前统一回缩到 raw 尺寸，
    # 保证「优化前/后」在同等像素基准上对比（质量分反映清晰度而非分辨率差异）。
    if improved_result.shape[:2] != raw_result.shape[:2]:
        improved_result = cv2.resize(
            improved_result,
            (raw_result.shape[1], raw_result.shape[0]),
            interpolation=cv2.INTER_AREA,
        )
    qa = evaluate_quality_arrays(original_rgb, improved_result)
    delta = round(qa.score - qb.score, 1)
    detail = (
        f"质量分 {qb.score} → {qa.score}"
        f"（{'+' if delta >= 0 else ''}{delta}）"
    )
    return {
        "score_before": qb.score,
        "score_after": qa.score,
        "delta": delta,
        "better": delta > 0,
        "detail": detail,
        "level_before": qb.level,
        "level_after": qa.level,
    }


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    # 阶段 122：自检必须**与环境无关**。本机可能通过 `config.user_overrides.json`
    # 打开了「真实超分」（实测本环境即如此：REAL_SUPERRES_ENABLED=true / scale=2），
    # 那会让下面每一条「形状不变」断言失败（`postprocess` 在 real_superres=None 时
    # 读 `config.REAL_SUPERRES_ENABLED`，故此处显式关掉即可；用例 7 另用**显式入参**
    # 单独测真·超分路径，不受影响）。
    config.REAL_SUPERRES_ENABLED = False

    # 合成图：浅灰底 + 一块「水印」灰块。
    base = np.full((300, 400, 3), 240, dtype=np.uint8)
    orig = base.copy()
    cv2.rectangle(orig, (100, 100), (300, 200), (200, 200, 200), -1)

    # 掩码：标记水印块。
    mask = np.zeros((300, 400), dtype=np.uint8)
    cv2.rectangle(mask, (100, 100), (300, 200), 255, -1)

    # 「原始 inpaint 结果」：填充区颜色略微偏离原图（模拟色偏）。
    raw = base.copy()
    cv2.rectangle(raw, (100, 100), (300, 200), (215, 215, 215), -1)

    # 后处理：色彩还原应把填充区拉回接近原图颜色。
    improved = postprocess(raw, original_rgb=orig, mask=mask)
    assert improved.shape == raw.shape
    imp = evaluate_improvement(orig, mask, raw, improved)
    print("后处理前后对比：", imp["detail"], "提升?" , imp["better"])

    # 关闭总开关应原样返回。
    same = postprocess(raw, original_rgb=orig, mask=mask, enable=False)
    assert np.array_equal(same, raw), "关闭总开关后不应改变结果"
    print("用例1通过：总开关关闭时透传原始结果。")

    # 无原图 / 掩码时仅锐化（不报错）。
    sharp_only = postprocess(raw, original_rgb=None, mask=None)
    assert sharp_only.shape == raw.shape
    print("用例2通过：缺原图/掩码时仅锐化不报错。")

    # 各子步骤独立开关。
    _ = postprocess(raw, original_rgb=orig, mask=mask, color_match=False, edge_blend=False)
    _ = postprocess(raw, original_rgb=orig, mask=mask, sharpen=0.0)
    print("用例3通过：子步骤独立开关正常。")

    # 阶段 21：三道深化 pass 不报错且形状一致。
    pb = postprocess(raw, original_rgb=orig, mask=mask,
                     poisson_blend=True, poisson_strength=1.0)
    assert pb.shape == raw.shape
    tt = postprocess(raw, original_rgb=orig, mask=mask,
                     texture_transfer=True, texture_amount=0.6)
    assert tt.shape == raw.shape
    ar = postprocess(raw, original_rgb=orig, mask=mask,
                     artifact_reduce=True, artifact_amount=0.6)
    assert ar.shape == raw.shape
    # 三道组合（全开）。
    combo = postprocess(raw, original_rgb=orig, mask=mask,
                        poisson_blend=True, poisson_strength=1.0,
                        texture_transfer=True, texture_amount=0.6,
                        artifact_reduce=True, artifact_amount=0.6)
    assert combo.shape == raw.shape
    print("用例4通过：阶段21三道深化 pass 形状一致、不报错、可组合。")

    # 阶段 29：轻量超分 pass 形状一致、不报错；amount=0 等同关闭（不改变结果）。
    sr = postprocess(raw, original_rgb=orig, mask=mask,
                     superres=True, superres_amount=0.8)
    assert sr.shape == raw.shape
    sr_off = postprocess(raw, original_rgb=orig, mask=mask,
                         superres=True, superres_amount=0.0)
    sr_disabled = postprocess(raw, original_rgb=orig, mask=mask)  # 超分默认关闭
    assert np.array_equal(sr_off, sr_disabled), "超分强度=0 应等同关闭（不改变结果）"
    print("用例6通过：阶段29轻量超分形状一致、amount=0 等同关闭。")

    # 阶段 32 / 122：真·超分路径。**尺寸是否改变取决于环境**——装了 torch 时 esrgan
    # 后端可用（缺训练权重则退化为**纯双三次放大**）⇒ 画布真的放大；无 torch 时后端为
    # None ⇒ 尺寸不变。故只断言「要么同尺寸、要么恰好 4× 放大」（旧断言硬编码「不应改变
    # 尺寸」，在装 torch 的环境上必然失败——阶段 122 实测）。
    rs = postprocess(raw, original_rgb=orig, mask=mask,
                     real_superres=True, real_scale=4)
    _h, _w = raw.shape[:2]
    assert rs.shape in ((_h, _w, 3), (_h * 4, _w * 4, 3)), f"真·超分尺寸异常：{rs.shape}"
    rs2 = postprocess(raw, original_rgb=orig, mask=mask,
                      real_superres=True, real_scale=4,
                      superres=True, superres_amount=0.8)
    assert rs2.shape == rs.shape, "真实/轻量超分互斥：两条路径输出尺寸应一致"
    print(f"用例7通过：阶段32真·超分 -> {rs.shape}（原 {raw.shape}；"
          f"{'后端可用，已放大' if rs.shape != raw.shape else '后端不可用，尺寸不变'}）。")

    # 阶段 21：边界贴边掩码时 Poisson 安全回退（不抛异常）。
    edge_mask = np.zeros((300, 400), dtype=np.uint8)
    cv2.rectangle(edge_mask, (0, 0), (80, 80), 255, -1)  # 贴左上角
    edge_raw = base.copy()
    cv2.rectangle(edge_raw, (0, 0), (80, 80), (210, 210, 210), -1)
    safe = postprocess(edge_raw, original_rgb=base, mask=edge_mask,
                       poisson_blend=True, poisson_strength=1.0)
    assert safe.shape == edge_raw.shape
    print("用例5通过：贴边掩码下 Poisson 安全回退。")
    print("postprocess_service.py 自检全部通过。")
