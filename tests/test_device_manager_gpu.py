# -*- coding: utf-8 -*-
"""GPU 设备选择链路契约测试（D1 产品化 · GPU 自动选设备）。

背景：core/device_manager.select_device("auto") 的 CUDA 分支此前**从未被 CI 覆盖**
（CI 机器无 GPU，torch.cuda.is_available() 恒为 False），属于「死代码路径风险」——
一旦用户在真机装好 CUDA 版 torch，行为全靠运行时验证，无回归护栏。

本测试用 monkeypatch 把 torch.cuda 伪装成「可用显卡」，对 auto/cuda/cpu 三种模式、
显存安全水位降级、半精度门控逐一断言，把 CUDA 分支钉成可机器判定的契约。

注意：本测试**不依赖真实显卡**，只在 CPU 版 torch 上伪造 CUDA 行为，沙箱/CI 均可跑。
"""

import config
import pytest

import core.device_manager as dm
from core.device_manager import (
    select_device,
    use_fp16,
    reset_cache,
    get_device_info,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个用例前后清空设备选择缓存，避免模式串味。"""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def _fake_cuda(monkeypatch):
    """把 torch.cuda 伪装成「有一张 8GB 显卡可用」。"""
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True, raising=False)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 0, raising=False)

    class _FakeProps:
        name = "Fake NVIDIA GPU"
        total_memory = 8 * 1024 * 1024 * 1024  # 8 GB

    monkeypatch.setattr(
        torch.cuda, "get_device_properties", lambda _i: _FakeProps(), raising=False
    )
    # 跳过可选依赖 pynvml：直接返回充足的剩余显存，避免走到真实 NVML 查询。
    monkeypatch.setattr(dm, "_query_free_vram_mb", lambda _index: 7000, raising=True)
    return torch


def test_auto_selects_cuda_when_available(_fake_cuda):
    """auto 模式在检测到 CUDA 时应选 GPU。"""
    handle = select_device("auto")
    assert handle.info.kind == "cuda"
    assert handle.info.is_gpu is True
    assert handle.info.index == 0


def test_auto_use_fp16_true_on_gpu(_fake_cuda):
    """GPU 模式下且配置允许半精度时，use_fp16() 应为 True。"""
    assert config.DEVICE_USE_FP16 is True  # 项目默认开启
    assert use_fp16() is True


def test_cuda_explicit_raises_when_unavailable(monkeypatch):
    """强制 cuda 模式但系统无显卡时，应明确报错而非静默降级。"""
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False, raising=False)
    try:
        select_device("cuda")
        raise AssertionError("期望 RuntimeError，但未抛出")
    except RuntimeError:
        pass


def test_auto_downgrades_to_cpu_on_low_vram(_fake_cuda, monkeypatch):
    """auto 模式下剩余显存低于安全阈值时应退回 CPU。"""
    monkeypatch.setattr(dm, "_query_free_vram_mb", lambda _index: 100, raising=True)
    assert config.DEVICE_MIN_VRAM_MB > 100  # 确认阈值确实高于伪造值
    handle = select_device("auto")
    assert handle.info.kind == "cpu"


def test_cpu_mode_explicit():
    """强制 cpu 模式应返回非 GPU 设备，且半精度关闭。"""
    handle = select_device("cpu")
    assert handle.info.kind == "cpu"
    assert handle.info.is_gpu is False
    assert use_fp16() is False


def test_get_device_info_caches_selection(_fake_cuda):
    """get_device_info 应在已选定 CUDA 后稳定返回 GPU 信息。"""
    info = get_device_info()
    assert info.kind == "cuda"
    assert info.is_gpu is True
