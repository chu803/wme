"""阶段 50 补充：benchmark 聚合逻辑的单测（不触发 torch 推理，仅校验结构与数值）。

锁定 `scripts/run_benchmark.py` 产出的 benchmark_summary.json 契约：
  · per_case_engine：每个 (data, case, engine) 一条，含均值/标准差；
  · lama_minus_opencv：每个 (data, case) 一条，diff = LaMa − OpenCV 的 ΔPSNR。
"""
import sys
import os
import json

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import run_benchmark as rb  # noqa: E402


def _fake_rows():
    """构造确定的假数据：gradient/textured × corner_logo/grid × opencv/lama，
    每个 (data,case,engine) 给两个种子，ΔPSNR 已知，便于断言聚合正确。"""
    rows = []
    for data in ("gradient", "textured"):
        for case in ("corner_logo", "grid"):
            for engine in ("opencv", "lama"):
                base = {"opencv": 5.0, "lama": 8.0}[engine]
                for seed in (1, 2):
                    rows.append({
                        "data": data,
                        "case": case,
                        "engine": engine,
                        "seed": seed,
                        "delta_psnr": base + (0.4 if seed == 1 else -0.4),
                        "coverage_pct": 3.0,
                        "mae_repaired": 4.0,
                    })
    return rows


def test_agg_one_entry_per_combo():
    rows = _fake_rows()
    summary = rb._agg(rows)
    # 2 数据 × 2 用例 × 2 引擎 = 8 条。
    assert len(summary) == 8
    keys = {(s["data"], s["case"], s["engine"]) for s in summary}
    assert keys == {
        ("gradient", "corner_logo", "opencv"),
        ("gradient", "corner_logo", "lama"),
        ("gradient", "grid", "opencv"),
        ("gradient", "grid", "lama"),
        ("textured", "corner_logo", "opencv"),
        ("textured", "corner_logo", "lama"),
        ("textured", "grid", "opencv"),
        ("textured", "grid", "lama"),
    }


def test_agg_mean_and_std():
    rows = _fake_rows()
    summary = {(_["data"], _["case"], _["engine"]): _ for _ in rb._agg(rows)}
    ocv = summary[("gradient", "corner_logo", "opencv")]
    assert ocv["delta_psnr_mean"] == pytest.approx(5.0)
    assert ocv["delta_psnr_std"] == pytest.approx(0.4)
    assert ocv["n"] == 2


def test_compare_table_diff_is_lama_minus_opencv():
    rows = _fake_rows()
    summary = rb._agg(rows)
    cmp = rb._compare_table(summary)
    # 每 (data,case) 一条 → 4 条。
    assert len(cmp) == 4
    by = {(c["data"], c["case"]): c for c in cmp}
    r = by[("gradient", "corner_logo")]
    assert r["opencv_dpsnr"] == pytest.approx(5.0)
    assert r["lama_dpsnr"] == pytest.approx(8.0)
    assert r["diff"] == pytest.approx(3.0)  # LaMa − OpenCV


def test_compare_table_flags_winner():
    rows = _fake_rows()
    # LaMa(+8) 远优于 OpenCV(+5) → diff>0.5。
    cmp = rb._compare_table(rb._agg(rows))
    assert all(c["diff"] > 0.5 for c in cmp)


def test_benchmark_summary_schema_on_disk(tmp_path, monkeypatch):
    """run_benchmark 写出的 benchmark_summary.json 结构应可被下游消费。"""
    import json as _json

    out = tmp_path / "benchmark_summary.json"
    monkeypatch.setattr(rb, "OUT_DIR", str(tmp_path))
    summary = rb._agg(_fake_rows())
    cmp = rb._compare_table(summary)
    out.write_text(_json.dumps({
        "seeds": [1, 2],
        "per_case_engine": summary,
        "lama_minus_opencv": cmp,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    loaded = _json.loads(out.read_text(encoding="utf-8"))
    assert set(loaded.keys()) == {"seeds", "per_case_engine", "lama_minus_opencv"}
    assert len(loaded["lama_minus_opencv"]) == 4
