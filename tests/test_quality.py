# -*- coding: utf-8 -*-
"""阶段 56：无参考修复质量打分单测。

覆盖 `core.quality.no_reference_inpaint_score` 的基本性质：
  · 空掩码 → 0.0（不偏袒任一引擎）；
  · 完美修复（还原真值背景）分数高于劣化修复（保留水印 / 常量填充）；
  · 在「完美 → 轻微噪声 → 严重劣化」之间单调；
  · 与真实引擎修复结果集成不报错，且真修复优于「留着水印」。
"""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from core.quality import no_reference_inpaint_score  # noqa: E402


def _synthetic():
    """构造一张 64×64 均匀底色底图 + 一块矩形水印，返回 (clean, marked, mask)。

    用**均匀背景**（而非渐变）是为了让「完美修复」的局部统计与背景环严格可比，
    从而精确刻画打分函数的「无缝→高分」单调性（渐变背景会让环统计量受全局斜率影响，
    与局部掩码区不可比；真实渐变鲁棒性由 validate_selector 在 benchmark 上单独验证）。
    """
    h = w = 64
    v = 128
    clean = np.full((h, w, 3), v, np.uint8)  # 均匀底色
    marked = clean.copy()
    marked[20:44, 20:44] = 200  # 水印块（常量，与底色差异明显）
    mask = np.zeros((h, w), np.uint8)
    mask[20:44, 20:44] = 255
    return clean, marked, mask


def test_empty_mask_returns_zero():
    clean, marked, _ = _synthetic()
    empty = np.zeros((64, 64), np.uint8)
    assert no_reference_inpaint_score(clean, marked, empty) == 0.0


def test_perfect_repair_beats_watermark_left_in():
    clean, marked, mask = _synthetic()
    # 完美修复：掩码区还原成真值底色。
    good = clean.copy()
    # 劣化修复：掩码区仍留着水印（常量 200）。
    bad = marked.copy()
    s_good = no_reference_inpaint_score(good, marked, mask)
    s_bad = no_reference_inpaint_score(bad, marked, mask)
    assert s_good > s_bad


def test_monotonic_degradation():
    clean, marked, mask = _synthetic()
    # 三档修复：完美(还原底色128) / 中等(填160) / 严重(填0)，均用常量填充。
    # 均匀背景下指标退化为「均值匹配」，对填充值单调。
    good = clean.copy()                       # 块=128
    mid = clean.copy()
    mid[20:44, 20:44] = 160                   # 块=160（介于 128 与 0 之间）
    bad = clean.copy()
    bad[20:44, 20:44] = 0                     # 块=0（离底色最远）

    s_good = no_reference_inpaint_score(good, marked, mask)
    s_mid = no_reference_inpaint_score(mid, marked, mask)
    s_bad = no_reference_inpaint_score(bad, marked, mask)

    # 完美修复分数应接近其上界 0（与背景环差异≈0）。
    assert s_good >= -1e-6
    # 单调性：完美 ≥ 中等 ≥ 严重。
    assert s_good >= s_mid >= s_bad
    # 且严格区分两端。
    assert s_good > s_bad


def test_local_average_blend_beats_constant_fill():
    clean, marked, mask = _synthetic()
    # 用掩码环均值（=底色 128）填充 → 与完美修复等价，应优于常量 0 填充。
    blend = clean.copy()
    blend[20:44, 20:44] = 128
    bad = clean.copy()
    bad[20:44, 20:44] = 0
    assert no_reference_inpaint_score(blend, marked, mask) > \
        no_reference_inpaint_score(bad, marked, mask)


def test_integration_with_real_engine():
    """与真实引擎修复结果集成：真修复分数高于「留着水印」。"""
    from scripts import make_demo_assets as mda

    clean = mda.make_base(7)
    marked = mda.wm_corner_logo(clean)
    res = mda.detect_watermark_mask(marked, preset="ai_logo")
    mask = res.mask
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    assert (mask > 0).any(), "检测应命中角标水印"

    out = mda.inpaint(marked, mask, engine="opencv")
    s_repaired = no_reference_inpaint_score(out, marked, mask)
    s_left = no_reference_inpaint_score(marked, marked, mask)
    assert isinstance(s_repaired, float)
    assert s_repaired > s_left
