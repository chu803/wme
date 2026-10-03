# -*- coding: utf-8 -*-
"""快检：validate + preflight（ASCII 落盘）。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CMDS = [
    ("validate", [PY, "scripts/build_real_ground_truth.py", "validate"]),
    ("preflight", [PY, "scripts/real_asset_preflight.py"]),
]
lines = []
for name, cmd in CMDS:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, timeout=300)
    lines.append(f"===== {name} EXIT={p.returncode} =====")
    lines.append((p.stdout or "").strip()[-2000:])
    if (p.stderr or "").strip():
        lines.append("-- stderr --")
        lines.append((p.stderr or "").strip()[-600:])
    lines.append("")
with open(".gate172a.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii"))
print("done")
