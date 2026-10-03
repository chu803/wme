# -*- coding: utf-8 -*-
"""
阶段 18 结果后处理与画质增强 · 单元测试。

覆盖：
  · _color_match  : 把被修复区颜色统计向周围原图对齐（色偏收窄）。
  · _sharpen      : unsharp mask 轻量锐化（amount>0 改变结果，=0 透传）。
  · _edge_blend   : 边界羽化混合（不报错，输出同尺寸）。
  · postprocess   : 总开关关闭时原样透传；缺原图/掩码仅锐化不报错。
  · evaluate_improvement : 对比「优化前/后」质量分，返回关键字段与 delta。
"""
import os
import tempfile

import numpy as np
import cv2
import pytest

from services.postprocess_service import (
    postprocess,
    evaluate_improvement,
    _color_match,
    _sharpen,
    _edge_blend,
    _artifact_reduce,
    _texture_transfer,
    _poisson_blend,
    _superres,
)


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


# 环境前提：postprocess(superres=True) 的「形状不变」断言仅在真实超分不可用
# （无 torch → 退化为轻量超分/保持形状）时成立；torch 就绪后会真实放大。
_TORCH_PRESENT = _torch_available()


def test_artifact_reduce_runs_and_keeps_shape():
    """伪影抑制：掩码区内双边滤波，不报错、输出同尺寸。"""
    orig, mask = _synth()
    result = orig.copy()
    cv2.rectangle(result, (60, 60), (240, 140), (205, 205, 205), -1)
    out = _artifact_reduce(result, mask, amount=0.6)
    assert out.shape == result.shape
    assert out.dtype == np.uint8
    # amount=0 应原样返回。
    same = _artifact_reduce(result, mask, amount=0.0)
    assert np.array_equal(same, result)


def test_texture_transfer_injects_external_detail_only():
    """纹理重建：仅注入原图外部高频细节，不把水印区细节注回。"""
    orig, mask = _synth()
    result = orig.copy()
    cv2.rectangle(result, (60, 60), (240, 140), (205, 205, 205), -1)
    out = _texture_transfer(result, mask, orig, amount=0.6)
    assert out.shape == result.shape
    # 掩码外（原图背景）应保持不变（细节来源也是背景，叠加后≈原值）。
    outside = mask == 0
    assert np.allclose(out[outside], orig[outside], atol=2)


def test_poisson_blend_runs_and_edge_safe():
    """Poisson 融合：普通掩码融合成功；贴边掩码安全回退不抛异常。"""
    orig, mask = _synth()
    result = orig.copy()
    cv2.rectangle(result, (60, 60), (240, 140), (205, 205, 205), -1)
    out = _poisson_blend(result, mask, orig, strength=1.0)
    assert out.shape == result.shape

    # 贴边掩码（左上角）——不应抛异常。
    edge_mask = np.zeros((200, 300), dtype=np.uint8)
    cv2.rectangle(edge_mask, (0, 0), (40, 40), 255, -1)
    edge_raw = orig.copy()
    cv2.rectangle(edge_raw, (0, 0), (40, 40), (210, 210, 210), -1)
    safe = _poisson_blend(edge_raw, edge_mask, orig, strength=1.0)
    assert safe.shape == edge_raw.shape


def test_superres_runs_and_amount_zero_passthrough():
    """轻量超分：形状一致；amount=0 等同关闭不改变结果。"""
    orig, mask = _synth()
    result = orig.copy()
    cv2.rectangle(result, (60, 60), (240, 140), (205, 205, 205), -1)
    out = _superres(result, mask, orig, amount=0.8)
    assert out.shape == result.shape
    assert out.dtype == np.uint8

    off = _superres(result, mask, orig, amount=0.0)
    assert np.array_equal(off, result), "超分强度=0 应原样返回"


@pytest.mark.skipif(_TORCH_PRESENT, reason="torch 已安装，真实超分后端存在（会放大），跳过「形状不变」断言")
def test_postprocess_superres_wired_and_default_off():
    """postprocess：superres 开启时形状一致；默认关闭（config=False）不改变结果。"""
    orig, mask = _synth()
    raw = orig.copy()
    cv2.rectangle(raw, (60, 60), (240, 140), (215, 215, 215), -1)

    on = postprocess(raw, original_rgb=orig, mask=mask,
                     superres=True, superres_amount=0.8)
    assert on.shape == raw.shape

    # 默认（关闭）应与不开 superres 的结果一致。
    disabled = postprocess(raw, original_rgb=orig, mask=mask)
    explicit_off = postprocess(raw, original_rgb=orig, mask=mask,
                               superres=False, superres_amount=0.8)
    assert np.array_equal(disabled, explicit_off)



def _save(arr: np.ndarray) -> str:
    """把 RGB 数组写成临时 PNG，返回路径。"""
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(f.name, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    f.close()
    return f.name


def _synth() -> "tuple[np.ndarray, np.ndarray]":
    """造一张「底图 + 水印块」原图，并返回 (原图, 掩码)。"""
    h, w = 200, 300
    base = np.full((h, w, 3), 240, dtype=np.uint8)
    orig = base.copy()
    cv2.rectangle(orig, (60, 60), (240, 140), (200, 200, 200), -1)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(mask, (60, 60), (240, 140), 255, -1)
    return orig, mask


def test_color_match_narrows_deviation():
    """
    _color_match 把被修复区统计向「周围原图」（mask==0，即背景）对齐。

    构造：均匀背景 200，水印块（150）位于掩码内；修复后填充区略有色偏
    （215，偏亮）。色彩还原应把填充区从 215 拉向周围背景 ≈200，收窄偏离。
    """
    h, w = 200, 300
    orig = np.full((h, w, 3), 200, dtype=np.uint8)
    cv2.rectangle(orig, (60, 60), (240, 140), (150, 150, 150), -1)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(mask, (60, 60), (240, 140), 255, -1)
    # 修复后：背景保持 200，填充区有轻微色偏（215 偏亮）。
    result = np.full((h, w, 3), 200, dtype=np.uint8)
    cv2.rectangle(result, (60, 60), (240, 140), (215, 215, 215), -1)

    out = _color_match(result, mask, orig, strength=0.8)
    inside = mask > 0
    before = float(result[inside][:, 0].mean())
    after = float(out[inside][:, 0].mean())
    # 色彩还原应把填充区拉向周围环境（≈200），偏离收窄。
    assert after < before, f"色彩还原应拉向周围环境，实际 {before} -> {after}"
    assert out.shape == result.shape


def test_sharpen_changes_with_amount_and_passthrough_at_zero():
    """amount>0 改变结果；amount=0 原样返回。"""
    orig, _ = _synth()
    # 先用轻微模糊的图作为「待锐化」输入。
    blurred = cv2.GaussianBlur(orig, (0, 0), sigmaX=2.0)
    out = _sharpen(blurred, amount=0.6)
    assert out.shape == blurred.shape
    # 锐化应在一定程度上拉开与模糊图的差（拿回高频）。
    diff = float(np.abs(out.astype(int) - blurred.astype(int)).mean())
    assert diff > 0, "锐化应改变像素值"

    same = _sharpen(blurred, amount=0.0)
    assert np.array_equal(same, blurred), "amount=0 应原样返回"


def test_edge_blend_runs_and_keeps_shape():
    """边界羽化不报错、不改变输出尺寸（与周围柔和过渡）。"""
    orig, mask = _synth()
    result = orig.copy()
    cv2.rectangle(result, (60, 60), (240, 140), (205, 205, 205), -1)
    out = _edge_blend(result, mask, orig, band=3)
    assert out.shape == result.shape
    assert out.dtype == np.uint8


def test_postprocess_master_switch_off_passthrough():
    """总开关关闭时原样透传，零额外开销。"""
    orig, mask = _synth()
    raw = orig.copy()
    cv2.rectangle(raw, (60, 60), (240, 140), (215, 215, 215), -1)
    out = postprocess(raw, original_rgb=orig, mask=mask, enable=False)
    assert np.array_equal(out, raw), "关闭总开关后不应改变结果"


def test_postprocess_missing_geom_sharpen_only():
    """缺原图/掩码时跳过几何两步，仅锐化且不报错。"""
    _, _ = _synth()
    raw = np.full((200, 300, 3), 128, dtype=np.uint8)
    out = postprocess(raw, original_rgb=None, mask=None)
    assert out.shape == raw.shape
    assert out.dtype == np.uint8


def test_evaluate_improvement_returns_keys_and_delta():
    """对比优化前后质量分，返回关键字段且 delta 已计算。"""
    orig, mask = _synth()
    raw = np.full_like(orig, 240)
    cv2.rectangle(raw, (60, 60), (240, 140), (215, 215, 215), -1)
    improved = postprocess(raw, original_rgb=orig, mask=mask)

    rep = evaluate_improvement(orig, mask, raw, improved)
    for key in ("score_before", "score_after", "delta", "better",
                "detail", "level_before", "level_after"):
        assert key in rep, f"缺少字段 {key}"
    assert rep["delta"] == round(rep["score_after"] - rep["score_before"], 1)
    assert isinstance(rep["better"], bool)


def test_config_superres_snapshot_present():
    """阶段 29：新增超分配置键存在于快照，且强度在合法区间。"""
    import config as _cfg
    snap = _cfg.get_config_snapshot()
    assert "后处理·轻量超分" in snap
    assert "后处理·轻量超分强度" in snap
    assert _cfg.POSTPROCESS_SUPERRES_ENABLED is False
    assert 0.0 <= _cfg.POSTPROCESS_SUPERRES_AMOUNT <= 1.0
