# -*- coding: utf-8 -*-
"""阶段 320e · 设备选择惰性化守卫：OpenCV 引擎不得触发 `select_device()`。

实测背景（2026-10-23）：`select_device()` = **1.66s** 且会 `import torch`（结果有缓存）。
OpenCV 引擎的实现根本不看 device，LaMa / 插件引擎在入口自行 `device or select_device()`，
所以跳过是安全的。**收益条件要说清**：默认 OCR 开启时，检测阶段已经选过设备（缓存命中），
改动省不到什么；真正省的是 `DETECT_USE_OCR=false`（或未来不含文字策略的预设）——
实测 OCR 关闭时首图链路 **7.64s → 3.27s（−4.4s，−57%）**，日志不再出现「自动选择设备」。

本文件只测**接线**（谁在什么条件下被调用），不加载模型、不跑推理：
把 `_run_engine_once` 换成原样返回，统计 `select_device` 的调用次数。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import core.inpainter as ip  # noqa: E402


@pytest.fixture
def counters(monkeypatch):
    calls = {"device": 0}

    def _fake_select():
        calls["device"] += 1
        return "FAKE_DEVICE"

    def _fake_run(engine, image, mask, device, progress, **kw):
        calls["last_device"] = device
        return image.copy()

    monkeypatch.setattr(ip, "select_device", _fake_select)
    monkeypatch.setattr(ip, "_run_engine_once", _fake_run)
    return calls


def _img():
    return np.full((64, 64, 3), 128, np.uint8)


def _mask():
    m = np.zeros((64, 64), np.uint8)
    m[16:32, 16:32] = 255
    return m


def test_opencv_engine_skips_device_selection(counters):
    ip.inpaint(_img(), _mask(), engine="opencv")
    assert counters["device"] == 0, (
        "OpenCV 引擎仍在选设备：白白付出 1.66s 与 torch 导入（输出不变）")
    assert counters.get("last_device") is None, "OpenCV 引擎应收到 device=None"


def test_model_engine_still_selects_device(counters):
    ip.inpaint(_img(), _mask(), engine="lama")
    assert counters["device"] == 1, "模型引擎必须照旧选设备（否则 LaMa 会缺设备句柄）"
    assert counters.get("last_device") == "FAKE_DEVICE"


def test_explicit_device_is_respected(counters):
    ip.inpaint(_img(), _mask(), engine="lama", device="GIVEN")
    assert counters["device"] == 0, "调用方显式给 device 时不应再选一次"
    assert counters.get("last_device") == "GIVEN"
