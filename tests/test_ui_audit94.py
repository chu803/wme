"""阶段 94 · `ui/tab_models` 审计的回归测试（含 `core/model_manager` 根因守卫）。

本文件名遵循「测本量具以外的模块」——它不是 `audit_selector` 的量具测试。

三类内容
--------------------------------------------------------------------------
  1) **真缺陷守卫**（先写测试、确认它红、再修 → 见 docs/94 的记录）：
     · D1 `core.model_manager._ensure_init` 在初始化**之前**置 `_INITED=True`
       ⇒ 自动发现的 I/O 一旦失败，注册表**永久半初始化**且异常冒到调用方；
     · D2 `ui/tab_models.build_models_tab` 在**构建期**裸调 `_mm.get_active_model()`
       ⇒ 该异常会让**整个应用起不来**（不是单个标签页报错）；同文件
       `_slot_choices()` 有回退、`_render_*` 有 try，同一风险面三种处理方式；
     · D3 `on_verify` / `on_detail` 无异常护栏，而 `on_download` 有
       ⇒ 违反阶段 81 的「UI 唯一异常入口 format_exception_for_ui」。
  2) **登记的边界（刻意不改，用测试钉住现状）**：
     · `gr.Markdown` 的 `label` 在 `container=False`（默认）下**永不渲染**
       ⇒ `tab_models` 两处 `label=t(...)` 与 `tab_history` 的 `reg_label` 都是
       无害死元数据。**不得**据此把 `reg_label` 扩散到更多 Markdown 上。
     · `gr.Progress()` 作为默认参数在导入期求值 —— 实测可构造、可调用，无害。
  3) **i18n 完整性**：`tab_models` 引用的 `models.*` 键在双语下必须都存在。
"""
from __future__ import annotations

import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core import model_manager as mm  # noqa: E402
from ui import tab_models as tm  # noqa: E402
from utils.i18n import set_language, t  # noqa: E402


# ---------------------------------------------------------------------------
# 全局状态快照/还原（model_manager 是进程内单例注册表）
# ---------------------------------------------------------------------------

@pytest.fixture
def frozen_registry():
    """快照并还原 model_manager 的模块级可变状态。"""
    snap = {
        "_SLOTS": dict(mm._SLOTS),
        "_ACTIVE": mm._ACTIVE,
        "_INITED": mm._INITED,
        "_CACHE": dict(mm._CACHE),
    }
    yield
    mm._SLOTS.clear()
    mm._SLOTS.update(snap["_SLOTS"])
    mm._ACTIVE = snap["_ACTIVE"]
    mm._INITED = snap["_INITED"]
    mm._CACHE.clear()
    mm._CACHE.update(snap["_CACHE"])


def _boom(*_a, **_k):
    raise OSError("INJECTED-IO-FAILURE")


# ===========================================================================
# D1 · 注册表不得因「自动发现」的 I/O 失败而永久半初始化
# ===========================================================================

def test_registry_survives_discover_io_failure(frozen_registry, monkeypatch):
    """D1：`os.listdir` 抛错时，注册表仍应给出内置槽，且不向调用方抛异常。

    修复前：异常从 `_ensure_init` 冒出，且 `_INITED` 已置 True
    ⇒ 后续每次调用都拿不到「配置槽」，且**永远不再重试**。
    """
    monkeypatch.setattr(mm.config, "MODEL_AUTO_DISCOVER", True,
                        raising=False)
    monkeypatch.setattr(os, "listdir", _boom)
    mm._SLOTS.clear()
    mm._INITED = False

    slots = mm.list_model_slots()          # 修复前此处抛 OSError
    names = [s.name for s in slots]
    assert "lama" in names, f"内置槽丢失：{names}"


def test_inited_flag_is_set_only_after_success(frozen_registry, monkeypatch):
    """D1b：初始化中途失败时 `_INITED` 必须保持 False（否则永久半初始化、不再重试）。

    注入点选 `_init_config_slots` ——它在 `_init_builtin` 之后、`_load_state` 之前，
    正好落在"已经写了一半注册表"的位置，是半初始化的典型窗口。
    """
    def _fail_init():
        raise RuntimeError("INJECTED-INIT-FAILURE")

    monkeypatch.setattr(mm, "_init_config_slots", _fail_init)
    mm._SLOTS.clear()
    mm._INITED = False

    with pytest.raises(RuntimeError):
        mm.list_model_slots()

    assert mm._INITED is False, (
        "初始化失败却把 _INITED 固化为 True → 注册表永久半初始化且不再重试"
    )

    # 去掉注入后应当能**重试**并成功（这正是 `_INITED` 未被固化的意义）。
    monkeypatch.undo()
    monkeypatch.setattr(mm.config, "MODEL_AUTO_DISCOVER", False, raising=False)
    mm._INITED = False
    names = [s.name for s in mm.list_model_slots()]
    assert "lama" in names


# ===========================================================================
# D2 · 构建期调用不得让整个应用起不来
# ===========================================================================

def test_build_models_tab_survives_active_model_failure(monkeypatch):
    """D2：`get_active_model()` 抛错时，构建标签页不应崩（整个 app 会起不来）。

    注意：`build_models_tab()` 必须在 `gr.Blocks()` 上下文内调用——否则 Gradio 的
    `gr.Row.__exit__` 会因缺少 Blocks 而报错（那是 Gradio 的用法约束，不是本缺陷）。
    """
    import gradio as gr

    from ui import i18n_rt

    snap = list(i18n_rt._REGISTRY)
    monkeypatch.setattr(mm, "get_active_model", _boom)
    try:
        with gr.Blocks():
            tm.build_models_tab()          # 修复前此处抛 OSError
    finally:
        i18n_rt._REGISTRY.clear()
        i18n_rt._REGISTRY.extend(snap)


def test_on_refresh_survives_active_model_failure(monkeypatch):
    """D2b：刷新事件同样不应把原始异常抛给 Gradio。"""
    monkeypatch.setattr(mm, "get_active_model", _boom)
    out = tm.on_refresh()                  # 修复前此处抛 OSError
    assert isinstance(out, tuple)


# ===========================================================================
# D3 · UI 异常入口统一（阶段 81 契约）
# ===========================================================================

@pytest.mark.parametrize("fn_name,dep,call", [
    ("on_verify", "verify_model", lambda: tm.on_verify("lama")),
    ("on_detail", "slot_detail", lambda: tm.on_detail("lama")),
    ("on_set_active", "set_active_model", lambda: tm.on_set_active("lama")),
    ("on_rollback", "rollback_model", lambda: tm.on_rollback("lama")),
    ("on_reload", "reload_active", lambda: tm.on_reload()),
])
def test_ui_handlers_do_not_propagate_manager_exceptions(monkeypatch, fn_name, dep, call):
    """D3：管理的异常必须变成**文案**，不得让 Gradio 展示原始 traceback。"""
    monkeypatch.setattr(mm, dep, _boom)
    out = call()                            # 修复前此处抛 OSError

    def _flat(o):
        if isinstance(o, (tuple, list)):
            return " ".join(_flat(x) for x in o)
        return str(o)

    assert "INJECTED-IO-FAILURE" in _flat(out), f"{fn_name} 未把异常转成文案：{out!r}"


def test_on_download_remains_guarded(monkeypatch):
    """对照组：`on_download` 本来就守（修复不得破坏它）。"""
    monkeypatch.setattr(mm, "download_model", _boom)
    assert "INJECTED-IO-FAILURE" in tm.on_download("lama")


# ===========================================================================
# 登记的边界（刻意不改；用测试钉住，若上游行为变了会强制重新决策）
# ===========================================================================

def test_boundary_gradio_markdown_label_is_never_rendered():
    """`gr.Markdown` 的 label 在 container=False（默认）下不渲染。

    ⇒ `tab_models` L281/L285 的 `label=t(...)` 与 `tab_history` 的 `reg_label`
    都是**无害死元数据**。若本条因 gradio 升级而失败，说明 label 开始渲染了，
    那时才需要给 Markdown 补 `reg_label`（**当前不需要**）。
    """
    import gradio as gr

    md = gr.Markdown(value="body", label="LBL")
    assert getattr(md, "container", None) is False
    assert getattr(md, "show_label", None) is False


def test_boundary_progress_default_is_import_safe():
    """`on_download(progress: gr.Progress = gr.Progress())` 的默认值在导入期求值。

    实测可构造、可当作无参回调调用（无请求上下文时为空操作），**不是缺陷**。
    """
    import inspect

    import gradio as gr

    sig = inspect.signature(tm.on_download)
    default = sig.parameters["progress"].default
    assert isinstance(default, gr.Progress)
    gr.Progress()(0.0, "probe")             # 不抛即通过


def test_boundary_slot_choices_hardcoded_fallback():
    """`_slot_choices()` 的 `["lama"]` 硬编码回退：**刻意保留**。

    它存在的意义正是「注册表也挂了」时仍能给下拉框一个可用项；改成动态回退
    会与它自身的保护目的矛盾。此处只钉住「回退项确实是内置槽」。
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(mm, "list_models", _boom)
        assert tm._slot_choices() == ["lama"]
    finally:
        monkeypatch.undo()


# ===========================================================================
# i18n 完整性
# ===========================================================================

def test_models_keys_complete_in_both_languages():
    """`tab_models` 引用的 `models.*` 键在 zh-CN / en-US 下都必须有翻译。"""
    src = open(os.path.join(_ROOT, "ui", "tab_models.py"), encoding="utf-8").read()
    keys = sorted(set(re.findall(r't\(\s*"(models\.[A-Za-z0-9_.]+)"', src)))
    assert keys, "未能从 tab_models 提取到任何 models.* 键（正则失效？）"
    missing = []
    for lang in ("zh-CN", "en-US"):
        set_language(lang)
        for k in keys:
            if t(k) == k:
                missing.append(f"{lang}:{k}")
    set_language("zh-CN")
    assert not missing, f"缺失翻译：{missing}"


def test_models_keys_are_shared_not_hardcoded():
    """界面文案不得硬编码中文（必须走 i18n）。

    **开发者日志除外**：`log.warning("UI 下载模型失败：%s")` 这类是给自己看的，
    本项目日志一贯中文（`core/` 各模块同理），i18n 契约只管**用户可见**文案。
    """
    src = open(os.path.join(_ROOT, "ui", "tab_models.py"), encoding="utf-8").read()
    # 去掉注释与文档串后，剔除 log.*(...) 调用，再看是否还有裸中文字面量。
    body = re.sub(r'"""[\s\S]*?"""', "", src)
    body = re.sub(
        r"\blog\.(?:debug|info|warning|error|critical)\((?:[^()]|\([^()]*\))*\)",
        "", body,
    )
    body = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    hits = re.findall(r'"[^"\n]*[\u4e00-\u9fff][^"\n]*"', body)
    assert not hits, f"疑似硬编码中文文案：{hits[:5]}"
