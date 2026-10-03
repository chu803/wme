"""阶段 72 · 多次转码级联审计脚本的红线判定测试。

`scripts/cascade_robustness.py` 是阶段 72 的常驻护栏，四条红线：
  ① 黑屏红线（非豁免用例召回 0 = 静默漏检）；
  ② 分用例召回下限（corner/edge ≥90%、grid ≥55%）；
  ③ 自动模式误报红线：general 预设全轮次 ≤2%（重压缩图上推荐器只会给出 general）；
  ④ 四预设平均误报：n≤2 ≤2%（正常区间沿用阶段 57 红线）；n≥3 ≤5%（极端再压缩的已记录代价）。

端到端数据由脚本自身与 `docs/72-*` 负责，本文件锁定判定语义（纯函数，快）。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts.cascade_robustness import (  # noqa: E402
    AVG_FPR_MAX_DEGRADED,
    AVG_FPR_MAX_NORMAL,
    BLACKOUT_EXEMPT_CASES,
    GENERAL_FPR_MAX,
    RECALL_FLOORS,
    evaluate,
    to_markdown,
)


def _wm_row(kind="textured", case="grid", recalls=None, gt_px=5000):
    """构造带水印用例行：recalls 为 {轮数: 召回}。"""
    rounds = {n: {"recall": r, "precision": 0.3, "iou": 0.2}
              for n, r in (recalls or {}).items()}
    return {"kind": kind, "case": case, "gt_px": gt_px, "rounds": rounds}


def _clean_row(kind="textured", fprs=None):
    """构造干净图行：fprs 为 {轮数: (平均误报, general 误报)}。"""
    rounds = {n: {"avg": avg, "general": gen, "per_preset": {}}
              for n, (avg, gen) in (fprs or {}).items()}
    return {"kind": kind, "case": "(clean)", "rounds": rounds}


def _report(rows):
    return {"config": {"recall_floors": RECALL_FLOORS}, "rows": rows}


def test_evaluate_passes_healthy():
    ok, problems = evaluate(_report([
        _wm_row(recalls={0: 0.9, 3: 0.7, 8: 0.6}),
        _clean_row(fprs={0: (0.009, 0.0), 3: (0.022, 0.007), 8: (0.037, 0.014)}),
    ]))
    assert ok, problems


# ---------------------------------------------------------------------------
# 1. 黑屏红线
# ---------------------------------------------------------------------------

def test_blackout_red_line():
    ok, problems = evaluate(_report([_wm_row(recalls={0: 0.8, 8: 0.0})]))
    assert not ok
    assert any("黑屏红线" in p and "n=8" in p for p in problems)


@pytest.mark.parametrize("case", sorted(BLACKOUT_EXEMPT_CASES))
def test_blackout_exempt_case(case):
    """信息论极限用例（极淡平铺）归零不算回归。"""
    ok, _ = evaluate(_report([_wm_row(case=case, recalls={0: 0.0, 8: 0.0})]))
    assert ok


def test_zero_gt_cell_ignored():
    """无真值单元不参与黑屏与召回判定。"""
    ok, _ = evaluate(_report([_wm_row(recalls={0: 0.0, 8: 0.0}, gt_px=0)]))
    assert ok


# ---------------------------------------------------------------------------
# 2. 召回下限
# ---------------------------------------------------------------------------

def test_recall_floor_violation():
    floor = RECALL_FLOORS["grid"]
    ok, problems = evaluate(_report([
        _wm_row(case="grid", recalls={0: floor + 0.05, 8: floor - 0.05})]))
    assert not ok
    assert any("召回红线" in p and "n=8" in p for p in problems)


def test_recall_floor_holds_at_other_rounds():
    """只有越线的轮次报警，其余轮次不误报。"""
    floor = RECALL_FLOORS["grid"]
    ok, problems = evaluate(_report([
        _wm_row(case="grid", recalls={0: floor + 0.05, 8: floor + 0.05})]))
    assert ok, problems


# ---------------------------------------------------------------------------
# 3. 自动模式误报红线（general 全轮次 ≤2%）
# ---------------------------------------------------------------------------

def test_general_fpr_red_line():
    ok, problems = evaluate(_report([
        _clean_row(fprs={0: (0.01, 0.0), 8: (0.037, 0.021)})]))
    assert not ok
    assert any("自动模式误报红线" in p and "n=8" in p for p in problems)


def test_general_fpr_exactly_at_limit_passes():
    ok, _ = evaluate(_report([_clean_row(fprs={8: (0.05, GENERAL_FPR_MAX)})]))
    assert ok


# ---------------------------------------------------------------------------
# 4. 四预设平均误报：正常区间与退化区间两条线
# ---------------------------------------------------------------------------

def test_avg_fpr_normal_round_red_line():
    """n≤2 沿用阶段 57 的 2% 红线（正常质量区间不许放宽）。"""
    ok, problems = evaluate(_report([
        _clean_row(fprs={2: (AVG_FPR_MAX_NORMAL + 0.001, 0.0)})]))
    assert not ok
    assert any("正常区间误报红线" in p for p in problems)


def test_avg_fpr_degraded_round_allows_documented_cost():
    """n≥3 的四预设平均误报按「已记录代价」放宽到 5%——实测最大 4.76%。"""
    ok, problems = evaluate(_report([
        _clean_row(fprs={3: (AVG_FPR_MAX_DEGRADED - 0.001, 0.019),
                         8: (AVG_FPR_MAX_DEGRADED - 0.001, 0.019)})]))
    assert ok, problems


def test_avg_fpr_degraded_round_red_line():
    ok, problems = evaluate(_report([
        _clean_row(fprs={3: (AVG_FPR_MAX_DEGRADED + 0.001, 0.019)})]))
    assert not ok
    assert any("退化区间误报红线" in p for p in problems)


def test_degraded_round_still_bounded_by_general_line():
    """退化区间放宽的是「四预设平均」，general 的 2% 契约仍然独立生效。"""
    ok, problems = evaluate(_report([
        _clean_row(fprs={3: (AVG_FPR_MAX_DEGRADED - 0.001, 0.03)})]))
    assert not ok
    assert any("自动模式误报红线" in p for p in problems)


# ---------------------------------------------------------------------------
# 5. 渲染冒烟
# ---------------------------------------------------------------------------

def test_markdown_smoke():
    report = _report([_wm_row(recalls={0: 0.9, 8: 0.6}),
                      _clean_row(fprs={0: (0.01, 0.0), 8: (0.037, 0.014)})])
    md = to_markdown(report)
    assert "n=0" in md and "均 " in md


# ---------------------------------------------------------------------------
# 6. 缩放维度（阶段 73）：scale < 1 的行为记录性数据，仅保留黑屏红线（x0.75）
# ---------------------------------------------------------------------------

def _scaled_wm_row(case="corner_logo", scale=0.75, recalls=None, gt_px=3000):
    row = _wm_row(case=case, recalls=recalls, gt_px=gt_px)
    row["scale"] = scale
    return row


def _scaled_clean_row(scale=0.75, fprs=None):
    row = _clean_row(fprs=fprs)
    row["scale"] = scale
    return row


def test_scaled_075_blackout_is_redline():
    """x0.75 下角标黑屏仍是红线（下采样未落入阶段 67 的 opt-in 兜底区间）。"""
    ok, problems = evaluate(_report([_scaled_wm_row(scale=0.75, recalls={3: 0.0})]))
    assert not ok
    assert any("黑屏红线" in p and "x0.75" in p for p in problems)


def test_scaled_05_blackout_is_recorded_not_redline():
    """x0.5 属阶段 67 已记录的低分辨率区间：黑屏记数据、不触发本审计红线。"""
    ok, problems = evaluate(_report([
        _scaled_wm_row(case="corner_logo", scale=0.5, recalls={1: 0.0}),
        _scaled_wm_row(case="grid", scale=0.5, recalls={3: 0.02}),
        _scaled_clean_row(scale=0.5, fprs={8: (0.05, 0.03)}),   # 误报也只记录
    ]))
    assert ok, problems


def test_scaled_rows_have_no_recall_floor_and_no_fpr_contract():
    """低分辨率行的召回/误报为记录性数据：低召回与高误报都不判定。"""
    ok, problems = evaluate(_report([
        _scaled_wm_row(case="grid", scale=0.75, recalls={8: 0.23}),
        _scaled_wm_row(case="corner_logo", scale=0.75, recalls={8: 0.5}),
        _scaled_clean_row(scale=0.75, fprs={3: (0.04, 0.0301)}),
    ]))
    assert ok, problems


def test_scale_1_rows_keep_full_contract():
    """缩放维度不得稀释原尺寸（scale=1）的既有红线。"""
    ok, _ = evaluate(_report([_wm_row(recalls={8: 0.0})]))          # 黑屏仍失败
    assert not ok
    ok, _ = evaluate(_report([_clean_row(fprs={0: (0.01, GENERAL_FPR_MAX + 0.01)})]))
    assert not ok


def test_markdown_labels_scaled_rows():
    report = _report([_scaled_wm_row(scale=0.75, recalls={0: 0.9})])
    assert "@x0.75" in to_markdown(report)


def test_rescale_semantics():
    import numpy as np
    from scripts.cascade_robustness import rescale
    img = np.zeros((640, 640, 3), np.uint8)
    assert rescale(img, 1.0) is img
    out = rescale(img, 0.5)
    assert out.shape[:2] == (320, 320)
