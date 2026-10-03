"""阶段 181 探针：基线 corner_logo 在干净图右下角深块/文字上的误报 + 非确定复现。

目的：
 1) 复现 180 登记的「基线 corner_logo 在右下角深块/文字上误报 + 跨进程非确定」。
 2) 定位到底是哪个子通路（white / lift / mser / ocr）在干净图上产出 corner_logo。
"""
import os
import sys
import numpy as np
from PIL import Image

_ROOT = "D:/work 111/2026-08-03-17-38-51"
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
from core.detector import (KIND_CORNER_LOGO, detect_watermark_mask,  # noqa: E402
                           _corner_patches, _plate_boxes_in_patch)

CLEAN = os.path.join(_ROOT, "benchmark_real", "clean", "images")
REAL = os.path.join(_ROOT, "benchmark_real", "images")


def load(name, real=False):
    base = REAL if real else CLEAN
    return np.asarray(Image.open(os.path.join(base, name)).convert("RGB"))


def determinism_check(name, times=5):
    print(f"\n### 确定性复现：{name}（杠杆 OFF，{times} 次同进程重复）")
    config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = False
    for i in range(times):
        res = detect_watermark_mask(load(name), preset="general", use_ocr=False)
        cl = [r for r in res.regions if r.kind == KIND_CORNER_LOGO]
        print(f"  run{i+1}: corner_logo={len(cl)}  "
              + " | ".join(f"({r.x},{r.y},{r.w},{r.h},c={r.confidence:.2f})" for r in cl))


def subpath_check(name, real=False):
    print(f"\n### 子通路定位：{name}")
    arr = load(name, real=real)
    h, w = arr.shape[:2]
    patches = _corner_patches(h, w, config.DETECT_CORNER_RATIO, search_bottom=config.DETECT_SEARCH_BOTTOM)
    area_total = h * w
    # 只看右下角搜索区
    for (px, py, pw, ph) in patches:
        if not (px + pw >= w and py + ph >= h):
            continue
        patch = arr[py:py + ph, px:px + pw]
        print(f"  右下角 patch @({px},{py},{pw},{ph})")
        # white 源
        wb = _plate_boxes_in_patch(patch, px, py, area_total,
                                  max(2, int(round(config.DETECT_PLATE_MIN_SIDE))),
                                  mask_source="white")
        print(f"    white 源框数={len(wb)} -> {wb[:3]}")
        # 实测真实豆包图对照：应该 white 源命中
        print(f"    (config) V_MIN={config.DETECT_PLATE_V_MIN} S_MAX={config.DETECT_PLATE_S_MAX} "
              f"MIN_AREA_RATIO={config.DETECT_PLATE_MIN_AREA_RATIO} "
              f"MAX_AREA_RATIO={config.DETECT_PLATE_MAX_AREA_RATIO} "
              f"SOLIDITY={config.DETECT_PLATE_SOLIDITY} INK_MIN={config.DETECT_PLATE_INK_MIN}")


if __name__ == "__main__":
    determinism_check("syn_02_corner_text.png", 5)
    determinism_check("syn_05_natural.png", 5)
    determinism_check("syn_01_plain.png", 5)
    subpath_check("syn_02_corner_text.png")
    subpath_check("ai_logo_02_snow_mountain_lake.jpg", real=True)
