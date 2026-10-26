# -*- coding: utf-8 -*-
"""阶段 338 · PDF / 视频入口必须把 **RGB** 交给核心管线（通道顺序是跨入口契约）。

实测（本机 `ui/assets/sample_watermarked.png`，同一张图、同一预设、同一灵敏度）：

    RGB（单图/自动/批量页实际喂的）  mask_pixels=4292   regions=6  coverage=1.05%
    BGR（PDF 与视频以前喂的）        mask_pixels=10328  regions=7  coverage=2.52%

⇒ 「同一套核心逻辑」在三个入口之间其实不是同一件事：`core/detector` 按
`cv2.cvtColor(rgb, COLOR_RGB2GRAY)` 取灰（权重 0.299R/0.587G/0.114B），喂 BGR 就等于
把 R 与 B 的权重调换；颜色类先验（白底板、角标模板 NCC）也跟着错。
用户表现：同样的水印，单图页擦得干净，PDF/视频页残留或擦过头。

为什么以前「颜色看着是对的」：两处错互相抵消——喂进去是 BGR（检测错），
编码/写出时又按 BGR 解释（`cv2.imencode` / `VideoWriter` 的约定），颜色碰巧回正。
所以这个缺陷只能靠**通道顺序断言**抓住，肉眼看不出来。
"""
from __future__ import annotations

import os

import cv2
import numpy as np
import pytest

import services.pdf_service as ps
import services.video_service as vs

fitz = pytest.importorskip("fitz")


def _red_page_pdf(tmp_path) -> str:
    """造一份一页 PDF：白底 + 纯红矩形（RGB 世界里 R=255,G=0,B=0）。"""
    path = str(tmp_path / "red.pdf")
    doc = fitz.open()
    page = doc.new_page(width=100, height=100)
    page.draw_rect(fitz.Rect(20, 20, 60, 60), color=(1, 0, 0), fill=(1, 0, 0))
    doc.save(path)
    doc.close()
    return path


def test_page_to_array_returns_rgb_not_bgr(tmp_path):
    """`_page_to_array` 必须交 RGB：红块应当是「R 高 B 低」。"""
    doc = fitz.open(_red_page_pdf(tmp_path))
    try:
        arr = ps._page_to_array(doc.load_page(0), 96)
    finally:
        doc.close()

    r, g, b = (int(x) for x in arr[40, 40])          # 红块中心
    assert r > 180 and b < 60, (
        f"红块中心读到的通道顺序不对（应为 RGB）：R={r} G={g} B={b}；"
        "BGR 会让 PDF/视频页用反的灰度权重做检测")


def test_encode_page_image_flips_back_once(tmp_path):
    """编码侧翻回 BGR 必须**恰好一次**：解码回来应是 OpenCV 的 BGR 排布。"""
    doc = fitz.open(_red_page_pdf(tmp_path))
    try:
        rgb = ps._page_to_array(doc.load_page(0), 96)
    finally:
        doc.close()

    data, _ext = ps._encode_page_image(rgb)
    back = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert back is not None
    b, g, r = (int(x) for x in back[40, 40])
    assert r > 180 and b < 60, f"编码往返后颜色错位：B={b} G={g} R={r}"


def _red_video(tmp_path) -> str:
    """造一段 3 帧纯红的小视频（VideoWriter 按 BGR 解释，所以写 (0,0,255)）。"""
    path = str(tmp_path / "red.avi")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (64, 64))
    assert w.isOpened(), "本机无法生成测试视频（VideoWriter 未就绪）"
    for _ in range(3):
        w.write(np.zeros((64, 64, 3), dtype=np.uint8) + np.array([0, 0, 255], np.uint8))
    w.release()
    return path


def test_process_video_feeds_rgb_to_pipeline(tmp_path, monkeypatch):
    """逐帧循环必须把 RGB 交给 `process_image`，并把结果正确写回 BGR 容器。"""
    captured = {}

    def fake_process_image(rgb, **kw):
        captured["arr"] = np.asarray(rgb).copy()
        # 假装「没检出水印」：帧应原样输出，不触发修复分支。
        return {"status": "no_watermark", "result_rgb": rgb, "mask": None,
                "stats": None, "recommended_preset": None, "recommendation": None}

    monkeypatch.setattr(vs, "process_image", fake_process_image)
    res = vs.process_video(_red_video(tmp_path), str(tmp_path / "out.avi"),
                           engine="opencv", sensitivity=0.5,
                           frame_step=1, reuse_mask=False,
                           on_progress=lambda *a: None)
    got = captured.get("arr")
    assert got is not None, "process_image 没被调用，测试没量到东西"
    r, g, b = (int(x) for x in got[32, 32])
    assert r > 180 and b < 60, (
        f"喂给核心管线的数组是 BGR（R={r} G={g} B={b}）⇒ 检测侧通道权重反了")
    assert res.written_frames == 3, res

    # 读回输出帧用 `VideoCapture`（与输入同样的容器 API）。
    # `cv2.imread` 对 MJPG-AVI 的第一帧不稳定（实测返回 None），别拿它当判据。
    cap = cv2.VideoCapture(str(res.out_path))
    try:
        ok_read, out = cap.read()
    finally:
        cap.release()
    assert ok_read and out is not None, f"输出帧读不回来：{res.out_path}"
    ob, og, orr = (int(x) for x in out[32, 32])
    assert orr > 180 and ob < 60, f"写出侧颜色错位：B={ob} G={og} R={orr}"
