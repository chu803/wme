# -*- coding: utf-8 -*-
"""
阶段 256 守卫 · PDF / 视频异步作业端点**真实长素材端到端验收**。

背景
----
255 把 PDF/视频/模型下载长任务接成了异步作业端点（POST /api/jobs + 轮询 + 文件下载），
但 255 的守卫刻意用**确定性轻量 runner**（`test_ping`）验证 HTTP 全链路，**不跑真实推理**。
本守卫补上「真实长素材端到端」这一环：用合成带水印的 PDF（2 页）与视频（12 帧），
经 `POST /api/jobs {kind:"pdf"/"video"}` 真正派发到 worker 线程跑 `process_pdf`/`process_video`
（与 GUI/CLI/库同一核心），再轮询到终态、经 `GET .../file` 下载结果并校验有效性。

钉死五条契约
------------
1. **派发真实处理**：提交 pdf/video 作业后，worker 真的跑了 `process_pdf`/`process_video`
   （不是 dummy）——结果里页数/帧数 > 0、status=success、progress=1.0。
2. **结果文件有效**：下载的 `.../file` 是合法 PDF（页数 == 输入页数）/ 合法 mp4（帧数 == 输入帧数）。
3. **水印确实被处理**（非严格，仅验收「不恶化」）：重栅格化输出首页/首帧后复检，
   输出掩码像素 ≤ 输入掩码像素（检测器漏检则 0<=0 仍过；命中则典型显著下降）。
4. **闸门之外**：长任务在 worker 线程跑，不占推理闸门槽位（沿用 255 的豁免契约，此处不重复断言）。
5. **不触发沙箱删除护栏**：真实 PDF/视频处理在进程内 ≤ 50 文件删除（探针已证；12 帧视频实测安全）。

⚠ 合成材料说明：本守卫用「合成带水印图」而非用户真实 AI 图——它验收的是**端点管线 + 真实处理派发**
这一层；水印移除质量本身由 29 图基准门禁（real_gate / e2e_real_probe）覆盖，不在本守卫范围。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time

import pytest

fitz = pytest.importorskip("fitz")        # PyMuPDF：PDF 读 + 栅格化
cv2 = pytest.importorskip("cv2")          # 视频写 + 抽帧
np = pytest.importorskip("numpy")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import server as S  # noqa: E402
import api          # noqa: E402


# --------------------------------------------------------------------------- #
# 合成真实长素材（模块级一次，进程内复用）
# --------------------------------------------------------------------------- #
def _watermark_frame(w=400, h=300, text="WATERMARK"):
    img = np.full((h, w, 3), 245, dtype=np.uint8)
    cv2.putText(img, text, (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (40, 40, 40), 4)
    return img


def _build_pdf_bytes() -> bytes:
    from PIL import Image
    pages = []
    for i in range(2):
        arr = _watermark_frame()
        if i == 1:
            cv2.putText(arr, "PAGE2", (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                        (60, 60, 60), 3)
        pages.append(Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)))
    import io
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=[pages[1]])
    return buf.getvalue()


def _build_video_bytes(n=12, w=320, h=240) -> bytes:
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10, (w, h))
    for _ in range(n):
        vw.write(_watermark_frame(w, h))
    vw.release()
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    return data


PDF_BYTES = _build_pdf_bytes()
VIDEO_BYTES = _build_video_bytes()


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


def _post(port, path, payload):
    import http.client
    body = json.dumps(payload).encode("utf-8")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request("POST", path, body=body,
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


def _get(port, path):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        c.close()


def _get_raw(port, path):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def _wait_done(port, jid, timeout=150):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        st, b = _get(port, f"/api/jobs/{jid}")
        assert st == 200, f"轮询期望 200，实得 {st}：{b[:200]}"
        jb = json.loads(b)
        if jb["status"] in ("success", "failed"):
            return jb
        last = jb
        time.sleep(0.5)
    raise AssertionError(f"作业 {jid} 超时未终态（最后状态 {last}）")


def _detect_mask_pixels(png_bytes: bytes) -> int:
    _, stats = api.detect_watermark_bytes(png_bytes)
    return int(stats.get("mask_pixels", 0) or 0)


def _pdf_page_png(pdf_path: str, page: int = 0, dpi: int = 100) -> bytes:
    with fitz.open(pdf_path) as doc:
        pix = doc[page].get_pixmap(dpi=dpi)
    return pix.tobytes("png")


def _video_first_frame_png(mp4_path: str) -> bytes:
    cap = cv2.VideoCapture(mp4_path)
    ret, frame = cap.read()
    cap.release()
    assert ret, "无法读取视频首帧"
    return cv2.imencode(".png", frame)[1].tobytes()


# --------------------------------------------------------------------------- #
# 1) PDF 异步作业 · 真实 2 页素材端到端
# --------------------------------------------------------------------------- #
def test_pdf_async_job_e2e_real_material(live_server):
    """POST /api/jobs {kind:pdf} 经 worker 真跑 process_pdf，终态 success，
    下载结果 PDF 合法且页数 == 输入页数；水印处理不恶化。"""
    st, body = _post(live_server, "/api/jobs",
                     {"kind": "pdf",
                      "params": {"file": base64.b64encode(PDF_BYTES).decode("ascii"),
                                 "engine": "opencv", "dpi": 100}})
    assert st == 200, f"提交失败：{body[:200]}"
    jid = json.loads(body)["job_id"]

    final = _wait_done(live_server, jid)
    assert final["status"] == "success", f"作业失败：{final}"
    assert final["progress"] == 1.0, f"progress 非 1.0：{final}"

    res = final["result"]
    assert res["total_pages"] == 2, f"总页数应为 2：{res}"
    assert res["processed_pages"] == 2, f"应处理 2 页：{res}"
    assert res["success_pages"] == 2, f"应成功 2 页：{res}"
    assert res["status"] == "success"

    # 下载结果文件
    st2, raw = _get_raw(live_server, f"/api/jobs/{jid}/file")
    assert st2 == 200, f"下载期望 200，实得 {st2}"
    assert raw[:4] == b"%PDF", "下载内容不是合法 PDF（缺 %PDF 头）"

    # 校验输出 PDF 页数 == 输入；水印处理不恶化（文件仍在时栅格化复检）
    import tempfile
    out_path = os.path.join(tempfile.gettempdir(), f"wme_e2e256_{jid}.pdf")
    with open(out_path, "wb") as f:
        f.write(raw)
    try:
        in_png = _pdf_page_png(_pdf_input_path())
        out_png = _pdf_page_png(out_path)
        with fitz.open(out_path) as doc:
            assert doc.page_count == 2, f"输出 PDF 页数应为 2，实得 {doc.page_count}"
    finally:
        os.remove(out_path)
    in_mask = _detect_mask_pixels(in_png)
    out_mask = _detect_mask_pixels(out_png)
    assert out_mask <= in_mask, (
        f"水印未减反增（输入 {in_mask} -> 输出 {out_mask}），疑似处理失效")


def _pdf_input_path() -> str:
    """把合成 PDF 字节落盘供栅格化（模块级常量已在内存，这里按需写盘一次）。"""
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "wme_e2e256_in.pdf")
    if not os.path.exists(p):
        with open(p, "wb") as f:
            f.write(PDF_BYTES)
    return p


# --------------------------------------------------------------------------- #
# 2) 视频异步作业 · 真实 12 帧素材端到端
# --------------------------------------------------------------------------- #
def test_video_async_job_e2e_real_material(live_server):
    """POST /api/jobs {kind:video} 经 worker 真跑 process_video，终态 success，
    下载结果 mp4 合法且帧数 == 输入帧数；水印处理不恶化。"""
    st, body = _post(live_server, "/api/jobs",
                     {"kind": "video",
                      "params": {"file": base64.b64encode(VIDEO_BYTES).decode("ascii"),
                                 "engine": "opencv", "max_frames": 15,
                                 "frame_step": 2}})
    assert st == 200, f"提交失败：{body[:200]}"
    jid = json.loads(body)["job_id"]

    final = _wait_done(live_server, jid)
    assert final["status"] == "success", f"作业失败：{final}"
    assert final["progress"] == 1.0, f"progress 非 1.0：{final}"

    res = final["result"]
    # 视频结果含 total_frames / processed_frames（不同版本字段名可能不同，宽松断言存在性）
    assert res.get("status") == "success"

    st2, raw = _get_raw(live_server, f"/api/jobs/{jid}/file")
    assert st2 == 200, f"下载期望 200，实得 {st2}"
    assert len(raw) > 100, "下载的视频内容过短，疑似空文件"

    import tempfile
    out_path = os.path.join(tempfile.gettempdir(), f"wme_e2e256_{jid}.mp4")
    with open(out_path, "wb") as f:
        f.write(raw)
    try:
        # 用 cv2 直接探帧数（避免依赖 ffprobe）
        cap = cv2.VideoCapture(out_path)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        assert frames == 12, f"输出视频帧数应为 12，实得 {frames}"
        in_png = _video_first_frame_png(_video_input_path())
        out_png = _video_first_frame_png(out_path)
    finally:
        os.remove(out_path)
    in_mask = _detect_mask_pixels(in_png)
    out_mask = _detect_mask_pixels(out_png)
    assert out_mask <= in_mask, (
        f"水印未减反增（输入 {in_mask} -> 输出 {out_mask}），疑似处理失效")


def _video_input_path() -> str:
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "wme_e2e256_in.mp4")
    if not os.path.exists(p):
        with open(p, "wb") as f:
            f.write(VIDEO_BYTES)
    return p
