"""阶段 57 · 端到端质量门禁的回归测试。

分两层：
  1) **判定逻辑层**（纯函数，零计算开销）：`quality_gate.evaluate` / `aggregate`
     的各种越线、漂移、配置不一致、去重语义——这是门禁"会不会正确报警"的核心，
     必须用确定数据锁死，避免哪天容差改坏了而无人察觉。
  2) **集成冒烟层**（真实跑一次极小配置）：保证门禁脚本本身在真实检测/修复链路上
     能跑通、字段齐全、修复确有净提升。默认用小尺寸 + 单用例控制耗时，标 slow。

注意：干净图误报率存在**分辨率依赖**（同种子 photo 底图 320² 约 4.1%、640² 约 0.5%，
详见 `docs/57-*`），故集成冒烟用 `with_clean=False` 跳过该项——它只在 size=640
的正式门禁里判定，不进单测。
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import quality_gate as qg  # noqa: E402


# ---------------------------------------------------------------------------
# 构造确定数据的辅助函数
# ---------------------------------------------------------------------------

def _row(kind="gradient", case="corner_logo", engine="opencv",
         precision=0.6, recall=0.9, iou=0.55, delta=8.0, fpr=0.001):
    """一条标准明细行（数值均可覆盖，便于构造越线场景）。"""
    return {
        "kind": kind, "case": case, "case_cn": case, "engine": engine,
        "fpr": fpr, "recall": recall, "precision": precision, "iou": iou,
        "pred_cov": 0.02, "gt_cov": 0.02, "coverage_pct": 2.0,
        "psnr_marked": 30.0, "psnr_repaired": 30.0 + delta,
        "delta_psnr": delta, "mae_repaired": 3.0, "quality_score": -0.1,
    }


def _healthy_rows():
    """覆盖 REQUIRED_POSITIVE_DELTA 两个用例的健康明细。"""
    return [
        _row(kind="gradient", case="corner_logo", delta=9.0),
        _row(kind="gradient", case="edge_text", delta=12.0),
        _row(kind="photo", case="corner_logo", delta=7.0),
        _row(kind="photo", case="edge_text", delta=10.0),
    ]


def _clean_rows(fpr=0.004):
    return [{"kind": k, "preset": p, "fpr": fpr, "pred_cov": fpr}
            for k in ("gradient", "photo") for p in ("general", "ai_logo")]


def _summary(rows=None, clean=None, config=None):
    rows = _healthy_rows() if rows is None else rows
    clean = _clean_rows() if clean is None else clean
    return {
        "config": {"seed": 7, "size": 640, "kinds": ["gradient", "photo"],
                   "cases": ["corner_logo", "edge_text"], "engines": ["opencv"],
                   **(config or {})},
        "rows": rows,
        "clean": clean,
        "aggregate": qg.aggregate(rows, clean),
    }


# ---------------------------------------------------------------------------
# 1. 判定逻辑层（纯函数，快）
# ---------------------------------------------------------------------------

def test_evaluate_passes_on_healthy_summary():
    """健康数据：无基线也能通过绝对红线。"""
    ok, problems = qg.evaluate(_summary())
    assert ok, problems
    assert problems == []


def test_evaluate_flags_clean_fpr_red_line():
    """干净图误报率越线必须报警（这是阶段 49 的核心成果，不容回退）。"""
    s = _summary(clean=_clean_rows(fpr=0.08))
    ok, problems = qg.evaluate(s)
    assert not ok
    assert any("clean_fpr" in p and "红线" in p for p in problems)


def test_evaluate_flags_precision_red_line():
    """精确率低于 35% 必须报警。"""
    rows = [_row(case=c, precision=0.10) for c in ("corner_logo", "edge_text")]
    ok, problems = qg.evaluate(_summary(rows=rows))
    assert not ok
    assert any("precision" in p and "红线" in p for p in problems)


def test_evaluate_flags_non_positive_delta():
    """必赢用例（corner_logo / edge_text）出现 ΔPSNR≤0 必须报警。"""
    rows = [
        _row(case="corner_logo", delta=9.0),
        _row(case="edge_text", delta=-2.5),     # 修完反而更差
    ]
    ok, problems = qg.evaluate(_summary(rows=rows))
    assert not ok
    assert any("edge_text" in p and "ΔPSNR" in p for p in problems)


def test_evaluate_flags_missing_required_case():
    """必赢用例一条结果都没有时，不能静默通过（可能是检测器整体失效）。"""
    rows = [_row(case="corner_logo", delta=9.0)]
    ok, problems = qg.evaluate(_summary(rows=rows))
    assert not ok
    assert any("edge_text" in p and "无" in p for p in problems)


def test_evaluate_flags_baseline_drift():
    """指标相对基线明显下滑（超出容差）必须报警。"""
    baseline = _summary()
    # 精确率从 0.6 掉到 0.4（下滑 33%，超过 20% 容差）。
    rows = [_row(case=c, precision=0.4) for c in ("corner_logo", "edge_text")]
    ok, problems = qg.evaluate(_summary(rows=rows), baseline)
    assert not ok
    assert any("漂移" in p and "precision" in p for p in problems)


def test_evaluate_ignores_improvement():
    """指标变好永远不报警（容差只约束变坏方向）。"""
    baseline = _summary()
    rows = [_row(case=c, precision=0.95, iou=0.9, delta=20.0)
            for c in ("corner_logo", "edge_text")]
    ok, problems = qg.evaluate(_summary(rows=rows), baseline)
    assert ok, problems


def test_evaluate_flags_config_mismatch():
    """基线尺寸/种子与本次不一致时数字不可比，必须提示而非默默比对。"""
    baseline = _summary()
    ok, problems = qg.evaluate(_summary(config={"size": 320}), baseline)
    assert not ok
    assert any("配置" in p for p in problems)


def test_clean_fpr_drift_uses_absolute_tolerance():
    """误报率基线近 0，相对容差无意义——用绝对上浮 0.01 判定。"""
    baseline = _summary(clean=_clean_rows(fpr=0.002))
    # 0.002 → 0.005：绝对上浮 0.003，未超 0.01，应通过。
    ok, _ = qg.evaluate(_summary(clean=_clean_rows(fpr=0.005)), baseline)
    assert ok
    # 0.002 → 0.030：绝对上浮 0.028，应报警。
    ok, problems = qg.evaluate(_summary(clean=_clean_rows(fpr=0.030)), baseline)
    assert not ok
    assert any("clean_fpr" in p and "漂移" in p for p in problems)


def test_aggregate_dedupes_engines():
    """同一 (kind, case) 跑多引擎时，检测侧指标只算一次（否则引擎数会稀释聚合）。"""
    rows = [
        _row(case="corner_logo", engine="opencv", precision=0.8, recall=0.9, iou=0.7),
        _row(case="corner_logo", engine="lama", precision=0.8, recall=0.9, iou=0.7),
    ]
    agg = qg.aggregate(rows, _clean_rows())
    assert agg["precision"] == pytest.approx(0.8, abs=1e-6)
    assert agg["n_cases"] == 1          # 去重后只剩 1 个 (kind, case)
    assert agg["n_repaired"] == 2       # 但修复结果确实是 2 条


def test_aggregate_skips_none_delta():
    """未检出掩码的行 delta_psnr=None，不得污染平均（也不得抛异常）。"""
    rows = [
        _row(case="corner_logo", delta=10.0),
        {**_row(case="grid"), "delta_psnr": None, "mae_repaired": None,
         "quality_score": None},
    ]
    agg = qg.aggregate(rows, _clean_rows())
    assert agg["delta_psnr"] == pytest.approx(10.0, abs=1e-6)
    assert agg["n_repaired"] == 1


# ---------------------------------------------------------------------------
# 1b. 尺寸鲁棒性扫描的判定逻辑（纯函数，快）
# ---------------------------------------------------------------------------

def _sweep(rows=None, limit=0.02):
    """构造一个最小 sweep 结构。"""
    cfg = {"seed": 7, "kinds": ["textured", "photo"],
           "sizes": [256, 320, 448, 640], "max_fpr": limit}
    if rows is None:
        rows = [{"size": s, "kind": k, "fpr": 0.005, "presets": {}}
                for s in cfg["sizes"] for k in cfg["kinds"]]
    return {"config": cfg, "rows": rows,
            "max_fpr": max(r["fpr"] for r in rows)}


def test_evaluate_sweep_passes_healthy():
    """全档误报率均 ≤ 红线时通过。"""
    ok, problems = qg.evaluate_sweep(_sweep())
    assert ok, problems


def test_evaluate_sweep_flags_small_image_regression():
    """小图误报率反弹必须报警——这是阶段 57 修复守护的核心场景。"""
    rows = [{"size": 640, "kind": "textured", "fpr": 0.010, "presets": {}},
            {"size": 256, "kind": "textured", "fpr": 0.045, "presets": {}}]
    ok, problems = qg.evaluate_sweep(_sweep(rows=rows))
    assert not ok
    assert any("256" in p and "尺寸红线" in p for p in problems)


def test_min_area_floor_anchor_semantics():
    """绝对下限 = 参考边长(640)² × 占比 × relax，且 >= 1。"""
    from core.detector import _min_area_px_floor
    # translucent 通路（relax=1）：0.0002 × 640² ≈ 81.9 → 81。
    assert _min_area_px_floor(0.0002) == 81
    # tiled/grid 通路（relax=0.25）：0.0002 × 640² × 0.25 ≈ 20.5 → 20。
    assert _min_area_px_floor(0.0002, relax=0.25) == 20
    # 极小占比也要 >= 1（防空洞下限导致 max_area 断言异常）。
    assert _min_area_px_floor(0.0) == 1


# ---------------------------------------------------------------------------
# 2. 集成冒烟层（真实跑极小配置）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_gate_smoke_real_run():
    """真实跑一次门禁（gradient × corner_logo，size=320），验证链路与字段完整性。

    刻意用 with_clean=False：干净图误报率有分辨率依赖（320² 天然偏高），
    该项只在 size=640 的正式门禁中判定。
    """
    s = qg.run_gate(kinds=("gradient",), cases=("corner_logo",), seed=7,
                    size=320, engines=("opencv",), with_clean=False)

    assert s["clean"] == []                     # 跳过干净图评估
    assert len(s["rows"]) == 1
    row = s["rows"][0]
    for key in ("precision", "recall", "iou", "delta_psnr",
                "mae_repaired", "quality_score"):
        assert key in row

    # 角落 LOGO 是检测器 + OpenCV 的舒适区：必须检出、且修复确有净提升。
    assert row["delta_psnr"] is not None and row["delta_psnr"] > 0
    assert row["precision"] > 0.5
    assert row["quality_score"] is not None

    # 该配置下应通过全部必赢红线（corner_logo 在，edge_text 缺 → 会因缺用例报警）。
    ok, problems = qg.evaluate(s)
    assert not ok
    assert any("edge_text" in p for p in problems)
