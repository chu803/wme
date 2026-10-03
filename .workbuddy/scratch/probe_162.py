# -*- coding: utf-8 -*-
"""Stage 162 探针：ui/tab_models.py 的 AST 级契约检查（无需导入 gradio）。

① 接线契约（skill 5h）：fn.click(inputs=[...], outputs=[...]) 的 输入数 / 输出数
   必须 == 目标函数 (形参数 − 框架注入项) / (return 元组长度)。
② i18n 泄漏：string 常量里若含 CJK 汉字 **或** 全角/中日韩标点（U+3000-303F、
   U+FF00-FFEF、U+2018-201D、U+2014、U+2026），且不在 docstring、不在 t(...) 实参，
   即为硬编码文案候选。
"""
from __future__ import annotations

import ast

PATH = "D:/work 111/2026-08-03-17-38-51/ui/tab_models.py"
SRC = open(PATH, encoding="utf-8").read()
TREE = ast.parse(SRC)

INJECTED = {"progress", "evt"}   # click/change 末位 progress；select 首位 evt


def _func_params(name):
    for n in ast.walk(TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            args = n.args
            names = [a.arg for a in list(args.posonlyargs) + list(args.args)]
            return names
    return None


def _return_tuple_len(name):
    """返回该函数所有 return 的元组长度集合（非元组→1）。"""
    lens = set()
    for n in ast.walk(TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            for r in ast.walk(n):
                if isinstance(r, ast.Return) and r.value is not None:
                    if isinstance(r.value, ast.Tuple):
                        lens.add(len(r.value.elts))
                    else:
                        lens.add(1)
    return lens


print("== ① 接线契约（inputs == 形参数−注入；outputs == return 元组长度）==")
viol = 0
for n in ast.walk(TREE):
    if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
        continue
    if n.func.attr not in ("click", "change", "select"):
        continue
    kw = {k.arg: k.value for k in n.keywords}
    fn = kw.get("fn")
    fn_name = fn.id if isinstance(fn, ast.Name) else "?"
    inputs = kw.get("inputs")
    outputs = kw.get("outputs")
    n_in = len(inputs.elts) if isinstance(inputs, ast.List) else "?"
    n_out = len(outputs.elts) if isinstance(outputs, ast.List) else "?"
    params = _func_params(fn_name) or []
    injected = sum(1 for p in params if p in INJECTED)
    expect_in = len(params) - injected
    rets = _return_tuple_len(fn_name)
    ok = (n_in == expect_in) and (n_out in rets if rets else n_out == 1)
    if not ok:
        viol += 1
    print(f"  {fn_name:16} inputs={n_in} params={len(params)}(-{injected}) outputs={n_out} "
          f"return_tuple_len={sorted(rets)}  -> {'OK' if ok else 'MISMATCH'}")
print(f"  接线违例数={viol}")
print()

print("== ② 硬编码文案扫描（CJK 汉字 + 全角/中日韩标点；剔 docstring 与 t(...) 实参）==")


def _is_suspect(s: str) -> bool:
    for c in s:
        o = ord(c)
        if 0x4E00 <= o <= 0x9FFF:          # CJK 汉字
            return True
        if 0x3000 <= o <= 0x303F:          # CJK 标点（、。〈〉等）
            return True
        if 0xFF00 <= o <= 0xFFEF:          # 半角/全角形式（｜／：…）
            return True
        if o in (0x2014, 0x2026, 0x2018, 0x2019, 0x201C, 0x201D):
            return True
    return False


doc = set()
for n in ast.walk(TREE):
    if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        b = getattr(n, "body", [])
        if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
            doc.add(id(b[0].value))

t_arg = set()   # t(...) 的实参字面量（它们是要翻译的键，不算泄漏）
for n in ast.walk(TREE):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "t":
        for a in n.args:
            for sub in ast.walk(a):
                if isinstance(sub, ast.Constant):
                    t_arg.add(id(sub))

leaks = []
for n in ast.walk(TREE):
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        if id(n) in doc or id(n) in t_arg:
            continue
        if _is_suspect(n.value):
            leaks.append((n.lineno, n.value))

for ln, v in leaks:
    print(f"  L{ln}: {v!r}")
print(f"  候选泄漏数={len(leaks)}")
print()
print("PROBE DONE")
