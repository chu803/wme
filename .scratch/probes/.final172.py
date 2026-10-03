# -*- coding: utf-8 -*-
"""最终逐图 bbox：行/列亮度剖面的偏离区间；剖面失效（纯色底）时回退到统一值。"""
import json
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
FALLBACK = (1703, 1890, 1995, 1996)


def sm(v, k=7):
    return np.convolve(v, np.ones(k) / k, mode="same")


def run_range(prof, base):
    bg = float(np.median(prof[:60])) if base == "row" else float(np.median(prof[-60:]))
    pl = float(np.percentile(prof, 90))
    if abs(pl - bg) < 15:
        return None
    thr = (bg + pl) / 2
    idx = np.where(prof > thr)[0]
    if not len(idx):
        return None
    return int(idx.min()), int(idx.max())


boxes = {}
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).astype(np.float32).mean(axis=2)
    xs = []
    for y in (1915, 1940, 1965):
        rr = run_range(sm(a[y, 1560:2048]), "row")
        if rr and 120 < rr[1] - rr[0] < 430:
            xs.append((1560 + rr[0], 1560 + rr[1]))
    if xs:
        l = int(np.median([x[0] for x in xs]))
        r = int(np.median([x[1] for x in xs]))
    else:
        l, r = FALLBACK[0], FALLBACK[2]
    ys = []
    for x in (l + 25, l + 55):
        if x >= 2048:
            continue
        rr = run_range(sm(a[1830:2048, x]), "col")
        if rr and 40 < rr[1] - rr[0] < 170:
            ys.append((1830 + rr[0], 1830 + rr[1]))
    if ys:
        t = int(np.median([y[0] for y in ys]))
        b = int(np.median([y[1] for y in ys]))
    else:
        t, b = FALLBACK[1], FALLBACK[3]
    boxes[f] = [l, t, r, b]

# 网格复核
CX0, CY0, CX1, CY1 = 1600, 1850, 2048, 2040
tiles = []
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).copy()
    x0, y0, x1, y1 = boxes[f]
    cv2.rectangle(a, (x0, y0), (x1, y1), (255, 0, 0), 3)
    tiles.append(a[CY0:CY1, CX0:CX1])
th, tw = tiles[0].shape[:2]
grid = np.full((th * 5 + 40, tw * 2 + 10, 3), 255, np.uint8)
for i, t2 in enumerate(tiles):
    r, c = divmod(i, 2)
    grid[r * (th + 8):r * (th + 8) + th, c * (tw + 10):c * (tw + 10) + tw] = t2
Image.fromarray(grid).save(".final172_view.png")

with open(".final172.result.txt", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(boxes, ensure_ascii=False, indent=1))
print(json.dumps(boxes, ensure_ascii=False))
