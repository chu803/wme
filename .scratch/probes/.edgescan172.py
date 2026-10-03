# -*- coding: utf-8 -*-
"""逐图量取徽标板 bbox：用亮度剖面的强梯度定位板的左/右/上/下边界（独立于检测器）。"""
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]


def smooth(v, k=7):
    return np.convolve(v, np.ones(k) / k, mode="same")


def _argmax_range(d, lo, hi):
    seg = d[lo:hi]
    return lo + int(np.argmax(seg))


out = []
boxes = {}
for f in files:
    g = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).astype(np.float32).mean(axis=2)
    # 1) 行剖面（y=1943）→ 左右边界
    row = smooth(g[1943, 1560:2048])
    dr = np.abs(np.gradient(row))
    lx = 1560 + _argmax_range(dr, 60, 240)     # 1620..1800
    rx = 1560 + _argmax_range(dr, 360, 488)    # 1920..2048
    # 2) 竖剖面（在板左内边 x=lx+22 处）→ 上下边界
    cx = min(2047, lx + 22)
    col = smooth(g[1830:2048, cx])
    dc = np.abs(np.gradient(col))
    ty = 1830 + _argmax_range(dc, 20, 110)     # 1850..1940
    by = 1830 + _argmax_range(dc, 120, 218)    # 1950..2048
    boxes[f] = (lx, ty, rx, by)
    out.append(f"{f}\tplate≈({lx},{ty})-({rx},{by}) w={rx - lx} h={by - ty}")

# 网格复核（每图各自 bbox）
CX0, CY0, CX1, CY1 = 1620, 1850, 2048, 2040
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
Image.fromarray(grid).save(".edgescan172_view.png")

with open(".edgescan172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("done")
