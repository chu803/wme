# -*- coding: utf-8 -*-
"""阶段 319 · 交付面提示（体积 / 视频抽帧）的判据与接线守卫。

为什么要有守卫：这两条提示的**数字来自实测**（PNG 4.65× 输入、step 5 提速 1.82×），
但提示本身的阈值与前提很容易被后人改着改着变成"永远不出现"或"每次都出现"。
故把「什么时候该提示」写成可执行的判据，并额外用 `_print_result` 做一次**接线**验证
——函数对但没被调用，等于没有（LESSONS #304）。
"""
import os
import sys
from types import SimpleNamespace

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cli  # noqa: E402


# ---------------------------------------------------------------------------
# 一、体积提示
# ---------------------------------------------------------------------------
def test_size_hint_silent_below_threshold():
    assert cli.size_hint(1_000_000, 1_200_000, "PNG") is None      # 1.2× < 1.5×
    assert cli.size_hint(1_000_000, 1_000_000, "PNG") is None


def test_size_hint_fires_and_names_a_smaller_format():
    h = cli.size_hint(1_650_169, 7_679_911, "PNG")                 # 实测 4.65×
    assert h and "4.7×" in h and "WEBP" in h.upper()
    h2 = cli.size_hint(1_000_000, 3_000_000, "JPG")
    assert h2 and "JPG" in h2.upper()


def test_size_hint_handles_missing_or_zero():
    for a, b in ((0, 10), (10, 0), (None, 10), (10, None)):
        assert cli.size_hint(a, b, "PNG") is None


# ---------------------------------------------------------------------------
# 二、视频抽帧提示
# ---------------------------------------------------------------------------
def test_frame_step_hint_only_for_default_step_with_reuse():
    assert cli.frame_step_hint(1, reuse_mask=True, max_frames=900) is not None
    assert cli.frame_step_hint(5, reuse_mask=True, max_frames=900) is None
    assert cli.frame_step_hint(1, reuse_mask=False, max_frames=900) is None, (
        "关闭掩码复用时每帧本来就要检测，提示会误导")
    assert cli.frame_step_hint(1, reuse_mask=True, max_frames=5) is None, (
        "短片段提速收益可忽略，不该打扰")


# ---------------------------------------------------------------------------
# 三、接线：函数对但没被调用等于没有
# ---------------------------------------------------------------------------
def _args(**kw):
    base = dict(fmt="PNG", evaluate=False, frame_step=1,
                no_reuse_mask=False, max_frames=900)
    base.update(kw)
    return SimpleNamespace(**base)


def test_print_result_wires_image_size_hint(tmp_path, capsys):
    src = tmp_path / "a.jpg"
    dst = tmp_path / "a.png"
    src.write_bytes(b"\x00" * 1_000_000)
    dst.write_bytes(b"\x00" * 4_000_000)
    rd = {"status": "success", "kind": "image", "original_name": "a.jpg",
          "input_path": str(src), "out_path": str(dst)}
    cli._print_result(rd, _args(), {"success": 0, "skipped": 0, "failed": 0}, [])
    out = capsys.readouterr().out
    assert "💡" in out and "体积" in out, f"体积提示没有接线：{out!r}"


def test_print_result_wires_video_step_hint(capsys):
    rd = {"status": "success", "kind": "video", "original_name": "v.mp4",
          "input_path": "v.mp4", "out_path": "v_out.mp4"}
    cli._print_result(rd, _args(), {"success": 0, "skipped": 0, "failed": 0}, [])
    out = capsys.readouterr().out
    assert "💡" in out and "frame-step" in out, f"抽帧提示没有接线：{out!r}"


def test_print_result_no_hint_for_pdf(capsys):
    """PDF 输出体积受整页重绘影响，且没有等价的 -f 替代 ⇒ 不提示。"""
    rd = {"status": "success", "kind": "pdf", "original_name": "d.pdf",
          "input_path": "d.pdf", "out_path": "d_out.pdf"}
    cli._print_result(rd, _args(), {"success": 0, "skipped": 0, "failed": 0}, [])
    assert "💡" not in capsys.readouterr().out
