# -*- coding: utf-8 -*-
"""逐图定位：边缘密度峰值 = 徽标中心；尺寸用剖面客观测得 (296x102)。"""
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
W, H = 296, 102
out = []
boxes = {}
for f in files:
    g = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).astype(np.uint8)
    gray = cv2.cvtColor(g, cv2.COLOR_RGB2GRAY)
    e = cv2.Canny(gray, 50, 150).astype(np.float32)
    dens = cv2.boxFilter(e, -1, (W, H), normalize=True)
    sub = dens[1690:2048, 1490:2048]
    _mn, _mx, _ml, ml = cv2.minMaxLoc(sub)
    cx, cy = 1490 + ml[0], 1690 + ml[1]
    b = (cx - W // 2, cy - H // 2, cx + W // 2, cy + H // 2)
    boxes[f] = b
    out.append(f"{f}\tdensity={_mx:.1f}\tbbox={b}")

CX0, CY0, CX1, CY1 = 1620, 1840, 2048, 2048
tiles = []
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).copy()
    x0, y0, x1, y1 = boxes[f]
    cv2.rectangle(a, (x0, y0), (x1, y1), (255, 0, 0), 3)
    tiles.append(a[CY0:CY1, CX0:CX1])
th, tw = tiles[0].shape[:2]
grid = np.full((th * 5 + 40, tw * 2 + 10, 3), 255, np.uint8)
for i, t in enumerate(tiles):
    r, c = divmod(i, 2)
    grid[r * (th + 8):r * (th + 8) + th, c * (tw + 10):c * (tw + 10) + tw] = t
Image.fromarray(grid).save(".dens172_view.png")

with open(".dens172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("done")
