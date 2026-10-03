# -*- coding: utf-8 -*-
"""
阶段 171 守卫：`e2e_real_probe` 的门禁退出码契约（0 通过 / 1 失败 / 2 跳过）。

背景（阶段 171 门禁退出码契约审计）：
  `e2e_real_probe` 的 `--gate` 帮助文本写着「退出码 0 通过 / 1 失败 / 2 跳过」，
  `evaluate_gate` 文档也写着 `skipped`「计入退出码 2 的依据」；但 `main` 此前
  **只把 `fails` 变成 1、其余一律 0** ⇒ 「基线缺失 ⇒ 漂移层未判」这一**跳过**
  被读成 exit 0（绿）——文档承诺的 2 形同虚设，且与同族 `prompted_area_gate`
  （基线缺失返回 2）不对称（经验 185：判据不对称即 bug）。

本守卫钉住三件事：
  1. `gate_exit_code` 的映射：()→0、fails→1、skipped→2、两者都有→1（失败优先）；
  2. **无基线 ⇒ skipped 非空且映射到 2**（回归：此前映射到 0）；
  3. 红线越界 ⇒ 映射到 1；有基线且合规 ⇒ 映射到 0。

刻意不用 pytest fixture，便于直接调用核验。
"""

import scripts.e2e_real_probe as e2e


def _good_agg():
    """一个四类红线全过的最小 agg（corner_logo）。"""
    return [{
        "case": "corner_logo", "n": 1, "n_total": 1,
        "removal": 0.35, "collateral": 1.0, "overdraw": 1.0, "spill": 0.0,
    }]


def _base(agg):
    return {"engine": e2e.E2E_GATE_ENGINE, "side": e2e.SIDE,
            "limit": e2e.E2E_GATE_LIMIT, "aggregate": agg}


def test_exit_code_mapping_basic():
    assert e2e.gate_exit_code([], []) == 0
    assert e2e.gate_exit_code(["boom"], []) == 1
    assert e2e.gate_exit_code([], ["skipped something"]) == 2
    # 失败优先于跳过
    assert e2e.gate_exit_code(["boom"], ["skipped"]) == 1


def test_missing_baseline_is_skip_not_pass():
    """回归守卫：无基线 ⇒ 漂移层未判 ⇒ 必须 exit 2，不得 exit 0。"""
    fails, _notes, skipped = e2e.evaluate_gate(_good_agg(), None)
    assert not fails, "红线应全过（构造如此）"
    assert skipped, "无基线必须产生 skipped 项"
    assert e2e.gate_exit_code(fails, skipped) == 2


def test_with_baseline_good_metrics_passes():
    agg = _good_agg()
    fails, _notes, skipped = e2e.evaluate_gate(agg, _base(agg))
    assert not fails and not skipped
    assert e2e.gate_exit_code(fails, skipped) == 0


def test_red_line_violation_fails():
    bad = [{"case": "corner_logo", "n": 1, "n_total": 1,
            "removal": 0.0, "collateral": 1.0, "overdraw": 1.0, "spill": 0.0}]
    fails, _notes, skipped = e2e.evaluate_gate(bad, _base(bad))
    assert fails, "removal 0.0 < 红线 0.25，应判失败"
    assert e2e.gate_exit_code(fails, skipped) == 1
