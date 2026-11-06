# -*- coding: utf-8 -*-
"""阶段 385：角标 LOGO 真实图「擦除效果」探针的守卫。

探针回答的是用户唯一会问的问题：**真实角标图跑完检测+修复，角标真的擦掉了吗**。
守卫保证：
  · 纯函数（`_ncc`、`_changed_ratio`）口径正确、可复算；
  · "擦净"的判据**跟着 config 走**（不与路由阈值漂移）；
  · 基线 JSON 存在且含关键指标（资产缺失时跳过，不算通过）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "scripts" / "corner_removal_probe.py"
BASELINE = ROOT / "benchmark_real" / "corner_removal_baseline.json"

_spec = None
mod = None


def _load_probe():
    import importlib.util

    spec = importlib.util.spec_from_file_location("corner_removal_probe385", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_module_loads():
    global _spec, mod
    mod = _load_probe()
    assert hasattr(mod, "run") and hasattr(mod, "main")


def test_ncc_is_scale_and_shift_invariant():
    m = _load_probe()
    a = np.arange(48 * 64, dtype=np.float32).reshape(48, 64)
    assert m._ncc(a, a) == pytest.approx(1.0, abs=1e-6)
    assert m._ncc(a, a * 3 + 7) == pytest.approx(1.0, abs=1e-6)
    assert m._ncc(a, -a) == pytest.approx(-1.0, abs=1e-6)
    assert m._ncc(np.zeros((4, 4), np.float32), np.zeros((4, 4), np.float32)) == 0.0


def test_changed_ratio_splits_inside_and_outside_bbox():
    m = _load_probe()
    before = np.zeros((10, 10, 3), np.uint8)
    after = before.copy()
    after[0:2, 0:2] = 50        # 框内 4 px
    after[8:9, 8:9] = 50        # 框外 1 px
    bbox = (0, 0, 5, 5)
    out_ratio, in_ratio = m._changed_ratio(before, after, bbox)
    assert in_ratio == pytest.approx(4 / 25, abs=1e-6)
    assert out_ratio == pytest.approx(1 / 75, abs=1e-6)


def test_erased_threshold_follows_config():
    import config

    m = _load_probe()
    assert m._ncc_erased_threshold() == pytest.approx(
        float(getattr(config, "RECOMMEND_CORNER_WATERMARK_NCC_MIN", 0.30)), abs=1e-6
    )


def test_baseline_records_the_key_metrics():
    if not BASELINE.exists():
        pytest.skip("缺基线 JSON（需先跑 scripts/corner_removal_probe.py）")
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    for key in ("n_images", "detected_rate", "erased_rate_given_detected",
                "median_ncc_before", "median_ncc_after",
                "median_outside_change_ratio", "rows"):
        assert key in data, f"基线缺字段：{key}"
    assert data["n_images"] > 0, "基线一张图都没量到"
    assert data["median_ncc_before"] > data["median_ncc_after"], (
        "修复前的角标可见度必须高于修复后，否则口径反了"
    )
