"""阶段 263 · full 基线标定口径：真实超分状态必须进入可比性维度。

背景（决策就绪包见 docs/263-*.md）
---------------------------------
阶段 262 实测：开启真实超分令 full 链路耗时 **+2.6s**（无权重 esrgan 退化为双三次，
只放大尺寸、无质量增益）。而旧 `COMPARABILITY_KEYS` 只含
`source/stage/repeat/device/gpu_name/engine/runs`，**漏了超分状态** ⇒
「超分开」与「超分关」的 full 测量会被误判为同口径，护栏失准（LESSONS 230 重演）。

本文件把「超分状态是口径维度」钉成机器判定：
  · 超分开 vs 超分关 → 不可比（判跳过，绝不假装同口径对比）；
  · 同口径（都开/都关）→ 可比；
  · detect 口径不涉及超分（键恒为 None）→ 不受超分维度影响；
  · `measure(stage="full")` 必须**真实记录**当前 config 的超分口径（守卫须指真实调用点）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

import scripts.perf_probe as pp  # noqa: E402


def _full_meta(superres_enabled=True, superres_scale=2, superres_backend="esrgan",
               **over):
    m = {"source": "benchmark", "stage": "full", "repeat": 3,
         "device": "cuda", "gpu_name": "NVIDIA GeForce RTX 5060",
         "engine": "lama", "runs": 10,
         "real_superres_enabled": superres_enabled,
         "real_superres_scale": superres_scale,
         "real_superres_backend": superres_backend}
    m.update(over)
    return m


def _full_baseline(superres_enabled=True, superres_scale=2, superres_backend="esrgan",
                   p95=9.8):
    return {"version": pp.BASELINE_VERSION, "p95": p95,
            "source": "benchmark", "stage": "full", "repeat": 3,
            "device": "cuda", "gpu_name": "NVIDIA GeForce RTX 5060",
            "engine": "lama", "runs": 10,
            "real_superres_enabled": superres_enabled,
            "real_superres_scale": superres_scale,
            "real_superres_backend": superres_backend}


def test_superres_is_full_stage_scoped_not_detect():
    """超分口径必须是 **full 阶段专属** 维度：detect 基线（无超分键）不得因超分维度被判不可比。

    这正是阶段 263 不把超分放进全局 COMPARABILITY_KEYS 的原因——
    否则会让现有 v3 detect 基线被判「结构过旧」失效。
    """
    detect_base = {"version": pp.BASELINE_VERSION, "p95": 0.68, "source": "benchmark",
                   "stage": "detect", "repeat": 3, "device": "cpu", "gpu_name": None,
                   "engine": None, "runs": 10}  # 故意无 real_superres_* 键
    # 本次 detect 测量即便带了超分键，也不应触发不可比（detect 不涉及超分）：
    detect_meta = {"source": "benchmark", "stage": "detect", "repeat": 3,
                   "device": "cpu", "gpu_name": None, "engine": None, "runs": 10,
                   "real_superres_enabled": True, "real_superres_scale": 2,
                   "real_superres_backend": "esrgan"}
    ok, why = pp.baseline_comparable(detect_base, detect_meta)
    assert ok, f"detect 口径不应被超分维度影响，实际：{why}"


def test_full_stage_superres_on_vs_off_not_comparable():
    """核心不变量：超分开 vs 超分关 → 不可比（绝不假装同口径对比）。"""
    # 基线「开」，本次「关」：
    ok, why = pp.baseline_comparable(
        _full_baseline(superres_enabled=True),
        _full_meta(superres_enabled=False))
    assert not ok, "超分开的基线与超分关的测量竟被判可比"
    assert "real_superres_enabled" in why, f"拒绝理由应点名超分维度，实际：{why}"
    # 反向同样不可比：
    ok2, why2 = pp.baseline_comparable(
        _full_baseline(superres_enabled=False),
        _full_meta(superres_enabled=True))
    assert not ok2 and "real_superres_enabled" in why2


def test_full_stage_superres_scale_mismatch_not_comparable():
    """scale 不同（2× vs 4×）也是不同口径 ⇒ 不可比。"""
    ok, why = pp.baseline_comparable(
        _full_baseline(superres_enabled=True, superres_scale=2),
        _full_meta(superres_enabled=True, superres_scale=4))
    assert not ok and "real_superres_scale" in why


def test_full_stage_same_superres_comparable():
    """同口径（都开、scale/backend 一致）→ 可比，不因超分维度拦下。"""
    ok, why = pp.baseline_comparable(
        _full_baseline(superres_enabled=True, superres_scale=2,
                       superres_backend="esrgan"),
        _full_meta(superres_enabled=True, superres_scale=2,
                   superres_backend="esrgan"))
    assert ok, f"超分口径完全一致应判可比，实际：{why}"
    # 都关也应收敛到可比：
    ok2, _ = pp.baseline_comparable(
        _full_baseline(superres_enabled=False),
        _full_meta(superres_enabled=False))
    assert ok2, "超分都关应判可比"


def test_detect_stage_superres_none_does_not_break_comparability():
    """detect 口径不涉及超分（键恒 None）→ 同 None 应可比，超分维度不影响 detect。"""
    base = {"version": pp.BASELINE_VERSION, "p95": 0.68, "source": "benchmark",
            "stage": "detect", "repeat": 3, "device": "cpu", "gpu_name": None,
            "engine": None, "runs": 10,
            "real_superres_enabled": None, "real_superres_scale": None,
            "real_superres_backend": None}
    meta = {"source": "benchmark", "stage": "detect", "repeat": 3,
            "device": "cpu", "gpu_name": None, "engine": None, "runs": 10,
            "real_superres_enabled": None, "real_superres_scale": None,
            "real_superres_backend": None}
    ok, why = pp.baseline_comparable(base, meta)
    assert ok, f"detect 口径（超分均为 None）应可比，实际：{why}"


def test_measure_full_records_superres_state(monkeypatch):
    """守卫须指真实调用点：measure(stage='full') 必须真实记录当前 config 超分口径。"""
    import config as _cfg
    monkeypatch.setattr(_cfg, "REAL_SUPERRES_ENABLED", True)
    monkeypatch.setattr(_cfg, "REAL_SUPERRES_SCALE", 2)
    monkeypatch.setattr(_cfg, "REAL_SUPERRES_BACKEND", "esrgan")

    import api as _api

    def _stub(data, **kw):
        return (b"\x89PNG", None)

    monkeypatch.setattr(_api, "remove_watermark_bytes", _stub)
    _, meta = pp.measure(source="synthetic", limit=1, repeat=1, stage="full",
                         warmup=False, seed=3)
    assert meta["real_superres_enabled"] is True, "full 测量未记录超分开状态"
    assert meta["real_superres_scale"] == 2
    assert meta["real_superres_backend"] == "esrgan"


def test_full_baseline_missing_superres_key_is_stale_not_fake_comparable():
    """没有超分键的旧基线（如升级前 v3）应被判「结构过旧」而非偷偷当同口径。"""
    stale = {"version": 3, "p95": 9.8, "source": "benchmark", "stage": "full",
             "repeat": 3, "device": "cuda", "gpu_name": "NVIDIA GeForce RTX 5060",
             "engine": "lama", "runs": 10}  # 故意缺 real_superres_*
    ok, why = pp.baseline_comparable(stale, _full_meta())
    assert not ok and ("缺字段" in why or "结构过旧" in why or "real_superres" in why), (
        f"缺超分键的旧基线竟被当可比：{why}")
