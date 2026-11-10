# -*- coding: utf-8 -*-
"""阶段 394：默认「通用检测」收窄到承诺类（角标 + 半透明），边缘小字降为需显式选用。

起因（豆包挑战集 + 干净图基线实测）：
- 默认路径 `general` = 半透明 + 角落 + **边缘小字**（三者默认全开）；
- 「边缘小字」策略在**规则网格**（玻璃幕墙窗格、百叶、砖缝、围栏）上会误命中
  ⇒ 一张**零水印**的干净照片被当成"边缘小字"修掉，实测改动 5.15%（可见涂抹）；
- 边缘小字本来就不在承诺面（UI 明说"不承诺"）⇒ 让默认口径对齐承诺面，
  能力不删（`ai_text_heavy` 预设仍可显式选用）。

守卫：
  · general 明确关掉边缘小字，且仍保留半透明 + 角落（承诺面口径）；
  · 边缘小字能力仍在（显式预设可开）；
  · 状态文案要报出"实际改动 x% 像素"（用户能判断代价）。
"""

from __future__ import annotations

import numpy as np

from core.detection_presets import resolve_preset


def test_general_preset_disables_edge_text():
    g = resolve_preset("general", 0.5)
    assert g.get("enable_edge") is False, "通用检测不应再默认开边缘小字（规则网格误伤）"


def test_general_preset_keeps_promised_strategies():
    g = resolve_preset("general", 0.5)
    assert g.get("enable_translucent") in (None, True), "半透明要保留"
    assert g.get("enable_corner") in (None, True), "角落 LOGO（承诺类）要保留"
    assert g.get("enable_grid") in (None, False), "网格点阵不在承诺面"
    assert g.get("enable_gradient") in (None, False), "全局渐变不在承诺面"


def test_edge_text_capability_still_selectable():
    """能力不删：显式选 ai_text_heavy 仍会开边缘小字。"""
    g = resolve_preset("ai_text_heavy", 0.5)
    assert g.get("enable_edge") is True, "边缘小字必须仍可显式选用"


def test_actual_change_ratio_reports_pixel_diff():
    """阶段 405：改动比例改为产物与原图逐像素测量（掩码面积会低报保护带）。"""
    from ui.tab_auto import _actual_change_ratio

    a = np.zeros((10, 10, 3), dtype=np.uint8)
    b = a.copy()
    b[0, 0] = 100
    assert abs(_actual_change_ratio(a, b) - 0.01) < 1e-9
    assert _actual_change_ratio(a, a) == 0.0
    # 真·超分改变画布尺寸 ⇒ 无法逐像素比较，返回 None（调用方跳过该行）。
    assert _actual_change_ratio(a, np.zeros((5, 5, 3), dtype=np.uint8)) is None
