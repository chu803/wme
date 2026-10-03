# -*- coding: utf-8 -*-
"""独立测量角落水印 bbox：跨 10 张图取右下区域，平台徽标像素一致 ⇒ 低方差区 = 徽标。

不使用检测器（避免循环论证）。同时导出放大裁剪图供人眼复核。
"""
import os

import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]

X0, Y0 = 1500, 1700           # 右下区域搜索窗口（2048² 图中）
regions = []
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB"))
    regions.append(a[Y0:2048, X0:2048].astype(np.float32))
stack = np.stack(regions)                       # (N, h, w, 3)
std = stack.std(axis=0).mean(axis=2)            # (h, w) 跨图标准差

out = [f"n_images={len(files)} region=({X0},{Y0})-2048", f"std_min={std.min():.2f} std_max={std.max():.2f}"]
for thr in (2.0, 3.0, 5.0):
    mask = std < thr
    ys, xs = np.where(mask)
    if len(xs) == 0:
        out.append(f"thr={thr}: (none)")
        continue
    xa, xb = int(xs.min()) + X0, int(xs.max()) + X0
    ya, yb = int(ys.min()) + Y0, int(ys.max()) + Y0
    out.append(f"thr={thr}: stroke_bbox=({xa},{ya},{xb},{yb}) w={xb - xa} h={yb - ya} px={len(xs)}")

# 用 thr=5 的 bbox 导出放大裁剪，供 Read 复核（含徽标底板）
mask = std < 5.0
ys, xs = np.where(mask)
if len(xs):
    xa, xb = int(xs.min()) + X0, int(xs.max()) + X0
    ya, yb = int(ys.min()) + Y0, int(ys.max()) + Y0
    m = 30
    cx0, cy0 = max(0, xa - m), max(0, ya - m)
    cx1, cy1 = min(2048, xb + m), min(2048, yb + m)
    img0 = np.asarray(Image.open(os.path.join(SRC, files[0])).convert("RGB"))
    crop = img0[cy0:cy1, cx0:cx1]
    Image.fromarray(crop).resize(((cx1 - cx0) * 3, (cy1 - cy0) * 3), Image.NEAREST).save(".measure172_crop.png")
    out.append(f"crop=({cx0},{cy0})-({cx1},{cy1}) saved to .measure172_crop.png")

with open(".measure172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("done")
