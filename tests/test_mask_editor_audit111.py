# -*- coding: utf-8 -*-
"""
阶段 111 · `core/mask_editor` 真值审计守卫。

背景
----
`core/mask_editor` 是纯 numpy 掩码代数层（merge_masks / apply_brush / apply_rect）。
阶段 111 的探针（25 项，覆盖既有测试未触达的路径）证明**核心代数正确、无真缺陷**；
本文件把这些「未测但正确」的不变量钉成机器可判定的守卫，避免日后回归。

钉死的不变量（均为探针实证过的正确行为）：
  · RGB 亮度通道 / RGBA alpha 通道的二值化；
  · manual 模式在 det 或 user 为空时的边界语义；
  · 精确上/下采样（最近邻）的像素保真；
  · 浮点圆心四舍五入、模式大小写不敏感、输出恒为纯 0/255 uint8；
  · 负半径/反向矩形/1D 输入等退化输入的健壮性。

⚠ 特别钉死一条「看似缺陷其实正确」的项：`target_shape` 大于掩码时，
最近邻上采样会放大掩码面积（像素数不守恒）——这是 `target_shape` 的语义，
不是缺陷。本文件用断言显式记录这一点，防止后人误把它当 bug 修掉（经验：别为
「面积不守恒」硬改 resize，会破坏坐标对齐）。
"""
import numpy as np
import pytest

from core.mask_editor import (
    merge_masks,
    apply_brush,
    apply_rect,
    MASK_MODE_MERGE,
    MASK_MODE_MANUAL,
    count_mask_pixels,
)


# ------------------------------------------------------------------------------
# 1) 输入二值化：RGB 亮度 / RGBA alpha
# ------------------------------------------------------------------------------
def test_rgb_luminance_path():
    """RGB（3 通道）按亮度二值化：白块=255、近黑(亮度>0)=255、纯黑=0。"""
    rgb = np.zeros((30, 30, 3), dtype=np.uint8)
    rgb[5:15, 5:15] = (255, 255, 255)
    rgb[20:25, 20:25] = (10, 0, 0)          # 亮度 10 -> >0 -> 计入
    out = merge_masks(rgb, None, MASK_MODE_MERGE)
    assert out.shape == (30, 30)
    assert out[10, 10] == 255
    assert out[22, 22] == 255
    assert out[0, 0] == 0


def test_rgba_alpha_zero_not_counted():
    """RGBA：alpha=0 的透明区域不计入，alpha=255 的不透明区域计入。"""
    rgba = np.zeros((20, 20, 4), dtype=np.uint8)
    rgba[2:6, 2:6, 3] = 0
    rgba[10:14, 10:14, 3] = 255
    out = merge_masks(rgba, None, MASK_MODE_MERGE)
    assert out[4, 4] == 0
    assert out[12, 12] == 255


# ------------------------------------------------------------------------------
# 2) manual 模式的边界语义
# ------------------------------------------------------------------------------
def test_manual_empty_det_returns_user():
    """manual 模式 + 检测为空：user ∩ ¬空 = user（完整保留用户笔迹）。"""
    user = np.zeros((60, 60), dtype=np.uint8)
    user[10:20, 10:20] = 255
    m = merge_masks(None, user, MASK_MODE_MANUAL)
    assert m.shape == (60, 60)
    assert count_mask_pixels(m) == 100
    assert m[15, 15] == 255


def test_manual_empty_user_returns_empty():
    """manual 模式 + 用户为空：没有可保留的笔迹 -> 空掩码。"""
    det = np.zeros((60, 60), dtype=np.uint8)
    det[10:20, 10:20] = 255
    m = merge_masks(det, None, MASK_MODE_MANUAL)
    assert count_mask_pixels(m) == 0


def test_merge_empty_user_returns_det():
    """merge 模式 + 用户为空：检测 ∪ 空 = 检测。"""
    det = np.zeros((60, 60), dtype=np.uint8)
    det[10:20, 10:20] = 255
    m = merge_masks(det, None, MASK_MODE_MERGE)
    assert count_mask_pixels(m) == 100 and m[15, 15] == 255


def test_manual_empty_det_with_target_shape_upscales():
    """manual + det 为空 + target_shape 更大：user 上采样到 target。

    ⚠ 像素数不守恒（10×10 方块最近邻放大成 ~20×20）是 `target_shape` 的
    预期语义，不是缺陷——钉死此行为，防止日后被误修。
    """
    user = np.zeros((60, 60), dtype=np.uint8)
    user[10:20, 10:20] = 255
    m = merge_masks(None, user, MASK_MODE_MANUAL, target_shape=(120, 120))
    assert m.shape == (120, 120)
    assert m[25, 25] == 255 and m[5, 5] == 0
    assert 200 <= count_mask_pixels(m) <= 400


# ------------------------------------------------------------------------------
# 3) 精确上/下采样（最近邻）的像素保真
# ------------------------------------------------------------------------------
def test_upsample_preserves_all_pixels():
    """(2,2) 全 255 -> (4,4) 全 255（上采样应保留每个源像素）。"""
    small = np.full((2, 2), 255, dtype=np.uint8)
    m = merge_masks(small, None, MASK_MODE_MERGE, target_shape=(4, 4))
    assert m.shape == (4, 4)
    assert count_mask_pixels(m) == 16


def test_downsample_keeps_single_point():
    """(3,3) 仅 [0,0]=255 -> (1,1) 仍保留该点（最近邻采样落在源 [0,0]）。"""
    pt = np.zeros((3, 3), dtype=np.uint8)
    pt[0, 0] = 255
    m = merge_masks(pt, None, MASK_MODE_MERGE, target_shape=(1, 1))
    assert count_mask_pixels(m) == 1


# ------------------------------------------------------------------------------
# 4) 健壮性：浮点坐标、模式大小写、输出纯度、退化输入
# ------------------------------------------------------------------------------
def test_brush_float_center_rounds():
    """浮点圆心 40.4,40.4 应四舍五入为 (40,40)，且不修改原掩码。"""
    base = np.zeros((80, 80), dtype=np.uint8)
    b = apply_brush(base, 40.4, 40.4, 10)
    assert b[40, 40] == 255 and b[30, 40] == 255
    assert base[40, 40] == 0


def test_mode_case_insensitive():
    """模式名大小写不敏感（'MERGE' / 'Manual' 均正确解析）。"""
    det = np.zeros((100, 100), dtype=np.uint8); det[10:20, 10:20] = 255
    user = np.zeros((100, 100), dtype=np.uint8); user[40:50, 40:50] = 255
    assert count_mask_pixels(merge_masks(det, user, "MERGE")) == 200
    m = merge_masks(det, user, "Manual")
    assert count_mask_pixels(m) == 100 and m[15, 15] == 0 and m[45, 45] == 255


@pytest.mark.parametrize("mode", [MASK_MODE_MERGE, MASK_MODE_MANUAL])
def test_output_is_pure_binary_uint8(mode):
    """两种模式的输出恒为 uint8 且只含 0/255（无中间值）。"""
    det = np.zeros((100, 100), dtype=np.uint8); det[10:20, 10:20] = 255
    user = np.zeros((100, 100), dtype=np.uint8); user[40:50, 40:50] = 255
    m = merge_masks(det, user, mode)
    assert m.dtype == np.uint8
    assert set(np.unique(m).tolist()) <= {0, 255}


def test_brush_negative_radius_returns_copy():
    """负半径画笔：安全返回副本（不修改、不报错）。"""
    base = np.zeros((20, 20), dtype=np.uint8)
    b = apply_brush(base, 5, 5, -3)
    assert count_mask_pixels(b) == 0 and b is not base


def test_rect_reversed_range_noop():
    """反向矩形 (x0>x1 / y0>y1) 应无操作。"""
    base = np.zeros((10, 10), dtype=np.uint8)
    assert count_mask_pixels(apply_rect(base, 2, 8, 6, 3)) == 0


def test_float_mask_one_zero():
    """浮点掩码：1.0 视为掩码、0.0 视为空。"""
    fmask = np.zeros((10, 10), dtype=np.float32)
    fmask[0:3, 0:3] = 1.0
    assert count_mask_pixels(merge_masks(fmask, None, MASK_MODE_MERGE)) == 9


def test_manual_partial_overlap_strips_only_overlap():
    """manual 部分重叠：与检测层重叠的用户笔迹被剥离，独有笔迹保留。"""
    det = np.zeros((100, 100), dtype=np.uint8); det[0:10, 0:10] = 255
    user = np.zeros((100, 100), dtype=np.uint8)
    user[0:10, 0:10] = 255       # 重叠 -> 剥离
    user[20:30, 20:30] = 255     # 独有 -> 保留
    m = merge_masks(det, user, MASK_MODE_MANUAL)
    assert m[5, 5] == 0
    assert m[25, 25] == 255
    assert count_mask_pixels(m) == 100


def test_1d_input_raises():
    """1D 输入（非法维度）应抛 ValueError，而非静默产出错误掩码。"""
    with pytest.raises(ValueError):
        merge_masks(np.zeros((100,), dtype=np.uint8), None, MASK_MODE_MERGE)
