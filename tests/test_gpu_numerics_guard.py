# -*- coding: utf-8 -*-
"""阶段 207 · GPU 数值健全性守卫（RTX 5060 首测发现的 P0 缺陷）。

## 缺陷经过（实测，RTX 5060 + torch 2.9.1+cu128）

在 GPU 上按**项目默认设置**（`DEVICE_USE_FP16=True`）跑 LaMa 修复，结果**静默毁图**：

| 设置 | 掩码区均值 | 近黑像素占比 |
|---|---|---|
| 原图（掩码区） | 172.2 | — |
| **fp16 开（默认）** | **5.65** | **73.9%** |
| fp16 关 | 161.96 | 0.0% |

根因链：
  ① LaMa 的 FFC 用**复数运算**，PyTorch 的 `ComplexHalf` 至今实验性
     ⇒ fp16 前向输出 **100% NaN**（实测 2048² 图：12,582,912/12,582,912 个元素）；
  ② `tensor_to_rgb` 只 `clamp(0,1)`，而 **`clamp` 拦不住 NaN** ⇒
     `astype(np.uint8)` 把 NaN 变 0 ⇒ 修复区被填黑；
  ③ API 仍报 `status="success"` ⇒ **静默**。

此前从未被发现，因为全部质量验证（172/177 real_gate）都跑在 **CPU/fp32** 上，
GPU 路径直到阶段 207 才第一次真正跑起来。

## 本文件锁定三层不变式

1. `tensor_to_rgb` **必须净化 NaN/±inf**（安全网：任何路径都不能把 NaN 变成垃圾像素）；
2. LaMa **必须登记为 fp16 不安全**（根因：不让它被 `.half()`）；
3. `_run_lama_tile` 的 dtype **跟随模型**；显式传入与之矛盾的 `use_half` 必须**响亮失败**。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

torch = pytest.importorskip("torch", reason="本守卫需要 torch（PyPI 清华镜像/官方源均可装）")


# ---------------------------------------------------------------------------
# ① NaN 安全网
# ---------------------------------------------------------------------------

def test_tensor_to_rgb_sanitizes_nan_and_inf():
    """全 NaN / 含 ±inf 的张量输入，必须得到**确定且合法**的 uint8 输出。

    反例（修复前）：`clamp(0,1)` 对 NaN 无效 ⇒ `(arr*255).round().astype(np.uint8)`
    触发 “invalid value encountered in cast”，产出平台相关的垃圾值。
    """
    from core.image_utils import tensor_to_rgb

    # 全 NaN ⇒ 应为全 0（确定行为），且不得抛异常 / 不得告警溢出
    t = torch.full((1, 3, 8, 8), float("nan"))
    out = tensor_to_rgb(t)
    assert out.dtype == np.uint8
    assert out.shape == (8, 8, 3)
    assert int(out.max()) == 0, f"全 NaN 应映射为全 0（确定行为），实测 max={out.max()}"

    # ±inf ⇒ 按映射规则夹紧（+inf→1 即 255，-inf→0）
    t2 = torch.zeros(1, 3, 4, 4)
    t2[0, 0, 0, 0] = float("inf")
    t2[0, 1, 1, 1] = float("-inf")
    out2 = tensor_to_rgb(t2)
    assert out2[0, 0, 0] == 255, "+inf 应映射为白（255）"
    assert out2[1, 1, 0] == 0, "-inf 应映射为黑（0）"


def test_tensor_to_rgb_does_not_warn_on_nan_input():
    """NaN 输入不得再触发 `invalid value encountered in cast`（本缺陷的可观测症状）。"""
    import warnings

    from core.image_utils import tensor_to_rgb

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tensor_to_rgb(torch.full((1, 3, 8, 8), float("nan")))
    bad = [w for w in caught if "invalid value" in str(w.message)]
    assert not bad, f"仍出现非法 cast 告警：{[str(w.message) for w in bad]}"


def test_tensor_to_rgb_keeps_normal_range_unchanged():
    """正常 [0,1] 张量的行为不得被改动（加固只应影响异常值）。"""
    from core.image_utils import tensor_to_rgb

    # (1,3,2,2)：通道 0 全 0.0、通道 1 全 0.5、通道 2 全 1.0
    t = torch.zeros(1, 3, 2, 2)
    t[0, 1] = 0.5
    t[0, 2] = 1.0
    out = tensor_to_rgb(t)                 # → (2,2,3)
    assert out.shape == (2, 2, 3)
    assert int(out[0, 0, 0]) == 0
    assert int(out[0, 0, 1]) == 128        # 0.5×255 = 127.5 → round 128
    assert int(out[0, 0, 2]) == 255


# ---------------------------------------------------------------------------
# ② LaMa 必须登记为 fp16 不安全（根因）
# ---------------------------------------------------------------------------

def test_lama_is_registered_as_fp16_unsafe():
    """`lama` 必须在 `_FP16_UNSAFE_MODELS` 里——否则它会重新被 `.half()` ⇒ 全 NaN。

    实测依据见 `core/model_manager._FP16_UNSAFE_MODELS` 的 docstring。
    """
    import core.model_manager as mm

    assert hasattr(mm, "_FP16_UNSAFE_MODELS"), "缺少 _FP16_UNSAFE_MODELS 登记表"
    assert "lama" in mm._FP16_UNSAFE_MODELS, "lama 必须登记为 fp16 不安全"
    assert mm._is_fp16_unsafe("lama") is True
    assert mm._is_fp16_unsafe("LaMa") is True, "判定应大小写不敏感"
    assert mm._is_fp16_unsafe("some_other_model") is False


def test_fp16_unsafe_registry_entries_have_reasons():
    """登记表里的每一项都必须在源码里**找得到理由**（防「幽灵登记」，同 LESSONS 217）。"""
    import core.model_manager as mm

    src = open(os.path.join(_ROOT, "core", "model_manager.py"), encoding="utf-8").read()
    for name in mm._FP16_UNSAFE_MODELS:
        assert name in src, f"{name} 不在源码里？"
    # 理由必须提到根因关键词（ComplexHalf / 复数 / NaN），否则等于没写依据
    assert any(k in src for k in ("ComplexHalf", "复数", "NaN")), \
        "_FP16_UNSAFE_MODELS 附近缺少根因说明（ComplexHalf / 复数 / NaN）"


def test_load_path_consults_the_fp16_unsafe_registry():
    """加载路径**必须真的消费**登记表（否则登记只是装饰）。"""
    src = open(os.path.join(_ROOT, "core", "model_manager.py"), encoding="utf-8").read()
    assert "_is_fp16_unsafe(slot.name)" in src, \
        "load_lama_model 未按 _is_fp16_unsafe 判断 ⇒ 登记表形同虚设"


# ---------------------------------------------------------------------------
# ③ dtype 跟随模型；矛盾时响亮失败
# ---------------------------------------------------------------------------

class _IdentityProbe:
    """恒等伪模型：dtype 可控，输入原样输出（值域 [0,1]）。"""

    def __init__(self, dtype):
        self._p = torch.nn.Parameter(torch.zeros(1, dtype=dtype))

    def parameters(self):
        return iter([self._p])

    def __call__(self, img, mask):
        return img


def test_run_lama_tile_derives_dtype_from_model():
    """float32 模型 + 不传 use_half ⇒ 正常返回 uint8（输入不被强转 half）。"""
    from core.inpainter import _run_lama_tile

    m = _IdentityProbe(torch.float32)
    img = np.full((16, 16, 3), 128, np.uint8)
    mask = np.zeros((16, 16), np.uint8)
    out = _run_lama_tile(m, img, mask)
    assert out.dtype == np.uint8 and out.shape == (16, 16, 3)
    assert int(out.mean()) in range(120, 136), f"恒等模型应保持原值，实测均值 {out.mean()}"


def test_run_lama_tile_rejects_contradictory_use_half():
    """显式传入与模型 dtype 矛盾的 use_half 必须**响亮失败**（不得静默错配）。"""
    from core.inpainter import _run_lama_tile

    m = _IdentityProbe(torch.float32)
    img = np.full((16, 16, 3), 128, np.uint8)
    mask = np.zeros((16, 16), np.uint8)
    with pytest.raises(ValueError, match="唯一真值来源"):
        _run_lama_tile(m, img, mask, use_half=True)


def test_run_lama_tile_accepts_matching_use_half():
    """与模型 dtype **一致**的显式 use_half 仍可用（向后兼容旧调用）。"""
    from core.inpainter import _run_lama_tile

    m = _IdentityProbe(torch.float32)
    img = np.full((16, 16, 3), 200, np.uint8)
    mask = np.zeros((16, 16), np.uint8)
    out = _run_lama_tile(m, img, mask, use_half=False)     # 与 float32 一致 ⇒ 允许
    assert out.shape == (16, 16, 3)
