# -*- coding: utf-8 -*-
"""精修量取角标板：行/列亮度剖面「偏离局部背景」的最长区间 = 板（半透明板对比度低，阈值取小）。

输出 {filename: [x0,y0,x1,y1]} 与网格复核图。
"""
import json
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image, ImageDraw

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]


def sm(v, k=9):
    return np.convolve(v, np.ones(k) / k, mode="same")


def longest_run(dev, thr):
    m = dev > thr
    best = (0, 0, 0)
    i = 0
    while i < len(m):
        if m[i]:
            j = i
            while j < len(m) and m[j]:
                j += 1
            if j - i > best[0]:
                best = (j - i, i, j)
            i = j
        else:
            i += 1
    return best[1], best[2]


boxes = {}
for f in files:
    g = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).astype(np.float32).mean(axis=2)
    # 行剖面
    row = sm(g[1945, 1580:2048])
    bg = float(np.median(row[:70]))
    i0, i1 = longest_run(np.abs(row - bg), 5.0)
    l, r = 1580 + i0, 1580 + i1
    # 列剖面（板内 x = l+160）
    cx = min(2040, l + 160)
    col = sm(g[1850:2048, cx])
    bgy = float(np.median(col[-70:]))
    j0, j1 = longest_run(np.abs(col - bgy), 5.0)
    t, b = 1850 + j0, 1850 + j1
    boxes[f] = [l, t, r, b]

WX0, WY0, WX1, WY1 = 1580, 1840, 2048, 2020
S = 2
tiles = []
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).copy()
    x0, y0, x1, y1 = boxes[f]
    cv2.rectangle(a, (x0, y0), (x1, y1), (255, 0, 0), 2)
    tiles.append(np.kron(a[WY0:WY1, WX0:WX1], np.ones((S, S, 1))).astype(np.uint8))
th, tw = tiles[0].shape[:2]
grid = np.full((th * 5 + 30, tw * 2 + 10, 3), 255, np.uint8)
for i, t in enumerate(tiles):
    r2, c2 = divmod(i, 2)
    grid[r2 * (th + 6):r2 * (th + 6) + th, c2 * (tw + 6):c2 * (tw + 6) + tw] = t
Image.fromarray(grid).save(".plate172_view.png")
with open(".plate172.result.txt", "w", encoding="utf-8") as fh:
    fh.write(json.dumps(boxes, ensure_ascii=False, indent=1))
print(json.dumps({k: [v[2] - v[0], v[3] - v[1]] for k, v in boxes.items()}, ensure_ascii=False))
