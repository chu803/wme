# -*- coding: utf-8 -*-
"""阶段 30X · 裁剪到水印包围盒修复（`INFER_CROP_TO_MASK`）的守卫。

背景：真实大图（2048²）上 LaMa fp32 整图 forward 是 full 管线主瓶颈（实测 4~8s）。
裁剪到「水印包围盒 + margin」后只送小图给引擎，实测 ~3.6× 加速。

⚠ 质量边界（实测）：裁剪**改变 mask 内的填充结果**——LaMa 对 crop vs 整图有尺寸相关
  行为（mask 内 ~0.5% 像素差 >10，不随 margin 缩小）；mask **外**逐字节不变。
  故该优化由 `INFER_CROP_TO_MASK` 门控、**默认关**，是否默认开由 ΔPSNR 门禁判决。

本守卫锁定：
  ① 默认关（零行为变更，不会被误改成 True）；
  ② 三条触发条件（flag / 图够大 / 掩码够小）各边界；
  ③ 裁剪修复确实「只送小图给引擎 + 填掉水印 + 周边逐字节不变」。
"""
from __future__ import annotations

import numpy as np
import pytest

import config
from core import inpainter as I


class _FakeEngine:
    """记录每次收到的图像形状、把掩码区填成纯色 7 的假引擎（无模型依赖，确定性）。"""

    def __init__(self) -> None:
        self.shapes: list = []

    def inpaint(self, image, mask, device=None, progress=None):
        self.shapes.append(image.shape[:2])
        out = image.copy()
        out[mask > 0] = 7
        return out


def test_crop_disabled_by_default():
    """默认关 ⇒ 零行为变更（守卫默认值不被误开）。"""
    assert config.INFER_CROP_TO_MASK is False


def test_should_crop_respects_flag(monkeypatch):
    img = np.zeros((2048, 2048, 3), np.uint8)
    mask = np.zeros((2048, 2048), np.uint8)
    mask[10:20, 10:20] = 255
    monkeypatch.setattr(config, "INFER_CROP_TO_MASK", False)
    assert I._should_crop(img, mask) is False
    monkeypatch.setattr(config, "INFER_CROP_TO_MASK", True)
    assert I._should_crop(img, mask) is True


def test_should_crop_rejects_large_mask(monkeypatch):
    """掩码占比过大 ⇒ 不裁（包围盒≈整图，裁了没意义）。"""
    monkeypatch.setattr(config, "INFER_CROP_TO_MASK", True)
    img = np.zeros((2048, 2048, 3), np.uint8)
    mask = np.full((2048, 2048), 255, np.uint8)
    assert I._should_crop(img, mask) is False


def test_should_crop_rejects_small_image(monkeypatch):
    """图长边小于下限 ⇒ 不裁（小图整图本就快）。"""
    monkeypatch.setattr(config, "INFER_CROP_TO_MASK", True)
    monkeypatch.setattr(config, "INFER_CROP_MIN_SIDE", 1024)
    img = np.zeros((512, 512, 3), np.uint8)
    mask = np.zeros((512, 512), np.uint8)
    mask[10:20, 10:20] = 255
    assert I._should_crop(img, mask) is False


def test_mask_bbox():
    m = np.zeros((100, 100), np.uint8)
    m[20:30, 40:60] = 255
    y0, y1, x0, x1 = I._mask_bbox(m)
    assert (y0, y1, x0, x1) == (20, 29, 40, 59)
    assert I._mask_bbox(np.zeros((10, 10), np.uint8)) is None


def test_inpaint_cropped_fills_watermark_and_keeps_rest(monkeypatch):
    """裁剪修复：引擎只收到**小图**（省时来源）+ 水印区被填 + 周边逐字节不变。"""
    monkeypatch.setattr(config, "INFER_CROP_MARGIN", 4)
    img = np.zeros((2048, 2048, 3), np.uint8)
    mask = np.zeros((2048, 2048), np.uint8)
    mask[1000:1010, 1000:1010] = 255
    eng = _FakeEngine()
    out = I._inpaint_cropped(eng, img, mask, None, lambda *a: None)
    assert len(eng.shapes) == 1
    sh = eng.shapes[0]
    assert sh[0] < 100 and sh[1] < 100, f"引擎应收小图，实得 {sh}"
    assert (out[mask > 0] == 7).all()
    assert np.array_equal(out[mask == 0], img[mask == 0])


def test_run_engine_once_routes_through_crop(monkeypatch):
    """接线守卫：flag 开 ⇒ `_run_engine_once` 走裁剪路径（引擎收到小图）。"""
    from unittest import mock
    monkeypatch.setattr(config, "INFER_CROP_TO_MASK", True)
    monkeypatch.setattr(config, "INFER_CROP_MARGIN", 4)

    fake_eng = _FakeEngine()
    with mock.patch.object(I.plugin_manager, "get_engine", return_value=fake_eng):
        fake_eng.requires_model = False
        img = np.zeros((2048, 2048, 3), np.uint8)
        mask = np.zeros((2048, 2048), np.uint8)
        mask[1000:1010, 1000:1010] = 255
        I._run_engine_once("opencv", img, mask, None, lambda *a: None, strict_setup=True)
    assert fake_eng.shapes and fake_eng.shapes[0][0] < 100, \
        f"接线未走裁剪：引擎收到 {fake_eng.shapes}"