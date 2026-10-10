# -*- coding: utf-8 -*-
"""
阶段 282 · `core/mask_editor` 深度续审（5j 复审链延续，接 276–281）。

首轮审计（阶段 24 基础单测 + 阶段 111 真值审计 + 掩码代数自检）已覆盖：
  · merge_masks 的 merge 并集 / manual 剥离检测层 / None·空分支 / 尺寸对齐 / 未知模式回退；
  · _as_binary 的 RGB 亮度 / RGBA alpha / 浮点掩码 / 1D 抛错；
  · _canonical 的 manual 空 det→user / 空 user→空 / target_shape 上采样（并显式记录
    「面积不守恒是语义非缺陷」）；
  · _resize_binary 的最近邻上/下采样保真；输出恒 uint8 纯 0/255；模式大小写不敏感；
  · apply_brush 增删·不修改原掩码·负半径·越界·浮点圆心；apply_rect 增删·边界夹取·反向区间。

本轮按「契约输入类别枚举」钉死**首轮遗漏的成功/边界分支不变量**（探针实证全部正确，零真缺陷）：
  · apply_brush / apply_rect 的**非 2D 输入 → ValueError**（含 3D / 1D）；
  · 浮点半径 `int(round(r))` 的**银行家舍入**边界：0.4/0.5→0（无操作）、0.6→1、1.5→2；负值无操作；
  · apply_rect 浮点坐标 `int()` **截断** + 半开区间语义；
  · target_shape 含 0 / 负维度 → **被忽略**，回退到掩码自身尺寸；
  · mode=None / "" / 0 → merge；**带空白**的 "merge " 经「未知模式回退」仍得 merge（结果正确）；
  · det/user 尺寸不同且无 target_shape → **逐维取 max**（可能大于任一输入）；
  · 画笔圆心贴四角/边缘均在界内正常工作（越界保护不误伤合法边缘）；
  · apply_rect 负坐标夹取到 0 / 整幅覆盖 / 贴边退化区间无操作；
  · _as_binary 对 (H,W,1) 与 (H,W,2) 走亮度均值分支；(H,W,4) 走 alpha 分支；
  · 布尔掩码 → uint8；负值掩码 → 视作空（`>0` 为 False）。

零真缺陷（与 276–281 同构）：首轮遗漏的是「未钉边界」而非代码缺陷。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.mask_editor import (  # noqa: E402
    merge_masks,
    apply_brush,
    apply_rect,
    _as_binary,
    MASK_MODE_MERGE,
    MASK_MODE_MANUAL,
    count_mask_pixels,
)


def _z(h=20, w=20):
    return np.zeros((h, w), np.uint8)


# --------------------------------------------------------------------------- #
# 1 · apply_brush / apply_rect 非 2D 输入 → ValueError
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", [(4, 4, 3), (10,), (2, 2, 2, 2)])
def test_brush_non_2d_raises(shape):
    """非 (H,W) 掩码喂给画笔必须显式拒绝（不得静默产出错误掩码）。"""
    with pytest.raises(ValueError):
        apply_brush(np.zeros(shape, np.uint8), 1, 1, 2)


@pytest.mark.parametrize("shape", [(4, 4, 3), (10,)])
def test_rect_non_2d_raises(shape):
    with pytest.raises(ValueError):
        apply_rect(np.zeros(shape, np.uint8), 0, 0, 2, 2)


# --------------------------------------------------------------------------- #
# 2 · 浮点半径的银行家舍入边界
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("radius, expect_painted", [
    (0.0, 0), (0.4, 0), (0.5, 0),    # round→0 ⇒ 无操作
    (0.6, 5),                        # round→1 ⇒ 画一个十字盘（r=1 → 5 px）
    (1.5, 13),                       # round→2（银行家舍入到偶数）
    (-0.6, 0),                       # 负半径无操作
])
def test_brush_float_radius_rounding(radius, expect_painted):
    out = apply_brush(_z(40, 40), 20, 20, radius)
    assert count_mask_pixels(out) == expect_painted, f"radius={radius} 绘制像素数不符"


def test_brush_negative_radius_returns_copy():
    base = _z(20, 20)
    out = apply_brush(base, 5, 5, -3)
    assert out is not base and count_mask_pixels(out) == 0


# --------------------------------------------------------------------------- #
# 3 · apply_rect 浮点坐标 int() 截断 + 半开区间
# --------------------------------------------------------------------------- #
def test_rect_float_coords_truncate_half_open():
    out = apply_rect(_z(40, 40), 1.9, 1.9, 6.9, 6.9)
    ys, xs = np.nonzero(out)
    assert (ys.min(), ys.max()) == (1, 5), "坐标 int() 截断 + 半开区间 [y0,y1)"
    assert (xs.min(), xs.max()) == (1, 5)
    assert count_mask_pixels(out) == 25


def test_rect_negative_coords_clip_to_zero():
    out = apply_rect(_z(20, 20), -10, -10, 5, 5)
    assert count_mask_pixels(out) == 25, "负坐标须夹取到 0，不得为负索引"


def test_rect_covering_whole_image():
    out = apply_rect(_z(20, 20), 0, 0, 100, 100)
    assert count_mask_pixels(out) == 400, "越界矩形应夹取为整幅覆盖"


@pytest.mark.parametrize("rect", [(20, 20, 5, 5), (5, 20, 20, 5), (5, 5, 5, 5)])
def test_rect_degenerate_ranges_noop(rect):
    assert count_mask_pixels(apply_rect(_z(20, 20), *rect)) == 0


# --------------------------------------------------------------------------- #
# 4 · target_shape 含 0 / 负维度 → 忽略并回退
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ts", [(0, 100), (100, 0), (-5, 100), (100, -5)])
def test_target_shape_nonpositive_dims_ignored(ts):
    det = _z(20, 20); det[5:10, 5:10] = 255
    out = merge_masks(det, None, MASK_MODE_MERGE, target_shape=ts)
    assert out.shape == (20, 20), f"target_shape={ts} 应被忽略，回退掩码自身尺寸"
    assert count_mask_pixels(out) == 25


# --------------------------------------------------------------------------- #
# 5 · mode 缺省 / 空白
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", [None, "", 0, False])
def test_falsy_mode_defaults_to_merge(mode):
    det = _z(20, 20); det[5:10, 5:10] = 255
    user = _z(20, 20); user[15:18, 15:18] = 255
    out = merge_masks(det, user, mode)
    assert count_mask_pixels(out) == 34, "falsy mode 应走 merge 并集（25+9）"


def test_whitespace_padded_mode_still_resolves_to_merge():
    """'merge '（带尾空白）不精确匹配 ⇒ 走「未知模式回退」，结果仍是 merge。"""
    det = _z(20, 20); det[5:10, 5:10] = 255
    user = _z(20, 20); user[15:18, 15:18] = 255
    padded = merge_masks(det, user, "merge ")
    exact = merge_masks(det, user, MASK_MODE_MERGE)
    assert np.array_equal(padded, exact), "空白模式回退后必须与 merge 结果一致"


# --------------------------------------------------------------------------- #
# 6 · 无 target_shape 时逐维取 max
# --------------------------------------------------------------------------- #
def test_canonical_takes_per_dimension_max():
    """det(10,50) × user(50,10) → 逐维 max 得 (50,50)（可能大于任一输入）。"""
    a = np.zeros((10, 50), np.uint8); a[0:2, 0:2] = 255
    b = np.zeros((50, 10), np.uint8); b[0:2, 0:2] = 255
    out = merge_masks(a, b, MASK_MODE_MERGE)
    assert out.shape == (50, 50)


# --------------------------------------------------------------------------- #
# 7 · 画笔圆心贴边（合法边缘不得被越界保护误伤）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cy, cx", [(0, 0), (39, 39), (0, 20), (20, 0)])
def test_brush_center_on_edge_paints(cy, cx):
    out = apply_brush(_z(40, 40), cy, cx, 5)
    assert out[cy, cx] == 255, f"圆心 ({cy},{cx}) 在界内，必须绘制"
    assert count_mask_pixels(out) > 0


# --------------------------------------------------------------------------- #
# 8 · _as_binary 通道分支
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", [(8, 8, 1), (8, 8, 2)])
def test_as_binary_non_rgba_3d_uses_luminance(shape):
    arr = np.zeros(shape, np.uint8); arr[2:5, 2:5] = 200
    out = _as_binary(arr)
    assert out.shape == (8, 8), "非 4 通道的 3D 输入走亮度均值 → (H,W)"
    assert count_mask_pixels(out) == 9


def test_as_binary_rgba_uses_alpha_not_luminance():
    """RGBA 必须取 alpha，而非 RGB 亮度（alpha=0 但 RGB=255 仍应判空）。"""
    rgba = np.zeros((8, 8, 4), np.uint8)
    rgba[:, :, :3] = 255       # RGB 全白
    rgba[:, :, 3] = 0          # 但完全透明
    assert count_mask_pixels(_as_binary(rgba)) == 0, "RGBA 取 alpha：透明即空"


# --------------------------------------------------------------------------- #
# 9 · 布尔 / 负值掩码
# --------------------------------------------------------------------------- #
def test_bool_mask_binarized_to_uint8():
    bools = np.zeros((8, 8), bool); bools[1:3, 1:3] = True
    out = merge_masks(bools, None, MASK_MODE_MERGE)
    assert out.dtype == np.uint8 and count_mask_pixels(out) == 4


def test_negative_mask_treated_as_empty():
    """负值掩码：`(>0)` 判定 ⇒ 负值视作空（不抛错、不计入）。"""
    neg = np.full((8, 8), -5, np.int16)
    assert count_mask_pixels(merge_masks(neg, None, MASK_MODE_MERGE)) == 0