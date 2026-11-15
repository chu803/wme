# -*- coding: utf-8 -*-
"""阶段 408：batch_cpu_threads 公式钳制 / 进入设线程 / 退出恢复 / 异常仍恢复。

gatevenv（torch_required）。monkeypatch torch.get/set_num_threads 与 os.cpu_count，
不产生真实线程副作用。
"""
from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytestmark = pytest.mark.torch_required

import config  # noqa: E402,F401  (确保 sys.path 与项目一致)
import core.device_manager as dm  # noqa: E402


def _patch(monkeypatch, cores, entry):
    import torch
    monkeypatch.setattr(dm.os, "cpu_count", lambda: cores)
    monkeypatch.setattr(torch, "get_num_threads", lambda: entry)
    calls = []
    monkeypatch.setattr(torch, "set_num_threads", lambda n: calls.append(n))
    return calls


def test_formula_conc10_and_restore(monkeypatch):
    calls = _patch(monkeypatch, 24, 16)
    with dm.batch_cpu_threads(10) as target:
        assert target == 2          # 24//10=2，clamp(2,1,16)=2（实测最优）
        assert calls == [2]
    assert calls == [2, 16]         # 退出恢复进入值


def test_conc1_caps_to_entry(monkeypatch):
    calls = _patch(monkeypatch, 24, 16)
    with dm.batch_cpu_threads(1) as target:
        assert target == 16         # 24//1=24，clamp(24,1,16)=16（不降单图延迟）
    assert calls == [16, 16]


def test_small_cores_floor_one(monkeypatch):
    calls = _patch(monkeypatch, 4, 16)
    with dm.batch_cpu_threads(10) as target:
        assert target == 1          # 4//10=0，下限 1
    assert calls == [1, 16]


def test_none_cores_fallback(monkeypatch):
    calls = _patch(monkeypatch, None, 16)
    with dm.batch_cpu_threads(10) as target:
        assert target == 1          # (None or 2)//10=0，下限 1
    assert calls == [1, 16]


def test_restores_on_exception(monkeypatch):
    calls = _patch(monkeypatch, 24, 16)
    with pytest.raises(RuntimeError):
        with dm.batch_cpu_threads(10):
            raise RuntimeError("boom")
    assert calls == [2, 16]         # 异常路径仍恢复