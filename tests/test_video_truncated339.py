# -*- coding: utf-8 -*-
"""阶段 339 · 视频解码提前停止不许被报成「处理完成」（帧数不塌，但也不能悄悄少）。

被修掉的假陈述（`services/video_service.py` 收尾）：

    ok, frame = cap.read()
    if not ok:
        break            # 读完了（或读到坏帧），正常收尾   ← 两种情况被当成一种

`truncated` 只在「达到帧数上限」那一支被置位，所以解码中途停止时
（VBR 的 mp4/mkv 很常见，`CAP_PROP_FRAME_COUNT` 常常虚高）写出 210/900 帧也会：
`status="success"`、`truncated=False`、`warnings` 里一个字没有、进度条 100%。
用户看到的是「✅ 处理完成」，实际后 3/4 画面根本没进结果视频。

修法不是改判失败（产出的那段确实有效），而是**把少掉的部分说出来**：
`written < total` 且不是因为上限截断 ⇒ 置 `truncated` 并追加一条 warning
（`ui/tab_media.py:522` 会把 warnings 逐条显示在结果文本里）。
"""
from __future__ import annotations

import numpy as np
import pytest

import cv2

import services.video_service as vs

_W = _H = 64


class _FakeCap:
    """只交付前 `delivered` 帧，但元数据声称有 `declared` 帧 —— 模拟解码中途停止。"""

    def __init__(self, delivered: int, declared: int):
        self._i = 0
        self._n = delivered
        self._declared = declared

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self._declared)
        if prop == cv2.CAP_PROP_FPS:
            return 10.0
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return _W
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return _H
        return 0.0

    def read(self):
        if self._i >= self._n:
            return False, None
        self._i += 1
        frame = np.zeros((_H, _W, 3), dtype=np.uint8)
        frame[:, :] = (30 * self._i, 100, 200)
        return True, frame

    def release(self):
        pass


def _pass_through(monkeypatch):
    """让 process_image 原样返回（本用例量的是收尾诚实性，不是修复效果）。"""
    monkeypatch.setattr(
        vs, "process_image",
        lambda rgb, **kw: {"status": "no_watermark", "result_rgb": rgb, "mask": None,
                           "stats": None, "recommended_preset": None, "recommendation": None})


def _make_src(tmp_path) -> str:
    """`process_video` 入口先 `_os.path.isfile(src)`，所以要真的有这个文件。

    帧内容/帧数随后由 `_FakeCap` 接管，这里只负责让路径存在。
    """
    path = str(tmp_path / "src.avi")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (_W, _H))
    assert w.isOpened(), "本机 VideoWriter 不可用，测不了视频链路"
    for i in range(2):
        w.write(np.full((_H, _W, 3), 40 + i, dtype=np.uint8))
    w.release()
    return path


def _run(tmp_path, monkeypatch, delivered, declared):
    src = _make_src(tmp_path)
    _pass_through(monkeypatch)
    monkeypatch.setattr(vs.cv2, "VideoCapture",
                        lambda *a, **k: _FakeCap(delivered, declared))
    res = vs.process_video(src, str(tmp_path / f"out_{delivered}.avi"),
                           engine="opencv", sensitivity=0.5, frame_step=1,
                           reuse_mask=False, on_progress=lambda *a: None)
    return res


def test_early_decode_stop_is_reported_not_hidden(tmp_path, monkeypatch):
    """元数据 5 帧、实际只读出 2 帧 ⇒ 必须置 truncated 并把少掉的帧数说出来。"""
    res = _run(tmp_path, monkeypatch, delivered=2, declared=5)

    assert res.status == "success", res.error        # 产出的那段确实有效，不算失败
    assert res.written_frames == 2, res.summary()
    assert res.total_frames == 5, res.summary()
    assert res.truncated is True, (
        "解码中途停止却被记成「没截断」⇒ 界面会显示「✅ 处理完成」且没有任何提醒")
    assert any("2/5" in w for w in res.warnings), res.warnings


def test_full_decode_is_not_marked_truncated(tmp_path, monkeypatch):
    """反向锚：帧数齐平时不许凭空冒出「截断」告警（别把修复改成新噪声）。"""
    res = _run(tmp_path, monkeypatch, delivered=5, declared=5)

    assert res.written_frames == 5, res.summary()
    assert res.truncated is False, res.summary()
    assert not any("只写出" in w for w in res.warnings), res.warnings


def test_frame_limit_truncation_still_warns_once(tmp_path, monkeypatch):
    """原有语义不许退化：达到帧数上限时仍然只报一次截断，不重复追加。"""
    src = _make_src(tmp_path)
    _pass_through(monkeypatch)
    monkeypatch.setattr(vs.cv2, "VideoCapture", lambda *a, **k: _FakeCap(10, 10))
    res = vs.process_video(src, str(tmp_path / "out_limit.avi"),
                           engine="opencv", sensitivity=0.5, max_frames=3,
                           frame_step=1, reuse_mask=False, on_progress=lambda *a: None)
    assert res.truncated is True, res.summary()
    assert sum("只写出" in w for w in res.warnings) <= 1, (
        f"上限截断与提前结束被各报了一次，重复提醒：{res.warnings}")
