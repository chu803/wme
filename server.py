# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : server.py
 文件说明 : 零依赖 REST 服务（阶段 9 拓展功能，顶层模块）。

            只用 Python 标准库（http.server + threading）实现一个轻量 HTTP 接口，
            让任何能发 HTTP 请求的程序（网页 JS、其他后端、自动化脚本）都能远程
            调用本工具的水印擦除能力，且【不引入 FastAPI / Flask 等任何新依赖】。

            与 Gradio 网页服务相互独立：
                · 网页界面 : 默认 http://127.0.0.1:7860
                · REST 接口: 默认 http://127.0.0.1:7861   （本文件）

            端点契约：
                GET  /api/health            -> {"status":"ok", ...能力快照,
                                       "concurrency":{"max_concurrency","active","peak","gated"}}
                                           ⚠ health 与用法页豁免并发闸门（阶段 199）
                POST /api/remove            -> 单图处理（检测+修复一步到位）
                     请求体(JSON): {"image": "<base64>", "mode","engine",
                                    "strength","dilate","feather","threshold",
                                    "denoise","sensitivity","fmt","quality","evaluate"}
                     响应(JSON):   {"status","result":"<base64>","mask_pixels",
                                    "regions","used_ocr","engine","quality"}
                POST /api/detect            -> 仅检测，返回水印掩码
                     请求体(JSON): {"image": "<base64>", "preset","sensitivity"}
                     响应(JSON):   {"status","mask":"<base64 png>", "mask_pixels",
                                    "coverage_ratio","region_count","by_kind",
                                    "used_ocr","regions":[{"x","y","w","h","kind","confidence"}]}
                POST /api/inpaint           -> 按提供的掩码修复（跳过检测）
                     请求体(JSON): {"image":"<base64>","mask":"<base64 png>",
                                    "engine","strength","dilate","feather",
                                    "threshold","denoise","dual_run","fmt","quality"}
                     响应(JSON):   {"status","result":"<base64>","engine"}
                POST /api/batch            -> 批量处理
                     请求体(JSON): {"images":["<base64>","<base64>",...], "params":{...}}
                     响应(JSON):   {"results":[{...同上...}, ...]}
                GET  /                     -> 用法说明 HTML

            设计约束：复用 api.remove_watermark_bytes，与 GUI/CLI/库共用同一核心。
            并发约束：阶段 199 起经 `_BoundedThreadingHTTPServer` 限流（config.API_MAX_CONCURRENCY，
            超额排队而非拒绝），因每个请求都要跑一次完整推理，无闸门会拖垮 P95。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import json
import base64
import socket
import threading
import urllib.parse as _urllib_parse        # 阶段 251：解析 ?name= 查询串（模型校验端点）
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
import api
import job_manager as JM  # 阶段 255：异步作业模型（PDF/视频/模型下载）
from services import api_security as SEC  # 阶段 299：token 鉴权 + 限流（安全模式）
from utils.logger import get_logger

log = get_logger("server")

# 单次请求体的最大字节数（约 50MB），防止异常大请求打垮服务。
_MAX_BODY_BYTES = 50 * 1024 * 1024


# ==============================================================================
# 第 1 节：参数解析与响应构造
# ==============================================================================
def _collect_params(body: Dict[str, Any]) -> Dict[str, Any]:
    """从请求体里挑出合法的修复参数，缺省留给 api 去补 config 默认值。

    阶段 76 扩容：与 `api.remove_watermark_bytes` 的签名对齐——此前白名单只覆盖
    基础参数，导致 REST 客户端**无法使用**双跑择优（阶段 74）与可选增强（阶段 21）。
    另对布尔键做归一，字符串 "true"/"1"/"yes"/"on" 也接受（便于表单类客户端）。
    """
    params: Dict[str, Any] = {}
    for key in ("mode", "engine", "strength", "dilate", "feather",
                "threshold", "denoise", "sensitivity", "fmt", "quality", "evaluate",
                "preset", "auto_preset",
                # 阶段 76：可选增强（阶段 21）+ 双跑择优（阶段 74）
                "poisson_blend", "poisson_strength",
                "texture_transfer", "texture_amount",
                "artifact_reduce", "artifact_amount",
                "superres", "superres_amount",
                "real_superres", "real_scale", "real_backend",
                "dual_run",
                # 阶段 233：显式允许「自动选预设 + 直接擦除」。
                #   REST 是**客户端显式发起的处理请求**，但「自动选预设」意味着
                #   服务端替客户端拿主意；对文字密集插画可能大面积误检，
                #   故同样要求客户端显式表态（详见 api.AutoEraseNotConfirmed）。
                "allow_auto_erase"):
        if key in body:
            params[key] = body[key]
    for bool_key in ("dual_run", "auto_preset", "evaluate", "poisson_blend",
                     "texture_transfer", "artifact_reduce", "superres",
                     "real_superres", "allow_auto_erase"):
        if bool_key in params and isinstance(params[bool_key], str):
            params[bool_key] = params[bool_key].strip().lower() in (
                "1", "true", "yes", "on")
    return params


def _quality_to_dict(qr: Optional[Any]) -> Optional[Dict[str, Any]]:
    """把 QualityReport 转成可 JSON 序列化的字典。"""
    if qr is None:
        return None
    return {
        "score": qr.score,
        "level": qr.level,
        "ssim": qr.ssim,
        "changed_ratio": qr.changed_ratio,
        "detail": qr.detail,
    }


def _process_one_b64(image_b64: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理单张 base64 图片，返回 REST 响应字典（成功或失败都返回结构化结果）。
    """
    try:
        raw = base64.b64decode(image_b64)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"base64 解码失败：{exc}"}

    try:
        out_bytes, qr = api.remove_watermark_bytes(raw, **params)
    except api.AutoEraseNotConfirmed as exc:
        # 阶段 233：**单独识别**，让客户端能程序化区分
        # 「参数需要确认」与「处理真的失败」——否则客户端会把二者混为一谈。
        return {
            "status": "needs_confirmation",
            "error": str(exc),
            "hint": ("请显式指定 preset，或传 allow_auto_erase=true，"
                     "或先调 /api/detect 预览掩码后再传 mask"),
            "regions": None,
            "used_ocr": None,
        }
    except Exception as exc:  # noqa: BLE001 - 单张出错不影响服务可用性。
        return {"status": "failed", "error": str(exc)}

    return {
        "status": "success",
        "result": base64.b64encode(out_bytes).decode("ascii"),
        "mask_pixels": None,  # 字节模式不回传内部统计，保持接口精简
        "regions": None,
        "used_ocr": None,
        "engine": params.get("engine") or config.INPAINT_ENGINE,
        # 阶段 76：回显本次实际生效的双跑择优开关（请求未传时取进程默认），
        # 便于客户端确认「这一张到底有没有双跑」。
        "dual_run": (params["dual_run"] if "dual_run" in params
                     else bool(getattr(config, "INPAINT_DUAL_RUN", False))),
        "quality": _quality_to_dict(qr),
    }


def _collect_inpaint_params(body: Dict[str, Any]) -> Dict[str, Any]:
    """从请求体里挑出合法的「修复」参数（供 /api/inpaint 使用）。"""
    params: Dict[str, Any] = {}
    for key in ("strength", "dilate", "feather", "threshold", "denoise",
                "engine", "dual_run", "fmt", "quality"):
        if key in body:
            params[key] = body[key]
    if "dual_run" in params and isinstance(params["dual_run"], str):
        params["dual_run"] = params["dual_run"].strip().lower() in (
            "1", "true", "yes", "on")
    return params


def _detect_one_b64(image_b64: str) -> Dict[str, Any]:
    """检测单张 base64 图片，返回 REST 响应字典（成功或失败都返回结构化结果）。"""
    try:
        raw = base64.b64decode(image_b64)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"base64 解码失败：{exc}"}
    try:
        mask_bytes, stats = api.detect_watermark_bytes(
            raw, preset=None, sensitivity=None)
    except Exception as exc:  # noqa: BLE001 - 单张出错不影响服务可用性。
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "success",
        "mask": base64.b64encode(mask_bytes).decode("ascii"),
        **stats,
    }


def _inpaint_one_b64(image_b64: str, mask_b64: str,
                     params: Dict[str, Any]) -> Dict[str, Any]:
    """按提供的掩码修复单张 base64 图片，返回 REST 响应字典。"""
    try:
        img_raw = base64.b64decode(image_b64)
        mask_raw = base64.b64decode(mask_b64)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"base64 解码失败：{exc}"}
    try:
        out_bytes = api.inpaint_watermark_bytes(img_raw, mask_raw, **params)
    except Exception as exc:  # noqa: BLE001 - 单张出错不影响服务可用性。
        return {"status": "failed", "error": str(exc)}
    return {
        "status": "success",
        "result": base64.b64encode(out_bytes).decode("ascii"),
        "engine": params.get("engine") or config.INPAINT_ENGINE,
    }


# ==============================================================================
# 第 1.5 节：并发闸门（阶段 199 · 对应优化清单 D1「并发策略」）
# ==============================================================================
# 背景：ThreadingHTTPServer 的默认模型是「每个连接开一个线程、不设上限」。而本服务
#       5 个端点**每次调用都要跑一次完整推理**（检测 + LaMa 修复），若无上限，N 个并发
#       请求 = N 线程同时抢 CPU/显存 ⇒ 单请求时延成倍恶化，**直接冲垮阶段 186 的 P95 门禁**
#       （185 清单 D1 原话：「并发时 CPU 争用，可能拖垮 P95 ⇒ 与 A5 互为前提」）。
# ⚠ Gradio 侧的 config.SERVER_MAX_THREADS **不作用于 REST**（那是 Gradio 自己的队列），
#   故 REST 必须有独立闸门，否则就是 91「配置项名存实亡」的变体。
# 语义：**排队等待**而非拒绝 ⇒ 客户端只会变慢、不会拿到错误码，且 P95 可预测。
# 豁免：/api/health 与 / 不参与排队（健康探针必须始终可答，否则长推理占满槽位时
#       编排器/浏览器会把「繁忙」误判为「死服」而误杀或白屏）。
class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """带并发上限的 ThreadingHTTPServer（`max_concurrency=0` 关闭闸门）。"""

    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass,
                 *, max_concurrency: int = 0) -> None:
        self.max_concurrency = max(0, int(max_concurrency))
        self._sem = (threading.BoundedSemaphore(self.max_concurrency)
                     if self.max_concurrency > 0 else None)
        self._active = 0
        self._peak = 0
        self._lock = threading.Lock()
        super().__init__(server_address, RequestHandlerClass)

    def process_request_thread(self, request, client_address) -> None:
        """在**工作线程内**取闸门：accept 循环永不被阻塞（否则客户端连不上而非变慢）。

        ⚠ 指标语义（阶段 199 守卫实测抓出）：**豁免请求不占用闸门槽位，故也不计入
        `active`/`peak`**。否则 health 探针（恒豁免）会把 `peak` 顶过 `max_concurrency`，
        让「峰值 ≤ 上限」这条不变式在指标上自相矛盾（实测 peak=2 / 上限=1）。
        """
        exempt = _is_exempt_from_gate(request)
        gated = self._sem is not None and not exempt
        if gated:
            self._sem.acquire()
        if not exempt:
            with self._lock:
                self._active += 1
                self._peak = max(self._peak, self._active)
        try:
            super().process_request_thread(request, client_address)
        finally:
            if not exempt:
                with self._lock:
                    self._active -= 1
            if gated:
                self._sem.release()

    def concurrency_stats(self) -> Dict[str, Any]:
        """闸门可观测状态（供 /api/health 自省与契约测试断言）。"""
        with self._lock:
            return {
                "max_concurrency": self.max_concurrency,
                "active": self._active,
                "peak": self._peak,
                "gated": self._sem is not None,
            }


def _is_exempt_from_gate(request: Any) -> bool:
    """判断该请求是否豁免并发闸门（健康检查 / 用法页 / **只读轻量查询**）。

    实现要点：用 `MSG_PEEK` **只窥探不消费**首行——此时 handler 的 `rfile` 尚未创建，
    直接 `recv` 会把请求行从 socket 里取走，导致后面解析失败。任何异常都按
    「不豁免」处理（fail-closed：宁可排队，也不给未鉴权的重活开免检通道）。

    阶段 251：新增豁免 `/api/capabilities` 与 `/api/models*`。它们是**纯只读、
    毫秒级**的查询（模型槽清单 / 当前槽 / 权重校验），若也排队，则服务繁忙时
    调用方**恰恰最需要**自省「用哪个模型、权重齐不齐」却拿不到——可观测性被
    闸门本身挡住。只读端点无副作用，豁免不构成旁路风险（推理类端点仍全部排队）。
    """
    try:
        head = request.recv(256, socket.MSG_PEEK)
        if not head:
            return False
        line = head.split(b"\r\n", 1)[0]
        if not line.startswith(b"GET "):
            return False
        if line.endswith(b" /"):
            return True
        return (b"/api/health" in line
                or b"/api/capabilities" in line
                or b"/api/models" in line
                or b"/api/jobs" in line)  # 阶段 255：作业轮询是只读轻量查询
    except Exception:  # noqa: BLE001 - 窥探失败即视为不豁免
        return False


# ==============================================================================
# 第 2 节：HTTP 请求处理器
# ==============================================================================
class _Handler(BaseHTTPRequestHandler):
    """处理所有 HTTP 请求（GET / POST），线程安全。"""

    # 让日志安静一些（不打印每条请求到 stderr）。
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        log.debug(fmt, *args)

    # ---- 跨域（CORS）：方便浏览器前端直接 fetch 本接口 ----
    def _set_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802 - 标准库要求此命名
        self.send_response(204)
        self._set_common_headers()
        self.end_headers()

    def _read_body(self) -> Optional[Dict[str, Any]]:
        """读取并解析 JSON 请求体，出错返回 None 并已在内部回写 400。"""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > _MAX_BODY_BYTES:
            self._send_json(400, {"error": "请求体为空或超出大小限制"})
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"error": f"JSON 解析失败：{exc}"})
            return None

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._set_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, code: int, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(code)
        self._set_common_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- 阶段 255：异步作业轮询 / 文件下载 ----
    def _handle_get_job(self) -> None:
        """处理 GET /api/jobs/{id}[/file]。

        路由规则：先把查询串切掉，再按 '/' 切分剩余路径。
          · {id} 且无 /file 后缀  -> 返回作业结构化状态（job_to_dict）。
          · {id}/file            -> 流式下载 result 文件（PDF/视频等）。
        """
        # 去掉查询串，避免 ?x=1 干扰切分。
        path_only = self.path.split("?", 1)[0]
        rest = path_only[len("/api/jobs/"):]
        parts = [p for p in rest.split("/") if p != ""]
        if not parts:
            self._send_json(400, {"error": "缺少 job id（用法：GET /api/jobs/{id}）"})
            return
        jid = parts[0]
        job = JM.JOB_MANAGER.get(jid)
        if job is None:
            self._send_json(404, {"error": f"作业不存在：{jid}"})
            return
        # /file 后缀：下载结果文件。
        if len(parts) >= 2 and parts[1].startswith("file"):
            out_path = job.out_path
            if not out_path or not _os.path.exists(out_path):
                self._send_json(404, {"error": "结果文件不存在或尚未生成",
                                      "status": job.status})
                return
            self._send_file(out_path)
            return
        self._send_json(200, JM.job_to_dict(job))

    def _send_file(self, path: str) -> None:
        """把本地结果文件流式回写（按扩展名选 Content-Type，分块读避免整文件进内存）。"""
        ext = _os.path.splitext(path)[1].lower()
        ctype = {
            ".pdf": "application/pdf",
            ".mp4": "video/mp4",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(ext, "application/octet-stream")
        try:
            size = _os.path.getsize(path)
        except OSError as exc:
            self._send_json(500, {"error": f"读取结果文件失败：{exc}"})
            return
        self.send_response(200)
        self._set_common_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{_os.path.basename(path)}"')
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):  # 客户端断开
                    break

    # ---- GET ----
    def do_GET(self) -> None:  # noqa: N802
        # 阶段 299：安全模式（API_AUTH_ENABLED）下，作业结果文件下载需鉴权；
        # 其余只读 GET 豁免鉴权但仍受限流。限流对所有 GET 生效。
        _path_no_q = self.path.split("?", 1)[0]
        _auth_required = _path_no_q.startswith("/api/jobs/") and "/file" in _path_no_q
        if not SEC.security_gate(self, auth_required=_auth_required):
            return
        if self.path.startswith("/api/health"):
            payload: Dict[str, Any] = {"status": "ok", **api.get_capabilities()}
            # 阶段 199：把并发闸门状态一并暴露，服务方可自省「繁忙」还是「死服」。
            _stats = getattr(self.server, "concurrency_stats", None)
            if callable(_stats):
                payload["concurrency"] = _stats()
            self._send_json(200, payload)
        # ---- 阶段 251：能力自发现 + 模型查询（只读，轻量）----
        # 动机：此前 `get_capabilities` 只作为 /api/health 的一部分出现（语义混杂），
        # 且 api.py 的模型管理族（list/active/verify）**完全没有 HTTP 端点** ——
        # 容器化部署时调用方无法查询「装了哪些模型、当前用哪个、权重完整吗」。
        elif self.path.startswith("/api/capabilities"):
            self._send_json(200, api.get_capabilities())
        # ⚠ 顺序要紧：更「长」的路径必须排在前面，否则会被 startswith 短前缀吃掉。
        elif self.path.startswith("/api/models/active"):
            self._send_json(200, {"active": api.get_active_model()})
        elif self.path.startswith("/api/models/verify"):
            # 允许 ?name=<槽名>；缺省校验当前 active 槽。
            qs = _urllib_parse.parse_qs(_urllib_parse.urlparse(self.path).query)
            name = (qs.get("name") or [None])[0]
            self._send_json(200, api.verify_model(name))
        elif self.path.startswith("/api/models"):
            self._send_json(200, {"models": api.list_models()})
        # ---- 阶段 255：异步作业模型（短轮询，只读、豁免闸门）----
        # GET /api/jobs/{id}      -> 轮询状态 / 进度 / 结果
        # GET /api/jobs/{id}/file -> 下载结果文件（PDF/视频等）
        elif self.path.startswith("/api/jobs/"):
            self._handle_get_job()
        elif self.path == "/" or self.path == "":
            self._send_html(200, _USAGE_HTML)
        else:
            self._send_json(404, {"error": "未知路径。请访问 GET / （用法页）或 "
                                "GET /api/health、/api/capabilities、/api/models；"
                                "POST /api/remove、/api/detect、/api/inpaint、/api/batch"})

    # ---- POST ----
    def do_POST(self) -> None:  # noqa: N802
        # 阶段 299：安全模式下所有 POST 端点（含作业提交）必须携带有效 token；
        # 限流对所有 POST 生效。未过闸已回写 401/429，直接返回。
        if not SEC.security_gate(self, auth_required=True):
            return
        if self.path.startswith("/api/remove"):
            body = self._read_body()
            if body is None:
                return
            image = body.get("image")
            if not isinstance(image, str) or not image:
                self._send_json(400, {"error": "缺少 image 字段（base64 字符串）"})
                return
            params = _collect_params(body)
            self._send_json(200, _process_one_b64(image, params))
        elif self.path.startswith("/api/detect"):
            body = self._read_body()
            if body is None:
                return
            image = body.get("image")
            if not isinstance(image, str) or not image:
                self._send_json(400, {"error": "缺少 image 字段（base64 字符串）"})
                return
            # preset/sensitivity 可选传递，缺省交给 api 补 config 默认。
            self._send_json(200, _detect_one_b64(image))
        elif self.path.startswith("/api/inpaint"):
            body = self._read_body()
            if body is None:
                return
            image = body.get("image")
            mask = body.get("mask")
            if not isinstance(image, str) or not image:
                self._send_json(400, {"error": "缺少 image 字段（base64 字符串）"})
                return
            if not isinstance(mask, str) or not mask:
                self._send_json(400, {"error": "缺少 mask 字段（base64 字符串）"})
                return
            params = _collect_inpaint_params(body)
            self._send_json(200, _inpaint_one_b64(image, mask, params))
        elif self.path.startswith("/api/batch"):
            body = self._read_body()
            if body is None:
                return
            images = body.get("images")
            if not isinstance(images, list) or not images:
                self._send_json(400, {"error": "缺少 images 字段（base64 字符串列表）"})
                return
            params = _collect_params(body.get("params", {}) or {})
            results = [_process_one_b64(im, params) for im in images]
            self._send_json(200, {"results": results})
        # ---- 阶段 255：异步作业提交（毫秒级，仅入队，重活在 worker 线程）----
        elif self.path.startswith("/api/jobs"):
            body = self._read_body()
            if body is None:
                return
            kind = body.get("kind")
            params = body.get("params", {}) or {}
            if not isinstance(kind, str) or not kind:
                self._send_json(400, {"error": "缺少 kind 字段（任务类型）"})
                return
            if not isinstance(params, dict):
                self._send_json(400, {"error": "params 必须是 JSON 对象"})
                return
            jid = JM.JOB_MANAGER.submit(kind, params)
            if jid is None:
                registered = sorted(JM.JOB_MANAGER._runners.keys())
                self._send_json(400, {
                    "error": f"未知任务类型：{kind!r}",
                    "registered": registered,
                })
                return
            self._send_json(200, {"status": "queued", "job_id": jid,
                                  "poll": f"/api/jobs/{jid}"})
        else:
            self._send_json(404, {"error": "未知路径"})


# ==============================================================================
# 第 3 节：服务启动
# ==============================================================================
def start_server(host: Optional[str] = None, port: Optional[int] = None,
                max_concurrency: Optional[int] = None) -> None:
    """
    启动 REST 服务并阻塞运行（直到 Ctrl+C）。

    参数：
        host : 监听地址，缺省取 config.API_HOST（默认 127.0.0.1，仅本机）。
        port : 端口，缺省取 config.API_PORT（默认 7861）。
        max_concurrency : 并发闸门，缺省取 config.API_MAX_CONCURRENCY（默认 4；0=不限）。
    """
    host = host or config.API_HOST
    port = port or config.API_PORT
    max_concurrency = (config.API_MAX_CONCURRENCY if max_concurrency is None
                       else max_concurrency)

    if not config.API_ENABLED:
        raise RuntimeError(
            "REST 服务已被 config.API_ENABLED 禁用，无法启动。"
            "如需启用，请在环境变量中设置 API_ENABLED=true。"
        )

    # 阶段 299：fail-closed 启动守卫——安全模式开启但密钥为空 ⇒ 拒绝启动，
    # 不接收任何请求，避免「开了鉴权却无密钥」的伪安全。
    if config.API_AUTH_ENABLED and not config.API_TOKEN:
        raise RuntimeError(
            "API 安全模式已开启（API_AUTH_ENABLED=true）但 API_TOKEN 为空。"
            "出于 fail-closed 原则服务拒绝启动；请通过环境变量 API_TOKEN "
            "设置一个非空共享密钥后再启动。"
        )

    httpd = _BoundedThreadingHTTPServer((host, port), _Handler,
                                        max_concurrency=max_concurrency)
    url = f"http://{host}:{port}"

    # 阶段 238：REST 服务启动即后台预热 EasyOCR，消除首个 /api/detect|remove 请求的
    # 冷启动卡顿（GPU 模式约 ~5s）。非阻塞守护线程，不影响端口监听与首个连接。
    try:
        if config.DETECT_USE_OCR:
            from core.detector import warm_up_ocr
            warm_up_ocr(blocking=False)
    except Exception:  # noqa: BLE001 - 预热失败绝不影响服务启动
        pass

    print("=" * 60)
    print("AI 智能水印擦除工作台 —— REST 服务已启动")
    print(f"  健康检查 : GET  {url}/api/health")
    print(f"  单图处理 : POST {url}/api/remove")
    print(f"  仅检测   : POST {url}/api/detect")
    print(f"  按掩码修 : POST {url}/api/inpaint")
    print(f"  批量处理 : POST {url}/api/batch")
    print(f"  异步作业 : POST {url}/api/jobs  -> 提交（pdf/video/model_download）")
    print(f"             GET  {url}/api/jobs/{{id}}      -> 轮询状态/进度")
    print(f"             GET  {url}/api/jobs/{{id}}/file -> 下载结果")
    print(f"  并发上限 : {max_concurrency or '不限（已关闭闸门）'}"
          f"（超出的请求排队等待，不报错）")
    if host == "0.0.0.0":
        print("  ⚠️  注意：当前监听 0.0.0.0（全网可访问），请务必配合防火墙！")
    print("=" * 60)
    print("按 Ctrl+C 停止服务。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭 REST 服务……")
    finally:
        httpd.server_close()


# 简单的用法说明页（GET / 返回）。
_USAGE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>AI 智能水印擦除工作台 · REST API</title>
<style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.7;color:#222}
code{background:#f3f3f3;padding:2px 6px;border-radius:4px}pre{background:#0f172a;color:#e2e8f0;padding:16px;border-radius:8px;overflow:auto}
h1{color:#1e3a8a}b{color:#b91c1c}</b></style></head>
<body>
<h1>AI 智能水印擦除工作台 · REST API</h1>
<p>这是本工具的「零依赖 HTTP 接口」，与网页界面相互独立。只需发送 base64 图片即可远程擦除水印。</p>
<h2>① 健康检查</h2>
<pre>GET /api/health</pre>
<h2>② 单图处理（POST /api/remove）</h2>
<pre>{
  "image": "&lt;base64 图片&gt;",
  "engine": "opencv",        // 可选：lama / opencv / auto
  "mode": "auto",            // 可选：auto / manual
  "fmt": "PNG",              // 可选：PNG / JPG / WEBP / TIFF / AVIF
  "evaluate": true           // 可选：是否返回质量评估
}</pre>
<h2>③ 批量处理（POST /api/batch）</h2>
<pre>{ "images": ["&lt;base64&gt;", "&lt;base64&gt;"], "params": { "engine": "opencv" } }</pre>
<h2>④ 仅检测（POST /api/detect）</h2>
<pre>{ "image": "&lt;base64 图片&gt;", "preset": "default" }</pre>
<p>返回 <code>mask</code>（水印掩码的 base64 PNG）+ <code>regions</code>（包围盒列表）+ 覆盖率统计。可先预览再决定修复。</p>
<h2>⑤ 按掩码修复（POST /api/inpaint）</h2>
<pre>{ "image": "&lt;base64 原图&gt;", "mask": "&lt;base64 掩码&gt;", "engine": "opencv" }</pre>
<p>跳过自动检测，直接用你提供的掩码修复——适合「先 detect 预览、再用外部模型/人工圈定掩码」的两段式流程。</p>
<h2>⑥ 能力自发现（阶段 251 新增）</h2>
<pre>GET /api/capabilities</pre>
<p>返回本服务支持的能力清单（引擎、格式、参数可选值等），供调用方<b>自发现</b>而无需硬编码。
此前这份能力清单只作为 <code>/api/health</code> 响应里的一部分出现（语义混杂：健康 ≠ 能力），
现拆为独立端点；<code>/api/health</code> 为<b>向后兼容</b>仍包含它。</p>
<h2>⑦ 模型查询（阶段 251 新增，只读）</h2>
<pre>GET /api/models# 列出全部模型槽
GET /api/models/active        # 当前使用的模型槽
GET /api/models/verify?name=lama  # 校验权重完整性（省略 name 则校验当前槽）</pre>
<p>用于<b>容器化部署时的自省</b>：镜像里预置了哪些模型、当前用哪个、权重是否完整/被截断，
都能直接查询，不必进容器执行 CLI。这三个端点都是<b>只读、毫秒级</b>，因此<b>豁免并发闸门</b>
——服务繁忙时恰恰最需要它们来定位问题。</p>
<p><b>长任务</b>（PDF 擦除 / 视频擦除 / 模型下载）不再提供「同步 POST」，因为它们会占用推理闸门
数分钟甚至数十分钟、把其它请求堵在队列里——改用下面的<b>异步作业模型</b>（阶段 255）：</p>
<pre>POST /api/jobs { "kind": "pdf" | "video" | "model_download", "params": { ... } }
  -> { "status": "queued", "job_id": "xxxx", "poll": "/api/jobs/xxxx" }
GET  /api/jobs/{id}        -> { "status","progress","message","result", ... }
GET  /api/jobs/{id}/file   -> 下载结果文件（PDF/视频）</pre>
<p>提交即返回 <code>job_id</code>（毫秒级、只入队），真正的重活在<b>并发闸门之外的 worker 线程</b>跑，
因此不再占用推理闸门槽位；轮询端点是<b>只读</b>，同样<b>豁免闸门</b>。
<code>kind</code> 可选值即下方「已注册任务类型」；PDF 的 <code>params</code> 含 <code>file</code>（PDF 的 base64）
与其它擦除参数，视频追加 <code>ext</code>，模型下载含 <code>name</code>。</p>
<p><b>仍不提供</b>：<code>POST /api/models/active</code>（切换模型）是<b>写操作</b>需配套确认机制，
暂未开放为 HTTP 端点——这与异步作业模型无关，属另一项变更，需先设计确认流程。</p>
<h2>⑧ 异步长任务（阶段 255 新增）</h2>
<pre>POST /api/jobs { "kind": "pdf", "params": { "file": "&lt;base64 PDF&gt;", "engine": "opencv" } }
  -> { "status": "queued", "job_id": "xxxx", "poll": "/api/jobs/xxxx" }
GET  /api/jobs/xxxx        -> 状态 / 进度（0~1）/ 结果
GET  /api/jobs/xxxx/file   -> 下载生成后的 PDF/视频</pre>
<p>PDF、视频擦除与模型下载都是<b>分钟级到数十分钟级</b>的长任务。若做成同步 POST，会<b>占住并发闸门
槽位</b>数分钟，把其它推理请求全堵在队列里。阶段 255 用「提交即返回 + 后台 worker 线程」把长任务
<b>异步化</b>：提交（POST）只在请求线程里干「建 job + 入队」两件轻活、毫秒返回；真正的重活在
<b>并发闸门之外的 daemon worker 线程</b>执行，故不占推理闸门槽位。轮询（GET）端点是<b>只读</b>，
同样<b>豁免闸门</b>——服务繁忙时也能查进度。任务类型可扩展：<code>kind</code> 缺省支持
<code>pdf</code> / <code>video</code> / <code>model_download</code>；未知类型会被拒绝（400 并回显已注册类型）。</p>
<h2>⑨ 并发与限流</h2>
<p>本服务的每个<b>推理</b>请求都会跑一次完整流程（检测 + 修复），因此内置<b>并发闸门</b>：
超出上限的请求会<b>排队等待</b>（不会收到错误码，只是变慢），以保证时延可预测、避免多人并发时拖垮机器。
上限由环境变量 <code>WME_API_MAX_CONCURRENCY</code> 控制（默认 4，设为 0 关闭闸门）。
<code>GET /api/health</code> 的 <code>concurrency</code> 字段会实时回报 <code>active</code>（正在处理数）、
<code>peak</code>（本进程峰值）与 <code>max_concurrency</code>（上限）。
<b>豁免排队</b>的只读端点：<code>/api/health</code>、<code>/api/capabilities</code>、
<code>/api/models*</code>、<code>/api/jobs*</code> 与本页——它们无副作用，服务繁忙时仍可用来自省。</p>
<p><b>注意：</b>默认仅监听 127.0.0.1（本机）。若需局域网/公网访问，请设置
<code>API_HOST=0.0.0.0</code> 并自行做好防火墙与鉴权，避免被他人滥用。</p>
<h2>⑩ 安全模式（阶段 299 可选）</h2>
<p>若接口需暴露到不可信网络，可开启<b>安全模式</b>：设置 <code>API_AUTH_ENABLED=true</code> 并给
<code>API_TOKEN</code> 一个非空密钥（建议走环境变量，勿硬编码）。开启后：<b>所有 POST 端点</b>
（remove/detect/inpaint/batch/jobs 提交）与<b>作业结果文件下载</b>必须携带
<code>Authorization: Bearer &lt;token&gt;</code>（或 <code>?token=&lt;token&gt;</code>），否则返回 401；
只读 GET（health/capabilities/models/jobs 状态）豁免鉴权但仍受<b>限流</b>保护
（<code>API_RATE_LIMIT_PER_MIN</code> / <code>API_RATE_BURST</code>）。若开启安全模式却未设
<code>API_TOKEN</code>，服务<b>拒绝启动</b>（fail-closed）。默认关闭，行为与未加入前一致。</p>
</body></html>"""


# ==============================================================================
# 模块自检：启动到随机端口，探活 + 单图处理后关闭
# ==============================================================================
if __name__ == "__main__":
    import numpy as np
    import cv2
    import urllib.request

    # 造一张带水印的测试图并转 base64（深色粗体、高对比，能在无 EasyOCR 环境稳定检测）。
    cv_img = np.full((240, 360, 3), 245, dtype=np.uint8)
    cv2.putText(cv_img, "WATERMARK", (15, 40), cv2.FONT_HERSHEY_SIMPLEX,
                1.4, (40, 40, 40), 4)
    _, buf = cv2.imencode(".png", cv_img)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")

    # 启动到随机端口（走生产的带闸门服务器类，与 start_server 同一路径）。
    srv = _BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_concurrency=2)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{port}"

    # 健康检查
    with urllib.request.urlopen(f"{base}/api/health", timeout=10) as r:
        h = json.loads(r.read())
    assert h["status"] == "ok", "健康检查失败"
    print(f"用例1通过：/api/health -> device={h['device']} lama={h['lama_available']}")

    # 单图处理
    # ⚠ 阶段 233 起：未显式指定 preset 的「自动选预设 + 直接擦除」会被确认门拦下
    # （needs_confirmation），故自检需显式表态 allow_auto_erase=true 才能走通真实擦除链路。
    req = json.dumps({"image": b64, "engine": "opencv", "evaluate": True,
                     "allow_auto_erase": True}).encode("utf-8")
    req_obj = urllib.request.Request(
        f"{base}/api/remove", data=req,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req_obj, timeout=30) as r:
        resp = json.loads(r.read())
    assert resp["status"] == "success" and resp["result"], "单图处理失败"
    qr = resp.get("quality") or {}
    print(f"用例2通过：/api/remove -> 返回结果 {len(resp['result'])} 字符，"
          f"质量分 {qr.get('score')}（{qr.get('level')}）")

    # 批量处理（同样需显式 allow_auto_erase，见阶段 233 确认门）
    req2 = json.dumps({"images": [b64, b64],
                      "params": {"engine": "opencv", "allow_auto_erase": True}}).encode("utf-8")
    req_obj2 = urllib.request.Request(
        f"{base}/api/batch", data=req2,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req_obj2, timeout=60) as r:
        resp2 = json.loads(r.read())
    assert len(resp2["results"]) == 2 and all(x["status"] == "success" for x in resp2["results"]), \
        "批量处理失败"
    print(f"用例3通过：/api/batch -> 处理 {len(resp2['results'])} 张，全部成功")

    # 仅检测
    req3 = json.dumps({"image": b64}).encode("utf-8")
    req_obj3 = urllib.request.Request(
        f"{base}/api/detect", data=req3,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req_obj3, timeout=30) as r:
        resp3 = json.loads(r.read())
    assert resp3["status"] == "success" and resp3.get("mask"), "仅检测失败"
    print(f"用例4通过：/api/detect -> 命中像素 {resp3.get('mask_pixels')} "
          f"区域数 {resp3.get('region_count')}")

    # 按掩码修复（用合成实心掩码覆盖水印区域，验证两段式链路）
    syn_mk = np.zeros((240, 360), dtype=np.uint8)
    cv2.rectangle(syn_mk, (5, 5), (355, 100), 255, -1)
    _, mk_buf = cv2.imencode(".png", syn_mk)
    mask_b64 = base64.b64encode(mk_buf.tobytes()).decode("ascii")
    req4 = json.dumps({"image": b64, "mask": mask_b64, "engine": "opencv"}).encode("utf-8")
    req_obj4 = urllib.request.Request(
        f"{base}/api/inpaint", data=req4,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req_obj4, timeout=30) as r:
        resp4 = json.loads(r.read())
    assert resp4["status"] == "success" and resp4.get("result"), "按掩码修复失败"
    print(f"用例5通过：/api/inpaint -> 返回结果 {len(resp4['result'])} 字符")

    srv.shutdown()
    print("server.py 自检通过。")
