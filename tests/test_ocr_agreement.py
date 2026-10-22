# -*- coding: utf-8 -*-
"""阶段 P0-2 · OCR 同意规则（`DETECT_TRANSLUCENT_REQUIRE_OCR`）守卫测试。

背景（37 张豆包真实水印金标准，2048²）：
  · 纯图误报 67/77 是 `translucent`，其置信度是**常量 ~0.401**，与真实水印的
    translucent 区域**无法用置信度分离**；
  · 真实水印（角标/平铺）都能被 OCR 命中，而**纯图 OCR 命中 0 个** ⇒ OCR 是干净判据。

规则：`translucent` 区域若与任何 `ocr_text` 区域不重叠（含「整图无 OCR 框」），
则从 kept_regions 剔除。作用域：杠杆开启 + 预设启用 OCR + **非平铺预设**。

模拟 A/B（不改代码，37 张）：
  纯图误报区域 73 → 9（−88%）；右下区误报 8 → 1；纯图区域面积 2.43% → 0.46%；
  角标 OCR 命中 10/12 不变、覆盖 1.27% → 1.08%（−15%）；平铺 big 11/10 不变。

默认 OFF（opt-in）：该规则会牺牲「无文字半透明叠加水印」这一类能力面
（无 OCR 框时 translucent 全数剔除），故作为可选杠杆而非默认行为。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import core.detector as det  # noqa: E402
from core.detector import (  # noqa: E402
    KIND_TRANSLUCENT,
    DetectionRegion,
    _region_overlap,
    detect_watermark_mask,
)


def _t(x, y, w, h, kind=KIND_TRANSLUCENT, conf=0.4):
    return DetectionRegion(x=x, y=y, w=w, h=h, kind=kind, confidence=conf)


# ---------------------------------------------------------------------------
# ① _region_overlap：宽松重叠判据（中心点互含或 IoU>0.05）
# ---------------------------------------------------------------------------

def test_region_overlap_center_inside():
    a = _t(10, 10, 20, 20)          # 中心 (20,20)
    b = _t(15, 15, 20, 20)          # 覆盖 (15..35, 15..35)
    assert _region_overlap(a, b) is True


def test_region_overlap_iou_above_threshold():
    a = _t(0, 0, 20, 20)
    b = _t(10, 0, 20, 20)           # IoU = 200/600 ≈ 0.333 > 0.05
    assert _region_overlap(a, b) is True


def test_region_overlap_disjoint():
    a = _t(0, 0, 10, 10)
    b = _t(100, 100, 10, 10)
    assert _region_overlap(a, b) is False


# ---------------------------------------------------------------------------
# ② 接线：杠杆 / use_ocr / OCR 重叠 / 平铺预设 四者的组合行为
# ---------------------------------------------------------------------------

@pytest.fixture
def translucent_only(monkeypatch):
    """注入两块**不相邻**的 translucent 区域 + 可控 OCR 框输出。

    几何（128×128 画布，避开 15% 覆盖率闸与碎片合并）：
      mask[20:60, 20:60] = 255（1600px = 9.8%）
      near = (24,24,16,16) 中心 (32,32) → 与 OCR 框 (22,22,14,14) 重叠
      far  = (52,52,10,10) 中心 (57,57) → 与 OCR 框不相交，且与 near 间距 12px
                                        （> 碎片合并间隙 15×128/640=3，不会被合并）
    """
    mask = np.zeros((128, 128), dtype=np.uint8)
    mask[20:60, 20:60] = 255
    near, far = _t(24, 24, 16, 16), _t(52, 52, 10, 10)
    monkeypatch.setattr(
        det, "detect_translucent",
        lambda *a, **k: (mask.copy(), [near, far]),
    )
    return near, far


def _patch_ocr(monkeypatch, boxes):
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: object())
    monkeypatch.setattr(
        det, "_ocr_boxes_in_patch", lambda *a, **k: list(boxes)
    )


def _translucent(res):
    return [r for r in res.regions if r.kind == KIND_TRANSLUCENT]


def test_lever_off_keeps_translucent(translucent_only, monkeypatch):
    """杠杆关闭：两块 translucent 都保留（默认行为不变）。"""
    _patch_ocr(monkeypatch, [(22, 22, 14, 14)])
    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_REQUIRE_OCR", False)
    res = detect_watermark_mask(np.zeros((128, 128, 3), dtype=np.uint8), use_ocr=True)
    assert len(_translucent(res)) == 2


def test_lever_on_drops_non_overlapping(translucent_only, monkeypatch):
    """杠杆开启：与 OCR 框重叠的保留，不相交的剔除。

    注意：保留的 near 会与 OCR 框被「碎片聚合」并成一个区域（bbox 取并集），
    故不断言绝对坐标，只验证 far 被剔除、near 存活。
    """
    _patch_ocr(monkeypatch, [(22, 22, 14, 14)])
    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_REQUIRE_OCR", True)
    res = detect_watermark_mask(np.zeros((128, 128, 3), dtype=np.uint8), use_ocr=True)
    kept = _translucent(res)
    assert len(kept) == 1, f"应只剩 near（与 OCR 合并后）一个区域，实得 {kept}"
    k = kept[0]
    assert (k.x <= 24 and k.y <= 24 and k.x + k.w >= 40 and k.y + k.h >= 40), (
        f"保留区域未覆盖 near：{k}"
    )


def test_lever_on_no_ocr_boxes_drops_all(translucent_only, monkeypatch):
    """整图无 OCR 框时 translucent 全数剔除——纯图清误报的来源（A/B 实测 −88%）。"""
    _patch_ocr(monkeypatch, [])
    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_REQUIRE_OCR", True)
    res = detect_watermark_mask(np.zeros((128, 128, 3), dtype=np.uint8), use_ocr=True)
    assert _translucent(res) == []


def test_lever_requires_use_ocr(translucent_only, monkeypatch):
    """预设关闭 OCR 时规则不生效（没有可同意的对象）。"""
    _patch_ocr(monkeypatch, [])
    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_REQUIRE_OCR", True)
    res = detect_watermark_mask(np.zeros((128, 128, 3), dtype=np.uint8), use_ocr=False)
    assert len(_translucent(res)) == 2


def test_lever_skipped_for_tiled_preset(translucent_only, monkeypatch):
    """平铺预设（enhance_tiled=True）不适用该规则——其水印 OCR 可能不点火。"""
    _patch_ocr(monkeypatch, [])
    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_REQUIRE_OCR", True)
    res = detect_watermark_mask(np.zeros((128, 128, 3), dtype=np.uint8),
                                preset="ai_tiled", use_ocr=True)
    assert len(_translucent(res)) == 2


# ---------------------------------------------------------------------------
# ③ P0-4：OCR 文字框自播种掩码（DETECT_OCR_BOX_TRUST）
# ---------------------------------------------------------------------------

def test_ocr_box_trust_rescues_empty_refine(monkeypatch):
    """P0-4 反例：证据 refine 把 OCR 框裁空时，杠杆 ON 必须仍保留该区域。

    根因（真实豆包成对图 pair_04「海边礁石」）：OCR 已读到角标文字，但框内残差证据弱
    ⇒ refine 裁空 ⇒ kept_regions 丢弃 ⇒ 整图返空掩码（静默漏检）。
    本测试把 refine 打成"全空"以复现该条件。
    """
    import core.detector as det
    img = np.full((128, 128, 3), 128, dtype=np.uint8)
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(det, "_ocr_boxes_in_patch",
                        lambda *a, **k: [(80, 80, 30, 16)])
    monkeypatch.setattr(
        det, "_refine_regions_to_mask",
        lambda regions, ev, h, w, pad: (np.zeros((h, w), np.uint8), len(regions)),
    )
    # 强制走 refine 通路（均匀图的证据场会触发「退化 → 整框填充」回退，
    # 那样即便杠杆 OFF 区域也能活，就复现不了 pair_04 的条件）。
    monkeypatch.setattr(det, "_evidence_is_degenerate", lambda ev: False)

    monkeypatch.setattr(config, "DETECT_OCR_BOX_TRUST", False)
    res_off = detect_watermark_mask(img, use_ocr=True)
    assert not [r for r in res_off.regions if r.kind == det.KIND_OCR_TEXT], (
        "杠杆 OFF 时应复现 pair_04：OCR 框被 refine 裁空后丢弃"
    )

    monkeypatch.setattr(config, "DETECT_OCR_BOX_TRUST", True)
    res_on = detect_watermark_mask(img, use_ocr=True)
    kept = [r for r in res_on.regions if r.kind == det.KIND_OCR_TEXT]
    assert kept and any((r.x, r.y) == (80, 80) for r in kept), (
        f"杠杆 ON 时应自播种 OCR 文字框，实得 {[(r.kind, r.x, r.y) for r in res_on.regions]}"
    )
    assert int(np.asarray(res_on.mask).max()) > 0, "OCR 框应贡献非空掩码"


def test_ocr_box_trust_narrow_scope_keeps_refined_shape(monkeypatch):
    """窄口径守卫：refine **非空**时不得整框填充 OCR 框（只保留证据形状）。

    反例来源（实测）：P0-4 初版按「单框中心无证据」逐框救援，在 benchmark_prompted
    上一次填满 23 个文字框（占画面 9%）⇒ ai_text_heavy 覆盖率 +48%、p90 +51%，
    触发面积漂移门禁。收窄为「整图返空才救援」后本用例锁死该语义：
    refine 给出框内一小块证据 ⇒ 掩码面积必须 < 整框面积。
    """
    img = np.full((128, 128, 3), 128, dtype=np.uint8)
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(det, "_ocr_boxes_in_patch", lambda *a, **k: [(80, 80, 30, 16)])
    monkeypatch.setattr(
        det, "_refine_regions_to_mask",
        lambda regions, ev, h, w, pad: (
            np.pad(np.full((4, 4), 255, np.uint8), ((82, h - 86), (82, w - 86))), 0,
        ),
    )
    monkeypatch.setattr(det, "_evidence_is_degenerate", lambda ev: False)
    monkeypatch.setattr(config, "DETECT_OCR_BOX_TRUST", True)

    res = detect_watermark_mask(img, use_ocr=True)
    mask = np.asarray(res.mask)
    box = mask[80:96, 80:110]
    filled = int((box > 0).sum())
    # 证据块不含框中心 ⇒ 下游 kept_regions 会丢弃该区域（阶段 103 既有语义），
    # 允许 0；关键是**不得整框填满**（宽口径会得到 30×16=480）。
    assert filled < 30 * 16, (
        f"窄口径下 OCR 框被整框填满（{filled}px / 480px）"
        f"——疑似退回「逐框整填」宽口径"
    )


def test_ocr_box_trust_default_on():
    """杠杆默认开启（P0-4 落地值）。"""
    assert config.DETECT_OCR_BOX_TRUST is True
