"""阶段 186 · 性能探针 `perf_probe` 守卫测试（纯函数层，不跑真实推理）。

锁定四层：
  ① 工作量层：`build_workload` **确定性**（同 seed 逐位相同）且确实包含各通路要用的内容；
  ② 分位层：`summarize` 走**唯一实现** `utils.stats.nearest_rank_percentile`（最近秩、无插值）；
  ③ 判定层：`gate_decision` 的退出码语义 —— 无基线/结构过旧 ⇒ **2（跳过，不假装通过）**、
     超回归护栏 ⇒ 1、`--enforce-target` 才把绝对目标升级为红线；
  ④ 抽取回归层：`real_gate._area_stats` 的 P90 在改用共用件后**取值不变**。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import perf_probe as pp          # noqa: E402
from scripts import real_gate as rg           # noqa: E402
from utils import stats                       # noqa: E402


# ---------------------------------------------------------------------------
# ① 工作量层
# ---------------------------------------------------------------------------

def test_workload_is_deterministic():
    a = pp.build_workload(size=256, seed=7)
    b = pp.build_workload(size=256, seed=7)
    c = pp.build_workload(size=256, seed=8)
    assert np.array_equal(a, b), "同 seed 必须逐位相同（否则测量不可复现）"
    assert not np.array_equal(a, c), "不同 seed 应产生不同工作量"


def test_workload_shape_and_contains_plate():
    img = pp.build_workload(size=1024, seed=1)
    assert img.shape == (1024, 1024, 3) and img.dtype == np.uint8
    # 右下角区域应**以亮牌色 236 为主**（格点会 +26 并被截到 255，故不能断言 max）
    vals, counts = np.unique(img[-32:, -32:, 0], return_counts=True)
    assert int(vals[counts.argmax()]) == 236, "右下角未被亮牌主导，工作量构造失真"


# ---------------------------------------------------------------------------
# ② 分位层
# ---------------------------------------------------------------------------

def test_summarize_uses_the_single_shared_implementation():
    assert pp.nearest_rank_percentile is stats.nearest_rank_percentile, (
        "perf_probe 必须复用 utils.stats 的唯一实现，不得自带第二份分位逻辑"
    )


def test_summarize_nearest_rank_semantics():
    """最近秩口径（含 round 的银行家舍入），把既有约定钉死。"""
    s = pp.summarize([1.0, 2.0, 3.0, 4.0])
    assert s["n"] == 4
    assert s["min"] == 1.0 and s["max"] == 4.0
    # idx = round(q*(n-1))：p50 → round(1.5)=2 → xs[2]=3.0；p95 → round(2.85)=3 → 4.0
    assert s["p50"] == 3.0, f"最近秩 p50 期望 3.0，实际 {s['p50']}"
    assert s["p95"] == 4.0, f"最近秩 p95 期望 4.0，实际 {s['p95']}"


def test_summarize_single_and_empty():
    assert pp.summarize([0.5])["p95"] == 0.5
    assert pp.summarize([]) == {}


def test_nearest_rank_rejects_bad_q():
    with pytest.raises(ValueError):
        stats.nearest_rank_percentile([1, 2], 1.5)
    with pytest.raises(ValueError):
        stats.nearest_rank_percentile([1, 2], float("nan"))
    assert stats.nearest_rank_percentile([], 0.5) is None


# ---------------------------------------------------------------------------
# ②' 工作量来源层（真实图优先，无图才兜底合成）
# ---------------------------------------------------------------------------

def test_iter_workload_falls_back_to_synthetic(tmp_path):
    items, used = pp.iter_workload(source="benchmark", limit=3,
                                   images_dir=str(tmp_path / "not-exist"))
    assert used == "synthetic", "无基准图时必须降级并如实回报 source"
    assert len(items) == 1 and items[0][0].startswith("synthetic:")


def test_iter_workload_uses_benchmark_images_first(tmp_path):
    from PIL import Image
    d = tmp_path / "images"
    d.mkdir()
    Image.new("RGB", (32, 32)).save(d / "ai_logo_02_x.jpg")
    Image.new("RGB", (32, 32)).save(d / "01_plain.jpg")
    items, used = pp.iter_workload(source="benchmark", limit=5, images_dir=str(d))
    assert used == "benchmark"
    assert [n for n, _ in items] == ["ai_logo_02_x.jpg", "01_plain.jpg"], (
        "应优先真实角标图（让角标通路真的跑起来），其余按名排序"
    )


def test_baseline_comparable_rejects_source_and_stage_mismatch():
    # 阶段 215：以下 meta 字面量必须带全部可比性维度（engine/runs），
    # 否则会先被判「结构过旧」而跳过，根本测不到「口径不同」这一层。
    _m = {"repeat": 3, "device": "cpu", "gpu_name": None,
          "engine": None, "runs": 10,
          "real_superres_enabled": False, "real_superres_scale": 1,
          "real_superres_backend": "auto"}
    ok, why = pp.baseline_comparable(_base(), dict(_m, source="synthetic",
                                                   stage="detect"))
    assert not ok and "source" in why
    ok2, why2 = pp.baseline_comparable(_base(), dict(_m, source="benchmark",
                                                     stage="inpaint"))
    assert not ok2 and "stage" in why2
    ok3, why3 = pp.baseline_comparable(_base(), dict(_m, source="benchmark",
                                                     stage="detect", repeat=1))
    assert not ok3 and "repeat" in why3, "工作量口径不同（重复次数）也不可比"
    ok4, why4 = pp.baseline_comparable(_base(), dict(_m, source="benchmark",
                                                     stage="detect"))
    assert ok4, f"口径完全一致应判可比，实际：{why4}"


def test_baseline_comparable_rejects_detect_vs_full():
    ok, why = pp.baseline_comparable(_base(), {"source": "benchmark", "stage": "full",
                                              "repeat": 3, "device": "cpu", "gpu_name": None,
                                              "engine": None, "runs": 10,
                                              "real_superres_enabled": False,
                                              "real_superres_scale": 1,
                                              "real_superres_backend": "auto"})
    assert not ok and "stage" in why, "detect 基线与 full 全链路基线不可比"


def test_baseline_comparable_rejects_device_mismatch():
    """D1-Stage 6：跨硬件基线不可比——沙箱 CPU 基线不能用 RTX 5060 全链路比对。

    不同 GPU 型号之间（如 RTX 4090 vs RTX 5060）时延也差一截，必须各自独立标定。
    """
    cpu_base = _base(device="cpu", gpu_name=None)
    ok, why = pp.baseline_comparable(cpu_base, _meta(device="cuda",
                                                     gpu_name="NVIDIA GeForce RTX 5060"))
    assert not ok and "device" in why, "跨设备（cpu↔cuda）基线绝不可比"

    ok2, _ = pp.baseline_comparable(cpu_base, _meta(device="cpu", gpu_name=None))
    assert ok2, "同 cpu（gpu_name 均为空）应可比"

    cuda_base = _base(device="cuda", gpu_name="NVIDIA GeForce RTX 4090")
    ok3, why3 = pp.baseline_comparable(cuda_base, _meta(device="cuda",
                                                        gpu_name="NVIDIA GeForce RTX 5060"))
    assert not ok3 and "gpu_name" in why3, "不同 GPU 型号之间也不可比"


def test_measure_records_device_fingerprint(monkeypatch):
    """D1-Stage 6：measure 必须把设备指纹写入 meta，使基线带硬件标签。"""
    monkeypatch.setattr(pp, "device_fingerprint",
                        lambda: {"device": "cuda", "gpu_name": "FAKE GPU"})
    import api as _api
    monkeypatch.setattr(_api, "remove_watermark_bytes", lambda data, **kw: (b"", None))
    _, meta = pp.measure(source="synthetic", limit=1, repeat=1, stage="full",
                         warmup=False, seed=3)
    assert meta["device"] == "cuda"
    assert meta["gpu_name"] == "FAKE GPU"


def test_measure_full_stage_wires_api_and_records_stage(monkeypatch):
    """--stage full 必须走 api.remove_watermark_bytes 全链路并记录 stage=full。"""
    import api as _api

    calls = []

    def _stub(data, **kw):
        calls.append(kw)
        return (b"\x89PNG", None)

    monkeypatch.setattr(_api, "remove_watermark_bytes", _stub)
    times, meta = pp.measure(source="synthetic", limit=1, repeat=1, stage="full",
                             warmup=False, seed=3)
    assert meta["stage"] == "full"
    assert len(times) == 1 and times[0] >= 0.0
    # 阶段 214：除工作量的 1 次外，**还会多 2 次锚点测量**（轮首 + 轮尾测同一张图），
    # 用于抓「本轮内渐进劣化」。故此处断言「≥3 次」而非「恰好 1 次」，
    # 但每-次都必须是 lama 引擎（锚点测量也须走同一全链路，否则不可比）。
    assert len(calls) == 3, (
        f"full 阶段应有 1 次工作量 + 2 次锚点测量（共 3 次），实际 {len(calls)}")
    assert all(c.get("engine") == "lama" for c in calls), (
        f"锚点测量也必须走 lama 全链路（否则与工作量的口径不一致）：{calls}")
    assert meta.get("anchor_name"), "必须记录锚点图名（供人工判读）"
    assert meta.get("anchor_head") is not None and meta.get("anchor_tail") is not None
    assert meta.get(pp.ANCHOR_KEY) is not None, "必须写入锚点比"


def test_refresh_rules_mark_p95_as_higher_is_worse():
    """刷新护栏必须把 P95 判为「越高越坏」（方向 max），否则变慢会被静默刷新。"""
    rules = pp._refresh_rules()
    assert rules[pp.P95_METRIC][0] == "max"
    assert rules[pp.P95_METRIC][1] > 0


# ---------------------------------------------------------------------------
# ③ 判定层
# ---------------------------------------------------------------------------

def _base(p95=1.0, device="cpu", gpu_name=None):
    return {"version": pp.BASELINE_VERSION, "p95": p95, "source": "benchmark",
            "stage": "detect", "repeat": 3, "device": device, "gpu_name": gpu_name,
            # 阶段 215：补两个可比性维度。缺任一个都会被判「结构过旧」而跳过，
            # 根本测不到护栏层 ⇒ 用例变成「假绿」（LESSONS 81 的连带损伤）。
            "engine": None, "runs": 10,
            # 阶段 263：补三个超分可比性维度（与 _meta 保持一致，否则会先被判结构过旧）。
            "real_superres_enabled": False, "real_superres_scale": 1,
            "real_superres_backend": "auto"}


def _meta(source="benchmark", stage="detect", repeat=3, device="cpu", gpu_name=None):
    return {"source": source, "stage": stage, "repeat": repeat,
            "device": device, "gpu_name": gpu_name,
            "engine": None, "runs": 10,
            "real_superres_enabled": False, "real_superres_scale": 1,
            "real_superres_backend": "auto"}


def test_no_baseline_skips_never_passes():
    code, reasons = pp.gate_decision(None, {"p95": 0.1}, _meta())
    assert code == 2 and reasons
    code2, _ = pp.gate_decision({"version": 999, "p95": 1.0}, {"p95": 0.1}, _meta())
    assert code2 == 2, "结构过旧的基线必须判跳过，不得拿旧世界比对"


def test_within_regression_rail_passes():
    #阶段 212：本组四例只测**护栏/目标层**（第 3 层），故显式声明跳过第 2 层
    #   噪声闸门（它由 test_perf_gate_staleness.py 专项覆盖）。生产路径绝不这样调用。
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 1.2}, _meta(), tol=0.30,
                                     skip_noise_gate=True)
    assert code == 0, reasons
    assert any("在护栏内" in r for r in reasons)


def test_over_regression_rail_fails():
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 1.31}, _meta(), tol=0.30,
                                     skip_noise_gate=True)
    assert code == 1, reasons
    assert any("超护栏" in r for r in reasons)


def test_absolute_target_is_reported_but_not_gated_by_default():
    """CPU 上必然超 1.5s：默认只报告差距，不制造常红。"""
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 1.2}, _meta(), tol=0.30,
                                     enforce_target=False, target=0.1,
                                     skip_noise_gate=True)
    assert code == 0, reasons
    assert any("未启用 --enforce-target" in r for r in reasons)


def test_enforce_target_turns_it_into_a_red_line():
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 1.2}, _meta(), tol=0.30,
                                     enforce_target=True, target=0.1,
                                     skip_noise_gate=True)
    assert code == 1, reasons
    assert any("超目标" in r for r in reasons)


# ---------------------------------------------------------------------------
# ④ 抽取回归层（real_gate 的 P90 取值不得因抽共用件而变）
# ---------------------------------------------------------------------------

def test_real_gate_area_p90_unchanged_after_extraction():
    rows = [{"image_id": f"{i:02d}", "results": {"ai_grid": {
        "mask_coverage": c, "pred_count": 1}}} for i, c in enumerate(
        [0.10, 0.20, 0.30, 0.40])]
    s = rg._area_stats(rows, "ai_grid")
    assert s["n"] == 4
    assert s["mask_coverage_mean"] == pytest.approx(0.25)
    # 最近秩：idx = round(0.9*3) = 3 → 有序样本第 3 个 = 0.40
    assert s["mask_coverage_p90"] == pytest.approx(0.40)
