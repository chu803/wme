# -*- coding: utf-8 -*-
"""
阶段 32 · 真·超分（AI 模型 + torch）测试。

覆盖：
  1. 后端工厂在无 torch / 无第三方包时优雅返回 None（优雅降级契约）。
  2. superres_upscale 在 backend=None 时返回 (None, False, note)。
  3. postprocess(real_superres=True) 无后端时回退轻量超分（尺寸不变、不报错）。
  4. postprocess(real_superres=True) 后端可用时真实放大输出尺寸（mock 后端）。
  5. evaluate_improvement 在「improved 被放大」时尺寸归一、不报错。
  6. components.RepairParamHandles 含新字段；param_values 透出新键。
  7. i18n 新增真·超分键存在（中 / 英）。
"""

import types

import numpy as np
import pytest

import config
from core import superres as core_superres
from services import postprocess_service as pp
from utils import i18n


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


# 环境前提：后端工厂的「无后端」行为仅在 torch 缺失时成立；
# torch 就绪后 esrgan 后端存在（退化的双三次 torch 推理），断言反转。
_TORCH_PRESENT = _torch_available()


# ------------------------------------------------------------------------------
# 1. 后端工厂优雅降级（无 torch / realesrgan / basicsr 环境）
# ------------------------------------------------------------------------------
@pytest.mark.skipif(_TORCH_PRESENT, reason="torch 已安装，后端工厂行为反转，跳过「无后端」验证")
def test_get_superres_backend_unavailable():
    # 无 torch 环境下，三种 kind 都应返回 None（调用方回退轻量超分）。
    assert core_superres.get_superres_backend("esrgan") is None
    assert core_superres.get_superres_backend("realesrgan") is None
    assert core_superres.get_superres_backend("auto") is None
    # 未知 kind 也应返回 None，而非抛异常。
    assert core_superres.get_superres_backend("bogus") is None


# ------------------------------------------------------------------------------
# 2. superres_upscale 无后端时的回退信号
# ------------------------------------------------------------------------------
def test_superres_upscale_none_backend():
    fake = np.zeros((64, 64, 3), dtype=np.uint8)
    out, used, note = core_superres.superres_upscale(fake, None, 4)
    assert out is None
    assert used is False
    assert isinstance(note, str) and note


# ------------------------------------------------------------------------------
# 辅助：合成测试图（浅灰底 + 一块「水印」灰块）
# ------------------------------------------------------------------------------
def _make_images():
    base = np.full((100, 120, 3), 240, dtype=np.uint8)
    import cv2  # 仅测试用

    orig = base.copy()
    cv2.rectangle(orig, (20, 20), (90, 80), (200, 200, 200), -1)
    mask = np.zeros((100, 120), dtype=np.uint8)
    cv2.rectangle(mask, (20, 20), (90, 80), 255, -1)
    raw = base.copy()
    cv2.rectangle(raw, (20, 20), (90, 80), (215, 215, 215), -1)
    return orig, mask, raw


# ------------------------------------------------------------------------------
# 3. postprocess 真实超分无后端 → 优雅回退（尺寸不变）
# ------------------------------------------------------------------------------
@pytest.mark.skipif(_TORCH_PRESENT, reason="torch 已安装，真实超分后端存在，跳过「无后端回退」验证")
def test_postprocess_real_superres_fallback_no_backend():
    orig, mask, raw = _make_images()
    out = pp.postprocess(
        raw, original_rgb=orig, mask=mask,
        real_superres=True, real_scale=4,
    )
    # 无后端时应回退（轻量超分默认关闭），输出尺寸与原图一致。
    assert out.shape == raw.shape


# ------------------------------------------------------------------------------
# 4. postprocess 真实超分后端可用 → 真实放大输出尺寸（mock 后端）
# ------------------------------------------------------------------------------
class _FakeBackend(core_superres.SuperResBackend):
    name = "fake"

    def _infer(self, arr, scale):
        # 简单 2× 放大（重复像素），仅验证尺寸变化链路。
        return arr.repeat(scale, axis=0).repeat(scale, axis=1)


def test_postprocess_real_superres_upscales(monkeypatch):
    orig, mask, raw = _make_images()
    fake = _FakeBackend()
    monkeypatch.setattr(pp, "get_superres_backend", lambda *a, **k: fake)
    out = pp.postprocess(
        raw, original_rgb=orig, mask=mask,
        real_superres=True, real_scale=2,
    )
    # 后端可用 → 输出应被真实放大（2×）。
    assert out.shape[0] == raw.shape[0] * 2
    assert out.shape[1] == raw.shape[1] * 2


# ------------------------------------------------------------------------------
# 5. evaluate_improvement 尺寸归一（improved 被放大时）
# ------------------------------------------------------------------------------
def test_evaluate_improvement_size_mismatch():
    orig, mask, raw = _make_images()
    # improved 被「放大」成不同尺寸（模拟真实超分输出）。
    improved = np.zeros((raw.shape[0] * 2, raw.shape[1] * 2, 3), dtype=np.uint8)
    imp = pp.evaluate_improvement(orig, mask, raw, improved)
    assert set(imp.keys()) >= {
        "score_before", "score_after", "delta", "better",
        "detail", "level_before", "level_after",
    }
    assert isinstance(imp["delta"], (int, float))


# ------------------------------------------------------------------------------
# 6. components 句柄字段 + param_values 透出新键（不依赖 gradio 构造组件）
# ------------------------------------------------------------------------------
def test_components_real_superres_fields_and_param_values():
    import dataclasses

    from ui import components as comp

    field_names = {f.name for f in dataclasses.fields(comp.RepairParamHandles)}
    assert "real_superres" in field_names
    assert "real_scale" in field_names
    assert "real_backend" in field_names

    # 用带 .value 的哑对象实例化句柄，直接验证 param_values 透出新键。
    class _V:
        def __init__(self, v):
            self.value = v

    handles = comp.RepairParamHandles(
        engine=_V("auto"), sensitivity=_V(0.5), strength=_V(1.0),
        denoise=_V(3), dilate=_V(4), feather=_V(4), threshold=_V(200),
        postprocess=_V(True), sharpen=_V(0.6),
        poisson_blend=_V(False), poisson_strength=_V(1.0),
        texture_transfer=_V(False), texture_amount=_V(0.6),
        artifact_reduce=_V(False), artifact_amount=_V(0.5),
        superres=_V(False), superres_amount=_V(0.6),
        real_superres=_V(True), real_scale=_V(4), real_backend=_V("esrgan"),
    )
    vals = comp.param_values(handles)
    assert vals["real_superres"] is True
    assert vals["real_scale"] == 4
    assert vals["real_backend"] == "esrgan"


# ------------------------------------------------------------------------------
# 7. i18n 真·超分键存在（中 / 英）
# ------------------------------------------------------------------------------
def test_i18n_real_superres_keys():
    i18n.set_language("zh-CN")
    for k in ("real_superres_label", "real_scale_label",
              "real_backend_label", "real_backend_info"):
        assert i18n.t(f"params.{k}") != f"params.{k}", f"params.{k} 缺失（zh-CN）"
    assert i18n.t("params.real_superres_label") == "真·超分（AI 模型放大）"
    assert i18n.t("params.real_backend_label") == "超分后端"

    i18n.set_language("en-US")
    for k in ("real_superres_label", "real_scale_label",
              "real_backend_label", "real_backend_info"):
        assert i18n.t(f"params.{k}") != f"params.{k}", f"params.{k} 缺失（en-US）"
    assert i18n.t("params.real_superres_label") == "Real Super-Res (AI Model Upscale)"

    # 复位为默认语言，避免影响其它用例。
    i18n.set_language(config.UI_LANGUAGE)


def test_resolve_backend_accepts_string_kind(monkeypatch):
    """阶段 34：_resolve_backend 接受字符串后端类型并透传给工厂。"""
    from services import postprocess_service

    captured = {}
    def fake_get(kind, model_path="", model_dir=""):
        captured["kind"] = kind
        return None
    monkeypatch.setattr(postprocess_service, "get_superres_backend", fake_get)

    postprocess_service._resolve_backend("realesrgan", "", "")
    assert captured.get("kind") == "realesrgan", f"字符串类型应透传，实际 {captured}"

    # None 类型回落到 config 默认（与视频场景一致）。
    postprocess_service._resolve_backend(None, "", "")
    assert captured.get("kind") == config.REAL_SUPERRES_BACKEND

