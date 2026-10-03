# -*- coding: utf-8 -*-
"""
D1 细粒度 REST 端点契约测试（/api/detect + /api/inpaint）。

不实际起长驻服务，仅把 _Handler 绑到随机端口、线程拉起、发真实 HTTP 请求，
覆盖 /api/health /api/remove /api/detect /api/inpaint /api/batch 五个端点，
确保两段式（先 detect 预览掩码 → 再 inpaint 用该掩码修复）链路真实可跑。
"""
from __future__ import annotations

import base64
import json
import threading
import urllib.request

import cv2
import numpy as np
import pytest

import server


@pytest.fixture(scope="module")
def srv():
    # 深色粗体文字水印（高对比，能在无 EasyOCR 的沙箱里稳定被检测到）。
    cv_img = np.full((240, 360, 3), 245, dtype=np.uint8)
    cv2.putText(cv_img, "WATERMARK", (15, 40), cv2.FONT_HERSHEY_SIMPLEX,
                1.4, (40, 40, 40), 4)
    _, buf = cv2.imencode(".png", cv_img)
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")

    # 合成实心掩码（覆盖水印区域），保证 inpaint 有非空掩码。
    syn_mk = np.zeros((240, 360), dtype=np.uint8)
    cv2.rectangle(syn_mk, (5, 5), (355, 100), 255, -1)
    _, mk_buf = cv2.imencode(".png", syn_mk)
    mask_b64 = base64.b64encode(mk_buf.tobytes()).decode("ascii")

    # 走生产的带闸门服务器类（与 start_server 同一路径），避免测到「不存在的旧路径」。
    httpd = server._BoundedThreadingHTTPServer(("127.0.0.1", 0), server._Handler,
                                               max_concurrency=2)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    yield base, b64, mask_b64
    httpd.shutdown()


def _post(base, path, payload):
    req = json.dumps(payload).encode("utf-8")
    obj = urllib.request.Request(
        f"{base}{path}", data=req,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(obj, timeout=60) as r:
        return json.loads(r.read())


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return json.loads(r.read())


def test_health(srv):
    base, _, _ = srv
    h = _get(base, "/api/health")
    assert h["status"] == "ok"
    assert "device" in h and "lama_available" in h


def test_remove(srv):
    base, b64, _ = srv
    resp = _post(base, "/api/remove", {"image": b64, "engine": "opencv"})
    assert resp["status"] == "success"
    assert resp["result"]


def test_detect(srv):
    base, b64, _ = srv
    resp = _post(base, "/api/detect", {"image": b64})
    assert resp["status"] == "success"
    assert resp["mask"]
    # 掩码能解回同尺寸单通道图
    mk = cv2.imdecode(np.frombuffer(base64.b64decode(resp["mask"]), np.uint8),
                      cv2.IMREAD_GRAYSCALE)
    assert mk is not None and mk.shape == (240, 360)
    assert "coverage_ratio" in resp and "regions" in resp


def test_inpaint(srv):
    base, b64, mask_b64 = srv
    resp = _post(base, "/api/inpaint",
                 {"image": b64, "mask": mask_b64, "engine": "opencv"})
    assert resp["status"] == "success"
    assert resp["result"]
    out = cv2.imdecode(np.frombuffer(base64.b64decode(resp["result"]), np.uint8),
                       cv2.IMREAD_COLOR)
    assert out is not None and out.shape == (240, 360, 3)


def test_batch(srv):
    base, b64, _ = srv
    resp = _post(base, "/api/batch",
                 {"images": [b64, b64], "params": {"engine": "opencv"}})
    assert len(resp["results"]) == 2
    assert all(x["status"] == "success" for x in resp["results"])
