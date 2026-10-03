# -*- coding: utf-8 -*-
"""诊断：在 3 张图上叠加「检测器预测区域」与「我的真值框」，判断低 recall 是漏检还是真值错位。"""
import json
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, ROOT)
from core.detector import detect_watermark_mask  # noqa: E402

BENCH = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(BENCH, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(BENCH, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}

pick = ["ai_logo_02_snow_mountain", "ai_logo_test_01_orange_c", "ai_logo_04_neon_street_n"]
out = []
tiles = []
for iid in pick:
    im = next(i for i in man["images"] if i["id"] == iid)
    path = os.path.join(BENCH, im["path"])
    rgb = np.asarray(Image.open(path).convert("RGB"))
    res = detect_watermark_mask(rgb, preset="general", use_ocr=False)
    mask = res.mask
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    tb = tby[iid]["watermarks"][0]["bbox"]
    tx, ty, tw, th = tb
    vis = rgb.copy()
    # 预测掩码（红）
    vis[mask > 0] = (0.4 * vis[mask > 0] + 0.6 * np.array([255, 0, 0])).astype(np.uint8)
    # 我的真值框（绿）
    cv2.rectangle(vis, (tx, ty), (tx + tw, ty + th), (0, 255, 0), 4)
    # 预测区域框（品红）
    for r in res.regions:
        cv2.rectangle(vis, (int(r.x), int(r.y)),
                      (int(r.x + r.w), int(r.y + r.h)), (255, 0, 255), 2)
    out.append(f"{iid}: truth={tb} pred_regions={len(res.regions)} "
               f"mask_px={int((mask > 0).sum())} mask_cov={(mask > 0).mean():.4f}")
    tiles.append(vis[1780:2048, 1480:2048])

th2 = tiles[0].shape[0]
tw2 = tiles[0].shape[1]
grid = np.full((th2 * 3 + 20, tw2, 3), 255, np.uint8)
for i, t in enumerate(tiles):
    grid[i * (th2 + 10):i * (th2 + 10) + th2] = t
Image.fromarray(grid).save(".diag172_view.png")
with open(".diag172.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out).encode("ascii", "replace").decode("ascii") + "\n")
print("\n".join(out))
