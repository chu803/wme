# -*- coding: utf-8 -*-
"""阶段 173 收尾：全 10 图掩码覆盖 + 合成门禁 + 真实门禁。"""
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
PY = sys.executable
BENCH = os.path.join(ROOT, "benchmark_real")

lines = []
# 1) 全 10 图 mask recall
from core.detector import detect_watermark_mask  # noqa: E402

man = json.load(open(os.path.join(BENCH, "manifest.json"), encoding="utf-8"))
truth = json.load(open(os.path.join(BENCH, "ground_truth.json"), encoding="utf-8"))
tby = {e["image_id"]: e for e in truth["entries"]}
ti = tt = 0
per = []
for im in man["images"]:
    if im["id"] not in tby:
        continue
    tx, ty, tw, th = tby[im["id"]]["watermarks"][0]["bbox"]
    rgb = np.asarray(Image.open(os.path.join(BENCH, im["path"])).convert("RGB"))
    m = detect_watermark_mask(rgb, preset="general", use_ocr=False).mask
    if m.ndim == 3:
        m = m[:, :, 0]
    inter = int((m[ty:ty + th, tx:tx + tw] > 0).sum())
    ti += inter
    tt += tw * th
    per.append(f"{im['id'][8:20]}={inter / (tw * th):.2f}")
lines.append(f"[mask_recall all-10] MICRO={ti / tt:.4f} | " + " ".join(per))

# 2) 合成门禁
for name, cmd in (("quality_gate", [PY, "scripts/quality_gate.py"]),
                  ("real_gate", [PY, "scripts/real_gate.py"])):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, timeout=1800)
    lines.append(f"\n===== {name} EXIT={p.returncode} =====")
    lines.append("\n".join((p.stdout or "").strip().splitlines()[-16:]))

with open(".final173.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("done")
