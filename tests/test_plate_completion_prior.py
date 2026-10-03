"""阶段 179 · 生成器感知牌体补全先验（plate_footprint_prior）守卫测试。

锁定五层：
  ① 开关层：杠杆 DETECT_PLATE_COMPLETE_FROM_SIGNAL 关闭即静默；
  ② 信号锚定层：仅当右下角搜索区内有 translucent/edge_text 证据、且无 corner_logo
     命中时才补全；
  ③ 防重复层：右下角已有 corner_logo 命中时不重复补全（避免与既通路叠加回归）；
  ④ 几何层：补全区域贴右下角外侧、尺寸 = 340x128 × min(h,w)/2048（规范化足迹）；
  ⑤ 牌内锚定层（阶段 182）：至少一个信号区域与牌足迹自身重叠 ≥
     DETECT_PLATE_PRIOR_ANCHOR_OVERLAP（默认 0.50），把右下角远离足迹的
     文字/招牌信号（180/181 实测干净图误报源）挡在门外；阈值置 0 回 179 行为。
另含一个真实目标图的端到端守卫（图缺失则跳过），钉住「低对比底召回提升」的实测量。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest
from PIL import Image

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
from core.detector import (  # noqa: E402
    KIND_CORNER_LOGO,
    KIND_TRANSLUCENT,
    KIND_EDGE_TEXT,
    DetectionRegion,
)

H = W = 2048


def _region(kind, x, y, w, h):
    return DetectionRegion(x=x, y=y, w=w, h=h, kind=kind, confidence=0.5)


def _set_lever(val: bool) -> None:
    config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = val


def _run(regions, lever: bool):
    """跑一次补全先验，返回 (all_regions, mask_parts) 副本。"""
    _set_lever(lever)
    try:
        all_regions = list(regions)
        mask_parts = []
        det._apply_plate_completion_prior(
            all_regions, mask_parts, H, W, config.DETECT_CORNER_RATIO,
            config.DETECT_SEARCH_BOTTOM,
        )
        return all_regions, mask_parts
    finally:
        _set_lever(False)


def test_lever_off_is_silent():
    """杠杆关闭 → 即使有信号也不补全。"""
    regions = [_region(KIND_TRANSLUCENT, 1700, 1900, 100, 100)]
    out, parts = _run(regions, False)
    assert out == regions
    assert parts == []


def test_completes_when_signal_in_bottom_right_and_no_plate():
    """右下角有 translucent 信号、无 corner_logo → 补发 1 个 corner_logo + 1 掩码块。"""
    regions = [_region(KIND_TRANSLUCENT, 1700, 1900, 100, 100)]
    out, parts = _run(regions, True)
    added = [r for r in out if r.kind == KIND_CORNER_LOGO]
    assert len(added) == 1, f"应补 1 个 corner_logo，实际 {len(added)}"
    assert len(parts) == 1, "应写入 1 个实心掩码块"
    # 掩码块是 340x128 实心（规范化足迹）
    assert parts[0].shape == (H, W)
    assert int(parts[0].sum()) == 340 * 128 * 255


def test_no_double_add_when_corner_logo_present():
    """右下角已有 corner_logo 命中 → 不重复补全（防叠加回归）。"""
    regions = [
        _region(KIND_TRANSLUCENT, 1700, 1900, 100, 100),
        _region(KIND_CORNER_LOGO, 1700, 1900, 60, 30),
    ]
    out, parts = _run(regions, True)
    added = [r for r in out if r.kind == KIND_CORNER_LOGO]
    assert len(added) == 1, f"已有 corner_logo 不应再补，实际 {len(added)}"
    assert parts == []


def test_only_bottom_right_is_completed():
    """信号在左上角（非右下） → 不补全（豆包不变式：牌恒在右下）。"""
    regions = [_region(KIND_TRANSLUCENT, 100, 100, 80, 80)]
    out, parts = _run(regions, True)
    assert out == regions
    assert parts == []


def test_completion_anchored_bottom_right():
    """补全区域贴右下角外侧、尺寸为规范化足迹 340x128（@2048²）。

    信号取牌足迹内部（fp_ov=1.0），同时满足阶段 182 牌内锚定守卫。
    """
    regions = [_region(KIND_EDGE_TEXT, 1750, 1950, 90, 40)]
    out, _ = _run(regions, True)
    added = [r for r in out if r.kind == KIND_CORNER_LOGO][0]
    assert (added.x, added.y, added.w, added.h) == (W - 340, H - 128, 340, 128)


def test_anchor_guard_blocks_off_footprint_signal():
    """阶段 182：信号碰右下角搜索区但远离牌足迹（fp_ov < 0.5）→ 守卫拦截，不补全。

    复刻 180 误报源形态：syn_02/syn_05 的右下角文字证据落在足迹外/边缘。
    """
    regions = [_region(KIND_TRANSLUCENT, 1500, 1700, 120, 100)]  # fp_ov=0
    out, parts = _run(regions, True)
    added = [r for r in out if r.kind == KIND_CORNER_LOGO]
    assert added == [], f"远离足迹的信号不应补全，实际补了 {added}"
    assert parts == []


def test_anchor_guard_passes_when_signal_inside_footprint():
    """阶段 182：信号在牌足迹内部（fp_ov ≥ 0.5）→ 正常补全。"""
    regions = [_region(KIND_TRANSLUCENT, 1750, 1950, 90, 40)]  # fp_ov=1.0
    out, parts = _run(regions, True)
    added = [r for r in out if r.kind == KIND_CORNER_LOGO]
    assert len(added) == 1
    assert len(parts) == 1


def test_anchor_guard_disable_restores_prior_behavior():
    """阶段 182：阈值置 0 关闭守卫 → 远离足迹的信号也补全（179 原始行为可恢复）。"""
    saved = config.DETECT_PLATE_PRIOR_ANCHOR_OVERLAP
    config.DETECT_PLATE_PRIOR_ANCHOR_OVERLAP = 0.0
    try:
        regions = [_region(KIND_TRANSLUCENT, 1500, 1700, 120, 100)]  # fp_ov=0
        out, parts = _run(regions, True)
        added = [r for r in out if r.kind == KIND_CORNER_LOGO]
        assert len(added) == 1, "守卫关闭后应回到 179 行为（补发整牌）"
        assert len(parts) == 1
    finally:
        config.DETECT_PLATE_PRIOR_ANCHOR_OVERLAP = saved


def test_end_to_end_low_contrast_target_boosted():
    """端到端守卫：真实低对比底图上，杠杆 ON 必须新增 corner_logo 且召回提升。

    钉住 178 归因结论——4 张低对比底此前 corner_logo 输出为 0、召回全来自
    translucent/edge_text 碎片；179 补全先验须在此类图上补发角标区域。
    """
    bench = os.path.join(_ROOT, "benchmark_real")
    gt = os.path.join(bench, "ground_truth.json")
    man = os.path.join(bench, "manifest.json")
    if not (os.path.isfile(gt) and os.path.isfile(man)):
        pytest.skip("benchmark_real 不存在，跳过端到端守卫")

    import json
    truth = json.load(open(gt, encoding="utf-8"))
    tby = {e["image_id"]: e["watermarks"][0]["bbox"] for e in truth["entries"]}
    manifest = json.load(open(man, encoding="utf-8"))
    id2path = {im["id"]: im["path"] for im in manifest["images"]}
    target = "ai_logo_08_abstract_flui"  # 178 实测 corner_logo=0、召回最低(0.03)
    if target not in id2path:
        pytest.skip(f"{target} 不在基准中，跳过")

    arr = np.asarray(Image.open(os.path.join(bench, id2path[target])).convert("RGB"))

    # 杠杆 OFF：无 corner_logo
    _set_lever(False)
    r_off = det.detect_watermark_mask(arr, preset="general", use_ocr=False)
    n_off = sum(1 for r in r_off.regions if r.kind == KIND_CORNER_LOGO)

    # 杠杆 ON：应补出 corner_logo
    _set_lever(True)
    try:
        r_on = det.detect_watermark_mask(arr, preset="general", use_ocr=False)
    finally:
        _set_lever(False)
    n_on = sum(1 for r in r_on.regions if r.kind == KIND_CORNER_LOGO)

    assert n_off == 0, f"杠杆 OFF 时 {target} 不应有 corner_logo，实际 {n_off}"
    assert n_on >= 1, f"杠杆 ON 时 {target} 应补出 corner_logo，实际 {n_on}"
