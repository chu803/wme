# -*- coding: utf-8 -*-
"""阶段 123 真值审计守卫：core/mask_generator 的两处真缺陷。

F1（崩溃）：dict 图层循环对 (H,W,1) 图层误入 cvtColor(RGB2GRAY) 分支而崩溃；
    同模块的 _mask_to_gray_uint8 本就能处理 (H,W,1)，属内部不一致。
F2（静默丢数据）：dict 有 layers 但 background=None、target_size=None 时，
    旧逻辑直接返回 (1,1) 空掩码，丢弃用户已涂抹区域。

修复：图层灰度化统一走 _mask_to_gray_uint8；尺寸推断增加「首个有效图层」兜底。
判据：行为级（不依赖源码字符串），可与 test_legacy_selfcheck 互为印证。
"""
import sys
import os

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import mask_generator as mg
from utils.exceptions import InvalidMaskError


def _editor_with_layers(layers, background=None):
    return {"background": background, "layers": layers, "composite": background}


def test_f1_hw1_layer_dict_no_crash():
    """(H,W,1) 单通道图层在 dict 路径不得崩溃，且被正确二值化。"""
    layer1c = np.zeros((100, 100, 1), dtype=np.uint8)
    layer1c[10:20, 10:20, 0] = 200
    bg = np.zeros((100, 100, 3), dtype=np.uint8)
    out = mg.build_mask_from_editor(_editor_with_layers([layer1c], background=bg),
                                    brush_threshold=10)
    assert out.shape == (100, 100)
    assert mg.count_mask_pixels(out) == 10 * 10


def test_f1_hw1_layer_matches_internal_helper():
    """dict 路径对 (H,W,1) 图层的结果应与直接走 _mask_to_gray_uint8 一致。"""
    layer1c = np.zeros((100, 100, 1), dtype=np.uint8)
    layer1c[10:20, 10:20, 0] = 200
    bg = np.zeros((100, 100, 3), dtype=np.uint8)
    out = mg.build_mask_from_editor(_editor_with_layers([layer1c], background=bg),
                                    brush_threshold=10)
    ref = (mg._mask_to_gray_uint8(layer1c) > 10).astype(np.uint8) * 255
    assert np.array_equal(out, ref)


def test_f1_hw2_layer_raises_clean_error():
    """(H,W,2) 畸形图层应抛 InvalidMaskError，而非 OpenCV 底层崩溃。"""
    layer2c = np.zeros((100, 100, 2), dtype=np.uint8)
    layer2c[10:20, 10:20, 1] = 255
    bg = np.zeros((100, 100, 3), dtype=np.uint8)
    with pytest.raises(InvalidMaskError):
        mg.build_mask_from_editor(_editor_with_layers([layer2c], background=bg),
                                 brush_threshold=10)


def test_f2_dict_without_background_keeps_mask():
    """dict 无 background 时仍应从 layers 提取掩码，不得返回 (1,1) 空掩码。"""
    layer_a = np.zeros((100, 100, 4), dtype=np.uint8)
    layer_a[30:40, 30:40, 3] = 255
    out = mg.build_mask_from_editor(_editor_with_layers([layer_a], background=None),
                                    brush_threshold=10)
    assert out.shape == (100, 100)
    assert mg.count_mask_pixels(out) == 10 * 10


def test_f2_dict_no_bg_no_layers_returns_empty():
    """dict 既无 background 也无任何有效图层时，仍安全地返回空掩码。"""
    out = mg.build_mask_from_editor(_editor_with_layers([None], background=None),
                                    brush_threshold=10)
    assert mg.count_mask_pixels(out) == 0
