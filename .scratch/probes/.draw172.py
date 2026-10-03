# -*- coding: utf-8 -*-
"""在候选 bbox 处画红框，供人眼判定是否贴合徽标底板。"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
pick = [files[1], files[2], files[3]]           # 白杯 / 霓虹夜 / 水果木纹

BX0, BY0, BX1, BY1 = (int(sys.argv[1]), int(sys.argv[2]),
                      int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 \
    else (1670, 1880, 1980, 1970)

X0, Y0, X1, Y1 = 1600, 1840, 2048, 2032
tiles = []
for f in pick:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).copy()
    ImageDraw.Draw(Image.fromarray(a)).rectangle([BX0, BY0, BX1, BY1], outline=(255, 0, 0), width=3)
    a = np.asarray(Image.fromarray(a))
    c = a[Y0:Y1, X0:X1]
    S = 2
    im = Image.fromarray(np.kron(c, np.ones((S, S, 1))).astype(np.uint8))
    d = ImageDraw.Draw(im)
    for gx in range(X0, X1 + 1, 50):
        d.line([((gx - X0) * S, 0), ((gx - X0) * S, 10)], fill=(0, 200, 0), width=1)
        d.text(((gx - X0) * S + 1, 10), str(gx), fill=(0, 200, 0))
    for gy in range(Y0, Y1 + 1, 25):
        d.line([(0, (gy - Y0) * S), (10, (gy - Y0) * S)], fill=(0, 128, 255), width=1)
        d.text((12, (gy - Y0) * S), str(gy), fill=(0, 128, 255))
    tiles.append((f, im))

w = max(im.width for _, im in tiles)
canvas = Image.new("RGB", (w, sum(im.height + 16 for _, im in tiles)), (255, 255, 255))
d = ImageDraw.Draw(canvas)
d.text((2, 1), f"bbox=({BX0},{BY0})-({BX1},{BY1}) w={BX1 - BX0} h={BY1 - BY0}", fill=(0, 0, 0))
y = 16
for f, im in tiles:
    canvas.paste(im, (0, y))
    y += im.height + 16
canvas.save(".draw172_view.png")
print("saved")
