# -*- coding: utf-8 -*-
"""客观量取：在高对比图（白雪）上扫描穿过徽标板的横/竖亮度剖面，找板的边界。"""
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
f = "ai_logo_02_snow_mountain_lake.jpg"
a = np.asarray(Image.open(f"{SRC}\\{f}").convert("RGB")).astype(np.float32).mean(axis=2)


def smooth(v, k=5):
    ker = np.ones(k) / k
    return np.convolve(v, ker, mode="same")


out = [f"image={f}  shape={a.shape}"]

# 横剖面：取几行，看板左右边界
for y in (1900, 1925, 1950):
    prof = smooth(a[y, 1560:2048], 7)
    bg = float(np.median(prof[:80]))
    plate = float(np.percentile(prof, 90))
    thr = (bg + plate) / 2
    idx = np.where(prof > thr)[0]
    if len(idx):
        out.append(f"row y={y}: bg={bg:.0f} plate_p90={plate:.0f} thr={thr:.0f} "
                   f"x_left={1560 + idx.min()} x_right={1560 + idx.max()}")

# 竖剖面：取几列，看板上下边界
for x in (1690, 1720):
    prof = smooth(a[1840:2048, x], 7)
    bg = float(np.median(prof[-80:]))
    plate = float(np.percentile(prof, 90))
    thr = (bg + plate) / 2
    idx = np.where(prof > thr)[0]
    if len(idx):
        out.append(f"col x={x}: bg={bg:.0f} plate_p90={plate:.0f} thr={thr:.0f} "
                   f"y_top={1840 + idx.min()} y_bottom={1840 + idx.max()}")

with open(".scan172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("done")
