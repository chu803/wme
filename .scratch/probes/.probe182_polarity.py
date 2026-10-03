# -*- coding: utf-8 -*-
"""阶段 182 探针：量测 179 先验足迹区的「牌极性」统计，验证可分离性。

对每组图（10 张 ai_logo 真实豆包牌 + 5 张 syn_* 合成干净图）计算：
  足迹区 = 右下角 340x128 @2048² 随 min(h,w) 缩放（与 _apply_plate_completion_prior 一致）
  · bright_frac  = (V>=140 & S<=80) 占比   —— 白牌体极性（与白牌通路同阈值）
  · ink_frac     = (V<=100) 占比           —— 暗墨迹占比
  · mean_v       — 足迹区平均亮度
预期：真实豆包牌 bright_frac 高（白板）；syn_02/syn_05（暗块+白字）bright_frac≈0 → 可分离。
"""
import io
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, r"D:/work 111/2026-08-03-17-38-51")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import cv2  # noqa: E402

ROOT = r"D:/work 111/2026-08-03-17-38-51"


def footprint_stats(arr):
    h, w = arr.shape[:2]
    fw = max(8, int(round(340 * min(h, w) / 2048.0)))
    fh = max(8, int(round(128 * min(h, w) / 2048.0)))
    bx, by = w - fw, h - fh
    patch = arr[by:by + fh, bx:bx + fw]
    hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
    Hc, Sc, Vc = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    bright = ((Vc >= 140) & (Sc <= 80)).mean()
    ink = (Vc <= 100).mean()
    return bright, ink, float(Vc.mean()), (bx, by, fw, fh)


def show(tag, path):
    arr = np.asarray(Image.open(path).convert("RGB"))
    bright, ink, mv, box = footprint_stats(arr)
    print(f"{tag:34s} bright={bright:.3f} ink={ink:.3f} meanV={mv:6.1f} box={box}")


print("== 真实豆包牌（ai_logo_*，期望 bright 高）==")
root = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
for im in man["images"]:
    if not str(im["id"]).startswith("ai_logo"):
        continue
    show(im["id"], os.path.join(root, im["path"]))

print("== 合成干净图（syn_*，syn_02/syn_05 期望 bright≈0 → 可拒）==")
croot = os.path.join(ROOT, "benchmark_real", "clean")
cman = json.load(open(os.path.join(croot, "manifest.json"), encoding="utf-8"))
for im in cman["images"]:
    show(im["id"], os.path.join(croot, im["path"]))
