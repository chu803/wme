# -*- coding: utf-8 -*-
"""阶段 400：探针的**评价指标**必须保守——处理出错的样本不能自己藏起来。

旧口径：检出率/擦净率只对"跑成功"的样本取平均（分母 = ok）⇒ 失败/异常的图片
被踢出分母，反而让指标变好看（例如"检出率 94.2%"其实只统计了跑通的那批）。

守卫：
  · 保守口径的两个率必须存在（分母含出错样本，出错记为未检出/未擦净）；
  · 判据函数在"有出错样本"时必须**报出问题**（不许把失败藏起来）；
  · 判据函数优先使用保守口径（旧 summary 缺字段时回退旧键，保证向后兼容）。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROBE = ROOT / "scripts" / "corner_removal_probe.py"


def _load_probe():
    import importlib.util

    spec = importlib.util.spec_from_file_location("corner_removal_probe400", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_exposes_conservative_rates():
    """summary 必须同时给出保守口径（分母含出错样本）。"""
    import inspect

    src = PROBE.read_text(encoding="utf-8")
    assert "detected_rate_all" in src, "缺少保守口径的检出率"
    assert "erased_rate_given_detected_all" in src, "缺少保守口径的擦净率"
    assert inspect.isfunction(_load_probe().judge)


def test_judge_flags_error_samples():
    """有样本出错 ⇒ 判据必须报出来（且用保守口径的率）。"""
    m = _load_probe()
    summary = {
        "n_images": 4, "n_errors": 1,
        "detected_rate_all": 0.5, "erased_rate_given_detected_all": 0.5,
        "detected_rate": 1.0, "erased_rate_given_detected": 1.0,
        "median_outside_change_ratio": 0.001,
    }
    problems = m.judge(summary)
    assert problems, "出错样本必须被判出来"
    assert any("出错" in p for p in problems), f"没有点明出错：{problems}"
    assert any("检出率" in p for p in problems), f"保守口径的检出率没被用上：{problems}"


def test_judge_backward_compatible_with_old_summary():
    """旧 summary（没有保守口径字段）仍能判，且仍能踩红。"""
    m = _load_probe()
    old = {
        "n_images": 10, "n_errors": 0,
        "detected_rate": 0.2, "erased_rate_given_detected": 0.2,
        "median_outside_change_ratio": 0.001,
    }
    problems = m.judge(old)
    assert any("检出率" in p for p in problems)


def test_judge_passes_clean_summary():
    m = _load_probe()
    good = {
        "n_images": 10, "n_errors": 0,
        "detected_rate_all": 0.95, "erased_rate_given_detected_all": 0.99,
        "detected_rate": 0.95, "erased_rate_given_detected": 0.99,
        "median_outside_change_ratio": 0.005,
    }
    assert m.judge(good) == []
