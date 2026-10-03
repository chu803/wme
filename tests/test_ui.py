# -*- coding: utf-8 -*-
"""UI 装配集成测试（阶段 6/11/12/13）。

覆盖：
- build_app() 不抛异常且装配出 >0 个组件块（四入口共用同一界面的回归护栏）。
- UI 引擎下拉 choices 与插件注册表动态清单一致（阶段 11）。
- UI 下拉与 API 能力接口使用同一份动态引擎清单（四入口一致性）。
"""

import api

from ui.app import build_app
from ui.components import get_engine_choices
from core import plugins as pg


def test_build_app_no_throw_and_has_blocks():
    """界面应成功装配出非空组件树。"""
    app = build_app()
    blocks = getattr(app, "blocks", None)
    assert blocks is not None
    assert len(blocks) > 0


def test_engine_choices_consistent_with_registry():
    """UI 引擎下拉应直接来自插件注册表动态清单。"""
    assert get_engine_choices() == pg.get_engine_choices()


def test_ui_and_api_share_engine_list():
    """UI 下拉与 API 能力接口应都包含同一组引擎（四入口一致性护栏）。"""
    ui_choices = get_engine_choices()
    api_engine_names = [e["name"] for e in api.get_capabilities()["engines"]]
    for needed in ("auto", "lama", "opencv", "demo_model"):
        assert needed in ui_choices, f"UI 缺少引擎 {needed}"
        assert needed in api_engine_names, f"API 缺少引擎 {needed}"


def test_models_tab_handlers(monkeypatch):
    """模型管理页新增的 on_reload / on_detail / _render_status_md（mock 掉 core，避免 torch）。"""
    from ui import tab_models as mt

    # 状态面板：渲染设备 / 路径 / 缓存数。
    monkeypatch.setattr(
        mt._mm,
        "get_model_info",
        lambda: {
            "device": "cpu",
            "device_name": "CPU",
            "device_is_gpu": False,
            "vram_mb": 0,
            "fp16": False,
            "cache_count": 0,
            "exists": True,
            "path": "/x/big-lama.pt",
            "size_mb": 200.0,
            "loaded": False,
            "active_model": "lama",
            "models": [],
        },
    )
    md = mt._render_status_md()
    # 语言无关断言：设备名 / 权重路径都会出现，标题含「状态」或「Status」。
    assert "CPU" in md and "/x/big-lama.pt" in md
    assert ("状态" in md) or ("Status" in md)

    # on_reload 成功路径：返回 (status_md, models_md, info) 三元组。
    monkeypatch.setattr(
        mt._mm,
        "reload_active",
        lambda: {"ok": True, "name": "lama", "loaded": True, "error": ""},
    )
    out = mt.on_reload()
    assert len(out) == 3 and "lama" in out[2]

    # on_detail 缺失文件：提示不存在。
    monkeypatch.setattr(
        mt._mm,
        "slot_detail",
        lambda name=None: {"name": "lama", "exists": False, "ok": False, "reason": "x"},
    )
    d = mt.on_detail("lama")
    # 语言无关断言：文件名固定出现，失败提示含「不存在」或「does not exist」。
    assert "lama" in d and ("不存在" in d or "does not exist" in d)
