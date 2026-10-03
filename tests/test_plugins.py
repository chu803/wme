# -*- coding: utf-8 -*-
"""插件/自定义引擎注册表测试（阶段 11/13）。

覆盖：
- 动态引擎清单含 auto/lama/opencv 及示例插件 demo_model。
- 内置引擎恒可用、未知引擎返回 False。
- 注册临时引擎后可被查询，清理后不污染全局注册表。
- get_engine 返回的是 InpaintEnginePlugin 实例。
"""

from core import plugins as pg
from core.plugins import InpaintEnginePlugin, register_engine, _REGISTRY


def test_builtin_choices_present():
    """动态引擎清单应以 auto 开头，并含 lama/opencv/demo_model。"""
    choices = pg.get_engine_choices()
    assert choices[0] == "auto"
    assert "lama" in choices
    assert "opencv" in choices
    assert "demo_model" in choices


def test_has_engine_boundaries():
    """has_engine 对内置/未知引擎的判定。"""
    assert pg.has_engine("auto") is True
    assert pg.has_engine("lama") is True
    assert pg.has_engine("opencv") is True
    assert pg.has_engine("demo_model") is True
    # 未知引擎
    assert pg.has_engine("not_a_real_engine") is False


def test_register_cleanup_isolated():
    """注册临时引擎后可被查询，弹出后不影响真实注册表。"""
    class _Tmp(InpaintEnginePlugin):
        name = "tmp_test_engine"
        display_name = "临时测试引擎"

        def inpaint(self, image, mask, *, device=None, progress=None):
            return image

    register_engine(_Tmp())
    try:
        assert pg.has_engine("tmp_test_engine") is True
        eng = pg.get_engine("tmp_test_engine")
        assert isinstance(eng, InpaintEnginePlugin)
        # 详情列表里也应出现
        names = [e["name"] for e in pg.get_engine_detail()]
        assert "tmp_test_engine" in names
    finally:
        # 清理：从注册表弹出，不污染后续用例
        _REGISTRY.pop("tmp_test_engine", None)

    assert pg.has_engine("tmp_test_engine") is False


def test_get_engine_returns_plugin_instance():
    """get_engine('lama') 应返回已注册的插件实例。"""
    eng = pg.get_engine("lama")
    assert eng is not None
    assert isinstance(eng, InpaintEnginePlugin)
    assert eng.name == "lama"
