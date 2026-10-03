# -*- coding: utf-8 -*-
"""行剖面最长「偏离背景」区间 = 徽标板左右边界（对高对比图有效）。"""
import os

import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
pick = ["ai_logo_02_snow_mountain_lake.jpg", "ai_logo_04_neon_street_night.jpg",
        "ai_logo_09_vintage_typewriter.jpg", "ai_logo_10_lavender_hot_air_balloon.jpg",
        "ai_logo_05_fruit_platter.jpg"]


def smooth(v, k=9):
    return np.convolve(v, np.ones(k) / k, mode="same")


out = []
for f in pick:
    g = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).astype(np.float32).mean(axis=2)
    row = smooth(g[1943, 1560:2048])
    bg = float(np.median(row))
    dev = np.abs(row - bg)
    thr = max(12.0, float(dev.max()) * 0.5)
    m = dev > thr
    # 最长连续 True 段
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
    _ln, i0, i1 = best
    out.append(f"{f}\tbg={bg:.0f}\tplate_x=[{1560 + i0},{1560 + i1}] w={i1 - i0}")
    # 竖剖面（板内 x = 左边界+30）
    cx = min(2047, 1560 + i0 + 30)
    col = smooth(g[1830:2048, cx])
    bgy = float(np.median(col))
    devc = np.abs(col - bgy)
    mc = devc > max(12.0, float(devc.max()) * 0.5)
    bestc = (0, 0, 0)
    i = 0
    while i < len(mc):
        if mc[i]:
            j = i
            while j < len(mc) and mc[j]:
                j += 1
            if j - i > bestc[0]:
                bestc = (j - i, i, j)
            i = j
        else:
            i += 1
    out.append(f"    col x={cx}: plate_y=[{1830 + bestc[1]},{1830 + bestc[2]}] h={bestc[2] - bestc[1]}")

with open(".scan2_172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("done")
