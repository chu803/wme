# -*- coding: utf-8 -*-
"""Stage 170 独立验证：S1(real_gate 空真值) + S2(编码加固全覆盖)。"""
import ast
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
PY = sys.executable

fails = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)


# ---------------- S1：real_gate 空真值不再崩溃 ----------------
import scripts.real_gate as rg  # noqa: E402

try:
    summary = rg.run_real_gate()
    ok, problems = rg.evaluate_real(summary, None)
    check(ok is False, "empty-truth: 门禁不应判通过")
    check(any("承诺类型缺少真值证据" in p for p in problems),
          "empty-truth: 缺少 committed_missing 判定")
    check(any("没有任何预设结果" in p for p in problems),
          "empty-truth: 缺少「无预设结果」判定")
    md = rg.to_markdown(summary)
    check("承诺" in md, "empty-truth: markdown 未渲染承诺面横幅")
except Exception as exc:  # noqa: BLE001
    check(False, f"empty-truth 抛异常（回归）：{type(exc).__name__}: {exc}")

# ---------------- S2a：元扫描 —— 打印非 GBK 字符的脚本必须已加固 ----------------
SD = os.path.join(ROOT, "scripts")
HARDEN = ("force_utf8_stdio", "reconfigure")


def _cname(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


missing = []
scanned = 0
for fn in sorted(os.listdir(SD)):
    if not fn.endswith(".py"):
        continue
    src = io.open(os.path.join(SD, fn), encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    chars = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _cname(n) in ("print", "write"):
            for m in ast.walk(n):
                if isinstance(m, ast.Constant) and isinstance(m.value, str):
                    for c in m.value:
                        if ord(c) > 127:
                            try:
                                c.encode("gbk")
                            except UnicodeEncodeError:
                                chars.add(c)
    if not chars:
        continue
    scanned += 1
    if not any(h in src for h in HARDEN):
        missing.append(fn)

check(not missing, f"未加固脚本（打印非 GBK 字符）：{missing}")
check(scanned >= 10, f"元扫描命中脚本数异常（{scanned}），探针可能失效")

# ---------------- S2b：子进程 —— emoji 打印在 GBK 管道下不再崩 ----------------
p = subprocess.run([PY, ".emoji_check.py"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", cwd=ROOT, timeout=120)
check(p.returncode == 0, f"emoji_check 退出码 {p.returncode}（应 0）")

# ---------------- S2c：端到端 —— quality_gate --quick 不再「通过却 EXIT=1」 ----------------
p = subprocess.run([PY, "scripts/quality_gate.py", "--quick"], capture_output=True,
                   text=True, encoding="utf-8", errors="replace", cwd=ROOT, timeout=600)
check(p.returncode == 0, f"quality_gate --quick 退出码 {p.returncode}（应 0；此前因编码=1）")

result = [f"CHECKS={checks} FAILS={len(fails)}"]
if fails:
    result.append("FAILURES:")
    result += ["  - " + f.encode("ascii", "replace").decode("ascii") for f in fails]
else:
    result.append("ALL OK")
with open(".verify170.result.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(result) + "\n")
print("FAILS=" + str(len(fails)))
