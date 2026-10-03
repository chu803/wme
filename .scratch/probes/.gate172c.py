# -*- coding: utf-8 -*-
"""精修后复跑：validate + real_gate（ASCII 落盘）。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CMDS = [
    ("validate", [PY, "scripts/build_real_ground_truth.py", "validate"]),
    ("real_gate", [PY, "scripts/real_gate.py"]),
]
lines = []
for name, cmd in CMDS:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, timeout=1800)
    lines.append(f"===== {name} EXIT={p.returncode} =====")
    lines.append("\n".join((p.stdout or "").strip().splitlines()[-26:]))
    lines.append("")
with open(".gate172c.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii"))
print("done")
