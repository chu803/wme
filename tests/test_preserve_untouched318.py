# -*- coding: utf-8 -*-
"""阶段 318 ·「只动水印区」`POSTPROCESS_PRESERVE_UNTOUCHED` 守卫。

背景（三条实测串成的因果链）：
  · 阶段 314b：关后处理改动 5.05%，开默认 35.05%；
  · 阶段 316：原分辨率真实图上「水印**外**改动 46.9%–88.4%」；
  · 阶段 317：误伤 33.8%–72.7% **与选了哪个预设无关** ⇒ 来自后处理，不来自擦除。
根因不是 bug，而是阶段 60 的裁决「掩码内不锐化、掩码外照常锐化」。本开关提供另一种契约：
**掩码 + 外扩带之外，输出与原图逐字节一致**；默认关闭 ⇒ 现行交付观感零变化。

判据要点：`bit-exact`（不是"差不多"），且**必须证明构造成功**——开关关着时带外确实会变，
否则"变一样了"这条断言是空的（LESSONS #295 哑巴守卫）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
import services.postprocess_service as pps  # noqa: E402

SIZE = 160
MX, MY, MW, MH = 60, 70, 30, 24      # 掩码落点（离四边都远，边界效应不干扰判据）


def _textured(seed: int = 3) -> np.ndarray:
    """高频噪声底图——保证 unsharp 锐化**必然**改动绝大多数像素。"""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(SIZE, SIZE, 3), dtype=np.uint16).astype(np.uint8)


def _mask() -> np.ndarray:
    m = np.zeros((SIZE, SIZE), np.uint8)
    m[MY:MY + MH, MX:MX + MW] = 255
    return m


def _call(src, original, mask, monkeypatch, on: bool, band: int | None = None):
    monkeypatch.setattr(config, "POSTPROCESS_PRESERVE_UNTOUCHED", on, raising=True)
    if band is not None:
        monkeypatch.setattr(config, "POSTPROCESS_PRESERVE_BAND_PX", band, raising=True)
    return pps.postprocess(src.copy(), original_rgb=original, mask=mask)


def test_default_is_on_by_product_decision():
    """默认 = **开**（2026-10-23 产品决策）。

    318 落地时默认关（只加能力），依据是 60 用例 A/B：误伤中位 50% → 0.8–2.1%、
    0/60 擦除度变差、60/60 ΔPSNR 改善 2.2–3.7 dB、耗时 0.99×。
    产品定位是「去水印」，不是「顺手美化整张图」⇒ 把「你没要求的像素不动」作为默认承诺。
    想要旧的「整图轻微锐化」观感，可在设置页关掉，或 `WME_POSTPROCESS_PRESERVE_UNTOUCHED=false`。
    """
    assert config.POSTPROCESS_PRESERVE_UNTOUCHED is True, (
        "默认被改回去了：翻这个默认属对外可见决策，须先有 A/B 判决（见 docs/318 §五）")


def test_outside_band_is_bit_exact_when_on(monkeypatch):
    src = _textured()
    original = src.copy()
    mask = _mask()

    # 先证明构造成功：开关关着时，带外**必须**被后处理改动（否则下面的"相等"是空断言）
    off = _call(src, original, mask, monkeypatch, on=False)
    keep = pps._preserve_band(mask > 0, config.POSTPROCESS_PRESERVE_BAND_PX)
    outside = ~keep
    assert outside.any()
    n_changed_off = int((off[outside] != original[outside]).any(axis=1).sum())
    assert n_changed_off > 0.5 * int(outside.sum()), (
        f"反例构造失败：默认后处理只改了带外 {n_changed_off} 像素，"
        f"本用例将失去意义（底图需保持高频）")

    on = _call(src, original, mask, monkeypatch, on=True)
    assert np.array_equal(on[outside], original[outside]), (
        "开关开启后带外仍有像素被改动 ⇒ 契约（逐字节一致）未成立")


def test_inside_band_still_gets_postprocess_result(monkeypatch):
    """保护带内必须**仍走后处理**——否则「只动水印区」会把修复成果一起还原掉。"""
    src = _textured()
    original = src.copy()
    mask = _mask()
    off = _call(src, original, mask, monkeypatch, on=False)
    on = _call(src, original, mask, monkeypatch, on=True)
    keep = pps._preserve_band(mask > 0, config.POSTPROCESS_PRESERVE_BAND_PX)
    assert np.array_equal(on[keep], off[keep]), (
        "带外还原顺手改了带内像素，A/B 时 gain 会被误伤轴污染")


def test_band_zero_restores_the_ring_that_band_eight_keeps(monkeypatch):
    """带宽 0 ⇒ 紧贴掩码外的一圈也还原；带宽 8 ⇒ 同一圈仍走后处理。

    这条同时钉住两件事：带宽真的在起作用，且 `keep` 的方向没写反
    （写反成「保留带外、还原带内」会把修复成果抹掉，而上面几条用例全都会"绿"）。
    """
    src = _textured(seed=7)
    original = src.copy()
    mask = _mask()
    inside = mask > 0
    ring = pps._preserve_band(inside, 8) & ~inside      # 掩码外 1~8px 的一圈
    assert ring.any()

    off = _call(src, original, mask, monkeypatch, on=False)
    assert int((off[ring] != original[ring]).any(axis=1).sum()) > 0.5 * int(ring.sum()), (
        "反例构造失败：这一圈未被默认后处理改动，带宽判据将失去意义")

    b0 = _call(src, original, mask, monkeypatch, on=True, band=0)
    assert np.array_equal(b0[ring], original[ring]), "带宽 0 未还原紧邻掩码外的一圈"
    b8 = _call(src, original, mask, monkeypatch, on=True, band=8)
    assert np.array_equal(b8[ring], off[ring]), "带宽 8 未把这一圈留给后处理（keep 方向可能反了）"


def test_missing_original_or_mask_skips_the_restore_step(monkeypatch):
    """缺原图或缺掩码 ⇒ **还原这一步**跳过（其余后处理照常），与关闭开关时逐字节一致。"""
    src = _textured()
    monkeypatch.setattr(config, "POSTPROCESS_PRESERVE_UNTOUCHED", False, raising=True)
    ref_no_orig = pps.postprocess(src.copy(), original_rgb=None, mask=_mask())
    ref_no_mask = pps.postprocess(src.copy(), original_rgb=src, mask=None)
    monkeypatch.setattr(config, "POSTPROCESS_PRESERVE_UNTOUCHED", True, raising=True)
    assert np.array_equal(pps.postprocess(src.copy(), original_rgb=None, mask=_mask()),
                          ref_no_orig), "缺原图时还原步骤不是 no-op"
    assert np.array_equal(pps.postprocess(src.copy(), original_rgb=src, mask=None),
                          ref_no_mask), "缺掩码时还原步骤不是 no-op"


def test_shape_mismatch_skips_the_restore_step(monkeypatch):
    """原图与结果形状不符 ⇒ no-op（与关闭开关时逐字节一致），且不得改动作大小。"""
    src = _textured()
    small = src[:80, :80]
    monkeypatch.setattr(config, "POSTPROCESS_PRESERVE_UNTOUCHED", False, raising=True)
    ref = pps.postprocess(src.copy(), original_rgb=small, mask=_mask())
    monkeypatch.setattr(config, "POSTPROCESS_PRESERVE_UNTOUCHED", True, raising=True)
    got = pps.postprocess(src.copy(), original_rgb=small, mask=_mask())
    assert got.shape == src.shape
    assert np.array_equal(got, ref), "形状不符时不应有任何改动，实得与关闭时不同"


def test_full_frame_mask_has_nothing_to_restore(monkeypatch):
    """掩码铺满全图（阶段 316 里 a1 那种整图重绘）⇒ 无外部可还原，结果应与关闭时一致。"""
    src = _textured(seed=11)
    full = np.full((SIZE, SIZE), 255, np.uint8)
    off = _call(src, src.copy(), full, monkeypatch, on=False)
    on = _call(src, src.copy(), full, monkeypatch, on=True)
    assert np.array_equal(on, off)


def test_preserve_band_on_empty_mask_stays_empty():
    """`cv2.dilate` 默认边界值 DBL_MAX 的陷阱：空掩码外扩后仍须为空。"""
    assert pps._preserve_band(np.zeros((SIZE, SIZE), bool), 24).sum() == 0, (
        "空掩码被外扩出像素 ⇒ 未显式 borderValue=0，会把整圈画面边缘错误保留为后处理结果")
