# -*- coding: utf-8 -*-
"""
阶段 37 测试：视频管理（结果浏览 / 帧对比 / 导出 / 历史视频记录）。

覆盖纯逻辑与轻量集成：
  · extract_sampled_frames（均匀抽样、含首末帧）
  · make_contact_sheet（网格拼图、空列表兜底）
  · export_frame_compare / export_contact_sheet（合成视频导出 PNG）
  · HistoryRecord 视频字段序列化往返
  · tab_media._store_video_record 调用缓存登记（monkeypatch，不落盘）
  · tab_history.on_select 对视频记录返回视频预览路径
全部使用临时目录，不污染项目缓存。
"""

import os as _os
import sys as _sys

import numpy as np
import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import cv2  # noqa: E402
import config  # noqa: E402
from services import video_service as vs  # noqa: E402
from services.export_service import make_contact_sheet  # noqa: E402
from services.video_export import export_frame_compare, export_contact_sheet  # noqa: E402
from services.cache_service import HistoryRecord  # noqa: E402


def _make_video(path: str, n: int, w=64, h=48, with_mark=True):
    """合成一段 n 帧的小视频，右下角带白色水印（可选）。"""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, 10.0, (w, h))
    try:
        for i in range(n):
            canvas = np.full((h, w, 3), 30 + i * 6, dtype=np.uint8)
            if with_mark:
                cv2.putText(canvas, "MARK", (w - 30, h - 6),
                             cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            writer.write(canvas)
    finally:
        writer.release()


def test_extract_sampled_frames_even(tmp_path):
    p = str(tmp_path / "src.mp4")
    _make_video(p, n=12)
    frames = vs.extract_sampled_frames(p, n=config.VIDEO_COMPARE_FRAMES, return_indices=True)
    assert len(frames) >= 1
    idxs = [f["idx"] for f in frames]
    # 必须含首帧与末帧。
    assert 0 in idxs
    assert (12 - 1) in idxs
    for f in frames:
        assert isinstance(f["image"], np.ndarray)
        assert f["image"].ndim == 3 and f["image"].shape[2] == 3
    # 序号应单调递增。
    assert idxs == sorted(idxs)


def test_extract_sampled_frames_missing_file():
    frames = vs.extract_sampled_frames("__not_exist__.mp4", n=4)
    assert frames == []


def test_make_contact_sheet_grid():
    arr = np.zeros((20, 20, 3), dtype=np.uint8) + 200
    sheet = make_contact_sheet([arr, arr, arr, arr], cols=2, thumb_size=20)
    assert isinstance(sheet, np.ndarray)
    assert sheet.ndim == 3
    # 2 列 2 行 → 高度约 40，宽度约 40。
    assert sheet.shape[0] >= 40 and sheet.shape[1] >= 40


def test_make_contact_sheet_empty():
    sheet = make_contact_sheet([], cols=4)
    assert isinstance(sheet, np.ndarray)
    assert sheet.shape[2] == 3


def test_video_export_frame_and_sheet(tmp_path):
    src = str(tmp_path / "src.mp4")
    out = str(tmp_path / "out.mp4")
    _make_video(src, n=10)
    _make_video(out, n=10, with_mark=False)  # 结果视频无水印

    cmp_png = export_frame_compare(src, out, idx=2, out_dir=str(tmp_path))
    assert cmp_png and _os.path.isfile(cmp_png)
    assert cmp_png.endswith(".png")

    sheet_png = export_contact_sheet(out, n=4, cols=2, out_dir=str(tmp_path))
    assert sheet_png and _os.path.isfile(sheet_png)
    assert sheet_png.endswith(".png")


def test_video_export_missing_video():
    assert export_frame_compare("a.mp4", "b.mp4", 0) is None
    assert export_contact_sheet("__not_exist__.mp4") is None


def test_history_record_video_roundtrip():
    rec = HistoryRecord(
        id="video_1", ts=123.0, original_name="v.mp4",
        kind="video", video_path="/x/out.mp4", result_path="/x/out.mp4",
        duration=3.5, fps=30.0, frames=105, width=1920, height=1080, upscaled=True,
    )
    d = rec.to_dict()
    assert d["kind"] == "video"
    assert d["video_path"] == "/x/out.mp4"
    assert d["width"] == 1920 and d["upscaled"] is True
    rec2 = HistoryRecord.from_dict(d)
    assert rec2.kind == "video"
    assert rec2.duration == 3.5 and rec2.fps == 30.0
    assert rec2.frames == 105 and rec2.upscaled is True


def test_tab_media_stores_video_record(monkeypatch):
    import ui.tab_media as tm

    captured = {}

    class _FakeRes:
        out_path = "/tmp/fake_out.mp4"
        total_frames = 90
        fps = 30.0
        written_frames = 90
        width = 640
        height = 480
        upscaled = False

        def summary(self):
            return "test summary"

    def fake_add(src, out, **kw):
        captured["src"] = src
        captured["out"] = out
        captured["kw"] = kw
        return None

    monkeypatch.setattr(tm._CACHE, "add_video_record", fake_add)
    tm._store_video_record(_FakeRes(), "/tmp/src.mp4")
    assert captured["src"] == "/tmp/src.mp4"
    assert captured["out"] == "/tmp/fake_out.mp4"
    assert captured["kw"]["frames"] == 90
    assert captured["kw"]["width"] == 640
    assert captured["kw"]["upscaled"] is False


def test_tab_history_on_select_video_preview(monkeypatch):
    import ui.tab_history as th

    rec = HistoryRecord(
        id="v1", ts=1.0, original_name="v.mp4", kind="video",
        video_path="/tmp/v.mp4", result_path="/tmp/v.mp4",
    )
    monkeypatch.setattr(th, "_HISTORY_ID_MAP", [(rec.id, rec.result_path)])
    monkeypatch.setattr(th._CACHE, "get", lambda rid: rec)

    class _Evt:
        index = 0

    # 阶段 38 起 on_select 返回六元组（后三项为对比查看器：配对 / HTML / 摘要）。
    detail, dl, video, pair, html, summary = th.on_select(_Evt())
    assert video == "/tmp/v.mp4"
    assert "🎬" in detail or "Video" in detail
    # 视频记录不进入图片对比查看器，配对为空且给出引导文案。
    assert pair is None and html == "" and summary
    # 图片记录第三项为 None。
    img_rec = HistoryRecord(id="i1", ts=1.0, original_name="a.png", kind="image",
                             result_path="/tmp/a.png")
    monkeypatch.setattr(th, "_HISTORY_ID_MAP", [(img_rec.id, img_rec.result_path)])
    monkeypatch.setattr(th._CACHE, "get", lambda rid: img_rec)
    video2 = th.on_select(_Evt())[2]
    assert video2 is None
