# -*- coding: utf-8 -*-
"""阶段 397：半透明通路的「结构边缘守卫」——规则网格不该被当成半透明水印。

背景（阶段 394 的遗留 #46）：一张**零水印**的玻璃幕墙照（规则窗格铺满画面）
被半透明通路误命中 ⇒ 局部涂抹 5.10%，画面被抹花。

可分性实测（docs/397）：
  · 真半透明叠层（合成 faint_tiled）的**区域**边缘能量 5.5~12.5；
  · 规则网格误伤的区域边缘能量 62.3~261.9 ⇒ 区域粒度上分得很干净。
  （连通域粒度上有重叠 ⇒ 守卫必须落在**区域**这一级。）

代价面：这条通路服务的「半透明平铺」类**本就不承诺**（阶段 324 实测修复收益上界
仅 0.026、真实照片 0/15 可达）⇒ 收紧它对承诺面无损，主要收益是"干净图不再被涂"。

守卫：
  · 干净幕墙 ⇒ 半透明区域数 0（掩码覆盖 <1%）；
  · 合成真叠层 ⇒ 半透明区域**不得大幅缩水**（召回不退化）。
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from core import detector as det  # noqa: E402
from scripts.detector_diag import BASE_KINDS  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402

FACADE = ROOT / "benchmark_real" / "clean" / "clean_16_grid_facade.jpg"


def _read(p: pathlib.Path):
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[:, :, None].repeat(3, axis=2)


def _translucent_regions(rgb: np.ndarray):
    res = det.detect_watermark_mask(rgb, preset="general", effective_preset="general", use_ocr=False)
    return [r for r in res.regions if r.kind == det.KIND_TRANSLUCENT], res


def test_clean_grid_facade_has_no_translucent_regions():
    """规则网格的干净图：不该再被判成半透明水印。"""
    regions, res = _translucent_regions(_read(FACADE))
    assert len(regions) == 0, f"干净幕墙仍被判出 {len(regions)} 个半透明区域"
    assert float((res.mask > 0).mean()) < 0.01, (
        f"干净幕墙掩码覆盖 {float((res.mask > 0).mean()):.2%}（应 <1%）")


def test_real_translucent_overlay_recall_not_collapsed():
    """真半透明叠层（合成夹具）：区域数不得大幅缩水。"""
    base = BASE_KINDS["gradient"](7, w=640, h=640)
    marked = CASES["faint_tiled"][0](base)
    regions, _res = _translucent_regions(marked[:, :, ::-1].copy())
    assert len(regions) >= 10, f"真叠层的半透明区域只剩 {len(regions)} 个（召回塌了）"


def test_guard_switch_and_threshold_exist():
    import config

    assert config.DETECT_TRANSLUCENT_STRUCTURED_EDGE_GUARD is True
    assert 10.0 < config.DETECT_STRUCTURED_EDGE_MAX_ENERGY < 80.0, (
        "阈值要落在真阳性(≤12.5)与误伤(≥62.3)之间")
