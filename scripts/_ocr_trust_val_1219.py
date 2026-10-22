# -*- coding: utf-8 -*-
"""P0-4（OCR 文字框自播种掩码）验证探针（一次性）。
三组：10 对成对图（pair_04 是否被救回 + removal）、15 张干净图（误报是否不变）、
12 张真实角标（右下区面积是否上升）。用完即删。
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402
from core.detector import detect_watermark_mask  # noqa: E402
from core.inpainter import inpaint  # noqa: E402

INC = os.path.join(_ROOT, "benchmark_real", "incoming")
BLUR, THR, DIL, PAD = 25, 8.0, 8, 6


def _mae(a, b, sel):
    if not np.any(sel):
        return 0.0
    d = np.abs(a.astype(np.float64) - b.astype(np.float64)).mean(axis=2)
    return float(d[sel].mean())


def truth_mask(wm, clean):
    d = np.abs(wm.astype(np.float32) - clean.astype(np.float32)).mean(axis=2)
    m = (cv2.blur(d, (BLUR, BLUR)) > THR).astype(np.uint8)
    n, lab, st, cent = cv2.connectedComponentsWithStats(m, 8)
    h, w = d.shape
    best, ba = None, 0
    for j in range(1, n):
        x, y, bw, bh, ar = st[j]
        if ar < 0.001 * w * h:
            continue
        cx, cy = cent[j]
        if cx > 0.60 * w and cy > 0.70 * h and ar > ba:
            best, ba = j, ar
    if best is None:
        return None
    sel = cv2.dilate((lab == best).astype(np.uint8),
                     np.ones((2 * DIL + 1,) * 2, np.uint8), 1) > 0
    return sel


def load(p):
    return np.array(Image.open(p).convert("RGB"))


print("=== ① 10 对成对图（general + LaMa）===")
rem = []
for i in range(1, 11):
    k = f"pair_{i:02d}"
    wm = load(f"{INC}/pairs/{k}_wm.jpg")
    clean = load(f"{INC}/pairs/{k}_clean.jpg")
    t = truth_mask(wm, clean)
    res = detect_watermark_mask(wm, preset="general")
    pred = np.asarray(res.mask) > 0
    if t is None or not np.any(pred):
        print(f"  {k}: truth={'None' if t is None else 'ok'} mask_empty={not np.any(pred)}"
              f" regions={len(res.regions)}")
        rem.append(0.0)
        continue
    rep = inpaint(wm, res.mask, engine="lama")
    b, a = _mae(wm, clean, t), _mae(rep, clean, t)
    r = 1 - a / b if b else 0.0
    rem.append(r)
    print(f"  {k}: regions={len(res.regions):3d} overdraw={pred.sum()/max(1,int(t.sum())):.2f} "
          f"removal={r:.3f}")
print(f"  MEAN removal={sum(rem)/len(rem):.4f}  (P0-4 前 0.1649)")

print("\n=== ② 15 张干净图（general）误报 ===")
rows = json.load(open(f"{INC}/manifest_db.json", encoding="utf-8"))
tot_r = tot_c = 0
for r in [x for x in rows if x["group"] == "plain-photos"]:
    arr = load(f"{INC}/{r['id']}.jpg")
    res = detect_watermark_mask(arr, preset="general")
    cov = float((np.asarray(res.mask) > 0).sum()) / (arr.shape[0] * arr.shape[1])
    tot_r += len(res.regions)
    tot_c += cov
    print(f"  {r['id']}: regions={len(res.regions):3d} cov={cov*100:.2f}%")
print(f"  MEAN regions={tot_r/15:.1f} cov={tot_c/15*100:.2f}%  (P0-4 前 73 区域 / 0.73%)")

print("\n=== ③ 12 张真实角标（general）右下区 ===")
cz, area, ocr = 0, [], 0
for r in [x for x in rows if x["group"] == "corner-watermark"]:
    arr = load(f"{INC}/{r['id']}.jpg")
    H, W = arr.shape[:2]
    res = detect_watermark_mask(arr, preset="general")
    a = sum(x.w * x.h for x in res.regions
            if 0.70 * W < x.x + x.w / 2 and 0.82 * H < x.y + x.h / 2) / float(W * H)
    area.append(a)
    cz += int(any(0.70 * W < x.x + x.w / 2 and 0.82 * H < x.y + x.h / 2 for x in res.regions))
    ocr += int(any(x.kind == "ocr_text" and 0.70 * W < x.x + x.w / 2
                   and 0.82 * H < x.y + x.h / 2 for x in res.regions))
print(f"  右下区有区域 {cz}/12；ocr_text 命中 {ocr}/12；右下区面积 mean={np.mean(area)*100:.3f}%"
      f"（P0-4 前 2.771%）")
