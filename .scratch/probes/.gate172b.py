# -*- coding: utf-8 -*-
"""real_gate 判定（ASCII 落盘）。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
CMDS = [
    ("real_gate", [PY, "scripts/real_gate.py"]),
    ("real_gate_strict", [PY, "scripts/real_gate.py", "--strict-scope"]),
    ("e2e_gate", [PY, "scripts/e2e_real_probe.py", "--gate"]),
]
lines = []
for name, cmd in CMDS:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT, timeout=1500)
        lines.append(f"===== {name} EXIT={p.returncode} =====")
        lines.append("\n".join((p.stdout or "").strip().splitlines()[-30:]))
        if (p.stderr or "").strip():
            lines.append("-- stderr --")
            lines.append("\n".join((p.stderr or "").strip().splitlines()[-6:]))
    except subprocess.TimeoutExpired:
        lines.append(f"===== {name} TIMEOUT =====")
    lines.append("")
with open(".gate172b.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii"))
print("done")
