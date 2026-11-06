# -*- coding: utf-8 -*-
"""阶段 388：ai_logo 预设的**专属**掩码膨胀下限。

为什么有下限：阶段 385 在真实标注角标图上量到，角标是半透明板块，检测器多只咬住
实心笔画 ⇒ 修复后仍有残留（路由到 ai_logo 的图里只有约一半被完全擦净）；
把膨胀调到 12~24 可把完全擦净率从 51% 提到 70%~75%。

为什么**不是全局默认**：膨胀按类标定（阶段 105 校准：网格类用 0 更好，膨胀会溢出
水印区、撞上"只动水印区"的误伤红线）；实测全局默认调到 12 会让 5 条标定守卫变红。
⇒ 只能做成**按预设的下限**，且必须能一键关掉（设 0 即回到旧行为）。
"""

from __future__ import annotations

import config
from services.auto_pipeline import _apply_preset_dilate_floor


def test_floor_applies_only_to_ai_logo():
    floor = config.MASK_DILATE_MIN_AI_LOGO
    assert _apply_preset_dilate_floor("ai_logo", 0) == floor
    assert _apply_preset_dilate_floor("ai_logo", 5) == floor
    assert _apply_preset_dilate_floor("ai_logo", 40) == 40, "用户显式调大时不得被压回去"
    for other in ("ai_grid", "ai_text_heavy", "ai_faint_tiled", "ai_gradient", "ai_multi", None):
        assert _apply_preset_dilate_floor(other, 0) == 0, f"{other} 不该被加下限"


def test_floor_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "MASK_DILATE_MIN_AI_LOGO", 0)
    assert _apply_preset_dilate_floor("ai_logo", 0) == 0


def test_config_default_and_env_switch():
    # 默认值由真实图扫参决定（阶段 390：12/24/36 px ⇒ 擦净 80.0%/84.4%/88.9%）
    assert config.MASK_DILATE_MIN_AI_LOGO == 24
    assert config._env_int("MASK_DILATE_MIN_AI_LOGO", 24, 0, 100) == 24


def test_global_default_is_untouched():
    """全局默认必须仍是 0（按类标定的结论：网格类用 0 更好）。"""
    assert config.MASK_DILATE_DEFAULT == 0
