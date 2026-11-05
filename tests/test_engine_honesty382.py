# -*- coding: utf-8 -*-
"""阶段 382：引擎「承诺与实际一致」的守卫。

背景：界面写的是「auto=有模型用 LaMa，否则降级 OpenCV」，而代码里
`config.INPAINT_AUTO_PREFER` 默认是 **opencv** ⇒ auto 实际**总是**走 OpenCV
（阶段 55 依实测定下的取舍：更快、更保守）。于是：
  ① 界面说明与实际不符；
  ② 完成状态只显示用户选的标签（"AUTO"），用户看不出实际用了哪个引擎。

本守卫钉住：解析函数与修复路径同源、界面展示实际引擎、说明文案不再承诺「有模型用 LaMa」。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import config
import core.inpainter as inpainter_mod
import ui.engine_display as ed
from utils.i18n import t, t_in

ROOT = Path(__file__).resolve().parent.parent


def test_auto_resolves_by_configured_preference(monkeypatch):
    """auto 的实际引擎由 INPAINT_AUTO_PREFER 决定（默认 opencv，不是"有模型就 LaMa"）。"""
    monkeypatch.setattr(config, "INPAINT_AUTO_PREFER", "opencv")
    monkeypatch.setattr(inpainter_mod, "is_model_available", lambda: True)
    assert inpainter_mod.resolve_effective_engine("auto") == "opencv"

    monkeypatch.setattr(config, "INPAINT_AUTO_PREFER", "lama")
    monkeypatch.setattr(inpainter_mod, "is_model_available", lambda: True)
    assert inpainter_mod.resolve_effective_engine("auto") == "lama"

    monkeypatch.setattr(inpainter_mod, "is_model_available", lambda: False)
    assert inpainter_mod.resolve_effective_engine("auto") == "opencv"


def test_explicit_engine_is_returned_unchanged(monkeypatch):
    monkeypatch.setattr(inpainter_mod, "is_model_available", lambda: False)
    assert inpainter_mod.resolve_effective_engine("lama") == "lama"
    assert inpainter_mod.resolve_effective_engine("opencv") == "opencv"


def test_display_shows_effective_engine_and_reason(monkeypatch):
    """auto 落到 OpenCV 时：显示 OPENCV，并说明是"权重未就绪"还是"配置默认"。"""
    monkeypatch.setattr(config, "INPAINT_AUTO_PREFER", "opencv")
    monkeypatch.setattr(inpainter_mod, "is_model_available", lambda: False)
    monkeypatch.setattr(ed, "is_model_available", lambda: False)
    label, note = ed.engine_display("auto")
    assert label == "OPENCV"
    assert "尚未就绪" in note

    monkeypatch.setattr(inpainter_mod, "is_model_available", lambda: True)
    monkeypatch.setattr(ed, "is_model_available", lambda: True)
    label, note = ed.engine_display("auto")
    assert label == "OPENCV"
    assert "默认" in note


def test_display_has_no_note_for_explicit_engine(monkeypatch):
    monkeypatch.setattr(ed, "is_model_available", lambda: False)
    label, note = ed.engine_display("lama")
    assert label == "LAMA"
    assert note == ""


def test_single_status_reports_effective_engine(monkeypatch):
    """单图页完成文案必须显示实际引擎，而不是用户选的 "AUTO"。"""
    from ui.tab_single import _compose_status

    monkeypatch.setattr(config, "INPAINT_AUTO_PREFER", "opencv")
    monkeypatch.setattr(inpainter_mod, "is_model_available", lambda: False)
    monkeypatch.setattr(ed, "is_model_available", lambda: False)

    text = _compose_status("auto", r"D:\out\x.png", 1.0)
    assert "OPENCV" in text
    assert "AUTO" not in text
    assert "尚未就绪" in text


def test_engine_help_text_matches_actual_behaviour():
    """界面不得再承诺「auto=有模型用 LaMa」——那与代码里的默认偏好相反。"""
    zh_info = t_in("zh-CN", "params.engine_info")
    en_info = t_in("en-US", "params.engine_info")
    assert zh_info and en_info, "engine_info 文案取不到，断言会空过"
    assert "有模型用 LaMa" not in zh_info
    assert "LaMa if model present" not in en_info
    assert "OpenCV" in zh_info and "OpenCV" in en_info
    assert "默认" in zh_info
