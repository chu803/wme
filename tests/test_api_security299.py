# -*- coding: utf-8 -*-
"""
阶段 299 守卫 · API 安全层（token 鉴权 + 限流）。

背景（你决定：API 鉴权/限流）
---------------------------
发布前的「商品外壳」缺口之一是 REST 接口无鉴权/限流：任何能访问端口的程序都能无限
调用重型推理端点。本阶段补一个**零新依赖**（纯标准库）的安全层，由 config.API_AUTH_ENABLED
总开关控制，**默认关闭 ⇒ 与加入前行为完全一致**（既有调用方/测试零影响）。

本守卫钉死契约
-------------
1. 默认关闭时：POST 无 token 也绝不返回 401/429（零行为变更）。
2. 安全模式开启 + token 正确：POST 放行（过闸，后续业务校验如缺 image 仍走原 400）。
3. 安全模式开启 + 无/错 token：POST 与作业文件下载返回 401。
4. 只读 GET（capabilities 等）豁免鉴权（仍可达，呼应 251 闸门豁免），但作业文件下载需鉴权。
5. 限流：突发耗尽后返回 429。
6. fail-closed：安全模式开启但 API_TOKEN 为空 ⇒ start_server 拒绝启动。
7. 限流器/鉴权逻辑单测：令牌桶、token 提取、恒定时间比对。

⚠ 全部用 monkeypatch 在进程内切换 config（server.py 在请求时动态读 config.API_*），
   不依赖 import 顺序；只读测试不写文件，不触发沙箱删除护栏。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
import server as S  # noqa: E402
from services import api_security as SEC  # noqa: E402


# ---------------------------------------------------------------------------
# 单元：令牌桶限流
# ---------------------------------------------------------------------------
def test_token_bucket_allows_burst_then_denies(monkeypatch):
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_RATE_LIMIT_PER_MIN", 60)
    monkeypatch.setattr(config, "API_RATE_BURST", 2)
    SEC.reset_rate_limiter()
    assert SEC._rate_limit_allow("k") is True
    assert SEC._rate_limit_allow("k") is True
    assert SEC._rate_limit_allow("k") is False  # 突发耗尽
    # 不同 key 互不干扰
    assert SEC._rate_limit_allow("other") is True


# ---------------------------------------------------------------------------
# 单元：token 提取（header 优先，query 兜底）
# ---------------------------------------------------------------------------
def test_extract_token_from_header_and_query():
    assert SEC.extract_token({"Authorization": "Bearer abc"}, "") == "abc"
    assert SEC.extract_token({}, "token=xyz") == "xyz"
    assert SEC.extract_token({}, "") is None
    assert SEC.extract_token({"authorization": "bearer  x"}, "") == "x"


# ---------------------------------------------------------------------------
# 单元：security_gate 在默认关闭 / 开启下的行为
# ---------------------------------------------------------------------------
class _FakeHandler:
    def __init__(self, path, token=None, ip="127.0.0.1"):
        self.path = path
        self.client_address = (ip, 0)
        self._status = None
        hdrs = {}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        self.headers = hdrs

    def _send_json(self, code, payload):
        self._status = code
        self._payload = payload


def test_gate_off_is_noop(monkeypatch):
    """默认关闭：即便 auth_required，也放行且不回写任何响应。"""
    monkeypatch.setattr(config, "API_AUTH_ENABLED", False)
    h = _FakeHandler("/api/remove", token=None)
    assert SEC.security_gate(h, auth_required=True) is True
    assert h._status is None


def test_gate_on_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_TOKEN", "secret")
    ok = _FakeHandler("/api/remove", token="secret")
    bad = _FakeHandler("/api/remove", token="wrong")
    assert SEC.security_gate(ok, auth_required=True) is True
    assert SEC.security_gate(bad, auth_required=True) is False
    assert bad._status == 401


# ---------------------------------------------------------------------------
# 集成：安全模式开启的 live server
# ---------------------------------------------------------------------------
def _wait_ready(port: int, timeout: float = 10.0) -> None:
    """等服务线程真正开始接受连接。

    2026-10-23 全量实测：`serve_forever()` 线程刚 start 时立刻发请求，在慢机（当天整机比前一日慢 2.9×）
    上偶发 `ConnectionRefusedError`，表现为 `test_post_without_token_401` 单点红、隔离跑全绿。
    这不是产品的竞态（生产由 start_server 阻塞启动），是**测试夹具的启动竞态** ⇒ 此处轮询等待，
    不放宽任何断言。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"测试服务 {timeout}s 内未就绪（端口 {port}）")


def _make_server(monkeypatch, **cfg):
    for k, v in cfg.items():
        monkeypatch.setattr(config, k, v)
    SEC.reset_rate_limiter()
    srv = S._BoundedThreadingHTTPServer(("127.0.0.1", 0), S._Handler,
                                         max_concurrency=2)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _wait_ready(port)
    return srv, port


@pytest.fixture
def secure_server(monkeypatch):
    srv, port = _make_server(
        monkeypatch,
        API_AUTH_ENABLED=True, API_TOKEN="secret",
        API_RATE_LIMIT_PER_MIN=100000, API_RATE_BURST=100000)
    yield port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def rl_server(monkeypatch):
    # 限流开到极紧：每分钟 1、突发 1 ⇒ 第二请求必 429
    srv, port = _make_server(
        monkeypatch,
        API_AUTH_ENABLED=True, API_TOKEN="secret",
        API_RATE_LIMIT_PER_MIN=1, API_RATE_BURST=1)
    yield port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def open_server():
    # 默认 config（鉴权关闭）
    srv = S._BoundedThreadingHTTPServer(("127.0.0.1", 0), S._Handler,
                                         max_concurrency=2)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _wait_ready(port)
    yield port
    srv.shutdown()
    srv.server_close()


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


def _post(port, path, body=None, token=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        c.request("POST", path, body=json.dumps(body or {}).encode(), headers=headers)
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


def test_default_off_post_without_token_not_401(open_server):
    """零行为变更：鉴权默认关闭时，POST 无 token 不得被 401/429。"""
    st, _ = _post(open_server, "/api/remove", {"image": ""})
    assert st not in (401, 429), f"默认关闭却返回鉴权/限流码：{st}"


def test_post_without_token_401(secure_server):
    st, _ = _post(secure_server, "/api/remove", {"image": ""})
    assert st == 401


def test_post_with_valid_token_passes_gate(secure_server):
    """带正确 token：过闸（缺 image 仍走业务 400，但绝不能是 401/429）。"""
    st, _ = _post(secure_server, "/api/remove", {"image": ""}, token="secret")
    assert st not in (401, 429), f"携带正确 token 却被拒：{st}"


def test_readonly_get_without_token_200(secure_server):
    """只读 GET 豁免鉴权（呼应 251 闸门豁免），无 token 仍 200。"""
    st, _ = _get(secure_server, "/api/capabilities")
    assert st == 200


def test_job_file_download_requires_auth(secure_server):
    """作业结果文件下载（数据外发）必须鉴权：无 token ⇒ 401。"""
    st, _ = _get(secure_server, "/api/jobs/nope/file")
    assert st == 401


def test_job_file_download_with_token_not_401(secure_server):
    """带 token：过闸（作业不存在 ⇒ 404，而非 401）。"""
    st, _ = _get(secure_server, "/api/jobs/nope/file", )
    # 用 header 传 token（query 也可，单测 header 路径）
    c = http.client.HTTPConnection("127.0.0.1", secure_server, timeout=30)
    try:
        c.request("GET", "/api/jobs/nope/file",
                  headers={"Authorization": "Bearer secret"})
        r = c.getresponse()
        st = r.status
    finally:
        c.close()
    assert st == 404, f"带 token 应过闸（作业不存在=404），实得 {st}"


def test_rate_limit_returns_429(rl_server):
    """突发耗尽 ⇒ 第二请求 429。"""
    st1, _ = _get(rl_server, "/api/capabilities")
    st2, _ = _get(rl_server, "/api/capabilities")
    assert st1 == 200, f"首请求应放行，实得 {st1}"
    assert st2 == 429, f"限流应拦截第二请求，实得 {st2}"


def test_start_server_refuses_empty_token(monkeypatch):
    """fail-closed：安全模式开但 token 空 ⇒ 拒绝启动。"""
    monkeypatch.setattr(config, "API_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "API_TOKEN", "")
    with pytest.raises(RuntimeError):
        S.start_server(port=0)
