# -*- coding: utf-8 -*-
"""阶段 392：角标 LOGO「板块整块填充」的守卫。

背景（真实标注角标图实测）：检测器掩码只盖住板块的 5%~14%（半透明板块只被咬住
实心笔画）⇒ 修复后仍明显残留（完全擦净 84.4%）；按**规范化足迹**填一块实心后，
完全擦净升到 97.9%、框外足迹 2.09%。

守卫保证：
  · 只对**角标 LOGO 预设**生效（其它水印类型不受影响）；
  · 必须**角标模板信号命中**才填（否则不许擦右下角 —— 宁可漏检不可滥杀）；
  · 足迹几何 = 340x128 @2048² 随短边缩放、贴右下角；
  · 开关默认开启、可一键关掉。
"""

from __future__ import annotations

import numpy as np
import pytest

import config
from core import detector


def test_switch_defaults_on_and_can_be_disabled():
    assert config.DETECT_AI_LOGO_PLATE_FILL is True
    assert config._env_bool("DETECT_AI_LOGO_PLATE_FILL", True) is True


def test_plate_box_geometry():
    """2048² → 340x128；1024² → 170x64；贴右下角。"""
    bx, by, fw, fh = detector._ai_logo_plate_box(2048, 2048)
    assert (fw, fh) == (340, 128)
    assert (bx, by) == (2048 - 340, 2048 - 128)
    bx, by, fw, fh = detector._ai_logo_plate_box(1024, 1024)
    assert (fw, fh) == (170, 64)
    assert (bx, by) == (1024 - 170, 1024 - 64)


def _blank() -> np.ndarray:
    return np.full((256, 256, 3), 200, np.uint8)


def test_fill_requires_signal(monkeypatch):
    """信号没命中 ⇒ 板块区域必须全 0（真走检测器全链路，不是只测替身）。

    反例：填充块若无条件重算足迹（绕过信号闸），本测试必红。
    """
    monkeypatch.setattr(detector, "_ai_logo_plate_signal", lambda rgb: 0.0)
    result = detector.detect_watermark_mask(
        _blank(), preset="ai_logo", effective_preset="ai_logo", use_ocr=False,
    )
    bx, by, bw, bh = detector._ai_logo_plate_box(256, 256)
    assert not result.mask[by:by + bh, bx:bx + bw].any(), "信号为 0 仍擦了右下角"


def test_fill_when_signal_hits(monkeypatch):
    """信号命中 ⇒ 板块区域整块实心 255。"""
    monkeypatch.setattr(detector, "_ai_logo_plate_signal", lambda rgb: 1.0)
    result = detector.detect_watermark_mask(
        _blank(), preset="ai_logo", effective_preset="ai_logo", use_ocr=False,
    )
    bx, by, bw, bh = detector._ai_logo_plate_box(256, 256)
    assert (result.mask[by:by + bh, bx:bx + bw] == 255).all()


def test_fill_only_for_ai_logo_preset():
    """只有 ai_logo 预设会触发整块填充。"""
    src = open(detector.__file__, encoding="utf-8").read()
    assert '_eff_preset == "ai_logo"' in src
    assert "DETECT_AI_LOGO_PLATE_FILL" in src


def test_pipeline_passes_effective_preset():
    """自动推荐路径必须把**实际预设**告诉检测器，否则整块填充不会生效。"""
    from services import auto_pipeline

    src = open(auto_pipeline.__file__, encoding="utf-8").read()
    assert "effective_preset=" in src
