"""2026-10-17 · 修复区颜色/亮度匹配守卫（config.INPAINT_COLOR_MATCH）。

背景：多平台调研发现「修复区与周围亮度/色差接缝」是共性痛点；IOPaint 用
`sd_match_histograms` 消它。本项目用单调的 mean/std 仿射实现（core.inpainter._match_region_color），
探针实测真实图色差最大 [1.0,23.2,22.8] → [0.8,0.4,0.6]。

本守卫锁定四件事（合成用例，无需 GPU/模型，确定性 seed）：
  ① 颜色匹配把修复区 mean 拉向周围环（offset 大幅收敛）；
  ② 只改掩码内——掩码外与输入逐字节相同（铁律）；
  ③ 鲁棒性：核心区 <50 像素原样返回、不崩；
  ④ config 开关存在且区间合法（可逆杠杆）。
注入反证：令 `_match_region_color` 直接 return result ⇒ ①失败（见提交说明）。
"""
import numpy as np
import pytest

import config
from core.inpainter import _match_region_color


def _synth(offset: int = 60):
    """原图 ~170 带噪声；修复区 = 原图对应区 + offset（模拟 LaMa 的亮度/色偏）。"""
    rng = np.random.default_rng(7)
    base = np.clip(170 + rng.normal(0, 10, (240, 240, 3)), 0, 255).astype(np.uint8)
    result = base.copy()
    mask = np.zeros((240, 240), np.uint8)
    mask[90:150, 90:150] = 255
    core = mask > 0
    result[core] = np.clip(result[core].astype(np.int16) + offset, 0, 255).astype(np.uint8)
    return base, result, mask


def test_color_match_pulls_fill_toward_surroundings():
    base, result, mask = _synth(offset=60)
    core = mask > 0
    k = 16
    import cv2
    ring = cv2.bitwise_and(
        cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))),
        cv2.bitwise_not(mask),
    ) > 0
    surround_mean = float(base[ring].mean())
    before_gap = abs(float(result[core].mean()) - surround_mean)
    out = _match_region_color(result, base, mask, config.INPAINT_COLOR_MATCH_RING)
    after_gap = abs(float(out[core].mean()) - surround_mean)
    assert after_gap < before_gap * 0.25, (
        f"颜色匹配应把修复区均值拉向周围：gap {before_gap:.1f} → {after_gap:.1f}"
        f"（surround={surround_mean:.1f}）"
    )


def test_color_match_only_touches_mask_interior():
    base, result, mask = _synth(offset=60)
    out = _match_region_color(result, base, mask, config.INPAINT_COLOR_MATCH_RING)
    outside = mask == 0
    assert np.array_equal(out[outside], result[outside]), (
        "掩码外被改动——违反「mask 外逐字节一致」铁律"
    )


def test_color_match_robust_on_tiny_core():
    """核心区 <50 像素应原样返回（不强配）。"""
    base = np.full((100, 100, 3), 180, np.uint8)
    result = base.copy()
    mask = np.zeros((100, 100), np.uint8)
    mask[10:12, 10:12] = 255  # 4 像素
    out = _match_region_color(result, base, mask, config.INPAINT_COLOR_MATCH_RING)
    assert np.array_equal(out, result)


def test_config_flags_present_and_reversible():
    assert isinstance(config.INPAINT_COLOR_MATCH, bool)
    assert 4 <= config.INPAINT_COLOR_MATCH_RING <= 64
    assert 0 <= config.INPAINT_COLOR_MATCH_MIN_OFFSET <= 60


def test_color_match_gated_by_min_offset():
    """阈值门控：偏移 < min_offset 的通道不校正；超过则校（温和版核心）。"""
    _, result_small, mask = _synth(offset=3)   # 偏移 3 < 默认阈值 6
    base_small, _, _ = _synth(offset=3)
    out = _match_region_color(result_small, base_small, mask,
                              config.INPAINT_COLOR_MATCH_RING,
                              config.INPAINT_COLOR_MATCH_MIN_OFFSET)
    assert np.array_equal(out, result_small), "偏移不足阈值时应原样返回（门控须生效）"
    base_big, result_big, mask_big = _synth(offset=60)
    out2 = _match_region_color(result_big, base_big, mask_big,
                               config.INPAINT_COLOR_MATCH_RING,
                               config.INPAINT_COLOR_MATCH_MIN_OFFSET)
    assert not np.array_equal(out2, result_big), "偏移超阈值应被校正"
