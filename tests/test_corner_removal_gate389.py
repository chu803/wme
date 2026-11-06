# -*- coding: utf-8 -*-
"""阶段 389：承诺类（角标 LOGO）真实图擦除效果门禁的守卫。

这道门禁把"承诺类不许悄悄退化"变成可拦截的红线：
检出率 / 完全擦净率 / 框外变化，三条都由 `config` 单点声明。
守卫保证：判定逻辑正确（好数据放行、坏数据各自被抓）、阈值在合理区间、
以及**门禁真的接进了统一入口**（否则又是一条永不生效的闸）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "corner_removal_probe389", ROOT / "scripts" / "corner_removal_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _good() -> dict:
    return {
        "detected_rate": 0.86,
        "erased_rate_given_detected": 0.80,
        "median_outside_change_ratio": 0.013,
    }


def test_good_summary_passes():
    assert _load_probe().judge(_good()) == []


def test_low_detection_is_flagged():
    bad = _good() | {"detected_rate": 0.5}
    problems = _load_probe().judge(bad)
    assert any("检出率" in p for p in problems), problems


def test_low_erasure_is_flagged():
    bad = _good() | {"erased_rate_given_detected": 0.4}
    problems = _load_probe().judge(bad)
    assert any("擦净率" in p for p in problems), problems


def test_excessive_collateral_is_flagged():
    bad = _good() | {"median_outside_change_ratio": 0.2}
    problems = _load_probe().judge(bad)
    assert any("框外变化" in p for p in problems), problems


def test_thresholds_are_sane_and_have_headroom():
    """阈值必须低于实测值（否则门禁天天红），也不能宽松到测不出退化。"""
    assert 0.60 <= config.CORNER_REMOVAL_MIN_ERASED_RATE <= 0.78
    assert 0.70 <= config.CORNER_REMOVAL_MIN_DETECTED_RATE <= 0.87
    assert 0.015 <= config.CORNER_REMOVAL_MAX_OUTSIDE_RATIO <= 0.05


def test_gate_is_registered_in_the_unified_entry():
    import sys

    spec = importlib.util.spec_from_file_location(
        "run_all_checks389", ROOT / "scripts" / "run_all_checks.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses 需要模块已登记在 sys.modules 里（否则解析注解时拿不到模块）
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    steps = module.build_steps(gates=True)
    names = [getattr(s, "name", None) for s in steps]
    assert "corner_removal_gate" in names, "门禁没接进统一入口，等于永不生效"
    step = next(s for s in steps if getattr(s, "name", None) == "corner_removal_gate")
    assert "--gate" in step.argv, "门禁步骤必须带 --gate"
    assert "auto" in step.argv, "门禁必须走推荐器（用户默认路径），不能只量强制预设"
