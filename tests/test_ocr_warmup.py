# -*- coding: utf-8 -*-
"""守卫测试：warm_up_ocr 的安全性 / 幂等性 / 非阻塞性（阶段 238）。

不真实加载 EasyOCR（避免 ~5s 初始化拖慢测试），通过 monkeypatch 内部
`_get_ocr_reader` 验证调用契约；真实加载路径由 server.py / main.py 自检覆盖。
"""
import time

import config
import core.detector as det


def test_warm_up_ocr_noop_when_ocr_disabled(monkeypatch):
    monkeypatch.setattr(config, "DETECT_USE_OCR", False)
    calls = []
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: calls.append(1) or None)
    # 两种模式都应直接 no-op，绝不触发初始化。
    assert det.warm_up_ocr(blocking=False) is None
    assert det.warm_up_ocr(blocking=True) is None
    assert calls == [], "DETECT_USE_OCR=False 时 warm_up_ocr 不应初始化 OCR"


def test_warm_up_ocr_nonblocking_spawns_and_returns_immediately(monkeypatch):
    monkeypatch.setattr(config, "DETECT_USE_OCR", True)
    events = []

    def fake_reader():
        events.append("init")
        time.sleep(0.2)  # 模拟重初始化耗时
        return None

    monkeypatch.setattr(det, "_get_ocr_reader", fake_reader)

    t0 = time.perf_counter()
    det.warm_up_ocr(blocking=False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.1, "非阻塞模式应在 0.1s 内返回（不等待初始化）"
    # 后台线程稍后完成初始化。
    deadline = time.perf_counter() + 2.0
    while "init" not in events and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert events == ["init"], "非阻塞模式应丢到后台线程初始化 OCR"


def test_warm_up_ocr_blocking_initializes_synchronously(monkeypatch):
    monkeypatch.setattr(config, "DETECT_USE_OCR", True)
    calls = []
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: calls.append(1) or None)
    det.warm_up_ocr(blocking=True)
    assert calls == [1], "阻塞模式应同步初始化 OCR"


def test_warm_up_ocr_repeated_calls_safe(monkeypatch):
    monkeypatch.setattr(config, "DETECT_USE_OCR", True)
    calls = []
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: calls.append(1) or None)
    # 重复调用绝不抛异常（真实幂等由 _get_ocr_reader 内部 _OCR_READER 缓存保证）。
    for _ in range(3):
        det.warm_up_ocr(blocking=True)
    assert len(calls) == 3
