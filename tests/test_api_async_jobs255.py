# -*- coding: utf-8 -*-
"""
阶段 255 守卫 · API 异步作业模型（PDF / 视频 / 模型下载长任务）。

背景
----
251 把模型查询族做成只读端点，但 PDF 擦除 / 视频擦除 / 模型下载这三类**长任务**
在 server.py 里**始终没有 HTTP 入口**（用法页原话：「均需先设计异步作业模型」）。
直接做成同步 POST 会**占住并发闸门槽位**数分钟~数十分钟，把其它推理请求堵在队列里。

255 用「提交即返回 job_id + 后台 worker 线程」把长任务异步化：
  · POST /api/jobs {kind, params}       -> 立即返回 {status:"queued", job_id, poll}
  · GET  /api/jobs/{id}                 -> 轮询 状态/进度(0~1)/结果
  · GET  /api/jobs/{id}/file            -> 下载结果文件

本守卫钉死五条契约
----------------
1. **端点可用**（真起 socket 打真请求，不用 mock）；
2. **提交契约**：已知 kind 立即返回 queued + job_id + poll 字段；未知 kind -> 400 且
   回显已注册类型（不允许静默吞错）；
3. **轮询契约**：轮询到终态 success，progress==1.0、result 原样透传、结构字段齐全；
4. **文件下载契约**：有 out_path 则 GET .../file 返回文件字节；无 out_path 则 404；
5. **闸门豁免契约**：GET /api/jobs/* 是只读轮询、**豁免并发闸门**；POST /api/jobs 是
   动作、不豁免（与 199 的 fail-closed 一致）。

⚠ 本守卫用一个**确定性的轻量自定义 runner**（`test_ping`）验证全链路，**不触发真实
推理**（故不依赖 GPU/模型权重，也不写多余文件）；仅向 job_dir 写 1 个临时结果文件、
无删除，故不会触发沙箱批量删除护栏。
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

import job_manager as JM  # noqa: E402
import server as S  # noqa: E402


# 确定性轻量 runner：写一个小结果文件 + 回写进度，不碰任何推理。
_DUMMY_KIND = "test_ping"


def _dummy_run(job: JM.Job, cb) -> None:
    cb(0.0, "开始")
    cb(0.5, "处理中")
    out = os.path.join(JM.JOB_MANAGER._job_dir, f"{job.id}_out.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("hello-job")
    with JM.JOB_MANAGER._lock:
        job.result = {"echo": job.params.get("x")}
        job.out_path = out
    cb(1.0, "完成")


@pytest.fixture(scope="module", autouse=True)
def _register_dummy():
    """注册确定性 runner（模块级一次：JM.JOB_MANAGER 是进程级单例）。"""
    JM.JOB_MANAGER.register(_DUMMY_KIND, _dummy_run)
    yield


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


def _post(port: int, path: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request("POST", path, body=body,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


# --------------------------------------------------------------------------- #
# 1) 提交契约
# --------------------------------------------------------------------------- #
def test_submit_known_kind_returns_job_id(live_server):
    """已知 kind -> 200 + status=queued + job_id + poll 字段。"""
    st, body = _post(live_server, "/api/jobs",
                     {"kind": _DUMMY_KIND, "params": {"x": 7}})
    assert st == 200, f"期望 200，实得 {st}：{body[:200]}"
    data = json.loads(body)
    assert data.get("status") == "queued", f"status 应为 queued：{data}"
    jid = data.get("job_id")
    assert jid and isinstance(jid, str) and len(jid) >= 8, f"job_id 异常：{jid}"
    assert data.get("poll") == f"/api/jobs/{jid}", f"poll 字段错误：{data}"
    # 回写模块级变量供后续用例复用
    test_submit_known_kind_returns_job_id._jid = jid


def test_submit_unknown_kind_rejected_with_registered_list(live_server):
    """未知 kind -> 400，且回显已注册类型（不允许静默吞错）。"""
    st, body = _post(live_server, "/api/jobs", {"kind": "nope", "params": {}})
    assert st == 400, f"期望 400，实得 {st}：{body[:200]}"
    data = json.loads(body)
    assert "registered" in data, f"400 未回显已注册类型：{sorted(data)}"
    assert _DUMMY_KIND in data["registered"], \
        "已注册的 test_ping 未出现在回显列表（runner 注册链路断？）"


def test_submit_missing_kind_rejected(live_server):
    """缺 kind -> 400，错误信息点名 kind。"""
    st, body = _post(live_server, "/api/jobs", {"params": {}})
    assert st == 400, f"期望 400，实得 {st}：{body[:200]}"
    assert "kind" in json.loads(body)["error"], "错误未点名 kind"


# --------------------------------------------------------------------------- #
# 2) 轮询契约
# --------------------------------------------------------------------------- #
def test_poll_reaches_terminal_success(live_server):
    """轮询已知作业直到终态：success + progress==1.0 + result 透传。"""
    # 先提交一个新作业（独立用例，不依赖上面的 _jid）
    st, body = _post(live_server, "/api/jobs",
                     {"kind": _DUMMY_KIND, "params": {"x": 42}})
    assert st == 200
    jid = json.loads(body)["job_id"]

    final = None
    for _ in range(100):
        st2, b2 = _get(live_server, f"/api/jobs/{jid}")
        assert st2 == 200, f"轮询期望 200，实得 {st2}：{b2[:200]}"
        jb = json.loads(b2)
        if jb["status"] in ("success", "failed"):
            final = jb
            break
        import time
        time.sleep(0.05)
    assert final is not None, "50 次轮询仍未终态（worker 未执行？）"
    assert final["status"] == "success", f"期望 success，实得 {final}"
    assert final["progress"] == 1.0, f"progress 应为 1.0：{final}"
    assert final["result"] == {"echo": 42}, f"result 未透传：{final}"


def test_poll_response_structure_complete(live_server):
    """轮询响应须含全部约定字段（id/kind/status/progress/message/params/
    result/error/out_path/created_at/updated_at），防止字段悄悄丢。"""
    st, body = _post(live_server, "/api/jobs", {"kind": _DUMMY_KIND})
    assert st == 200
    jid = json.loads(body)["job_id"]
    # 等终态
    for _ in range(100):
        st2, b2 = _get(live_server, f"/api/jobs/{jid}")
        jb = json.loads(b2)
        if jb["status"] == "success":
            break
        import time
        time.sleep(0.05)
    expected = {"id", "kind", "status", "progress", "message", "params",
                "result", "error", "out_path", "created_at", "updated_at"}
    assert expected <= set(jb), f"轮询响应缺字段：{expected - set(jb)}"


# --------------------------------------------------------------------------- #
# 3) 文件下载契约
# --------------------------------------------------------------------------- #
def test_file_download_returns_bytes(live_server):
    """有 out_path 的作业，GET .../file 返回结果文件字节。"""
    st, body = _post(live_server, "/api/jobs", {"kind": _DUMMY_KIND})
    assert st == 200
    jid = json.loads(body)["job_id"]
    for _ in range(100):
        st2, b2 = _get(live_server, f"/api/jobs/{jid}")
        if json.loads(b2)["status"] == "success":
            break
        import time
        time.sleep(0.05)
    # 直接用 http.client 取原始字节
    c = http.client.HTTPConnection("127.0.0.1", live_server, timeout=30)
    try:
        c.request("GET", f"/api/jobs/{jid}/file")
        r = c.getresponse()
        raw = r.read()
    finally:
        c.close()
    assert r.status == 200, f"期望 200，实得 {r.status}"
    assert raw == b"hello-job", f"下载内容不符：{raw!r}"


def test_file_download_missing_returns_404(live_server):
    """无 out_path 的作业，GET .../file 应 404（而非 500 或空 200）。"""
    # 手动塞一个终态但无 out_path 的作业
    nofile = JM.Job(id="t_nofile_255", kind=_DUMMY_KIND, status="success")
    with JM.JOB_MANAGER._lock:
        JM.JOB_MANAGER._jobs["t_nofile_255"] = nofile
    st, _ = _get(live_server, "/api/jobs/t_nofile_255/file")
    assert st == 404, f"无文件应 404，实得 {st}"


def test_get_unknown_job_returns_404(live_server):
    """不存在的 job_id -> 404。"""
    st, _ = _get(live_server, "/api/jobs/zzzzzzzzzzzz")
    assert st == 404, f"不存在 job 应 404，实得 {st}"


# --------------------------------------------------------------------------- #
# 4) 闸门豁免契约
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


def test_job_poll_is_exempt_from_gate():
    """GET /api/jobs/{id} 是只读轮询，必须豁免并发闸门（繁忙时也要能查进度）。"""
    assert _peek_line(b"GET /api/jobs/abc123 HTTP/1.1"), \
        "GET /api/jobs/* 未豁免并发闸门 ⇒ 服务繁忙时无法轮询进度"


def test_job_submit_is_NOT_exempt_from_gate():
    """POST /api/jobs 是动作（开启长任务），不得豁免（遵循 199 fail-closed）。"""
    assert not _peek_line(b"POST /api/jobs HTTP/1.1"), \
        "POST /api/jobs 被错误豁免并发闸门"


# --------------------------------------------------------------------------- #
# 5) 文档漂移契约
# --------------------------------------------------------------------------- #
def test_usage_page_documents_jobs_endpoints(live_server):
    """用法页必须登记 /api/jobs（文档与实现漂移是本项目的既有失分模式）。"""
    st, body = _get(live_server, "/")
    assert st == 200
    for token in ("/api/jobs", "异步作业", "model_download"):
        assert token in body, f"用法页未提及 {token}（文档漂移）"
