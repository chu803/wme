# -*- coding: utf-8 -*-
"""阶段 314b · 预填图层「丢 alpha」回归守卫。

真实故障（UI 端到端实测发现）：
  检测完成后，`_mask_to_rgba_layer` 把掩码包装成红色图层预填进 ImageEditor。
  **Gradio 6.x 在往返时把图层压成 RGB、丢弃 alpha**——浏览器实测回传
  `shape=[2048,2048,3]`、`rgb_mean=[255,59,48]`（整层纯红）。
  原实现「RGB 全图刷红 + alpha 表达掩码」⇒ 灰度恒 ≈116 > 阈值 10 ⇒ **整图被判为涂抹区**
  ⇒ 一键修复重绘整张图（实测改动了 82.11% 像素、状态栏显示「你补画 4,134,147 像素」）。

本守卫模拟「alpha 被丢弃」的服务端形态，断言掩码只覆盖原始水印区域。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.mask_generator import build_mask_from_editor, count_mask_pixels  # noqa: E402
from ui.tab_auto import _mask_to_rgba_layer                                 # noqa: E402


def _layer_as_gradio_returns_it(mask: np.ndarray) -> np.ndarray:
    """复刻 Gradio 6.x 的往返结果：RGBA → RGB（alpha 丢弃）。"""
    return _mask_to_rgba_layer(mask)[:, :, :3].copy()


def test_alpha_loss_keeps_mask_area():
    mask = np.zeros((128, 128), np.uint8)
    mask[20:40, 30:60] = 255                       # 20×30 = 600 px
    built = build_mask_from_editor(
        {"background": None, "layers": [_layer_as_gradio_returns_it(mask)], "composite": None},
        brush_threshold=10,
        target_size=(128, 128),
    )
    assert count_mask_pixels(built) == 600, (
        f"丢 alpha 后掩码应只有 600px，实得 {count_mask_pixels(built)}px "
        f"（整图 16384px）——预填图层的 RGB 背景必须为黑，不能全图刷色"
    )


def test_alpha_loss_empty_mask_stays_empty():
    """零掩码（未检出）不得因丢 alpha 变成「整图涂抹」。"""
    mask = np.zeros((64, 64), np.uint8)
    built = build_mask_from_editor(
        {"background": None, "layers": [_layer_as_gradio_returns_it(mask)], "composite": None},
        brush_threshold=10,
        target_size=(64, 64),
    )
    assert count_mask_pixels(built) == 0


def test_rgba_path_still_uses_alpha():
    """RGBA 通路（alpha 未丢）行为不变：按 alpha 取掩码。"""
    mask = np.zeros((64, 64), np.uint8)
    mask[5:15, 5:15] = 255
    built = build_mask_from_editor(
        {"background": None, "layers": [_mask_to_rgba_layer(mask)], "composite": None},
        brush_threshold=10,
        target_size=(64, 64),
    )
    assert count_mask_pixels(built) == 100


@pytest.mark.parametrize("size", [(512, 512), (2048, 2048)])
def test_no_full_canvas_fill_at_real_sizes(size):
    """真实尺寸下也不得把整幅画布当掩码（防止小图侥幸通过）。"""
    h, w = size
    mask = np.zeros((h, w), np.uint8)
    mask[10:20, 10:20] = 255
    built = build_mask_from_editor(
        {"background": None, "layers": [_layer_as_gradio_returns_it(mask)], "composite": None},
        brush_threshold=10,
        target_size=(h, w),
    )
    assert count_mask_pixels(built) == 100


def test_user_brush_layer_survives_alpha_loss():
    """**用户手涂路径**同等安全：浏览器画出来的图层是「透明底 + 不透明笔迹」，
    丢 alpha 后底为黑、笔迹为画笔色 ⇒ 掩码只含笔迹。

    与预填图层的区别正在这里：预填当年是「整层刷色 + 靠 alpha 区分」，丢 alpha 即全图误判；
    用户笔迹由浏览器绘制，透明区本来就是 0 值，压成 RGB 后仍为黑 ⇒ 天然安全。
    本用例把这条区别钉死，避免有人把预填「改回全刷色」时连带认为手涂也没问题。
    """
    h, w = 200, 300
    layer = np.zeros((h, w, 4), np.uint8)                    # 透明底
    layer[50:70, 100:160] = (255, 59, 48, 255)              # 一段笔迹（20×60）
    rgb = layer[:, :, :3].copy()                             # 浏览器往返：丢 alpha
    built = build_mask_from_editor(
        {"background": None, "layers": [rgb], "composite": None},
        brush_threshold=10,
        target_size=(h, w),
    )
    assert count_mask_pixels(built) == 1200, (
        f"手涂掩码应等于笔迹面积 1200px，实得 {count_mask_pixels(built)}"
    )
