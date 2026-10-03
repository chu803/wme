# -*- coding: utf-8 -*-
"""阶段 102 · 修复侧 P0：掩码值域决定「修不修」——但此前是**静默**决定。

审计对象：`core/inpainter.py` 的公开入口 `inpaint()`。

真实缺陷（已实测复现）
----------------------
`inpaint()` 的 docstring 声明「mask：掩码 H×W（任意值域，0/非0 即可，内部会标准化）」，
但实现只做 `mask.astype(np.uint8)`，**不做值域归一化**；随后 `_prepare_mask()` 用
`MASK_THRESHOLD_DEFAULT = 127` 二值化。
⇒ 传 `res.mask > 0`（bool）或 0/1 掩码 —— 最自然的写法 —— 会被阈值**整个清零**，
   修复退化成**静默空操作**：不抛异常、输出与输入逐字节相同。
   （实测：同一图同一掩码，0/255 → 改动 110269 px；bool → 0 px。）

这正是项目最忌讳的「静默全绿」形态，且**第三方按契约调用时必踩**。

本文件守四层
------------
I1  值域等价：bool / 0-1 / [0,1] 浮点 三种表示必须与 0/255 **逐字节相同**
I2  灰度软掩码语义不变：>1 的灰度掩码仍由 threshold 决定，**且不会被静默提升**
I3  真「空掩码」仍抛 EmptyMaskError；三维掩码仍取第 0 通道
I4  「非空但二值化后全空」必须发出**可见告警**（把静默变成可见）
+  I0 守卫自检：绕过 inpaint 直接喂 0/1 给 `_prepare_mask` **必须**得到全零
    —— 证明 I1 测的是真东西，而不是「本来就一直好」
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
    _prepare_mask,
    inpaint,
)


# ---------------------------------------------------------------------------
# 夹具：一张小图 + 一个方形掩码（opencv 引擎，确定性、不需要模型）
# ---------------------------------------------------------------------------

@pytest.fixture()
def scene():
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
    """固定参数跑一次修复，保证不同值域之间的可比性。"""
    return inpaint(img, mask, engine="opencv", strength=1.0, denoise=0)


# ---------------------------------------------------------------------------
# I0 · 守卫自检：确认「不归一化就会全零」这一机制真实存在
# ---------------------------------------------------------------------------

def test_I0_prepare_mask_zeroes_zero_one_input():
    """直接喂 {0,1} 给 _prepare_mask 必须得到全零。

    这是 I1/I2 的**存在性证明**：如果这一步不是全零，那说明阈值链路变了，
    I1 就可能在守一个不存在的问题；反之它保证了「提升值域」这一步是必需的。
    """
    m01 = np.ones((32, 32), np.uint8)          # 全是 1 = 「整幅都是水印」
    proc = _prepare_mask(m01, dilate=2, feather=5,
                         thresh=config.MASK_THRESHOLD_DEFAULT)
    assert proc.max() == 0, "0/1 掩码未被阈值清零，说明 I1 的机制前提已变"


# ---------------------------------------------------------------------------
# I1 · 值域等价（本次缺陷的正面守门）
# ---------------------------------------------------------------------------

def test_I1_bool_mask_equals_0_255_mask(scene):
    """bool 掩码与 0/255 掩码必须产生逐字节相同的修复结果。"""
    img, m255 = scene
    out255 = _run(img, m255)
    out_bool = _run(img, m255 > 0)
    assert np.array_equal(out255, out_bool), (
        "bool 掩码与 0/255 掩码结果不一致 —— 值域归一化回归"
    )
    assert not np.array_equal(out255, img), "修复没有产生任何改动（夹具失效）"


def test_I1_zero_one_uint8_mask_equals_0_255_mask(scene):
    """uint8 的 {0,1} 掩码同样必须等价。"""
    img, m255 = scene
    m01 = (m255 > 0).astype(np.uint8)
    assert np.array_equal(_run(img, m255), _run(img, m01))


def test_I1_float_unit_mask_equals_0_255_mask(scene):
    """[0,1] 浮点掩码（常见于归一化后的调用方）同样必须等价。"""
    img, m255 = scene
    mf = (m255 > 0).astype(np.float32)
    assert np.array_equal(_run(img, m255), _run(img, mf))


# ---------------------------------------------------------------------------
# I2 · 灰度软掩码语义不变（不得被「顺手提升」）
# ---------------------------------------------------------------------------

def test_I2_gray_mask_below_threshold_is_not_promoted(scene):
    """全部低于 threshold 的灰度软掩码**不得**被提升为 0/255 —— 否则 threshold 失效。"""
    img, _ = scene
    soft = np.zeros(img.shape[:2], np.uint8)
    soft[20:40, 20:40] = config.MASK_THRESHOLD_DEFAULT - 1     # 恰好低于阈值
    out = _run(img, soft)
    assert np.array_equal(out, img), (
        "灰度软掩码被错误提升 —— 修复不该发生（threshold 语义被破坏）"
    )


def test_I2_gray_mask_above_threshold_is_honored(scene):
    """高于 threshold 的灰度软掩码必须正常参与修复（反向对照）。"""
    img, _ = scene
    soft = np.zeros(img.shape[:2], np.uint8)
    soft[20:40, 20:40] = config.MASK_THRESHOLD_DEFAULT + 1
    out = _run(img, soft)
    assert not np.array_equal(out, img), "高于阈值的灰度掩码未能触发修复"


# ---------------------------------------------------------------------------
# I3 · 空掩码与三维掩码
# ---------------------------------------------------------------------------

def test_I3_all_black_mask_still_raises(scene):
    img, _ = scene
    with pytest.raises(EmptyMaskError):
        _run(img, np.zeros(img.shape[:2], np.uint8))


def test_I3_three_channel_mask_uses_first_channel(scene):
    """(H,W,3) 掩码必须按第 0 通道解释，且与单通道结果一致。"""
    img, m255 = scene
    m3 = np.repeat(m255[:, :, None], 3, axis=2)
    assert np.array_equal(_run(img, m255), _run(img, m3))


# ---------------------------------------------------------------------------
# I4 · 「非空但二值化后全空」必须可见
# ---------------------------------------------------------------------------

def test_I4_empty_after_threshold_emits_warning(scene, monkeypatch):
    """修复不发生又没报错时，必须留下可见告警（静默是审计的头号敌人）。"""
    import core.inpainter as ip

    seen: list = []
    monkeypatch.setattr(ip.log, "warning", lambda *a, **k: seen.append(a))

    img, _ = scene
    soft = np.zeros(img.shape[:2], np.uint8)
    soft[20:40, 20:40] = 10                     # 非空，但远低于阈值 127
    _run(img, soft)

    assert any("二值化后为空" in str(a[0]) for a in seen), (
        f"未发出可见告警：{[a[0] for a in seen]}"
    )


def test_I4_no_warning_on_normal_mask(scene, monkeypatch):
    """正常掩码不得刷这条告警（避免噪声把信号淹没）。"""
    import core.inpainter as ip

    seen: list = []
    monkeypatch.setattr(ip.log, "warning", lambda *a, **k: seen.append(a))

    img, m255 = scene
    _run(img, m255)
    assert not any("二值化后为空" in str(a[0]) for a in seen)
