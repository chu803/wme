# -*- coding: utf-8 -*-
"""细化量取：同一窗口裁 4 张不同底色的图，叠放对比，验证徽标位置一致 + 精确 bbox。"""
import os

import numpy as np
from PIL import Image, ImageDraw

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
pick = [files[0], files[1], files[3], files[6]]

X0, Y0, X1, Y1 = 1640, 1855, 1990, 1995
tiles = []
for f in pick:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB"))
    c = a[Y0:Y1, X0:X1]
    S = 2
    big = np.kron(c, np.ones((S, S, 1))).astype(np.uint8)
    im = Image.fromarray(big)
    d = ImageDraw.Draw(im)
    for gx in range(X0, X1 + 1, 25):
        px = (gx - X0) * S
        d.line([(px, 0), (px, 12)], fill=(255, 0, 0), width=1)
        d.text((px + 1, 12), str(gx), fill=(255, 0, 0))
    for gy in range(Y0, Y1 + 1, 25):
        py = (gy - Y0) * S
        d.line([(0, py), (12, py)], fill=(0, 128, 255), width=1)
        d.text((14, py), str(gy), fill=(0, 128, 255))
    tiles.append((f, im))

w = max(im.width for _, im in tiles)
canvas = Image.new("RGB", (w, sum(im.height + 18 for _, im in tiles)), (255, 255, 255))
d = ImageDraw.Draw(canvas)
y = 0
for f, im in tiles:
    d.text((2, y + 3), f, fill=(0, 0, 0))
    canvas.paste(im, (0, y + 18))
    y += im.height + 18
canvas.save(".refine172_view.png")
print("saved .refine172_view.png")
