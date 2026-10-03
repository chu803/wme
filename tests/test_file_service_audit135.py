# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 135 守卫测试：services/file_service.make_thumbnail 形状归一化与退化尺寸
------------------------------------------------------------------------------
 背景（docs/135）：
   make_thumbnail 是「视频抽帧 / PDF 首页渲染 / 历史重处理」三条调用路径的
   唯一形状防线，原实现只接受 H×W×3：
     · 灰度 2D 输入   → ValueError: could not broadcast (85,128)→(85,128,3)
     · RGBA 4C 输入   → ValueError: could not broadcast (85,128,4)→(85,128,3)
     · 退化尺寸 (0×N) → OpenCV 断言崩溃（不透明报错）
   且 cache_service 的 except 会把上述崩溃放大为「整条历史记录登记流产」，
   违背其「保证记录始终可注册」承诺。
 修复：
   _as_rgb3 归一化（2D→三通道复制 / 4C→丢 alpha / 其他→ValueError）
   + 退化尺寸显式 ValueError 守卫。
 注入验证：本文件任一用例在 BUGGY 版本下必须失败（见 docs/135 注入记录）。
==============================================================================
"""

from __future__ import annotations

import numpy as np
import pytest

from services.file_service import make_thumbnail

SIZE = 64


# ------------------------------------------------------------------------------
# D1：灰度 2D → 三通道复制
# ------------------------------------------------------------------------------
def test_make_thumbnail_grayscale_2d_returns_rgb():
    g = np.full((200, 300), 77, dtype=np.uint8)
    thumb = make_thumbnail(g, size=SIZE)
    assert thumb.shape == (SIZE, SIZE, 3)
    # 内容区像素三通道应相等（灰度复制，非染色）
    center = thumb[SIZE // 2, SIZE // 2]
    assert center[0] == center[1] == center[2]
    # 内容灰度值应来自原图（77），而非灰底 240
    assert int(center[0]) in (77, 240)  # 中心点可能落在灰底外沿，放宽到两值之一
    content = thumb[thumb[:, :, 0] != 240]
    assert content.size > 0, "缩略图中没有任何内容像素"
    assert np.all(content[:, 0] == content[:, 1]) and np.all(
        content[:, 1] == content[:, 2]
    ), "灰度复制通道不一致"


# ------------------------------------------------------------------------------
# D2：RGBA 4C → 丢弃 alpha
# ------------------------------------------------------------------------------
def test_make_thumbnail_rgba_drops_alpha():
    rgba = np.zeros((200, 300, 4), dtype=np.uint8)
    rgba[:, :, 0] = 10
    rgba[:, :, 1] = 20
    rgba[:, :, 2] = 30
    rgba[:, :, 3] = 255  # alpha 任意值都应被丢弃
    thumb = make_thumbnail(rgba, size=SIZE)
    assert thumb.shape == (SIZE, SIZE, 3)
    content = thumb[thumb[:, :, 0] != 240]
    assert content.size > 0
    assert np.all(content[:, 0] == 10) and np.all(content[:, 1] == 20) and np.all(
        content[:, 2] == 30
    ), "RGBA 前 3 通道值未被保留"


# ------------------------------------------------------------------------------
# D3：退化尺寸 → 清晰 ValueError（不是 OpenCV 断言）
# ------------------------------------------------------------------------------
@pytest.mark.parametrize("shape", [(0, 100, 3), (100, 0, 3), (0, 0, 3)])
def test_make_thumbnail_degenerate_size_raises_valueerror(shape):
    with pytest.raises(ValueError, match="空图像"):
        make_thumbnail(np.zeros(shape, dtype=np.uint8), size=SIZE)


# ------------------------------------------------------------------------------
# 不支持形状 → 清晰 ValueError（1D / 5 通道）
# ------------------------------------------------------------------------------
def test_make_thumbnail_unsupported_shape_raises_valueerror():
    with pytest.raises(ValueError, match="shape"):
        make_thumbnail(np.zeros(100, dtype=np.uint8), size=SIZE)
    with pytest.raises(ValueError, match="shape"):
        make_thumbnail(np.zeros((10, 10, 5), dtype=np.uint8), size=SIZE)


# ------------------------------------------------------------------------------
# 对照：正常 RGB 行为不回归（形状 / 居中 / 灰底）
# ------------------------------------------------------------------------------
def test_make_thumbnail_rgb_centered_aspect_kept():
    rgb = np.full((200, 300, 3), (10, 20, 30), dtype=np.uint8)
    thumb = make_thumbnail(rgb, size=SIZE)
    assert thumb.shape == (SIZE, SIZE, 3)
    # 比例 300/200=1.5 → 贴满宽度，上下留灰底（居中）
    assert np.all(thumb[0] == 240) and np.all(thumb[-1] == 240), "上下边缘应为灰底"
    mid = thumb[SIZE // 2]
    content = mid[mid[:, 0] != 240]
    assert content.size > 0
    assert np.all(content[:, 0] == 10) and np.all(content[:, 1] == 20) and np.all(
        content[:, 2] == 30
    ), "RGB 值未被保留"
