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


def test_grid_evidence_and_lowers_false_coverage():
    """A/B 反证：关闭 DETECT_GRID_EVIDENCE_AND 时 gradient/grid 精确率大幅下降。

    2026-10-17 勘误：本用例早期用 textured(seed=7) 作底图，只见 on==off 精确率均 100%，
    误判 EVIDENCE_AND 已惰性并打了 xfail。经 scripts 探针扫 3 底图 × 8 seed 发现该结论只对
    textured/photo 成立（那里精确率本就 100%，EVIDENCE_AND 仅略降召回，看似「无用」）；
    但在 **gradient** 底图上它是关键误报抑制器——关闭后折叠模板的伪周期把误报铺满全图
    （如 seed=3：精确率 1.00→0.10、预测覆盖率 1.2%→12.2%）。故正确夹具是 gradient，
    原断言（开显著高于关）在 gradient 上稳定成立。
    ⚠ 双刃剑（2026-10-17 真实图探针补充）：删/留都不能一概而论——本断言只在**合成 gradient
    洪水**场景成立；在真实 doubao 图上 EVIDENCE_AND 开着反而把覆盖率推高（mean 11.29% vs
    关 5.60%，交集把周期模板打碎 + 覆盖率裁剪「保留小区域」所致）。故它防洪、但并非普适
    保护 ai_grid 红线，根因是 ai_grid 在非网格图上误触发 is_grid。详见 MEMORY.md 发布工程段。
    """
    kind = "gradient"
    base = BASE_KINDS[kind](3)
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
