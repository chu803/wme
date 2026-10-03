"""阶段 63 · 有损压缩（JPEG）鲁棒性回归测试。

背景：真实输入几乎必经 JPEG，而 benchmark 全部无损。审计结论是**检测器对 JPEG 鲁棒**
（召回 corner_logo 100%、edge_text 98~100%、grid 77.9~99.7%，干净误报 ≤1.05%），
但审计过程中踩到一个**度量口径陷阱**，必须用测试永久锁住：

    真值必须用「无损配对」求水印支撑区（|wm(base) − base| > 0，未压缩图）；
    若改用「压缩后配对」求差，两张图各自的 JPEG 噪声互相独立 → 真值从 ~1.2% 膨胀到
    近全图 → 召回被稀释成假性崩塌（实测 grid 从 ~99% 被算成「12%」）。

本文件同时锁定：① 红线判定语义；② 口径陷阱（压缩 GT 膨胀、假性召回崩塌）；
③ 真实召回下限与干净误报（slow）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts.detector_diag import BASE_KINDS, gt_mask, metrics  # noqa: E402
from scripts.jpeg_robustness import (  # noqa: E402
    CLEAN_FPR_MAX,
    QUALITIES,
    RECALL_FLOORS,
    evaluate,
    jpeg_roundtrip,
    run,
)
from scripts.make_demo_assets import CASES  # noqa: E402


def _row(quality="JPEG75", kind="textured", case="grid",
         recall=0.9, precision=0.5, iou=0.4):
    return {"quality": quality, "kind": kind, "case": case,
            "recall": recall, "precision": precision, "iou": iou}


def _report(rows):
    return {"config": {"qualities": ["PNG", 90, 75, 60], "recall_floors": {},
                       "clean_fpr_max": CLEAN_FPR_MAX},
            "rows": rows}


# ---------------------------------------------------------------------------
# 1. 红线判定语义（纯函数，快）
# ---------------------------------------------------------------------------

def test_evaluate_passes_healthy():
    ok, problems = evaluate(_report([_row(recall=0.9), _row(case="(clean_fpr)", iou=0.01)]))
    assert ok, problems


def test_evaluate_flags_recall_drop():
    """网格在 textured 上召回跌破 70% 必须报警（JPEG 恶化回归护栏）。"""
    ok, problems = evaluate(_report([_row(kind="textured", case="grid", recall=0.55)]))
    assert not ok
    assert any("召回红线" in p and "grid" in p for p in problems)


def test_evaluate_flags_clean_fpr_regression():
    """JPEG 下干净误报反弹必须报警。"""
    ok, problems = evaluate(_report([_row(case="(clean_fpr)", iou=0.05)]))
    assert not ok
    assert any("误报红线" in p for p in problems)


def test_recall_floors_cover_all_core_cases():
    """每个「核心用例 × 底图」都必须有召回红线，防漏配。"""
    for case in ("corner_logo", "edge_text", "grid"):
        for kind in ("gradient", "textured", "photo"):
            assert (case, kind) in RECALL_FLOORS, f"缺少 {case}/{kind} 召回红线"


# ---------------------------------------------------------------------------
# 2. 度量口径陷阱（阶段 63 的方法论护栏）
# ---------------------------------------------------------------------------

def test_compressed_gt_inflates_and_fakes_recall_collapse():
    """用「压缩后配对」求真值会膨胀到近全图 → 召回被稀释成假性崩塌。

    这条测试把当时的误判机制固化下来：若未来有人"顺手"把真值改成压缩后求差，
    就会再次得出"JPEG 把召回打到 12%"的错误结论。
    """
    base_raw = BASE_KINDS["textured"](7, w=640, h=640)
    marked_raw = CASES["grid"][0](base_raw)
    gt_clean = gt_mask(base_raw, marked_raw)                  # 正确口径
    q = 75
    gt_compressed = gt_mask(jpeg_roundtrip(base_raw, q),
                            jpeg_roundtrip(marked_raw, q))    # 错误口径
    cov_clean = float(gt_clean.mean())
    cov_compressed = float(gt_compressed.mean())
    assert cov_compressed > 5 * cov_clean, (
        f"压缩后配对真值应显著膨胀（{cov_compressed:.3f} vs {cov_clean:.3f}）")
    assert cov_compressed > 0.5, "压缩噪声应让几乎整图都被判为「水印区」"


# ---------------------------------------------------------------------------
# 3. 真实召回下限与干净误报（slow）
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
@pytest.mark.parametrize("case", ["corner_logo", "edge_text", "grid"])
def test_jpeg75_recall_floor(kind, case):
    """JPEG75 下核心用例召回必须达标（实测留 ~8~10pp 余量）。"""
    report = run(qualities=(75,), kinds=(kind,))
    rows = [r for r in report["rows"] if r["case"] == case]
    assert rows, "审计未产出该用例结果"
    floor = RECALL_FLOORS[(case, kind)]
    assert rows[0]["recall"] >= floor, (
        f"JPEG75 {kind}/{case} 召回 {rows[0]['recall'] * 100:.1f}% < {floor * 100:.0f}%")


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_jpeg75_clean_fpr_no_regression(kind):
    """JPEG75 下干净图四预设平均误报必须 < 2%。"""
    report = run(qualities=(75,), kinds=(kind,))
    rows = [r for r in report["rows"] if r["case"] == "(clean_fpr)"]
    assert rows and rows[0]["iou"] < CLEAN_FPR_MAX


def test_qualities_constant_covers_png_and_jpeg():
    """审计至少覆盖 PNG 基线与 JPEG90/75/60 三档。"""
    assert None in QUALITIES and {90, 75, 60} <= set(QUALITIES)
