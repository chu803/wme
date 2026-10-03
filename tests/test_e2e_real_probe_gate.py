# -*- coding: utf-8 -*-
"""Stage 156 回归守卫：e2e_real_probe.evaluate_gate 的红线方向契约。

背景
----
`e2e_real_probe.evaluate_gate` 是「修复侧门禁」的**决策函数**（与检测侧 real_gate 对应），
但既有测试（test_e2e_real_probe.py J1~J6）只覆盖**量测**（removal/collateral/spill 是否响应），
**没有守卫决策函数本身**。决策函数若把红线方向写反（如 removal 本应守下界却守了上界），
会让「去痕率掉到负值」这类严重退化被静默放过。本文件固化红线方向契约 +
0-case / 缺类型 / 引擎不符 三类必须判失败的边界。

红线口径（来自 E2E_LIMITS，corner_logo 为例）：
  removal    ≥ 0.25  （下界，低了变坏）
  collateral ≤ 1.40  （上界，高了变坏）
  overdraw   ≤ 1.16  （上界）
  spill      ≤ 0.05  （上界）
"""

from __future__ import annotations

import scripts.e2e_real_probe as ep


def _row(case: str = "corner_logo", n: int = 3, n_total: int = 3,
         removal: float = 0.30, collateral: float = 1.30,
         overdraw: float = 1.10, spill: float = 0.03) -> dict:
    return {
        "case": case, "n": n, "n_total": n_total,
        "removal": removal, "collateral": collateral,
        "overdraw": overdraw, "spill": spill,
    }


def test_gate_in_spec_passes():
    # 各项均在红线内（baseline=None 只判红线、跳过漂移）
    fails, notes, skipped = ep.evaluate_gate([_row()], None)
    assert fails == [], fails
    assert any("无基线快照" in s for s in skipped)


def test_gate_redline_removal_below_fails():
    # removal 落到红线之下（0.20 < 0.25）必须判失败
    fails, _, _ = ep.evaluate_gate([_row(removal=0.20)], None)
    assert any("corner_logo.removal" in f and "< 0.25" in f for f in fails), fails


def test_gate_redline_collateral_above_fails():
    fails, _, _ = ep.evaluate_gate([_row(collateral=1.50)], None)
    assert any("corner_logo.collateral" in f and "> 1.4" in f for f in fails), fails


def test_gate_redline_overdraw_above_fails():
    fails, _, _ = ep.evaluate_gate([_row(overdraw=1.30)], None)
    assert any("corner_logo.overdraw" in f and "> 1.16" in f for f in fails), fails


def test_gate_redline_spill_above_fails():
    fails, _, _ = ep.evaluate_gate([_row(spill=0.06)], None)
    assert any("corner_logo.spill" in f and "> 0.05" in f for f in fails), fails


def test_gate_removal_high_is_not_failure():
    # removal 高于红线（更好）绝不能判失败（下界只守下限）
    fails, _, _ = ep.evaluate_gate([_row(removal=0.90)], None)
    assert not any("removal" in f for f in fails), fails


def test_gate_zero_cases_fails():
    fails, _, _ = ep.evaluate_gate([_row(n=0, n_total=3)], None)
    assert any("0/3" in f for f in fails), fails


def test_gate_missing_case_fails():
    fails, _, _ = ep.evaluate_gate([_row(case="nonexistent_case")], None)
    assert any("未登记红线" in f for f in fails), fails


def test_gate_baseline_engine_mismatch_fails():
    baseline = {"engine": "opencv", "side": 640, "aggregate": []}
    fails, _, _ = ep.evaluate_gate([_row()], baseline)
    assert any("引擎不同" in f for f in fails), fails


def test_gate_drift_removal_drop_fails():
    # 当前 removal=0.10 相对基线 0.30 下跌 -66.7%，超漂移容差
    baseline = {
        "engine": "lama", "side": 640,
        "aggregate": [{"case": "corner_logo", "n": 3, "removal": 0.30,
                       "collateral": 1.30, "overdraw": 1.10, "spill": 0.03}],
    }
    fails, _, _ = ep.evaluate_gate([_row(removal=0.10)], baseline)
    assert any("[漂移] corner_logo.removal" in f for f in fails), fails
