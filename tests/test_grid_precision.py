"""阶段 53：网格水印在纹理/照片底图精确率回归测试。

背景（detector_diag.py，阶段 52 后数字）：
    网格策略的折叠模板掩码在纹理/照片底图上会把「纹理平均残留的假响应」
    沿周期铺满全图，导致合并后精确率仅 textured 11.6% / photo 30% / gradient 31%。
    阶段 53 修复：把折叠模板掩码与「单图证据门限」取交集
    （config.DETECT_GRID_EVIDENCE_AND），真实网格点在单图门限上必然显著、
    纹理假响应则不显著，于是收敛为只保留网格节点。

本文件侧重「网格策略单独」的精确率（隔离验证修复本身，不被其他策略合并稀释），
并加 A/B 反证与干净图护栏。
"""
import numpy as np
import pytest

import config
from core.detector import detect_grid_watermark, detect_watermark_mask
from scripts.detector_diag import BASE_KINDS, CASES, gt_mask, metrics


def _grid_only(kind: str, case: str = "grid", preset: str = "ai_grid"):
    """隔离调用网格策略，返回 (mask, gt)。"""
    base = BASE_KINDS[kind](7)
    marked = CASES[case][0](base)
    g = gt_mask(base, marked)
    h, w = marked.shape[:2]
    s = config.DETECT_TRANSLUCENT_SENSITIVITY
    m, _ = detect_grid_watermark(
        marked, s, h * w,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    return m, g


@pytest.mark.parametrize("kind,min_prec,min_rec", [
    ("gradient", 0.95, 0.95),   # 阶梯 49 起即接近完美
    ("textured", 0.80, 0.80),   # 修复前合并后仅 11.6%；隔离原始 tiled 也仅 ~38%
    ("photo",    0.90, 0.90),   # 修复前合并后约 30%
])
def test_grid_precision_improved(kind, min_prec, min_rec):
    """网格策略单独精确率应达高精度，召回仍保持（textured 略降属信息论极限）。"""
    m, g = _grid_only(kind)
    mm = metrics(m, g)
    assert mm["precision"] >= min_prec, (
        f"{kind}/grid 精确率 {mm['precision']*100:.1f}% 低于 {min_prec*100:.0f}%"
    )
    assert mm["recall"] >= min_rec, (
        f"{kind}/grid 召回 {mm['recall']*100:.1f}% 低于 {min_rec*100:.0f}%"
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "OBSOLETE PREMISE（阶段 49/53/106 后失效）：本用例断言「关闭 DETECT_GRID_EVIDENCE_AND "
        "时网格精确率明显更低」。实测（2026-10-17，canonical venv）在 marked/clean × "
        "gradient/textured/photo 共 5 种组合下，EVIDENCE_AND 开/关的 precision 与 FPR 恒等"
        "（on==off==100% / FPR 0%），且与 DETECT_TRANSLUCENT_SENSITIVITY(0.55/0.50) 无关。"
        "根因：阶段 106 把折叠模板门限抬到 k_tmpl=1.3× 后，纹理假响应已在模板层被压净，"
        "EVIDENCE_AND（core/detector.py:2228 与单图证据门限相交）退化为无害冗余安全网、"
        "不再产生可观测收益 ⇒ 原断言前提不再成立。保留原断言以记录历史意图；"
        "待办（择一）：① 重建能复现历史失败模式（纹理折叠后仍越模板阈、但单图证据门限拒之）"
        "的夹具并撤 xfail；② 删除已冗余的 DETECT_GRID_EVIDENCE_AND 通路。见 MEMORY.md 发布工程段。"
    ),
)
def test_grid_evidence_and_lowers_false_coverage():
    """A/B 反证（已失效，见 xfail reason）：关闭 DETECT_GRID_EVIDENCE_AND 时 textured/grid 精确率明显更低。"""
    kind = "textured"
    base = BASE_KINDS[kind](7)
    marked = CASES["grid"][0](base)
    g = gt_mask(base, marked)
    h, w = marked.shape[:2]
    s = config.DETECT_TRANSLUCENT_SENSITIVITY
    args = (marked, s, h * w, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO)

    on_prec = metrics(detect_grid_watermark(*args)[0], g)["precision"]
    prev = config.DETECT_GRID_EVIDENCE_AND
    config.DETECT_GRID_EVIDENCE_AND = False
    try:
        off_prec = metrics(detect_grid_watermark(*args)[0], g)["precision"]
    finally:
        config.DETECT_GRID_EVIDENCE_AND = prev
    assert on_prec > off_prec + 0.30, (
        f"交集应显著提升精确率：开 {on_prec*100:.1f}% vs 关 {off_prec*100:.1f}%"
    )


def test_grid_clean_image_no_false_grid():
    """干净图（无水印）上网格策略不应产出掩码——is_grid 判定应不成立。"""
    base = BASE_KINDS["textured"](7)
    s = config.DETECT_TRANSLUCENT_SENSITIVITY
    m, _ = detect_grid_watermark(
        base, s, base.shape[0] * base.shape[1],
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    cov = float(np.count_nonzero(m > 0)) / max(1, m.size)
    assert cov < 0.02, f"干净图 grid 掩码覆盖 {cov*100:.2f}% 应接近 0"


def test_grid_merged_precision_not_regressed():
    """整体合并掩码在 textured/grid 上精确率不应回退到修复前（11.6%）附近。"""
    kind, case = "textured", "grid"
    base = BASE_KINDS[kind](7)
    marked = CASES[case][0](base)
    g = gt_mask(base, marked)
    res = detect_watermark_mask(marked, preset="ai_grid", use_ocr=False)
    mm = metrics(res.mask, g)
    assert mm["precision"] >= 0.18, (
        f"合并精确率 {mm['precision']*100:.1f}% 过低（修复前 11.6%）"
    )
    assert mm["recall"] >= 0.80, f"合并召回 {mm['recall']*100:.1f}% 过低"
