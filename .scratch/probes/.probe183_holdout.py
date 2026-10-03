# -*- coding: utf-8 -*-
"""阶段 183 留出探针：在 30 张**未标注**真实 AI 图上量测守卫先验的触发率。

⚠ 诚实边界：这 30 张真值为空（149 清除伪造标注后为未标注态），**不是**干净图真值，
   也**不是**水印真值——本探针只回答「守卫先验在真实图上触发的频率」，
   **不能**当作 FP 率（触发可能是真水印命中，也可能是误报）。仅作支撑性证据。
"""
import io
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, r"D:/work 111/2026-08-03-17-38-51")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config  # noqa: E402
from core.detector import (  # noqa: E402
    KIND_CORNER_LOGO,
    KIND_EDGE_TEXT,
    KIND_TRANSLUCENT,
    detect_watermark_mask,
)

ROOT = r"D:/work 111/2026-08-03-17-38-51"
bench = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(bench, "manifest.json"), encoding="utf-8"))


def run(arr, lever, anchor):
    config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = lever
    config.DETECT_PLATE_PRIOR_ANCHOR_OVERLAP = anchor
    try:
        return detect_watermark_mask(arr, preset="general", use_ocr=False)
    finally:
        config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = False
        config.DETECT_PLATE_PRIOR_ANCHOR_OVERLAP = 0.50


def main():
    rows = []
    n_on_extra = 0
    for im in man["images"]:
        if str(im["id"]).startswith("ai_logo"):
            continue
        arr = np.asarray(Image.open(os.path.join(bench, im["path"])).convert("RGB"))
        h, w = arr.shape[:2]
        fw = max(8, int(round(340 * min(h, w) / 2048.0)))
        fh = max(8, int(round(128 * min(h, w) / 2048.0)))
        bx, by = w - fw, h - fh
        r_off = run(arr, False, 0.50)
        r_on = run(arr, True, 0.50)
        n_off = sum(1 for r in r_off.regions if r.kind == KIND_CORNER_LOGO)
        n_on = sum(1 for r in r_on.regions if r.kind == KIND_CORNER_LOGO)
        extra = max(0, n_on - n_off)
        if extra:
            n_on_extra += 1
        # 最大锚定重叠（合并前不可得，此处用合并后信号近似，仅供参考）
        mx = 0.0
        for r in r_on.regions:
            if r.kind not in (KIND_TRANSLUCENT, KIND_EDGE_TEXT):
                continue
            ox = max(0, min(int(r.x) + int(r.w), bx + fw) - max(int(r.x), bx))
            oy = max(0, min(int(r.y) + int(r.h), by + fh) - max(int(r.y), by))
            mx = max(mx, (ox * oy) / max(1, int(r.w) * int(r.h)))
        rows.append((im["id"], n_off, n_on, extra, round(mx, 2)))

    print("== 30 张未标注真实 AI 图 · 守卫先验触发率（ON−OFF）==")
    print(f"{'id':6s} {'OFF_cl':>6s} {'ON_cl':>6s} {'extra':>6s} {'max_fp_ov':>10s}")
    for t in rows:
        print(f"{t[0]:6s} {t[1]:>6d} {t[2]:>6d} {t[3]:>6d} {t[4]:>10.2f}")
    print(f"\n守卫先验新增触发图数 = {n_on_extra}/{len(rows)}")
    print("⚠ 未标注语料，触发≠误报；仅作支撑性证据（不能当 FP 率）。")


if __name__ == "__main__":
    main()
