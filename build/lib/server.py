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
                GET  /api/health            -> {"status":"ok", ...能力快照}
                POST /api/remove            -> 单图处理
                     请求体(JSON): {"image": "<base64>", "mode","engine",
                                    "strength","dilate","feather","threshold",
                                    "denoise","sensitivity","fmt","quality","evaluate"}
                     响应(JSON):   {"status","result":"<base64>","mask_pixels",
                                    "regions","used_ocr","engine","quality"}
                POST /api/batch            -> 批量处理
                     请求体(JSON): {"images":["<base64>","<base64>",...], "params":{...}}
                     响应(JSON):   {"results":[{...同上...}, ...]}
                GET  /                     -> 用法说明 HTML

            设计约束：复用 api.remove_watermark_bytes，与 GUI/CLI/库共用同一核心。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import json
import base64
import threading
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
                "dual_run"):
        if key in body:
            params[key] = body[key]
    for bool_key in ("dual_run", "auto_preset", "evaluate", "poisson_blend",
                     "texture_transfer", "artifact_reduce", "superres",
                     "real_superres"):
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

    # ---- GET ----
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/health"):
            self._send_json(200, {"status": "ok", **api.get_capabilities()})
        elif self.path == "/" or self.path == "":
            self._send_html(200, _USAGE_HTML)
        else:
            self._send_json(404, {"error": "未知路径，请访问 /api/health 或 POST /api/remove"})

    # ---- POST ----
    def do_POST(self) -> None:  # noqa: N802
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
        else:
            self._send_json(404, {"error": "未知路径"})


# ==============================================================================
# 第 3 节：服务启动
# ==============================================================================
def start_server(host: Optional[str] = None, port: Optional[int] = None) -> None:
    """
    启动 REST 服务并阻塞运行（直到 Ctrl+C）。

    参数：
        host : 监听地址，缺省取 config.API_HOST（默认 127.0.0.1，仅本机）。
        port : 端口，缺省取 config.API_PORT（默认 7861）。
    """
    host = host or config.API_HOST
    port = port or config.API_PORT

    if not config.API_ENABLED:
        raise RuntimeError(
            "REST 服务已被 config.API_ENABLED 禁用，无法启动。"
            "如需启用，请在环境变量中设置 API_ENABLED=true。"
        )

    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print("=" * 60)
    print("AI 智能水印擦除工作台 —— REST 服务已启动")
    print(f"  健康检查 : GET  {url}/api/health")
    print(f"  单图处理 : POST {url}/api/remove")
    print(f"  批量处理 : POST {url}/api/batch")
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
<p><b>注意：</b>默认仅监听 127.0.0.1（本机）。若需局域网/公网访问，请设置
<code>API_HOST=0.0.0.0</code> 并自行做好防火墙与鉴权，避免被他人滥用。</p>
</body></html>"""


# ==============================================================================
# 模块自检：启动到随机端口，探活 + 单图处理后关闭
# ==============================================================================
if __name__ == "__main__":
    import numpy as np
    import cv2
    import urllib.request

    # 造一张带水印的测试图并转 base64。
    cv_img = np.full((200, 320, 3), 240, dtype=np.uint8)
    cv2.putText(cv_img, "WATERMARK", (20, 110), cv2.FONT_HERSHEY_SIMPLEX,
                1.2, (170, 170, 170), 3)
    _, buf = cv2.imencode(".png", cv_img)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")

    # 启动到随机端口。
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{port}"

    # 健康检查
    with urllib.request.urlopen(f"{base}/api/health", timeout=10) as r:
        h = json.loads(r.read())
    assert h["status"] == "ok", "健康检查失败"
    print(f"用例1通过：/api/health -> device={h['device']} lama={h['lama_available']}")

    # 单图处理
    req = json.dumps({"image": b64, "engine": "opencv", "evaluate": True}).encode("utf-8")
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

    # 批量处理
    req2 = json.dumps({"images": [b64, b64], "params": {"engine": "opencv"}}).encode("utf-8")
    req_obj2 = urllib.request.Request(
        f"{base}/api/batch", data=req2,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req_obj2, timeout=60) as r:
        resp2 = json.loads(r.read())
    assert len(resp2["results"]) == 2 and all(x["status"] == "success" for x in resp2["results"]), \
        "批量处理失败"
    print(f"用例3通过：/api/batch -> 处理 {len(resp2['results'])} 张，全部成功")

    srv.shutdown()
    print("server.py 自检通过。")
