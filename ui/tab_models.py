# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/tab_models.py
 文件说明 : 模型管理标签页（阶段 12 新增，阶段 19 接入 i18n）。

 功能
 --------------------------------------------------------------------------
 · 列出全部模型槽（内置 lama / 配置登记 / 在 models/ 自动发现的 .pt/.onnx）；
 · 显示每个槽的存在性、体积、是否已加载、是否为当前模型；
 · 「当前模型」一键切换（LaMa 引擎随即改用其权重，持久化重启仍生效）；
 · 下载指定模型（带 gr.Progress 实时进度条；覆盖前自动备份，失败自动回滚）；
 · 校验完整性（存在性 / 体积 / SHA256）；
 · 回滚到上一版权重备份。

 复用关系
 --------------------------------------------------------------------------
 · 核心逻辑全部来自 core.model_manager（与 CLI / API 共用同一套模型注册表），
   本页只做界面呈现与事件绑定，不含任何业务逻辑。
 · 文案统一走 utils.i18n 的 t()，组件注册进 ui.i18n_rt 以支持实时语言切换。
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

import gradio as gr

import config
from core import model_manager as _mm
from utils.logger import get_logger
from utils.i18n import t
from utils.exceptions import format_exception_for_ui
from ui.i18n_rt import (
    register,
    reg_value,
    reg_label,
    reg_label_info,
)

log = get_logger("ui.tab_models")


def _render_models_md() -> str:
    """
    把当前模型注册表渲染为 Markdown 清单（含当前模型标记、状态、体积）。

    返回值：
        Markdown 字符串。
    """
    try:
        slots = _mm.list_models()
        active = _mm.get_active_model()
    except Exception as exc:  # noqa: BLE001
        return t("models.read_fail", exc=format_exception_for_ui(exc))

    if not slots:
        return t("models.empty")

    lines = [t("models.list_title"), ""]
    for s in slots:
        mark = " *" if s["name"] == active else "  "
        avail = t("models.avail") if _mm.is_model_available(s["name"]) else t("models.missing")
        if s.get("size_mb"):
            sz = f"{s['size_mb']} MB"
        elif s.get("expected_mb"):
            sz = t("models.expected_mb", mb=s["expected_mb"])
        else:
            sz = t("models.size_unknown")
        loaded = t("models.loaded_tag") if s.get("loaded") else ""
        lines.append(
            t(
                "models.slot_line",
                mark=mark,
                name=s["name"],
                display=s["display_name"],
                avail=avail,
                size=sz,
                loaded=loaded,
            )
        )
        if s.get("note"):
            lines.append(f"  - _{s['note']}_")
    lines.append("")
    lines.append(t("models.active", name=active))
    return "\n".join(lines)


def _render_status_md() -> str:
    """
    把当前运行状态渲染为 Markdown（设备 / GPU / FP16 / 内存缓存 / 权重路径）。

    返回值：
        Markdown 字符串。
    """
    try:
        info = _mm.get_model_info()
    except Exception as exc:  # noqa: BLE001
        return t("models.read_fail", exc=format_exception_for_ui(exc))

    dev_kind = info.get("device", "cpu")
    dev_name = info.get("device_name", dev_kind)
    if info.get("device_is_gpu"):
        dev_label = t("models.status_device_gpu")
    else:
        dev_label = t("models.status_device_cpu")
    dev_line = t("models.status_device", name=dev_name, kind=dev_kind)

    lines = [
        t("models.status_title"),
        "",
        # 阶段 162：分隔符必须**留在本地化模板内**（与 slot_line 的 zh「｜」/ en「|」一致），
        # 不得在 UI 代码里硬编码全角「｜」——否则 en-US 下仍显示全角分隔符（i18n 泄漏），
        # 且项目既有的 CJK-only i18n 守卫（只扫 U+4E00–U+9FFF）**结构上看不到**这类泄漏。
        t("models.status_device_line", line=dev_line, label=dev_label),
    ]
    if info.get("device_is_gpu"):
        lines.append(f"- {t('models.status_vram', vram=info.get('vram_mb', 0))}")
    fp16_state = t("models.status_fp16_on") if info.get("fp16") else t("models.status_fp16_off")
    lines.append(f"- {t('models.status_fp16', state=fp16_state)}")

    n = info.get("cache_count", 0)
    loaded_state = (
        t("models.status_loaded_yes") if info.get("loaded") else t("models.status_loaded_no")
    )
    lines.append(f"- {t('models.status_loaded', n=n, state=loaded_state)}")
    lines.append(f"- {t('models.status_active_path', path=info.get('path') or '—')}")
    return "\n".join(lines)


def _slot_choices() -> list[str]:
    """返回模型槽名称列表（供下拉框选项）。"""
    try:
        return [s["name"] for s in _mm.list_models()]
    except Exception:  # noqa: BLE001
        return ["lama"]


def _safe_active_model() -> str:
    """
    取当前模型名；注册表初始化异常时回退到内置槽。

    为什么需要它（阶段 94 D2）：`build_models_tab` 在**构建期**就要拿当前模型名，
    而这里是应用启动路径——一旦 `get_active_model()` 抛异常，**整个应用起不来**
    （不是单个标签页报错）。同文件的 `_slot_choices()` 有回退、`_render_*` 有
    try/except，只有这一处裸露，属「同一风险面三种处理方式」。
    """
    try:
        return _mm.get_active_model()
    except Exception as exc:  # noqa: BLE001
        log.warning("读取当前模型失败，回退内置槽 lama：%s", exc)
        return "lama"


def on_refresh() -> tuple:
    """刷新模型清单与下拉选项。"""
    return (
        _render_models_md(),
        gr.update(choices=_slot_choices(), value=_safe_active_model()),
        _render_status_md(),
        t("models.refresh_done"),
    )


def on_set_active(name: str) -> tuple:
    """把选中的模型槽设为当前模型。"""
    if not name:
        return _render_models_md(), gr.update(), _render_status_md(), t("models.no_slot")
    try:
        ok = _mm.set_active_model(name)
    except Exception as exc:  # noqa: BLE001
        log.warning("UI 切换当前模型失败：%s", exc)
        return (
            _render_models_md(),
            gr.update(),
            _render_status_md(),
            t("models.op_fail", exc=format_exception_for_ui(exc)),
        )
    if ok:
        return (
            _render_models_md(),
            gr.update(choices=_slot_choices(), value=name),
            _render_status_md(),
            t("models.set_active_done", name=name),
        )
    return (
        _render_models_md(),
        gr.update(),
        _render_status_md(),
        t("models.set_active_fail", name=name),
    )


def on_download(name: str, progress: gr.Progress = gr.Progress()) -> str:
    """
    下载选中的模型权重（带实时进度条）。

    参数说明：
        name    : 目标模型槽名称（空则下载当前模型）。
        progress: Gradio 进度条句柄。
    """
    target = name or _safe_active_model()
    progress(0.0, t("models.download_start", target=target))

    def _cb(downloaded: int, total: int, speed_kbps: float) -> None:
        frac = downloaded / total if total else 0.0
        progress(
            frac,
            t(
                "models.download_progress",
                done=f"{downloaded / 1e6:.1f}",
                total=f"{total / 1e6:.1f}",
                kbps=f"{speed_kbps:.0f}",
            ),
        )

    try:
        path = _mm.download_model(target, progress_cb=_cb)
        return t("models.download_done", target=target, path=path)
    except Exception as exc:  # noqa: BLE001
        log.warning("UI 下载模型失败：%s", exc)
        return t("models.download_fail", exc=format_exception_for_ui(exc))


def on_verify(name: str) -> tuple:
    """校验选中模型的完整性。"""
    try:
        v = _mm.verify_model(name or None)
        status = t("models.verify_pass") if v["ok"] else t("models.verify_fail")
        msg = t("models.verify_done", name=v["name"], status=status, reason=v["reason"])
    except Exception as exc:  # noqa: BLE001
        log.warning("UI 校验模型失败：%s", exc)
        msg = t("models.op_fail", exc=format_exception_for_ui(exc))
    return (_render_models_md(), msg)


def on_rollback(name: str) -> tuple:
    """把选中模型回滚到上一版备份。"""
    target = name or _safe_active_model()
    try:
        ok = _mm.rollback_model(target)
        msg = (t("models.rollback_done", target=target) if ok
               else t("models.rollback_fail", target=target))
    except Exception as exc:  # noqa: BLE001
        log.warning("UI 回滚模型失败：%s", exc)
        msg = t("models.op_fail", exc=format_exception_for_ui(exc))
    return (_render_models_md(), msg)


def on_reload() -> tuple:
    """
    清空模型内存缓存并强制重新加载当前模型（热重载）。

    适用于「程序运行期间手动替换/重下了权重文件」的场景，
    path 未变但旧模型仍驻留内存时，无需重启即可生效。
    """
    try:
        r = _mm.reload_active()
        if r["ok"]:
            state = (
                t("models.reload_loaded_yes") if r["loaded"] else t("models.reload_loaded_no")
            )
            msg = t("models.reload_done", name=r["name"], state=state)
        else:
            msg = t("models.reload_fail", error=r["error"])
    except Exception as exc:  # noqa: BLE001
        log.warning("UI 热重载模型失败：%s", exc)
        msg = t("models.op_fail", exc=format_exception_for_ui(exc))
    return (_render_status_md(), _render_models_md(), msg)


def on_detail(name: str) -> str:
    """展示选中模型的详尽元数据（路径 / 大小 / 修改时间 / SHA256 / 状态）。"""
    try:
        d = _mm.slot_detail(name or None)
        if not d.get("exists"):
            return t("models.detail_missing", name=d.get("name", ""))
        status = t("models.verify_pass") if d["ok"] else t("models.verify_fail")
        return t(
            "models.detail_done",
            name=d.get("name", ""),
            path=d.get("path", ""),
            size=d.get("size_mb", 0),
            mtime=d.get("mtime") or t("models.size_unknown"),
            sha=d.get("sha256") or "—",
            sha_c=d.get("sha_computed") or "—",
            status=status,
            reason=d.get("reason", ""),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("UI 读取模型详情失败：%s", exc)
        return t("models.op_fail", exc=format_exception_for_ui(exc))


def build_models_tab():
    """构建「模型管理」标签页。"""
    with gr.Column():
        _models_title = gr.Markdown(t("models.title"))
        reg_value(_models_title, "models.title")
        _models_subtitle = gr.Markdown(t("models.subtitle"))
        reg_value(_models_subtitle, "models.subtitle")

        models_md = gr.Markdown(value=_render_models_md(), label=t("models.list_label"))
        register(models_md, lambda: {"value": _render_models_md()})

        # 运行状态面板：设备 / GPU / FP16 / 内存缓存 / 权重路径。
        status_md = gr.Markdown(value=_render_status_md(), label=t("models.status_title"))
        register(status_md, lambda: {"value": _render_status_md()})

        with gr.Row():
            model_selector = gr.Dropdown(
                choices=_slot_choices(),
                value=_safe_active_model(),
                label=t("models.select_label"),
                info=t("models.select_info"),
                scale=3,
            )
            reg_label_info(
                model_selector, "models.select_label", "models.select_info"
            )
            refresh_btn = gr.Button(t("models.refresh"), scale=1)
            reg_label(refresh_btn, "models.refresh")

        with gr.Row():
            set_active_btn = gr.Button(t("models.set_active"), variant="primary")
            reg_label(set_active_btn, "models.set_active")
            download_btn = gr.Button(t("models.download"), variant="primary")
            reg_label(download_btn, "models.download")
        with gr.Row():
            verify_btn = gr.Button(t("models.verify"))
            reg_label(verify_btn, "models.verify")
            rollback_btn = gr.Button(t("models.rollback"))
            reg_label(rollback_btn, "models.rollback")
        with gr.Row():
            reload_btn = gr.Button(t("models.reload"), variant="secondary")
            reg_label(reload_btn, "models.reload")
            detail_btn = gr.Button(t("models.detail"))
            reg_label(detail_btn, "models.detail")

        info = gr.Markdown("")
        detail_md = gr.Markdown("")

        # 绑定事件。
        refresh_btn.click(
            fn=on_refresh,
            inputs=[],
            outputs=[models_md, model_selector, status_md, info],
        )
        set_active_btn.click(
            fn=on_set_active,
            inputs=[model_selector],
            outputs=[models_md, model_selector, status_md, info],
        )
        download_btn.click(
            fn=on_download,
            inputs=[model_selector],
            outputs=[info],
        )
        verify_btn.click(
            fn=on_verify,
            inputs=[model_selector],
            outputs=[models_md, info],
        )
        rollback_btn.click(
            fn=on_rollback,
            inputs=[model_selector],
            outputs=[models_md, info],
        )
        reload_btn.click(
            fn=on_reload,
            inputs=[],
            outputs=[status_md, models_md, info],
        )
        detail_btn.click(
            fn=on_detail,
            inputs=[model_selector],
            outputs=[detail_md],
        )
