# -*- coding: utf-8 -*-
"""Stage 130 回归守卫：services/validators 两处真缺陷（D1/D2）。

D1：`__main__` 自检用例4/5 传**不存在的** note.txt / clip.mp3 并期望 UnsupportedMediaError，
    但两函数「先查存在性、再查格式」⇒ 抛 FileNotFoundError（未被 except 捕获）⇒ 自检 exit 1。
    子进程守卫见 test_legacy_selfcheck.py 的 MODULES（已登记 services/validators.py）。
D2：validate_numpy_image docstring 曾称「非 uint8 抛 ValueError」，实现却仅 warning。
    守卫锁定「非 uint8 放行（仅告警）」这一真实契约，防 docstring 再次漂移。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest

from services.validators import validate_numpy_image, validate_pdf_file, validate_video_file
from utils.exceptions import UnsupportedMediaError


# ==============================================================================
# D1 · 存在性检查优先于格式白名单（自检崩溃的根因）
# ==============================================================================
def test_missing_file_raises_filenotfound_not_unsupported():
    """不存在的坏扩展名路径 → FileNotFoundError（存在性优先）。"""
    with pytest.raises(FileNotFoundError):
        validate_pdf_file("__not_exist__.txt")
    with pytest.raises(FileNotFoundError):
        validate_video_file("__not_exist__.mp3")


def test_existing_bad_extension_raises_unsupported(tmp_path):
    """真实存在但扩展名不支持 → UnsupportedMediaError（自检用例4/5 的正确形态）。"""
    txt = tmp_path / "note.txt"
    txt.write_bytes(b"not a pdf at all")
    mp3 = tmp_path / "clip.mp3"
    mp3.write_bytes(b"\x00\x01\x02")
    with pytest.raises(UnsupportedMediaError):
        validate_pdf_file(str(txt))
    with pytest.raises(UnsupportedMediaError):
        validate_video_file(str(mp3))


# ==============================================================================
# D2 · 非 uint8 数组放行（仅告警），不抛 ValueError
# ==============================================================================
def test_non_uint8_array_is_accepted_with_warning():
    """float32 数组应放行（返回 meta），不得抛 ValueError（docstring 已订正）。"""
    meta = validate_numpy_image(np.zeros((4, 4, 3), dtype=np.float32))
    assert meta.npixels == 16


def test_illegal_ndim_still_raises():
    """真正的形态非法（ndim 不在 {2,3}）仍抛 ValueError（回归：修复不得放宽形态校验）。"""
    with pytest.raises(ValueError):
        validate_numpy_image(np.zeros((4, 4, 3, 2), dtype=np.uint8))
    with pytest.raises(ValueError):
        validate_numpy_image("not-an-array")
