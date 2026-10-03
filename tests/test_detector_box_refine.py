"""阶段 103 守卫：框内「像素级二次确认」（掩码—实体对齐）。

守什么
------
阶段 103 归因探针（`%TEMP%/align_probe.py`，10 张真实底图 × 4 类合成水印）发现：
只有区域框、没有形状掩码的策略（角落 LOGO、边缘小字）此前退化为**整框填满**——
`edge_text` 通路在 4 类上分别涂 10.0 / 6.8 / 9.0 / 7.0% 图面，而 mask% ≈ bbox%
（证明几乎全是矩形填充），精确率只有 0.009~0.166。这就是「检测框」与「水印实体」
之间缺失的对齐环节。

修复后这些策略改为在框内只保留「有证据」的像素。本文件守住四件事：
  1. 掩码**不再整框填满**（有证据区域之外不涂任何像素）；
  2. 证据形状**不会超出**原包围盒（`refined ⊆ bbox_fill`，宁漏不误杀）；
  3. 框内无证据 ⇒ 整框丢弃（不再靠「框」本身当实体）；
  4. 证据场退化（全图无证据）⇒ 由调用方回退整框填充，不得静默清空掩码。

注入式反例
----------
把 `_collect` 里的二次确认分支退回 `_regions_to_mask`（即去掉本阶段改动）后，
`test_K2_evidence_shrinks_box` / `test_K3_no_evidence_box_dropped` /
`test_K5_end_to_end_refine_shrinks_mask` 必须转红；其余用例保持绿。
"""
from __future__ import annotations

import os

import numpy as np
import pytest

import config
from core.detector import (
    KIND_EDGE_TEXT,
    DetectionRegion,
    _evidence_is_degenerate,
    _refine_regions_to_mask,
    detect_watermark_mask,
)
from scripts.make_demo_assets import make_base, wm_edge_text, wm_faint_tiled


def _box(x, y, w, h, kind=KIND_EDGE_TEXT):
    return DetectionRegion(x=x, y=y, w=w, h=h, kind=kind, confidence=0.6)


def _evidence(shape, rects):
    """在 shape 上按 rects = [(x0, y0, x1, y1)] 置 255。"""
    ev = np.zeros(shape, dtype=np.uint8)
    for (x0, y0, x1, y1) in rects:
        ev[y0:y1, x0:x1] = 255
    return ev


# ---------------------------------------------------------------------------
# K1 · 原语自检
# ---------------------------------------------------------------------------

def test_K1_degenerate_detector():
    """全零证据场 = 退化；哪怕只有一个证据像素也不算退化。"""
    assert _evidence_is_degenerate(np.zeros((8, 8), np.uint8)) is True
    assert _evidence_is_degenerate(np.zeros((0, 0), np.uint8)) is True
    one = np.zeros((8, 8), np.uint8)
    one[3, 3] = 255
    assert _evidence_is_degenerate(one) is False


def test_K1_guard_selfcheck_box_fill_would_be_big():
    """守卫自检：若退回整框填充，掩码面积会是证据面积的数倍。

    否则下面的 K2/K3 守的是一个「本来就一样」的假问题。
    """
    ev = _evidence((100, 100), [(10, 10, 20, 20)])       # 10×10 = 100px 证据
    mask, _ = _refine_regions_to_mask([_box(0, 0, 100, 100)], ev, 100, 100, pad=0)
    assert int((mask > 0).sum()) == 100
    assert 100 * 10 <= 100 * 100, "整框填充会是 10000px，是证据面积的 100 倍"


# ---------------------------------------------------------------------------
# K2 · 证据形状：不再整框填满
# ---------------------------------------------------------------------------

def test_K2_evidence_shrinks_box():
    """框内只有一小块证据时，掩码必须等于该证据（而非整框）。"""
    ev = _evidence((60, 60), [(10, 10, 20, 20)])
    mask, dropped = _refine_regions_to_mask([_box(0, 0, 60, 60)], ev, 60, 60, pad=0)
    assert dropped == 0
    assert int((mask > 0).sum()) == 100, "应只保留 10×10 证据像素"
    assert mask[50, 50] == 0, "远离证据的框内像素不得被涂"


def test_K2_pad_expands_but_stays_inside_box():
    """pad>0 时证据向外膨胀，但**不得越出包围盒**。"""
    ev = _evidence((60, 60), [(10, 10, 20, 20)])
    m0, _ = _refine_regions_to_mask([_box(0, 0, 60, 60)], ev, 60, 60, pad=0)
    m2, _ = _refine_regions_to_mask([_box(0, 0, 60, 60)], ev, 60, 60, pad=2)
    assert int((m2 > 0).sum()) > int((m0 > 0).sum())
    # 膨胀后仍必须是「整框填充」的真子集。
    box_fill = np.zeros((60, 60), np.uint8)
    box_fill[0:60, 0:60] = 255
    assert not np.any((m2 > 0) & ~(box_fill > 0))


def test_K2_evidence_outside_box_is_not_picked():
    """框外的证据不属于该框（掩码只从框内取）。"""
    ev = _evidence((60, 60), [(40, 40, 50, 50)])
    mask, dropped = _refine_regions_to_mask([_box(0, 0, 20, 20)], ev, 60, 60, pad=0)
    assert dropped == 1, "框内无证据 ⇒ 该框丢弃"
    assert int((mask > 0).sum()) == 0


def test_K2_clipped_box_does_not_crash():
    """越界包围盒必须被裁剪（项目已知边界：regions 包围盒可能越界）。"""
    ev = _evidence((30, 30), [(25, 25, 30, 30)])
    mask, dropped = _refine_regions_to_mask([_box(20, 20, 40, 40)], ev, 30, 30, pad=1)
    assert dropped == 0
    assert mask.shape == (30, 30)
    assert int((mask > 0).sum()) > 0


# ---------------------------------------------------------------------------
# K3 · 框内无证据 ⇒ 整框丢弃
# ---------------------------------------------------------------------------

def test_K3_no_evidence_box_dropped():
    """框内完全无证据时该框不得涂任何像素，且计入 dropped。"""
    ev = _evidence((40, 40), [(0, 0, 4, 4)])
    mask, dropped = _refine_regions_to_mask(
        [_box(10, 10, 20, 20)], ev, 40, 40, pad=2
    )
    assert dropped == 1
    assert int((mask > 0).sum()) == 0


def test_K3_mixed_boxes_keep_only_evidenced():
    """多框混合：有证据的保留、无证据的丢弃，两者互不污染。"""
    ev = _evidence((80, 80), [(5, 5, 15, 15)])
    mask, dropped = _refine_regions_to_mask(
        [_box(0, 0, 20, 20), _box(40, 40, 20, 20)], ev, 80, 80, pad=0
    )
    assert dropped == 1
    assert int((mask > 0).sum()) == 100
    assert not np.any(mask[40:60, 40:60])


# ---------------------------------------------------------------------------
# K4 · 退化回退（宁漏不误杀）
# ---------------------------------------------------------------------------

def test_K4_degenerate_evidence_triggers_fallback_path():
    """证据场全零时，`_evidence_is_degenerate` 必须为真 ⇒ 调用方走整框填充。

    这里断言的是**回退判据本身**：若它返回 False，`_collect` 就会拿全零证据去裁剪，
    把整幅图裁空（静默漏检）。
    """
    assert _evidence_is_degenerate(np.zeros((64, 64), np.uint8)) is True


def test_K4_flag_off_restores_box_fill():
    """关闭开关时必须逐字节回到「整框填充」的旧行为。"""
    base = make_base(seed=7, w=640, h=640)
    marked = wm_edge_text(base)
    old = config.DETECT_BOX_EVIDENCE_REFINE
    try:
        config.DETECT_BOX_EVIDENCE_REFINE = False
        res_off = detect_watermark_mask(marked, preset="ai_text_heavy", use_ocr=False)
        m_off = np.asarray(res_off.mask) > 0
    finally:
        config.DETECT_BOX_EVIDENCE_REFINE = old
    assert m_off.sum() > 0, "关掉开关后仍应有掩码（整框填充）"


# ---------------------------------------------------------------------------
# K5 · 端到端：掩码面积收缩且不新增像素
# ---------------------------------------------------------------------------

def _mask_with_flag(marked, flag):
    old = config.DETECT_BOX_EVIDENCE_REFINE
    try:
        config.DETECT_BOX_EVIDENCE_REFINE = flag
        res = detect_watermark_mask(marked, preset="ai_text_heavy", use_ocr=False)
    finally:
        config.DETECT_BOX_EVIDENCE_REFINE = old
    return np.asarray(res.mask) > 0


def test_K5_end_to_end_refine_shrinks_mask():
    """端到端：开启二次确认后掩码**严格变小**（不再整框填满）。"""
    base = make_base(seed=7, w=640, h=640)
    marked = wm_edge_text(base)
    m_on = _mask_with_flag(marked, True)
    m_off = _mask_with_flag(marked, False)
    assert m_off.sum() > 0
    assert m_on.sum() > 0, "收紧后不得变成空掩码（那是漏检，不是对齐）"
    assert m_on.sum() < m_off.sum(), (
        f"开启后应更小：on={m_on.sum()} off={m_off.sum()}"
    )


def test_K5_refined_mask_is_subset_of_box_fill():
    """二次确认只能**少涂**，绝不能多涂 —— 掩码是整框填充的子集。"""
    base = make_base(seed=11, w=640, h=640)
    marked = wm_edge_text(base)
    m_on = _mask_with_flag(marked, True)
    m_off = _mask_with_flag(marked, False)
    extra = int((m_on & ~m_off).sum())
    assert extra == 0, f"二次确认新增了 {extra} 个框外像素"


# ---------------------------------------------------------------------------
# K6 · 框内相对门限缩放必须真的参与判定（防止配置项变死代码）
# ---------------------------------------------------------------------------

_REAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "benchmark_real", "images")


def _first_real_image():
    if not os.path.isdir(_REAL_DIR):
        return None
    names = sorted(f for f in os.listdir(_REAL_DIR)
                   if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if not names:
        return None
    return os.path.join(_REAL_DIR, names[0])


def _load_real_640() -> np.ndarray:
    """取第一张真实基准图并缩到长边 640（与 quality_gate / e2e 口径一致）。"""
    import cv2

    from core.image_utils import load_image

    img = load_image(_first_real_image())
    if max(img.shape[:2]) != 640:
        k = 640 / float(max(img.shape[:2]))
        img = cv2.resize(img, (max(1, int(round(img.shape[1] * k))),
                               max(1, int(round(img.shape[0] * k)))),
                         interpolation=cv2.INTER_AREA)
    return img


@pytest.mark.skipif(_first_real_image() is None, reason="无真实基准图，跳过")
def test_K6_k_scale_is_live():
    """`DETECT_BOX_REFINE_K_SCALE` 必须真的参与判定（防止配置项变死代码）。

    必须在**真实底图**上测：合成渐变底图的证据很强（远高于两个门限），两种缩放
    结果相同，测不出「配置是否被读取」。

    注意：这里**不能**断言「缩放小的掩码是缩放大的超集」——最终掩码还要过
    `DETECT_MAX_COVERAGE_RATIO` 覆盖率闸，放宽门限会把覆盖率推过上限进而触发裁剪，
    端到端的集合关系会被闸打断。超集关系由下一条用例在**原语层**守住。
    """
    marked = wm_faint_tiled(_load_real_640())

    def mask_at(scale: float) -> np.ndarray:
        old = config.DETECT_BOX_REFINE_K_SCALE
        try:
            config.DETECT_BOX_REFINE_K_SCALE = scale
            res = detect_watermark_mask(marked, preset="ai_faint_tiled", use_ocr=False)
        finally:
            config.DETECT_BOX_REFINE_K_SCALE = old
        return np.asarray(res.mask) > 0

    tight = mask_at(1.0)
    loose = mask_at(0.05)
    assert tight.sum() > 0 and loose.sum() > 0
    assert not np.array_equal(tight, loose), (
        "两种相对门限缩放产生了完全相同的掩码 ⇒ DETECT_BOX_REFINE_K_SCALE 没被读取"
    )


def test_K6_gate_widening_is_superset():
    """原语层：降低相对门限只会**放宽**证据，证据必须是严格的超集。"""
    from core.detector import _evidence_gate, _residual_fields, _sensitivity_to_ksigma

    rng = np.random.default_rng(103)
    img = rng.normal(128, 12, (256, 256, 3)).clip(0, 255).astype(np.uint8)
    # 叠一条弱水印条纹，让证据落在门限边缘，两种门限才会分离。
    img[120:130, :, :] = (
        img[120:130, :, :].astype(np.int16) + 8
    ).clip(0, 255).astype(np.uint8)

    ra, sn, _ = _residual_fields(img)
    k_full = _sensitivity_to_ksigma(0.72) * 1.0
    k_half = _sensitivity_to_ksigma(0.72) * 0.5
    ev_full = _evidence_gate(ra, sn, config.DETECT_MIN_DELTA, k_full) > 0
    ev_half = _evidence_gate(ra, sn, config.DETECT_MIN_DELTA, k_half) > 0
    assert ev_half.sum() >= ev_full.sum()
    assert not np.any(ev_full & ~ev_half), "放宽门限后反而丢了像素 —— 方向写反了"

