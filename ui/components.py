# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/components.py
 文件说明 : 可复用 UI 组件库（阶段 6 新增）。
            把「修复参数面板」「导出格式选择」等会在多个标签页重复出现的
            控件抽成统一构建器，避免单图/自动/批量/设置各写一份、日后改一处
            要同步四处的尴尬。

            提供：
            1. RepairParamHandles  : 修复参数组件的句柄集合（dataclass）。
            2. build_repair_params : 统一构建修复参数面板，返回句柄。
            3. param_values        : 把句柄当前值提取成字典，方便传给 process_*。
            4. repair_params_text  : 历史记录用的「修复参数摘要」文案（走 i18n，唯一真值来源）。
            5. build_export_format_selector : 导出格式 + 画质选择（设置页/批量页复用）。

            设计约束：
            · 本文件属于「界面层」，可 import gradio 与 config。
            · 只负责「造组件 + 返回句柄」，不绑定事件回调——
              绑定交给各标签页的 build_*_tab，保持组件库纯粹、可复用。
            · 参数默认值全部取自 config，确保与单图/自动页一致。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from dataclasses import dataclass, fields as _dc_fields

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import gradio as gr

import config
from utils.i18n import t, t_in, get_language
from ui.i18n_rt import reg_label_info, reg_value, reg_label, reg_info, reg_textbox


def normalize_file_paths(value) -> list:
    """把 Gradio File / Upload 组件的返回值规整成「纯路径字符串列表」（**唯一实现**）。

    为什么抽到组件库（阶段 191）
    ---------------------------
    `ui/tab_batch.py` 与 `ui/tab_media.py` 各自实现了一份归一化：形状需求不同
    （批量要**列表**、媒体只要**单个**路径），但**对「带 `.name` 的对象」处理不一致**
    —— 批量落到 `str(obj)`、媒体回落 `obj.name`。同一个 Gradio 返回值在两个页面
    结果不同，一旦 Gradio 改返回形态，只会有一个页面坏掉。
    更隐蔽的是：`pathlib.Path` **也有 `.name`**，而 `.name` 只是**末段文件名**
    ⇒ 旧实现会把 `/tmp/a.png` 悄悄截成 `a.png`（丢目录）。故本实现把
    `os.PathLike` 的判断**排在 `.name` 之前**。

    形状与优先级（依次判断，逐项累积）：
        None                                  → 跳过
        str                                   → 原样
        os.PathLike（含 pathlib.Path）        → `os.fspath(...)`
        dict                                  → `path` → `name`（取到即止）
        列表 / 元组                           → 逐项递归
        带 `.name` 的对象（如 gr 临时文件）   → `str(obj.name)`
        未知标量                              → **丢弃**（见下）

    ⚠ **兜底约定的不对称是有意的，勿按直觉统一**（两者都有既有测试守着，
      见 `tests/test_tab_batch_helpers.py`）：
        · **列表内**的未知元素 → `str(item)`（历史行为：`[123] → ["123"]`）
        · **顶层**的未知标量   → 丢弃（`3.14 → []`），**不静默塞垃圾路径**
      阶段 191 首版把顶层也写成 `str(value)`，直接打破了后一条契约 ——
      教训：抽取共用实现时**必须跑该函数既有的测试**，不能只跑自己新写的。

    返回值：
        路径字符串列表；**去重保序**（同一路径被多次选中只留一次）。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, _os.PathLike):
        return [_os.fspath(value)]
    if isinstance(value, dict):
        p = value.get("path") or value.get("name")
        return [str(p)] if p else []
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            got = normalize_file_paths(item)
            if got:
                out.extend(got)
            elif item is not None:
                out.append(str(item))      # 列表内未知元素：保留历史兜底
        seen, uniq = set(), []
        for p in out:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        return uniq
    name = getattr(value, "name", None)
    if isinstance(name, _os.PathLike):
        return [_os.fspath(name)]
    if name:
        return [str(name)]
    return []                              # 顶层未知标量：丢弃（不塞垃圾路径）


def format_elapsed(seconds: float, *, key: str = "single.msg.elapsed") -> str:
    """拼装「本次处理耗时」行（阶段 190 起多页共用接缝，D2 推广到批量/媒体）。

    单图 / 批量 / 媒体三页的耗时展示共用同一段格式化口径：
        · 占位符统一为 `{seconds:.2f}`（保留两位秒）；
        · 措辞各有差异（单图=修复+增强、批量=整批、媒体=本次处理），
          故由调用方传入各自的 i18n 键，文案随页适配。

    参数说明：
        seconds : 耗时秒数（浮点，通常来自 `time.perf_counter()` 差值）。
        key     : i18n 键名；默认 `single.msg.elapsed`，批量 / 媒体分别传
                  `batch.msg.elapsed` / `media.msg.elapsed`。

    返回值：
        已本地化的耗时行（含前导换行，便于直接拼进多行状态文本）。
    """
    return t(key, seconds=seconds)


def get_engine_choices() -> list[str]:
    """
    返回当前可用修复引擎选项（内建 + 已加载插件）。

    供各标签页的「修复引擎」Radio 与 CLI / API 共用来源，确保界面选项与
    实际可运行的引擎保持一致。导入失败时回退内建三选一，保证界面不崩。
    """
    try:
        from core import plugins as _pg

        return _pg.get_engine_choices()
    except Exception:  # noqa: BLE001
        return ["auto", "lama", "opencv"]


def render_scope_note(lang: "str | None" = None) -> str:
    """
    生成「适用范围」提示（阶段 95）。

    背景：
        真实图基准 30 张全部只标注了角标一类（`corner_logo`），另外四类的真实指标
        是**未知**而非 100%。上一阶段把这个沉默显式化，本阶段用户拍定能力面：
        **只处理角标 LOGO 类图片**。于是需要把这句话说给用户听——

    设计要点（防止又造一份「人读副本」）：
        承诺面与类型名**全部由 `config` 派生**，本函数与 i18n 模板里都不出现任何
        字面类型名。因此「界面上承诺了哪些类型」永远等于
        `config.WATERMARK_TYPE_COMMITTED`：改 config 一处，界面自动跟着变。
        守卫见 `tests/test_watermark_scope95.py`（双向断言：承诺类型必须出现、
        非承诺类型只能出现在「不做承诺」一侧）。

    参数：
        lang : 语言代码；None 表示取当前界面语言（语言切换时由 resolver 实时调用）。

    返回值：
        一行 Markdown 提示；语言无法识别时由 `t_in` 自动回退默认语言。
    """
    if lang is None:
        lang = get_language()
    en = str(lang).lower().startswith("en")
    sep = ", " if en else "、"

    def _one(name: str) -> str:
        label = config.watermark_type_label(name, lang)
        return f"{label} (`{name}`)" if en else f"{label}（`{name}`）"

    committed = sorted(config.WATERMARK_TYPE_COMMITTED)
    others = sorted(set(config.WATERMARK_TYPE_SCOPE) - set(committed))
    return t_in(
        lang, "scope.note",
        types=sep.join(_one(n) for n in committed),
        others=sep.join(_one(n) for n in others),
    )


@dataclass
class RepairParamHandles:
    """
    修复参数组件的句柄集合。

    各标签页 build_*_tab 调用 build_repair_params() 后拿到本对象，
    即可用 handles.engine / handles.strength ... 引用具体组件，
    放入 Gradio 的 inputs / outputs 列表，或传给 run_batch。
    """
    engine: gr.Radio
    sensitivity: gr.Slider
    strength: gr.Slider
    denoise: gr.Slider
    dilate: gr.Slider
    feather: gr.Slider
    threshold: gr.Slider
    postprocess: gr.Checkbox
    sharpen: gr.Slider
    poisson_blend: gr.Checkbox
    poisson_strength: gr.Slider
    texture_transfer: gr.Checkbox
    texture_amount: gr.Slider
    artifact_reduce: gr.Checkbox
    artifact_amount: gr.Slider
    superres: gr.Checkbox
    superres_amount: gr.Slider
    real_superres: gr.Checkbox
    real_scale: gr.Slider
    real_backend: gr.Dropdown


# ------------------------------------------------------------------------------
# 真实超分控件运行时注册表（阶段 35）
# ------------------------------------------------------------------------------
# 设置页「应用真实超分」按钮需要跨页刷新所有功能页的真实超分控件
# （总开关 / 放大倍数 / 后端）。由于单图/自动页是内联构建、批量/媒体页走
# build_repair_params，注册表统一收集这些控件，供设置页一键更新输出。
_SUPERRES_WIDGETS: list[dict] = []


def register_superres_widget(
    enabled: "gr.Checkbox", scale: "gr.Slider", backend: "gr.Dropdown"
) -> None:
    """登记一组真实超分控件（enabled/scale/backend），供设置页刷新。"""
    _SUPERRES_WIDGETS.append(
        {"enabled": enabled, "scale": scale, "backend": backend}
    )


def register_superres_handles(h: "RepairParamHandles") -> None:
    """从 RepairParamHandles 登记真实超分控件（批量/媒体页便捷入口）。"""
    register_superres_widget(h.real_superres, h.real_scale, h.real_backend)


def get_superres_widgets() -> list[dict]:
    """返回已登记的全部真实超分控件组（每个功能页一组）。"""
    return list(_SUPERRES_WIDGETS)


def clear_superres_widgets() -> None:
    """清空注册表（测试或重建界面时使用）。"""
    _SUPERRES_WIDGETS.clear()


def build_repair_params(
    *,
    accordion: bool = True,
    accordion_title: str = "params.accordion",
    open_: bool = True,
    sensitivity_visible: bool = True,
    sensitivity_label: str = "params.sensitivity_label",
    sensitivity_info: str = "params.sensitivity_info",
) -> RepairParamHandles:
    """
    统一构建「修复参数面板」，返回句柄。

    参数说明：
        accordion         : 是否用 gr.Accordion 包裹（批量页/设置页用 True 更整洁）。
        accordion_title   : 折叠面板标题。
        open_             : 折叠面板默认是否展开。
        sensitivity_visible : 是否显示「检测敏感度」滑块（手动/批量模式可隐藏）。
        sensitivity_label/info : 敏感度滑块的标签与提示文案。

    返回值：
        RepairParamHandles，含全部 7 个参数组件句柄。

    默认值来源（与单图/自动页保持一致）：
        engine     <- config.INPAINT_ENGINE
        sensitivity<- config.DETECT_TRANSLUCENT_SENSITIVITY
        strength   <- config.INPAINT_STRENGTH_*
        denoise    <- config.DENOISE_STRENGTH_*
        dilate     <- config.MASK_DILATE_*
        feather    <- config.MASK_FEATHER_*
        threshold  <- config.MASK_THRESHOLD_*
    """
    def _body() -> RepairParamHandles:
        engine = gr.Radio(
            choices=get_engine_choices(),
            value=config.INPAINT_ENGINE,
            label=t("params.engine_label"),
            info=t("params.engine_info"),
        )
        reg_label_info(engine, "params.engine_label", "params.engine_info")
        sensitivity = gr.Slider(
            minimum=0.0, maximum=1.0,
            value=config.DETECT_TRANSLUCENT_SENSITIVITY, step=0.05,
            label=t(sensitivity_label),
            info=t(sensitivity_info),
            visible=sensitivity_visible,
        )
        if sensitivity_visible:
            reg_label_info(sensitivity, sensitivity_label, sensitivity_info)
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
        threshold = gr.Slider(
            minimum=config.MASK_THRESHOLD_MIN,
            maximum=config.MASK_THRESHOLD_MAX,
            value=config.MASK_THRESHOLD_DEFAULT,
            step=config.MASK_THRESHOLD_STEP,
            label=t("params.threshold_label"),
            info=t("params.threshold_info"),
        )
        reg_label_info(threshold, "params.threshold_label", "params.threshold_info")
        # 阶段 18：后处理增强开关 + 锐化强度（默认开启，可逐次关闭）。
        postprocess = gr.Checkbox(
            value=config.POSTPROCESS_ENABLED,
            label=t("params.postprocess_label"),
            info=t("params.postprocess_info"),
        )
        reg_label_info(postprocess, "params.postprocess_label", "params.postprocess_info")
        sharpen = gr.Slider(
            minimum=0.0, maximum=2.0,
            value=config.POSTPROCESS_SHARPEN_AMOUNT, step=0.1,
            label=t("params.sharpen_label"),
            info=t("params.sharpen_info"),
        )
        reg_label_info(sharpen, "params.sharpen_label", "params.sharpen_info")
        # 阶段 21：画质增强深化三道开关 + 强度滑块（默认关闭，向后兼容）。
        with gr.Row():
            poisson_blend = gr.Checkbox(
                value=config.POSTPROCESS_POISSON_BLEND,
                label=t("params.poisson_label"),
                info=t("params.poisson_info"),
            )
            reg_label_info(poisson_blend, "params.poisson_label", "params.poisson_info")
            texture_transfer = gr.Checkbox(
                value=config.POSTPROCESS_TEXTURE_TRANSFER,
                label=t("params.texture_label"),
                info=t("params.texture_info"),
            )
            reg_label_info(texture_transfer, "params.texture_label", "params.texture_info")
            artifact_reduce = gr.Checkbox(
                value=config.POSTPROCESS_ARTIFACT_REDUCE,
                label=t("params.artifact_label"),
                info=t("params.artifact_info"),
            )
            reg_label_info(artifact_reduce, "params.artifact_label", "params.artifact_info")
        with gr.Row():
            poisson_strength = gr.Slider(
                minimum=0.0, maximum=1.0,
                value=config.POSTPROCESS_POISSON_BLEND_STRENGTH, step=0.05,
                label=t("params.poisson_strength_label"),
                info=t("params.poisson_strength_info"),
            )
            reg_label_info(poisson_strength, "params.poisson_strength_label", "params.poisson_strength_info")
            texture_amount = gr.Slider(
                minimum=0.0, maximum=1.0,
                value=config.POSTPROCESS_TEXTURE_TRANSFER_AMOUNT, step=0.05,
                label=t("params.texture_amount_label"),
                info=t("params.texture_amount_info"),
            )
            reg_label_info(texture_amount, "params.texture_amount_label", "params.texture_amount_info")
            artifact_amount = gr.Slider(
                minimum=0.0, maximum=1.0,
                value=config.POSTPROCESS_ARTIFACT_REDUCE_AMOUNT, step=0.05,
                label=t("params.artifact_amount_label"),
                info=t("params.artifact_amount_info"),
            )
            reg_label_info(artifact_amount, "params.artifact_amount_label", "params.artifact_amount_info")
            # 阶段 29：轻量超分（细节重建）开关 + 强度滑块。
            superres = gr.Checkbox(
                value=config.POSTPROCESS_SUPERRES_ENABLED,
                label=t("params.superres_label"),
                info=t("params.superres_info"),
            )
            reg_label_info(superres, "params.superres_label", "params.superres_info")
            superres_amount = gr.Slider(
                minimum=0.0, maximum=1.0,
                value=config.POSTPROCESS_SUPERRES_AMOUNT, step=0.05,
                label=t("params.superres_amount_label"),
                info=t("params.superres_amount_info"),
            )
            reg_label_info(superres_amount, "params.superres_amount_label", "params.superres_amount_info")
        # 阶段 32：真·超分（AI 模型放大）开关 + 放大倍数滑块。
        with gr.Row():
            real_superres = gr.Checkbox(
                value=config.REAL_SUPERRES_ENABLED,
                label=t("params.real_superres_label"),
                info=t("params.real_superres_info"),
            )
            reg_label_info(real_superres, "params.real_superres_label", "params.real_superres_info")
            real_scale = gr.Slider(
                minimum=1, maximum=8,
                value=config.REAL_SUPERRES_SCALE, step=1,
                label=t("params.real_scale_label"),
                info=t("params.real_scale_info"),
            )
            reg_label_info(real_scale, "params.real_scale_label", "params.real_scale_info")
        # 阶段 34：真实超分后端选择器（auto / esrgan / realesrgan）。
        real_backend = gr.Dropdown(
            choices=["auto", "esrgan", "realesrgan"],
            value=config.REAL_SUPERRES_BACKEND,
            label=t("params.real_backend_label"),
            info=t("params.real_backend_info"),
        )
        reg_label_info(real_backend, "params.real_backend_label", "params.real_backend_info")
        handles = RepairParamHandles(
            engine=engine, sensitivity=sensitivity, strength=strength,
            denoise=denoise, dilate=dilate, feather=feather, threshold=threshold,
            postprocess=postprocess, sharpen=sharpen,
            poisson_blend=poisson_blend, poisson_strength=poisson_strength,
            texture_transfer=texture_transfer, texture_amount=texture_amount,
            artifact_reduce=artifact_reduce, artifact_amount=artifact_amount,
            superres=superres, superres_amount=superres_amount,
            real_superres=real_superres, real_scale=real_scale,
            real_backend=real_backend,
        )
        # 阶段 35：登记真实超分控件，供设置页「应用」时跨页刷新。
        register_superres_handles(handles)
        return handles

    if accordion:
        with gr.Accordion(t(accordion_title), open=open_) as _acc:
            reg_label(_acc, accordion_title)
            return _body()
    return _body()


def param_values(h: RepairParamHandles) -> dict:
    """
    把修复参数句柄「当前值」提取成字典，便于传给 process_auto_repair 等。

    参数说明：
        h : build_repair_params() 返回的句柄。

    返回值：
        字典：{engine, sensitivity, strength, dilate, feather, threshold, denoise}。
        注意键名与 ui/tab_batch._process_one / process_auto_repair 的形参一致。
    """
    return {
        "engine": h.engine.value,
        "sensitivity": h.sensitivity.value,
        "strength": h.strength.value,
        "dilate": h.dilate.value,
        "feather": h.feather.value,
        "threshold": h.threshold.value,
        "denoise": h.denoise.value,
        "postprocess": h.postprocess.value,
        "sharpen": h.sharpen.value,
        "poisson_blend": h.poisson_blend.value,
        "poisson_strength": h.poisson_strength.value,
        "texture_transfer": h.texture_transfer.value,
        "texture_amount": h.texture_amount.value,
        "artifact_reduce": h.artifact_reduce.value,
        "artifact_amount": h.artifact_amount.value,
        "superres": h.superres.value,
        "superres_amount": h.superres_amount.value,
        "real_superres": h.real_superres.value,
        "real_scale": h.real_scale.value,
        "real_backend": h.real_backend.value,
    }


def repair_params_text(
    engine: str,
    strength,
    dilate,
    feather,
    threshold,
    denoise,
) -> str:
    """历史记录里的「修复参数摘要」文案（**唯一真值来源**，走 i18n）。

    为什么需要它（阶段 110 审计发现，属技能候选 #4「同一数据的多个出口各自拼装」
    + #11「文案生成层的硬编码」）：
        `ui/tab_single.py` / `ui/tab_batch.py` / `ui/tab_auto.py` **各写一份** f-string，
        且**硬编码中文**：`f"引擎={engine} 力度={strength} …"`。
        该文案会被 `services/report_service` 渲染进 HTML（`参数：…`）与 PDF 报告
        ⇒ **切到 en-US 后报告里仍是中文**（i18n 泄漏）；且三处同语义不同源
        ⇒ 改一处会漏两处。

    参数说明：
        engine/strength/dilate/feather/threshold/denoise : 本次修复的实参。

    返回值：
        已本地化的摘要字符串（键 `params.hist_summary`，zh-CN / en-US 均有翻译）。
    """
    return t(
        "params.hist_summary",
        engine=engine, strength=strength, denoise=denoise,
        dilate=dilate, feather=feather, threshold=threshold,
    )


def repair_params_text_short(engine: str, strength, denoise) -> str:
    """历史页「重新处理」的摘要（**只有 3 个参数在作用域内**，且标注来源）。

    为什么单列一个函数：`ui/tab_history.reprocess(rec_id, engine, strength, denoise)` 的
    入口只接收这 3 个值（膨胀/平滑/阈值不在该页的控件里）。若强行复用 6 参数模板，
    只能补 `None` ⇒ 摘要出现 "None"。⇒ 判据是**参数可得性**，不是"看起来像同一种文案"。
    """
    return t("params.hist_summary_short",
             engine=engine, strength=strength, denoise=denoise)


def build_export_format_selector(
    *,
    format_label_key: str = "params.export_format_label",
    quality_label_key: str = "params.export_quality_label",
) -> tuple[gr.Radio, gr.Slider]:
    """
    构建「导出格式 + 画质」选择控件（设置页/批量页复用）。

    参数说明：
        format_label_key  : 格式 Radio 标签的 i18n 键。
        quality_label_key : 画质 Slider 标签的 i18n 键。

    返回值：
        (format_radio, quality_slider) 元组，调用方自行放入布局与 inputs。
    """
    fmt = gr.Radio(
        choices=list(config.SUPPORTED_OUTPUT_FORMATS),
        value=config.DEFAULT_OUTPUT_FORMAT,
        label=t(format_label_key),
        info=t("params.export_format_info"),
    )
    reg_label_info(fmt, format_label_key, "params.export_format_info")
    quality = gr.Slider(
        minimum=1, maximum=100,
        value=config.DEFAULT_OUTPUT_QUALITY, step=1,
        label=t(quality_label_key),
        info=t("params.export_quality_info"),
    )
    reg_label_info(quality, quality_label_key, "params.export_quality_info")
    return fmt, quality


def _kind_label(kind: str) -> str:
    """
    把检测区域类别（KIND_*）映射成本地化展示名（阶段 22 命中率展示用）。

    参数：kind — 区域类别常量（如 "translucent" / "grid_watermark"）。
    返回：本地化标签；若翻译缺失则回退为原常量名（不暴露内部 key）。
    """
    val = t("detect.kind." + kind)
    return val if not val.startswith("detect.kind.") else kind


def build_detection_stats_markdown(stats) -> str:
    """
    把一次检测的 DetectionStats 渲染成 Markdown（阶段 22 命中率展示）。

    设计说明：
        · 纯 UI 层字符串构造，依赖 utils.i18n.t 与 config，无 gradio 依赖，
          可安全被 tab_auto / tab_batch / tab_media 复用。
        · 入参为 core.detector.DetectionStats（或任意具备同名字段的兼容对象），
          不在此处 import core，避免组件库对检测核心产生硬依赖。

    参数：stats — 检测统计对象（可能为空）。
    返回：Markdown 字符串（含「检测命中率」标题）。
    """
    label = t("detect.stats.label")
    if stats is None:
        return f"### 🔍 {label}\n\n{t('detect.stats.placeholder')}"
    if getattr(stats, "region_count", 0) == 0:
        return f"### 🔍 {label}\n\n{t('detect.stats.none')}"

    coverage_pct = float(getattr(stats, "coverage_ratio", 0.0)) * 100.0
    pixels = int(getattr(stats, "mask_pixels", 0))
    count = int(getattr(stats, "region_count", 0))
    by_kind = getattr(stats, "by_kind", {}) or {}
    used_ocr = bool(getattr(stats, "used_ocr", False))

    lines = [f"### 🔍 {label}"]
    lines.append(
        "- " + t("detect.stats.coverage", coverage=f"{coverage_pct:.1f}", pixels=f"{pixels:,}")
    )
    lines.append("- " + t("detect.stats.regions", count=count))
    if by_kind:
        parts = [
            f"{_kind_label(kind)} ×{cnt}"
            for kind, cnt in sorted(by_kind.items(), key=lambda kv: -kv[1])
        ]
        lines.append("- " + t("detect.stats.by_kind") + " " + t("detect.stats.kind_sep").join(parts))
    if used_ocr:
        lines.append(
            "- " + t("detect.stats.ocr_on", conf=f"{config.DETECT_OCR_MIN_CONFIDENCE:.2f}")
        )
    else:
        lines.append("- " + t("detect.stats.ocr_off"))
    return "\n".join(lines)


def aggregate_detection_stats(items) -> "object | None":
    """
    把多张图（批量 / 媒体页）的 DetectionStats 聚合成一个合成统计（阶段 23）。

    设计说明：
        · 纯 UI 层聚合，延迟 import core.detector.DetectionStats，
          不强制组件库对检测核心产生导入期依赖。
        · 覆盖率取各图平均值；像素数 / 区域数 / 各类别命中累加；
          OCR 只要任一张用过即记为启用。

    参数：items — 可迭代的 DetectionStats（或任意具备同名字段的兼容对象），允许含 None。
    返回：合成 DetectionStats；若没有任何有效统计则返回 None。
    """
    mask_pixels = 0
    regions = 0
    by_kind: dict = {}
    ocr = False
    cov_sum = 0.0
    n = 0
    for st in items:
        if st is None:
            continue
        mask_pixels += int(getattr(st, "mask_pixels", 0) or 0)
        regions += int(getattr(st, "region_count", 0) or 0)
        ocr = ocr or bool(getattr(st, "used_ocr", False))
        cov_sum += float(getattr(st, "coverage_ratio", 0.0) or 0.0)
        n += 1
        for k, c in (getattr(st, "by_kind", {}) or {}).items():
            by_kind[k] = by_kind.get(k, 0) + int(c)
    if n == 0:
        return None
    from core.detector import DetectionStats
    return DetectionStats(
        mask_pixels=mask_pixels,
        coverage_ratio=(cov_sum / n) if n else 0.0,
        region_count=regions,
        by_kind=by_kind,
        used_ocr=ocr,
    )


def build_aggregate_detection_stats_markdown(items) -> str:
    """
    把多张图的检测统计聚合并渲染成 Markdown（阶段 23，批量 / 媒体页复用）。

    参数：items — 可迭代的 DetectionStats（允许含 None）。
    返回：Markdown 字符串（无有效统计时显示占位）。
    """
    syn = aggregate_detection_stats(items)
    return build_detection_stats_markdown(syn)


if __name__ == "__main__":
    # 阶段 138：自检必须在 Blocks 上下文内真正构造组件（旧版把任何异常吞成
    # 「注意」且退出码 0 —— 本机 gradio 下构造实际失败（'Slider' object has
    # no attribute 'page'）却一直显示「通过」，是哑巴自检的活案例）。
    def _run_selfcheck() -> int:
        handles = build_repair_params(accordion=False)
        vals = param_values(handles)
        assert set(vals.keys()) == {
            "engine", "sensitivity", "strength", "dilate", "feather",
            "threshold", "denoise", "postprocess", "sharpen",
            "poisson_blend", "poisson_strength", "texture_transfer",
            "texture_amount", "artifact_reduce", "artifact_amount",
            "superres", "superres_amount", "real_superres", "real_scale",
            "real_backend",
        }, "参数键不完整"
        # dataclass 字段与 param_values 键集必须一致（防两头各改各的）。
        assert set(vals.keys()) == {f.name for f in _dc_fields(handles)}, (
            "param_values 键集与 RepairParamHandles 字段不一致"
        )
        build_export_format_selector()
        return len(vals)

    try:
        with gr.Blocks():
            _n = _run_selfcheck()
        print(f"组件构造成功：修复参数 {_n} 项 + 导出选择 2 项。")
        print("ui/components.py 自检通过。")
    except Exception as exc:  # noqa: BLE001
        # 构造失败必须失败退出（非零），绝不允许静默「通过」。
        raise SystemExit(f"ui/components.py 自检失败：{exc}")
