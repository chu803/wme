# -*- coding: utf-8 -*-
"""阶段 321 · 深色底牌先验守卫。

背景（阶段 317 探针）：真实 57 个标注水印 100% 是角标；但换一种板样式
（**深色圆角板 + 白字**）时 `ai_logo` 检回只有 0.267——原来两条候选来源
（白色判据 V≥140/S≤80、局部提亮）都假设「牌比底亮」。本通路是镜像：
**暗 + 低饱和**的紧凑连通域 + 内部必须有**亮**墨迹。

这里只测判据本身（不跑模型/OCR）：构造深色牌与白牌，断言极性没写反、开关有效。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core import detector as det  # noqa: E402

SIDE = 200
PLATE_W, PLATE_H = 48, 16
PX, PY = 60, 60


def _patch(plate_value: int, ink_value: int) -> np.ndarray:
    """灰底 + 一块不带圆角的实心牌 + 牌内三条竖条（模拟字）。"""
    img = np.full((SIDE, SIDE, 3), 150, np.uint8)
    img[PY:PY + PLATE_H, PX:PX + PLATE_W] = plate_value
    for i in range(3):
        x = PX + 6 + i * 12
        img[PY + 4:PY + PLATE_H - 4, x:x + 6] = ink_value
    return img


def _boxes(img, source):
    return det._plate_boxes_in_patch(img, 0, 0, SIDE * SIDE, 8, mask_source=source)


def _hits(boxes):
    return [b for b in boxes
            if b[0] <= PX + PLATE_W and b[0] + b[2] >= PX
            and b[1] <= PY + PLATE_H and b[1] + b[3] >= PY]


def test_dark_plate_is_found_by_dark_source():
    img = _patch(plate_value=24, ink_value=250)
    boxes = _boxes(img, "dark")
    assert boxes, "深色牌（dark + 低饱和 + 亮字）未被深色通路检出"
    assert _hits(boxes), f"检出的框与真值牌不相交：{boxes}"


def test_dark_plate_is_not_found_by_white_source():
    """极性对照：深色牌不应被**白牌**判据当成牌（否则等于没做镜像）。"""
    img = _patch(plate_value=24, ink_value=250)
    assert not _hits(_boxes(img, "white")), "白牌判据把深色牌当成了白牌"


def test_white_plate_is_not_found_by_dark_source():
    """反方向：白底黑字的牌不该被深色通路收进来（防全局放宽）。"""
    img = _patch(plate_value=245, ink_value=20)
    assert not _hits(_boxes(img, "dark")), "深色判据把白牌当成了深色牌"


def test_dark_plate_without_ink_is_rejected():
    """整块纯暗（无亮字）不是牌：ink 闸门必须挡住。"""
    img = _patch(plate_value=24, ink_value=24)
    assert not _hits(_boxes(img, "dark")), "无亮墨迹的纯暗块被误判为牌"


def test_dark_source_respects_flag(monkeypatch):
    monkeypatch.setattr(config, "DETECT_PLATE_ENABLED", False, raising=True)
    img = _patch(plate_value=24, ink_value=250)
    assert _boxes(img, "dark") == [], "总开关关闭时深色通路仍在工作"
