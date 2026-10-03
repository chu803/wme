# -*- coding: utf-8 -*-
"""
阶段 115 审计守卫：core/superres 的「无权重 esrgan」输出正确性与 scale 缓存契约。

审计发现（阶段 115）：
  C2（真缺陷，高严重）：`_TorchESRGANBackend` 的「无权重」分支保留的是**随机初始化的
     卷积网络**，其输出是噪声（实测与干净双三次 mean_abs_diff≈102、画面均值 138→45），
     却被 docstring / note 称为「双三次上采样 + 恒等卷积」。而 `postprocess_service`
     只要拿到**非 None** 输出就直接采用（忽略 used_real 旗标）⇒ 开启真·超分（esrgan
     后端、无权重，因 `REAL_SUPERRES_MODEL_PATH` 默认空）即得到**近黑噪声图**。
     修复：无权重（或权重加载失败）时改为真正的纯双三次上采样（nn.Upsample）。
  C9（真缺陷，潜伏）：`_ensure_model` 用 `_built` 缓存模型但未按 scale 键控 ⇒ 同一实例
     先请求 2× 再请求 4×，第二次返回的仍是 2× 尺寸（实测）。

本文件钉死上述契约。全部依赖 torch；无 torch 时自动跳过。
"""

import os as _os
import sys as _sys

import numpy as np
import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import cv2  # noqa: E402
from core.superres import (SuperResBackend, get_superres_backend,  # noqa: E402
                           superres_upscale)


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


_needs_torch = pytest.mark.skipif(
    not _torch_available(), reason="无 torch，esrgan 后端不可用（工厂返回 None）")


def _img(h: int = 48, w: int = 64) -> np.ndarray:
    """结构化测试图：渐变 + 圆块（非纯色，便于与干净双三次对比）。"""
    xs = (np.arange(w)[None, :] * 4 % 256).astype(np.uint8)
    ys = (np.arange(h)[:, None] * 5 % 256).astype(np.uint8)
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:, :, 0] = xs
    arr[:, :, 1] = ys
    arr[:, :, 2] = ((xs.astype(int) + ys.astype(int)) * 3 % 256).astype(np.uint8)
    cv2.circle(arr, (32, 24), 10, (255, 255, 255), -1)
    return arr


# ------------------------------------------------------------------------------
# C2 守卫：无权重 esrgan 必须产出「干净双三次」，绝不能是随机卷积噪声
# ------------------------------------------------------------------------------
@_needs_torch
def test_esrgan_no_weights_produces_clean_bicubic():
    arr = _img()
    h, w = arr.shape[:2]
    b = get_superres_backend("esrgan", model_path="")
    assert isinstance(b, SuperResBackend)
    out, used, note = superres_upscale(arr, b, 4)
    assert out is not None and out.shape == (h * 4, w * 4, 3)
    assert used is False
    # note 必须如实标注「双三次」——不得再谎称「训练权重 / 真实推理」。
    assert "双三次" in note
    # 亮度必须与源接近（此前随机卷积会把均值 138→45 的近黑噪声图当结果）。
    assert abs(float(out.mean()) - float(arr.mean())) < 15.0
    # 与 cv2 干净双三次接近（此前 mean_abs_diff≈102）。
    bic = cv2.resize(arr, (w * 4, h * 4), interpolation=cv2.INTER_CUBIC)
    diff = np.abs(out.astype(int) - bic.astype(int))
    assert diff.mean() < 8.0, f"无权重 esrgan 输出非干净双三次：mean_abs_diff={diff.mean():.2f}"


# ------------------------------------------------------------------------------
# C9 守卫：模型缓存必须按 scale 键控（同一实例跨倍数复用不得返回旧尺寸）
# ------------------------------------------------------------------------------
@_needs_torch
def test_esrgan_scale_cache_keyed_by_scale():
    arr = _img()
    h, w = arr.shape[:2]
    b = get_superres_backend("esrgan", model_path="")
    o2, _, _ = superres_upscale(arr, b, 2)
    o4, _, _ = superres_upscale(arr, b, 4)
    assert o2 is not None and o2.shape == (h * 2, w * 2, 3)
    assert o4 is not None and o4.shape == (h * 4, w * 4, 3), "第二次请求的 scale 被忽略（缓存未按 scale 键控）"


# ------------------------------------------------------------------------------
# 既有契约钉死：输入校验 / 未知 kind 优雅降级
# ------------------------------------------------------------------------------
def test_upscale_rejects_non_bgr_input():
    b = SuperResBackend()
    out, used, note = b.upscale(np.zeros((4, 4), dtype=np.uint8), 4)  # 2D
    assert out is None and used is False and isinstance(note, str) and note
    out2, _, _ = b.upscale(np.zeros((4, 4, 4), dtype=np.uint8), 4)     # 4 通道
    assert out2 is None
    out3, _, _ = b.upscale("not-an-array", 4)                          # 非 ndarray
    assert out3 is None


def test_get_superres_backend_unknown_kind_none():
    # 未知 kind 应返回 None，而非抛异常（调用方回退轻量超分）。
    assert get_superres_backend("bogus") is None
    assert superres_upscale(np.zeros((8, 8, 3), np.uint8), None, 4)[0] is None
