# -*- coding: utf-8 -*-
"""导出右下角裁剪（×3）+ 原图坐标刻度，供人眼量取徽标 bbox。"""
import os

import numpy as np
from PIL import Image, ImageDraw

SRC = r"D:\Desktop\照片"
f0 = "ai_logo_03_white_coffee_cup.jpg"          # 白底：徽标最不易辨认，用它量最稳
img = np.asarray(Image.open(os.path.join(SRC, f0)).convert("RGB"))

CX0, CY0, CX1, CY1 = 1620, 1860, 2048, 2048     # 428 x 188 裁剪窗口
crop = img[CY0:CY1, CX0:CX1]
S = 3
big = np.kron(crop, np.ones((S, S, 1))).astype(np.uint8)
canvas = Image.fromarray(big)
W, H = canvas.size

# 上/左描原图坐标刻度（每 50px 一条），供读像素坐标
draw = ImageDraw.Draw(canvas)
for gx in range(CX0, CX1 + 1, 50):
    px = (gx - CX0) * S
    draw.line([(px, 0), (px, 12)], fill=(255, 0, 0), width=2)
    draw.text((px + 2, 12), str(gx), fill=(255, 0, 0))
for gy in range(CY0, CY1 + 1, 50):
    py = (gy - CY0) * S
    draw.line([(0, py), (12, py)], fill=(0, 128, 255), width=2)
    draw.text((14, py), str(gy), fill=(0, 128, 255))

canvas.save(".crop172_view.png")
print(f"cropped ({CX0},{CY0})-({CX1},{CY1}) -> .crop172_view.png {W}x{H}")
