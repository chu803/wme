# -*- coding: utf-8 -*-
"""阶段 384：传给 Gradio 的参数必须**真实存在**——1.0.8 起不来的根因守卫。

1.0.8 把 `analytics_enabled` 传给了 `Blocks.launch()`，而该参数只存在于
**构造器** `Blocks.__init__` ⇒ 发布物启动即 `TypeError`，界面根本起不来。
之所以没被本地单测发现：测试用的是 `launch(**kwargs)` 的**鸭子替身**，任何关键字都收。

本守卫改成拿**真实签名**对账：
  ① `app.launch(...)` 的每个关键字都必须出现在 `gradio.Blocks.launch` 的签名里；
  ② `Blocks(...)` 构造的关键字同理；
  ③ 替身不再能蒙混：守卫直接读真实签名，替身宽松与否不影响结论。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import gradio as gr

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "ui" / "app.py"


def _call_keywords(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg}


def _calls_to(attr: str) -> list[ast.Call]:
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == attr:
            found.append(node)
        elif isinstance(func, ast.Name) and func.id == attr:
            found.append(node)
    return found


def test_launch_keywords_exist_in_real_signature():
    """app.launch(...) 的每个关键字都必须被 gradio.Blocks.launch 接受。"""
    calls = _calls_to("launch")
    assert calls, "没找到 app.launch(...) 调用（源码结构变了？）"
    allowed = set(inspect.signature(gr.Blocks.launch).parameters)
    for call in calls:
        for kw in _call_keywords(call):
            assert kw in allowed, (
                f"app.launch({kw}=...) 不是 gradio.Blocks.launch 的参数 ⇒ 启动会抛 TypeError"
            )


def test_blocks_constructor_keywords_exist_in_real_signature():
    """gr.Blocks(...) 的每个关键字都必须被 gradio.Blocks.__init__ 接受。"""
    calls = _calls_to("Blocks")
    assert calls, "没找到 gr.Blocks(...) 构造调用"
    allowed = set(inspect.signature(gr.Blocks.__init__).parameters)
    for call in calls:
        for kw in _call_keywords(call):
            assert kw in allowed, (
                f"gr.Blocks({kw}=...) 不是 gradio.Blocks.__init__ 的参数 ⇒ 启动会抛 TypeError"
            )
