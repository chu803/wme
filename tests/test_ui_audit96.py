# -*- coding: utf-8 -*-
"""
阶段 96 审计 `ui/tab_auto` 的回归测试。

覆盖两个真缺陷：
  P1. `on_enhance` 此前无任何异常护栏，渲染失败会把 traceback 直接抛给 Gradio；
      阶段 96 补 try/except + format_exception_for_ui 降级为错误提示 HTML。
  P2. `build_app()` 此前只清空 i18n 注册表、不清空 `_SUPERRES_WIDGETS`，
      导致该构建期全局注册表在重复构建时线性累积（阶段 91 U3 同源）；
      阶段 96 在 build_app 开头补 clear_superres_widgets()。

命名约定：test_ui_audit96.* 是「真在测该模块」的审计测试，
应被 audit_selector 计入 `ui/tab_auto` 的引用（降低其覆盖度荒漠比值）。
"""
import numpy as np
import pytest

from utils.i18n import set_language, save_language
from ui.components import get_superres_widgets, clear_superres_widgets
from ui import tab_auto as ta


@pytest.fixture(autouse=True)
def _zh_lang():
    # 进程级全局语言状态（经验 65：自己建场景，避免被其它文件留在 en-US）。
    set_language("zh-CN")
    save_language("zh-CN")
    yield


# ---------------------------------------------------------------------------
# P1：on_enhance 异常护栏
# ---------------------------------------------------------------------------
def test_on_enhance_guards_render_error(monkeypatch):
    """渲染抛异常时必须降级为错误提示，而非把异常冒给 UI 框架。"""

    def _boom(*a, **k):
        raise RuntimeError("injected render failure")

    monkeypatch.setattr("services.compare_service.render_compare_html", _boom)
    arr = np.zeros((16, 16, 3), dtype=np.uint8)
    out = ta.on_enhance(arr, arr, "concat")  # 不应抛出
    assert isinstance(out, str) and out.strip()
    # 错误提示文案来自 i18n（本地化为中文），且包含转义后的异常信息。
    assert "对比渲染失败" in out
    assert "injected render failure" in out


def test_on_enhance_normal_html_passthrough(monkeypatch):
    """正常路径不应被护栏改变：渲染返回什么，on_enhance 就返回什么。"""
    sentinel = "<sentinel-html/>"
    monkeypatch.setattr(
        "services.compare_service.render_compare_html",
        lambda *a, **k: sentinel,
    )
    arr = np.zeros((16, 16, 3), dtype=np.uint8)
    out = ta.on_enhance(arr, arr, "concat")
    assert out == sentinel


def test_on_enhance_no_result_when_inputs_missing():
    """原图/结果为空仍走既有「无结果」分支（行为未被护栏改动）。"""
    arr = np.zeros((16, 16, 3), dtype=np.uint8)
    assert ta.on_enhance(None, arr, "concat") == ta.t("compare.no_result")
    assert ta.on_enhance(arr, None, "concat") == ta.t("compare.no_result")


# ---------------------------------------------------------------------------
# P2：build_app 清空真实超分注册表
# ---------------------------------------------------------------------------
def test_build_app_clears_superres_registry():
    """重复构建应用，注册表数量必须稳定（不累积）。"""
    import ui.app as app_mod

    clear_superres_widgets()
    app_mod.build_app()
    n1 = len(get_superres_widgets())
    app_mod.build_app()
    n2 = len(get_superres_widgets())
    # 每个功能页登记一组真实超分控件，数量 > 0 且两次构建一致。
    assert n1 > 0
    assert n2 == n1, f"真实超分注册表在重复构建时累积：{n1} -> {n2}"
