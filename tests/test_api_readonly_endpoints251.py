# -*- coding: utf-8 -*-
"""
阶段 251 守卫 · API 只读端点（能力自发现 + 模型查询）与并发闸门豁免契约。

背景
----
250 的API 侧审计发现：`api.py` 的能力远多于 HTTP 层暴露的——模型管理族
（`list_models` / `get_active_model` / `verify_model` / `set_active_model`）
**完全没有 HTTP 端点**，且 `get_capabilities()` 只作为 `/api/health` 响应里
的一部分出现（语义混杂：健康 ≠ 能力）。对**容器化部署**而言，这意味着调用方
无法查询「镜像里预置了哪些模型、当前用哪个、权重完整吗」。

251 补了4 个**只读**端点（`/api/capabilities`、`/api/models`、`/api/models/active`、
`/api/models/verify`），并把它们纳入并发闸门豁免。

本守卫钉死三条契约
----------------
1. **端点可用**（真起 socket 打真请求，不用 mock）；
2. **路由优先级**：`/api/models/active` **不得**被 `startswith("/api/models")`
   短前缀吃掉——这是 startswith 路由最容易踩的坑；
3. **豁免契约**：只读端点豁免并发闸门（否则服务繁忙时最需要自省却拿不到），
   而**推理类端点绝不豁免**（否则等于给未鉴权的重活开免检通道，见 199）。

⚠ 本守卫**只发 GET、不写文件**，故不会触发沙箱批量删除护栏。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import server as S  # noqa: E402


@pytest.fixture(scope="module")
def live_server():
    """真起一个带并发闸门的服务器（随机端口，模块内复用）。"""
    srv = S._BoundedThreadingHTTPServer(("127.0.0.1", 0), S._Handler,
                                       max_concurrency=2)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield port
    srv.shutdown()
    srv.server_close()


def _get(port: int, path: str):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# 1) 端点可用
# --------------------------------------------------------------------------- #
def test_capabilities_endpoint(live_server):
    """GET /api/capabilities 应返回非空能力字典（客户端自发现的依据）。"""
    st, body = _get(live_server, "/api/capabilities")
    assert st == 200, f"期望 200，实得 {st}：{body[:200]}"
    data = json.loads(body)
    assert isinstance(data, dict) and data, "能力清单为空 dict"
    # 与 api.get_capabilities() 同源（不允许HTTP 层私自裁剪字段）
    import api
    assert set(data) == set(api.get_capabilities()), (
        "端点返回的字段集与 api.get_capabilities() 不一致——"
        "HTTP 层不得私自裁剪能力清单")


def test_models_list_endpoint(live_server):
    """GET /api/models 应返回含 `models` 数组的 JSON。"""
    st, body = _get(live_server, "/api/models")
    assert st == 200, f"期望 200，实得 {st}"
    data = json.loads(body)
    assert "models" in data, f"响应缺 `models` 键：{sorted(data)}"
    assert isinstance(data["models"], list), "`models` 必须是数组"


def test_models_active_endpoint(live_server):
    """GET /api/models/active 应返回含 `active` 的 JSON。"""
    st, body = _get(live_server, "/api/models/active")
    assert st == 200, f"期望 200，实得 {st}"
    assert "active" in json.loads(body), f"响应缺 `active` 键：{body[:200]}"


def test_models_verify_endpoint_with_and_without_query(live_server):
    """/api/models/verify 无参与带?name= 都应 200（查询串解析不崩）。"""
    for path in ("/api/models/verify", "/api/models/verify?name=lama"):
        st, body = _get(live_server, path)
        assert st == 200, f"{path} 期望 200，实得 {st}：{body[:200]}"
        assert "ok" in json.loads(body), f"{path} 响应缺 `ok` 键"


# --------------------------------------------------------------------------- #
# 2) 契约：health 向后兼容（不得因拆端点而破坏既有调用方）
# --------------------------------------------------------------------------- #
def test_health_still_contains_capabilities(live_server):
    """`/api/health` **仍**包含能力字段——251 拆出独立端点属**新增**，
    不能让既有调用方（靠 health 读能力）静默失效。"""
    st, body = _get(live_server, "/api/health")
    assert st == 200
    h = json.loads(body)
    assert "status" in h, f"health 缺 status：{sorted(h)}"
    assert "concurrency" in h, "health 缺 concurrency（199 已暴露，回归？）"
    import api
    caps = set(api.get_capabilities())
    assert caps & set(h), (
        "health 不再包含任何能力字段 ⇒ 拆 /api/capabilities 时破坏了向后兼容，"
        "依赖 health 读能力的既有调用方会静默失效")


# --------------------------------------------------------------------------- #
# 3) 契约：并发闸门豁免（只读豁免 / 推理不豁免）
# --------------------------------------------------------------------------- #
def _peek_line(request_line: bytes) -> bool:
    """用 socketpair 模拟一条待处理的连接，判定是否豁免并发闸门。"""
    client_sock, srv_sock = socket.socketpair()
    try:
        client_sock.sendall(request_line + b"\r\nHost: x\r\n\r\n")
        return bool(S._is_exempt_from_gate(srv_sock))
    finally:
        client_sock.close()
        srv_sock.close()


@pytest.mark.parametrize("path", [
    "/api/health",
    "/api/capabilities",
    "/api/models",
    "/api/models/active",
    "/api/models/verify",
])
def test_readonly_endpoints_are_exempt_from_gate(path):
    """只读端点必须豁免并发闸门（繁忙时可观测性不能被闸门挡住）。"""
    assert _peek_line(f"GET {path} HTTP/1.1".encode()), (
        f"{path} 未豁免并发闸门 ⇒ 服务繁忙时恰恰最需要自省"
        f"（模型状态/能力）却要排队，可用性被闸门自身挡住")


@pytest.mark.parametrize("path", [
    "/api/remove",
    "/api/detect",
    "/api/inpaint",
    "/api/batch",
])
def test_inference_endpoints_are_NOT_exempt_from_gate(path):
    """推理类端点**绝不**豁免（否则等于给未鉴权的重活开免检通道，199 的核心约束）。"""
    assert not _peek_line(f"POST {path} HTTP/1.1".encode()), (
        f"{path} 被错误豁免并发闸门 ⇒ 未鉴权的重活可绕过排队，"
        f"违反阶段 199 的 fail-closed 约束")


def test_usage_page_documents_new_endpoints(live_server):
    """用法页必须登记新端点（文档与实现漂移是本项目的既有失分模式）。"""
    st, body = _get(live_server, "/")
    assert st == 200
    for token in ("/api/capabilities", "/api/models"):
        assert token in body, f"用法页未提及 {token}（文档漂移）"