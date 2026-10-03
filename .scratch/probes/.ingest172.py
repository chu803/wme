# -*- coding: utf-8 -*-
"""收编 10 张 ai_logo 真实图到 benchmark_real（add --copy，--style corner_logo）。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
SRC = r"D:\Desktop\照片"

files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
out = []
for f in files:
    p = subprocess.run(
        [PY, "scripts/collect_real_assets.py", "add", os.path.join(SRC, f),
         "--ai-source", "other", "--style", "corner_logo", "--copy"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    out.append(f"[{p.returncode}] {f} :: {(p.stdout or '').strip()} {(p.stderr or '').strip()[:100]}")

with open(".ingest172.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(out).encode("ascii", "replace").decode("ascii") + "\n")
print("\n".join(o.encode("ascii", "replace").decode("ascii") for o in out))
