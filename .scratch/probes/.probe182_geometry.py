# -*- coding: utf-8 -*-
"""阶段 182 探针二：量测 translucent/edge_text 证据区域与牌足迹的几何关系。

假设：真实豆包牌的 logo 证据在足迹**内部**（牌内）；syn_02/syn_05 的文字证据
在足迹**外部**（暗块+白字，牌足迹位置只有暗块/背景）。
对每张图（lever OFF 跑检测器）列出信号区域及其与足迹的重叠率。
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
from core.detector import (  # noqa: E402
    KIND_CORNER_LOGO,
    KIND_EDGE_TEXT,
    KIND_TRANSLUCENT,
    detect_watermark_mask,
)

ROOT = r"D:/work 111/2026-08-03-17-38-51"
KINDS = {KIND_TRANSLUCENT: "translucent", KIND_EDGE_TEXT: "edge_text",
         KIND_CORNER_LOGO: "corner_logo"}


def probe(path, tag):
    saved = config.DETECT_PLATE_COMPLETE_FROM_SIGNAL
    config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = False
    try:
        arr = np.asarray(Image.open(path).convert("RGB"))
        h, w = arr.shape[:2]
        fw = max(8, int(round(340 * min(h, w) / 2048.0)))
        fh = max(8, int(round(128 * min(h, w) / 2048.0)))
        bx, by = w - fw, h - fh
        res = detect_watermark_mask(arr, preset="general", use_ocr=False)
        sig = [r for r in res.regions if r.kind in (KIND_TRANSLUCENT, KIND_EDGE_TEXT)]
        plate = [r for r in res.regions if r.kind == KIND_CORNER_LOGO]
        print(f"-- {tag} footprint=({bx},{by},{fw},{fh}) plate_n={len(plate)}")
        for r in sig:
            ox = max(0, min(r.x + r.w, bx + fw) - max(r.x, bx))
            oy = max(0, min(r.y + r.h, by + fh) - max(r.y, by))
            ov = ox * oy / max(1, r.w * r.h)
            inside = "IN " if ov >= 0.5 else ("part" if ov > 0 else "OUT")
            print(f"   {KINDS.get(r.kind, r.kind):12s} ({r.x},{r.y},{r.w},{r.h}) fp_ov={ov:.2f} {inside}")
    finally:
        config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = saved


root = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
for im in man["images"]:
    if str(im["id"]).startswith("ai_logo"):
        probe(os.path.join(root, im["path"]), im["id"])

croot = os.path.join(ROOT, "benchmark_real", "clean")
cman = json.load(open(os.path.join(croot, "manifest.json"), encoding="utf-8"))
for im in cman["images"]:
    probe(os.path.join(croot, im["path"]), im["id"])
