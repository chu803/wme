# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/tab_single.py
 文件说明 : 单图处理标签页（阶段 3 核心界面）。
            提供：单张图片拖拽上传 + 画笔涂抹 / 矩形框选水印区域 +
            修复参数面板 + 原图/处理结果左右对比预览 + 一键下载。
            本文件属于界面层（ui），只负责「收集操作、展示结果」，
            所有算法都委托给 core / services 层，自身不写图像处理逻辑。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import time as _time
from typing import Optional

import numpy as np
import gradio as gr

import config
from utils.i18n import t
from ui.i18n_rt import reg_label, reg_label_info, reg_value, reg_textbox
from core.inpainter import inpaint
from core.mask_generator import build_mask_from_editor, count_mask_pixels
from ui.components import get_engine_choices, register_superres_widget, repair_params_text, format_elapsed
from services.file_service import save_result
from services import compare_service as _cs
from services import export_service as _es
from services.postprocess_service import postprocess as pp_postprocess
from services.validators import validate_numpy_image
from utils.exceptions import EmptyMaskError, ImageTooLargeError, format_exception_for_ui
from utils.logger import get_logger
from services.cache_service import get_cache

log = get_logger("ui.tab_single")
_CACHE = get_cache()


def _extract_image(editor_value):
    """
    从 ImageEditor 返回值中安全提取「原图 numpy 数组」。

    参数说明：
        editor_value : ImageEditor 作为函数输入时的值，支持两种格式：
                       · dict（Gradio 6.x）：取 'background' 键。
                       · (image, mask) 元组 / 单 ndarray（Gradio 4.x 及个别情况）。

    返回值：
        原图数组，或 None（未上传时）。
    """
    if editor_value is None:
        return None
    if isinstance(editor_value, dict):
        # Gradio 6.x：'background' 是用户上传的原图。
        return editor_value.get("background")
    if isinstance(editor_value, (tuple, list)):
        return editor_value[0] if len(editor_value) >= 1 else None
    if isinstance(editor_value, np.ndarray):
        return editor_value
    return None


#: `process_single` 的**UI 控件形参**（顺序 = `run_btn.click(inputs=[...])` 的接线顺序）。
#:
#: ⚠ **阶段 110 修复的 P0：Gradio 按位置传参，而控件比形参少 1 个** ——
#: 原签名把内部开关 `record` 排在最后一个控件形参 `dual_run` **之前**，
#: 于是 21 个控件逐个右移一位：**「双跑择优」复选框绑到了 `record`**，
#: 而 `dual_run` 永远吃默认值 `False`。实测后果（`%TEMP%/s110_probe.py` / `s110_add.py`）：
#:   · 复选框默认 False ⇒ `record=False` ⇒ **默认路径根本不登记处理历史**；
#:   · 勾选它 ⇒ `record=True` ⇒ 历史又回来了，但 `dual_run` 依然恒 False
#:     ⇒ **「双跑择优」在界面上完全无效**（两个功能被整体错位）。
#:
#: 契约（本模块的铁律）：**内部开关形参必须排在全部 UI 控件形参之后**。
#: 该契约由 `tests/test_tab_single_audit110.py` 用「AST 提取 inputs 列表 + 前缀断言」钉住。
_SINGLE_WIDGET_PARAMS: tuple = (
    "editor_value", "engine", "strength", "dilate", "feather", "threshold", "denoise",
    "enable_postprocess", "sharpen",
    "poisson_blend", "poisson_strength", "texture_transfer", "texture_amount",
    "artifact_reduce", "artifact_amount",
    "superres", "superres_amount",
    "real_superres", "real_scale", "real_backend",
    "dual_run",
)


def _compose_status(engine: str, out_path: str, elapsed: float,
                    upscaled_note: str = "") -> str:
    """拼装单图处理完成后的状态文案（阶段 190 抽为纯函数，便于运行时验证）。

    三段结构**不可省**：
        ① `single.msg.done`    —— 引擎 + 保存路径（阶段早期就有）
        ② `single.msg.elapsed` —— 本次「修复 + 增强」耗时（阶段 190 新增，D2）
        ③ `upscaled_note`      —— 真实超分放大提示（阶段 32，可为空串）

    参数说明：
        engine        : 实际使用的引擎名（本函数负责转大写展示）。
        out_path      : 结果保存路径。
        elapsed       : 秒数（浮点）；文案里用 `{seconds:.2f}` 格式化到两位。
        upscaled_note : 超分提示；无放大时传空串。
    """
    return (
        t("single.msg.done", engine=engine.upper(), path=out_path)
        + format_elapsed(elapsed)
        + upscaled_note
    )


def process_single(
    editor_value,
    engine: str,
    strength: float,
    dilate: int,
    feather: int,
    threshold: int,
    denoise: int,
    enable_postprocess: bool = True,
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
    dual_run: bool = False,
    record: bool = True,
):
    """
    单图处理主回调：解析涂抹 → 构建掩码 → 调用修复引擎 → 保存并回传预览。

    参数说明：
        editor_value : ImageEditor 返回值（含原图与掩码）。
        engine/strength/dilate/feather/threshold/denoise : 来自参数面板的用户输入。
        dual_run     : 双跑择优开关（**UI 控件**；必须排在 `record` 之前，见
                       `_SINGLE_WIDGET_PARAMS`）。
        record       : 是否登记处理历史（**内部开关**，界面无控件 ⇒ 必须排在
                       全部 UI 控件形参之后，否则会与末尾控件错位）。

    返回值：
        **五元组** `(orig_view, result_view, compare_view, status_text, download_file)`，
        与 `run_btn.click(outputs=[...])` 的五个输出**逐位**对应
        （旧 docstring 写作"四元组"且漏了 `compare_view`，阶段 110 更正）。
    """
    # 解析原图。
    image = _extract_image(editor_value)
    if image is None:
        return (
            None,
            None,
            None,
            t("single.msg.no_image"),
            None,
        )

    # 校验图片分辨率（防御超大图）。
    try:
        validate_numpy_image(image)
    except ImageTooLargeError as exc:
        return image, None, None, t("single.msg.too_large", exc=format_exception_for_ui(exc)), None
    except ValueError as exc:
        return image, None, None, t("single.msg.bad_data", exc=format_exception_for_ui(exc)), None

    # 由 ImageEditor 的涂抹结果构建标准掩码（自动对齐到原图尺寸）。
    try:
        mask = build_mask_from_editor(
            editor_value,
            brush_threshold=10,
            target_size=image.shape[:2],
        )
    except Exception as exc:  # 掩码解析失败不应导致崩溃
        log.exception("掩码构建失败")
        return image, None, None, t("single.msg.mask_fail", exc=format_exception_for_ui(exc)), None

    # 空掩码检测：用户没画任何东西。
    if count_mask_pixels(mask) == 0:
        return (
            image,
            None,
            None,
            t("single.msg.no_mask"),
            None,
        )

    # 进度回调：界面层通过它接收 core 层的进度（此处仅记录日志，
    # 阶段 5 批量处理时会接入 Gradio 实时进度条）。
    def _progress(p: float, msg: str) -> None:
        log.info("单图处理进度 %.0f%%：%s", p * 100, msg)

    # 调用核心修复引擎。
    try:
        # 阶段 190：本次耗时口径 = 「开始修复」→「增强完成」（含 inpaint + 后处理），
        # 不含涂抹解析与落盘——与 i18n 文案 `single.msg.elapsed` 的措辞保持一致。
        _t0 = _time.perf_counter()
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
        # LaMa 引擎缺少模型文件。
        return (
            image,
            None,
            None,
            t("single.msg.no_model", exc=format_exception_for_ui(exc)),
            None,
        )
    except Exception as exc:
        log.exception("单图修复异常")
        return image, None, None, t("single.msg.fail", exc=format_exception_for_ui(exc)), None

    # 阶段 18 + 21：修复后自动叠加画质增强（锐化 + 色彩还原 + 边缘融合 + 深化三道）。
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
    # 阶段 190：耗时在此收口——「修复 + 增强」结束，落盘/对比图不计入（与文案同口径）。
    elapsed = _time.perf_counter() - _t0

    # 阶段 32：真实超分可能放大输出尺寸，提示用户分辨率已提升。
    upscaled_note = ""
    if result.shape != raw_result.shape:
        try:
            factor = max(1, round(result.shape[1] / raw_result.shape[1]))
        except Exception:  # noqa: BLE001
            factor = 0
        upscaled_note = t("auto.msg.upscaled", factor=factor)

    # 保存结果到 outputs 目录。
    try:
        ts = _time.strftime("%H%M%S")
        out_path = save_result(
            result,
            f"single_{ts}",
            fmt=config.DEFAULT_OUTPUT_FORMAT,
            quality=config.DEFAULT_OUTPUT_QUALITY,
        )
        status = _compose_status(engine, out_path, elapsed, upscaled_note)
    except Exception as exc:
        log.exception("结果保存失败")
        # 修复本身成功，仅保存失败：仍回传结果图，方便用户手动另存。
        return (
            image,
            result,
            None,
            t("single.msg.save_fail", exc=format_exception_for_ui(exc)),
            None,
        )

    # 阶段 45：单图处理历史记录闭环（此前仅批量 / 媒体页登记历史）。
    # 原图为 ImageEditor 内存数组，无文件路径，故传 original_arr 由缓存层落盘。
    if record:
        try:
            # 阶段 110：参数摘要走**共享 + i18n**（此前硬编码中文，会被报告层渲染出来）。
            params = repair_params_text(
                engine, strength, dilate, feather, threshold, denoise
            )
            rec = _CACHE.add_success(
                None, result,
                original_name=f"single_{ts}",
                mode="manual",
                engine=engine,
                params=params,
                original_arr=image,
            )
            if rec is not None:
                _CACHE.update_result_path(rec, out_path)
        except Exception as exc:  # noqa: BLE001
            log.warning("单图历史记录登记失败（不影响主流程）：%s", exc)

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
    与 tab_auto.on_enhance 逻辑一致，仅标签取自 single 段的 i18n。

    阶段 38：渲染实现下沉到 services.compare_service，本函数只提供本地化标签
    与「无结果」文案，避免同一套逻辑在多个标签页里各写一份。
    """
    if orig_arr is None or result_arr is None:
        return t("compare.no_result")
    html = _cs.render_compare_html(
        orig_arr, result_arr, mode,
        before_label=t("single.orig_label"),
        after_label=t("single.result_label"),
        heatmap_caption=t("compare.heatmap_label"),
    )
    return html or t("compare.no_result")


def build_single_tab():
    """
    构建「单图处理」标签页的全部 Gradio 组件，并绑定处理回调。

    返回值：
        无（组件已加入当前 Blocks 上下文）。
    """
    with gr.Row(equal_height=False):
        # ---------------- 左列：编辑 + 参数 ----------------
        with gr.Column(scale=3):
            # ImageEditor 同时支持「拖拽上传」与「画笔涂抹」。
            # 说明（Gradio 版本差异）：
            #   · Gradio 4.x 用 tools=["brush","rect"] 提供画笔与矩形框选；
            #   · Gradio 6.x 移除了 tools 参数，画笔/橡皮为内置工具，
            #     矩形框选可用「画笔按住拖动覆盖矩形范围」等效达成。
            # 因此这里统一用 Brush 对象配置画笔（红色、固定色），不依赖 tools。
            editor_kwargs = dict(
                label=t("single.editor_label"),
                type="numpy",
                image_mode="RGB",
                sources=["upload", "clipboard"],
                interactive=True,
                height=480,
                elem_id="single_editor",
            )
            # 自定义画笔：红色、固定色，便于一眼辨认涂抹区域。
            if hasattr(gr, "Brush"):
                editor_kwargs["brush"] = gr.Brush(
                    default_size=40, colors=["#ff3b30"], color_mode="fixed"
                )
            # 提供橡皮工具，便于修正误涂区域（Gradio 6.x 支持 Eraser 对象）。
            if hasattr(gr, "Eraser"):
                try:
                    editor_kwargs["eraser"] = gr.Eraser(default_size=40)
                except Exception:
                    # Eraser 构造参数因版本而异，失败则忽略（使用默认行为）。
                    pass
            editor = gr.ImageEditor(**editor_kwargs)
            reg_label(editor, "single.editor_label")

            with gr.Accordion(t("single.params_accordion"), open=True) as _par_acc:
                reg_label(_par_acc, "single.params_accordion")
                with gr.Row():
                    engine = gr.Radio(
                        choices=get_engine_choices(),
                        value=config.INPAINT_ENGINE,
                        label=t("params.engine_label"),
                        info=t("params.engine_info"),
                    )
                    reg_label_info(engine, "params.engine_label", "params.engine_info")
                with gr.Row():
                    # 阶段 74：双跑择优（opt-in）——主/副引擎各跑一次，按无参考质量分择优。
                    dual_run = gr.Checkbox(
                        value=False,
                        label=t("params.dual_run_label"),
                        info=t("params.dual_run_info"),
                    )
                    reg_label_info(dual_run, "params.dual_run_label",
                                   "params.dual_run_info")
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
                with gr.Row():
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
                with gr.Row():
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

            run_btn = gr.Button(
                t("single.run"), variant="primary", size="lg", elem_id="single_run"
            )
            reg_value(run_btn, "single.run")

        # ---------------- 右列：对比 + 状态 + 下载 ----------------
        with gr.Column(scale=2):
            reg_value(gr.Markdown(t("single.result_title")), "single.result_title")
            with gr.Row():
                orig_view = gr.Image(
                    label=t("single.orig_label"), type="numpy", interactive=False, height=300
                )
                reg_label(orig_view, "single.orig_label")
                result_view = gr.Image(
                    label=t("single.result_label"), type="numpy", interactive=False, height=300
                )
                reg_label(result_view, "single.result_label")
            # 「结果对比视图」：原图 | 处理后 内存拼接（不落盘），受 config.UI_COMPARE_VIEW 控制。
            compare_view = gr.Image(
                label=t("single.compare_label"),
                type="numpy", interactive=False, height=300,
                visible=config.UI_COMPARE_VIEW,
            )
            reg_label(compare_view, "single.compare_label")
            status_box = gr.Textbox(
                label=t("single.status_label"), lines=5, interactive=False,
                placeholder=t("single.status_placeholder"),
            )
            reg_textbox(status_box, "single.status_label", "single.status_placeholder")
            out_file = gr.File(label=t("single.download_label"), interactive=False)
            reg_label(out_file, "single.download_label")

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

    # 绑定按钮回调。输出新增 compare_view（对比拼接图）。
    run_btn.click(
        fn=process_single,
        inputs=[editor, engine, strength, dilate, feather, threshold, denoise,
                pp_enable, pp_sharpen,
                pp_poisson, pp_poisson_strength, pp_texture, pp_texture_amount,
                pp_artifact, pp_artifact_amount,
                pp_superres, pp_superres_amount,
                pp_real_superres, pp_real_scale, pp_real_backend,
                dual_run],
        outputs=[orig_view, result_view, compare_view, status_box, out_file],
    )

    # 阶段 31：增强对比视图 —— 复用 run 输出的 orig_view / result_view 作为输入
    # （不改动 process_single 的 outputs，避免接线错位）；按钮与模式切换触发重新生成。
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
