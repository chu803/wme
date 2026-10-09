#!/usr/bin/env python3
# ==============================================================================
#  阶段 211 · 性能门禁「永久跳过」缺陷的**防复发**守卫
# ------------------------------------------------------------------------------
#  缺陷事实（已修复）
#  ------------------
#  `scripts/perf_probe.py` 在 D1-Stage 6 把 `device`/`gpu_name` 加入可比性维度时，
#  **没有递增 `BASELINE_VERSION`**。于是仓内 2026-10-01 建的基线（v1、无硬件字段）
#  仍能通过版本检查，落到键值比较才被判「device 不一致（基线 None vs 本次 cuda）」
#  ⇒ **每次运行都退出码 2（跳过）**。
#
#  为什么这个缺陷特别危险
#  ----------------------
#  「跳过」的退出码 2 在门禁家族契约里表示「未标定 / 不可比」，看起来完全**良性**：
#  有输出、有解释、不报错。而回归护栏的真实作用（不许变慢）**一次都没生效过**——
#  典型的「守卫在、且每次都有输出，但从不判定」（LESSONS 183 有守卫≠有覆盖）。
#  更糟的是它能被**误读**：看到「device 不一致」会以为「本机是GPU、基线是 CPU，
#  硬件不同不便比」——于是一个纯代码缺陷被伪装成合理的环境差异，长期无人发现。
#
#  本文件守什么
#  ------------
#  ① **版本铁律**：可比性维度（COMPARABILITY_KEYS）变更时必须递增 BASELINE_VERSION。
#     由「版本号必须 ≥ 维度引入后的版本」这一可机读断言近似钉死（见下）。
#  ② **基线完整性**：仓内落盘的 `benchmark_real/perf_baseline.json` 必须含
#     COMPARABILITY_KEYS 的**每一个**字段。否则它就是一份「永远判跳过」的基线
#     ——门禁存在但零约束，且没人会发现。
#  ③ **诊断可区分**：「基线缺字段」（可修：重新标定）必须与「字段值不同」
#     （不可修：跨硬件）给出**不同**的提示，防止缺陷被伪装成环境差异。
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

BASELINE_PATH = os.path.join(_ROOT, "benchmark_real", "perf_baseline.json")


# ==============================================================================
# ① 版本铁律
# ==============================================================================
def test_baseline_version_is_at_least_two():
    """BASELINE_VERSION 必须 ≥ 2。

    v1 是「加入 device/gpu_name 却没递增」的历史值；一旦有人把可比性维度再加回去
    而沿用旧版本，本断言是唯一会响的闹钟。

    阶段 215 补入 `engine`/`runs` 时已递增到 3；该下限断言保持 ≥ 2 不变
    （它是「铁律存在性」的哨兵，具体下限由 test_perf_comparability.py 钉死 ≥ 3）。
    """
    assert pp.BASELINE_VERSION >= 2, (
        f"BASELINE_VERSION={pp.BASELINE_VERSION}：若改动了 "
        f"{pp.COMPARABILITY_KEYS}，必须递增版本，否则历史基线会静默退化为永久跳过"
    )


def test_comparability_keys_are_a_tuple_and_nonempty():
    """可比性维度必须是显式声明的元组（守卫要遍历它，故不能是散落的字面量）。"""
    assert isinstance(pp.COMPARABILITY_KEYS, tuple)
    assert pp.COMPARABILITY_KEYS, "可比性维度不得为空——否则任何基线都判可比"
    # 这三个是工作量口径，缺了会让「合成图基线」与「真实图基线」互相判可比
    for k in ("source", "stage", "repeat"):
        assert k in pp.COMPARABILITY_KEYS, f"工作量口径维度 {k} 缺失"


# ==============================================================================
# ② 落盘基线完整性（本缺陷的直接检测面）
# ==============================================================================
@pytest.mark.skipif(not os.path.isfile(BASELINE_PATH),
                    reason="性能基线尚未标定（首次需 --update 生成）")
def test_on_disk_baseline_contains_every_comparability_key():
    """仓内基线必须含每个可比性字段，否则它永远判跳过 → 护栏零约束。"""
    with open(BASELINE_PATH, encoding="utf-8") as f:
        baseline = json.load(f)
    missing = [k for k in pp.COMPARABILITY_KEYS if k not in baseline]
    assert not missing, (
        f"benchmark_real/perf_baseline.json 缺可比性字段 {missing} ⇒ "
        f"每次运行都会判「不可比」而跳过，性能回归护栏零约束。"
        f"请重新标定：python scripts/perf_probe.py --update"
    )
    assert baseline.get("version") == pp.BASELINE_VERSION, (
        f"基线版本 {baseline.get('version')} != 脚本版本 {pp.BASELINE_VERSION} ⇒ "
        f"请重新标定：python scripts/perf_probe.py --update"
    )


@pytest.mark.skipif(not os.path.isfile(BASELINE_PATH),
                    reason="性能基线尚未标定")
def test_on_disk_baseline_is_comparable_with_its_own_meta():
    """基线必须与「从自身元信息构造的 meta」可比（自洽性健全检查）。

    这条能在不跑推理的前提下发现「基线字段被手工改坏」这类问题。
    """
    with open(BASELINE_PATH, encoding="utf-8") as f:
        baseline = json.load(f)
    meta = {k: baseline.get(k) for k in pp.COMPARABILITY_KEYS}
    ok, why = pp.baseline_comparable(baseline, meta)
    assert ok, f"基线与自身元信息不可比（自洽性被破坏）：{why}"


# ==============================================================================
# ③ 诊断可区分：缺字段 ≠ 值不同
# ==============================================================================
def test_missing_field_and_mismatched_value_give_different_diagnosis():
    """「基线缺字段」必须给出**可修**提示，与「值不同」区分开。

    若两者提示相同，缺陷会被误读成「本机硬件不同，不便比对」而长期无人修复
    ——这正是本缺陷能存活到阶段 211 的原因。
    """
    # 值不同（跨硬件）：不可比，且**不该**建议重新标定
    base_ok = {"version": pp.BASELINE_VERSION, "p95": 1.0,
               "source": "benchmark", "stage": "detect", "repeat": 3,
               "device": "cpu", "gpu_name": None,
               "engine": None, "runs": 10}
    meta_cuda = {"source": "benchmark", "stage": "detect", "repeat": 3,
                 "device": "cuda", "gpu_name": "NVIDIA GeForce RTX 5060",
                 "engine": None, "runs": 10}
    ok, why = pp.baseline_comparable(base_ok, meta_cuda)
    assert not ok
    assert "device" in why
    assert "重新标定" not in why, (
        "跨硬件差异是设计意图，不该建议重新标定（否则会诱导用户用错误硬件刷新基线）")

    # 缺字段（结构过旧）：必须明确提示**可修**
    base_old = {"version": pp.BASELINE_VERSION, "p95": 1.0,
                "source": "benchmark", "stage": "detect", "repeat": 3}
    meta_same = {"source": "benchmark", "stage": "detect", "repeat": 3,
                 "device": "cuda", "gpu_name": "NVIDIA GeForce RTX 5060",
                 "engine": None, "runs": 10}
    ok2, why2 = pp.baseline_comparable(base_old, meta_same)
    assert not ok2
    assert "重新标定" in why2 and "--update" in why2, (
        f"基线结构过旧必须给出可修提示（--update），实际得到：{why2}")
    assert "device" in why2 and "gpu_name" in why2, "应指明缺了哪些字段"


def test_gate_still_skips_on_incomparable_baseline():
    """修诊断不得放松判定：不可比仍必须判跳过（退出码 2），绝不假装通过。"""
    base_old = {"version": pp.BASELINE_VERSION, "p95": 1.0,
                "source": "benchmark", "stage": "detect", "repeat": 3}
    meta = {"source": "benchmark", "stage": "detect", "repeat": 3,
            "device": "cuda", "gpu_name": "X", "engine": None, "runs": 10}
    code, reasons = pp.gate_decision(base_old, {"p95": 0.01}, meta)
    assert code == 2, "不可比必须判跳过（2），绝不能因为 P95 好看就放行"
    assert any("跳过" in r for r in reasons)