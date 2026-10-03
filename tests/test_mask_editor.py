# -*- coding: utf-8 -*-
"""
阶段 24 交互式掩码精修 · 单元测试 + 服务层接线验证。

覆盖：
  · merge_masks     : 两种精修模式的掩码合并（merge 并集 / manual 剥离检测层）、
                      None/空分支、尺寸对齐、未知模式回退。
  · apply_brush     : 圆形画笔增/删，且不修改原掩码。
  · apply_rect      : 矩形增/删与边界夹取。
  · count_mask_pixels : 从 core.mask_generator 单源再导出，行为一致。
  · process_image    : 服务层 user_mask 透传接线 —— 不传时不改变旧行为（新增键默认
                      0 / 默认模式），传入时返回键完整且 auto 成功路径下统计正确。
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
from core.mask_generator import count_mask_pixels as mg_count
from services.auto_pipeline import process_image

import config


# ------------------------------------------------------------------------------
# merge_masks
# ------------------------------------------------------------------------------
def test_merge_union_detect_or_user():
    """merge 模式 = 检测 ∪ 用户，两个区域都进入最终掩码。"""
    det = np.zeros((100, 100), dtype=np.uint8)
    det[10:20, 10:20] = 255
    user = np.zeros((100, 100), dtype=np.uint8)
    user[40:50, 40:50] = 255
    m = merge_masks(det, user, MASK_MODE_MERGE)
    assert m.shape == (100, 100)
    assert count_mask_pixels(m) == 10 * 10 + 10 * 10
    assert m[15, 15] == 255 and m[45, 45] == 255 and m[60, 60] == 0


def test_manual_strips_detect_layer():
    """manual 模式 = 用户 ∩ ¬检测：剥离预填检测层，只留用户新画的笔迹。"""
    det = np.zeros((100, 100), dtype=np.uint8)
    det[10:20, 10:20] = 255                 # 检测层
    user = np.zeros((100, 100), dtype=np.uint8)
    user[10:20, 10:20] = 255                # 用户在检测层上 → 应被剥离
    user[15:25, 30:40] = 255                # 用户新画区域 → 应保留
    m = merge_masks(det, user, MASK_MODE_MANUAL)
    assert m[15, 15] == 0, "检测层像素应被剥离"
    assert m[20, 35] == 255, "用户新画区域应保留"
    assert count_mask_pixels(m) == 10 * 10  # 15:25 行 × 30:40 列


def test_merge_none_and_resize():
    """None/空分支与尺寸对齐：检测为空时返回用户掩码；小掩码最近邻放大保留区域。"""
    user = np.zeros((100, 100), dtype=np.uint8)
    user[10:20, 10:20] = 255
    assert merge_masks(None, None, MASK_MODE_MERGE).size == 0
    m = merge_masks(None, user, MASK_MODE_MERGE, target_shape=(100, 100))
    assert m.shape == (100, 100) and count_mask_pixels(m) == 10 * 10

    small = np.zeros((50, 50), dtype=np.uint8)
    small[0:5, 0:5] = 255
    m = merge_masks(small, user, MASK_MODE_MERGE, target_shape=(100, 100))
    assert m.shape == (100, 100)
    assert m[0, 0] == 255, "小掩码最近邻放大后应保留左上角区域"
    assert m[15, 15] == 255, "用户区域应保留"
    assert count_mask_pixels(m) >= 10 * 10 + 25


def test_merge_unknown_mode_fallback():
    """未知模式回退为 merge。"""
    det = np.zeros((100, 100), dtype=np.uint8)
    det[10:20, 10:20] = 255
    user = np.zeros((100, 100), dtype=np.uint8)
    user[40:50, 40:50] = 255
    m = merge_masks(det, user, "bogus")
    assert count_mask_pixels(m) == count_mask_pixels(
        merge_masks(det, user, MASK_MODE_MERGE)
    )


def test_merge_accepts_rgb_alpha_inputs():
    """_as_binary 应能把 RGBA/RGB 输入规整为二值（alpha/亮度 > 0 即视作掩码）。"""
    # RGBA：alpha=255 的方块
    rgba = np.zeros((40, 40, 4), dtype=np.uint8)
    rgba[5:15, 5:15, 3] = 255
    out = merge_masks(rgba, None, MASK_MODE_MERGE)
    assert out.shape == (40, 40)
    assert count_mask_pixels(out) == 10 * 10


# ------------------------------------------------------------------------------
# apply_brush / apply_rect
# ------------------------------------------------------------------------------
def test_brush_add_and_erase_no_mutate():
    """圆形画笔增删；原掩码不被修改。"""
    base = np.zeros((80, 80), dtype=np.uint8)
    brushed = apply_brush(base, 40, 40, 10)
    assert brushed[40, 40] == 255 and brushed[30, 40] == 255 and brushed[20, 40] == 0
    erased = apply_brush(brushed, 40, 40, 5, add=False)
    assert erased[40, 40] == 0 and erased[30, 40] == 255
    assert base[40, 40] == 0, "原掩码不应被修改"


def test_brush_out_of_bounds_noop():
    """圆心越界时画笔应无操作并返回副本。"""
    base = np.zeros((20, 20), dtype=np.uint8)
    out = apply_brush(base, -5, 5, 10)
    assert out.shape == (20, 20)
    assert count_mask_pixels(out) == 0
    out = apply_brush(base, 5, 5, 0)
    assert count_mask_pixels(out) == 0


def test_rect_add_erase_and_clip():
    """矩形增删与边界夹取：越界矩形自动夹到图像范围内。"""
    base = np.zeros((50, 50), dtype=np.uint8)
    r = apply_rect(base, 10, 10, 20, 20)
    assert count_mask_pixels(r) == 100
    r2 = apply_rect(r, 5, 5, 60, 60)  # 越界：仅覆盖行/列 5..49
    assert r2[5, 5] == 255 and r2[49, 49] == 255 and r2[4, 4] == 0
    assert count_mask_pixels(r2) == 45 * 45
    r3 = apply_rect(r2, 0, 0, 25, 25, add=False)
    assert count_mask_pixels(r3) == 45 * 45 - 20 * 20


def test_rect_invalid_range_noop():
    """退化矩形（y1<=y0 或 x1<=x0）应返回原掩码副本，不报错。"""
    base = np.zeros((10, 10), dtype=np.uint8)
    out = apply_rect(base, 5, 5, 5, 8)
    assert count_mask_pixels(out) == 0


# ------------------------------------------------------------------------------
# count_mask_pixels 再导出一致性
# ------------------------------------------------------------------------------
def test_count_mask_pixels_rexport_consistent():
    """mask_editor.count_mask_pixels 应与 mask_generator 单源实现完全一致。"""
    a = np.zeros((30, 30), dtype=np.uint8)
    a[2:8, 2:8] = 255
    assert count_mask_pixels(a) == mg_count(a) == 36


# ------------------------------------------------------------------------------
# 服务层接线（process_image user_mask 透传）
# ------------------------------------------------------------------------------
def test_process_image_backward_compat_keys(synthetic_image_mask):
    """不传 user_mask 时：旧调用方式完全可用，且返回字典新增键默认为 0 / 默认模式。"""
    img, mask = synthetic_image_mask
    res = process_image(
        img, mode="manual", mask=mask, engine="opencv", postprocess=False
    )
    assert res["status"] == "success"
    assert "user_mask_pixels" in res
    assert res["user_mask_pixels"] == 0
    assert "mask_mode" in res
    assert res["mask_mode"] == config.MASK_EDIT_DEFAULT_MODE
    # 形态约束：修复结果尺寸与输入一致
    assert res["result_rgb"].shape == img.shape


def test_process_image_user_mask_merge_threading(synthetic_image_mask):
    """auto 模式下传入 user_mask（merge）：返回键完整；若检测命中则统计正确。

    检测是否命中取决于环境（无 torch 时检测器可能跳过），因此只在 status==success
    时严格断言统计，skipped 时仅验证键存在，避免环境差异导致偶发失败。
    """
    img, mask = synthetic_image_mask
    res = process_image(
        img,
        mode="auto",
        engine="opencv",
        user_mask=mask,
        mask_mode="merge",
        postprocess=False,
    )
    assert "user_mask_pixels" in res
    assert "mask_mode" in res
    if res["status"] == "success":
        assert res["mask_mode"] == "merge"
        assert res["user_mask_pixels"] == int(count_mask_pixels(mask))
        assert res["result_rgb"].shape == img.shape
    else:
        # 至少要带掩码统计键，且未崩溃
        assert res["user_mask_pixels"] >= 0


def test_process_image_user_mask_manual_mode_key(synthetic_image_mask):
    """auto 模式下 user_mask + manual 模式：成功时应标记 manual 模式。"""
    img, mask = synthetic_image_mask
    res = process_image(
        img,
        mode="auto",
        engine="opencv",
        user_mask=mask,
        mask_mode="manual",
        postprocess=False,
    )
    assert res["mask_mode"] in (MASK_MODE_MANUAL, MASK_MODE_MERGE)  # 合法值
    if res["status"] == "success":
        assert res["mask_mode"] == "manual"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
