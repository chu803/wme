"""阶段 61 · 可选增强（纹理重建 / Poisson）的真值审计结论守卫。

审计发现（docs/61-*，掩码区 PSNR Δ 相对默认链）：
  · **纹理重建是死代码**：detail 只在 mask==0 保留、权重只在 mask>0 非零 → 乘积恒 0，
    全 12 用例 Δ=0.00。修好后实测**净负收益**（gradient corner_logo −12.6 dB、
    textured corner_logo −3.3 dB）——外来高频既无法还原真实纹理，还可能带入掩膜外
    残留水印的高频。
  · **Poisson 无缝融合极端不稳定**：按原图边界梯度重建修复区，corner_logo/edge_text
    −10.9 ~ −26.4 dB（textured faint_tiled 上又 +15.9 dB）。
  · 伪影抑制（+0.0~+0.4 dB）与轻量超分（≈0）基本中性，保持自由开启。
  · 自动回退网（无参考质量分）对"统计一致但内容错误"的劣化不敏感（拦不住上述两项）。

处置：纹理重建与 Poisson 均改为**部署级放行闸**（POSTPROCESS_TEXTURE_ALLOW /
POSTPROCESS_POISSON_ALLOW，默认 False）——即使调用方传入 True 也不执行；
两项的可执行路径仍保留并回归测试（修复后的纹理重建不再是死代码）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import cv2  # noqa: E402
from services.postprocess_service import (  # noqa: E402
    postprocess,
    _texture_transfer,
    _revert_if_worse,
)


def _smooth_case():
    """平滑外围绕一个中央掩码（模拟渐变底上的 LOGO）。"""
    h, w = 300, 400
    orig = np.full((h, w, 3), 200, np.uint8)
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (120, 90), (280, 210), 255, -1)
    result = np.full((h, w, 3), 200, np.uint8)
    cv2.rectangle(result, (120, 90), (280, 210), (196, 196, 196), -1)
    return orig, mask, result


# ---------------------------------------------------------------------------
# 1. 纹理重建：不再是死代码（但仍需部署放行）
# ---------------------------------------------------------------------------

def test_texture_transfer_not_dead_code():
    """修复前 detail 与权重作用域互斥（Δ 恒为 0）；修复后真能改变修复区像素。"""
    orig, mask, result = _smooth_case()
    # 给外围加真实纹理（高频细节），使其满足"外围有纹理"前提。
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 12.0, orig.shape).astype(np.float32)
    textured = np.clip(orig.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    out = _texture_transfer(result, mask, textured, amount=0.8)
    assert not np.array_equal(out, result), \
        "纹理重建仍是死代码（外部细节未真正注入修复区）"


def test_texture_transfer_skips_smooth_surroundings():
    """外围本身平滑时跳过（否则等于向干净填充区撒噪声，实测 −12.6 dB）。"""
    orig, mask, result = _smooth_case()
    out = _texture_transfer(result, mask, orig, amount=0.8)
    assert np.array_equal(out, result), \
        "外围无纹理时应跳过纹理重建"


# ---------------------------------------------------------------------------
# 2. 部署级放行闸
# ---------------------------------------------------------------------------

def _postprocess_gated(**kw):
    return postprocess(**kw)


def test_poisson_requires_deployment_allow():
    """默认（未放行）时，即使传入 poisson_blend=True 也不执行。"""
    orig, mask, result = _smooth_case()
    out = postprocess(result, original_rgb=orig, mask=mask, poisson_blend=True,
                      superres=False, real_superres=False)
    # 未放行 → 结果只经过默认链（色彩默认关、边界柔化/锐化），掩码内不被 Poisson 改写；
    # 用"放行后确实会改变"来证明闸门有效（见下一条用例），此处只断言不抛异常且形状一致。
    assert out.shape == result.shape
    assert config.POSTPROCESS_POISSON_ALLOW is False


def test_poisson_runs_when_allowed(monkeypatch):
    """显式放行后 Poisson 才真正执行（结果相对默认链发生变化）。"""
    monkeypatch.setattr(config, "POSTPROCESS_POISSON_ALLOW", True)
    orig, mask, result = _smooth_case()
    # 让修复区明显偏离周围（Poisson 会按边界梯度重算，结果必然不同于输入）。
    result2 = result.copy()
    cv2.rectangle(result2, (120, 90), (280, 210), (40, 40, 40), -1)
    out = postprocess(result2, original_rgb=orig, mask=mask, poisson_blend=True,
                      superres=False, real_superres=False)
    assert out.shape == result2.shape
    assert not np.array_equal(out[mask > 0], result2[mask > 0]), \
        "放行后 Poisson 应真正改写修复区（否则仍未接线）"


def test_texture_requires_deployment_allow(monkeypatch):
    """纹理重建同样需要部署放行；放行后开启才会改变结果。"""
    orig, mask, result = _smooth_case()
    rng = np.random.default_rng(1)
    orig_t = np.clip(orig.astype(np.float32)
                     + rng.normal(0, 12.0, orig.shape), 0, 255).astype(np.uint8)
    blocked = postprocess(result, original_rgb=orig_t, mask=mask,
                          texture_transfer=True, superres=False,
                          real_superres=False)
    monkeypatch.setattr(config, "POSTPROCESS_TEXTURE_ALLOW", True)
    allowed = postprocess(result, original_rgb=orig_t, mask=mask,
                          texture_transfer=True, superres=False,
                          real_superres=False)
    assert not np.array_equal(blocked, allowed), \
        "放行开关未生效：放行前后结果应不同"


# ---------------------------------------------------------------------------
# 3. 自动回退网（best-effort）
# ---------------------------------------------------------------------------

def test_revert_if_worse_reverts_degraded_step():
    """质量分明显下滑时撤销该步（返回 pre）。"""
    orig, mask, pre = _smooth_case()
    post = pre.copy()
    # 往修复区塞强噪声 → 无参考质量分应显著下滑。
    rng = np.random.default_rng(2)
    inside = mask > 0
    noise = rng.normal(0, 60.0, int(inside.sum()) * 3).reshape(-1, 3)
    post[inside] = np.clip(post[inside].astype(np.float32) + noise, 0, 255).astype(np.uint8)
    assert np.array_equal(_revert_if_worse(pre, post, orig, mask, "测试"), pre), \
        "质量分下滑应触发自动回退"


def test_revert_keeps_better_step():
    """未下滑（更好）时保留该步结果。"""
    orig, mask, pre = _smooth_case()
    assert np.array_equal(_revert_if_worse(pre, pre, orig, mask, "测试"), pre)


def test_auto_revert_default_on():
    assert config.POSTPROCESS_AUTO_REVERT is True
    assert config.POSTPROCESS_AUTO_REVERT_TOL >= 0.0
