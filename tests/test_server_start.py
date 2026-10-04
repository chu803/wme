# -*- coding: utf-8 -*-
"""
Stage 208 回归：server.start_server 的 max_concurrency 接线 + API 禁用守卫。

此前 start_server 在签名里漏掉了 max_concurrency 参数，却在函数体内引用它，
导致任何调用（含 main.py --api）都抛 UnboundLocalError，且 API_ENABLED=False
的 RuntimeError 守卫成了死代码。本测试锁死该不变式，且不需要真实端口绑定。
"""
from __future__ import annotations

import pytest

import config
import server


class _RecordingServer:
    """替代 _BoundedThreadingHTTPServer：只记录构造参数，serve_forever 不阻塞。"""

    def __init__(self, server_address, RequestHandlerClass, *, max_concurrency: int = 0):
        self.server_address = server_address
        self.max_concurrency = max_concurrency

    def serve_forever(self):
        return  # 不阻塞，便于单测

    def server_close(self):
        return


def _factory(captured):
    def _make(server_address, RequestHandlerClass, *, max_concurrency: int = 0):
        captured["mc"] = max_concurrency
        return _RecordingServer(server_address, RequestHandlerClass,
                                max_concurrency=max_concurrency)
    return _make


def test_start_server_default_max_concurrency(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(server, "_BoundedThreadingHTTPServer", _factory(captured))
    server.start_server(host="127.0.0.1", port=0)
    assert captured["mc"] == config.API_MAX_CONCURRENCY


def test_start_server_override_max_concurrency(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(server, "_BoundedThreadingHTTPServer", _factory(captured))
    server.start_server(host="127.0.0.1", port=0, max_concurrency=3)
    assert captured["mc"] == 3


def test_start_server_disabled_raises(monkeypatch):
    monkeypatch.setattr(server, "_BoundedThreadingHTTPServer", _RecordingServer)
    prev = config.API_ENABLED
    config.API_ENABLED = False
    try:
        with pytest.raises(RuntimeError):
            server.start_server(host="127.0.0.1", port=0)
    finally:
        config.API_ENABLED = prev


def test_start_server_disabled_no_crash_before_guard(monkeypatch):
    """修复前：start_server 在到达 API_ENABLED 守卫前就因 UnboundLocalError 崩溃，
    导致 RuntimeError 守卫永远不可达。修复后应在守卫处正确抛出 RuntimeError。"""
    monkeypatch.setattr(server, "_BoundedThreadingHTTPServer", _RecordingServer)
    prev = config.API_ENABLED
    config.API_ENABLED = False
    try:
        with pytest.raises(RuntimeError):
            server.start_server(host="127.0.0.1", port=0, max_concurrency=0)
    finally:
        config.API_ENABLED = prev
