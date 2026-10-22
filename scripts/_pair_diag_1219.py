# -*- coding: utf-8 -*-
"""pair_04 漏检 / pair_10 过检 归因探针（一次性）。
对每张：① 右下角 patch 的 OCR 结果；② 水印真值区的残差/证据强度 vs 全图；
        ③ 各策略在真值区的产出。用完即删。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402
import core.detector as det  # noqa: E402
from core.detector import (  # noqa: E402
    _corner_patches,
    _evidence_gate,
    _residual_fields,
    _sensitivity_to_ksigma,
    detect_translucent,
)

D = os.path.join(_ROOT, "benchmark_real", "incoming", "pairs")
TRUTH = {"pair_04": (2361, 1273, 369, 131), "pair_10": (2397, 1343, 283, 193)}


def diag(key):
    wm = np.array(Image.open(f"{D}/{key}_wm.jpg").convert("RGB"))
    h, w = wm.shape[:2]
    print(f"\n===== {key}  size={w}x{h}  truth={TRUTH[key]} =====")

    # ① 右下角 patch 的 OCR
    patches = _corner_patches(h, w, config.DETECT_CORNER_RATIO,
                              search_bottom=config.DETECT_SEARCH_BOTTOM)
    br = [p for p in patches if p[0] + p[2] >= w and p[1] + p[3] >= h]
    if br:
        px, py, pw, ph = br[0]
        patch = wm[py:py + ph, px:px + pw]
        boxes = det._ocr_boxes_in_patch(patch, px, py, accept_low_conf=True)
        print(f"  BR patch=({px},{py},{pw},{ph}) OCR boxes={boxes}")
        boxes_lo = det._ocr_boxes_in_patch(patch, px, py, accept_low_conf=False)
        print(f"  OCR boxes(严格)={boxes_lo}")
    else:
        print("  无 BR patch")

    # ② 真值区 vs 全图的残差/证据强度
    tx, ty, tw, th = TRUTH[key]
    gray = cv2.cvtColor(wm, cv2.COLOR_RGB2GRAY)
    ra, sn, _ = _residual_fields(wm)
    at = h * w
    sig = config.DETECT_TRANSLUCENT_SENSITIVITY
    k = _sensitivity_to_ksigma(sig)
    ev = _evidence_gate(ra, sn, config.DETECT_MIN_DELTA, k)
    tsel = np.zeros((h, w), bool)
    tsel[ty:ty + th, tx:tx + tw] = True
    print(f"  残差幅度 真值区 mean={ra[tsel].mean():.2f} p90={np.percentile(ra[tsel],90):.2f}"
          f" | 全图 mean={ra.mean():.2f} p90={np.percentile(ra,90):.2f}")
    print(f"  证据密度 真值区={ev[tsel].mean()*100:.2f}% | 全图={ev.mean()*100:.2f}%"
          f" | MIN_DELTA={config.DETECT_MIN_DELTA} k={k:.3f}")
    # 顶边（pair_10 的误报源）
    tsel2 = np.zeros((h, w), bool)
    tsel2[0:int(0.18 * h), :] = True
    print(f"  证据密度 顶边18%={ev[tsel2].mean()*100:.2f}%  残差 mean={ra[tsel2].mean():.2f}")

    # ③ translucent 策略在真值区的产出
    m, regs = detect_translucent(wm, sig, at, config.DETECT_MIN_AREA_RATIO,
                                 config.DETECT_MAX_AREA_RATIO)
    inside = [r for r in regs if r.x < tx + tw and r.x + r.w > tx
              and r.y < ty + th and r.y + r.h > ty]
    print(f"  translucent 区域 {len(regs)} 个；与真值区相交的 {len(inside)} 个："
          f"{[(r.x, r.y, r.w, r.h) for r in inside[:5]]}")


for key in ("pair_04", "pair_10"):
    diag(key)
