# -*- coding: utf-8 -*-
"""Stage 155 回归守卫：run_real_benchmark._aggregate 的 micro 聚合。

背景
----
此前 `_aggregate` 只输出 per-image 均值（macro）precision/recall，而文件头对 recall 的
定义是 micro（TP 区域数 / 真值区域总数）。macro 会系统化高估（少数干净小图与多数重残
大图权重相同），喂给 real_gate 红线可能误绿。本文件固化：
  (1) micro 聚合数值正确且与 macro 不同；
  (2) macro 在偏斜分布下系统性高估（证明该字段有存在意义且旧口径确实偏松）；
  (3) 退化输入（全 error）不崩溃且 micro 落为 0.0；
  (4) real_gate 的红线走 micro，而非 macro。
"""

from __future__ import annotations

import importlib
import json

import scripts.run_real_benchmark as rrb


def _img_row(tp: int, fp: int, fn: int) -> dict:
    prec = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 0.0
    rec = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 0.0
    return {
        "image_id": f"img_{tp}_{fp}_{fn}",
        "results": {
            "general": {
                "tp": tp, "fp": fp, "fn": fn,
                "precision": prec, "recall": rec,
                "mean_iou": 0.5, "mask_iou": 0.1,
                "mask_coverage": 0.01, "truth_coverage": 0.01,
                "coverage_ratio": 1.0,
            }
        },
    }


def _micro(rows):
    tp = sum(r["results"]["general"]["tp"] for r in rows)
    fp = sum(r["results"]["general"]["fp"] for r in rows)
    fn = sum(r["results"]["general"]["fn"] for r in rows)
    return (
        tp / (tp + fp) if (tp + fp) else 0.0,
        tp / (tp + fn) if (tp + fn) else 0.0,
    )


def test_aggregate_reports_micro_correctly():
    # 1 完美小图 + 1 重残大图：macro 0.75 / micro 0.505
    rows = [_img_row(1, 0, 0), _img_row(50, 50, 50)]
    agg = rrb._aggregate(rows, ["general"])["per_preset_aggregate"]["general"]
    mp, mr = _micro(rows)
    assert abs(agg["precision_micro"] - round(mp, 4)) < 1e-9
    assert abs(agg["recall_micro"] - round(mr, 4)) < 1e-9
    # micro 与 macro 必须不同（否则新增字段无意义）
    assert agg["precision_micro"] != agg["precision_mean"]
    assert agg["recall_micro"] != agg["recall_mean"]
    # tp/fp/fn 汇总正确
    assert agg["tp_total"] == 51 and agg["fp_total"] == 50 and agg["fn_total"] == 50


def test_aggregate_macro_inflates_vs_micro():
    # 9 完美小图 + 1 重残大图：macro 0.95 vs micro 0.5413 → 误绿翻转点
    rows = [_img_row(1, 0, 0)] * 9 + [_img_row(50, 50, 50)]
    agg = rrb._aggregate(rows, ["general"])["per_preset_aggregate"]["general"]
    mp, _ = _micro(rows)
    # macro 至少在 micro 之上 0.2（证明旧口径系统偏松）
    assert agg["precision_mean"] - agg["precision_micro"] > 0.2
    assert agg["precision_micro"] == round(mp, 4)
    # 若有一条红线是 precision>=0.6，macro 会 PASS 而 micro 应 FAIL
    assert agg["precision_mean"] >= 0.6
    assert agg["precision_micro"] < 0.6


def test_aggregate_all_error_no_crash_and_micro_zero():
    rows = [{"image_id": "x", "results": {"general": {"error": "boom"}}}]
    agg = rrb._aggregate(rows, ["general"])["per_preset_aggregate"]["general"]
    assert agg["n"] == 0
    # 退化输入不崩溃，micro 落为 0.0（与 macro 一致，无 KeyError）
    assert agg["precision_micro"] == 0.0
    assert agg["recall_micro"] == 0.0


def test_real_gate_uses_micro_not_macro(monkeypatch, tmp_path):
    # 隔离 real_gate 到 tmp 基准目录，避免触碰真实 benchmark_real
    (tmp_path / "manifest.json").write_text(
        json.dumps({"images": [{"id": "a", "path": "a.png", "ai_source": "x"}]}),
        encoding="utf-8",
    )
    (tmp_path / "ground_truth.json").write_text(
        json.dumps({"entries": []}), encoding="utf-8"
    )
    monkeypatch.setenv("WME_BENCH_DIR", str(tmp_path))
    import scripts.real_gate as rg

    importlib.reload(rg)

    raw = {
        "timestamp": "x", "n_images": 2, "presets": ["general"], "per_image": [],
        "per_preset_aggregate": {
            "general": {
                "n": 2,
                "precision_micro": 0.5413, "recall_micro": 0.5413,
                "tp_total": 59, "fp_total": 50, "fn_total": 50,
                "precision_mean": 0.95, "recall_mean": 0.95,
                "mean_iou_mean": 0.5, "mask_iou_mean": 0.1,
                "mask_coverage_mean": 0.01, "truth_coverage_mean": 0.01,
                "coverage_ratio_mean": 1.0, "coverage_ratio_n": 2,
            }
        },
    }

    def fake_run_all(*a, **k):
        return raw

    monkeypatch.setattr(rg, "run_all", fake_run_all)

    summary = rg.run_real_gate(presets=["general"], area_presets=[])
    g = summary["aggregate"]["per_preset"]["general"]
    # 红线必须取 micro（0.5413），而非 macro（0.95）
    assert g["precision"] == 0.5413
    assert g["recall"] == 0.5413
    assert g["precision"] != raw["per_preset_aggregate"]["general"]["precision_mean"]
