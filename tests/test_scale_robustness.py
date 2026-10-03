"""阶段 67 · 缩放 × 二次转码级联审计脚本的红线判定测试。

`scripts/scale_robustness.py` 是阶段 67 的常驻护栏，采用**双口径**结构：
- 默认路径（兜底关闭）保精度：干净图四预设平均误报 ≤ 2%（阶段 57 锁定的保证）；
- 放行路径（兜底开启）保召回：不得出现黑屏，且分用例召回达标。

本文件锁定三条红线的判定语义与豁免规则（纯函数，快），端到端数据由
`tests/test_lowres_rescue.py` 与脚本自身负责。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts.scale_robustness import (  # noqa: E402
    BLACKOUT_EXEMPT_CASES,
    CLEAN_FPR_MAX,
    KNOWN_BOUNDARIES,
    RECALL_FLOORS,
    evaluate,
    summarize,
    to_markdown,
)


def _row(size=256, codec="jpeg75", kind="textured", case="grid",
         recall_off=0.9, recall_on=0.9, gt_px=1000):
    return {
        "size": size, "codec": codec, "kind": kind, "case": case, "gt_px": gt_px,
        "off": {"recall": recall_off, "precision": 0.5, "iou": 0.4},
        "on": {"recall": recall_on, "precision": 0.5, "iou": 0.4},
    }


def _clean(size=256, codec="jpeg75", kind="textured", fpr_off=0.005, fpr_on=0.01):
    return {"size": size, "codec": codec, "kind": kind, "case": "(clean)",
            "off": {"fpr": fpr_off, "per_preset": {}},
            "on": {"fpr": fpr_on, "per_preset": {}}}


def _report(rows):
    return {"config": {"clean_fpr_max": CLEAN_FPR_MAX,
                       "recall_floors": RECALL_FLOORS}, "rows": rows}


# ---------------------------------------------------------------------------
# 1. 健康报告通过
# ---------------------------------------------------------------------------

def test_evaluate_passes_healthy():
    ok, problems = evaluate(_report([_row(), _clean()]))
    assert ok, problems


# ---------------------------------------------------------------------------
# 2. 默认路径精度红线（阶段 57 保证，绝不允许回退）
# ---------------------------------------------------------------------------

def test_default_path_fpr_red_line():
    ok, problems = evaluate(_report([_clean(fpr_off=0.021)]))
    assert not ok
    assert any("默认路径精度红线" in p for p in problems)


def test_default_path_fpr_exactly_at_limit_passes():
    """等于红线不算越线（与门禁 `>` 判定保持一致）。"""
    ok, _ = evaluate(_report([_clean(fpr_off=CLEAN_FPR_MAX)]))
    assert ok


def test_release_arm_fpr_is_reported_not_enforced():
    """放行路径的误报代价不设红线（如实打印供权衡），不得触发失败。"""
    ok, _ = evaluate(_report([_clean(fpr_off=0.005, fpr_on=0.0798)]))
    assert ok


# ---------------------------------------------------------------------------
# 3. 放行路径黑屏红线（阶段 67 核心）
# ---------------------------------------------------------------------------

def test_blackout_red_line_on_rescue_arm():
    ok, problems = evaluate(_report([_row(recall_off=0.0, recall_on=0.0)]))
    assert not ok
    assert any("放行路径黑屏红线" in p for p in problems)


def test_blackout_on_default_arm_is_not_a_violation():
    """默认路径存在黑屏是**已知现状**（由放行闸承接），不算违规。"""
    ok, _ = evaluate(_report([_row(recall_off=0.0, recall_on=0.8)]))
    assert ok


@pytest.mark.parametrize("case", sorted(BLACKOUT_EXEMPT_CASES))
def test_blackout_exempt_cases(case):
    """信息论极限用例（极淡平铺）归零不算回归。"""
    ok, _ = evaluate(_report([_row(case=case, recall_off=0.0, recall_on=0.0)]))
    assert ok


def test_zero_gt_cell_is_ignored():
    """真值像素为 0 的单元不该按黑屏判定（无目标可漏）。"""
    ok, _ = evaluate(_report([_row(recall_on=0.0, gt_px=0)]))
    assert ok


# ---------------------------------------------------------------------------
# 4. 放行路径召回红线与边界豁免
# ---------------------------------------------------------------------------

def test_rescue_recall_floor():
    floor = RECALL_FLOORS["edge_text"]
    ok, problems = evaluate(_report([_row(case="edge_text", recall_on=floor - 0.01)]))
    assert not ok
    assert any("放行路径召回红线" in p for p in problems)


def test_no_known_boundaries_currently():
    """阶段 69：唯一的历史边界（photo/webp75/角标）已被双阈值并集修复 → 空集。

    若将来新增条目，请同时在本测试与 `docs/` 中写明依据与复核方式。
    """
    assert KNOWN_BOUNDARIES == set()


def test_known_boundary_is_exempt_from_floor(monkeypatch):
    """已记录的边界组合不参与召回下限判定（用合成条目验证机制仍然有效）。"""
    import scripts.scale_robustness as qa
    monkeypatch.setattr(qa, "KNOWN_BOUNDARIES", {("photo", "webp75", "corner_logo")})
    ok, _ = qa.evaluate(_report([_row(kind="photo", codec="webp75",
                                      case="corner_logo", recall_on=0.16)]))
    assert ok


def test_known_boundary_still_bounded_by_blackout_rule(monkeypatch):
    """边界组合仍受黑屏红线约束——部分漏检可接受，完全漏检不可接受。"""
    import scripts.scale_robustness as qa
    monkeypatch.setattr(qa, "KNOWN_BOUNDARIES", {("photo", "webp75", "corner_logo")})
    ok, problems = qa.evaluate(_report([_row(kind="photo", codec="webp75",
                                             case="corner_logo", recall_on=0.0)]))
    assert not ok
    assert any("黑屏红线" in p for p in problems)


def test_resolved_boundary_now_meets_floor():
    """原边界组合修复后须满足常规召回下限（不得再依赖豁免）。"""
    ok, problems = evaluate(_report([_row(kind="photo", codec="webp75",
                                          case="corner_logo", recall_on=1.0)]))
    assert ok, problems


# ---------------------------------------------------------------------------
# 5. 渲染与汇总（冒烟）
# ---------------------------------------------------------------------------

def test_markdown_and_summary_smoke():
    report = _report([_row(recall_off=0.0, recall_on=0.8), _clean()])
    md = to_markdown(report)
    assert "默认召回%" in md and "放行召回%" in md
    assert "兜底救回" in md          # 黑屏被救回的单元会被标注
    text = summarize(report)
    assert "默认路径" in text and "放行路径" in text
