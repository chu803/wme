# -*- coding: utf-8 -*-
"""诊断 4 张低对比底：白牌亮判据在哪个 V 门限下才覆盖整牌。"""
import json
import os
import sys

import cv2  # type: ignore
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

BENCH = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(BENCH, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(BENCH, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}
pick = ["ai_logo_06_flower_field_", "ai_logo_07_glass_curtain",
        "ai_logo_08_abstract_flui", "ai_logo_09_vintage_typew",
        "ai_logo_04_neon_street_n"]     # neon 作为「能工作」的对照
lines = []
for iid in pick:
    im = next(i for i in man["images"] if i["id"] == iid)
    arr = np.asarray(Image.open(os.path.join(BENCH, im["path"])).convert("RGB"))
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    v, s = hsv[:, :, 2].astype(int), hsv[:, :, 1].astype(int)
    x, y, w, h = tby[iid]["watermarks"][0]["bbox"]
    vb = v[y:y + h, x:x + w]
    sb = s[y:y + h, x:x + w]
    parts = []
    for thr in (140, 120, 100, 80):
        m = ((vb >= thr) & (sb <= 80))
        # 该门限下，牌内最大连通洞的比例（越大=越成片）
        u = cv2.connectedComponentsWithStats(m.astype(np.uint8), 8)
        big = max([u[2][i][4] for i in range(1, u[0])], default=0)
        parts.append(f"V>={thr}: fill={m.mean():.2f} maxcc={big / (w * h):.2f}")
    lines.append(f"{iid}: Vmean={vb.mean():.0f} Vp90={np.percentile(vb, 90):.0f} "
                 f"Smean={sb.mean():.0f} | " + " | ".join(parts))

with open(".probe174.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("\n".join(lines))
