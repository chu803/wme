# -*- coding: utf-8 -*-
"""跑 validate / preflight / real_gate，捕获退出码 + 输出尾部。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CMDS = [
    ("validate", [PY, "scripts/build_real_ground_truth.py", "validate"]),
    ("preflight", [PY, "scripts/real_asset_preflight.py"]),
    ("real_gate", [PY, "scripts/real_gate.py"]),
    ("real_gate_strict", [PY, "scripts/real_gate.py", "--strict-scope"]),
]
lines = []
for name, cmd in CMDS:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT, timeout=900)
    lines.append(f"===== {name} EXIT={p.returncode} =====")
    lines.append("\n".join((p.stdout or "").strip().splitlines()[-24:]))
    if (p.stderr or "").strip():
        lines.append("-- stderr --")
        lines.append("\n".join((p.stderr or "").strip().splitlines()[-6:]))
    lines.append("")
with open(".gate172.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii"))
print("done")
