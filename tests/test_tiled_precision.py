"""阶段 54：半透明通路（detect_tiled_translucent）在「周期性点阵」上的精确率回归。

背景（scripts/probe_tiled_dilate.py，阶段 53 后数字）：
    grid 合并精确率仅 textured 23.7% / photo 30.0% / gradient 30.9%。
    阶段 53 已把 grid 策略本身做到 90%+ 精确，但合并（OR）后仍被拉低，
    根因是 detect_tiled_translucent 的 _components_mask 在网格点阵上做了
    dilate=1 的几何扩张：网格点平滑过渡区被裹入，cov 1.2%→2.5%，tiled 在该处
    精确率仅 48~65%，合并后把 grid 的 100% 精确稀释到约 30%。

阶段 54 修复：把 DETECT_TILED_MASK_DILATE 默认值从 1 收敛为 0（与阶段 53 网格一致）。
    dilate=0 时 grid 合并精确率回升到 textured 33.3% / photo 42.7% / gradient 38.6%，
    而对真正依赖 tiled 的 faint_tiled / edge_text / corner_logo 精确率与召回在误差内不变
    （faint_tiled 召回仅降约 1pp，属信息论极限区噪声）。

本文件锁定：
    1) 默认（dilate=0）下 grid 合并精确率远高于阶段 54 前的 dilate=1 基线；
    2) A/B 反证：dilate=0 严格优于 dilate=1（锁定默认改 0 的决策）；
    3) 护栏：dilate=0 不损伤非点阵用例（极淡平铺/边缘文字/角标）的精确率与召回。
"""
import numpy as np
import pytest

import config
from core.detector import detect_watermark_mask
from scripts.detector_diag import BASE_KINDS, CASES, gt_mask, metrics


def _merged(kind: str, case: str, dilate: int):
    """在指定底图/用例上以给定 dilate 跑整体合并检测，返回指标字典。"""
    prev = config.DETECT_TILED_MASK_DILATE
    config.DETECT_TILED_MASK_DILATE = dilate
    try:
        base = BASE_KINDS[kind](7)
        marked = CASES[case][0](base)
        g = gt_mask(base, marked)
        preset = CASES[case][1]
        res = detect_watermark_mask(marked, preset=preset, use_ocr=False)
        return metrics(res.mask, g)
    finally:
        config.DETECT_TILED_MASK_DILATE = prev


@pytest.mark.parametrize("kind,min_prec,min_rec", [
    ("gradient", 0.35, 0.90),   # dilate=1 基线 30.9% → dilate=0 实测 38.6%
    ("textured", 0.30, 0.80),   # dilate=1 基线 23.7% → dilate=0 实测 33.3%
    ("photo",    0.40, 0.90),   # dilate=1 基线 30.0% → dilate=0 实测 42.7%
])
def test_grid_merged_precision_default_dilate0(kind, min_prec, min_rec):
    """默认（dilate=0）下 grid 合并精确率应显著高于阶段 54 前的 dilate=1 基线。"""
    m = _merged(kind, "grid", config.DETECT_TILED_MASK_DILATE)
    assert m["precision"] >= min_prec, (
        f"{kind}/grid 默认合并精确率 {m['precision']*100:.1f}% 低于 {min_prec*100:.0f}%"
        f"（阶段 54 前 dilate=1 基线约 24~31%）"
    )
    assert m["recall"] >= min_rec, (
        f"{kind}/grid 默认合并召回 {m['recall']*100:.1f}% 过低"
    )


def test_tiled_dilate0_better_than_dilate1_on_grid():
    """A/B 反证：dilate=0 在 grid 合并精确率上应严格优于 dilate=1（锁定默认改 0 的决策）。"""
    kind = "textured"  # 差距最大的一档（23.7% → 33.3%，+9.6pp）
    p1 = _merged(kind, "grid", 1)["precision"]
    p0 = _merged(kind, "grid", 0)["precision"]
    assert p0 > p1 + 0.05, (
        f"dilate=0 ({p0*100:.1f}%) 应明显优于 dilate=1 ({p1*100:.1f}%)"
    )


@pytest.mark.parametrize("case", ["faint_tiled", "edge_text", "corner_logo"])
def test_tiled_dilate0_no_regression_on_other_cases(case):
    """dilate=0 不应损伤非点阵用例（极淡平铺/边缘文字/角标）的精确率与召回。

    用 photo 底图（最难、最能暴露回归）。dilate=0 与 dilate=1 逐指标对比，
    容忍极小的信息论极限区噪声（faint_tiled 召回约 1pp 波动）。
    """
    kind = "photo"
    prev = config.DETECT_TILED_MASK_DILATE
    try:
        base = BASE_KINDS[kind](7)
        marked = CASES[case][0](base)
        g = gt_mask(base, marked)
        preset = CASES[case][1]

        config.DETECT_TILED_MASK_DILATE = 1
        m1 = metrics(detect_watermark_mask(marked, preset=preset, use_ocr=False).mask, g)
        config.DETECT_TILED_MASK_DILATE = 0
        m0 = metrics(detect_watermark_mask(marked, preset=preset, use_ocr=False).mask, g)
    finally:
        config.DETECT_TILED_MASK_DILATE = prev

    assert m0["precision"] >= m1["precision"] - 0.02, (
        f"{kind}/{case} dilate=0 精确率 {m0['precision']*100:.1f}% 低于 dilate=1 "
        f"{m1['precision']*100:.1f}%（退化）"
    )
    assert m0["recall"] >= m1["recall"] - 0.05, (
        f"{kind}/{case} dilate=0 召回 {m0['recall']*100:.1f}% 低于 dilate=1 "
        f"{m1['recall']*100:.1f}%（退化）"
    )
