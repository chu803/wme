# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 22 AI 水印检测能力深化 v2 —— 单元测试
 --------------------------------------------------------------------------
 覆盖点：
   1. 网格状平铺水印检测（core.detector.detect_grid_watermark）
      · 合成规则网格图 → 命中 KIND_GRID 区域
   2. 全局渐变叠加水印检测（core.detector.detect_gradient_watermark）
      · 均匀灰底 + 平滑左右渐变 → 整图标记 + KIND_GRADIENT 区域
   3. 总入口门控（core.detector.detect_watermark_mask）
      · enable_grid=True 命中网格；默认关闭（False）时同一图不产生 KIND_GRID
      · enable_gradient=True 命中渐变；默认关闭时同一图不产生 KIND_GRADIENT
   4. OCR 文字识别精度（core.detector._ocr_boxes_in_patch）
      · 低置信框（< DETECT_OCR_MIN_CONFIDENCE）被过滤
      · OCR 不可用（reader=None）时安全返回空
   5. 命中率统计（core.detector.compute_detection_stats / DetectionResult.stats）
      · coverage_ratio = 掩码像素 / 整图面积；by_kind 计数正确；used_ocr 透传
      · detect_watermark_mask 返回结果始终附带 stats（向后兼容非空）

 设计约束：
   · 纯算法层测试，仅依赖 numpy / opencv / config，不加载 gradio / torch。
   · OCR 用例通过 monkeypatch 注入假 Reader，避免沙箱缺 EasyOCR 时误判。
==============================================================================
"""

from __future__ import annotations

import os
import sys

import numpy as np

# 路径加固：保证从仓库根运行也能 import。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
import core.detector as det
from core.detector import (
    KIND_GRADIENT,
    KIND_GRID,
    compute_detection_stats,
    detect_gradient_watermark,
    detect_grid_watermark,
    detect_watermark_mask,
)


# ==============================================================================
# 合成测试图构造器
# ==============================================================================
def _make_grid_image(gh=360, gw=360, gstep=38, gsize=12):
    """构造「规则网格状平铺水印」图（浅灰底 + 规则排布小方块）。"""
    cv2 = det._require_cv2()
    grid = np.full((gh, gw, 3), 235, dtype=np.uint8)
    for gy in range(gstep // 2, gh, gstep):
        for gx in range(gstep // 2, gw, gstep):
            grid[gy - gsize // 2: gy + gsize // 2,
                 gx - gsize // 2: gx + gsize // 2] = 110
    return grid


def _make_gradient_image(ph=200, pw=320):
    """构造「全局渐变叠加水印」图（均匀灰底 + 平滑左右渐变）。"""
    cv2 = det._require_cv2()
    base_g = np.full((ph, pw), 200, dtype=np.uint8)
    grad = np.tile(np.linspace(0, 90, pw, dtype=np.float32), (ph, 1))
    grad_img = np.clip(base_g.astype(np.float32) + grad, 0, 255).astype(np.uint8)
    return cv2.cvtColor(grad_img, cv2.COLOR_GRAY2RGB)


# ==============================================================================
# 第 1 组：网格状平铺水印检测（直接调用策略函数）
# ==============================================================================
def test_detect_grid_watermark_hits():
    grid = _make_grid_image()
    area = grid.shape[0] * grid.shape[1]
    mask, regions = detect_grid_watermark(
        grid, 0.6, area,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    assert len(regions) > 0, "网格状水印未被检出"
    assert all(r.kind == KIND_GRID for r in regions), "网格区域类别错误"
    n_pixels = int(np.count_nonzero(mask > 0))
    assert n_pixels > 0, "网格检测掩码为空"


def test_detect_grid_watermark_negative_on_plain():
    """纯色图不应被误判为网格（无周期特征）。"""
    plain = np.full((300, 300, 3), 235, dtype=np.uint8)
    area = plain.shape[0] * plain.shape[1]
    mask, regions = detect_grid_watermark(
        plain, 0.6, area,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    assert len(regions) == 0
    assert int(np.count_nonzero(mask > 0)) == 0


# ==============================================================================
# 第 2 组：全局渐变叠加水印检测（直接调用策略函数）
# ==============================================================================
def test_detect_gradient_watermark_full_image():
    grad_img = _make_gradient_image()
    area = grad_img.shape[0] * grad_img.shape[1]
    mask, regions = detect_gradient_watermark(
        grad_img, area,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    assert len(regions) == 1, "渐变应整图标记为一个区域"
    assert regions[0].kind == KIND_GRADIENT
    # 整图标记：掩码像素数 = 整图像素数。
    assert int(np.count_nonzero(mask > 0)) == area, "渐变水印应整图标记"


# ==============================================================================
# 第 3 组：总入口门控（默认不启用网格/渐变，需预设或显式开启）
# ==============================================================================
def test_grid_gating_default_off():
    """默认（enable_grid=False）时，网格图不应产生 KIND_GRID 区域。"""
    grid = _make_grid_image()
    res = detect_watermark_mask(
        grid, enable_grid=False, enable_translucent=True,
        enable_corner=False, enable_edge=False, use_ocr=False, detect_scale=2000,
    )
    kinds = {r.kind for r in res.regions}
    assert KIND_GRID not in kinds, "默认关闭网格检测却检出了网格区域"


def test_grid_gating_on():
    """enable_grid=True 时，网格图应检出 KIND_GRID 区域。"""
    grid = _make_grid_image()
    res = detect_watermark_mask(
        grid, enable_grid=True, enable_translucent=False,
        enable_corner=False, enable_edge=False, use_ocr=False, detect_scale=2000,
    )
    assert any(r.kind == KIND_GRID for r in res.regions), "开启网格检测却未检出"


def test_gradient_gating_default_off():
    """默认（enable_gradient=False）时，渐变图不应产生 KIND_GRADIENT 区域。"""
    grad_img = _make_gradient_image()
    res = detect_watermark_mask(
        grad_img, enable_gradient=False, enable_translucent=True,
        enable_corner=False, enable_edge=False, use_ocr=False, detect_scale=2000,
    )
    kinds = {r.kind for r in res.regions}
    assert KIND_GRADIENT not in kinds, "默认关闭渐变检测却检出了渐变区域"


def test_gradient_gating_on():
    """enable_gradient=True 时，渐变图应整图标记且类别为 KIND_GRADIENT。"""
    grad_img = _make_gradient_image()
    res = detect_watermark_mask(
        grad_img, enable_gradient=True, enable_translucent=False,
        enable_corner=False, enable_edge=False, use_ocr=False, detect_scale=2000,
    )
    assert any(r.kind == KIND_GRADIENT for r in res.regions), "开启渐变检测却未检出"
    area = grad_img.shape[0] * grad_img.shape[1]
    assert int(np.count_nonzero(res.mask > 0)) == area, "渐变应整图标记"


# ==============================================================================
# 第 4 组：OCR 文字识别精度（置信度过滤 + 不可用降级）
# ==============================================================================
class _FakeOCRReader:
    """返回两个框：高置信保留、低置信丢弃。坐标在「原图坐标系」。"""
    def readtext(self, bgr):
        h, w = bgr.shape[:2]
        return [
            ([[10, 10], [40, 10], [40, 40], [10, 40]], "KEEP", 0.9),
            ([[100, 100], [140, 100], [140, 140], [100, 140]], "DROP", 0.10),
        ]


def test_ocr_confidence_filter(monkeypatch):
    """低于 DETECT_OCR_MIN_CONFIDENCE 的框应被过滤，仅保留高置信框。"""
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: _FakeOCRReader())
    # 关闭预处理放大，使坐标 1:1 映射，便于断言。
    monkeypatch.setattr(config, "DETECT_OCR_UPSCALE", False)
    monkeypatch.setattr(config, "DETECT_OCR_MIN_CONFIDENCE", 0.5)

    patch = np.zeros((200, 200, 3), dtype=np.uint8)
    boxes = det._ocr_boxes_in_patch(patch, 0, 0)
    assert len(boxes) == 1, f"应只保留 1 个高置信框，实际：{boxes}"
    x, y, bw, bh = boxes[0]
    # 坐标应映射回原图（px=py=0），且为保留的高置信框。
    assert (x, y, bw, bh) == (10, 10, 30, 30), boxes


def test_ocr_no_reader_returns_empty(monkeypatch):
    """OCR 不可用时安全返回空列表，不抛异常。"""
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: None)
    patch = np.zeros((100, 100, 3), dtype=np.uint8)
    assert det._ocr_boxes_in_patch(patch, 0, 0) == []


# ==============================================================================
# 第 5 组：命中率统计（compute_detection_stats / DetectionResult.stats）
# ==============================================================================
def test_compute_detection_stats_correctness():
    """coverage_ratio = 掩码像素 / 整图面积；by_kind 计数正确；used_ocr 透传。"""
    h, w = 100, 100
    area = h * w
    mask = np.zeros((h, w), dtype=np.uint8)
    # 在 (10,10,20,20) 处标记 100 像素。
    mask[10:30, 10:30] = 255
    regions = [
        det.DetectionRegion(x=10, y=10, w=20, h=20, kind=KIND_GRID),
        det.DetectionRegion(x=50, y=50, w=10, h=10, kind=KIND_GRID),
        det.DetectionRegion(x=70, y=70, w=5, h=5, kind=KIND_GRADIENT),
    ]
    stats = compute_detection_stats(mask, regions, area, used_ocr=True)
    assert stats.mask_pixels == 400  # 掩码 (10:30,10:30) 共 20×20 = 400 像素
    assert abs(stats.coverage_ratio - (stats.mask_pixels / area)) < 1e-6
    assert stats.region_count == 3
    assert stats.by_kind.get(KIND_GRID) == 2
    assert stats.by_kind.get(KIND_GRADIENT) == 1
    assert stats.used_ocr is True


def test_detect_result_always_has_stats():
    """detect_watermark_mask 返回结果应始终附带非空 stats（向后兼容）。"""
    img = np.full((200, 300, 3), 235, dtype=np.uint8)
    res = detect_watermark_mask(
        img, sensitivity=0.55, use_ocr=False, detect_scale=2000
    )
    assert res.stats is not None, "DetectionResult.stats 为空"
    assert 0.0 <= res.stats.coverage_ratio <= 1.0
