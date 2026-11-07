# -*- coding: utf-8 -*-
"""阶段 394：干净图误伤红线门禁（`clean_fp_gate`）的守卫。

要点：
  · 门禁量的是"没有水印的图被改了多少画面"（局部涂抹），不是"整牌误发"；
  · 红线在 config 里（单张上限 + 中位上限）；
  · 已知缺口**显式列出、不静默丢弃**（沿用阶段 243 的做法）；
  · 门禁必须接进统一入口，否则等于永不生效。
"""

from __future__ import annotations

import os
import sys

import config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _probe():
    import clean_fp_probe

    return clean_fp_probe


def test_redlines_exist_and_are_sane():
    assert 0 < config.CLEAN_FP_MAX_CHANGE_RATIO <= 0.05, "单张上限要存在且量级合理"
    assert 0 < config.CLEAN_FP_MEDIAN_CHANGE_RATIO <= config.CLEAN_FP_MAX_CHANGE_RATIO


def test_known_gap_images_are_declared():
    """已知缺口必须**显式登记**（门禁报告要列出它，而不是静默丢掉）。

    阶段 397：玻璃幕墙那张（规则网格被半透明通路误伤）已修好（改动 0.0000%）⇒ 缺口注销，
    但**夹具必须留在干净图集里**当回归用例（否则下次同样的回归没人拦）。
    """
    for name in config.CLEAN_FP_KNOWN_GAP_IMAGES:
        path = os.path.join(_ROOT, "benchmark_real", "clean", f"{name}.jpg")
        assert os.path.exists(path), f"已知缺口 {name} 必须有真实夹具文件"
    facade = os.path.join(_ROOT, "benchmark_real", "clean", "clean_16_grid_facade.jpg")
    assert os.path.exists(facade), "规则网格回归夹具不能删（阶段 397 的守卫靠它）"
    assert "clean_16_grid_facade" not in config.CLEAN_FP_KNOWN_GAP_IMAGES, (
        "幕墙那张已修好（0.0000%）⇒ 不该再排除于红线")


def test_judge_flags_too_large_change():
    p = _probe()
    bad = {
        "n_images": 3, "n_errors": 0,
        "median_change_ratio": 0.002,
        "max_change_ratio": 0.051,
        "rows": [
            {"image_id": "a", "changed_ratio": 0.051, "known_gap": False},
            {"image_id": "b", "changed_ratio": 0.001, "known_gap": False},
        ],
    }
    problems = p.judge(bad)
    assert problems, "单张超上限必须判红"
    assert any("单张改动" in x for x in problems)


def test_judge_flags_high_median():
    p = _probe()
    bad = {
        "n_images": 3, "n_errors": 0,
        "median_change_ratio": 0.02,
        "max_change_ratio": 0.02,
        "rows": [{"image_id": "a", "changed_ratio": 0.02, "known_gap": False}],
    }
    assert any("中位改动" in x for x in p.judge(bad))


def test_judge_passes_small_changes():
    p = _probe()
    ok = {
        "n_images": 3, "n_errors": 0,
        "median_change_ratio": 0.001,
        "max_change_ratio": 0.004,
        "rows": [{"image_id": "a", "changed_ratio": 0.004, "known_gap": False}],
    }
    assert p.judge(ok) == []


def test_known_gap_rows_are_excluded_from_redlines():
    """已知缺口不进红线聚合，但**必须出现在报告里**。"""
    p = _probe()
    summary = {
        "n_images": 2, "n_errors": 0,
        "median_change_ratio": 0.001, "max_change_ratio": 0.002,
        "known_gap_rows": [{"image_id": "clean_16_grid_facade", "changed_ratio": 0.05}],
        "rows": [
            {"image_id": "clean_16_grid_facade", "changed_ratio": 0.05, "known_gap": True},
            {"image_id": "other", "changed_ratio": 0.002, "known_gap": False},
        ],
    }
    assert p.judge(summary) == [], "已知缺口不该踩红线"
    assert summary["known_gap_rows"], "但它必须被列出来（不静默丢弃）"


def test_gate_is_registered_in_unified_entry():
    from scripts import run_all_checks

    names = [getattr(s, "name", "") for s in run_all_checks.build_steps(gates=True)]
    assert "clean_fp_gate" in names, "门禁没接进统一入口（--gates），等于永不生效"
