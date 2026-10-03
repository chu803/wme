# -*- coding: utf-8 -*-
"""边缘模板匹配：徽标颜色随底图变化，故在 Canny 边缘图上匹配其**结构**。

模板用「手工量的徽标区」粗略取，再对每张图在 ±60px 内精定位；输出 bbox + 分数。
"""
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
imgs = {f: np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")) for f in files}

# 用一张高对比图（白雪）粗取模板，范围放宽以含完整徽标
T = "ai_logo_02_snow_mountain_lake.jpg"
TB = (1650, 1870, 2010, 2000)
tpl = cv2.cvtColor(imgs[T][TB[1]:TB[3], TB[0]:TB[2]], cv2.COLOR_RGB2GRAY)
tpl_e = cv2.Canny(tpl, 40, 120)
th, tw = tpl.shape


def edges(g):
    return cv2.Canny(g, 40, 120)


out = []
boxes = {}
sx0, sy0 = TB[0] - 60, TB[1] - 60
SW, SH = tw + 120, th + 120
for f in files:
    g = cv2.cvtColor(imgs[f], cv2.COLOR_RGB2GRAY)
    srch = edges(g[sy0:sy0 + SH, sx0:sx0 + SW])
    res = cv2.matchTemplate(srch, tpl_e, cv2.TM_CCORR_NORMED)
    _mn, mx, _ml, ml = cv2.minMaxLoc(res)
    x, y = sx0 + ml[0], sy0 + ml[1]
    boxes[f] = (x, y, x + tw, y + th)
    out.append(f"{f}\tscore={mx:.3f}\tbbox=({x},{y},{x + tw},{y + th})")

# 2x5 网格复核
CY0, CY1 = TB[1] - 40, TB[3] + 60
CX0, CX1 = TB[0] - 40, TB[2] + 40
tiles = []
for f in files:
    a = imgs[f].copy()
    x0, y0, x1, y1 = boxes[f]
    cv2.rectangle(a, (x0, y0), (x1, y1), (255, 0, 0), 3)
    tiles.append(a[CY0:CY1, CX0:CX1])
th2, tw2 = tiles[0].shape[:2]
grid = np.full((th2 * 5 + 40, tw2 * 2 + 10, 3), 255, np.uint8)
for i, t in enumerate(tiles):
    r, c = divmod(i, 2)
    grid[r * (th2 + 8):r * (th2 + 8) + th2, c * (tw2 + 10):c * (tw2 + 10) + tw2] = t
Image.fromarray(grid).save(".edge172_view.png")

with open(".edge172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("done")
