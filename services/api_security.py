# -*- coding: utf-8 -*-
"""
阶段 299 · API 安全层（token 鉴权 + 限流），零新依赖（纯标准库）。

为什么独立成模块
----------------
server.py 是「零依赖 REST 服务」（只用 http.server）。鉴权与限流是横切关注点，
放进独立模块便于单测，也避免把安全逻辑和路由 dispatch 混在一起。

设计要点（与项目门禁家族一致）
--------------------------------
① **默认关闭、零行为变更**：`config.API_AUTH_ENABLED=False`（默认）时本模块所有
   检查直接放行，与「加入本模块前」完全一致——既有调用方、既有测试零影响。
② **fail-closed 启动守卫**：安全模式开启但 `API_TOKEN` 为空时，start_server 直接
   拒绝启动（不接收任何请求），避免「开了鉴权却无密钥」的伪安全。
③ **鉴权范围**：安全模式下，所有 POST 端点（remove/detect/inpaint/batch/jobs 提交）
   与「作业结果文件下载」（GET /api/jobs/{id}/file，数据外发）**必须**携带有效 token；
   只读 GET（health/capabilities/models/jobs 状态）**豁免鉴权**但仍受限流——自省能力
   不能被锁在门外（呼应 251 的闸门豁免契约）。
④ **限流范围**：安全模式下所有请求（GET+POST）走令牌桶，按「密钥」计——携带有效
   token 时按 token 计，否则按客户端 IP 计。令牌桶允许短时突发（burst），长期速率
   受 API_RATE_LIMIT_PER_MIN 约束，防止滥用拖垮阶段 186 的 P95 性能门禁。
⑤ **密钥比对用恒定时间**（hmac.compare_digest），避免时序侧信道。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import config


# ----------------------------------------------------------------------------
# 限流器（进程内令牌桶；单进程 stdlib 服务足够，无需共享存储）
# ----------------------------------------------------------------------------
_LOCK = threading.Lock()
_BUCKETS: Dict[str, List[float]] = {}  # key -> [剩余令牌, 上次补充时间戳(monotonic)]


def _rate_limit_allow(key: str) -> bool:
    """令牌桶：放行返回 True；限流返回 False（调用方应回 429）。

    速率/突发容量**每次读 config**（动态），便于测试用 monkeypatch 调整，
    也支持运行时改环境变量后下次请求生效。桶状态按 key 持久在 _BUCKETS。
    """
    if not config.API_AUTH_ENABLED:
        return True  # 安全模式关闭 ⇒ 不限流
    rate = float(config.API_RATE_LIMIT_PER_MIN)        # 令牌/分钟
    burst = float(config.API_RATE_BURST)               # 桶容量（突发上限）
    now = time.monotonic()
    with _LOCK:
        tokens, last = _BUCKETS.get(key, [burst, now])
        tokens = min(burst, tokens + (now - last) * rate / 60.0)
        if tokens >= 1.0:
            _BUCKETS[key] = [tokens - 1.0, now]
            return True
        _BUCKETS[key] = [tokens, now]
        return False


def reset_rate_limiter() -> None:
    """清空限流桶状态（测试用，避免用例间串扰）。"""
    with _LOCK:
        _BUCKETS.clear()


# ----------------------------------------------------------------------------
# token 提取与比对
# ----------------------------------------------------------------------------
def extract_token(headers: Any, query_str: str = "") -> Optional[str]:
    """从请求取出 token：优先 `Authorization: Bearer <t>`，其次 `?token=<t>`。"""
    auth = ""
    try:
        auth = headers.get("Authorization", "") or headers.get("authorization", "")
    except Exception:  # noqa: BLE001 - headers 形态异常时当无 token
        auth = ""
    if isinstance(auth, str) and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if query_str:
        try:
            from urllib.parse import parse_qs
            qs = parse_qs(query_str)
            tok = qs.get("token")
            if tok:
                return tok[0]
        except Exception:  # noqa: BLE001 - 查询串畸形 ⇒ 当无 token
            return None
    return None


def _token_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    secret = config.API_TOKEN
    if not secret:
        return False
    # 恒定时间比对，防时序侧信道。
    return __import__("hmac").compare_digest(token, secret)


# ----------------------------------------------------------------------------
# 统一安全闸门（在 do_GET / do_POST 顶部调用）
# ----------------------------------------------------------------------------
def security_gate(handler: Any, *, auth_required: bool) -> bool:
    """返回 True=放行；False=已回写 401/429，调用方应直接 return。

    handler 需提供：`headers`（email.message.Message）、`path`（含查询串）、
    `client_address[0]`（IP）、`_send_json(code, payload)`。
    """
    if not config.API_AUTH_ENABLED:
        return True  # 安全模式关闭 ⇒ 完全放行

    query_str = handler.path.split("?", 1)[1] if "?" in handler.path else ""
    token = extract_token(handler.headers, query_str)
    ip = "unknown"
    try:
        ip = handler.client_address[0]
    except Exception:  # noqa: BLE001
        pass
    key = token or ip

    # ① 限流（所有请求）
    if not _rate_limit_allow(key):
        retry = 1
        handler._send_json(429, {
            "error": "请求过于频繁，请稍后再试",
            "retry_after_sec": retry,
        })
        return False

    # ② 鉴权（仅 auth_required 路径）
    if auth_required and not _token_valid(token):
        handler._send_json(401, {
            "error": "未授权：缺少或无效的 API token",
            "hint": "在请求头 `Authorization: Bearer <token>` 或查询串 `?token=<token>` 中提供",
        })
        return False

    return True
