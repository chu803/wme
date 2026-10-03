# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 19 国际化（i18n）单元测试
 --------------------------------------------------------------------------
 覆盖点：
   · utils.i18n：t / set_language / get_language / load_language /
                 save_language / available_languages / 缺失键回退
   · ui.i18n_rt ：register / reg_* helper / apply_language /
                 registered_components / clear_registry（实时切换核心）
==============================================================================
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

# 路径加固：保证从仓库根运行也能 import。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.i18n import (
    t,
    set_language,
    get_language,
    load_language,
    save_language,
    available_languages,
    DEFAULT_LANG,
)
from ui.i18n_rt import (
    register,
    reg_value,
    reg_label,
    reg_info,
    reg_label_info,
    reg_textbox,
    apply_language,
    registered_components,
    clear_registry,
    current_language,
)


# ------------------------------------------------------------------------------
# 全局隔离（阶段 140 修复）
# ------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_lang_state(tmp_path, monkeypatch):
    """
    把语言持久化文件与内存语言状态全部隔离到临时目录。

    阶段 140 修复（真实缺陷，全量回归实测捕获）：
        `ui.i18n_rt.apply_language` 内部会调 `save_language(lang)` **真实写盘**，
        而本文件早前的 `test_apply_language_*` 用例直接调用它、未隔离 `_state_path`
        → 每次跑本文件都会把 `cache/i18n_state.json` 改成 `en-US` 并留在磁盘上。
        后果：**跑完测试套件后本机默认语言变成英文**，任何依赖「中文默认」的断言
        （如 `tests/test_i18n_audit137.py::test_t_dict_node_returns_key`）在后续运行
        中必失败——即经验 122 的「环境相关断言」在测试层面的同类地雷。

    修法：autouse 级隔离，覆盖本文件所有用例（含未来新增），
          既不改 `_STATE_FILE` 语义也不依赖各用例自觉。
    """
    fake_cache = tmp_path / "cache"
    fake_cache.mkdir(exist_ok=True)
    monkeypatch.setattr("utils.i18n._STATE_FILE", str(fake_cache / "i18n_state.json"))
    monkeypatch.setattr("utils.i18n._LANG", None)
    yield
    # 恢复进程内语言状态，避免污染同进程后续测试文件（xdist 关闭时同进程复用）。
    monkeypatch.setattr("utils.i18n._LANG", None)


# ------------------------------------------------------------------------------
# 第 1 组：utils.i18n 基础
# ------------------------------------------------------------------------------
def test_default_lang_is_zh():
    assert DEFAULT_LANG == "zh-CN"


def test_available_languages():
    langs = available_languages()
    assert "zh-CN" in langs
    assert "en-US" in langs


def test_t_zh_default():
    set_language("zh-CN")
    assert "单图处理" in t("app.tab.single")


def test_t_en():
    set_language("en-US")
    assert "Single Image" in t("app.tab.single")


def test_t_missing_key_falls_back_to_zh_then_key():
    # 不存在的键：先回退 zh-CN（仍不存在），最终回退为键本身。
    set_language("en-US")
    missing = "this.key.does.not.exist"
    assert t(missing) == missing


def test_t_with_placeholder():
    set_language("zh-CN")
    out = t("models.set_active_done", name="lama")
    assert "lama" in out
    assert "已" in out  # 中文文案


def test_set_language_ignores_invalid():
    set_language("zh-CN")
    set_language("fr-FR")  # 非法，忽略
    assert get_language() == "zh-CN"


def test_get_language_initializes():
    # 复位后 get_language 应触发 load（环境变量/持久化/默认）。
    set_language("en-US")
    assert get_language() in available_languages()


# ------------------------------------------------------------------------------
# 第 2 组：持久化（save / load）
# ------------------------------------------------------------------------------
def test_save_and_load_language(tmp_path, monkeypatch):
    # 强制走「持久化文件」分支：清掉环境变量。
    monkeypatch.delenv("WME_UI_LANGUAGE", raising=False)
    # 把缓存目录指到一个临时目录，避免污染真实 cache。
    fake_cache = tmp_path / "cache"
    fake_cache.mkdir(exist_ok=True)  # autouse fixture 可能已建同名目录
    monkeypatch.setattr("utils.i18n._STATE_FILE", None)
    monkeypatch.setattr("utils.i18n._LANG", None)
    monkeypatch.setattr(
        "utils.i18n._state_path",
        lambda: str(fake_cache / "i18n_state.json"),
    )

    save_language("en-US")
    state = fake_cache / "i18n_state.json"
    assert state.exists()
    # load 应读到持久化值。
    loaded = load_language()
    assert loaded == "en-US"
    assert get_language() == "en-US"


def test_load_language_env_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("WME_UI_LANGUAGE", "en-US")
    monkeypatch.setattr("utils.i18n._LANG", None)
    assert load_language() == "en-US"


# ------------------------------------------------------------------------------
# 第 3 组：ui.i18n_rt 实时切换核心
# ------------------------------------------------------------------------------
def setup_function(fn):
    clear_registry()


def teardown_function(fn):
    clear_registry()


def _fake_component():
    """一个极简的假组件，足以让 register 接受（非 None 即可）。"""
    return object()


def test_register_and_components_length():
    comp = _fake_component()
    register(comp, lambda: {"label": "x"})
    assert registered_components() == [comp]
    clear_registry()


def test_apply_language_returns_matching_updates():
    comp = _fake_component()
    register(comp, lambda: {"label": t("lang.label")})
    before = len(registered_components())
    updates = apply_language("en-US")
    assert len(updates) == before == 1


def test_apply_language_reevaluates_t_on_switch():
    """注册时用 lambda 包 t()，切换语言后解析应反映新语言。"""
    set_language("zh-CN")
    comp = _fake_component()
    register(comp, lambda: {"label": t("app.tab.single")})

    apply_language("en-US")
    # 通过 apply_language 的返回值拿到 gr.update 的 kwargs。
    updates = apply_language("en-US")
    kw = updates[0]
    # gr.update 的 label 应为英文。
    assert kw.get("label") == "Single Image (Manual)"


def test_reg_value_helper():
    clear_registry()
    comp = _fake_component()
    reg_value(comp, "app.tab.single")
    updates = apply_language("zh-CN")
    assert "单图处理" in updates[0]["value"]


def test_reg_label_helper():
    clear_registry()
    comp = _fake_component()
    reg_label(comp, "lang.label")
    updates = apply_language("en-US")
    assert updates[0]["label"] == "UI Language"


def test_reg_info_helper():
    clear_registry()
    comp = _fake_component()
    reg_info(comp, "lang.info")
    updates = apply_language("zh-CN")
    assert "重启" in updates[0]["info"]


def test_reg_label_info_helper():
    clear_registry()
    comp = _fake_component()
    reg_label_info(comp, "params.sensitivity_label", "params.sensitivity_info")
    updates = apply_language("en-US")
    assert updates[0]["label"] == "Detect Sensitivity"
    assert "fainter" in updates[0]["info"]


def test_reg_textbox_helper():
    clear_registry()
    comp = _fake_component()
    reg_textbox(comp, "media.pdf.pages_label", placeholder_key="media.pdf.pages_placeholder")
    updates = apply_language("zh-CN")
    assert "页码范围" in updates[0]["label"]
    assert "示例" in updates[0]["placeholder"]


def test_apply_language_resilient_to_bad_resolver():
    """某个 resolver 抛异常时，整体不崩，回退为 gr.update()。"""
    clear_registry()

    def _boom():
        raise RuntimeError("boom")

    comp_ok = _fake_component()
    comp_bad = _fake_component()
    register(comp_ok, lambda: {"label": "ok"})
    register(comp_bad, _boom)
    updates = apply_language("zh-CN")
    assert len(updates) == 2
    # 第二个回退为不含 label 的空 update。
    assert "label" not in updates[1] or updates[1].get("label") is None


def test_current_language_reflects_switch():
    clear_registry()
    apply_language("en-US")
    assert current_language() == "en-US"
