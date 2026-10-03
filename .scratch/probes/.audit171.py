# -*- coding: utf-8 -*-
"""Stage 171 侦察：跑全部门禁，捕获退出码 + 输出尾部（ASCII 落盘）。"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

CMDS = [
    ("real_gate", [PY, "scripts/real_gate.py"]),
    ("quality_gate", [PY, "scripts/quality_gate.py"]),
    ("mask_param_audit", [PY, "scripts/mask_param_audit.py"]),
    ("prompted_area_gate", [PY, "scripts/prompted_area_gate.py"]),
    ("e2e_real_probe_gate", [PY, "scripts/e2e_real_probe.py", "--gate"]),
    ("audit_selector_unaudited", [PY, "scripts/audit_selector.py", "--unaudited"]),
]

lines = []
for name, cmd in CMDS:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=1800, cwd=ROOT)
        out = (p.stdout or "")
        err = (p.stderr or "")
        lines.append(f"===== {name} EXIT={p.returncode} =====")
        lines.append("\n".join(out.strip().splitlines()[-20:]))
        if err.strip():
            lines.append("-- stderr tail --")
            lines.append("\n".join(err.strip().splitlines()[-6:]))
    except subprocess.TimeoutExpired:
        lines.append(f"===== {name} TIMEOUT =====")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"===== {name} EXC={exc!r} =====")
    lines.append("")

with open(".audit171.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii"))
print("done")
