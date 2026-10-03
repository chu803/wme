# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/auto_pipeline.py
 文件说明 : 自动处理管线（阶段 8 核心服务，服务层）。

            把「从一张图片文件，到一张修复结果」的完整链路抽成独立函数，
            让【命令行 CLI】、【界面批量页】、【程序化库 api.py】、【REST 服务 server.py】
            四套入口都能复用同一套核心逻辑，避免重复代码：

                输入文件
                   │
                   ▼
            ① 读取图片（中文路径 / 透明通道友好）
                   │
           ┌───────┴────────┐
        auto 模式          manual 模式
           │                  │
     智能检测水印          读取同名/指定掩码图
     detect_watermark_mask    │
           │                  │
           └────────┬─────────┘
                    ▼
             ② 核心修复 inpaint()
                    │
                    ▼
             ③ 保存结果 save_result()

            设计约束：
            · 本模块属于「服务层」，只允许调用 core / utils / config / 本层 file_service，
              严禁 import ui 与 gradio（CLI 不依赖界面即可独立运行）。
            · 进度只通过回调 on_progress(p, msg) 抛出，不直接打印到控制台
              （CLI 自己决定怎么显示进度，界面则接到进度条）。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from typing import Optional, Callable, Dict, Any

# ------------------------------------------------------------------------------
# 路径加固：保证无论从哪个目录启动都能 import 到项目根模块。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

import config
from core.image_utils import load_image_full, load_image_with_alpha, save_image
from core.detector import detect_watermark_mask
from core.detection_presets import resolve_preset
from core.preset_recommender import recommend_preset
from core.inpainter import inpaint
from core.mask_editor import merge_masks, MASK_MODE_MERGE, MASK_MODE_MANUAL
from core.mask_generator import count_mask_pixels
from services.file_service import save_result
from services.postprocess_service import postprocess as pp_postprocess, evaluate_improvement
from utils.logger import get_logger

log = get_logger("services.auto_pipeline")

# 进度回调类型：第一个参数是 0~1 的进度比例，第二个是提示文本。
ProgressCallback = Callable[[float, str], None]


def _noop_progress(p: float, msg: str) -> None:
    """默认空回调：不干任何事，方便 CLI 不传进度时也能跑。"""
    return None


def _same_name_mask(input_path: str) -> Optional[str]:
    """
    为某张原图寻找「同名掩码图」，约定后缀：
        原图 a.jpg  →  掩码 a.mask.png  /  a_mask.png  /  a.mask.jpg
    返回首个存在的路径，找不到返回 None。
    """
    stem = _os.path.splitext(input_path)[0]
    candidates = [
        stem + ".mask.png",
        stem + "_mask.png",
        stem + ".mask.jpg",
        stem + "_mask.jpg",
    ]
    for c in candidates:
        if _os.path.exists(c):
            return c
    return None


def _mask_from_annotation(arr: np.ndarray) -> np.ndarray:
    """
    把「人工标注的掩码图」（黑底 + 任意颜色涂抹）转成单通道 0/255 掩码。

    约定：近黑像素视为「不处理」，任何有颜色涂抹的像素视为「要修复的区域」。
    这样无论用户用红笔、绿笔还是白笔涂抹，都能正确识别。
    """
    # 取前三个通道（兼容 RGB / RGBA 输入），任一通道明显非黑即算涂抹。
    rgb = arr[:, :, :3] if arr.ndim == 3 else arr
    painted = np.any(rgb > 10, axis=2)
    return painted.astype(np.uint8) * 255


def process_image(
    rgb: np.ndarray,
    *,
    mask: Optional[np.ndarray] = None,
    mode: str = "auto",
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    enhance_tiled: Optional[bool] = None,
    search_bottom: Optional[bool] = None,
    device: Optional[object] = None,
    on_progress: Optional[ProgressCallback] = None,
    postprocess: Optional[bool] = None,
    sharpen: Optional[float] = None,
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
    real_backend: Optional[str] = None,
    real_model_path: Optional[str] = None,
    real_model_dir: Optional[str] = None,
    user_mask: Optional[np.ndarray] = None,
    mask_mode: Optional[str] = None,
    postprocess_eval: bool = False,
    dual_run: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    数组级原子单元：对「已读入内存的 RGB 数组」完成 检测 + 修复，返回结果数组。

    这是 CLI / GUI / 程序库 / REST 服务四套入口共用的「同一套核心逻辑」。
    文件读写、网络编解码交给各入口自行处理，本函数只管像素。

    参数：
        rgb        : H×W×3 的 uint8 数组（BGR，OpenCV 默认排布）。
        mask       : 单通道 0/255 掩码（manual 模式必备）；auto 模式传 None 即自动检测。
        mode/engine/各修复参数/灵敏度 : 与 process_file 一致（缺省取 config）。
        user_mask  : （阶段 24，仅 auto 模式生效）用户精修掩码，检测后按 mask_mode 合并。
        mask_mode  : 精修合并模式 "merge"（检测∪用户，默认）/ "manual"（用户∩¬检测）；
                     缺省取 config.MASK_EDIT_DEFAULT_MODE。
        on_progress: 进度回调 (p, msg)。

    返回字典：
        status     : "success" / "skipped" / "failed"
        result_rgb : 修复后的 RGB 数组（仅 success）
        mask_pixels: 掩码像素数
        regions    : 检测区域数（仅 auto）
        used_ocr   : 是否启用 OCR（仅 auto）
        user_mask_pixels : 用户精修掩码像素数（未传 user_mask 时为 0）
        mask_mode  : 实际生效的精修合并模式
        engine     : 实际引擎
        error      : 失败原因（仅 failed/skipped）
    """
    on_progress = on_progress or _noop_progress

    # 参数回填默认值（与 process_file 完全一致，保证四入口行为统一）。
    strength = config.INPAINT_STRENGTH_DEFAULT if strength is None else strength
    dilate = config.MASK_DILATE_DEFAULT if dilate is None else dilate
    feather = config.MASK_FEATHER_DEFAULT if feather is None else feather
    threshold = config.MASK_THRESHOLD_DEFAULT if threshold is None else threshold
    denoise = config.DENOISE_STRENGTH_DEFAULT if denoise is None else denoise
    sensitivity = config.DETECT_TRANSLUCENT_SENSITIVITY if sensitivity is None else sensitivity
    engine = config.INPAINT_ENGINE if engine is None else engine.lower()

    used_ocr = False
    regions = 0
    det_stats = None  # 阶段 23：检测命中率统计（仅自动检测模式有值）。
    user_pixels = 0  # 阶段 24：用户精修掩码像素数（未传 user_mask 时为 0）。
    used_mask_mode = config.MASK_EDIT_DEFAULT_MODE

    # 阶段 25：自动预设推荐（仅当未显式指定 preset 且开启 auto_preset 时）。
    #   自动分析图像特征挑出最契合的检测预设，省去用户手动试错；推荐结果透传回调用方，
    #   供界面展示「推荐理由」。推荐失败不影响主流程（回退默认检测）。
    recommendation = None       # Recommendation.to_dict() 或 None
    recommended_preset = None   # 推荐出的预设名（可能等于默认 general）
    if mode != "manual" and preset is None and auto_preset:
        try:
            rec = recommend_preset(rgb, sensitivity=sensitivity)
            recommended_preset = rec.preset
            recommendation = rec.to_dict()
            log.info(
                "自动预设推荐：%s（置信度 %.2f，触发特征 %s）",
                rec.preset, rec.confidence, ",".join(rec.triggered) or "无",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("自动预设推荐失败，回退默认检测：%s", exc)

    if mode == "manual":
        # ---------- 手动掩码模式 ----------
        if mask is None:
            return {
                "status": "failed",
                "error": "manual 模式必须提供 mask 数组（或先读取掩码图）",
            }
        # 直接使用调用方给定的掩码，不做检测。
        # 注：user_mask 仅 auto 模式生效，manual 模式调用方应直接把最终掩码传给 mask。
    else:
        # ---------- 智能检测模式 ----------
        on_progress(0.10, "智能检测水印……")
        # 把检测进度（0~1）平滑映射到 [0.10, 0.40] 段。
        def _det(p: float, msg: str) -> None:
            on_progress(0.10 + p * 0.30, msg)

        # 阶段 20/25：检测预设解析（显式 preset 优先 → 自动推荐 preset → 默认 bare）。
        #   注意：推荐到默认 general 时不覆盖裸参数（保持与旧行为完全一致）。
        if preset:
            _dkw = resolve_preset(preset, sensitivity)
        elif recommended_preset and recommended_preset != config.RECOMMEND_DEFAULT_PRESET:
            _dkw = resolve_preset(recommended_preset, sensitivity)
        else:
            _dkw = {"sensitivity": sensitivity}
            if enhance_tiled is not None:
                _dkw["enhance_tiled"] = enhance_tiled
            if search_bottom is not None:
                _dkw["search_bottom"] = search_bottom
        result = detect_watermark_mask(rgb, progress=_det, **_dkw)
        mask = result.mask
        used_ocr = result.used_ocr
        regions = len(result.regions)
        det_stats = result.stats

        # ---------- 阶段 24：可选用户精修掩码合并（仅 auto 模式）----------
        if user_mask is not None:
            used_mask_mode = str(mask_mode or config.MASK_EDIT_DEFAULT_MODE).lower()
            if used_mask_mode not in (MASK_MODE_MERGE, MASK_MODE_MANUAL):
                log.warning(
                    "未知掩码精修模式 %r，回退为 %r。", used_mask_mode, MASK_MODE_MERGE
                )
                used_mask_mode = MASK_MODE_MERGE
            mask = merge_masks(
                mask, user_mask, used_mask_mode, target_shape=rgb.shape[:2]
            )
            user_pixels = int(count_mask_pixels(user_mask))

    # 掩码为空 → 没什么可修的，标记 skipped 而非失败。
    mask_pixels = int((mask > 0).sum()) if mask is not None else 0
    if mask_pixels == 0:
        return {
            "status": "skipped",
            "mask_pixels": 0,
            "regions": regions,
            "used_ocr": used_ocr,
            "stats": det_stats,
            "user_mask_pixels": user_pixels,
            "mask_mode": used_mask_mode,
            "recommended_preset": recommended_preset,
            "recommendation": recommendation,
            "error": "未检测到水印区域",
        }

    # ---------- 核心修复 ----------
    on_progress(0.45, "修复中……")

    def _inp(p: float, msg: str) -> None:
        on_progress(0.45 + p * 0.45, msg)

    result_rgb = inpaint(
        rgb,
        mask,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        engine=engine,
        device=device,
        dual_run=dual_run,
        progress=_inp,
    )

    # ---------- 结果后处理与画质增强（阶段 18）----------
    raw_result = result_rgb
    result_rgb = pp_postprocess(raw_result, original_rgb=rgb, mask=mask,
                                enable=postprocess, sharpen=sharpen,
                                poisson_blend=poisson_blend,
                                poisson_strength=poisson_strength,
                                texture_transfer=texture_transfer,
                                texture_amount=texture_amount,
                                artifact_reduce=artifact_reduce,
                                artifact_amount=artifact_amount,
                                superres=superres,
                                superres_amount=superres_amount,
                                real_superres=real_superres,
                                real_scale=real_scale,
                                real_backend=real_backend,
                                real_model_path=real_model_path,
                                real_model_dir=real_model_dir)

    # 可选：优化前 / 后 质量对比（联动 quality_service）。
    imp = None
    if postprocess_eval:
        try:
            imp = evaluate_improvement(rgb, mask, raw_result, result_rgb)
        except Exception as exc:  # noqa: BLE001
            log.warning("后处理质量对比失败：%s", exc)

    return {
        "status": "success",
        "result_rgb": result_rgb,
        # 阶段 10 新增：把本次实际使用的掩码一并返回。
        # 视频场景下，固定位置水印（台标、角标）可直接复用上一帧的掩码，
        # 省掉重复检测的耗时。对已有调用方无影响 —— 只是字典多了一个键。
        "mask": mask,
        "mask_pixels": mask_pixels,
        "regions": regions,
        "used_ocr": used_ocr,
        "stats": det_stats,
        "user_mask_pixels": user_pixels,
        "mask_mode": used_mask_mode,
        "engine": engine,
        # 阶段 25：自动预设推荐结果（供界面/CLI 展示「机器为什么这么选」）。
        "recommended_preset": recommended_preset,
        "recommendation": recommendation,
        # 阶段 18：优化前 / 后 质量分（仅 postprocess_eval=True 时存在）。
        "quality_before": imp["score_before"] if imp else None,
        "quality_after": imp["score_after"] if imp else None,
        "quality_delta": imp["delta"] if imp else None,
        "quality_better": imp["better"] if imp else None,
        # 阶段 32：真实超分可能放大输出尺寸；标记结果是否已被放大（供报告/界面提示）。
        "upscaled": result_rgb.shape != raw_result.shape,
    }


def process_file(
    input_path: str | _os.PathLike,
    *,
    mode: str = "auto",
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    enhance_tiled: Optional[bool] = None,
    search_bottom: Optional[bool] = None,
    postprocess: Optional[bool] = None,
    sharpen: Optional[float] = None,
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
    real_backend: Optional[str] = None,
    dual_run: Optional[bool] = None,
    real_model_path: Optional[str] = None,
    real_model_dir: Optional[str] = None,
    mask_path: Optional[str | _os.PathLike] = None,
    out_fmt: Optional[str] = None,
    quality: Optional[int] = None,
    out_dir: Optional[str | _os.PathLike] = None,
    device: Optional[object] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """
    处理单张图片文件的原子单元（auto / manual 两种模式）。

    参数说明：
        input_path : 原图路径（支持中文路径）。
        mode       : "auto"（智能检测水印）或 "manual"（用掩码图修复）。
        engine     : 修复引擎 "lama"/"opencv"/"auto"，缺省取 config。
        strength/dilate/feather/threshold/denoise : 修复参数，缺省取 config。
        sensitivity : 仅 auto 模式用，半透明检测敏感度，缺省取 config。
        mask_path  : 仅 manual 模式用，指定掩码图；缺省自动找同名掩码。
        out_fmt    : 输出格式（PNG/JPG/WEBP/TIFF/AVIF），缺省取 config。
        quality    : 输出画质 1~100，仅 JPG/WebP 生效。
        on_progress: 进度回调 (p: float, msg: str)。

    返回值（字典）：
        status      : "success" / "skipped"（没检测到水印）/ "failed"（出错）
        input_path  : 原图路径
        original_name : 原图文件名
        out_path    : 结果图路径（仅 success 有）
        mask_pixels : 掩码像素数
        regions     : 检测到的水印区域数（仅 auto）
        used_ocr    : 是否启用了 OCR（仅 auto）
        engine      : 实际使用的引擎
        error       : 失败原因（仅 failed）
    """
    on_progress = on_progress or _noop_progress
    input_path = str(input_path)
    original_name = _os.path.basename(input_path)

    if not _os.path.exists(input_path):
        return {
            "status": "failed",
            "input_path": input_path,
            "original_name": original_name,
            "error": f"文件不存在：{input_path}",
        }

    # 参数回填默认值。
    strength = config.INPAINT_STRENGTH_DEFAULT if strength is None else strength
    dilate = config.MASK_DILATE_DEFAULT if dilate is None else dilate
    feather = config.MASK_FEATHER_DEFAULT if feather is None else feather
    threshold = config.MASK_THRESHOLD_DEFAULT if threshold is None else threshold
    denoise = config.DENOISE_STRENGTH_DEFAULT if denoise is None else denoise
    sensitivity = config.DETECT_TRANSLUCENT_SENSITIVITY if sensitivity is None else sensitivity
    engine = config.INPAINT_ENGINE if engine is None else engine.lower()

    on_progress(0.02, f"读取图片：{original_name}")
    # 阶段 73：同时取出内嵌 ICC 色彩配置（宽色域输入导出不偏色的前提）。
    rgb, alpha, icc = load_image_full(input_path)

    # 手动模式：先解析出掩码数组（auto 模式留空，交给 process_image 检测）。
    mask: Optional[np.ndarray] = None
    if mode == "manual":
        mp = str(mask_path) if mask_path else _same_name_mask(input_path)
        if not mp or not _os.path.exists(mp):
            return {
                "status": "failed",
                "input_path": input_path,
                "original_name": original_name,
                "error": "未找到掩码图（可传 --mask 指定，或放置同名 .mask.png）",
            }
        m_rgb, _ = load_image_with_alpha(mp)
        mask = _mask_from_annotation(m_rgb)

    # ---------- 调用数组级原子单元（检测 + 修复）----------
    # process_image 内部负责：参数回填、auto 检测、空掩码跳过、核心修复。
    # 文件读写由本函数负责，网络/内存编解码交给 api.py / server.py，四入口共用此核心。
    core = process_image(
        rgb,
        mask=mask,
        mode=mode,
        engine=engine,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        sensitivity=sensitivity,
        preset=preset,
        auto_preset=auto_preset,
        enhance_tiled=enhance_tiled,
        search_bottom=search_bottom,
        postprocess=postprocess,
        sharpen=sharpen,
        poisson_blend=poisson_blend,
        poisson_strength=poisson_strength,
        texture_transfer=texture_transfer,
        texture_amount=texture_amount,
        artifact_reduce=artifact_reduce,
        artifact_amount=artifact_amount,
        superres=superres,
        superres_amount=superres_amount,
        real_superres=real_superres,
        real_scale=real_scale,
        real_backend=real_backend,
        dual_run=dual_run,
        real_model_path=real_model_path,
        real_model_dir=real_model_dir,
        device=device,
        on_progress=on_progress,
    )
    if core["status"] == "failed":
        core["input_path"] = input_path
        core["original_name"] = original_name
        return core
    if core["status"] == "skipped":
        return {
            "status": "skipped",
            "input_path": input_path,
            "original_name": original_name,
            "mask_pixels": core.get("mask_pixels", 0),
            "regions": core.get("regions", 0),
            "used_ocr": core.get("used_ocr", False),
            "recommended_preset": core.get("recommended_preset"),
            "recommendation": core.get("recommendation"),
            "error": "未检测到水印区域（已跳过）",
        }

    result_rgb = core["result_rgb"]

    # ---------- 保存结果 ----------
    on_progress(0.92, "保存结果……")
    if out_dir is not None:
        # CLI 场景：结果直接落到用户指定目录（不再进 outputs/日期子目录）。
        from utils.path_helper import build_output_filename, make_unique_path

        ext = (out_fmt or config.DEFAULT_OUTPUT_FORMAT).lower()
        fname = build_output_filename(_os.path.splitext(original_name)[0], ext)
        target = make_unique_path(str(out_dir), fname)
        out_path = save_image(target, result_rgb, fmt=out_fmt, quality=quality,
                              alpha=alpha, icc=icc)
    else:
        # 界面场景：沿用统一 outputs 目录（按日期分文件夹）。
        out_path = save_result(
            result_rgb,
            _os.path.splitext(original_name)[0],
            fmt=out_fmt,
            quality=quality,
            alpha=alpha,
        )
    on_progress(1.0, "完成")

    return {
        "status": "success",
        "input_path": input_path,
        "original_name": original_name,
        "out_path": out_path,
        "mask_pixels": core.get("mask_pixels", 0),
        "regions": core.get("regions", 0),
        "used_ocr": core.get("used_ocr", False),
        "engine": core.get("engine", engine),
        "recommended_preset": core.get("recommended_preset"),
        "recommendation": core.get("recommendation"),
        "upscaled": core.get("upscaled", False),
    }


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    import tempfile
    from PIL import Image, ImageDraw  # type: ignore

    _require_cv2 = __import__("core.image_utils", fromlist=["_require_cv2"])._require_cv2
    cv2 = _require_cv2()

    # 造一张带半透明水印的测试图。
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    img = Image.new("RGB", (320, 200), (240, 240, 240))
    d = ImageDraw.Draw(img, "RGBA")
    for j in range(4):
        d.text((20 + j * 45, 80 + (j % 2) * 30), "WATERMARK", fill=(170, 170, 170, 140))
    img.save(tmp.name)

    # 用 OpenCV 降级引擎跑一次自动修复（沙箱无 torch/模型也能验证管线）。
    rd = process_file(
        tmp.name, mode="auto", engine="opencv",
        strength=1.0, dilate=4, feather=4, threshold=200, denoise=3, sensitivity=0.5,
        on_progress=lambda p, m: print(f"  [进度 {p:.0%}] {m}"),
    )
    print("返回字典：", rd)
    assert rd["status"] in ("success", "skipped"), "管线状态异常"
    if rd["status"] == "success":
        assert _os.path.exists(rd["out_path"]), "结果文件未生成"
        print(f"用例通过：自动修复成功，结果={rd['out_path']}，"
              f"掩码像素={rd['mask_pixels']}，区域数={rd['regions']}")
    else:
        print("用例通过（沙箱检测未命中，但管线流程完整无异常）：", rd["error"])

    # 阶段 127：补手动掩码模式用例。此前自检只覆盖 auto 模式，manual 分支的
    # `load_image_with_alpha` 漏 import 才得以长期蒙混（D1）。这里把 manual 路径
    # 也纳入子进程自检覆盖。
    mask_path = _os.path.splitext(tmp.name)[0] + ".mask.png"
    mimg = Image.new("RGB", (320, 200), (0, 0, 0))
    ImageDraw.Draw(mimg).rectangle([100, 60, 220, 140], fill=(255, 255, 255))
    mimg.save(mask_path)
    rd_m = process_file(
        tmp.name, mode="manual", mask_path=mask_path, engine="opencv",
        strength=1.0, dilate=4, feather=4, threshold=200, denoise=3,
        real_superres=False, superres=False,
    )
    assert rd_m["status"] == "success", f"manual 模式异常：{rd_m.get('error')}"
    assert _os.path.exists(rd_m["out_path"]), "manual 结果文件未生成"
    print(f"用例通过：手动掩码修复成功，结果={rd_m['out_path']}，"
          f"掩码像素={rd_m['mask_pixels']}")
    _os.remove(mask_path)

    _os.remove(tmp.name)
    print("auto_pipeline.py 自检通过。")
