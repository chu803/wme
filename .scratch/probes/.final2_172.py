# -*- coding: utf-8 -*-
"""最终定稿 bbox：x 取徽标板中线行的偏离区间(±12 余量)，y 取统一竖直范围(±余量)。

徽标板竖直位置跨图一致（≈1890..1996）；水平位置随底图略有漂移，故逐图量 x。
"""
import json
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
PAD_X = 12
TY, BY = 1884, 2002
FB = (1700, 1996)


def sm(v, k=7):
    return np.convolve(v, np.ones(k) / k, mode="same")


boxes = {}
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).astype(np.float32).mean(axis=2)
    p = sm(a[1940, 1560:2048])
    bg = float(np.median(p[:60]))
    pl = float(np.percentile(p, 90))
    thr = (bg + pl) / 2
    idx = np.where(p > thr)[0]
    if len(idx) and 120 < idx.max() - idx.min() < 430:
        l, r = 1560 + int(idx.min()), 1560 + int(idx.max())
    else:
        l, r = FB
    l = max(0, l - PAD_X)
    r = min(2047, r + PAD_X)
    boxes[f] = [l, TY, r, BY]

CX0, CY0, CX1, CY1 = 1580, 1840, 2048, 2048
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
Image.fromarray(grid).save(".final2_172_view.png")

with open(".final2_172.result.txt", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(boxes, ensure_ascii=False, indent=1))
print(json.dumps(boxes, ensure_ascii=False))
