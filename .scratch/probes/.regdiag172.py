# -*- coding: utf-8 -*-
"""逐图打印：真值框 vs 检测器预测区域（含与真值的 IoU、是否 >=0.3 命中）。"""
import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from core.detector import detect_watermark_mask  # noqa: E402

BENCH = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(BENCH, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(BENCH, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}


def iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, x1 - x0), max(0, y1 - y0)
    inter = iw * ih
    return inter / (aw * ah + bw * bh - inter) if inter else 0.0


lines = []
for im in man["images"]:
    iid = im["id"]
    if iid not in tby:
        continue
    tb = tby[iid]["watermarks"][0]["bbox"]
    rgb = np.asarray(Image.open(os.path.join(BENCH, im["path"])).convert("RGB"))
    res = detect_watermark_mask(rgb, preset="general", use_ocr=False)
    regs = [[int(r.x), int(r.y), int(r.w), int(r.h)] for r in res.regions]
    ious = sorted((round(iou(tb, r), 3) for r in regs), reverse=True)
    hit = sum(1 for v in ious if v >= 0.3)
    lines.append(f"{iid}: truth={tb} n_pred={len(regs)} hit(iou>=0.3)={hit} "
                 f"top_iou={ious[:5]}")
    lines.append(f"    preds={regs[:12]}")

with open(".regdiag172.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("done")
