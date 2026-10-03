# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 136 守卫测试：services/video_export 自检临时目录清理与导出边界
------------------------------------------------------------------------------
 背景（docs/136）：
   D1  __main__ 自检 mkdtemp 从不清理 ⇒ 每跑一次泄漏 1 目录 4 文件
       （2 视频 + 2 PNG）—— 守卫：子进程实跑后 %TEMP% 不得新增
       video_export_selftest_* 目录。
   D2  _extract_one 灰度 2D 帧崩溃 —— 探针证伪（OpenCV 5.0 的
       cvtColor(2D, BGR2RGB) 自动复制为 3 通道），本文件以
       「灰值内容视频抽帧仍得 H×W×3 且三通道相等」固化该真值。
   边界固化：idx 越界钳到末帧；缺文件/缺视频返回 None；
   src 与 out 尺寸不一致时对比图按源尺寸对齐；cols=0 走下游钳制不崩。
 注入验证：BUGGY（移除 finally 清理）下 test_selfcheck_cleans_tempdir 必须红。
==============================================================================
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys

import cv2
import numpy as np
import pytest

from services.video_export import _extract_one, export_contact_sheet, export_frame_compare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_video(path: str, size=(160, 120), n: int = 6, gray_value_start: int = 40) -> None:
    w, h = size
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (w, h))
    assert vw.isOpened(), f"VideoWriter 打不开（{path}）"
    for i in range(n):
        vw.write(np.full((h, w, 3), gray_value_start + i * 8, dtype=np.uint8))
    vw.release()


# ------------------------------------------------------------------------------
# _extract_one：正常 / 越界钳制 / 缺文件
# ------------------------------------------------------------------------------
def test_extract_one_normal_and_index_clamp(tmp_path):
    vid = str(tmp_path / "v.mp4")
    _write_video(vid, n=6)
    f0 = _extract_one(vid, 0)
    assert f0 is not None and f0.shape == (120, 160, 3)
    f99 = _extract_one(vid, 99)  # 越界应钳到末帧，不返回 None
    assert f99 is not None and f99.shape == (120, 160, 3)


def test_extract_one_gray_content_still_rgb3(tmp_path):
    """灰值内容视频：抽帧必须得到 H×W×3（D2 真值固化）。

    注：mp4 有损编码会把纯灰帧压出通道间噪声，逐位相等断言不成立；
    D2 的契约是「形状合规、不崩、不静默失败」，故只断言形状。
    """
    vid = str(tmp_path / "g.mp4")
    _write_video(vid, n=3, gray_value_start=77)
    f = _extract_one(vid, 1)
    assert f is not None and f.ndim == 3 and f.shape[2] == 3, f"灰值视频抽帧形状异常: {f.shape}"
    assert f.dtype == np.uint8


def test_extract_one_missing_file_returns_none(tmp_path):
    assert _extract_one(str(tmp_path / "不存在.mp4"), 0) is None
    assert _extract_one("", 0) is None


# ------------------------------------------------------------------------------
# export_frame_compare：尺寸不一致对齐 / 源缺失
# ------------------------------------------------------------------------------
def test_export_frame_compare_size_mismatch_aligns_to_src(tmp_path):
    src = str(tmp_path / "src.mp4")
    out = str(tmp_path / "out_small.mp4")
    _write_video(src, size=(160, 120), n=6)
    _write_video(out, size=(80, 60), n=6)  # 模拟被缩小的"结果"视频
    saved = export_frame_compare(src, out, 0, out_dir=str(tmp_path))
    assert saved and os.path.isfile(saved), "尺寸不一致路径导出失败"
    # 中文路径吃不了 cv2.imread（项目经典坑），用项目标准 load_image
    from core.image_utils import load_image

    img = load_image(saved)
    assert img.shape[0] == 120, f"对比图高度应对齐源(120)，实际 {img.shape}"


def test_export_frame_compare_missing_source_returns_none(tmp_path):
    vid = str(tmp_path / "v.mp4")
    _write_video(vid, n=3)
    assert export_frame_compare(str(tmp_path / "缺.mp4"), vid, 0, out_dir=str(tmp_path)) is None
    assert export_frame_compare("", "", 0, out_dir=str(tmp_path)) is None


# ------------------------------------------------------------------------------
# export_contact_sheet：cols 非法值走下游钳制（服务边界不崩）
# ------------------------------------------------------------------------------
def test_export_contact_sheet_invalid_cols_still_exports(tmp_path):
    vid = str(tmp_path / "v.mp4")
    _write_video(vid, n=4)
    saved = export_contact_sheet(vid, n=4, cols=0, out_dir=str(tmp_path))
    assert saved and os.path.isfile(saved), "cols=0 应被下游钳制而不是导出失败"


# ------------------------------------------------------------------------------
# D1 核心守卫：模块自检子进程实跑后不得新增 selftest 临时目录
# ------------------------------------------------------------------------------
def test_selfcheck_cleans_tempdir():
    import tempfile

    temp_root = tempfile.gettempdir()
    pat = os.path.join(temp_root, "video_export_selftest_*")
    before = set(glob.glob(pat))
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "services", "video_export.py")],
        capture_output=True, text=True, cwd=ROOT, timeout=180,
    )
    assert r.returncode == 0, f"自检失败：{(r.stderr or '')[-400:]}"
    after = set(glob.glob(pat))
    leaked = after - before
    assert not leaked, f"自检泄漏临时目录（阶段 136 守卫）：{leaked}"


# ------------------------------------------------------------------------------
# 自检产物隔离性：自检自己用 mkdtemp，不会写项目运行时目录（抽查 outputs）
# ------------------------------------------------------------------------------
def test_selfcheck_does_not_touch_project_outputs(monkeypatch):
    outputs = os.path.join(ROOT, "outputs")
    before = set(os.listdir(outputs)) if os.path.isdir(outputs) else set()
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "services", "video_export.py")],
        capture_output=True, text=True, cwd=ROOT, timeout=180,
    )
    assert r.returncode == 0
    after = set(os.listdir(outputs)) if os.path.isdir(outputs) else set()
    assert after == before, f"自检污染了项目 outputs 目录：{sorted(after - before)}"
