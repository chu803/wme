# -*- coding: utf-8 -*-
"""D2 可行性探针：局部提亮 lift = V - blur(V,k) > δ
  · 角标框内：最大「与真值重叠」连通块占真值面积比（越高越好 → 能圈住牌）
  · 四角搜索区内、且**不与真值重叠**的最大连通块占搜索区比（越低越好 → 误报）
"""
import json
import os
import sys

import cv2  # type: ignore
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
B = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(B, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(B, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}
labeled = [i for i in man["images"] if i["id"] in tby]
clean = [i for i in man["images"] if i["id"] not in tby][:4]


def stats(arr, box, k, delta):
    v = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[:, :, 2].astype(np.float32)
    lift = v - cv2.blur(v, (k, k))
    m = (lift > delta).astype(np.uint8)
    h, w = m.shape
    r = 0.25
    # 四角搜索区并集
    reg = np.zeros_like(m)
    reg[:int(h * r), :int(w * r)] = 1
    reg[:int(h * r), w - int(w * r):] = 1
    reg[h - int(h * r):, :int(w * r)] = 1
    reg[h - int(h * r):, w - int(w * r):] = 1
    reg &= m
    num, lab, st, _ = cv2.connectedComponentsWithStats(reg, 8)
    tmask = np.zeros_like(m)
    if box:
        x, y, bw, bh = box
        tmask[y:y + bh, x:x + bw] = 1
    best_t = 0
    best_f = 0
    for i in range(1, num):
        comp = (lab == i)
        ov = int(np.logical_and(comp, tmask > 0).sum())
        if ov > best_t:
            best_t = ov
        else:
            best_f = max(best_f, int(st[i][4]))
    cov = best_t / float(bw * bh) if box else 0.0
    return cov, best_f / float(reg.size + 1)


out = []
for k in (301, 501):
    for d in (10, 16):
        hits, fps = [], []
        for im in labeled:
            arr = np.asarray(Image.open(os.path.join(B, im["path"])).convert("RGB"))
            box = tby[im["id"]]["watermarks"][0]["bbox"]
            c, fp = stats(arr, box, k, d)
            hits.append((im["id"][8:18], round(c, 2)))
        for im in clean:
            arr = np.asarray(Image.open(os.path.join(B, im["path"])).convert("RGB"))
            _c, fp = stats(arr, None, k, d)
            fps.append(round(fp, 3))
        out.append(f"k={k} d={d}: badge_cov=" + " ".join(f"{a}={b}" for a, b in hits)
                   + " | clean_FP=" + str(fps))
with open(".probe176.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out).encode("ascii", "replace").decode("ascii") + "\n")
print("\n".join(out))
