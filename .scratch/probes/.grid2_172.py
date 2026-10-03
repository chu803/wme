# -*- coding: utf-8 -*-
"""同一个候选 bbox 叠加到 10 张图上，复核贴合度与位置一致性。"""
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
X0, Y0, X1, Y1 = 1700, 1892, 1996, 1994        # 由剖面扫描得到（+3px 余量）

CX0, CY0, CX1, CY1 = 1620, 1840, 2048, 2048
tiles = []
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).copy()
    cv2.rectangle(a, (X0, Y0), (X1, Y1), (255, 0, 0), 3)
    tiles.append(a[CY0:CY1, CX0:CX1])

th, tw = tiles[0].shape[:2]
grid = np.full((th * 5 + 40, tw * 2 + 10, 3), 255, np.uint8)
for i, t in enumerate(tiles):
    r, c = divmod(i, 2)
    grid[r * (th + 8):r * (th + 8) + th, c * (tw + 10):c * (tw + 10) + tw] = t
Image.fromarray(grid).save(".grid2_172_view.png")
print("saved", grid.shape)
