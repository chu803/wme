# -*- coding: utf-8 -*-
"""逐图定位徽标 bbox：以图 0 的徽标区为模板做归一化互相关（独立于检测器）。"""
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
imgs = {f: np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")) for f in files}

BX0, BY0, BX1, BY1 = 1676, 1888, 1968, 1962
tw, th = BX1 - BX0, BY1 - BY0
tpl = cv2.cvtColor(imgs[files[0]][BY0:BY1, BX0:BX1], cv2.COLOR_RGB2GRAY)

sx0, sy0 = 1636, 1848                 # 紧窗口（模板尺寸 + 各 80px 余量，允许 ±40 位移）
SW, SH = tw + 80, th + 80
out = []
boxes = {}
for f in files:
    g = cv2.cvtColor(imgs[f], cv2.COLOR_RGB2GRAY)
    srch = g[sy0:sy0 + SH, sx0:sx0 + SW]
    res = cv2.matchTemplate(srch, tpl, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _ml, ml = cv2.minMaxLoc(res)
    x, y = sx0 + ml[0], sy0 + ml[1]
    boxes[f] = (x, y, x + tw, y + th)
    out.append(f"{f}\tscore={mx:.3f}\tbbox=({x},{y},{x + tw},{y + th})")

# 一致性：把 10 个 bbox 叠到图 0 上渲染，供人眼复核
vis = imgs[files[0]].copy()
for f in files:
    x0, y0, x1, y1 = boxes[f]
    cv2.rectangle(vis, (x0, y0), (x1, y1), (255, 0, 0), 2)
crop = vis[1840:2048, 1560:2048]
big = np.kron(crop, np.ones((2, 2, 1))).astype(np.uint8)
Image.fromarray(big).save(".tpl172_view.png")

with open(".tpl172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("done")
