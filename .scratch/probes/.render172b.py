# -*- coding: utf-8 -*-
"""渲染 2x5 网格：每图叠加其 bbox（红），窗口带原图坐标刻度，供精修目视复核。

用法： python .render172b.py [boxes.json] [out.png]
boxes.json = {"<filename>": [x0,y0,x1,y1], ...}（缺省则从 ground_truth.json 取）
"""
import json
import os
import sys

import cv2  # type: ignore
import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = r"D:\Desktop\照片"
BENCH = os.path.join(ROOT, "benchmark_real")

man = json.load(open(os.path.join(BENCH, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(BENCH, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}
boxes = {}
for im in man["images"]:
    if im["id"] in tby:
        b = tby[im["id"]]["watermarks"][0]["bbox"]
        boxes[im["filename"]] = [b[0], b[1], b[0] + b[2], b[1] + b[3]]

if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
    boxes.update(json.load(open(sys.argv[1], encoding="utf-8")))
out_png = sys.argv[2] if len(sys.argv) > 2 else ".render172b_view.png"

WX0, WY0, WX1, WY1 = 1600, 1860, 2030, 2010
S = 2
tiles = []
for fn in sorted(boxes):
    a = np.asarray(Image.open(os.path.join(SRC, fn)).convert("RGB")).copy()
    x0, y0, x1, y1 = boxes[fn]
    cv2.rectangle(a, (x0, y0), (x1, y1), (255, 0, 0), 2)
    tile = np.kron(a[WY0:WY1, WX0:WX1], np.ones((S, S, 1))).astype(np.uint8)
    img = Image.fromarray(tile)
    d = ImageDraw.Draw(img)
    for gx in range(WX0, WX1 + 1, 25):
        px = (gx - WX0) * S
        d.line([(px, 0), (px, 14)], fill=(0, 255, 0), width=1)
        if (gx - WX0) % 100 == 0:
            d.text((px + 1, 15), str(gx), fill=(0, 255, 0))
    for gy in range(WY0, WY1 + 1, 25):
        py = (gy - WY0) * S
        d.line([(0, py), (14, py)], fill=(255, 220, 0), width=1)
        d.text((16, py), str(gy), fill=(255, 220, 0))
    tiles.append(img)

tw = max(t.width for t in tiles)
th = max(t.height for t in tiles)
cols = 2
rows = (len(tiles) + cols - 1) // cols
grid = Image.new("RGB", (cols * (tw + 6), rows * (th + 6)), (255, 255, 255))
for i, t in enumerate(tiles):
    r, c = divmod(i, cols)
    grid.paste(t, (c * (tw + 6), r * (th + 6)))
grid.save(out_png)
print("saved", out_png, grid.size)
