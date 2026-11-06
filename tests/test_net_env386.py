# -*- coding: utf-8 -*-
"""阶段 386：代理环境变量清洗的守卫。

问题实例：`NO_PROXY=localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1`
里���方括号 IPv6 写法会让 httpx 在**导入时**抛 `InvalidURL: Invalid port: ':1]'`
⇒ 轻则大批用例在收集阶段报错，重则 Gradio 起不来（界面直接打不开）。

守卫保证：
  · 清洗函数能把方括号形式改成 httpx 认的 `::1`，且不动其它条目；
  · `ui/app.py` 与 `conftest.py` 都在 **import gradio 之前**导入清洗模块（AST 顺序核对）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from utils.net_env import sanitize_proxy_env

ROOT = Path(__file__).resolve().parent.parent


def test_sanitize_strips_bracketed_ipv6(monkeypatch):
    # ⚠ Windows 上 os.environ 键名大小写不敏感 ⇒ 只能动一个拼写，
    # 否则 delenv("no_proxy") 会把 NO_PROXY 一起删掉。
    monkeypatch.setenv("NO_PROXY",
                        "localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1")
    assert sanitize_proxy_env() is True
    import os

    assert os.environ["NO_PROXY"] == "localhost,.localhost,127.0.0.1,127.0.0.0/8,::1,::1"
    assert "[" not in os.environ["NO_PROXY"]


def test_sanitize_is_noop_for_normal_values(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    assert sanitize_proxy_env() is False


@pytest.mark.parametrize("rel", ["ui/app.py", "conftest.py"])
def test_sanitizer_is_imported_before_gradio(rel):
    """清洗必须在 gradio 之前导入（httpx 在 gradio 导入链里被解析 NO_PROXY）。"""
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    net_idx = gradio_idx = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "gradio":
                    gradio_idx = node.lineno
                if alias.name in ("utils.net_env", "utils"):
                    net_idx = node.lineno if net_idx is None else min(net_idx, node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module in ("utils.net_env", "utils"):
            net_idx = node.lineno if net_idx is None else min(net_idx, node.lineno)
    assert net_idx is not None, f"{rel} 没有导入代理清洗模块"
    if gradio_idx is None:
        pytest.skip(f"{rel} 不直接导入 gradio（只需确认已导入清洗模块）")
    assert net_idx < gradio_idx, f"{rel}: 清洗模块必须在 import gradio 之前"
