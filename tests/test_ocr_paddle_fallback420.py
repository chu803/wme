# -*- coding: utf-8 -*-
"""阶段 420：PaddleOCR 兜底通路的单元测试（mock，不依赖真装 paddleocr）。

覆盖三条语义：
  ① EasyOCR 零产出 ⇒ 走 PaddleOCR 兜底并采纳其框；
  ② EasyOCR 有产出 ⇒ **不**调用 PaddleOCR（兜底只补漏，不添乱）；
  ③ PaddleOCR 不可用 ⇒ 维持原行为（空产出），主通路不受影响。
"""
from __future__ import annotations

import numpy as np
import pytest

import config
from core import detector as D


@pytest.fixture(autouse=True)
def _clean_cache():
    D._OCR_PATCH_CACHE.clear()
    yield
    D._OCR_PATCH_CACHE.clear()


def _patch(monkeypatch, easy_out, paddle_out, paddle_ok=True):
    monkeypatch.setattr(D, "_get_ocr_reader", lambda: object())
    monkeypatch.setattr(D, "_ocr_readtext", lambda r, b, t: easy_out)
    # 真函数里 _paddle_available 会先看配置开关，mock 必须保留这条语义。
    monkeypatch.setattr(
        D, "_paddle_available",
        lambda: bool(paddle_ok) and bool(config.DETECT_OCR_FALLBACK_PADDLE),
    )
    monkeypatch.setattr(D, "_paddle_boxes", lambda patch: paddle_out)


def test_fallback_used_when_easyocr_empty(monkeypatch):
    _patch(monkeypatch, easy_out=[], paddle_out=[(10, 20, 30, 15)])
    patch = np.zeros((40, 40, 3), np.uint8)
    boxes = D._ocr_boxes_in_patch(patch, 5, 7, accept_low_conf=True)
    assert boxes == [(10, 20, 30, 15)], "EasyOCR 零产出时应采纳 PaddleOCR 兜底框"


def test_fallback_not_called_when_easyocr_has_box(monkeypatch):
    called = []
    monkeypatch.setattr(D, "_get_ocr_reader", lambda: object())
    monkeypatch.setattr(D, "_ocr_readtext", lambda r, b, t: [
        ([[0, 0], [40, 0], [40, 20], [0, 20]], "x", 0.9),
    ])
    monkeypatch.setattr(D, "_paddle_available", lambda: True)
    monkeypatch.setattr(D, "_paddle_boxes", lambda patch: called.append(1) or [])
    patch = np.zeros((40, 40, 3), np.uint8)
    boxes = D._ocr_boxes_in_patch(patch, 5, 7, accept_low_conf=True)
    assert boxes, "EasyOCR 有产出时应保留其框"
    assert not called, "EasyOCR 已有产出时不应调用 PaddleOCR 兜底"


def test_no_fallback_when_paddle_unavailable(monkeypatch):
    _patch(monkeypatch, easy_out=[], paddle_out=[], paddle_ok=False)
    patch = np.zeros((40, 40, 3), np.uint8)
    assert D._ocr_boxes_in_patch(patch, 5, 7, accept_low_conf=True) == []


def test_paddle_fallback_config_switch_off(monkeypatch):
    old = config.DETECT_OCR_FALLBACK_PADDLE
    try:
        config.DETECT_OCR_FALLBACK_PADDLE = False
        _patch(monkeypatch, easy_out=[], paddle_out=[(1, 2, 3, 4)])
        patch = np.zeros((40, 40, 3), np.uint8)
        assert D._ocr_boxes_in_patch(patch, 5, 7, accept_low_conf=True) == []
    finally:
        config.DETECT_OCR_FALLBACK_PADDLE = old