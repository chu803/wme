# -*- coding: utf-8 -*-
"""阶段 408：核数感知批量并发默认公式 + env 覆盖。ci311venv（无 torch）。"""
from __future__ import annotations

import importlib
import os

import config


def test_default_formula_values():
    f = config._batch_concurrency_default
    assert f(24) == 10     # 本机 24 逻辑核 ⇒ 实测最优档
    assert f(16) == 8
    assert f(4) == 2
    assert f(2) == 2       # max(2, 1)=2
    assert f(1) == 2       # 下限 2
    assert f(None) == 2    # 兜底 (None or 2)//2=1 ⇒ max2
    assert f(100) == 10    # 上限 10


def test_env_override_honored(monkeypatch):
    monkeypatch.setenv("WME_BATCH_CONCURRENCY", "3")
    cfg = importlib.reload(config)
    try:
        assert cfg.BATCH_CONCURRENCY == 3
    finally:
        monkeypatch.delenv("WME_BATCH_CONCURRENCY", raising=False)
        importlib.reload(config)


def test_default_within_measured_range():
    # 未设 env 时默认落在 [2,10]；设了 env 则尊重 env（1–16）。
    if os.environ.get("WME_BATCH_CONCURRENCY") is None:
        assert 2 <= config.BATCH_CONCURRENCY <= 10
    else:
        assert 1 <= config.BATCH_CONCURRENCY <= 16