# -*- coding: utf-8 -*-
"""阶段 349 · 样本采集包的**成对性判据**必须有机器判定（否则「不可用」的样本会被当真值用）。

背景：`docs/P0` §六 要求真实平铺/点阵水印样本必须**成对**——同一张文件的两种导出、
像素逐位对齐、只多一层水印叠印。历史上两次接入的成对图都被判**不可用**，
原因正是**非像素配对**（两次生成的画面本身不同 ⇒ 减出来的「真值」里全是画面差异）。

本文件锁住 `scripts/grid_sample_check.py` 的判据逻辑：
尺寸不一致 ⇒ 不可用；两张完全相同 ⇒ 不可用；差异像素占比过大 ⇒ 不可用（疑似两张不同画面）。
"""
from __future__ import annotations

import numpy as np
import pytest

from scripts import grid_sample_check as gsc


def _write_rgb(path, arr):
    import cv2
    ok, buf = cv2.imencode(".png", cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    assert ok
    path.write_bytes(buf.tobytes())


def _pair(tmp_path, name, wm, clean, style="dir"):
    base = tmp_path / name
    if style == "dir":
        base.mkdir()
        wm_p, cl_p = base / "watermarked.png", base / "clean.png"
    else:
        wm_p, cl_p = tmp_path / f"{name}_watermarked.png", tmp_path / f"{name}_clean.png"
    _write_rgb(wm_p, wm)
    _write_rgb(cl_p, clean)
    return wm_p, cl_p


def _img(size=64, mark=False):
    a = np.full((size, size, 3), 200, dtype=np.uint8)
    if mark:
        a[10:20, 10:20] = 30          # 一小块「水印」差异
    return a


def test_find_pairs_supports_both_layouts(tmp_path):
    wm1, cl1 = _pair(tmp_path, "a", _img(mark=True), _img())
    _pair(tmp_path, "b", _img(mark=True), _img(), style="flat")
    pairs = gsc.find_pairs(tmp_path)
    names = sorted(p[0] for p in pairs)
    assert names == ["a", "b"], names
    assert (wm1.name, cl1.name) in {(p[1].name, p[2].name) for p in pairs}


def test_misaligned_pair_is_rejected(tmp_path):
    """尺寸不一致 ⇒ 不可用（这正是历史上被判「不可用」的那类样本）。"""
    wm_p, cl_p = _pair(tmp_path, "bad", _img(64, mark=True), _img(80))
    r = gsc.check_pair("bad", wm_p, cl_p)
    assert r["ok"] is False
    assert any("尺寸不一致" in x for x in r["reasons"]), r["reasons"]


def test_identical_pair_is_rejected(tmp_path):
    """两张完全相同 ⇒ 没有水印叠印，不是成对样本。"""
    wm_p, cl_p = _pair(tmp_path, "same", _img(), _img())
    r = gsc.check_pair("same", wm_p, cl_p)
    assert r["ok"] is False
    assert any("完全相同" in x for x in r["reasons"]), r["reasons"]


def test_too_different_pair_is_rejected(tmp_path):
    """差异像素占比过大 ⇒ 疑似两张不同画面（不是同一张的两种导出）。"""
    wm_p, cl_p = _pair(tmp_path, "diff", _img(mark=False), _img(mark=False))
    # 造一张「整体不同」的图：把一半像素改掉
    wm = _img()
    wm[:32] = 20
    _write_rgb(wm_p, wm)
    r = gsc.check_pair("diff", wm_p, cl_p)
    assert r["ok"] is False
    assert any("疑似两张不同画面" in x for x in r["reasons"]), r["reasons"]


def test_valid_pair_is_accepted(tmp_path):
    """正常成对样本必须被判可用，且真值像素数可算出来。

    画布用 1024² —— 脚本有一条「长边 ≥1024」的硬性要求（要原图导出、不裁剪），
    小图会被判不可用（这是**正确**行为，下面几条负例同时也会撞到它）。
    """
    wm_p, cl_p = _pair(tmp_path, "ok", _img(1024, mark=True), _img(1024))
    r = gsc.check_pair("ok", wm_p, cl_p)
    assert r["ok"] is True, r["reasons"]
    assert r["truth_pixels"] == 10 * 10, r["truth_pixels"]


def test_empty_dir_returns_two(tmp_path):
    """空目录 ⇒ 退出码 2（跳过），不能是 0（假绿）。"""
    assert gsc.main(["--pairs", str(tmp_path)]) == 2
