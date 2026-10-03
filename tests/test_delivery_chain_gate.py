"""阶段 68 · 质量门禁「交付链」口径的回归测试。

背景（docs/68-*）：门禁此前**只评估裸 inpaint**，而用户实际拿到的是「裸修复 + 后处理
默认链」的成品。阶段 60 正是因此在链路末端藏了三个灾难性缺陷（边缘融合把原图水印像素混回
掩码边界、色彩还原用全图统计 + 标准差重缩放、锐化放大掩码内残留），其中角标用例的**掩码区**
PSNR 从 38.4 dB 掉到 11.5 dB —— 而当时门禁全绿。

本阶段把「交付链」纳入门禁，并固化两条口径：
  ① 全图 ΔPSNR 之外，另行测量**掩码区**（被擦除区域）的 PSNR —— 全图指标按面积加权，
     会被大片未改动区域稀释，回答不了"擦干净了没"；
  ② 红线：后处理链**不得**让掩码区 PSNR 低于裸修复（`DELIVERY_MASKED_MAX_DROP`），
     这正好能抓住阶段 60 的三个缺陷类型。

本文件锁定：① 区域 PSNR 语义；② 交付链红线正/负向判定；③ 聚合字段；④ 缺失字段容错；
⑤ 极小配置端到端冒烟（真实后处理链跑通且掩码区零劣化）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import quality_gate as qg  # noqa: E402


def _row(kind="gradient", case="corner_logo", engine="opencv",
         precision=0.6, recall=0.9, iou=0.55, delta=8.0, fpr=0.001,
         masked_bare=30.0, masked_delivery=30.0, with_delivery=True):
    """一条标准明细行（含交付链字段，可关闭以模拟"后处理失败"）。"""
    r = {
        "kind": kind, "case": case, "case_cn": case, "engine": engine,
        "fpr": fpr, "recall": recall, "precision": precision, "iou": iou,
        "pred_cov": 0.02, "gt_cov": 0.02, "coverage_pct": 2.0,
        "psnr_marked": 30.0, "psnr_repaired": 30.0 + delta,
        "delta_psnr": delta, "mae_repaired": 3.0, "quality_score": -0.1,
    }
    if with_delivery:
        r.update({
            "psnr_masked_bare": masked_bare,
            "psnr_masked_delivery": masked_delivery,
            "masked_drop": round(masked_bare - masked_delivery, 2),
            "delta_psnr_delivery": delta + 0.5,
        })
    return r


def _summary(rows=None):
    rows = rows if rows is not None else [
        _row(kind="gradient", case="corner_logo", delta=9.0),
        _row(kind="gradient", case="edge_text", delta=12.0),
    ]
    return {
        "config": {"size": 640, "seed": 7, "kinds": ["gradient"],
                   "cases": ["corner_logo", "edge_text"], "engines": ["opencv"]},
        "rows": rows,
        "clean": [{"kind": "gradient", "preset": p, "fpr": 0.0}
                  for p in ("general",) ],
        "aggregate": qg.aggregate(rows, [{"fpr": 0.0}]),
    }


# ---------------------------------------------------------------------------
# 1. 区域 PSNR 语义（纯函数）
# ---------------------------------------------------------------------------

def test_psnr_region_identical_is_max():
    a = np.full((20, 20), 100, np.uint8)
    assert qg._psnr_region(a, a.copy(), np.ones((20, 20), bool)) == 100.0


def test_psnr_region_empty_is_none():
    a = np.full((20, 20), 100, np.uint8)
    assert qg._psnr_region(a, a.copy(), np.zeros((20, 20), bool)) is None


def test_psnr_region_ignores_changes_outside_region():
    """区域外怎么改都不影响分数——这正是"擦干净了没"要的口径。"""
    a = np.full((20, 20), 100, np.uint8)
    b = a.copy()
    b[:10, :] = 0                      # 上半图被毁
    region = np.zeros((20, 20), bool)
    region[15:, :] = True              # 只看下半图
    assert qg._psnr_region(a, b, region) == 100.0


def test_psnr_region_detects_change_inside_region():
    a = np.full((20, 20), 100, np.uint8)
    b = a.copy()
    b[15:, :] = 110
    region = np.zeros((20, 20), bool)
    region[15:, :] = True
    val = qg._psnr_region(a, b, region)
    assert val is not None and 20.0 < val < 30.0   # 误差 10 → PSNR ≈ 28.1 dB


# ---------------------------------------------------------------------------
# 2. 交付链保真红线
# ---------------------------------------------------------------------------

def test_delivery_red_line_passes_on_drop_zero():
    ok, problems = qg.evaluate(_summary([_row(masked_bare=38.4, masked_delivery=38.4),
                                         _row(case="edge_text", delta=12.0)]))
    assert ok, problems


def test_delivery_red_line_passes_when_postprocess_improves():
    """交付链比裸修复更好（阶段 60 实测的轻微正收益）不得报警。"""
    ok, _ = qg.evaluate(_summary([_row(masked_bare=38.4, masked_delivery=39.3),
                                  _row(case="edge_text", delta=12.0)]))
    assert ok


def test_delivery_red_line_fires_on_masked_degradation():
    """复现阶段 60 的缺陷形态（38.4 → 11.5 dB）必须被抓到。"""
    ok, problems = qg.evaluate(_summary([
        _row(masked_bare=38.4, masked_delivery=11.5),
        _row(case="edge_text", delta=12.0),
    ]))
    assert not ok
    assert any("交付链" in p and "masked" not in p for p in problems)
    assert any("劣化" in p for p in problems)


def test_delivery_red_line_tolerance_is_tight():
    """容差仅用于吸收浮点误差：0.02 dB 的劣化不该被放过。"""
    ok, _ = qg.evaluate(_summary([_row(masked_delivery=29.98),
                                  _row(case="edge_text", delta=12.0)]))
    assert not ok
    assert qg.DELIVERY_MASKED_MAX_DROP <= 0.01


def test_missing_delivery_fields_are_tolerated():
    """后处理失败（无交付数据）时不得误报红线，但会体现在聚合计数里。"""
    rows = [_row(with_delivery=False, case="corner_logo"),
            _row(with_delivery=False, case="edge_text", delta=12.0)]
    ok, problems = qg.evaluate(_summary(rows))
    assert ok, problems
    assert qg.aggregate(rows, []).get("n_delivery") == 0


# ---------------------------------------------------------------------------
# 3. 聚合字段
# ---------------------------------------------------------------------------

def test_aggregate_exposes_delivery_metrics():
    rows = [_row(masked_bare=30.0, masked_delivery=29.5, delta=8.0),
            _row(case="edge_text", delta=10.0, masked_bare=40.0, masked_delivery=40.0)]
    agg = qg.aggregate(rows, [{"fpr": 0.01}])
    assert agg["n_delivery"] == 2
    assert agg["masked_drop_worst"] == pytest.approx(0.5, abs=1e-6)
    assert agg["delta_psnr_delivery"] == pytest.approx(9.5, abs=1e-6)


def test_delivery_drift_key_is_registered():
    """交付链全图 ΔPSNR 也要进基线漂移监控。"""
    assert "delta_psnr_delivery" in qg.TOLERANCES


def test_markdown_contains_delivery_columns():
    md = qg.to_markdown(_summary())
    assert "ΔPSNR(交付)" in md and "掩码区(裸→交付)" in md


# ---------------------------------------------------------------------------
# 4. 端到端冒烟（真实后处理链，slow）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_gate_runs_delivery_chain_without_masked_degradation():
    """极小配置真跑一次：交付链必须跑通、字段齐全、掩码区零劣化。"""
    summary = qg.run_gate(kinds=("gradient",), cases=("corner_logo", "edge_text"),
                          size=320, with_clean=False)
    assert summary["rows"], "门禁未产出任何行"
    for r in summary["rows"]:
        assert r["delta_psnr_delivery"] is not None, f"缺交付链指标：{r}"
        assert r["psnr_masked_delivery"] is not None
        assert r["masked_drop"] <= qg.DELIVERY_MASKED_MAX_DROP, (
            f"{r['kind']}/{r['case']} 交付链掩码区劣化 {r['masked_drop']} dB")
    ok, problems = qg.evaluate(summary)
    assert ok, problems


@pytest.mark.slow
def test_guard_bites_on_mixed_back_pixels(monkeypatch):
    """护栏必须真的咬得住阶段 60 的缺陷形态。

    阶段 60 的第一个缺陷是「边缘融合把原图（= 仍带水印）的像素按软掩码混回掩码边界」，
    实测角标用例掩码区 PSNR 38.4 → 11.5 dB。这里把该形态注入回后处理入口，门禁必须失败
    ——若这条测试通过（门禁仍绿），说明交付链红线形同虚设。
    """
    real = qg.postprocess

    def bad(result, original_rgb=None, mask=None, **kw):
        out = real(result, original_rgb=original_rgb, mask=mask, **kw)
        if original_rgb is None or mask is None:
            return out
        m = (mask > 0)[:, :, None].astype(np.float32) * 0.5
        mixed = out.astype(np.float32) * (1.0 - m) + original_rgb.astype(np.float32) * m
        return np.clip(mixed, 0, 255).astype(np.uint8)

    monkeypatch.setattr(qg, "postprocess", bad)
    summary = qg.run_gate(kinds=("gradient",), cases=("corner_logo",),
                          size=320, with_clean=False)
    row = summary["rows"][0]
    assert row["masked_drop"] > qg.DELIVERY_MASKED_MAX_DROP, (
        f"注入缺陷后掩码区未劣化（drop={row['masked_drop']}），护栏不可信")
    ok, problems = qg.evaluate(summary)
    assert not ok
    assert any("交付链" in p for p in problems)
