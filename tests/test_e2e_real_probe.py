# -*- coding: utf-8 -*-
"""阶段 102 · 端到端真值量测脚本自身的守卫（`scripts/e2e_real_probe.py`）。

为什么守卫「量测工具」和守卫业务代码一样重要
--------------------------------------------
这个脚本产出的 `removal` / `collateral` 数字将作为后续阶段**优化修复侧的唯一判据**。
量测工具若悄悄失真（例如 removal 恒为 0、collateral 忘了取补集），
后续所有「改好了」的结论都会是假的 —— 这是阶段 85/87 那类「静默全绿」的同类风险。

核心守门（双向）：
    J1  **去痕率必须会响应**：把修复结果直接换成「干净原图」⇒ removal 必须 ≈ 1.0；
        换成「加印图本身」（等于没修）⇒ removal 必须 ≈ 0.0。
        一条只是「能跑」的实现会同时满足不了这两端。
    J2  collateral 只统计**非水印区**：故意在真值区外制造损伤 ⇒ collateral 必须上升，
        而在真值区内制造同样损伤 ⇒ collateral 必须不变。
    J3  聚合只吸收 status == ok 的行，且保留 n_total 以便发现静默跳过。
    J4  真值来自差值：真值覆盖必须 > 0，且落在 (0, 1) 开区间内。
    J5  外溢率必须双向响应（阶段 105 新增）：改动全在掩码内 ⇒ spill = 0；
        改动全在掩码外 ⇒ spill = 1。少一侧都会让「外溢」变成只会印数字的哑巴指标。
    J6  spill 红线必须真的会拦人：越过 5% 判失败，落在新默认实测值判通过。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import scripts.e2e_real_probe as ep                        # noqa: E402
from scripts.make_demo_assets import CASES, make_base      # noqa: E402


@pytest.fixture()
def clean():
    """确定性底图（640²，与 quality_gate 默认 size 一致）。

    刻意不用更小的尺寸：长边 < 640 时检测器可能**整图零检出**（属已知边界），
    那会让守卫测到「空掩码」而不是量测逻辑本身。
    """
    return make_base(seed=7, w=640, h=640)


# ---------------------------------------------------------------------------
# J1 · 去痕率必须双向响应（本文件的核心守卫）
# ---------------------------------------------------------------------------

def test_J1_removal_is_one_when_repair_returns_clean(clean, monkeypatch):
    """「完美修复」⇒ removal ≈ 1.0。"""
    monkeypatch.setattr(ep, "inpaint", lambda img, mask, **k: clean.copy())
    row = ep.run_case(clean, "corner_logo", "opencv")
    assert row["status"] == "ok"
    assert row["removal"] == pytest.approx(1.0, abs=0.02), row


def test_J1_removal_is_zero_when_repair_returns_input(clean, monkeypatch):
    """「完全没修」（返回加印图）⇒ removal ≈ 0.0，且 collateral ≈ 0。"""
    monkeypatch.setattr(ep, "inpaint", lambda img, mask, **k: img.copy())
    row = ep.run_case(clean, "corner_logo", "opencv")
    assert row["status"] == "ok"
    assert row["removal"] == pytest.approx(0.0, abs=0.02), row
    assert row["collateral"] == pytest.approx(0.0, abs=1e-9), row


# ---------------------------------------------------------------------------
# J2 · collateral 的选区必须是「真值之外」
# ---------------------------------------------------------------------------

def test_J2_collateral_ignores_damage_inside_truth(clean, monkeypatch):
    """只在真值区内部制造损伤 ⇒ collateral 必须仍为 0。"""
    marked = ep._WM["corner_logo"](clean)
    truth = np.any(marked != clean, axis=2)

    def damage_inside(img, mask, **k):
        out = img.copy()
        out[truth] = 0                      # 只在真值像素上砸黑
        return out

    monkeypatch.setattr(ep, "inpaint", damage_inside)
    row = ep.run_case(clean, "corner_logo", "opencv")
    assert row["collateral"] == pytest.approx(0.0, abs=1e-9), row
    assert row["removal"] < 0.5, "真值区被砸黑，去痕率理应变差"


def test_J2_collateral_catches_damage_outside_truth(clean, monkeypatch):
    """在真值区**外**制造损伤 ⇒ collateral 必须上升（反向对照）。"""
    marked = ep._WM["corner_logo"](clean)
    truth = np.any(marked != clean, axis=2)
    outside = ~ep._dilate(truth, ep.BOUNDARY_PAD)

    def damage_outside(img, mask, **k):
        out = img.copy()
        out[outside] = 0
        return out

    monkeypatch.setattr(ep, "inpaint", damage_outside)
    row = ep.run_case(clean, "corner_logo", "opencv")
    assert row["collateral"] > 1.0, row


# ---------------------------------------------------------------------------
# J3 · 聚合与渲染
# ---------------------------------------------------------------------------

def test_J3_aggregate_skips_non_ok_rows_and_keeps_total():
    rows = [
        {"case": "corner_logo", "status": "ok", "removal": 0.5, "collateral": 0.1,
         "overdraw": 1.0, "gain_db": 1.0, "truth_cov": 0.01, "pred_cov": 0.01,
         "spill": 0.0},
        {"case": "corner_logo", "status": "empty_mask", "removal": None, "collateral": None,
         "overdraw": 0.0, "gain_db": None, "truth_cov": 0.01, "pred_cov": 0.0,
         "spill": None},
    ]
    agg = ep.aggregate(rows)
    logo = [a for a in agg if a["case"] == "corner_logo"][0]
    assert logo["n"] == 1 and logo["n_total"] == 2
    assert logo["removal"] == pytest.approx(0.5)
    assert logo["spill"] == pytest.approx(0.0)


def test_J3_all_cases_rendered_in_markdown():
    agg = [{"case": c, "preset": CASES[c][1], "n": 0, "n_total": 0} for c in CASES]
    md = ep.to_markdown(agg)
    for c in CASES:
        assert c in md, f"表格缺少用例 {c}"


# ---------------------------------------------------------------------------
# J4 · 真值来自差值，必须是合理比例
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", list(CASES))
def test_J4_truth_coverage_is_sane(clean, case, monkeypatch):
    monkeypatch.setattr(ep, "inpaint", lambda img, mask, **k: img.copy())
    row = ep.run_case(clean, case, "opencv")
    assert row["status"] == "ok", row
    assert 0.0 < row["truth_cov"] < 1.0, row
    assert row["pred_cov"] >= 0.0


# ---------------------------------------------------------------------------
# J5 · 外溢率必须双向响应（阶段 105 新增指标）
# ---------------------------------------------------------------------------

def test_J5_spill_is_zero_when_changes_stay_inside_mask(clean, monkeypatch):
    """改动**全在掩码内** ⇒ spill = 0；且没改动时也是 0（不是未知）。"""
    def change_inside(img, mask, **k):
        out = img.copy()
        out[mask > 0] = np.clip(out[mask > 0].astype(np.int16) + 40, 0, 255).astype(np.uint8)
        return out

    monkeypatch.setattr(ep, "inpaint", change_inside)
    row = ep.run_case(clean, "corner_logo", "opencv")
    assert row["status"] == "ok"
    assert row["spill"] == pytest.approx(0.0, abs=1e-9), row

    monkeypatch.setattr(ep, "inpaint", lambda img, mask, **k: img.copy())
    row0 = ep.run_case(clean, "corner_logo", "opencv")
    assert row0["spill"] == 0.0, "零改动时外溢应为 0（而非 None/未知）"


def test_J5_spill_is_one_when_changes_are_outside_mask(clean, monkeypatch):
    """改动**全在掩码外** ⇒ spill = 1.0（反向对照；只测 0 一侧的实现会漏掉这种情况）。"""
    def change_outside(img, mask, **k):
        out = img.copy()
        sel = mask <= 0
        out[sel] = np.clip(out[sel].astype(np.int16) + 40, 0, 255).astype(np.uint8)
        return out

    monkeypatch.setattr(ep, "inpaint", change_outside)
    row = ep.run_case(clean, "corner_logo", "opencv")
    assert row["status"] == "ok"
    assert row["spill"] == pytest.approx(1.0, abs=1e-9), row


def test_J5_spill_redline_registered_for_every_case():
    """新增量必须同时登记红线 —— 否则门禁只印数字、不判定。"""
    for case in CASES:
        lim = ep.E2E_LIMITS.get(case)
        assert lim is not None, f"{case} 缺红线"
        assert lim.get("spill") is not None, f"{case} 缺 spill 红线"


def _agg_spill(case, spill):
    return {"case": case, "preset": CASES[case][1], "n": 10, "n_total": 10,
            "truth_cov": 0.02, "pred_cov": 0.02, "overdraw": 1.0,
            "removal": 0.5, "collateral": 0.5, "spill": spill, "gain_db": 0.5}


def test_J6_gate_fails_when_spill_exceeds_redline():
    """spill 越过 5% 红线必须判失败（否则新指标等于没接门禁）。"""
    agg = [_agg_spill(c, 0.30) for c in CASES]
    fails, _notes, _skipped = ep.evaluate_gate(agg, None)
    assert any("spill" in f for f in fails), fails


def test_J6_gate_passes_at_new_default_spill():
    """新默认配置的实测外溢（≈0.7%）必须判通过。"""
    agg = [_agg_spill(c, 0.007) for c in CASES]
    fails, _notes, _skipped = ep.evaluate_gate(agg, None)
    assert not [f for f in fails if "spill" in f], fails
