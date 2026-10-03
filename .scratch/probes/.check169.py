# -*- coding: utf-8 -*-
"""Stage 169：复刻 tests/test_i18n_coverage.py 的核心断言（无 pytest temp 依赖）。

覆盖：静态键均已定义 / zh-en 键集合一致 / 占位符一致 / 叶子非空 / 键名不含点。
"""
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from utils.i18n import TRANSLATIONS  # noqa: E402

_SRC_DIRS = ("core", "services", "utils", "ui")
_SRC_FILES = ("api.py", "server.py", "cli.py", "main.py")
_DYNAMIC_PREFIXES = ("detect.kind.", "detect.preset.", "detect.recommend.", "compare.viewer.", "media.")
_ALLOWED_UNKNOWN = {"not.exist.key", "x", "y"}


def flatten(node, prefix=""):
    out = {}
    for k, v in node.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


ZH = flatten(TRANSLATIONS["zh-CN"])
EN = flatten(TRANSLATIONS["en-US"])
ALL_KEYS = set(ZH) | set(EN)

fails = []

# 1. 静态键均已定义
files = []
for d in _SRC_DIRS:
    files += [p for p in (ROOT / d).rglob("*.py") if "__pycache__" not in str(p)]
files += [ROOT / f for f in _SRC_FILES]
referenced = {}
for p in files:
    src = p.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"""\bt\(\s*["']([^"']+)["']""", src):
        referenced.setdefault(m.group(1), str(p.relative_to(ROOT)))
missing = []
for key, src in sorted(referenced.items()):
    if key in ALL_KEYS or key in _ALLOWED_UNKNOWN:
        continue
    if any(key.startswith(p) for p in _DYNAMIC_PREFIXES):
        continue
    missing.append(f"{key} (from {src})")
if missing:
    fails.append("UNDEFINED KEYS: " + "; ".join(missing[:20]))

# 2. 键集合一致
only_zh = sorted(set(ZH) - set(EN))
only_en = sorted(set(EN) - set(ZH))
if only_zh:
    fails.append(f"EN missing: {only_zh[:20]}")
if only_en:
    fails.append(f"ZH missing: {only_en[:20]}")

# 3. 占位符一致
ph = re.compile(r"\{(\w+)\}")
bad_ph = []
for key in sorted(set(ZH) & set(EN)):
    a = set(ph.findall(str(ZH[key])))
    b = set(ph.findall(str(EN[key])))
    if a != b:
        bad_ph.append(f"{key}: zh={sorted(a)} en={sorted(b)}")
if bad_ph:
    fails.append("PLACEHOLDER MISMATCH: " + "; ".join(bad_ph[:20]))

# 4. 叶子非空字符串
bad_leaf = [k for k, v in {**ZH, **EN}.items() if not isinstance(v, str) or not v.strip()]
if bad_leaf:
    fails.append(f"EMPTY LEAVES: {bad_leaf[:20]}")

# 5. 键名不含点
dot_problems = []
for k in ALL_KEYS:
    pass  # ALL_KEYS 已按点分层，逐段检查在第 6 步用原始 dict

raw_dot = []


def walk(node, prefix=""):
    for k, v in node.items():
        if "." in k:
            raw_dot.append(f"{prefix}{k}")
        if isinstance(v, dict):
            walk(v, f"{prefix}{k}.")


for lang, table in TRANSLATIONS.items():
    walk(table, f"{lang}:")
if raw_dot:
    fails.append(f"KEY SEGMENT CONTAINS DOT: {raw_dot}")

# 6. report.* 键计数与样例
rep = sorted(k for k in ALL_KEYS if k.startswith("report."))
result_lines = [f"REFERENCED_STATIC_KEYS={len(referenced)}", f"REPORT_KEYS={len(rep)}",
                f"FAILS={len(fails)}"]
result_lines += ["report key: " + k for k in rep]
if fails:
    result_lines.append("FAILURES:")
    result_lines += ["  - " + f.encode("ascii", "replace").decode("ascii") for f in fails]
else:
    result_lines.append("ALL OK")

with open(".check169.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(result_lines) + "\n")
print("FAILS=" + str(len(fails)))
