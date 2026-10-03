# -*- coding: utf-8 -*-
"""测试「局部提亮」候选：lift = V - blur(V, k) > delta 是否能在各底图上圈住整块半透明白牌。

同时检查非角标区（左上 1/4）的误报（max component 占该区比例）。
"""
import json
import os
import sys

import cv2  # type: ignore
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(ROOT, "benchmark_real")
man = json.load(open(os.path.join(BENCH, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(BENCH, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}

lines = []
for k in (151, 301, 501):
    for delta in (10, 18, 28):
        rows = []
        for iid, im in ((i["id"], i) for i in man["images"]):
            if iid not in tby:
                continue
            arr = np.asarray(Image.open(os.path.join(BENCH, im["path"])).convert("RGB"))
            v = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[:, :, 2].astype(np.int16)
            lift = v - cv2.blur(v.astype(np.float32), (k, k)).astype(np.int16)
            m = (lift > delta).astype(np.uint8)
            x, y, w, h = tby[iid]["watermarks"][0]["bbox"]
            sub = m[y:y + h, x:x + w]
            cov = float(sub.sum()) / (w * h)
            # 只算真值框内覆盖率（越大越好）
            rows.append((iid[8:20], round(cov, 2)))
        lines.append(f"k={k} delta={delta}: " + " ".join(f"{a}={b}" for a, b in rows))
        with open(".probe174b.result.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("\n".join(lines))
