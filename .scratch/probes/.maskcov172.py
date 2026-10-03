# -*- coding: utf-8 -*-
"""诊断：检测器「掩码」对角标真值框的覆盖（mask recall），区分「掩码不足」vs「仅区域 bbox 不足」。"""
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

lines = []
tot_i = tot_t = 0
for im in man["images"]:
    iid = im["id"]
    if iid not in tby:
        continue
    tx, ty, tw, th = tby[iid]["watermarks"][0]["bbox"]
    rgb = np.asarray(Image.open(os.path.join(BENCH, im["path"])).convert("RGB"))
    m = detect_watermark_mask(rgb, preset="general", use_ocr=False).mask
    if m.ndim == 3:
        m = m[:, :, 0]
    box = m[ty:ty + th, tx:tx + tw] > 0
    inter = int(box.sum())
    tarea = tw * th
    marea = int((m > 0).sum())
    tot_i += inter
    tot_t += tarea
    lines.append(f"{iid}: mask_recall={inter / tarea:.3f} "
                 f"mask_cov={marea / m.size:.4f} truth_cov={tarea / m.size:.4f} "
                 f"(inter={inter}/{tarea}, pred={marea})")
lines.append(f"\nMICRO mask_recall = {tot_i / tot_t:.4f}  (sum inter {tot_i} / sum truth {tot_t})")
with open(".maskcov172.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("\n".join(l.encode("ascii", "replace").decode("ascii") for l in lines))
