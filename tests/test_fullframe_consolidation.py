# -*- coding: utf-8 -*-
"""阶段 P1 守卫：全幅平铺水印的「全帧区域」合成 + 预设接线。

背景：benchmark 的 bbox-IoU(≥0.3) 匹配是为局部水印设计的；grid / tiled-translucent
铺满整幅时检测器只吐小单元，区域级召回机械性为 0。本守卫固化：
  (1) `_consolidate_fullframe`：同类区域铺满全幅 → 返回 bbox=(0,0,W,H) 且
      `full_frame=True` 的全帧区域；
  (2) 局部聚集（未铺满）/ 区域数不足 → None；
  (3) 主导 kind 取出现最多者；非 eligible 类别不参与；
  (4) 预设接线：ai_grid / ai_tiled / ai_faint_tiled 的 resolve 带
      `enable_fullframe=True`，general 不带（默认关，防误伤普通照片）。
"""

from __future__ import annotations

import config
from core.detector import (
    KIND_GRID,
    KIND_TRANSLUCENT,
    DetectionRegion,
    _consolidate_fullframe,
)
from core.detection_presets import get_preset


def _reg(x, y, w=20, h=20, kind=KIND_GRID):
    return DetectionRegion(x=x, y=y, w=w, h=h, kind=kind)


def test_fullframe_spread_regions_consolidated():
    # 8 个网格单元散布覆盖 1000x800 整幅 → 全帧区域
    regs = [_reg(x, y) for x in (0, 250, 500, 980) for y in (0, 780)]
    W, H = 1000, 800
    fr = _consolidate_fullframe(regs, W, H, (KIND_GRID, KIND_TRANSLUCENT), 0.75, 6)
    assert fr is not None
    assert fr.full_frame is True
    assert (fr.x, fr.y, fr.w, fr.h) == (0, 0, W, H)
    assert fr.kind == KIND_GRID


def test_fullframe_localized_patch_not_consolidated():
    # 8 个单元挤在左上 140x60 → 未铺满 → None
    regs = [_reg(x, y) for x in (0, 40, 80, 120) for y in (0, 40)]
    fr = _consolidate_fullframe(regs, 1000, 800, (KIND_GRID,), 0.75, 6)
    assert fr is None


def test_fullframe_too_few_regions_not_consolidated():
    # 2 个区域即使横跨全幅也不判平铺（防少数大误报凑整幅）
    regs = [_reg(0, 0), _reg(980, 780)]
    fr = _consolidate_fullframe(regs, 1000, 800, (KIND_GRID,), 0.75, 6)
    assert fr is None


def test_fullframe_dominant_kind_is_majority():
    regs = [_reg(x, y, kind=KIND_TRANSLUCENT) for x in (0, 300, 600, 980) for y in (0, 780)]
    regs += [_reg(x, y, kind=KIND_GRID) for x, y in ((0, 0), (980, 780))]
    fr = _consolidate_fullframe(regs, 1000, 800, (KIND_GRID, KIND_TRANSLUCENT), 0.75, 6)
    assert fr is not None and fr.kind == KIND_TRANSLUCENT


def test_fullframe_ignores_ineligible_kinds():
    # 只有 corner_logo（不在 eligible）→ None
    regs = [_reg(x, y, kind="corner_logo") for x in (0, 300, 600, 980) for y in (0, 780)]
    fr = _consolidate_fullframe(regs, 1000, 800, (KIND_GRID, KIND_TRANSLUCENT), 0.75, 6)
    assert fr is None


def test_preset_wiring_fullframe():
    base = config.DETECT_TRANSLUCENT_SENSITIVITY
    for name in ("ai_grid", "ai_tiled", "ai_faint_tiled"):
        p = get_preset(name)
        assert p is not None, name
        assert p.resolve(base).get("enable_fullframe") is True, name
    g = get_preset("general")
    assert g is not None
    assert "enable_fullframe" not in g.resolve(base)


def test_config_defaults_are_conservative():
    # 默认保守：不开启、不铺满掩码
    assert config.DETECT_FULLFRAME_CONSOLIDATE is False
    assert config.DETECT_FULLFRAME_FILL_MASK is False
    assert 0.3 <= config.DETECT_FULLFRAME_SPREAD_RATIO <= 1.0
    assert config.DETECT_FULLFRAME_MIN_REGIONS >= 2


def test_mask_is_neutral_to_fullframe_region():
    """FILL_MASK=False 时，全帧区域不得参与掩码裁剪 / 覆盖率闸（否则面积漂移）。

    2026-10-18 real_gate 实测失败（ai_faint_tiled mask_coverage_mean +24.6%、
    p90 +39.9%，容差 15%）的根因守卫：全帧 region 的 bbox 是整幅，一旦参与
    final_mask 裁剪，就会把本该被小区域 bbox 裁掉的稀疏掩码像素全部保留。
    实测修复后 mask 对 fullframe 开关**逐字节相等**（3 张图验证）。
    """
    import inspect

    from core import detector as det

    src = inspect.getsource(det.detect_watermark_mask)
    assert "not config.DETECT_FULLFRAME_FILL_MASK" in src, (
        "最终掩码裁剪必须按 FILL_MASK 跳过全帧区域"
    )
    assert 'not getattr(r, "full_frame", False)' in src, (
        "覆盖率闸裁剪循环必须剔除全帧区域（否则区域级报告会丢）"
    )
