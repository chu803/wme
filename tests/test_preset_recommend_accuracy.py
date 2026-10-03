"""阶段 59/61 · 预设推荐器在纹理/照片底图上的准确率与组合水印可覆盖性测试。

背景：推荐器旧实现有两个系统性缺陷（此前只在纯色合成自检过，未在纹理/照片底图验证）：
  1) `_marks_at` 用百分位阈值，在纹理/照片底图上恒定选出 ~46.75% 像素 → 覆盖/周期/
     规则性特征全被背景纹理主导 → grid 点阵被推荐成 general（召回 0）、极淡平铺被
     误判成文字。修复：稳健统计门限（绝对残差下限 ∧ 分数 > 中位数 + k·1.4826·MAD）。
  2) 角落实心轮廓的面积/实心率判据会把「底部小字字形」（~44×18）误判为品牌 LOGO →
     文字水印被推给 ai_logo 预设而漏检。修复：角标最小实体边长须 ≥ 4% × min(图宽,图高)。
  3) 决策顺序把全局渐变放最前，任何光滑渐变内容都会吞掉角标/文字信号。修复：局部强
     结构（logo/平铺/网格/文字/签名）先于全局渐变。

本测试锁定「推荐正确性 + 推荐预设下的功能召回」双红线（textured/photo 为真实场景代表）：
  · grid / corner_logo / edge_text 在推荐预设下检测召回必须 ≥ 85%（修复前 grid=0、
    edge_text 被推给 ai_logo 后仅 25.8%）；
  · clean → general；
  · marks 层干净/网格分离度（防百分位反模式复发）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
import core.preset_recommender as recommender  # noqa: E402
from scripts.detector_diag import BASE_KINDS, gt_mask, metrics  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402

# edge_text 落入 ai_signature 或 ai_text_heavy 均正确（两者都启用 edge 策略）。
_EDGE_OK = {"ai_signature", "ai_text_heavy"}


def _detect(img, preset):
    m = det.detect_watermark_mask(img, preset=preset, use_ocr=False).mask
    return m[:, :, 0] if m.ndim == 3 else m


def _recall(kind, case, seed=7):
    """推荐 preset → 在该 preset 下检测 → 返回 (recommended_preset, recall)。"""
    base = BASE_KINDS[kind](seed, w=640, h=640)
    marked = CASES[case][0](base)
    rec = recommender.recommend_preset(marked)
    g = gt_mask(base, marked)
    m = metrics(_detect(marked, rec.preset), g)
    return rec.preset, m["recall"]


# ---------------------------------------------------------------------------
# 1. 功能双红线（真实场景代表：textured / photo）
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_grid_recommended_and_detected(kind):
    """网格点阵必须被推荐为 ai_grid 且在推荐预设下召回 ≥85%（修复前 general→0）。"""
    preset, recall = _recall(kind, "grid")
    assert preset == "ai_grid", f"{kind}/grid 应推荐 ai_grid，实际 {preset}"
    assert recall >= 0.85, f"{kind}/grid 在 {preset} 下召回 {recall * 100:.1f}% < 85%"


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_corner_logo_recommended_and_detected(kind):
    """角落 LOGO 推荐 ai_logo 且召回 ≥90%。"""
    preset, recall = _recall(kind, "corner_logo")
    assert preset == "ai_logo", f"{kind}/corner_logo 应推荐 ai_logo，实际 {preset}"
    assert recall >= 0.90


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_edge_text_recommended_with_edge_strategy(kind):
    """底部文字必须落入启用 edge 策略的预设（signature/text_heavy）且召回 ≥90%。

    修复前被字形误判为 ai_logo（无 edge 策略），召回仅 25.8%。
    """
    preset, recall = _recall(kind, "edge_text")
    assert preset in _EDGE_OK, (
        f"{kind}/edge_text 应推荐 {_EDGE_OK} 之一，实际 {preset}")
    assert recall >= 0.90, f"{kind}/edge_text 在 {preset} 下召回 {recall * 100:.1f}% < 90%"


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_clean_recommends_general(kind):
    """干净纹理/照片底图必须推荐 general（旧百分位 marks 层不会误报 grid/文字）。"""
    base = BASE_KINDS[kind](7, w=640, h=640)
    rec = recommender.recommend_preset(base)
    assert rec.preset == "general", f"{kind}/clean 应推荐 general，实际 {rec.preset}"


# ---------------------------------------------------------------------------
# 2. marks 层纹理鲁棒性（防百分位反模式复发，快）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_marks_layer_separates_clean_from_grid(kind):
    """稳健 marks：干净底图覆盖率须 <1%，grid 点阵须显著更高（修复前两者恒 46.75%）。"""
    base = BASE_KINDS[kind](7, w=640, h=640)
    sens = config.DETECT_TRANSLUCENT_SENSITIVITY

    def cov_of(img):
        rgb = recommender._ensure_rgb(img)
        gray_u8, residual, score, h, w = recommender._build_residual(rgb)
        marks = recommender._marks_at(score, sens, residual)
        return float(np.count_nonzero(marks > 0)) / marks.size

    clean_cov = cov_of(base)
    grid_cov = cov_of(CASES["grid"][0](base))
    assert clean_cov < 0.01, (
        f"{kind} 干净图 marks 覆盖率 {clean_cov * 100:.2f}% ≥ 1%（应被背景抑制）")
    assert grid_cov > 0.002, (
        f"{kind} grid marks 覆盖率 {grid_cov * 100:.3f}% 过稀，无法与干净图区分")
    assert grid_cov > 3 * clean_cov, (
        f"{kind} grid({grid_cov:.4f}) 与 clean({clean_cov:.4f}) 覆盖率无分离度")


def test_corner_glyph_not_counted_as_logo():
    """底部小字字形（最小边 ~2.8% 图宽）不得算作角标 LOGO（阶段 59 修复的守卫）。"""
    # 构造一个贴近左下角、满足旧面积/实心率判据的"字形级"实心块（44×18）。
    cv2 = recommender._require_cv2()
    img = np.full((640, 640, 3), 235, np.uint8)
    cv2.rectangle(img, (104, 581), (147, 599), (60, 60, 60), -1)  # 44×18
    count = recommender._corner_solid_count(
        cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), 640, 640)
    assert count == 0, "44×18 字形被误判为角标 LOGO（应要求最小边 ≥ 4% 图宽）"

    # 同位置的"品牌级"角标（最小边 73px ≥ 0.04×640=25.6）必须计为 1。
    img2 = np.full((640, 640, 3), 235, np.uint8)
    cv2.rectangle(img2, (444, 553), (625, 625), (20, 20, 24), -1)  # 181×72
    count2 = recommender._corner_solid_count(
        cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY), 640, 640)
    assert count2 == 1


# ---------------------------------------------------------------------------
# 3. 组合水印（阶段 61）：多类信号同时触发 → 推荐并集预设 ai_multi
# ---------------------------------------------------------------------------

def _combo_recall(kind, c1, c2, seed=7):
    """叠加两类水印，返回 (推荐预设, 推荐预设下对组合真值的召回)。"""
    base = BASE_KINDS[kind](seed, w=640, h=640)
    marked = CASES[c1][0](base)
    marked = CASES[c2][0](marked)
    rec = recommender.recommend_preset(marked)
    g = gt_mask(base, marked)
    return rec.preset, metrics(_detect(marked, rec.preset), g)["recall"]


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_combo_logo_plus_grid_recommends_union(kind):
    """LOGO + 网格组合必须走并集预设：修复前被推给 ai_logo（网格策略未启用）召回仅 72%。"""
    preset, recall = _combo_recall(kind, "corner_logo", "grid")
    assert preset == "ai_multi", f"{kind} logo+grid 应推荐 ai_multi，实际 {preset}"
    assert recall >= 0.90, f"{kind} logo+grid 召回 {recall * 100:.1f}% < 90%"


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_combo_logo_plus_text_recommends_union(kind):
    """LOGO + 底部文字组合必须走并集预设（修复前 ai_logo 无 edge 策略，召回 83.5%）。"""
    preset, recall = _combo_recall(kind, "corner_logo", "edge_text")
    assert preset == "ai_multi", f"{kind} logo+text 应推荐 ai_multi，实际 {preset}"
    assert recall >= 0.90, f"{kind} logo+text 召回 {recall * 100:.1f}% < 90%"


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_combo_text_plus_grid_recall(kind):
    """文字 + 网格：ai_grid 本身即超集，召回须 ≥90%（不得被降级为单信号预设而漏检）。"""
    preset, recall = _combo_recall(kind, "edge_text", "grid")
    assert recall >= 0.90, f"{kind} text+grid 在 {preset} 下召回 {recall * 100:.1f}% < 90%"


def test_ai_multi_preset_is_superset():
    """ai_multi 必须是策略并集（半透明+角落+边缘+网格+实体LOGO+平铺增强+底部搜索）。"""
    from core.detection_presets import resolve_preset
    kw = resolve_preset("ai_multi", 0.55)
    for flag in ("enable_translucent", "enable_corner", "enable_edge",
                 "enable_grid", "enable_logo"):
        assert kw.get(flag) is True, f"ai_multi 缺少策略开关 {flag}"
    assert kw.get("enhance_tiled") is True
    assert kw.get("search_bottom") is True
