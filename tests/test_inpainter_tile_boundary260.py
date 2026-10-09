# -*- coding: utf-8 -*-
"""
阶段 260 守卫 · 大图分块阈值 `INFER_MAX_SIDE` 的**边界语义**（待决项② 判定）。

结论（探针实测，见 docs/260）：长边**恰好等于** `INFER_MAX_SIDE`(2048) 时，
LaMa 整图推理**安全成功**（峰值 3348.7 MB / 卡容 8123 MB，无 OOM）⇒ 边界判定用
**严格大于 `>`**（等于阈值走整图）是正确的，**不改 `>=`**。

本守卫直接测**做边界判定的函数** `_tile_if_needed`（用探针伪引擎 + spy `split_into_tiles`），
把契约钉死：
  ① `max_side == INFER_MAX_SIDE` → **不分块**（走整图 `eng.inpaint`）；
  ② `max_side == INFER_MAX_SIDE + 1` → **分块**（`split_into_tiles` 被调用）；
  ③ `max_side == INFER_MAX_SIDE - 1` → **不分块**；
  ④ `ENABLE_TILING=False` 时即便超阈值也**不分块**。
用伪引擎（恒等映射 + 调用计数）而非真实权重 ⇒ CPU 快、不依赖 torch/GPU。
"""
from __future__ import annotations

import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core import inpainter as IP  # noqa: E402


class _ProbeEngine:
    """伪修复引擎：记录整图调用次数，恒等返回（mask 区填中灰以示「被修复」）。"""

    def __init__(self):
        self.full_calls = 0

    def inpaint(self, image, mask, device=None, progress=None):
        self.full_calls += 1
        out = np.array(image, copy=True)
        out[np.asarray(mask) > 0] = 127
        return out

    # 分块路径会用到的接口（保持最小可运行）
    requires_model = False
    name = "probe"


def _run_side(side, enable_tiling, monkeypatch, tile_size=512, overlap=64):
    """以给定边长/分块开关跑一次 _tile_if_needed，返回 (引擎, 是否触过分块)。"""
    eng = _ProbeEngine()
    split_called = {"v": False}
    real_split = IP.split_into_tiles

    def spy_split(*a, **kw):
        split_called["v"] = True
        return real_split(*a, **kw)

    monkeypatch.setattr(IP, "split_into_tiles", spy_split)
    monkeypatch.setattr(config, "ENABLE_TILING", enable_tiling)
    monkeypatch.setattr(config, "INFER_MAX_SIDE", 2048)
    monkeypatch.setattr(config, "TILE_SIZE", tile_size)
    monkeypatch.setattr(config, "TILE_OVERLAP", overlap)

    img = np.full((side, side, 3), 30, np.uint8)
    mask = np.zeros((side, side), np.uint8)
    mask[side // 2 - 4:side // 2 + 4, side // 2 - 4:side // 2 + 4] = 255

    out = IP._tile_if_needed(eng, img, mask, device=None, progress=None)
    assert out is not None
    return eng, split_called["v"]


def test_equal_to_threshold_does_not_tile(monkeypatch):
    """契约①：长边**恰好等于** INFER_MAX_SIDE(2048) → **不分块**（整图推理）。

    这是 docs/260 探针实测支撑的语义：2048 整图推理成功且显存有余（41%）。
    """
    eng, split_called = _run_side(2048, True, monkeypatch)
    assert not split_called, "长边恰好等于阈值时不应触发分块"
    assert eng.full_calls >= 1, "应走整图 eng.inpaint"


def test_above_threshold_tiles(monkeypatch):
    """契约②：长边 = 阈值+1（2049）→ **触发分块**（split_into_tiles 被调用）。"""
    _eng, split_called = _run_side(2049, True, monkeypatch)
    assert split_called, "长边超过阈值时必须触发分块"


def test_below_threshold_does_not_tile(monkeypatch):
    """契约③：长边 = 阈值−1（2047）→ 不分块。"""
    _eng, split_called = _run_side(2047, True, monkeypatch)
    assert not split_called, "长边低于阈值时不应分块"


def test_tiling_disabled_never_tiles(monkeypatch):
    """契约④：ENABLE_TILING=False 时，即便超阈值（2049）也不分块。"""
    _eng, split_called = _run_side(2049, False, monkeypatch)
    assert not split_called, "ENABLE_TILING=False 时不应分块"


def test_boundary_uses_strict_greater_than(monkeypatch):
    """钉死比较运算符是严格大于 `>`：等于阈值不分块、+1 分块（两断言合看）。"""
    _, split_at = _run_side(2048, True, monkeypatch)
    _, split_above = _run_side(2049, True, monkeypatch)
    assert (not split_at) and split_above, (
        "边界契约应为 `max_side > INFER_MAX_SIDE`（等于阈值走整图）；"
        f"实测 等于阈值分块={split_at}、+1 分块={split_above}"
    )


def test_config_infer_max_side_default_is_2048():
    """INFER_MAX_SIDE 默认 2048（探针标定与守卫边界均基于此值）。"""
    assert config.INFER_MAX_SIDE == 2048, (
        f"INFER_MAX_SIDE 默认变为 {config.INFER_MAX_SIDE}——"
        f"docs/260 的边界实测与本守卫边界需重新标定"
    )