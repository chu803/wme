# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 137a 守卫测试：export_service.make_compare_array 形状归一化（O1 补漏）
------------------------------------------------------------------------------
 背景（docs/136 观察项 O1 / docs/137a）：
   make_compare_array 原样 np.asarray(…, dtype=uint8)，2D 灰度直传在画布
   粘贴处 broadcast 崩溃（实证 ValueError），与同文件 make_contact_sheet
   （经 _to_rgb3）不一致。修复：_to_rgb3 补 2D→三通道分支并接入
   make_compare_array（_array_to_data_uri 一并受益）。
 注入验证：回退为 np.asarray 后本文件必须红（见 docs/137a 注入记录）。
==============================================================================
"""

from __future__ import annotations

import numpy as np

from services.export_service import _array_to_data_uri, make_compare_array, make_contact_sheet


def test_compare_array_grayscale_2d():
    g = np.full((120, 160), 77, dtype=np.uint8)
    out = make_compare_array(g, g)
    assert out.ndim == 3 and out.shape[2] == 3, f"2D 输入应产出 H×W×3，实际 {out.shape}"
    assert out.shape[0] == 120


def test_compare_array_rgba_drops_alpha():
    rgba = np.zeros((120, 160, 4), dtype=np.uint8)
    rgba[:, :, 0] = 10
    rgba[:, :, 1] = 20
    rgba[:, :, 2] = 30
    rgba[:, :, 3] = 255
    out = make_compare_array(rgba, rgba)
    assert out.ndim == 3 and out.shape[2] == 3, f"4C 输入应丢弃 alpha 产出 H×W×3，实际 {out.shape}"


def test_contact_sheet_grayscale_2d_frame():
    """_to_rgb3 补 2D 分支后，contact sheet 的灰度帧也应可拼。"""
    g = np.full((120, 160), 90, dtype=np.uint8)
    sheet = make_contact_sheet([g, g], cols=2)
    assert sheet.ndim == 3 and sheet.shape[2] == 3


def test_array_to_data_uri_grayscale():
    g = np.full((32, 32), 128, dtype=np.uint8)
    uri = _array_to_data_uri(g)
    assert uri.startswith("data:image/jpeg;base64,")
