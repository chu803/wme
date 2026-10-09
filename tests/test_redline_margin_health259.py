# -*- coding: utf-8 -*-
"""
阶段 259 · 红线余量健康监控的守卫（docs/258 决策 A 的落地守卫）。

背景
----
阶段 258 承诺面余量分析发现：`mask_recall` 红线 0.45 已达实测 0.5111 的 88%
（余量仅 ~12%），是全部红线里最脆弱的一条，却是阶段 175 起的对外头条口径。
决策 A = **维持红线不变**，但给它加「余量健康监控」。

本守卫钉死监控契约
----------------
1. **纯函数正确性**：`redline_relative_margin`（min/max 两型余量）与
   `check_redline_margin_health`（余量过薄 + 漂移-硬线缓冲不足两条判定）行为正确。
2. **注入式反例**（经验 #263/#268）：把某指标余量压到地板以下、把漂移容差调到
   ≥ 余量，监控**必须**报出——证明它真能抓住退化，不是哑巴。
3. **接线非死代码**（经验 #32）：`real_gate.main` 在门禁流程里**真实调用**了
   `check_redline_margin_health`（源码级断言调用点存在）。
4. **只报不失败**（经验 #47）：余量体检不得混入 `evaluate_real` 的 problems/退出码——
   否则 mask_recall（余量 12% vs 地板 10%）会让门禁长期红灯而失去拦回退作用。
5. **当前基线体检通过**：用 `benchmark_real/quality_baseline.json` 的承诺面实测值
   跑体检，**不应**报出「缓冲不足」类问题（现状是余量偏薄但双层防御仍有 0.010 缓冲）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import real_gate as rg  # noqa: E402

BASELINE_PATH = os.path.join(_ROOT, "benchmark_real", "quality_baseline.json")


# ---------------------------------------------------------------------------
# 1) 纯函数：相对余量
# ---------------------------------------------------------------------------
def test_relative_margin_min_type():
    """min 型（越高越好）：余量 = (value − thr) / value。"""
    # recall 实测 0.84，红线 0.64 → 余量 (0.84−0.64)/0.84 ≈ 0.238
    m = rg.redline_relative_margin("min", 0.84, 0.64)
    assert m == pytest.approx(0.2381, abs=1e-3)


def test_relative_margin_max_type():
    """max 型（越高越坏）：余量 = (thr − value) / value。"""
    # mask_coverage 实测 0.012，红线 0.020 → 余量 (0.020−0.012)/0.012 ≈ 0.667
    m = rg.redline_relative_margin("max", 0.012, 0.020)
    assert m == pytest.approx(0.6667, abs=1e-3)


def test_relative_margin_zero_or_negative_value_is_zero():
    """实测 ≤0 时余量信息无意义（返回 0.0，交由上层判缺失，不抛异常）。"""
    assert rg.redline_relative_margin("min", 0.0, 0.45) == 0.0
    assert rg.redline_relative_margin("min", -1.0, 0.45) == 0.0


def test_relative_margin_violated_is_nonpositive():
    """已越线（min 型 value<thr / max 型 value>thr）⇒ 余量 ≤ 0。"""
    assert rg.redline_relative_margin("min", 0.40, 0.45) < 0  # 召回不足
    assert rg.redline_relative_margin("max", 0.05, 0.020) < 0  # 过检面积超标


# ---------------------------------------------------------------------------
# 2) 体检：余量过薄 + 漂移-硬线缓冲不足
# ---------------------------------------------------------------------------
def test_health_flags_thin_margin_above_floor():
    """余量正常（recall 0.84 vs 地板 15%）不应报「余量过薄」。"""
    problems = rg.check_redline_margin_health("general", {"recall": 0.84})
    assert not any("余量" in p for p in problems), problems


def test_health_flags_margin_below_floor():
    """注入式反例：recall 实测压到 0.66（余量 3% < 地板 15%）必须报「余量过薄」。"""
    problems = rg.check_redline_margin_health("general", {"recall": 0.66})
    assert any("余量" in p and "recall" in p for p in problems), \
        f"余量过薄未被抓住: {problems}"


def test_health_flags_collapsed_drift_buffer():
    """注入式反例：把 mean_iou 实测压到余量 < 漂移容差(15%) ⇒ 报「缓冲不足」。

    mean_iou 红线 0.35；漂移容差 0.15。取实测 0.36 → 余量 (0.36−0.35)/0.36
    ≈ 0.028 < 0.15 ⇒ 漂移容差 ≥ 余量 ⇒ 双层防御压扁，必须报出。
    """
    problems = rg.check_redline_margin_health("general", {"mean_iou": 0.36})
    assert any("缓冲" in p for p in problems), \
        f"漂移-硬线缓冲不足未被抓住: {problems}"


def test_health_clean_state_returns_empty():
    """余量充足且缓冲充足（recall 0.84）⇒ 无任何体检告警。"""
    assert rg.check_redline_margin_health("general", {"recall": 0.84}) == []


def test_health_missing_metric_skipped():
    """指标缺失（None）时跳过，不误报（不把「没数据」读成「余量过薄」）。"""
    assert rg.check_redline_margin_health("general", {}) == []


def test_every_contract_metric_has_floor():
    """契约里每条红线都登记了预警地板——否则新增红线会因漏登记而静默无监控。"""
    for key in rg.REAL_PRESET_CONTRACT["general"]:
        assert key in rg.REDLINE_MARGIN_FLOOR, \
            f"{key} 未登记 REDLINE_MARGIN_FLOOR，新增红线会静默失去余量监控"


# ---------------------------------------------------------------------------
# 3) 接线非死代码（经验 #32）：main 真实调用了体检函数
# ---------------------------------------------------------------------------
def test_main_calls_margin_health():
    """源码级断言：`main` 里存在 `check_redline_margin_health` 的真实调用点。"""
    src = open(rg.__file__, encoding="utf-8").read()
    # 出现「定义 + main 内调用」；定义处是 `def check_`，调用处是 `check_redline_margin_health(`
    assert "def check_redline_margin_health" in src
    # main 内调用（advisory 段）：函数名后紧跟 `(_preset` 实参形态
    assert "check_redline_margin_health(_preset, _m)" in src, \
        "main 未真实调用余量体检（监控成死代码，经验 #32）"


def test_margin_health_not_in_problems():
    """只报不失败（经验 #47）：余量体检调用不得出现在 evaluate_real 的 problems 收集里。

    做法：`evaluate_real` 源码体内不应出现 `check_redline_margin_health`——
    它只在 main 里作为独立 advisory 调用，故不会污染 ok/退出码。
    """
    src = open(rg.__file__, encoding="utf-8").read()
    start = src.index("def evaluate_real")
    end = src.index("def to_markdown")
    body = src[start:end]
    assert "check_redline_margin_health" not in body, \
        "余量体检被混入 evaluate_real → 会变成门禁失败项，可能导致长期红灯"


# ---------------------------------------------------------------------------
# 4) 当前基线体检：余量偏薄但不应报「缓冲不足」
# ---------------------------------------------------------------------------
def test_committed_baseline_has_no_collapsed_buffer():
    """用承诺面基线实测跑体检：现状不应报「缓冲不足」（mask_recall 仍有 0.010 缓冲）。

    这是「只报不失败」策略的关键验证——若此处报出缓冲不足并被当成失败，
    门禁会长期红灯。允许报出「余量过薄」类 advisory，但不应有「缓冲不足」。
    """
    if not os.path.exists(BASELINE_PATH):
        pytest.skip("承诺面基线缺失（需 collect_real_assets）")
    with open(BASELINE_PATH, encoding="utf-8") as f:
        base = json.load(f)
    per = base.get("aggregate", {}).get("per_preset", {})
    metrics = per.get("general", {})
    assert metrics, "基线缺少 general 实测值"
    problems = rg.check_redline_margin_health("general", metrics)
    collapsed = [p for p in problems if "缓冲" in p]
    assert not collapsed, (
        f"承诺面基线出现「漂移-硬线缓冲不足」: {collapsed}——"
        f"这会让门禁长期红灯，请复核 mask_recall 等指标的余量"
    )


def test_committed_baseline_mask_recall_margin_is_thin_but_safe():
    """钉住 docs/258 的核心事实：mask_recall 余量确实薄（~12%），但仍高于 10% 地板。

    若此断言失败（余量变了），说明数据集/检测器换代——应刷新 docs/258 分析，
    而非默默让它通过。
    """
    if not os.path.exists(BASELINE_PATH):
        pytest.skip("承诺面基线缺失")
    with open(BASELINE_PATH, encoding="utf-8") as f:
        base = json.load(f)
    general = base.get("aggregate", {}).get("per_preset", {}).get("general", {})
    thr = rg.REAL_PRESET_CONTRACT["general"]["mask_recall"][1]
    value = general["mask_recall"]
    margin = rg.redline_relative_margin("min", value, thr)
    # 现状 ~12%：薄，但高于 10% 地板（余量健康监控存在的意义就在这个区间）
    assert 0.10 <= margin < 0.20, (
        f"mask_recall 余量 {margin:.1%} 已离开 docs/258 记录的「薄但安全」区间——"
        f"请复核并刷新 docs/258"
    )