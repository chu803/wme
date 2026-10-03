# -*- coding: utf-8 -*-
"""阶段 82 · 同 kind 相邻碎片聚合的单元测试。

背景（docs/A2 真实图门禁 F2 / docs/82）：真实 AI 平台图的「AI生成」角标会被
translucent 通路打成小碎片，每个碎片与真值框 IoU≈0.10 < 0.3 → 整个水印计 FN。
`_merge_adjacent_fragments` 用「间隙 + 双约束（最长边上限 / 填充率下限）」的
贪心合并解决该问题。本文件锁定其几何契约与部署开关。

特别地，``test_singleton_regions_survive`` 是「合并实现曾静默丢弃未参与聚合的
单体区域」这一缺陷的反例测试（阶段 82 开发中真实抓到过：image 01 的 AI 标以
单区域检出后被丢，整图召回 1.0 → 0.5）。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.detector import (  # noqa: E402
    DetectionRegion,
    KIND_CORNER_LOGO,
    KIND_TRANSLUCENT,
    _merge_adjacent_fragments,
)


def _r(x, y, w, h, kind=KIND_TRANSLUCENT, conf=0.5):
    return DetectionRegion(x=x, y=y, w=w, h=h, kind=kind, confidence=conf)


def _find(out, x, y, w, h):
    for r in out:
        if (r.x, r.y, r.w, r.h) == (x, y, w, h):
            return r
    return None


# ---------------------------------------------------------------------------
# 基本合并行为
# ---------------------------------------------------------------------------

def test_touching_fragments_merge():
    """相触（gap=0）的同 kind 碎片合并为一个区域，bbox 为并集框。"""
    regions = [_r(10, 10, 40, 40), _r(30, 30, 40, 40)]
    out = _merge_adjacent_fragments(regions, gap_px=24, max_side_px=160, fill_guard=2.2)
    assert len(out) == 1
    m = out[0]
    assert (m.x, m.y, m.w, m.h) == (10, 10, 60, 60)
    assert m.kind == KIND_TRANSLUCENT
    assert m.merged_from == 2
    assert m.anchors is not None and len(m.anchors) == 2
    # 置信度取成员最大值。
    assert m.confidence == pytest.approx(0.5)


def test_gap_within_threshold_merges_and_beyond_does_not():
    """间隙 <= gap_px 合并；> gap_px 不合并。"""
    a = _r(0, 0, 20, 20)
    near = _r(0, 30, 20, 20)   # 垂直间隙 10
    far = _r(0, 70, 20, 20)    # 与 near 垂直间隙 20
    out = _merge_adjacent_fragments([a, near, far], gap_px=24, max_side_px=160, fill_guard=2.2)
    # a+near（10）、near+far（20）均在阈值内 → 传递合并成一簇。
    assert len(out) == 1
    # 收紧 gap 到 15：far 与簇间隙 20 > 15，被截断。
    out2 = _merge_adjacent_fragments([a, near, far], gap_px=15, max_side_px=160, fill_guard=2.2)
    assert len(out2) == 2


def test_other_kinds_are_not_merged_and_preserved_by_identity():
    """非目标 kind 不参与合并、原对象原样保留。"""
    a = _r(0, 0, 40, 40, kind=KIND_CORNER_LOGO)
    b = _r(10, 10, 40, 40, kind=KIND_CORNER_LOGO)  # 与 a 相触
    t = _r(100, 100, 20, 20, kind=KIND_TRANSLUCENT)
    out = _merge_adjacent_fragments([a, b, t], gap_px=24, max_side_px=160, fill_guard=2.2)
    assert len(out) == 3
    assert a in out and b in out and t in out  # 原对象逐个保留
    assert all(r.merged_from == 1 for r in out)


def test_singleton_regions_survive():
    """反例回归：未参与合并的单体区域必须原样保留（开发中曾整体丢失）。"""
    singles = [_r(0, 0, 30, 30), _r(200, 200, 30, 30), _r(400, 400, 30, 30)]
    out = _merge_adjacent_fragments(singles, gap_px=24, max_side_px=160, fill_guard=2.2)
    assert len(out) == 3
    for r in singles:
        assert r in out


def test_merge_is_transitive_within_constraints():
    """链式碎片在约束内传递合并成一簇。"""
    # 三块沿 x 方向排布，两两间隙 10，总宽 100 < cap。
    regions = [_r(0, 0, 20, 20), _r(30, 0, 20, 20), _r(60, 0, 20, 20)]
    out = _merge_adjacent_fragments(regions, gap_px=24, max_side_px=160, fill_guard=2.2)
    assert len(out) == 1
    assert (out[0].x, out[0].w) == (0, 80)
    assert out[0].merged_from == 3


# ---------------------------------------------------------------------------
# 双约束（缺一不可，反例见 docs/82）
# ---------------------------------------------------------------------------

def test_max_side_cap_blocks_drag():
    """最长边上限挡「链式拖拽」：链总长超限时传递被截断。"""
    regions = [_r(0, 0, 20, 20), _r(30, 0, 20, 20), _r(60, 0, 20, 20)]
    # cap=25：任何合并（最短并宽 50）都超限 → 全部不并。
    out = _merge_adjacent_fragments(regions, gap_px=24, max_side_px=25, fill_guard=2.2)
    assert len(out) == 3
    # cap=55：a+b 并（宽 50 <= 55），再并 c 需宽 80 > 55 → 截断为两簇。
    out2 = _merge_adjacent_fragments(regions, gap_px=24, max_side_px=55, fill_guard=2.2)
    assert len(out2) == 2


def test_fill_guard_blocks_cross_empty_merge():
    """填充率守卫挡「跨空合并」：两个小区域斜对角排布，合并框大半是空白。"""
    a = _r(0, 0, 10, 10)
    b = _r(20, 20, 10, 10)  # gap=20（切比雪夫），合并框 30x30=900，成员和 200。
    out = _merge_adjacent_fragments([a, b], gap_px=24, max_side_px=160, fill_guard=2.2)
    assert len(out) == 2  # 900 > 2.2*200 → 被守卫挡下
    out2 = _merge_adjacent_fragments([a, b], gap_px=24, max_side_px=160, fill_guard=0)
    assert len(out2) == 1  # 守卫关闭时允许合并（行为对照）


def test_real_badge_geometry_merges_to_truth():
    """真实「AI生成」角标几何（1024 尺度碎片 → 640 锚点参数）能并回真值框。"""
    # docs/A2：右下角标真值 (875,952,140,60)，碎片 29~43px。
    frags = [
        _r(871, 934, 124, 28),
        _r(888, 970, 32, 35),
        _r(926, 968, 41, 41),
        _r(961, 968, 42, 43),
    ]
    # 1024 尺度等效参数：gap=24, cap=160（640 锚点 15/100 × 1.6）。
    out = _merge_adjacent_fragments(frags, gap_px=24, max_side_px=160, fill_guard=2.2)
    assert len(out) == 1
    m = out[0]
    inter_x = max(0, min(m.x + m.w, 875 + 140) - max(m.x, 875))
    inter_y = max(0, min(m.y + m.h, 952 + 60) - max(m.y, 952))
    inter = inter_x * inter_y
    union = m.w * m.h + 140 * 60 - inter
    assert inter / union >= 0.3  # 合并后达到区域匹配阈值


# ---------------------------------------------------------------------------
# 部署开关与主入口接线
# ---------------------------------------------------------------------------

@pytest.fixture
def two_fragment_detection(monkeypatch):
    """注入确定性的 translucent 双碎片输出（相触的两个小区域 + 有效掩码），
    保证主入口管线稳定走到「碎片聚合」出口。"""
    import core.detector as det

    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[24:34, 24:34] = 255   # 覆盖成员中心 (27,27)
    mask[30:38, 30:38] = 255   # 覆盖成员中心 (33,33)
    regions = [_r(20, 20, 14, 14), _r(28, 28, 10, 10)]  # 相触 → 可聚合

    def fake_translucent(rgb, sensitivity, area_total, min_area_ratio, max_area_ratio):
        return mask.copy(), [DetectionRegion(
            x=r.x, y=r.y, w=r.w, h=r.h, kind=r.kind, confidence=r.confidence,
        ) for r in regions]

    monkeypatch.setattr(det, "detect_translucent", fake_translucent)
    return regions


def test_pipeline_merge_respects_config_flag(two_fragment_detection, monkeypatch):
    """主入口在开关关闭时不做聚合、打开时调用聚合（接线测试）。"""
    from core.detector import detect_watermark_mask

    calls = {"n": 0}
    import core.detector as det_mod
    real_merge = det_mod._merge_adjacent_fragments

    def spy(regions, gap_px, max_side_px, fill_guard, kinds=None):
        calls["n"] += 1
        if kinds is None:
            return real_merge(regions, gap_px, max_side_px, fill_guard)
        return real_merge(regions, gap_px, max_side_px, fill_guard, kinds)

    monkeypatch.setattr("core.detector._merge_adjacent_fragments", spy)

    monkeypatch.setattr(config, "DETECT_FRAGMENT_MERGE", False)
    res_off = detect_watermark_mask(np.zeros((64, 64, 3), dtype=np.uint8), use_ocr=False)
    assert calls["n"] == 0
    assert len(res_off.regions) == 2
    assert all(r.merged_from == 1 for r in res_off.regions)

    monkeypatch.setattr(config, "DETECT_FRAGMENT_MERGE", True)
    res_on = detect_watermark_mask(np.zeros((64, 64, 3), dtype=np.uint8), use_ocr=False)
    assert calls["n"] == 1
    assert len(res_on.regions) == 1
    assert res_on.regions[0].merged_from == 2
    assert (res_on.regions[0].x, res_on.regions[0].y,
            res_on.regions[0].w, res_on.regions[0].h) == (20, 20, 18, 18)


def test_merge_failure_falls_back_to_unmerged(monkeypatch, two_fragment_detection):
    """聚合抛异常时按未聚合结果继续（回退路径必须有反例测试——经验 18）。"""
    from core.detector import detect_watermark_mask

    def boom(*args, **kwargs):
        raise RuntimeError("探针异常")

    monkeypatch.setattr(config, "DETECT_FRAGMENT_MERGE", True)
    monkeypatch.setattr("core.detector._merge_adjacent_fragments", boom)
    res = detect_watermark_mask(np.zeros((64, 64, 3), dtype=np.uint8), use_ocr=False)
    assert res is not None
    assert len(res.regions) == 2  # 未聚合的两个碎片原样保留
