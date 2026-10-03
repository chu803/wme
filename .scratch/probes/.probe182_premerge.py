# -*- coding: utf-8 -*-
"""阶段 182 探针三：捕获 _apply_plate_completion_prior 入口处的真实输入（合并前）。

monkey-patch 包装 prior，记录调用时的 all_regions（translucent/edge_text 及
corner_logo）与各自对牌足迹的重叠率，并记录 prior 是否补发了牌。
"""
import io
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, r"D:/work 111/2026-08-03-17-38-51")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config  # noqa: E402
from core import detector  # noqa: E402

ROOT = r"D:/work 111/2026-08-03-17-38-51"
_orig = detector._apply_plate_completion_prior
CAPTURED = {}


def _spy(all_regions, mask_parts, h, w, corner_ratio, search_bottom, preset=None):
    fw = max(8, int(round(340 * min(h, w) / 2048.0)))
    fh = max(8, int(round(128 * min(h, w) / 2048.0)))
    bx, by = w - fw, h - fh
    n_before = len(all_regions)
    _orig(all_regions, mask_parts, h, w, corner_ratio, search_bottom, preset=preset)
    fired = len(all_regions) > n_before
    sig = [r for r in all_regions if r.kind in (detector.KIND_TRANSLUCENT, detector.KIND_EDGE_TEXT)]
    rows = []
    for r in sig:
        ox = max(0, min(r.x + r.w, bx + fw) - max(r.x, bx))
        oy = max(0, min(r.y + r.h, by + fh) - max(r.y, by))
        ov = ox * oy / max(1, r.w * r.h)
        if ov > 0 or (r.x + r.w > bx - 200 and r.y + r.h > by - 200):
            rows.append((r.kind, (r.x, r.y, r.w, r.h), round(ov, 2)))
    CAPTURED["last"] = {"footprint": (bx, by, fw, fh), "fired": fired, "signals": rows}
    return None


detector._apply_plate_completion_prior = _spy


def probe(path, tag):
    config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = True
    try:
        arr = np.asarray(Image.open(path).convert("RGB"))
        detector.detect_watermark_mask(arr, preset="general", use_ocr=False)
    finally:
        config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = False
    c = CAPTURED["last"]
    print(f"-- {tag} fired={c['fired']} footprint={c['footprint']}")
    for kind, box, ov in c["signals"]:
        print(f"   {kind:12s} {box} fp_ov={ov}")


root = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
for im in man["images"]:
    if str(im["id"]).startswith("ai_logo"):
        probe(os.path.join(root, im["path"]), im["id"])

croot = os.path.join(ROOT, "benchmark_real", "clean")
cman = json.load(open(os.path.join(croot, "manifest.json"), encoding="utf-8"))
for im in cman["images"]:
    probe(os.path.join(croot, im["path"]), im["id"])
