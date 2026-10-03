"""阶段 199 · REST 并发闸门（`_BoundedThreadingHTTPServer`）守卫。

背景（对应优化清单 D1「并发策略」）：
    `ThreadingHTTPServer` 的默认模型是**每个连接开一个线程、不设上限**；而本服务
    5 个端点**每次调用都要跑一次完整推理**（检测 + LaMa 修复）。若无闸门，N 个并发
    请求 = N 线程同时抢 CPU/显存 ⇒ 单请求时延成倍恶化，直接冲垮阶段 186 立的
    **P95 门禁**（185 原话：「并发时 CPU 争用，可能拖垮 P95 ⇒ 与 A5 互为前提」）。
    ⚠ `config.SERVER_MAX_THREADS`（"队列最大并发请求数"）**不作用于 REST**——
      它只喂 Gradio 队列，故 REST 必须有独立闸门，否则即 91「配置项名存实亡」的变体。

锁定四层不变式：
  ① 闸门本体：并发峰值 **不超过** 配置上限（真并发打 HTTP，不是读代码断言）；
  ② 语义为**排队**而非拒绝：超额请求最终**仍拿到 200**（不是 429/503）——
     这是刻意选择（客户端只变慢、不报错，P95 可预测）；
  ③ 豁免契约：`/api/health` 与 `/` **不参与排队**，且 health 回报闸门自省字段；
  ④ 可关闭性：`max_concurrency=0` ⇒ 恢复无限并发（逃生门，且闸门如实报 `gated=False`）。

注入式反例（防「闸门其实没生效」的自欺）：
    同一并发负载分别打在 `max_concurrency=1` 与 `=0` 的服务器上，断言
    **不限流时观测到的并发峰值显著更高**——若两者峰值相同，说明闸门形同虚设。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

import config
import server


# ---------------------------------------------------------------------------
# 测试脚手架
# ---------------------------------------------------------------------------
def _make_server(max_concurrency: int):
    """在随机端口起一个带闸门的生产服务器。"""
    httpd = server._BoundedThreadingHTTPServer(
        ("127.0.0.1", 0), server._Handler, max_concurrency=max_concurrency)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


def _get_json(base: str, path: str, timeout: float = 30.0):
    with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as r:
        return json.loads(r.read())


@pytest.fixture
def slow_inpaint(monkeypatch):
    """把 `/api/remove` 变慢到可控时长，使并发窗口足够宽、峰值可观测。

    直接替换 `api.remove_watermark_bytes`——闸门在**请求处理线程**里取信号量，
    与业务实现无关，故替换业务函数不影响被测行为。
    """
    import api as _api

    def _slow(data, **kw):
        time.sleep(0.35)
        return b"fake-png-bytes"

    monkeypatch.setattr(_api, "remove_watermark_bytes", _slow)


def _fire_concurrent(base: str, n: int, *, timeout: float = 60.0) -> list:
    """并发发起 n 个 `/api/remove` 请求，返回各自 HTTP 状态码（按完成顺序）。"""
    import cv2
    import numpy as np
    import base64 as _b64

    img = np.full((64, 64, 3), 245, dtype=np.uint8)
    _, buf = cv2.imencode(".png", img)
    payload = _b64.b64encode(buf.tobytes()).decode("ascii")

    codes: list = []
    lock = threading.Lock()

    def _one():
        req = urllib.request.Request(
            f"{base}/api/remove", data=json.dumps({"image": payload}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code = r.status
        except Exception:  # noqa: BLE001 - 记录失败也算一个观测点
            code = -1
        with lock:
            codes.append(code)

    threads = [threading.Thread(target=_one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)
    return codes


# ---------------------------------------------------------------------------
# ① 闸门本体：峰值不超上限
# ---------------------------------------------------------------------------
def test_concurrency_peak_never_exceeds_limit(slow_inpaint):
    """max_concurrency=2 时，6 个并发请求的**观测峰值不得超过 2**。"""
    httpd, base = _make_server(2)
    try:
        _fire_concurrent(base, 6)
        stats = httpd.concurrency_stats()
        assert stats["max_concurrency"] == 2
        assert stats["gated"] is True
        assert stats["peak"] <= 2, f"并发峰值 {stats['peak']} 超过上限 2 ⇒ 闸门失效"
        assert stats["active"] == 0, "全部请求结束后 active 必须归零（信号量无泄漏）"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# ② 语义为排队而非拒绝
# ---------------------------------------------------------------------------
def test_over_limit_requests_are_queued_not_rejected(slow_inpaint):
    """超额请求必须**最终拿到 200**（排队），而不是 429/503（拒绝）。

    这是一条刻意选定的语义：REST 客户端只应「变慢」，不应因服务端繁忙而报错。
    """
    httpd, base = _make_server(1)
    try:
        codes = _fire_concurrent(base, 4)
        assert len(codes) == 4, f"应有 4 个响应，实际 {len(codes)}"
        assert all(c == 200 for c in codes), (
            f"超额请求应排队后成功，实际状态码 {codes}（出现非 200 即变成拒绝语义）"
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# ③ 豁免契约
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _warm_capabilities():
    """预热 `api.get_capabilities()`。

    为什么必须预热（LESSONS 200「统计量须高于噪声地板」）：它**首次**调用要加载
    修复插件并探测设备（实测 **1.823s**），而后续调用近乎瞬时。若不预热，这次
    一次性初始化开销会被误算进被测路径，让「health 是否被闸门阻塞」这类
    **时序断言**随机变红——那是测试缺陷，不是实现缺陷。
    """
    import api as _api
    _api.get_capabilities()
    yield


def test_health_is_exempt_from_the_gate(slow_inpaint):
    """闸门被慢请求占满时，/api/health 仍须**快速**可答（否则编排器会误杀繁忙实例）。

    阈值 0.3s 相对「慢请求 0.35s」有一半余量：真被阻塞必然 ≥0.35s 才返回，
    故 0.3s 能可靠区分「豁免」与「排队」，又不被调度抖动误伤。
    """
    httpd, base = _make_server(1)
    try:
        # 占满唯一槽位：2 个慢请求（第二个在排队）。
        blockers = [threading.Thread(target=_fire_concurrent, args=(base, 1))
                    for _ in range(2)]
        for t in blockers:
            t.start()
        time.sleep(0.15)  # 确保慢请求已持有闸门

        t0 = time.perf_counter()
        h = _get_json(base, "/api/health", timeout=5)
        elapsed = time.perf_counter() - t0
        assert h["status"] == "ok"
        assert elapsed < 0.3, (
            f"health 被闸门阻塞了 {elapsed:.3f}s ⇒ 豁免契约失效（繁忙 ≠ 死服）"
        )
        for t in blockers:
            t.join(timeout=30)
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_health_reports_concurrency_self_inspection():
    """health 必须回报闸门自省字段（max_concurrency/active/peak/gated）。"""
    httpd, base = _make_server(2)
    try:
        c = _get_json(base, "/api/health")["concurrency"]
        assert c["max_concurrency"] == 2
        assert c["active"] == 0 and c["peak"] == 0
        assert c["gated"] is True
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# ④ 可关闭性（逃生门）
# ---------------------------------------------------------------------------
def test_zero_disables_the_gate_and_reports_gated_false():
    """max_concurrency=0 ⇒ 关闭闸门（本地调试逃生门），且如实报 gated=False。"""
    httpd, base = _make_server(0)
    try:
        assert httpd.concurrency_stats()["gated"] is False
        assert _get_json(base, "/api/health")["concurrency"]["max_concurrency"] == 0
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------------------
# 注入式反例：闸门「形同虚设」必须能被证伪
# ---------------------------------------------------------------------------
def test_injection_counterexample_gate_actually_throttles(slow_inpaint):
    """反例：不限流时的并发峰值必须**显著高于**限流时。

    若 `max_concurrency=0`（不限）与 `=1`（限 1）观测到的峰值相同，
    说明闸门根本没起作用——本断言专门证伪这种"看起来在、其实不在"的实现。
    """
    gated, gated_base = _make_server(1)
    free, free_base = _make_server(0)
    try:
        _fire_concurrent(gated_base, 4)
        _fire_concurrent(free_base, 4)
        gated_peak = gated.concurrency_stats()["peak"]
        free_peak = free.concurrency_stats()["peak"]
        assert gated_peak <= 1, f"限流侧峰值 {gated_peak} 应 ≤ 1"
        assert free_peak > gated_peak, (
            f"不限流峰值 {free_peak} 未高于限流峰值 {gated_peak} ⇒ "
            f"闸门形同虚设（并发没有被真正串行化）"
        )
    finally:
        for httpd in (gated, free):
            httpd.shutdown()
            httpd.server_close()


# ---------------------------------------------------------------------------
# 配置契约：闸门上限必须来自 config（而非硬编码）
# ---------------------------------------------------------------------------
def test_config_exposes_api_max_concurrency_with_wme_prefix():
    """新配置项必须走 config 的 WME_ 前缀体系（LESSONS 209）。"""
    import re
    src = open("config.py", encoding="utf-8").read()
    assert re.search(r'API_MAX_CONCURRENCY:\s*Final\[int\]\s*=\s*'
                     r'_env_int\("API_MAX_CONCURRENCY"', src), (
        "API_MAX_CONCURRENCY 必须用 _env_int 读取（自动获得 WME_ 前缀与范围校验）"
    )
    # 取值范围必须含 0（关闭闸门的逃生门）与正数
    assert isinstance(config.API_MAX_CONCURRENCY, int)
    assert config.API_MAX_CONCURRENCY >= 0
