#!/usr/bin/env python3
"""
源码静态断言的共享工具：从函数源码中**剥离文档字符串**，只留可执行代码。

为什么需要它（LESSONS 235 / 214 的重复踩坑）
--------------------------------------------
`inspect.getsource(fn)` 返回的文本**包含 docstring**。而docstring 里常会**引用
被禁的写法来说明为什么不用它**，例如：

    为什么不用 `select_device("cuda")`：那是「强制 CUDA」语义，无卡时会抛……

于是朴素的 `assert 'select_device("cuda")' not in src` 会**命中这段说明并假失败**
（214 踩过一次、220 又踩一次——同一个坑踩两遍说明该工具化）。

本模块提供 `code_without_docstrings(fn_or_src)`，用于把断言的搜索空间
从「文本」收窄到「代码」，消除这类假失败与漏判。
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Union


def code_without_docstrings(src_obj: Union[str, object]) -> str:
    """返回去除**全部字符串常量与注释**后的源码文本（仅保留可执行结构）。

    实现说明：用 `ast.parse` + `ast.unparse`（3.9+）重建——所有 docstring
    都是 `Expr(Constant(str))` 节点，重建后自然消失。无法解析时**回退**为
    「逐行剥注释」的保守实现（宁可少剥离，也不整体失效）。
    """
    if not isinstance(src_obj, str):
        src_obj = inspect.getsource(src_obj)
    text = textwrap.dedent(src_obj)
    try:
        tree = ast.parse(text)
        # 去掉模块/函数/类的 docstring（ast.unparse 会保留它们，故显式摘除）
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:] or [ast.Pass()]
        return ast.unparse(ast.fix_missing_locations(tree))
    except (SyntaxError, ValueError):
        # 回退：删掉三引号块与整行注释
        out, in_doc, delim = [], False, ""
        for line in text.splitlines():
            s = line.strip()
            if not in_doc and (s.startswith('"""') or s.startswith("'''")):
                delim = s[:3]
                in_doc = not s.endswith(delim) or len(s) <= 3
                if s.endswith(delim) and len(s) > 3:
                    in_doc = False
                continue
            if in_doc:
                if delim in s:
                    in_doc = False
                continue
            out.append(line.split("#", 1)[0])
        return "\n".join(out)


def function_body_code(fn) -> str:
    """取函数体的**可执行代码**（去 docstring、去注释），不含 `def` 行。"""
    src = inspect.getsource(fn)
    text = textwrap.dedent(src)
    lines = text.splitlines()
    # 去掉 def 行与其后可能的 docstring
    try:
        tree = ast.parse(text)
        node = tree.body[0]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
            node.decorator_list = []
            return ast.unparse(node)
    except (SyntaxError, ValueError):
        pass
    # 回退：按缩进切函数体
    body = lines[1:]
    return "\n".join(ln for ln in body if not ln.strip().startswith("#"))
