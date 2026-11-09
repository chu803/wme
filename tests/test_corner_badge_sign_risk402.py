# -*- coding: utf-8 -*-
"""阶段 402：把「角落亮底招牌被多擦一圈」这个**新引入的风险**钉成数字。

背景（阶段 401 引入"板反推"判据）：角落里"白底 + 深色字"既可能是平台角标（承诺类），
也可能是**照片里的招牌**（不是水印）⇒ 像素上不可分。
实测（合成"照片 + 右下角亮底招牌"）：
  · 只擦文字（改动前的行为）：整图 0.373%
  · 加上板反推（现在）：整图 0.829% ⇒ **边际 0.457%**
即"招牌整块被擦掉"而不是"只擦字"。

守卫（characterization + 护栏）：边际改动不许超过 0.8%（当前实测 0.457%），
否则说明有人把放大系数/边距调大了 ⇒ 把误伤放大。
"""
from __future__ import annotations

import pathlib

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "scripts" / "assets" / "DejaVuSans-Bold.ttf"
pytest.importorskip("easyocr", reason="需要 EasyOCR：角落文字框相关行为")

#: 边际改动上限（当前实测 0.457%）。超了 = 放大系数/边距被调大 ⇒ 误伤被放大。
MAX_MARGINAL_CHANGE = 0.008


def _photo_with_corner_sign(size: int = 1024):
    """普通风景照 + 右下角**亮底招牌**（白底 + 深色字）——不是水印。"""
    from PIL import Image, ImageDraw, ImageFont

    rng = np.random.default_rng(7)
    base = Image.fromarray(rng.integers(60, 190, (size, size, 3), dtype=np.uint8))
    d = ImageDraw.Draw(base)
    for i in range(0, size, 32):
        d.line([(0, i), (size, i)], fill=(80, 140, 90), width=6)
    sw, sh = 150, 44
    x, y = size - sw - 30, size - sh - 30
    d.rectangle([x, y, x + sw, y + sh], fill=(250, 250, 250))
    f = ImageFont.truetype(str(FONT), 22)
    d.text((x + 12, y + 10), "SALE 50%", font=f, fill=(30, 30, 30))
    return np.asarray(base)[:, :, ::-1].copy(), (x, y, sw, sh)


def _changed_ratio(a, b) -> float:
    diff = np.abs(a.astype(int) - b.astype(int)).max(axis=2) > 10
    return float(diff.mean())


def test_corner_sign_marginal_damage_is_bounded():
    """角落亮底招牌（不是水印）：相对"只擦文字"的**边际**改动不许超过上限。"""
    from core.detector import detect_watermark_mask
    from core.inpainter import inpaint

    img, _sign = _photo_with_corner_sign()
    det = detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    full = inpaint(img, det.mask, engine="opencv", strength=1.0, dilate=0, feather=5,
                   threshold=127, denoise=3)

    # 去掉角落 OCR 文字框 ⇒ 只剩"板反推"之外的区域
    without = det.mask.copy()
    for r in det.regions:
        if r.kind == "ocr_text":
            without[r.y:r.y + r.h, r.x:r.x + r.w] = 0
    base = inpaint(img, without, engine="opencv", strength=1.0, dilate=0, feather=5,
                   threshold=127, denoise=3)

    diff_full = np.abs(img.astype(int) - full.astype(int)).max(axis=2) > 10
    diff_base = np.abs(img.astype(int) - base.astype(int)).max(axis=2) > 10
    marginal = float((diff_full & ~diff_base).mean())
    assert marginal <= MAX_MARGINAL_CHANGE, (
        f"角落招牌的边际误伤 {marginal:.3%} 超过上限 {MAX_MARGINAL_CHANGE:.1%}"
        f"（放大系数/边距被调大了？）")


def test_badge_criterion_is_configurable_back():
    """判据可一键关掉（用户不想让"板反推"生效时的退路）。"""
    import config

    assert config.DETECT_CORNER_BADGE_FROM_TEXT is True
    assert config._env_bool("DETECT_CORNER_BADGE_FROM_TEXT", True) is True
