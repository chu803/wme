# -*- coding: utf-8 -*-
"""
阶段 113 审计守卫：services/video_service.extract_sampled_frames 的返回契约。

审计发现（F1）：原函数 `return_indices=False` 分支是空操作——它把每条
{"idx", "image"} 原样重建一遍，导致 `return_indices` 这个参数「看起来能控制
是否返回帧序号、实际永远返回」，与文档承诺的契约不符（死代码 + 误导性 API）。
修复后：return_indices=False 必须真正剥离 "idx"，只返回 {"image"}。

本文件把该契约钉死，防止日后被误改回空操作或再次悄悄失效。
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


def _make_video(path: str, n: int = 12, w: int = 64, h: int = 48) -> None:
    """合成一段 n 帧的小视频，右下角带白色水印。"""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, 10.0, (w, h))
    try:
        for i in range(n):
            canvas = np.full((h, w, 3), 30 + i * 6, dtype=np.uint8)
            cv2.putText(canvas, "MARK", (w - 30, h - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            writer.write(canvas)
    finally:
        writer.release()


def test_esf_return_indices_true_keeps_idx(tmp_path):
    """默认/True 分支：每项必须同时含 'idx' 与 'image'。"""
    p = str(tmp_path / "src.mp4")
    _make_video(p, n=12)
    frames = vs.extract_sampled_frames(p, n=4, return_indices=True)
    assert len(frames) >= 1
    for f in frames:
        assert set(f.keys()) == {"idx", "image"}
        assert isinstance(f["idx"], int)
        assert isinstance(f["image"], np.ndarray)
        assert f["image"].ndim == 3 and f["image"].shape[2] == 3


def test_esf_return_indices_false_strips_idx(tmp_path):
    """False 分支（审计前为空操作）：每项必须仅含 'image'、不得含 'idx'。"""
    p = str(tmp_path / "src.mp4")
    _make_video(p, n=12)
    frames = vs.extract_sampled_frames(p, n=4, return_indices=False)
    assert len(frames) >= 1
    for f in frames:
        assert set(f.keys()) == {"image"}
        assert "idx" not in f
        assert isinstance(f["image"], np.ndarray)
        assert f["image"].ndim == 3 and f["image"].shape[2] == 3


def test_esf_default_keeps_idx(tmp_path):
    """默认参数即 return_indices=True，契约与显式 True 一致。"""
    p = str(tmp_path / "src.mp4")
    _make_video(p, n=12)
    frames = vs.extract_sampled_frames(p, n=4)
    assert len(frames) >= 1
    assert all(set(f.keys()) == {"idx", "image"} for f in frames)


def test_esf_missing_file_returns_empty():
    """文件缺失安全返回空列表（与 return_indices 取值无关）。"""
    assert vs.extract_sampled_frames("__not_exist__.mp4", n=4) == []
    assert vs.extract_sampled_frames("__not_exist__.mp4", n=4, return_indices=False) == []
