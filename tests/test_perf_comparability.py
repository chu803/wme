#!/usr/bin/env python3
# ==============================================================================
#  阶段 215 · 可比性维度补全的守卫（engine / runs）
# ------------------------------------------------------------------------------
#  发现的缺陷
#  ----------
#  Stage 211 把 `device`/`gpu_name` 纳入可比性维度并递增了版本。但本次逐字段
#  审计 `meta` 的全部键 vs `COMPARABILITY_KEYS`，发现**还有两个影响 P95 的维度
#  被漏掉**，且它们会产生**假红**（把「换了口径」误报成「性能回归」）：
#
#   ① **engine**（修复引擎）：`stage=full` 的 meta 本就带 `engine` 字段，
#      但它**不在**比对维度里 ⇒ 「opencv 的测量」会与「lama 的基线」直接比护栏。
#      实测两者耗时差一个数量级（lama 约 13s vs opencv 约 1~2s）⇒ 必然假红。
#   ② **runs**（实际计时张数）：P95 是**分位数**，样本量不同 ⇒ 分布形状不同。
#      实测「10 张基线 vs 3 张测量」被判可比；3 张的 P95 必然是最大值，不是真分位。
#
#  本文件钉死
#  ----------
#  · 两个维度必须**在** `COMPARABILITY_KEYS` 里；
#  · 版本号必须 ≥ 3（1→2 是 211 的 device/gpu_name，2→3 是本次的 engine/runs）；
#  · detect 口径必须**显式写** `engine=None`（而非让该键缺席→ 会被误报成「基线过期」）；
#  · 落盘的两个基线文件都必须含全部维度。
# ==============================================================================
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import perf_probe as pp  # noqa: E402

DETECT_BASE = os.path.join(_ROOT, "benchmark_real", "perf_baseline.json")
FULL_BASE = os.path.join(_ROOT, "benchmark_real", "perf_baseline_gpu_default.json")


# ==============================================================================
# ① 维度必须在
# ==============================================================================
@pytest.mark.parametrize("key", ["source", "stage", "repeat", "device",
                                 "gpu_name", "engine", "runs"])
def test_comparability_key_present(key):
    assert key in pp.COMPARABILITY_KEYS, (
        f"可比性维度缺 {key}—— 会让「换了{key}」的测量与旧基线直接比护栏，"
        f"产生假红（把口径差异误报成性能回归）")


def test_version_bumped_to_at_least_three():
    """版本必须 ≥ 3：1→2 是 211 的 device/gpu_name，2→3 是本阶段的 engine/runs。"""
    assert pp.BASELINE_VERSION >= 3, (
        f"BASELINE_VERSION={pp.BASELINE_VERSION}：改动 COMPARABILITY_KEYS "
        f"必须递增版本，否则旧基线静默退化为永久跳过（LESSONS 230）")


# ==============================================================================
# ② 缺字段 vs 值不同，要区分（沿用 211 的诊断分层）
# ==============================================================================
def test_missing_engine_is_stale_not_incomparable():
    """缺 `engine` ⇒ 「结构过旧（需重新标定）」，**不是**「engine 不一致」。

    两者处置相反：前者跑 `--update` 即可，后者（跨引擎）永远不该拿来比。
    """
    base = {"version": pp.BASELINE_VERSION, "p95": 13.0, "source": "benchmark",
            "stage": "full", "repeat": 3, "device": "cuda",
            "gpu_name": "X", "runs": 10}
    meta = {"source": "benchmark", "stage": "full", "repeat": 3,
            "device": "cuda", "gpu_name": "X", "runs": 10, "engine": "lama"}
    ok, why = pp.baseline_comparable(base, meta)
    assert not ok
    assert "结构过旧" in why and "重新标定" in why, f"实际：{why}"


def test_different_engine_is_not_comparable():
    """跨引擎必须判不可比，且**不得**建议重新标定（那会诱导错误刷新基线）。"""
    base = {"version": pp.BASELINE_VERSION, "p95": 13.0, "source": "benchmark",
            "stage": "full", "repeat": 3, "device": "cuda",
            "gpu_name": "X", "runs": 10, "engine": "lama"}
    meta = {"source": "benchmark", "stage": "full", "repeat": 3,
            "device": "cuda", "gpu_name": "X", "runs": 10, "engine": "opencv"}
    ok, why = pp.baseline_comparable(base, meta)
    assert not ok and "engine" in why, f"跨引擎必须不可比，实际 {ok}：{why}"
    assert "重新标定" not in why, (
        "跨引擎是**设计意图**（不该拿来比），不该建议重新标定")


def test_different_runs_is_not_comparable():
    """样本量不同 ⇒ P95 分位数形状不同 ⇒ 必须判不可比。"""
    base = {"version": pp.BASELINE_VERSION, "p95": 13.0, "source": "benchmark",
            "stage": "full", "repeat": 3, "device": "cuda",
            "gpu_name": "X", "runs": 10, "engine": "lama"}
    for few in (3, 5, 20):
        meta = {"source": "benchmark", "stage": "full", "repeat": 3,
                "device": "cuda", "gpu_name": "X", "runs": few, "engine": "lama"}
        ok, why = pp.baseline_comparable(base, meta)
        assert not ok and "runs" in why, f"runs={few} 应判不可比，实际 {ok}：{why}"


def test_detect_stage_writes_engine_none_explicitly():
    """detect 口径必须**显式**写 `engine=None`，不能靠「键缺席」。

    缺席会走「基线缺字段 ⇒ 结构过旧」的诊断，把「本维度对此口径不存在」
    误报成「基线已过期」⇒ 每次跑都判跳过。
    """
    import inspect
    src = inspect.getsource(pp.measure)
    det = src.split("---- 默认：仅检测阶段")[-1]
    assert '"engine": None' in det or "'engine': None" in det, (
        "detect 分支必须显式写 engine=None")
    # 两侧都是 None ⇒ 应判可比
    base = {"version": pp.BASELINE_VERSION, "p95": 0.68, "source": "benchmark",
            "stage": "detect", "repeat": 3, "device": "cuda",
            "gpu_name": "X", "runs": 10, "engine": None}
    meta = {"source": "benchmark", "stage": "detect", "repeat": 3,
            "device": "cuda", "gpu_name": "X", "runs": 10, "engine": None}
    ok, why = pp.baseline_comparable(base, meta)
    assert ok, f"detect 口径 engine 两侧均 None 应判可比，实际：{why}"


# ==============================================================================
# ③ 落盘基线完整性
# ==============================================================================
@pytest.mark.parametrize("path,label", [(DETECT_BASE, "detect"),
                                        (FULL_BASE, "full")])
def test_on_disk_baseline_has_every_dimension(path, label):
    """落盘基线必须含**每个**可比性维度（缺一个就永远判跳过）。"""
    if not os.path.isfile(path):
        pytest.skip(f"{label} 基线尚未标定")
    with open(path, encoding="utf-8") as f:
        b = json.load(f)
    missing = [k for k in pp.COMPARABILITY_KEYS if k not in b]
    assert not missing, (
        f"{label} 基线缺维度 {missing} ⇒ 每次运行都会判「不可比」而跳过，"
        f"护栏零约束。请重新标定：python scripts/perf_probe.py --update")
    assert b.get("version") == pp.BASELINE_VERSION, (
        f"{label} 基线版本 {b.get('version')} != {pp.BASELINE_VERSION}")


def test_two_baselines_are_independent_files():
    """两个口径必须**各自独立**基线文件（stage 是可比性维度，共用会互相判不可比）。"""
    assert os.path.abspath(DETECT_BASE) != os.path.abspath(FULL_BASE)
