# -*- coding: utf-8 -*-
"""阶段 408：run_batch 确实用 queue.max_concurrency 进入 batch_cpu_threads。

gatevenv（torch_required，tab_batch 导入链含 torch）。伪造 BatchQueue（run 空迭代，
不触发真实检测）+ spy 上下文管理器，只验证接线，不做真实修复。
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

pytest.importorskip("torch")
pytestmark = pytest.mark.torch_required

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import ui.tab_batch as tb  # noqa: E402


class _FakeQueue:
    def __init__(self, *a, **k):
        self.max_concurrency = 7
        self._submitted = []

    def submit(self, name, path):
        self._submitted.append((name, path))

    def run(self, processor, on_update=None):
        return iter(())  # 空：不触发真实检测/修复

    def summary(self):
        return {
            "progress": 1.0, "finished": 0, "done": 0, "failed": 0,
            "cancelled": 0, "total": len(self._submitted), "current": None,
            "pending": 0, "running": 0,
        }

    def cancel(self):
        pass


def test_run_batch_wraps_loop_with_batch_cpu_threads(monkeypatch, tmp_path):
    p = tmp_path / "a.png"
    cv2.imwrite(str(p), np.zeros((16, 16, 3), dtype=np.uint8))

    seen = {}

    @contextmanager
    def spy(conc):
        seen["conc"] = conc
        yield conc

    monkeypatch.setattr(tb, "BatchQueue", _FakeQueue)
    monkeypatch.setattr(tb, "batch_cpu_threads", spy)

    gen = tb.run_batch(
        [str(p)], None, None,
        "auto", "auto", 1.0, 0, 0, 128, 0, 1.0,
        False, 1.0, False, 1.0, False, 1.0, False, 1.0, False, 2,
        evaluate=False, compare=False, auto_preset=False, dual_run=False,
    )
    list(gen)  # 消费完整个生成器

    assert seen.get("conc") == 7, "run_batch 应用 queue.max_concurrency 进入上下文管理器"