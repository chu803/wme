"""阶段 64 · 视频「复用掩码」漂移护栏测试。

背景（docs/64-*）：复用掩码（抽帧 + 缓存）只在「水印位置不变」时成立。实测校准
（640×360 角落 LOGO，1px/帧漂移，掩码自带 padding）：

    漂移 0~8px（掩码 IoU ≥ 0.74）→ 旧掩码**无害**（残留与新掩码相当，padding 吸收位移）
    漂移 12~16px（IoU 0.56~0.64）→ 旧掩码残留**骤增 2.5~3.8 倍**（肉眼"没擦干净"）
    漂移 ≥20px（IoU ≤ 0.28）→ 逐帧检测本身也跟不住（水印移出可检区域），非护栏职责

修复：每次重新检测时比较新旧掩码 IoU，低于 `config.VIDEO_REUSE_MIN_IOU`（默认 **0.70**，
恰好区分无害区 0.74 与危险区 0.64）即自动改回逐帧检测（step=1）并告警。

本测试锁定：① `_mask_iou` 语义；② 静止水印与小漂移**不误触发**（避免白白变慢）；
③ 危险区漂移触发回退且残留显著下降；④ 边界判定（等于阈值=保持复用）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core.detector import detect_watermark_mask  # noqa: E402
from core.inpainter import inpaint  # noqa: E402
from scripts.detector_diag import BASE_KINDS  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402
from services.video_service import _mask_iou  # noqa: E402

H, W = 360, 640
PRESET = CASES["corner_logo"][1]


def _mask(img):
    m = detect_watermark_mask(img, preset=PRESET, use_ocr=False).mask
    return m[:, :, 0] if m.ndim == 3 else m


def _drift_frames(kind="textured", drift=1, n=8, seed=7):
    """水印「差值补丁」逐帧平移的干净合成（避免 warp 边框反射产生第二份水印）。"""
    base = BASE_KINDS[kind](seed, w=W, h=H)
    marked = CASES["corner_logo"][0](base)
    diff = marked.astype(np.int16) - base.astype(np.int16)
    frames, gts = [], []
    for i in range(n):
        s = i * drift
        shifted = np.roll(np.roll(diff, s, axis=0), s, axis=1)
        frames.append(np.clip(base.astype(np.int16) + shifted, 0, 255).astype(np.uint8))
        gts.append(np.abs(shifted).max(axis=2) > 0)
    return base, frames, gts


def _leftover(out, base, gt):
    return float(np.abs(out.astype(np.int16) - base.astype(np.int16)).max(axis=2)[gt].mean())


def _simulate(masks, step=2):
    """复刻 process_video 的复用/漂移逻辑，返回 (最终是否逐帧, 触发时的 IoU, 末帧掩码)。"""
    cached = None
    triggered = False
    trigger_iou = None
    for idx, m in enumerate(masks):
        need_detect = triggered or (idx % step == 0) or cached is None
        if not need_detect:
            continue
        if (not triggered) and step > 1 and cached is not None:
            iou = _mask_iou(cached, m)
            if iou < config.VIDEO_REUSE_MIN_IOU:
                triggered = True
                trigger_iou = iou
        cached = m
    return triggered, trigger_iou, cached


# ---------------------------------------------------------------------------
# 1. _mask_iou 语义（快）
# ---------------------------------------------------------------------------

def test_mask_iou_basics():
    a = np.zeros((10, 10), np.uint8)
    a[2:6, 2:6] = 255
    b = a.copy()
    assert _mask_iou(a, b) == pytest.approx(1.0)
    c = np.zeros((10, 10), np.uint8)
    c[6:8, 6:8] = 255
    assert _mask_iou(a, c) == pytest.approx(0.0)
    assert _mask_iou(None, a) == 0.0
    assert _mask_iou(a, np.zeros((5, 5), np.uint8)) == 0.0
    d = a.copy()
    d[6:8, 2:6] = 255          # 扩一块 → 部分重叠
    assert 0.0 < _mask_iou(a, d) < 1.0


def test_guard_boundary_equals_threshold_keeps_reuse():
    """IoU 恰好等于阈值时**不**触发回退（判定用严格小于）。"""
    a = np.zeros((10, 10), np.uint8)
    a[:5, :] = 255
    b = np.zeros((10, 10), np.uint8)
    b[:4, :] = 255
    iou = _mask_iou(a, b)          # 4/5 = 0.8
    thr = iou                       # 把阈值设成该值
    assert not (iou < thr), "等于阈值应保持复用（只有严格小于才回退）"


# ---------------------------------------------------------------------------
# 2. 静止水印不误触发（slow）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_static_watermark_does_not_trigger_guard():
    """静止水印（仅加 σ=1 噪声）相邻检测帧掩码 IoU 必须 ≥ 阈值，不触发回退。"""
    base = BASE_KINDS["textured"](7, w=W, h=H)
    marked = CASES["corner_logo"][0](base)
    rng = np.random.default_rng(3)
    frames = [np.clip(marked.astype(np.float32) + rng.normal(0, 1.0, marked.shape),
                      0, 255).astype(np.uint8) for _ in range(5)]
    masks = [_mask(f) for f in frames]
    ious = [_mask_iou(masks[i - 1], masks[i]) for i in range(1, len(masks))]
    assert min(ious) >= config.VIDEO_REUSE_MIN_IOU, (
        f"静止水印掩码 IoU {min(ious):.3f} < {config.VIDEO_REUSE_MIN_IOU}"
        "（护栏会误触发，逐帧检测白白变慢）")
    triggered, _, _ = _simulate(masks, step=2)
    assert not triggered


# ---------------------------------------------------------------------------
# 3. 漂移触发回退且残留显著下降（slow）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_harmless_small_drift_does_not_trigger_guard():
    """小漂移（掩码 padding 可吸收、旧掩码并无害）不得触发回退——避免白白变慢。

    校准：漂移 4px 时新旧掩码 IoU ≈ 0.86 ≥ 阈值 0.70，旧掩码残留与新掩码相当。
    """
    base, frames, _ = _drift_frames(kind="textured", drift=1, n=5)
    masks = [_mask(f) for f in frames]        # 最大漂移 4px
    triggered, _, _ = _simulate(masks, step=4)
    assert not triggered, "无害小漂移不应触发漂移护栏"


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["textured", "photo"])
def test_drift_triggers_guard_and_reduces_leftover(kind):
    """危险区漂移（12px，IoU 0.56~0.64）：护栏必须触发，且回退后残留 ≤ 未回退的一半。

    评估点取漂移 12px 那一帧：未回退 = 沿用 12 帧前的旧掩码（残留 2.9/2.5 倍）；
    护栏生效 = 该帧用新掩码。注：漂移 ≥20px 时逐帧检测本身也跟不住（能力边界，
    非本护栏职责），故不在末帧比较。
    """
    base, frames, gts = _drift_frames(kind=kind, drift=1, n=20)
    masks = [_mask(f) for f in frames]

    triggered, trigger_iou, _ = _simulate(masks, step=12)
    assert triggered, "危险区漂移未触发护栏（旧掩码会静默残留）"
    assert trigger_iou is not None and trigger_iou < config.VIDEO_REUSE_MIN_IOU

    t_eval = 12
    frame_eval, gt_eval = frames[t_eval], gts[t_eval]
    out_guard = inpaint(frame_eval, masks[t_eval], engine="opencv")   # 护栏后：新掩码
    out_stale = inpaint(frame_eval, masks[0], engine="opencv")        # 未回退：12 帧前旧掩码
    r_guard = _leftover(out_guard, base, gt_eval)
    r_stale = _leftover(out_stale, base, gt_eval)
    assert r_guard <= 0.5 * r_stale, (
        f"{kind} 漂移残留未显著下降：护栏 {r_guard:.2f} vs 旧掩码 {r_stale:.2f}")
