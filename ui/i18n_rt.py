# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/i18n_rt.py
 文件说明 : 国际化「实时切换」运行时（阶段 19 界面层）。

            职责（仅界面层，可 import gradio，但严禁 import ui 其它模块形成环）：
            1. register(component, resolver)
                 把 Gradio 组件注册进全局注册表，resolver() 返回一个
                 gr.update(**kwargs) 所需的字典（如 {"label": ..., "info": ...}）。
            2. 便捷 helper：
                  reg_value / reg_label / reg_info / reg_label_info / reg_textbox
                  （避免在每个标签页手写 lambda）。
            3. apply_language(lang)
                  设置语言并持久化，遍历注册表返回「与注册顺序一致」的
                 [gr.update(...), ...] 列表，供语言切换事件的 outputs 一次性刷新。
            4. registered_components()
                  返回与 apply_language 等长的组件列表（作为事件 outputs）。
            5. current_language()

            设计要点：
            · 静态文案（标题 / 折叠面板 / 按钮 / 滑块 / Markdown / 占位符）在
              构建时通过 t() 取当前语言，并注册进本表；切换语言时由 apply_language
              统一刷新。
            · 运行时生成的消息（状态日志 / 报错）直接在回调里调用 t()，天然
              反映当前语言，无需注册。
            · Gradio 的 TabItem 没有运行时 update 能力，标签页标题由 app.py 注入的
              JS 在切换时重命名（见 ui/app.py 的 _build_custom_js）。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import gradio as gr

from utils.i18n import set_language, save_language, t, get_language

# 注册表：[(component, resolver), ...]，resolver() -> dict（gr.update 的 kwargs）
_REGISTRY: list = []


def register(component, resolver) -> "object | None":
    """
    注册一个 Gradio 组件及其取值解析器。

    参数说明：
        component : Gradio 组件实例（或其句柄）；为 None 时直接返回，便于链式调用。
        resolver  : 无参可调用对象，返回 gr.update(**kwargs) 所需的字典
                    （如 {"label": t("x"), "info": t("y")}）。

    返回值：
        原 component（便于在 build 函数中直接 `register(comp, ...)` 包裹）。
    """
    if component is not None and callable(resolver):
        _REGISTRY.append((component, resolver))
    return component


def reg_value(component, key: str, **fmt) -> "object | None":
    """注册组件：value = t(key, **fmt)。用于 Markdown / 文本展示类组件。"""
    return register(component, lambda: {"value": t(key, **fmt)})


def reg_label(component, key: str) -> "object | None":
    """注册组件：label = t(key)。"""
    return register(component, lambda: {"label": t(key)})


def reg_info(component, key: str) -> "object | None":
    """注册组件：info = t(key)。"""
    return register(component, lambda: {"info": t(key)})


def reg_label_info(component, label_key: str, info_key: str) -> "object | None":
    """注册组件：label + info 同时刷新。用于滑块 / 单选 / 复选等。"""
    return register(component, lambda: {"label": t(label_key), "info": t(info_key)})


def reg_textbox(component, label_key: str, placeholder_key: "str | None" = None) -> "object | None":
    """注册文本框：label（+ 可选 placeholder）刷新。"""
    def _resolver():
        d = {"label": t(label_key)}
        if placeholder_key:
            d["placeholder"] = t(placeholder_key)
        return d
    return register(component, _resolver)


def apply_language(lang: str) -> list:
    """
    切换语言并刷新所有已注册组件。

    参数说明：
        lang : 目标语言代码（zh-CN / en-US）。非法值由 set_language 忽略。

    返回值：
        与注册表顺序一致的 [gr.update(...), ...] 列表（可直接作为事件返回值）。
        单个组件解析异常时回退为 gr.update()（整体不崩）。
    """
    set_language(lang)
    save_language(lang)
    updates = []
    for _comp, resolver in _REGISTRY:
        try:
            updates.append(gr.update(**resolver()))
        except Exception:  # noqa: BLE001
            updates.append(gr.update())
    return updates


def registered_components() -> list:
    """返回与 apply_language 等长、同序的组件列表（作为事件 outputs）。"""
    return [comp for comp, _ in _REGISTRY]


def clear_registry() -> None:
    """清空注册表（主要用于测试隔离与重复 build_app 时避免重复注册）。"""
    _REGISTRY.clear()


def current_language() -> str:
    """返回当前语言代码。"""
    return get_language()
