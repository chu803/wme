# -*- coding: utf-8 -*-
"""Stage 128 回归守卫：core/plugins 两处真缺陷（D1/D2）。

D1：docstring 称「内建身份由实例类型判定」，实现却用可伪造的 `is_builtin` 类属性
    ⇒ 插件自设 `is_builtin=True` 可在 `list_engines` 里伪装内建，并让 `reload_plugins`
    永不卸载它。修复：内建身份按具体类型（`_is_builtin_engine`）判定。
D2：`register_engine("auto")` 无保留名保护（auto 从不进 _REGISTRY，无同名冲突可拦）
    ⇒ 插件占用 auto 名会写进注册表，与 `list_engines` 硬编码的 auto 项脱节（隐藏引擎）。
    修复：`auto` 列为保留名，注册即拒。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
from core import plugins as pg
from core.plugins import InpaintEnginePlugin


class _Base(InpaintEnginePlugin):
    name = "base_audit128"
    display_name = "基类"

    def inpaint(self, image, mask, *, device=None, progress=None):  # pragma: no cover
        return image


@pytest.fixture
def iso():
    """隔离插件框架模块级状态，用例结束后还原。"""
    reg = dict(pg._REGISTRY)
    dirs = set(pg._SCANNED_DIRS)
    loaded = pg._LOADED
    rep = {k: (list(v) if isinstance(v, list) else v) for k, v in pg._LAST_REPORT.items()}
    yield pg
    pg._REGISTRY.clear()
    pg._REGISTRY.update(reg)
    pg._SCANNED_DIRS.clear()
    pg._SCANNED_DIRS.update(dirs)
    pg._LOADED = loaded
    for k, v in rep.items():
        pg._LAST_REPORT[k] = v


# ==============================================================================
# D2 · 保留名 auto
# ==============================================================================
def test_reserved_auto_name_rejected(iso):
    """插件不得占用保留名 auto；注册被拒，注册表不出现 auto。"""
    class _Auto(_Base):
        name = "auto"
        display_name = "偷名 auto"

    assert iso.register_engine(_Auto()) is False
    assert "auto" not in iso._REGISTRY
    assert iso.get_engine("auto") is None, "auto 不进注册表 ⇒ get_engine('auto') 为 None"
    infos = [e for e in iso.list_engines() if e.name == "auto"]
    assert len(infos) == 1 and infos[0].builtin is True, "清单里 auto 恰为 1 项内置选择器"


# ==============================================================================
# D1 · 内建身份按类型判定（抗 is_builtin 属性伪造）
# ==============================================================================
def test_is_builtin_attribute_cannot_masquerade(iso):
    """插件自设 is_builtin=True 不得在清单里伪装内建。"""
    class _Masq(_Base):
        name = "masq_audit128"
        display_name = "伪装内建"
        is_builtin = True  # 插件不应设置（基类 docstring 明示）

    try:
        assert iso.register_engine(_Masq()) is True
        info = next(e for e in iso.list_engines() if e.name == "masq_audit128")
        assert info.builtin is False, "插件不得靠 is_builtin 属性伪装为内建"
        assert iso._is_builtin_engine(_Masq()) is False
        assert iso._is_builtin_engine(iso.get_engine("lama")) is True
        assert iso._is_builtin_engine(iso.get_engine("opencv")) is True
    finally:
        iso._REGISTRY.pop("masq_audit128", None)


def test_masquerading_plugin_is_unloaded_by_reload(iso, tmp_path, monkeypatch):
    """伪装内建的插件必须能被 reload_plugins() 卸载（不再因 is_builtin 属性而存活）。"""
    class _Masq(_Base):
        name = "masq_reload128"
        display_name = "伪装内建"
        is_builtin = True

    assert iso.register_engine(_Masq()) is True
    assert "masq_reload128" in iso._REGISTRY

    monkeypatch.setattr(config, "PLUGIN_DIRS", [str(tmp_path)])  # 空目录：不加载真实插件
    iso.reload_plugins()
    assert "masq_reload128" not in iso._REGISTRY, "伪装内建的插件也应被 reload 卸载"
    assert iso.has_engine("lama") is True and iso.has_engine("opencv") is True, "内建须保留"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
