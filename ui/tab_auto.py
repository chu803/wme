# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/tab_auto.py
 文件说明 : 智能自动检测标签页（阶段 4 核心界面）。
            提供：图片上传 → 一键自动检测水印 → 检测可视化预览 →
            在编辑框中手动增涂/擦除微调 → 一键修复 → 左右对比预览 → 下载。

            自动检测由 core.detector.detect_watermark_mask 完成（半透明水印 /
            角落 LOGO / 边缘小字三策略合并）。检测结果会被预填进 ImageEditor
            的图层，用户可在此基础上画笔增涂或橡皮擦除，实现「机器初检 + 人工精修」。

            本文件属于界面层（ui），只负责「收集操作、展示结果」，
            所有算法都委托给 core / services 层，自身不写检测或修复逻辑。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import html as _html
import os as _os
import sys as _sys
import time as _time
from typing import Optional

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import gradio as gr

import config
from utils.i18n import t
from ui.i18n_rt import reg_label, reg_label_info, reg_value, reg_textbox
from core.detector import detect_watermark_mask, visualize_detection
from core.detection_presets import list_presets
from core.preset_recommender import recommend_preset
from utils.recommend_view import markdown as _build_recommend_markdown
from core.inpainter import inpaint
from core.mask_generator import build_mask_from_editor, count_mask_pixels
from core.mask_editor import merge_masks, MASK_MODE_MERGE, MASK_MODE_MANUAL
from services.file_service import save_result
from services import compare_service as _cs
from services import export_service as _es
from services.postprocess_service import postprocess as pp_postprocess, evaluate_improvement
from services.validators import validate_numpy_image
from services.cache_service import get_cache
from ui.components import (
    get_engine_choices, build_detection_stats_markdown, register_superres_widget,
    repair_params_text,
)
from utils.exceptions import EmptyMaskError, ImageTooLargeError, format_exception_for_ui
from utils.logger import get_logger

log = get_logger("ui.tab_auto")
_CACHE = get_cache()


def _preset_choice_label(name: str, default_display: str) -> str:
    """
    把预设名映射成本地化展示名。

    优先用 i18n 键 detect.preset.<name>.title；若翻译缺失（t() 会把键本身返回），
    则回退到预设内置的中文 display，保证界面绝不出现裸键名。
    """
    label = t(f"detect.preset.{name}.title")
    if label.startswith("detect.preset."):
        return default_display
    return label


def _extract_image(editor_value):
    """
    从 ImageEditor 返回值中安全提取「原图 numpy 数组」。

    参数说明：
        editor_value : ImageEditor 返回值，支持 dict（Gradio 6.x, 取 'background'）
                       或 (image, mask) 元组 / 单 ndarray（兼容旧版）。

    返回值：
        原图数组，或 None（未上传时）。
    """
    if editor_value is None:
        return None
    if isinstance(editor_value, dict):
        return editor_value.get("background")
    if isinstance(editor_value, (tuple, list)):
        return editor_value[0] if len(editor_value) >= 1 else None
    if isinstance(editor_value, np.ndarray):
        return editor_value
    return None


def _mask_to_rgba_layer(mask: np.ndarray, color=(255, 59, 48)) -> np.ndarray:
    """
    把二值掩码包装成「红色半透明图层」，用于预填进 ImageEditor 供用户微调。

    参数说明：
        mask  : (H, W) uint8 0/255 掩码。
        color : RGB 颜色元组（默认番茄红），代表水印待擦除区域。

    返回值：
        (H, W, 4) uint8 RGBA 数组，alpha 通道 = 掩码值（255 处不透明）。

    ⚠ 阶段 314b（实测）：Gradio 6.x 在往返编辑框时会把图层**压成 RGB、丢弃 alpha**。
    因此不能「RGB 全图刷红 + 靠 alpha 表达掩码」——alpha 一丢，整层灰度恒 ≈116 > 阈值 10
    ⇒ 整图被判为涂抹区 ⇒ 一键修复重绘整张图（实测 82% 像素被改、状态栏「你补画 413 万像素」）。
    故只在掩码处上色，其余保持纯黑：丢 alpha 后灰度仍是 0，阈值可正确区分。
    """
    h, w = mask.shape[:2]
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    on = mask > 0
    rgba[on, 0] = color[0]
    rgba[on, 1] = color[1]
    rgba[on, 2] = color[2]
    rgba[:, :, 3] = mask  # alpha 仍写（RGBA 感知的通路用得上）；RGB 侧已自带掩码语义
    return rgba


def process_recommend_preset(upload):
    """
    智能推荐预设（阶段 25）：分析已上传原图特征，挑出最契合的检测预设。

    返回值：
        二元组 (推荐预设名, 推荐理由 Markdown)，分别更新「检测预设」下拉与理由展示框。
    """
    if upload is None:
        return config.DETECT_PRESET_DEFAULT, t("detect.recommend.msg.no_upload")
    try:
        validate_numpy_image(upload)
    except (ImageTooLargeError, ValueError) as exc:
        return config.DETECT_PRESET_DEFAULT, t("detect.recommend.msg.bad_image", exc=format_exception_for_ui(exc))
    try:
        rec = recommend_preset(upload, sensitivity=config.DETECT_TRANSLUCENT_SENSITIVITY)
    except Exception as exc:
        log.warning("自动预设推荐失败：%s", exc)
        return config.DETECT_PRESET_DEFAULT, t("detect.recommend.msg.fail", exc=format_exception_for_ui(exc))
    return rec.preset, _build_recommend_markdown(rec)


def process_auto_detect(
    upload,
    sensitivity: float,
    en_translucent: bool,
    en_corner: bool,
    en_edge: bool,
    preset: str = "general",
):
    """
    自动检测主回调：上传图 → 检测 → 生成可视化预览 + 预填编辑框。

    参数说明：
        upload           : 上传的原图（numpy 数组）。
        sensitivity      : 半透明检测敏感度滑块值（0~1）。
        en_translucent   : 是否启用半透明检测。
        en_corner        : 是否启用角落 LOGO 检测。
        en_edge          : 是否启用边缘小字检测。
        preset           : 检测预设名（阶段 20，来自 core.detection_presets）。

    返回值：
        五元组 (detect_preview, editor_value, status_text, detect_stats_md, det_mask)，
        对应 5 个输出组件（detect_stats_md 为「检测命中率」Markdown；
        det_mask 为本次检测掩码，存入 gr.State 供修复回调做精修合并）。
    """
    # 未上传图片。
    if upload is None:
        return (
            None,
            None,
            t("auto.msg.no_upload"),
            build_detection_stats_markdown(None),
            None,
        )

    # 分辨率防御性校验。
    try:
        validate_numpy_image(upload)
    except ImageTooLargeError as exc:
        return upload, None, t("single.msg.too_large", exc=format_exception_for_ui(exc)), build_detection_stats_markdown(None), None
    except ValueError as exc:
        return upload, None, t("single.msg.bad_data", exc=format_exception_for_ui(exc)), build_detection_stats_markdown(None), None

    # 调用核心检测。
    try:
        result = detect_watermark_mask(
            upload,
            sensitivity=float(sensitivity),
            enable_translucent=bool(en_translucent),
            enable_corner=bool(en_corner),
            enable_edge=bool(en_edge),
            use_ocr=config.DETECT_USE_OCR,
            preset=str(preset or "general"),
        )
    except Exception as exc:
        log.exception("自动检测异常")
        return (
            upload,
            None,
            t("auto.msg.detect_fail", exc=format_exception_for_ui(exc)),
            build_detection_stats_markdown(None),
            None,
        )

    mask = result.mask

    # 未检测到任何水印：预填一个空图层，引导用户手动涂抹。
    if count_mask_pixels(mask) == 0:
        editor_value = {
            "background": upload,
            "layers": [],
            "composite": None,
        }
        return (
            upload,
            editor_value,
            t("auto.msg.no_detect"),
            build_detection_stats_markdown(result.stats),
            mask,
        )

    # 检测可视化：原图 + 半透明红层 + 彩色类别框。
    try:
        preview = visualize_detection(upload, result)
    except Exception as exc:
        log.warning("可视化生成失败，退回原图：%s", exc)
        preview = upload

    # 预填编辑框：背景=原图，图层=红色掩码（用户可在此基础上增涂/擦除）。
    mask_rgba = _mask_to_rgba_layer(mask)
    editor_value = {
        "background": upload,
        "layers": [mask_rgba],
        "composite": None,
    }

    ocr_tip = t("auto.msg.ocr_on") if result.used_ocr else t("auto.msg.ocr_off")
    status = t("auto.msg.detect_done", n=len(result.regions), ocr=ocr_tip)
    stats_md = build_detection_stats_markdown(result.stats)
    return preview, editor_value, status, stats_md, mask


def process_auto_repair(
    editor_value,
    engine: str,
    strength: float,
    dilate: int,
    feather: int,
    threshold: int,
    denoise: int,
    enable_postprocess: bool = True,
    sharpen: Optional[float] = None,
    postprocess_eval: bool = False,
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
    det_mask=None,
    mask_mode: Optional[str] = None,
    real_backend: Optional[str] = None,
    dual_run: "bool | None" = None,
    record: bool = True,
):
    """
    自动模式「一键修复」回调：从编辑框取图与掩码 → 修复 → 保存 → 回传预览。

    ⚠ **阶段 110 修复**：`record`（内部开关，界面无控件）原排在 `dual_run`（UI 控件）**之前**，
    而 Gradio 按位置传参、inputs 只有 24 项 ⇒ `dual_run` 复选框被绑到 `record` 上、
    `dual_run` 恒吃默认值 ⇒ **「双跑择优」在自动页同样完全无效**（与单图页同一处缺陷，
    由 `%TEMP%/s110_wiring.py` 的全 ui 扫描一并发现）。
    契约：**内部开关形参必须排在全部 UI 控件形参之后**（守卫见
    `tests/test_ui_wiring_contract110.py`）。
  
    参数说明：
        editor_value : 检测后预填（并可能经用户微调）的 ImageEditor 值。
        engine/strength/dilate/feather/threshold/denoise : 修复参数面板输入。
        det_mask     : 最近一次自动检测掩码（gr.State，阶段 24 精修合并用）。
        mask_mode    : 精修模式 "merge"（检测∪精修，默认）/ "manual"（仅用户笔迹）。

    返回值：
        五元组 (orig_view, result_view, compare_view, status_text, download_file)。
    """
    # 解析原图。
    image = _extract_image(editor_value)
    if image is None:
        return (
            None,
            None,
            None,
            t("auto.msg.no_editor"),
            None,
        )

    # 由编辑框构建标准掩码（含用户手动微调；merge 模式下已含预填的检测层）。
    try:
        user_mask = build_mask_from_editor(
            editor_value,
            brush_threshold=10,
            target_size=image.shape[:2],
        )
    except Exception as exc:
        log.exception("掩码构建失败")
        return image, None, None, t("single.msg.mask_fail", exc=format_exception_for_ui(exc)), None

    # 阶段 24：精修模式解析 + 检测掩码 × 用户掩码 合并。
    mode = str(mask_mode or config.MASK_EDIT_DEFAULT_MODE).lower()
    if mode not in (MASK_MODE_MERGE, MASK_MODE_MANUAL):
        log.warning("未知掩码精修模式 %r，回退为 %r。", mode, MASK_MODE_MERGE)
        mode = MASK_MODE_MERGE
    det = det_mask if isinstance(det_mask, np.ndarray) and det_mask.ndim == 2 else None

    auto_fallback = False
    if mode == MASK_MODE_MERGE:
        if count_mask_pixels(user_mask) == 0:
            # 编辑框为空：merge 模式先用检测掩码；仍无则自动兜底检测（默认预设），
            # 让「上传后直接点一键修复」也能工作。
            if det is None or count_mask_pixels(det) == 0:
                try:
                    auto_res = detect_watermark_mask(
                        image,
                        preset=config.DETECT_PRESET_DEFAULT,
                        use_ocr=config.DETECT_USE_OCR,
                    )
                    det = auto_res.mask
                    auto_fallback = True
                except Exception as exc:  # noqa: BLE001
                    log.warning("自动兜底检测失败：%s", exc)
                    det = None
            mask = det if det is not None else user_mask
        else:
            # 编辑框所见即所得（已含预填检测层）：直接用编辑框掩码。
            mask = user_mask
    else:
        # 仅精修：剥离预填的检测层，只保留用户亲手绘制的笔迹。
        mask = merge_masks(det, user_mask, MASK_MODE_MANUAL, target_shape=image.shape[:2])

    if mask is None:
        mask = np.zeros(image.shape[:2], dtype=np.uint8)

    # 空掩码检测。
    if count_mask_pixels(mask) == 0:
        return (
            image,
            None,
            None,
            t("auto.msg.no_mask"),
            None,
        )

    # 进度回调（阶段 5 批量处理时接入实时进度条）。
    def _progress(p: float, msg: str) -> None:
        log.info("自动修复进度 %.0f%%：%s", p * 100, msg)

    # 调用核心修复引擎。
    try:
        result = inpaint(
            image,
            mask,
            strength=strength,
            dilate=dilate,
            feather=feather,
            threshold=threshold,
            denoise=denoise,
            engine=engine,
            progress=_progress,
            dual_run=dual_run,
        )
    except EmptyMaskError as exc:
        return image, None, None, t("single.msg.no_mask"), None
    except FileNotFoundError as exc:
        return (
            image,
            None,
            None,
            t("single.msg.no_model", exc=format_exception_for_ui(exc)),
            None,
        )
    except Exception as exc:
        log.exception("自动修复异常")
        return image, None, None, t("single.msg.fail", exc=format_exception_for_ui(exc)), None

    # 阶段 18：修复后自动叠加画质增强（锐化 + 色彩还原 + 边缘融合）。
    raw_result = result
    result = pp_postprocess(
        raw_result, original_rgb=image, mask=mask,
        enable=enable_postprocess, sharpen=sharpen,
        poisson_blend=poisson_blend, poisson_strength=poisson_strength,
        texture_transfer=texture_transfer, texture_amount=texture_amount,
        artifact_reduce=artifact_reduce, artifact_amount=artifact_amount,
        superres=superres, superres_amount=superres_amount,
        real_superres=real_superres, real_scale=real_scale, real_backend=real_backend,
    )
    # 可选：优化前 / 后 质量对比，附到状态文案（联动 quality_service）。
    if postprocess_eval:
        try:
            imp = evaluate_improvement(image, mask, raw_result, result)
            eval_text = f"\n· 后处理提升：{imp['detail']}"
        except Exception as exc:  # noqa: BLE001
            log.warning("后处理质量对比失败：%s", exc)
            eval_text = ""
    else:
        eval_text = ""

    # 阶段 24：精修统计（检测命中 / 编辑框 / 你补画）+ 兜底提示。
    refine_tip = ""
    if auto_fallback:
        refine_tip += t("auto.msg.auto_fallback")
    if det is not None and count_mask_pixels(det) > 0:
        det_px = count_mask_pixels(det)
        added = count_mask_pixels(
            merge_masks(det, user_mask, MASK_MODE_MANUAL, target_shape=image.shape[:2])
        )
        refine_tip += t(
            "auto.msg.refine_stats",
            det=det_px, user=count_mask_pixels(user_mask), added=added,
        )

    # 阶段 32：真实超分可能放大输出尺寸，提示用户分辨率已提升。
    upscaled_note = ""
    if result.shape != raw_result.shape:
        try:
            factor = max(1, round(result.shape[1] / raw_result.shape[1]))
        except Exception:  # noqa: BLE001
            factor = 0
        upscaled_note = t("auto.msg.upscaled", factor=factor)

    # 保存结果。
    try:
        ts = _time.strftime("%H%M%S")
        out_path = save_result(
            result,
            f"auto_{ts}",
            fmt=config.DEFAULT_OUTPUT_FORMAT,
            quality=config.DEFAULT_OUTPUT_QUALITY,
        )
        status = t("single.msg.done", engine=engine.upper(), path=out_path) + eval_text + refine_tip + upscaled_note
    except Exception as exc:
        log.exception("结果保存失败")
        return (
            image,
            result,
            None,
            t("single.msg.save_fail", exc=format_exception_for_ui(exc)),
            None,
        )

    # 阶段 45：自动页处理历史记录闭环（此前仅批量 / 媒体页登记历史）。
    # 原图为 ImageEditor 内存数组，无文件路径，故传 original_arr 由缓存层落盘。
    # record=False 时跳过（历史页「重新处理」已就地更新既有记录，避免重复登记）。
    if record:
        try:
            # 阶段 110：参数摘要改为**共享 + i18n**（此前硬编码中文，会被报告层渲染出来）。
            params = repair_params_text(
                engine, strength, dilate, feather, threshold, denoise
            )
            rec = _CACHE.add_success(
                None, result,
                original_name=f"auto_{ts}",
                mode="auto",
                engine=engine,
                params=params,
                original_arr=image,
            )
            if rec is not None:
                _CACHE.update_result_path(rec, out_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("自动页历史记录登记失败（不影响主流程）：%s", exc)

    # 生成「原图 | 处理后」内存拼接对比图（不落盘），仅当 UI_COMPARE_VIEW 开启。
    compare = (
        _es.make_compare_array(image, result)
        if (config.UI_COMPARE_VIEW and result is not None)
        else None
    )
    return image, result, compare, status, out_path


def on_enhance(orig_arr, result_arr, mode: str) -> str:
    """
    阶段 31：根据所选模式生成「增强对比视图」HTML（拼接 / 拖动滑块 / 差异热图）。

    参数说明：
        orig_arr   : 原图数组（gr.Image 输出，修复前）。
        result_arr : 修复结果数组（gr.Image 输出，修复后）。
        mode       : "concat" / "slider" / "heatmap"（Radio 的稳定 value）。

    返回值：
        可直接交给 gr.HTML 渲染的字符串；无结果时返回提示文案。

    说明：
        阶段 38 起渲染实现统一下沉到 services.compare_service（本页只负责
        提供本地化标签与失败文案），三种模式的表现与批量页/历史页完全一致。
    """
    if orig_arr is None or result_arr is None:
        return t("compare.no_result")
    try:
        html = _cs.render_compare_html(
            orig_arr, result_arr, mode,
            before_label=t("auto.orig_label"),
            after_label=t("auto.result_label"),
            heatmap_caption=t("compare.heatmap_label"),
        )
    except Exception as exc:  # noqa: BLE001
        # 阶段 96：渲染失败必须降级为错误提示而非把 traceback 抛给 Gradio。
        # 与同文件 process_* 回调的「捕获 + format_exception_for_ui」保持一致。
        log.exception("增强对比视图生成失败")
        return (
            '<div style="color:#c0392b;padding:8px 10px;white-space:pre-wrap">'
            f"{_html.escape(t('compare.viewer.render_fail'))}<br/>"
            f"{_html.escape(format_exception_for_ui(exc))}</div>"
        )
    return html or t("compare.no_result")


def _reload_detection_mask(det_mask, upload):
    """
    把最近一次检测掩码重新预填进编辑框（阶段 24：「重新加载检测掩码」按钮）。
    掩码与上传原图尺寸不一致时拒绝重载（防止图层错位）。
    """
    bg = upload if isinstance(upload, np.ndarray) else None
    if bg is None or not isinstance(det_mask, np.ndarray) or det_mask.ndim != 2:
        return None
    if det_mask.shape[:2] != bg.shape[:2]:
        return None
    if count_mask_pixels(det_mask) == 0:
        return {"background": bg, "layers": [], "composite": None}
    return {
        "background": bg,
        "layers": [_mask_to_rgba_layer(det_mask)],
        "composite": None,
    }


def _clear_editor(upload):
    """清空编辑框图层，回到空白画布（阶段 24：「清空编辑框」按钮）。"""
    bg = upload if isinstance(upload, np.ndarray) else None
    if bg is None:
        return None
    return {"background": bg, "layers": [], "composite": None}


#: 阶段 320：随包发布的示例图（640² 真照片 + 右下角「StudioAI」角标）。
SAMPLE_IMAGE_PATH = _os.path.join(_ROOT, "docs", "assets", "sample_watermarked.png")


def load_sample_image():
    """「试一张示例」：把示例图填进上传框与编辑框，返回 (upload, editor, 状态文案)。

    返回三值与 `sample_btn.click(outputs=[upload, editor, status_box])` 对齐。
    读不到文件时三个值都不变（upload/editor 返回 gr.update()），只报错文案——
    缺一个演示资源不该让整页崩掉。
    """
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(SAMPLE_IMAGE_PATH) as im:
            arr = np.array(im.convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - 资源缺失/损坏都只降级为提示
        return gr.update(), gr.update(), t("auto.sample_fail", exc=str(exc)[:120])
    if arr.ndim != 3 or arr.shape[2] != 3:
        return gr.update(), gr.update(), t("auto.sample_fail", exc="示例图通道异常")
    # ⚠ 实测（320）：把 `{"background": arr, "layers": [], "composite": ...}` 直接塞给
    #   Gradio 6 的 ImageEditor，浏览器回传后会被当作"没有图" ⇒ 修复回调报
    #   「请先拖拽或上传一张图片到左侧编辑框」（composite 填 arr 也无效）。
    #   而「检测」回调返回的 `background + 1 个图层` 是**已被验证可用**的形态
    #   （314b 实测 + 本次真机点过）。故示例图只填上传框，编辑框交给用户点「② 自动检测水印」去填。
    return arr, gr.update(), t("auto.sample_loaded")


def build_auto_tab():
    """
    构建「智能自动检测」标签页的全部 Gradio 组件，并绑定回调。

    返回值：
        无（组件已加入当前 Blocks 上下文）。
    """
    with gr.Row(equal_height=False):
        # ---------------- 左列：上传 + 检测 + 微调编辑 ----------------
        with gr.Column(scale=3):
            upload = gr.Image(
                label=t("auto.upload_label"),
                type="numpy",
                image_mode="RGB",
                sources=["upload", "clipboard"],
                interactive=True,
                height=360,
                elem_id="auto_upload",
            )
            reg_label(upload, "auto.upload_label")
            # 阶段 320：首次体验——没有素材的人也能一键看到完整效果。
            #   动机：首屏只有「上传」，新用户没有现成的带水印图就直接卡住；
            #   示例图随包发布（docs/assets/sample_watermarked.png，640² 真照片 + 右下角标）。
            with gr.Row():
                sample_btn = gr.Button(
                    t("auto.sample_btn"), size="sm", variant="secondary",
                )
                reg_value(sample_btn, "auto.sample_btn")

            with gr.Accordion(t("auto.detect_accordion"), open=True) as _det_acc:
                reg_label(_det_acc, "auto.detect_accordion")
                # 阶段 20：检测模式（预设）下拉，一键适配 AI 生成图常见水印样式。
                detect_preset = gr.Dropdown(
                    choices=[(_preset_choice_label(p.name, p.display), p.name)
                             for p in list_presets()],
                    value=config.DETECT_PRESET_DEFAULT,
                    label=t("auto.detect_mode_label"),
                    info=t("auto.detect_mode_info"),
                )
                reg_label_info(detect_preset, "auto.detect_mode_label", "auto.detect_mode_info")
                # 阶段 25：智能推荐预设按钮（一键分析图像特征挑预设，免去手动试错）。
                recommend_btn = gr.Button(
                    t("detect.recommend.btn"), size="sm", variant="secondary",
                )
                reg_value(recommend_btn, "detect.recommend.btn")
                recommend_md = gr.Markdown(
                    value=t("detect.recommend.msg.no_upload"),
                    elem_id="auto_recommend_md",
                )
                reg_value(recommend_md, "detect.recommend.msg.no_upload")
                sensitivity = gr.Slider(
                    minimum=0.0, maximum=1.0,
                    value=config.DETECT_TRANSLUCENT_SENSITIVITY, step=0.05,
                    label=t("auto.sensitivity_label"),
                    info=t("auto.sensitivity_info"),
                )
                reg_label_info(sensitivity, "auto.sensitivity_label", "auto.sensitivity_info")
                with gr.Row():
                    en_translucent = gr.Checkbox(
                        value=config.DETECT_TRANSLUCENT_ENABLED,
                        label=t("auto.en_translucent"),
                    )
                    reg_label(en_translucent, "auto.en_translucent")
                    en_corner = gr.Checkbox(
                        value=config.DETECT_CORNER_LOGO_ENABLED,
                        label=t("auto.en_corner"),
                    )
                    reg_label(en_corner, "auto.en_corner")
                    en_edge = gr.Checkbox(
                        value=config.DETECT_EDGE_TEXT_ENABLED,
                        label=t("auto.en_edge"),
                    )
                    reg_label(en_edge, "auto.en_edge")

            detect_btn = gr.Button(
                t("auto.detect_btn"), variant="primary", size="lg", elem_id="auto_detect"
            )
            reg_value(detect_btn, "auto.detect_btn")

            # 检测可视化预览（红层 + 彩色类别框）。
            detect_preview = gr.Image(
                label=t("auto.detect_preview_label"),
                type="numpy", interactive=False, height=360,
            )
            reg_label(detect_preview, "auto.detect_preview_label")

            # 微调编辑框：检测后会被预填「背景+红色掩码图层」，用户可增涂/擦除。
            editor_kwargs = dict(
                label=t("auto.editor_label"),
                type="numpy",
                image_mode="RGB",
                interactive=True,
                height=360,
            )
            if hasattr(gr, "Brush"):
                editor_kwargs["brush"] = gr.Brush(
                    default_size=40, colors=["#ff3b30"], color_mode="fixed"
                )
            if hasattr(gr, "Eraser"):
                try:
                    editor_kwargs["eraser"] = gr.Eraser(default_size=40)
                except Exception:
                    pass
            editor = gr.ImageEditor(**editor_kwargs)
            reg_label(editor, "auto.editor_label")

            # 阶段 24：交互式掩码精修 —— 精修模式 + 重载 / 清空工具。
            # 检测掩码存进 State，供修复回调做「检测 × 精修」合并与统计。
            det_mask_state = gr.State(None)
            with gr.Row():
                mask_mode = gr.Radio(
                    choices=[(t("mask_edit.mode_merge"), MASK_MODE_MERGE),
                             (t("mask_edit.mode_manual"), MASK_MODE_MANUAL)],
                    value=config.MASK_EDIT_DEFAULT_MODE,
                    label=t("mask_edit.mode_label"),
                    info=t("mask_edit.mode_info"),
                )
                reg_label_info(mask_mode, "mask_edit.mode_label", "mask_edit.mode_info")
            with gr.Row():
                reload_mask_btn = gr.Button(
                    t("mask_edit.reload_btn"), size="sm",
                )
                reg_value(reload_mask_btn, "mask_edit.reload_btn")
                clear_editor_btn = gr.Button(
                    t("mask_edit.clear_btn"), size="sm",
                )
                reg_value(clear_editor_btn, "mask_edit.clear_btn")

            # 阶段 22：检测命中率统计展示（Markdown，随检测实时刷新）。
            detect_stats_md = gr.Markdown(
                value=t("detect.stats.placeholder"),
                elem_id="auto_detect_stats",
            )
            reg_value(detect_stats_md, "detect.stats.placeholder")

            with gr.Accordion(t("auto.repair_accordion"), open=True) as _rep_acc:
                reg_label(_rep_acc, "auto.repair_accordion")
                with gr.Row():
                    engine = gr.Radio(
                        choices=get_engine_choices(),
                        value=config.INPAINT_ENGINE,
                        label=t("params.engine_label"),
                        info=t("params.engine_info"),
                    )
                    reg_label_info(engine, "params.engine_label", "params.engine_info")
                    # 阶段 74/75：双跑择优（默认取配置；设置页可持久化默认值）。
                    dual_run = gr.Checkbox(
                        value=config.INPAINT_DUAL_RUN,
                        label=t("params.dual_run_label"),
                        info=t("params.dual_run_info"),
                    )
                    reg_label_info(dual_run, "params.dual_run_label", "params.dual_run_info")
                with gr.Row():
                    strength = gr.Slider(
                        minimum=config.INPAINT_STRENGTH_MIN,
                        maximum=config.INPAINT_STRENGTH_MAX,
                        value=config.INPAINT_STRENGTH_DEFAULT,
                        step=config.INPAINT_STRENGTH_STEP,
                        label=t("params.strength_label"),
                        info=t("params.strength_info"),
                    )
                    reg_label_info(strength, "params.strength_label", "params.strength_info")
                    denoise = gr.Slider(
                        minimum=config.DENOISE_STRENGTH_MIN,
                        maximum=config.DENOISE_STRENGTH_MAX,
                        value=config.DENOISE_STRENGTH_DEFAULT,
                        step=config.DENOISE_STRENGTH_STEP,
                        label=t("params.denoise_label"),
                        info=t("params.denoise_info"),
                    )
                    reg_label_info(denoise, "params.denoise_label", "params.denoise_info")
                with gr.Row():
                    dilate = gr.Slider(
                        minimum=config.MASK_DILATE_MIN,
                        maximum=config.MASK_DILATE_MAX,
                        value=config.MASK_DILATE_DEFAULT,
                        step=config.MASK_DILATE_STEP,
                        label=t("params.dilate_label"),
                        info=t("params.dilate_info"),
                    )
                    reg_label_info(dilate, "params.dilate_label", "params.dilate_info")
                    feather = gr.Slider(
                        minimum=config.MASK_FEATHER_MIN,
                        maximum=config.MASK_FEATHER_MAX,
                        value=config.MASK_FEATHER_DEFAULT,
                        step=config.MASK_FEATHER_STEP,
                        label=t("params.feather_label"),
                        info=t("params.feather_info"),
                    )
                    reg_label_info(feather, "params.feather_label", "params.feather_info")
                with gr.Row():
                    threshold = gr.Slider(
                        minimum=config.MASK_THRESHOLD_MIN,
                        maximum=config.MASK_THRESHOLD_MAX,
                        value=config.MASK_THRESHOLD_DEFAULT,
                        step=config.MASK_THRESHOLD_STEP,
                        label=t("params.threshold_label"),
                        info=t("params.threshold_info"),
                    )
                    reg_label_info(threshold, "params.threshold_label", "params.threshold_info")
                # 阶段 18：后处理增强开关 + 锐化强度。
                with gr.Row():
                    pp_enable = gr.Checkbox(
                        value=config.POSTPROCESS_ENABLED,
                        label=t("params.postprocess_label"),
                        info=t("params.postprocess_info"),
                    )
                    reg_label_info(pp_enable, "params.postprocess_label", "params.postprocess_info")
                    pp_sharpen = gr.Slider(
                        minimum=0.0, maximum=2.0,
                        value=config.POSTPROCESS_SHARPEN_AMOUNT, step=0.1,
                        label=t("params.sharpen_label"),
                        info=t("params.sharpen_info"),
                    )
                    reg_label_info(pp_sharpen, "params.sharpen_label", "params.sharpen_info")
                # 阶段 21：画质增强深化三道开关 + 强度滑块。
                with gr.Row():
                    pp_poisson = gr.Checkbox(
                        value=config.POSTPROCESS_POISSON_BLEND,
                        label=t("params.poisson_label"),
                        info=t("params.poisson_info"),
                    )
                    reg_label_info(pp_poisson, "params.poisson_label", "params.poisson_info")
                    pp_texture = gr.Checkbox(
                        value=config.POSTPROCESS_TEXTURE_TRANSFER,
                        label=t("params.texture_label"),
                        info=t("params.texture_info"),
                    )
                    reg_label_info(pp_texture, "params.texture_label", "params.texture_info")
                    pp_artifact = gr.Checkbox(
                        value=config.POSTPROCESS_ARTIFACT_REDUCE,
                        label=t("params.artifact_label"),
                        info=t("params.artifact_info"),
                    )
                    reg_label_info(pp_artifact, "params.artifact_label", "params.artifact_info")
                with gr.Row():
                    pp_poisson_strength = gr.Slider(
                        minimum=0.0, maximum=1.0,
                        value=config.POSTPROCESS_POISSON_BLEND_STRENGTH, step=0.05,
                        label=t("params.poisson_strength_label"),
                        info=t("params.poisson_strength_info"),
                    )
                    reg_label_info(pp_poisson_strength, "params.poisson_strength_label", "params.poisson_strength_info")
                    pp_texture_amount = gr.Slider(
                        minimum=0.0, maximum=1.0,
                        value=config.POSTPROCESS_TEXTURE_TRANSFER_AMOUNT, step=0.05,
                        label=t("params.texture_amount_label"),
                        info=t("params.texture_amount_info"),
                    )
                    reg_label_info(pp_texture_amount, "params.texture_amount_label", "params.texture_amount_info")
                    pp_artifact_amount = gr.Slider(
                        minimum=0.0, maximum=1.0,
                        value=config.POSTPROCESS_ARTIFACT_REDUCE_AMOUNT, step=0.05,
                        label=t("params.artifact_amount_label"),
                        info=t("params.artifact_amount_info"),
                    )
                    reg_label_info(pp_artifact_amount, "params.artifact_amount_label", "params.artifact_amount_info")
                    # 阶段 29：轻量超分（细节重建）开关 + 强度滑块。
                    pp_superres = gr.Checkbox(
                        value=config.POSTPROCESS_SUPERRES_ENABLED,
                        label=t("params.superres_label"),
                        info=t("params.superres_info"),
                    )
                    reg_label_info(pp_superres, "params.superres_label", "params.superres_info")
                    pp_superres_amount = gr.Slider(
                        minimum=0.0, maximum=1.0,
                        value=config.POSTPROCESS_SUPERRES_AMOUNT, step=0.05,
                        label=t("params.superres_amount_label"),
                        info=t("params.superres_amount_info"),
                    )
                    reg_label_info(pp_superres_amount, "params.superres_amount_label", "params.superres_amount_info")
                    # 阶段 32：真·超分（AI 模型放大）开关 + 放大倍数滑块。
                    pp_real_superres = gr.Checkbox(
                        value=config.REAL_SUPERRES_ENABLED,
                        label=t("params.real_superres_label"),
                        info=t("params.real_superres_info"),
                    )
                    reg_label_info(pp_real_superres, "params.real_superres_label", "params.real_superres_info")
                    pp_real_scale = gr.Slider(
                        minimum=1, maximum=8,
                        value=config.REAL_SUPERRES_SCALE, step=1,
                        label=t("params.real_scale_label"),
                        info=t("params.real_scale_info"),
                    )
                    reg_label_info(pp_real_scale, "params.real_scale_label", "params.real_scale_info")
                    pp_real_backend = gr.Dropdown(
                        choices=["auto", "esrgan", "realesrgan"],
                        value=config.REAL_SUPERRES_BACKEND,
                        label=t("params.real_backend_label"),
                        info=t("params.real_backend_info"),
                    )
                    reg_label_info(pp_real_backend, "params.real_backend_label", "params.real_backend_info")
                    # 阶段 35：登记真实超分控件，供设置页「应用」时跨页刷新。
                    register_superres_widget(pp_real_superres, pp_real_scale, pp_real_backend)

            # 阶段 29：后处理质量提升显示开关（开启后状态栏展示优化前→后质量分）。
            pp_eval = gr.Checkbox(
                value=False,
                label=t("params.eval_label"),
                info=t("params.eval_info"),
            )
            reg_label_info(pp_eval, "params.eval_label", "params.eval_info")

            repair_btn = gr.Button(
                t("auto.repair_btn"), variant="primary", size="lg", elem_id="auto_repair"
            )
            reg_value(repair_btn, "auto.repair_btn")

        # ---------------- 右列：对比 + 状态 + 下载 ----------------
        with gr.Column(scale=2):
            reg_value(gr.Markdown(t("auto.result_title")), "auto.result_title")
            with gr.Row():
                orig_view = gr.Image(
                    label=t("auto.orig_label"), type="numpy", interactive=False, height=300
                )
                reg_label(orig_view, "auto.orig_label")
                result_view = gr.Image(
                    label=t("auto.result_label"), type="numpy", interactive=False, height=300
                )
                reg_label(result_view, "auto.result_label")
            # 「结果对比视图」：原图 | 处理后 内存拼接（不落盘），受 config.UI_COMPARE_VIEW 控制。
            compare_view = gr.Image(
                label=t("auto.compare_label"),
                type="numpy", interactive=False, height=300,
                visible=config.UI_COMPARE_VIEW,
            )
            reg_label(compare_view, "auto.compare_label")
            status_box = gr.Textbox(
                label=t("auto.status_label"), lines=6, interactive=False,
                placeholder=t("auto.status_placeholder"),
            )
            reg_textbox(status_box, "auto.status_label", "auto.status_placeholder")
            out_file = gr.File(label=t("auto.download_label"), interactive=False)
            reg_label(out_file, "auto.download_label")

            # 阶段 31：增强对比视图（拼接 / 拖动滑块 / 差异热图）。
            enh_title = gr.Markdown(t("compare.enh_title"))
            reg_value(enh_title, "compare.enh_title")
            with gr.Row():
                enh_mode = gr.Radio(
                    choices=[
                        (t("compare.mode_concat"), "concat"),
                        (t("compare.mode_slider"), "slider"),
                        (t("compare.mode_heatmap"), "heatmap"),
                    ],
                    value="concat",
                    label=t("compare.enh_mode_label"),
                    info=t("compare.enh_mode_info"),
                )
                reg_label_info(
                    enh_mode, "compare.enh_mode_label", "compare.enh_mode_info"
                )
                enh_btn = gr.Button(t("compare.enh_btn"), variant="secondary")
                reg_label(enh_btn, "compare.enh_btn")
            enh_view = gr.HTML(value="")

    # 绑定「自动检测」按钮回调（输出含阶段 24 的检测掩码 State）。
    detect_btn.click(
        fn=process_auto_detect,
        inputs=[upload, sensitivity, en_translucent, en_corner, en_edge, detect_preset],
        outputs=[detect_preview, editor, status_box, detect_stats_md, det_mask_state],
    )

    # 阶段 320：一键载入示例图（填 上传框 + 编辑框 + 状态栏）。
    sample_btn.click(
        fn=load_sample_image,
        inputs=[],
        outputs=[upload, editor, status_box],
    )

    # 阶段 25：绑定「智能推荐预设」按钮（输出更新检测预设下拉 + 推荐理由）。
    recommend_btn.click(
        fn=process_recommend_preset,
        inputs=[upload],
        outputs=[detect_preset, recommend_md],
    )

    # 绑定「一键修复」按钮回调。输出新增 compare_view（对比拼接图）；
    # 输入新增阶段 24 的检测掩码 State 与精修模式。
    repair_btn.click(
        fn=process_auto_repair,
        inputs=[
            editor, engine, strength, dilate, feather, threshold, denoise,
            pp_enable, pp_sharpen, pp_eval,
            pp_poisson, pp_poisson_strength, pp_texture, pp_texture_amount,
            pp_artifact, pp_artifact_amount,             pp_superres, pp_superres_amount,
            pp_real_superres, pp_real_scale,
            det_mask_state, mask_mode, pp_real_backend,
            dual_run,
        ],
        outputs=[orig_view, result_view, compare_view, status_box, out_file],
    )

    # 阶段 31：增强对比视图 —— 直接复用 repair 输出的 orig_view / result_view 作为
    # 输入（不改动 repair 的 outputs，避免接线错位）；按钮与模式切换都触发重新生成。
    enh_btn.click(
        fn=on_enhance,
        inputs=[orig_view, result_view, enh_mode],
        outputs=[enh_view],
    )
    enh_mode.change(
        fn=on_enhance,
        inputs=[orig_view, result_view, enh_mode],
        outputs=[enh_view],
    )

    # 阶段 24：掩码精修工具按钮。
    reload_mask_btn.click(
        fn=_reload_detection_mask,
        inputs=[det_mask_state, upload],
        outputs=[editor],
    )
    clear_editor_btn.click(
        fn=_clear_editor,
        inputs=[upload],
        outputs=[editor],
    )
