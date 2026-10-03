# -*- coding: utf-8 -*-
"""单图高倍 + 20px 细网格，供精确读徽标板边界。"""
import os

import numpy as np
from PIL import Image, ImageDraw

SRC = r"D:\Desktop\照片"
f = "ai_logo_04_neon_street_night.jpg"
X0, Y0, X1, Y1 = 1600, 1860, 2048, 2010
img = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB"))[Y0:Y1, X0:X1]
S = 3
big = np.kron(img, np.ones((S, S, 1))).astype(np.uint8)
canvas = Image.fromarray(big)
d = ImageDraw.Draw(canvas)
for gx in range(X0, X1 + 1, 20):
    px = (gx - X0) * S
    d.line([(px, 0), (px, 16)], fill=(255, 40, 40), width=1)
    if (gx - X0) % 100 == 0:
        d.text((px + 1, 17), str(gx), fill=(255, 40, 40))
for gy in range(Y0, Y1 + 1, 20):
    py = (gy - Y0) * S
    d.line([(0, py), (16, py)], fill=(40, 160, 255), width=1)
    d.text((18, py), str(gy), fill=(40, 160, 255))
canvas.save(".zoom172_view.png")
print("saved", canvas.size)
