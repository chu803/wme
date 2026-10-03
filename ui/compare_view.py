# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/compare_view.py
 文件说明 : 前后对比查看器组件（阶段 38 新增，界面层）。

            阶段 31 的三模式对比（拼接 / 滑块 / 热图）只落在单图页与自动页，
            批量页与历史页仍只有静态缩略图——跑完一整批想逐张核查修复质量，
            只能把结果文件翻出来自己比。本模块把「查看器」做成可复用组件，
            批量页与历史页各建一份，行为完全一致：

              画廊点选任意条目 → 展示该条目「原图 vs 结果」对比
                              → 可切换三种模式
                              → 可看客观差异摘要（改动像素占比等）
                              → 可一键导出原始分辨率对比图

            职责边界：
            · 本模块只做「组件构建 + 事件回调 + 文案本地化」；
              所有图像计算与落盘都下沉在 services.compare_service。
            · 允许 import gradio 与 ui.i18n_rt，但不 import 任何 tab_* 模块，
              避免与调用方形成循环依赖。

            选中状态由调用方持有（各页自己的「画廊索引 → 配对」映射），
            本模块通过 gr.State 接收「原图路径 / 结果路径 / 条目名」三元组，
            因此同一页面里建多个查看器也互不干扰。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from dataclasses import dataclass

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import gradio as gr

import config
from services import compare_service as _cs
from ui.i18n_rt import reg_label, reg_label_info, reg_value
from utils.i18n import t
from utils.logger import get_logger

log = get_logger("ui.compare_view")


@dataclass
class CompareViewer:
    """
    一套「前后对比查看器」的组件句柄集合。

    字段说明：
        pair_state : gr.State，保存 (原图路径, 结果路径, 条目名) 三元组。
        mode       : gr.Radio，对比模式（concat / slider / heatmap）。
        html       : gr.HTML，对比结果渲染区。
        summary    : gr.Markdown，选中条目名 + 差异摘要。
        refresh_btn: gr.Button，手动刷新对比。
        export_btn : gr.Button，导出原始分辨率对比图。
        file       : gr.File，导出结果下载位。
    """

    pair_state: "gr.State"
    mode: "gr.Radio"
    html: "gr.HTML"
    summary: "gr.Markdown"
    refresh_btn: "gr.Button"
    export_btn: "gr.Button"
    file: "gr.File"


def _mode_choices() -> list:
    """三种对比模式的（显示名, 稳定值）选项，与阶段 31 单图/自动页保持一致。"""
    return [
        (t("compare.mode_concat"), _cs.MODE_CONCAT),
        (t("compare.mode_slider"), _cs.MODE_SLIDER),
        (t("compare.mode_heatmap"), _cs.MODE_HEATMAP),
    ]


def format_summary(name: str, orig_path: str, result_path: str) -> str:
    """
    组装「当前条目 + 差异摘要」的 Markdown 文本。

    参数说明：
        name        : 条目显示名（文件名或记录标题）。
        orig_path   : 原图路径。
        result_path : 结果图路径。

    返回值：
        两行 Markdown：第一行条目名，第二行客观差异统计；
        统计不可用时第二行显示占位文案（不报错）。
    """
    head = t("compare.viewer.selected", name=name or "—")
    stat = _cs.summarize_pair(orig_path, result_path)
    if not stat:
        return head + "\n\n" + t("compare.viewer.summary_none")
    line = t(
        "compare.viewer.summary",
        w=stat["width"], h=stat["height"],
        ratio=f"{stat['changed_ratio'] * 100:.2f}%",
        mean=f"{stat['mean_diff']:.2f}",
        mx=f"{stat['max_diff']:.0f}",
    )
    if stat.get("upscaled"):
        line += t(
            "compare.viewer.summary_upscaled",
            rw=stat["result_width"], rh=stat["result_height"],
        )
    return head + "\n\n" + line


def render_pair(pair, mode: str) -> "tuple[str, str]":
    """
    渲染一对「原图 / 结果」的对比视图（查看器的核心回调）。

    参数说明：
        pair : (原图路径, 结果路径, 条目名) 三元组；None 或空表示尚未选中。
        mode : 对比模式（界面 Radio 的稳定值）。

    返回值：
        (对比区 HTML, 摘要 Markdown)。各类异常情况都返回可读提示而非抛错：
          · 未选中        → 提示先在画廊点选；
          · 缺原图/结果   → 提示文件缺失；
          · 渲染失败      → 提示切换拼接模式重试。
    """
    if not pair:
        return "", t("compare.viewer.no_select")
    try:
        orig_path, result_path, name = (list(pair) + ["", "", ""])[:3]
    except Exception:  # noqa: BLE001 - 防御异常形状的 state
        return "", t("compare.viewer.no_select")
    if not orig_path or not result_path:
        return "", t("compare.viewer.no_pair")
    if not _os.path.isfile(str(orig_path)) or not _os.path.isfile(str(result_path)):
        return "", t("compare.viewer.no_pair")

    # 根因修复：文档承诺「各类异常都返回可读提示而非抛错」，但渲染引擎调用原先
    # 未加保护——一旦 _cs.render_pair_html 内部出错（如图片损坏、PIL 异常），
    # 异常会穿透到 Gradio 的 .select 回调并泄露完整 traceback。统一在此兜底，
    # 让 tab_batch / tab_history 等所有调用方的画廊点选回调都不再裸奔。
    try:
        html = _cs.render_pair_html(
            orig_path, result_path, mode,
            before_label=t("single.orig_label"),
            after_label=t("single.result_label"),
            heatmap_caption=t("compare.heatmap_label"),
        )
    except Exception as exc:  # noqa: BLE001 - 渲染异常不向上泄漏（与 docstring 承诺一致）
        log.warning("对比视图渲染失败（%s）：%s", mode, exc)
        return "", t("compare.viewer.render_fail")
    if not html:
        return "", t("compare.viewer.render_fail")
    # 摘要生成同样不得抛错（docstring：各类异常都返回可读提示而非抛错）。
    # format_summary 内部调 services.compare_service.summarize_pair，其对齐/统计
    # 段并无服务级兜底（极端场景或未来回归可能抛错）——阶段 97 的 I2 修复只护住
    # 了 render_pair_html，这里是同类残余，补齐：失败时保留已渲染的 HTML，
    # 摘要退回「条目名 + 统计不可用」占位。
    name_disp = str(name or _os.path.basename(str(result_path)))
    try:
        summary_md = format_summary(name_disp, str(orig_path), str(result_path))
    except Exception as exc:  # noqa: BLE001 - 摘要异常不向上泄漏（与 docstring 承诺一致）
        log.warning("对比视图摘要生成失败（%s）：%s", mode, exc)
        summary_md = (
            t("compare.viewer.selected", name=name_disp)
            + "\n\n" + t("compare.viewer.summary_none")
        )
    return html, summary_md


def export_pair(pair) -> "tuple[str | None, str]":
    """
    导出当前选中条目的对比图（原始分辨率 PNG，落到 EXPORT_DIR）。

    参数说明：
        pair : (原图路径, 结果路径, 条目名) 三元组。

    返回值：
        (文件路径 or None, 提示 Markdown)。
    """
    if not pair:
        return None, t("compare.viewer.no_select")
    try:
        orig_path, result_path = (list(pair) + ["", ""])[:2]
    except Exception:  # noqa: BLE001
        return None, t("compare.viewer.no_select")
    out = _cs.export_pair_compare(orig_path, result_path)
    if not out:
        return None, t("compare.viewer.export_fail")
    return out, t("compare.viewer.export_ok", path=out)


def build_compare_viewer(
    elem_prefix: str,
    *,
    open_: bool = False,
    show_title: bool = True,
) -> CompareViewer:
    """
    构建一套「前后对比查看器」组件（放在当前 Blocks 上下文中）。

    参数说明：
        elem_prefix : elem_id 前缀（如 "batch" / "history"），保证同页多份不撞 id。
        open_       : 折叠面板默认是否展开。
        show_title  : 是否显示标题行（放进已有面板时可关掉）。

    返回值：
        CompareViewer 句柄，调用方需要做的只有两件事：
          1. 在画廊 select 回调里把 (原图, 结果, 名称) 写进 viewer.pair_state；
          2. 把 viewer.html / viewer.summary 加进该回调的 outputs（或直接
             调用本模块的 render_pair 复用渲染逻辑）。

    说明：
        所有静态文案都注册进 i18n 运行时，语言切换即时刷新。
    """
    pair_state = gr.State(value=None)

    with gr.Accordion(t("compare.viewer.title"), open=open_) as acc:
        reg_label(acc, "compare.viewer.title")
        if show_title:
            hint = gr.Markdown(t("compare.viewer.hint"))
            reg_value(hint, "compare.viewer.hint")
        with gr.Row():
            mode = gr.Radio(
                choices=_mode_choices(),
                value=_cs.normalize_mode(config.UI_COMPARE_DEFAULT_MODE),
                label=t("compare.viewer.mode_label"),
                info=t("compare.viewer.mode_info"),
                elem_id=f"{elem_prefix}_compare_mode",
            )
            from ui.i18n_rt import register as _register
            _register(mode, lambda: {
                "choices": _mode_choices(),
                "label": t("compare.viewer.mode_label"),
                "info": t("compare.viewer.mode_info"),
            })
        summary = gr.Markdown(value=t("compare.viewer.no_select"))
        reg_value(summary, "compare.viewer.no_select")
        html = gr.HTML(value="", elem_id=f"{elem_prefix}_compare_html")
        with gr.Row():
            refresh_btn = gr.Button(
                t("compare.viewer.btn"), variant="secondary",
                elem_id=f"{elem_prefix}_compare_refresh",
            )
            reg_value(refresh_btn, "compare.viewer.btn")
            export_btn = gr.Button(
                t("compare.viewer.export_btn"), variant="primary",
                elem_id=f"{elem_prefix}_compare_export",
            )
            reg_value(export_btn, "compare.viewer.export_btn")
        file_out = gr.File(label=t("compare.viewer.file_label"), interactive=False)
        reg_label(file_out, "compare.viewer.file_label")

    viewer = CompareViewer(
        pair_state=pair_state, mode=mode, html=html, summary=summary,
        refresh_btn=refresh_btn, export_btn=export_btn, file=file_out,
    )

    # 模式切换 / 手动刷新：都用当前 pair_state 重渲染。
    mode.change(
        fn=render_pair, inputs=[pair_state, mode], outputs=[html, summary]
    )
    refresh_btn.click(
        fn=render_pair, inputs=[pair_state, mode], outputs=[html, summary]
    )
    # 导出对比图。
    export_btn.click(
        fn=export_pair, inputs=[pair_state], outputs=[file_out, summary]
    )
    return viewer


# ==============================================================================
# 模块自检（无需启动服务，仅验证纯函数回调；组件构建交由 tests/test_ui.py 覆盖）
# ==============================================================================
if __name__ == "__main__":
    import tempfile

    import numpy as np

    from core.image_utils import save_image

    tmp = tempfile.mkdtemp(prefix="compare_view_selftest_")
    src = _os.path.join(tmp, "原图.png")
    res = _os.path.join(tmp, "结果.png")
    base = np.full((80, 100, 3), 100, dtype=np.uint8)
    marked = base.copy()
    marked[10:30, 20:60] = 250
    save_image(src, marked, fmt="PNG")
    save_image(res, base, fmt="PNG")

    # 用例 1：未选中。
    h, s = render_pair(None, "concat")
    assert h == "" and ("尚未选择" in s or "Nothing selected" in s), "未选中提示异常"
    print("用例1通过：未选中提示正常")

    # 用例 2：文件缺失。
    h, s = render_pair((src, _os.path.join(tmp, "无.png"), "x"), "concat")
    assert h == "" and s, "缺文件提示异常"
    print("用例2通过：缺文件提示正常")

    # 用例 3：三模式渲染 + 摘要。
    for m in ("concat", "slider", "heatmap"):
        h, s = render_pair((src, res, "测试图.png"), m)
        assert h and "<" in h, f"{m} 渲染为空"
        assert "测试图.png" in s, "摘要缺少条目名"
    print("用例3通过：三模式渲染与摘要正常")

    # 用例 4：导出。
    f, msg = export_pair((src, res, "测试图.png"))
    assert f and _os.path.isfile(f), "导出失败"
    print(f"用例4通过：导出 {f}")

    print("ui/compare_view.py 自检全部通过。")
