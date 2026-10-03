# -*- coding: utf-8 -*-
"""
阶段 27 测试：媒体页（PDF / 视频）逐页/逐帧智能推荐。

覆盖三层：
  1. ui.tab_media._media_preset_args 路由（开启/关闭组合）；
  2. services.pdf_service.process_pdf 透传 auto_preset（真实临时 PDF + monkeypatch process_image）；
  3. services.video_service.process_video 检测帧路由 + preset_counts 聚合
     （真实临时 mp4 + monkeypatch process_image）。

不依赖 torch / OCR / 模型，跑得快且稳定。
"""
import sys
import os
import tempfile

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ------------------------------------------------------------------------------
# 1. 路由辅助（纯函数，零依赖）
# ------------------------------------------------------------------------------
def test_media_preset_args_on():
    from ui.tab_media import _media_preset_args

    assert _media_preset_args(True, "ai_tiled") == {"preset": None, "auto_preset": True}


def test_media_preset_args_off():
    from ui.tab_media import _media_preset_args

    assert _media_preset_args(False, "ai_tiled") == {"preset": "ai_tiled", "auto_preset": False}


def test_media_preset_args_off_fallback():
    from ui.tab_media import _media_preset_args
    import config

    assert _media_preset_args(False, None) == {
        "preset": config.DETECT_PRESET_DEFAULT, "auto_preset": False,
    }


# ------------------------------------------------------------------------------
# 2. PDF 服务透传（真实临时 PDF）
# ------------------------------------------------------------------------------
def _make_pdf(path: str, pages: int = 2):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((60, 100), f"content {i}", fontsize=18)
        pg.draw_rect(fitz.Rect(380, 720, 545, 770), color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
        pg.insert_text((392, 752), "WATERMARK", fontsize=16, color=(1, 1, 1))
    doc.save(path)
    doc.close()


def test_pdf_service_threads_auto_preset(monkeypatch):
    """开启推荐时：每页 process_image 收到 preset=None + auto_preset=True，
    PageResult.recommended_preset 被回填；关闭时收到共享 preset。"""
    import services.pdf_service as ps

    captured = []
    rec_name = "ai_tiled"

    def fake_process_image(frame, **kwargs):
        captured.append(dict(kwargs))
        # 第一页返回具体推荐；第二页返回默认 general（验证分布计数只统计具体预设）。
        rp = rec_name if len(captured) == 1 else None
        return {
            "status": "success",
            "result_rgb": np.zeros((4, 4, 3), dtype=np.uint8),
            "mask_pixels": 10, "regions": 1, "stats": None,
            "recommended_preset": rp, "mask": None,
        }

    monkeypatch.setattr(ps, "process_image", fake_process_image)

    tmp = tempfile.mkdtemp(prefix="test_pdf_rec_")
    pdf = os.path.join(tmp, "doc.pdf")
    _make_pdf(pdf, pages=2)

    res = ps.process_pdf(pdf, os.path.join(tmp, "out.pdf"), pages="1-2", dpi=80,
                         auto_preset=True, on_progress=lambda p, m: None)
    assert res.status == "success"
    # 两页都走了 auto_preset=True + preset=None
    assert all(c.get("auto_preset") is True for c in captured)
    assert all(c.get("preset") is None for c in captured)
    # 第一页回填了推荐预设，第二页为 None（推荐回退 general）
    assert res.pages[0].recommended_preset == rec_name
    assert res.pages[1].recommended_preset is None


def test_pdf_service_off_uses_shared_preset(monkeypatch):
    import services.pdf_service as ps

    captured = []

    def fake_process_image(frame, **kwargs):
        captured.append(dict(kwargs))
        return {
            "status": "success",
            "result_rgb": np.zeros((4, 4, 3), dtype=np.uint8),
            "mask_pixels": 0, "regions": 0, "stats": None,
            "recommended_preset": None, "mask": None,
        }

    monkeypatch.setattr(ps, "process_image", fake_process_image)

    tmp = tempfile.mkdtemp(prefix="test_pdf_off_")
    pdf = os.path.join(tmp, "doc.pdf")
    _make_pdf(pdf, pages=1)

    res = ps.process_pdf(pdf, os.path.join(tmp, "out.pdf"), pages="1", dpi=80,
                         preset="ai_corner", auto_preset=False,
                         on_progress=lambda p, m: None)
    assert res.status == "success"
    assert captured[0].get("auto_preset") is False
    assert captured[0].get("preset") == "ai_corner"
    assert res.pages[0].recommended_preset is None


# ------------------------------------------------------------------------------
# 3. 视频服务路由（真实临时 mp4）
# ------------------------------------------------------------------------------
def _make_mp4(path: str, frames: int = 3, size=(64, 48)):
    cv2 = pytest.importorskip("cv2")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    w, h = size
    vw = cv2.VideoWriter(path, fourcc, 2.0, (w, h))
    for i in range(frames):
        img = np.zeros((h, w, 3), dtype=np.uint8)
        cv2.putText(img, f"f{i}", (5, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        vw.write(img)
    vw.release()


def test_video_service_detect_frame_routing(monkeypatch):
    """开启推荐且不复用：检测帧 process_image 收到 preset=None + auto_preset=True，
    且 preset_counts 聚合到 ai_tiled；复用勾选时强制关闭推荐（preset_counts 为空）。"""
    import services.video_service as vs

    captured = []

    def fake_process_image(frame, **kwargs):
        captured.append(dict(kwargs))
        # 模拟 process_image 真实行为：仅 auto_preset=True 时才回推荐预设。
        rp = "ai_tiled" if kwargs.get("auto_preset") else None
        return {
            "status": "success",
            "result_rgb": frame.copy(),
            "mask": None, "stats": None,
            "recommended_preset": rp,
        }

    monkeypatch.setattr(vs, "process_image", fake_process_image)

    tmp = tempfile.mkdtemp(prefix="test_vid_rec_")
    mp4 = os.path.join(tmp, "v.mp4")
    _make_mp4(mp4, frames=3)

    # 场景 a：逐帧检测，开启推荐
    res = vs.process_video(mp4, os.path.join(tmp, "out.mp4"), frame_step=1, max_frames=3,
                           reuse_mask=False, auto_preset=True,
                           on_progress=lambda p, m: None)
    assert res.status == "success"
    detect_calls = [c for c in captured if c.get("mode") == "auto"]
    assert detect_calls, "应有检测帧"
    assert all(c.get("auto_preset") is True for c in detect_calls)
    assert all(c.get("preset") is None for c in detect_calls)
    assert res.preset_counts.get("ai_tiled") == len(detect_calls)

    # 场景 b：复用掩码（固定水印）→ 强制关闭推荐
    captured.clear()
    res2 = vs.process_video(mp4, os.path.join(tmp, "out2.mp4"), frame_step=1, max_frames=3,
                            reuse_mask=True, auto_preset=True,
                            on_progress=lambda p, m: None)
    assert res2.status == "success"
    detect_calls2 = [c for c in captured if c.get("mode") == "auto"]
    assert all(c.get("auto_preset") is False for c in detect_calls2)
    assert all(c.get("preset") is None or c.get("preset") == "general"
               for c in detect_calls2)
    assert res2.preset_counts == {}, "复用场景下不应有智能推荐分布"


def test_video_service_recommend_conf_avg(monkeypatch):
    """阶段 28：逐检测帧推荐时，VideoResult.recommend_conf_avg 应为推荐置信度的均值。"""
    import services.video_service as vs

    captured = []

    def fake_process_image(frame, **kwargs):
        captured.append(dict(kwargs))
        auto = bool(kwargs.get("auto_preset"))
        # 模拟真实 process_image：auto 时回推荐 + 置信度；否则不回。
        return {
            "status": "success",
            "result_rgb": frame.copy(),
            "mask": None, "stats": None,
            "recommended_preset": "ai_tiled" if auto else None,
            "recommendation": (
                {"preset": "ai_tiled", "scores": {"tiled": 0.8},
                 "triggered": ["tiled"], "confidence": 0.8}
                if auto else None
            ),
        }

    monkeypatch.setattr(vs, "process_image", fake_process_image)

    tmp = tempfile.mkdtemp(prefix="test_vid_conf_")
    mp4 = os.path.join(tmp, "v.mp4")
    _make_mp4(mp4, frames=3)

    res = vs.process_video(mp4, os.path.join(tmp, "out.mp4"), frame_step=1, max_frames=3,
                           reuse_mask=False, auto_preset=True,
                           on_progress=lambda p, m: None)
    assert res.status == "success"
    detect_n = sum(1 for c in captured if c.get("auto_preset"))
    assert detect_n > 0
    # 所有检测帧置信度都是 0.8 → 均值应等于 0.8。
    assert abs(res.recommend_conf_avg - 0.8) < 1e-6, f"均值应为 0.8，实际 {res.recommend_conf_avg}"

