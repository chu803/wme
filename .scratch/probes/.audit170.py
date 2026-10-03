# -*- coding: utf-8 -*-
"""Stage 170 侦察：跑门禁套件，捕获退出码 + 输出尾部（ASCII 落盘）。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

CMDS = [
    ("real_gate", [PY, "scripts/real_gate.py"]),
    ("real_gate_strict", [PY, "scripts/real_gate.py", "--strict-scope"]),
    ("real_gate_noarea", [PY, "scripts/real_gate.py", "--no-area"]),
    ("mask_param_audit", [PY, "scripts/mask_param_audit.py"]),
    ("audit_selector_unaudited", [PY, "scripts/audit_selector.py", "--unaudited"]),
    ("prompted_area_gate", [PY, "scripts/prompted_area_gate.py"]),
    ("quality_gate_quick", [PY, "scripts/quality_gate.py", "--quick"]),
]

lines = []
for name, cmd in CMDS:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900, cwd=ROOT)
        out = (p.stdout or "")
        err = (p.stderr or "")
        tail = "\n".join(out.strip().splitlines()[-18:])
        lines.append(f"===== {name} EXIT={p.returncode} =====")
        lines.append(tail)
        if err.strip():
            lines.append("-- stderr tail --")
            lines.append("\n".join(err.strip().splitlines()[-6:]))
    except subprocess.TimeoutExpired:
        lines.append(f"===== {name} TIMEOUT =====")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"===== {name} EXC={exc!r} =====")
    lines.append("")

ascii_out = "\n".join(lines).encode("ascii", "replace").decode("ascii")
with open(".audit170.result.txt", "w", encoding="utf-8") as f:
    f.write(ascii_out)
print("done")
