# -*- coding: utf-8 -*-
"""阶段 142 · 修复侧：掩码值域「越界回绕」导致静默空操作。

审计对象：`core/inpainter.py` 的公开入口 `inpaint()`。
荒漠度：3.3（26276 字节 ÷ 引用它的 12 个测试文件里 91 个用例）。

真实缺陷（探针实测复现，修前/修后对照）
--------------------------------------
`inpaint()` 的 docstring 声明 mask 为「bool / 0-1 / 0-255 **任意值域**，语义为
0=保留、非0=修复」，但入口只做 `mask.astype(np.uint8)`：

    float32 全片 300.0 掩码  →  astype(uint8) 回绕成 44  →  低于阈值 127
                             →  整片被清零  →  **零改动，不抛异常**
    float32 全片 256.0 掩码  →  astype(uint8) 回绕成 0
                             →  误抛 EmptyMaskError，理由是「掩码为空」

两者都是「契约承诺 vs 实现」的缺口，且第一种正是项目最忌讳的
**静默空操作**（不抛异常、输出与输入逐字节相同）。

实测（本文件夹具，64×64，掩码区 400 px）：
    修前：{255.0: 400 px, 256.0: 抛 EmptyMaskError, 300.0: **0 px**}
    修后：{255.0: 400 px, 256.0: 400 px, 300.0: **400 px**}

修复位置：`core/inpainter.py` 入口掩码规整段 —— 转 uint8 **之前**先看原始
值域上界，一旦存在 >255 的值就按「非零即修复」抬到 0/255，并留下可见告警。
落点选择理由：`threshold_mask/dilate_mask/feather_mask` 是 `image_utils` 的
公共底层函数（检测侧也在用），在那里加值域判定会波及无关调用方；
`inpaint()` 入口才是契约的承诺方，且此处已有阶段 102 的同类归一化先例。

本文件守四层
------------
J1  值域越界等价：>255 的浮点掩码必须与 0/255 掩码**逐字节相同**（核心断言）
J2  不得误伤 0~255 灰度软掩码：仍由 threshold 决定（与阶段 102 I2 互补）
J3  越界路径必须留下**可见告警**（把静默变成可见，含「不回绕」反向对照）
J4  256.0 边界不再误抛 EmptyMaskError（错误类型与原因的语义正确性）
+  J0 守卫自检：绕过 inpaint 直接 `astype(np.uint8)` 喂 300.0 必须回绕成 44
    —— 证明 J1 测的是真机制，而不是「本来就一直好」
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                    # noqa: E402
from core.inpainter import (                                     # noqa: E402
    EmptyMaskError,
    inpaint,
)


# ---------------------------------------------------------------------------
# 夹具：一张小图 + 一个方形掩码
# ---------------------------------------------------------------------------

@pytest.fixture()
def scene():
    """64×64 平滑渐变图 + 400 px 方形掩码（opencv 引擎，确定性、不需要模型）。"""
    h = w = 64
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 3), np.uint8)
    img[..., 0] = (xx * 3) % 256
    img[..., 1] = (yy * 5) % 256
    img[..., 2] = 128
    mask255 = np.zeros((h, w), np.uint8)
    mask255[20:40, 20:40] = 255
    return img, mask255


def _run(img, mask):
    """固定参数跑一次修复，保证不同值域之间可比。"""
    return inpaint(img, mask, engine="opencv", strength=1.0, denoise=0)


def _changed_pixels(a, b):
    return int((a != b).any(axis=-1).sum())


# ---------------------------------------------------------------------------
# J0 · 守卫自检：证明「uint8 回绕」这一机制真实存在
# ---------------------------------------------------------------------------

def test_J0_uint8_cast_wraps_overflow_for_real():
    """存在性证明：300.0 / 256.0 经 astype(uint8) 必须回绕，且低于阈值。

    若这一步不再回绕（例如 numpy 改了行为），J1 就在守一个不存在的问题；
    反之它证明了「入口先判值域上界」这一步是必需的。
    """
    assert np.array([300.0], np.float32).astype(np.uint8)[0] == 44
    assert np.array([256.0], np.float32).astype(np.uint8)[0] == 0
    assert 44 < config.MASK_THRESHOLD_DEFAULT, "回绕值不再低于阈值，J1 前提已变"


# ---------------------------------------------------------------------------
# J1 · 值域越界等价（本次缺陷的正面守门）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("overflow", [256.0, 300.0, 511.0, 1e6])
def test_J1_overflow_float_mask_equals_0_255_mask(scene, overflow):
    """>255 的浮点掩码必须与 0/255 掩码产生**逐字节相同**的修复结果。"""
    img, m255 = scene
    out255 = _run(img, m255)
    mf = np.zeros(img.shape[:2], np.float32)
    mf[20:40, 20:40] = overflow
    out_f = _run(img, mf)
    assert np.array_equal(out255, out_f), (
        f"值域越界掩码（{overflow}）与 0/255 掩码结果不一致 —— 回绕回归"
    )


def test_J1_overflow_mask_is_not_silent_noop(scene):
    """核心回归：越界掩码**必须产生改动**（守住「静默空操作」）。"""
    img, m255 = scene
    mf = np.zeros(img.shape[:2], np.float32)
    mf[20:40, 20:40] = 300.0
    out = _run(img, mf)
    assert not np.array_equal(out, img), (
        "越界掩码未产生任何改动 —— 静默空操作回归"
    )
    assert _changed_pixels(out, img) >= 400, "改动像素数少于掩码面积"


def test_J1_negative_float_mask_still_repairs(scene):
    """负值掩码同样属于「非零即修复」，不得被静默丢弃。"""
    img, _ = scene
    mf = np.zeros(img.shape[:2], np.float32)
    mf[20:40, 20:40] = -10.0
    out = _run(img, mf)
    assert not np.array_equal(out, img), "负值掩码未触发修复"


# ---------------------------------------------------------------------------
# J2 · 不得误伤 0~255 灰度软掩码（与阶段 102 互补）
# ---------------------------------------------------------------------------

def test_J2_gray_mask_below_threshold_still_no_repair(scene):
    """全部低于 threshold 的 0~255 灰度掩码仍不得触发修复（threshold 语义完整）。"""
    img, _ = scene
    soft = np.zeros(img.shape[:2], np.uint8)
    soft[20:40, 20:40] = config.MASK_THRESHOLD_DEFAULT - 1
    assert np.array_equal(_run(img, soft), img)


def test_J2_gray_mask_above_threshold_still_repairs(scene):
    """高于 threshold 的灰度掩码仍正常参与修复（反向对照）。"""
    img, _ = scene
    soft = np.zeros(img.shape[:2], np.uint8)
    soft[20:40, 20:40] = config.MASK_THRESHOLD_DEFAULT + 1
    assert not np.array_equal(_run(img, soft), img)


# ---------------------------------------------------------------------------
# J3 · 越界路径必须可见
# ---------------------------------------------------------------------------

def test_J3_overflow_mask_emits_warning(scene, monkeypatch):
    """走越界归一化时必须留下可见告警（静默是审计的头号敌人）。"""
    import core.inpainter as ip

    seen: list = []
    monkeypatch.setattr(ip.log, "warning", lambda *a, **k: seen.append(a))

    img, _ = scene
    mf = np.zeros(img.shape[:2], np.float32)
    mf[20:40, 20:40] = 300.0
    _run(img, mf)

    assert any("超过 255" in str(a[0]) for a in seen), (
        f"越界掩码未发出可见告警：{[a[0] for a in seen]}"
    )


def test_J3_normal_mask_emits_no_overflow_warning(scene, monkeypatch):
    """正常 0/255 掩码不得刷这条告警（避免噪声淹没信号）。"""
    import core.inpainter as ip

    seen: list = []
    monkeypatch.setattr(ip.log, "warning", lambda *a, **k: seen.append(a))

    img, m255 = scene
    _run(img, m255)
    assert not any("超过 255" in str(a[0]) for a in seen)


# ---------------------------------------------------------------------------
# J4 · 256.0 边界不再误抛 EmptyMaskError
# ---------------------------------------------------------------------------

def test_J4_overflow_256_no_longer_raises_empty_mask(scene):
    """256.0 曾回绕成 0 而误抛 EmptyMaskError（原因与实际不符），现在必须正常修复。"""
    img, _ = scene
    mf = np.zeros(img.shape[:2], np.float32)
    mf[20:40, 20:40] = 256.0
    out = _run(img, mf)          # 不得抛 EmptyMaskError
    assert not np.array_equal(out, img)


def test_J4_truly_empty_mask_still_raises(scene):
    """真「空掩码」仍必须抛 EmptyMaskError（不得因本次修复而放宽）。"""
    img, _ = scene
    with pytest.raises(EmptyMaskError):
        _run(img, np.zeros(img.shape[:2], np.uint8))
    with pytest.raises(EmptyMaskError):
        _run(img, np.zeros(img.shape[:2], np.float32))
