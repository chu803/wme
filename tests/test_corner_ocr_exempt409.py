# -*- coding: utf-8 -*-
"""阶段 409（#42）：角落高置信 OCR 豁免证据二次确认。

构造「整体有纹理（证据场非退化）+ 右下角纯暗块（框内无证据）」的合成图，
mock OCR 在右下角搜索区返回一个文字框。断言：
  · 豁免开（默认）：角落框保留为掩码（不被证据二次确认整框丢弃）；
  · 豁免关：角落框被证据二次确认丢弃（复现修复前的漏检）。
不依赖真实 OCR / 真实图，ci311venv 可跑（cv2/numpy）。
"""
from __future__ import annotations

import numpy as np
import pytest

import config
from core import detector as D

cv2 = pytest.importorskip("cv2")


def _make_img():
    rng = np.random.default_rng(7)
    img = (rng.random((512, 512, 3)) * 200 + 30).astype(np.uint8)  # 纹理→证据非退化
    img[384:, 384:] = 20  # 右下角纯暗块：框内无证据
    return img


def _mock_ocr(monkeypatch):
    # 仅在右下角搜索区（px=py=384）返回一个文字框，其余搜索区无输出。
    def fake(patch, px, py, accept_low_conf=False):
        if px == 384 and py == 384:
            return [(400, 440, 80, 40)]
        return []
    monkeypatch.setattr(D, "_ocr_boxes_in_patch", fake)
    monkeypatch.setattr(D, "_ocr_available", lambda: True)
    # 强制走证据二次确认通路，且模拟「框内无证据」⇒ 非豁免 OCR 框被整框丢弃。
    monkeypatch.setattr(D, "_evidence_is_degenerate", lambda ev: False)
    monkeypatch.setattr(D, "_ocr_box_rescue_mask",
                        lambda regs, ev, h, w, pad: np.zeros((h, w), np.uint8))


def _br_mask_px(img, exempt):
    config.DETECT_OCR_CORNER_EXEMPT = exempt
    try:
        res = D.detect_watermark_mask(img, use_ocr=True, preset="general")
    finally:
        config.DETECT_OCR_CORNER_EXEMPT = True
    return int((res.mask[440:480, 400:480] > 0).sum())


def test_corner_ocr_exempt_keeps_box(monkeypatch):
    _mock_ocr(monkeypatch)
    img = _make_img()
    assert _br_mask_px(img, exempt=True) > 0, "豁免开时角落 OCR 框应保留为掩码"


def test_corner_ocr_no_exempt_drops_box(monkeypatch):
    _mock_ocr(monkeypatch)
    img = _make_img()
    assert _br_mask_px(img, exempt=False) == 0, "豁免关时证据二次确认应丢弃无证据角落框（复现漏检）"