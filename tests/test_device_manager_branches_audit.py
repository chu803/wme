# -*- coding: utf-8 -*-
"""287 · core/device_manager 续审守卫。

首轮 `test_device_manager_gpu.py` 用 fake CUDA 钉死了 auto/cuda/cpu 三模式主路径 +
显存安全水位降级 + fp16 门控（D1 把 CUDA 分支从「死代码路径风险」变成可机判定契约）。
本轮补**首轮未覆盖的分支不变量**（探针 .scratch/probe_device_manager_reaudit.py 实跑 27 项全 OK）：
  · select_device 缓存复用语义（auto 复用旧决策 / 模式变化才重新探测 / 同模式返回同一对象）
  · select_device(None) 走 config.DEVICE_MODE 默认
  · _detect_cuda 的 torch 缺失 / get_device_properties 异常两条分支 → 返回 None
  · _query_free_vram_mb 的 NVML try/finally（三条子路径：成功 / mem 失败 / init 失败，均返回 None 且 shutdown 必调用 = 无资源泄漏）
  · apply_cpu_threads 的 n=None(走默认0=no-op) / 显式 n=2(写 torch 线程 + OMP/MKL env)
  · empty_cache 节流（二次立即调用被跳过 / force 绕过 / 配置关闭完全不调用）
  · use_fp16 在 config.DEVICE_USE_FP16=False 时（即便 GPU）返回 False
  · _DeviceHandle.torch_device 的 cpu/cuda 身份 + is_gpu 委托
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

pytest.importorskip("torch")  # 与 test_device_manager_gpu.py 同依赖边界：torch 在位才跑

import config  # noqa: E402
import core.device_manager as dm  # noqa: E402
from core.device_manager import (  # noqa: E402
    select_device,
    use_fp16,
    reset_cache,
    _detect_cuda,
    _query_free_vram_mb,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def _fake_cuda(monkeypatch):
    """把 torch.cuda 伪装成「有一张 8GB 显卡、剩余 7000MB」。"""
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True, raising=False)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 0, raising=False)

    class _P:
        name = "Fake NVIDIA GPU"
        total_memory = 8 * 1024 * 1024 * 1024

    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda _i: _P(), raising=False)
    monkeypatch.setattr(dm, "_query_free_vram_mb", lambda _index: 7000, raising=True)
    return torch


# --------------------------------------------------------------------------- #
# 1 · 缓存复用语义（首轮完全未覆盖）
# --------------------------------------------------------------------------- #
def test_cache_auto_reuses_cuda(_fake_cuda):
    """已选 cuda 后，select_device('auto') 应直接复用同一对象（不再重新探测）。"""
    h1 = select_device("cuda")
    h2 = select_device("auto")
    assert h1 is h2 and h1.info.kind == "cuda"


def test_cache_auto_reuses_cpu_no_reprobe(_fake_cuda):
    """已选 cpu 后，select_device('auto') 应复用 cpu（不重新探测 GPU，这是有意语义）。"""
    c1 = select_device("cpu")
    c2 = select_device("auto")
    assert c1 is c2 and c1.info.kind == "cpu"


def test_cache_mode_change_reprobes_cpu_to_cuda(_fake_cuda):
    """从 cpu 切到 cuda 应重新探测（模式变化，不复用）。"""
    c1 = select_device("cpu")
    c3 = select_device("cuda")
    assert c1 is not c3 and c3.info.kind == "cuda"


def test_cache_mode_change_reprobes_cuda_to_cpu(_fake_cuda):
    """从 cuda 切到 cpu 应重新探测。"""
    g1 = select_device("cuda")
    g2 = select_device("cpu")
    assert g1 is not g2 and g2.info.kind == "cpu"


def test_select_device_none_uses_default_mode(monkeypatch):
    """mode=None 应使用 config.DEVICE_MODE（默认 auto）；无 GPU 时退回 cpu。"""
    monkeypatch.setattr(config, "DEVICE_MODE", "auto")
    monkeypatch.setattr(config, "DEVICE_EMPTY_CACHE_AFTER_RUN", False)  # 避免副作用
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False, raising=False)
    h = select_device(None)
    assert h.info.kind == "cpu"


# --------------------------------------------------------------------------- #
# 2 · _detect_cuda 的退化分支
# --------------------------------------------------------------------------- #
def test_detect_cuda_torch_missing_returns_none(monkeypatch):
    """torch 不可导入时 _detect_cuda 应返回 None（而非抛异常）。"""
    monkeypatch.setitem(sys.modules, "torch", None)
    assert _detect_cuda() is None


def test_detect_cuda_props_exception_returns_none_and_auto_falls_back(_fake_cuda, monkeypatch):
    """get_device_properties 抛异常 → _detect_cuda 返回 None，且 auto 退回 cpu。"""
    import torch
    with monkeypatch.context() as m:
        m.setattr(torch.cuda, "get_device_properties", mock.Mock(side_effect=RuntimeError("boom")))
        assert _detect_cuda() is None
        assert select_device("auto").info.kind == "cpu"


# --------------------------------------------------------------------------- #
# 3 · _query_free_vram_mb 的 NVML try/finally（无资源泄漏）
# --------------------------------------------------------------------------- #
class _FakeNVML:
    def __init__(self, fail_at=None, free=6000):
        self.fail_at = fail_at
        self.free = free
        self.shutdown_called = False

    def nvmlInit(self):
        if self.fail_at == "init":
            raise RuntimeError("init fail")

    def nvmlDeviceGetHandleByIndex(self, _i):
        if self.fail_at == "handle":
            raise RuntimeError("handle fail")
        return object()

    def nvmlDeviceGetMemoryInfo(self, _h):
        if self.fail_at == "mem":
            raise RuntimeError("mem fail")
        class _M:
            pass
        m = _M()
        m.free = self.free * 1024 * 1024
        return m

    def nvmlShutdown(self):
        self.shutdown_called = True


def test_query_free_vram_success(monkeypatch):
    nv = _FakeNVML()
    monkeypatch.setitem(sys.modules, "pynvml", nv)
    assert _query_free_vram_mb(0) == 6000
    assert nv.shutdown_called, "成功路径也须关闭 NVML（无泄漏）"


def test_query_free_vram_mem_failure_returns_none(monkeypatch):
    nv = _FakeNVML(fail_at="mem")
    monkeypatch.setitem(sys.modules, "pynvml", nv)
    assert _query_free_vram_mb(0) is None
    assert nv.shutdown_called, "异常路径仍须在 finally 关闭 NVML"


def test_query_free_vram_init_failure_returns_none(monkeypatch):
    nv = _FakeNVML(fail_at="init")
    monkeypatch.setitem(sys.modules, "pynvml", nv)
    assert _query_free_vram_mb(0) is None


# --------------------------------------------------------------------------- #
# 4 · apply_cpu_threads
# --------------------------------------------------------------------------- #
def test_apply_cpu_threads_default_zero_is_noop(monkeypatch):
    """默认 config.CPU_THREADS=0 → 守卫 `n and n>0` 成立为假 → 不调 torch、不设 env。"""
    monkeypatch.setattr(config, "CPU_THREADS", 0)
    import torch
    called = []
    monkeypatch.setattr(torch, "set_num_threads", lambda n: called.append(n))
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    dm.apply_cpu_threads()
    assert called == [], "默认 0 线程应为 no-op"
    assert os.environ.get("OMP_NUM_THREADS") is None


def test_apply_cpu_threads_explicit_sets(monkeypatch):
    """显式 n=2 → 调 torch.set_num_threads(2) 并写入 OMP/MKL_NUM_THREADS。"""
    import torch
    import cv2
    called = []
    monkeypatch.setattr(torch, "set_num_threads", lambda n: called.append(n))
    monkeypatch.setattr(cv2, "setNumThreads", lambda n: None)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    dm.apply_cpu_threads(2)
    assert called == [2]
    assert os.environ.get("OMP_NUM_THREADS") == "2"
    assert os.environ.get("MKL_NUM_THREADS") == "2"


# --------------------------------------------------------------------------- #
# 5 · empty_cache 节流
# --------------------------------------------------------------------------- #
def test_empty_cache_throttle_and_force(monkeypatch):
    import time
    import torch
    calls = {"n": 0}
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True, raising=False)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(config, "DEVICE_EMPTY_CACHE_AFTER_RUN", True)
    monkeypatch.setattr(config, "EMPTY_CACHE_MIN_INTERVAL_SEC", 1.0)

    dm._last_empty_ts = 0.0
    dm.empty_cache()                       # 首次：执行
    assert calls["n"] == 1
    dm._last_empty_ts = time.monotonic()   # 模拟「刚调用过」
    dm.empty_cache()                       # 立即二次：节流跳过
    assert calls["n"] == 1
    dm.empty_cache(force=True)             # force 绕过节流
    assert calls["n"] == 2


def test_empty_cache_disabled_when_config_off(monkeypatch):
    import torch
    calls = {"n": 0}
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True, raising=False)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(config, "DEVICE_EMPTY_CACHE_AFTER_RUN", False)
    dm.empty_cache()
    assert calls["n"] == 0, "开关关闭时完全不调用"


# --------------------------------------------------------------------------- #
# 6 · use_fp16 / _DeviceHandle
# --------------------------------------------------------------------------- #
def test_use_fp16_false_when_config_disabled(_fake_cuda, monkeypatch):
    """即便在 GPU 上，config.DEVICE_USE_FP16=False 也必须返回 False。"""
    monkeypatch.setattr(config, "DEVICE_USE_FP16", False)
    assert use_fp16() is False


def test_device_handle_torch_device_identity(_fake_cuda, monkeypatch):
    """_DeviceHandle.torch_device 在 cpu/cuda 下身份正确；is_gpu 委托正确。"""
    # cpu 句柄
    reset_cache()
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False, raising=False)
    hc = select_device("cpu")
    assert str(hc.torch_device) == "cpu"
    # cuda 句柄（恢复 is_available=True，否则上面钉的 False 会延续到此处）
    reset_cache()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True, raising=False)
    hg = select_device("cuda")
    assert str(hg.torch_device) == "cuda:0"
    assert hg.is_gpu is True
