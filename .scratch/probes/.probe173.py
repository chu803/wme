# -*- coding: utf-8 -*-
"""扫描 DETECT_PLATE_CLOSE_K × DETECT_PLATE_MAX_ASPECT 对角标掩码覆盖率的影响。"""
import json
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import config  # noqa: E402
from core.detector import detect_watermark_mask  # noqa: E402

BENCH = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(BENCH, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(BENCH, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}
pick = ["ai_logo_05_fruit_platter", "ai_logo_08_abstract_flui", "ai_logo_09_vintage_typew",
        "ai_logo_02_snow_mountain", "ai_logo_04_neon_street_n", "ai_logo_03_white_coffee_"]

imgs = {}
for iid in pick:
    im = next(i for i in man["images"] if i["id"] == iid)
    imgs[iid] = np.asarray(Image.open(os.path.join(BENCH, im["path"])).convert("RGB"))

lines = []
for ck, asp in [(0, 2.5), (0, 3.2), (21, 3.2), (35, 3.2), (21, 2.5)]:
    config.DETECT_PLATE_CLOSE_K = ck
    config.DETECT_PLATE_MAX_ASPECT = asp
    ti = tt = 0
    parts = []
    for iid in pick:
        tx, ty, tw, th = tby[iid]["watermarks"][0]["bbox"]
        m = detect_watermark_mask(imgs[iid], preset="general", use_ocr=False).mask
        if m.ndim == 3:
            m = m[:, :, 0]
        inter = int((m[ty:ty + th, tx:tx + tw] > 0).sum())
        ti += inter
        tt += tw * th
        parts.append(f"{iid[8:18]}={inter / (tw * th):.2f}")
    lines.append(f"close_k={ck:2d} max_aspect={asp}: MICRO_mask_recall={ti / tt:.3f} | " + " ".join(parts))
    with open(".probe173.result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("\n".join(lines))
