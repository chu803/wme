# -*- coding: utf-8 -*-
"""列出 scripts/*.py 中**会被 print / write 输出**的 GBK 不可编码字符，及是否已加 UTF-8 加固。

只统计 print(...) 与 *.write(...) 调用实参里的字符串常量（含 f-string 各段），
避免把 docstring 里的字符误判为运行期崩溃点。
"""
import ast
import io
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
SD = os.path.join(ROOT, "scripts")
HARDEN = ("force_utf8_stdio", "reconfigure")


def _call_name(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _strings_in(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value


rows = []
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
        if isinstance(n, ast.Call) and _call_name(n) in ("print", "write"):
            for s in _strings_in(n):
                for c in s:
                    if ord(c) > 127:
                        try:
                            c.encode("gbk")
                        except UnicodeEncodeError:
                            chars.add(c)
    if not chars:
        continue
    hardened = any(h in src for h in HARDEN)
    rows.append((fn, " ".join("U+%04X" % ord(c) for c in sorted(chars)), hardened))

lines = [f"scripts PRINTING non-GBK chars: {len(rows)}",
         f"  missing hardening: {sum(1 for r in rows if not r[2])}", ""]
for fn, cps, h in rows:
    lines.append(f"{'OK ' if h else 'NO '} {fn:34s} {cps}")
with open(".probe170.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("done")
