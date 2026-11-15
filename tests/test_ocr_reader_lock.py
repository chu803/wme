# -*- coding: utf-8 -*-
"""阶段 408：OCR reader 并发首次初始化只建一个（双重检查锁）。

无 torch / 无真实 easyocr：伪造 easyocr.Reader（构造慢一点放大竞争窗口），
强制走 CPU 分支（DETECT_OCR_USE_GPU=False ⇒ 不触发 device_manager/torch）。
ci311venv 即可跑。
"""
from __future__ import annotations

import sys
import threading
import time
import types

import core.detector as det


def test_concurrent_first_init_builds_one_reader(monkeypatch):
    calls = []

    class FakeReader:
        def __init__(self, *a, **k):
            calls.append(1)
            time.sleep(0.05)  # 放大竞争窗口：无锁时多线程会同时进入创建

    fake = types.ModuleType("easyocr")
    fake.Reader = FakeReader
    monkeypatch.setitem(sys.modules, "easyocr", fake)
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(det, "_OCR_READER", None)
    monkeypatch.setattr(det.config, "DETECT_OCR_USE_GPU", False, raising=False)

    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(det._get_ocr_reader())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(calls) == 1, f"reader 被建了 {len(calls)} 次（应只 1 次）"
    assert results[0] is not None
    assert all(r is results[0] for r in results), "所有线程应拿到同一个 reader"