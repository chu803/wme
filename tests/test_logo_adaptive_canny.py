"""阶段 69 · 角标通路「双阈值并集」的回归测试。

背景（docs/69-*）：`detect_logo_watermark` 用固定 Canny 双阈值 (50,150) 取轮廓。Canny
比较的是**梯度幅度绝对值**，而 WebP 等有损编码与重采样会整体压低梯度——实测 480² WebP
的角标用例梯度中位数从 18.4 降到 14.0，固定阈值下轮廓碎成 2 块、**0 块通过形状过滤**
（召回 0），而同图 PNG/JPEG 均为 100%。阶段 67 已把该组合记为「已记录边界」。

修复：在固定阈值主通路之外，**额外**用一组噪声锚定的自适应阈值（`med + K·MAD`，只降不升、
下限 30、上限即主通路值）取一次轮廓，两者**取并集**。选择并集而非替换的直接依据：
实测替换会让 640² textured/WebP 从 100% 掉到 0%（阈值降到 32/96 时真轮廓不再通过形状过滤）
——并集严格 ⊇ 主通路，对原本通过的用例零影响（阶段 67 的教训「并集恒优于替换」）。

本测试锁定：① 主通路行为不变（高梯度图不启用补充通路，逐位一致）；② 补充通路救回被平滑
削弱的用例；③ 并集严格包含主通路；④ 去重有效；⑤ 自适应阈值只在 [下限, 主通路值] 内取值；
⑥ 开关关闭时行为回到主通路。
"""
import os
import sys

import cv2
import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
from scripts.detector_diag import BASE_KINDS, gt_mask, metrics  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402

#: 阶段 67 记录的边界组合：固定阈值下召回为 0，补充通路必须把它救回。
_WEBP_LOGO_CASE = (480, "webp75", "photo")
#: 替换式修复会破坏、而并集必须保住的用例（回归护栏）。
_UNION_GUARD_CASE = (640, "webp75", "textured")


def _encode(img, codec):
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    if codec == "png":
        ok, buf = cv2.imencode(".png", bgr)
    elif codec.startswith("jpeg"):
        ok, buf = cv2.imencode(".jpg", bgr,
                               [int(cv2.IMWRITE_JPEG_QUALITY), int(codec[4:])])
    else:
        ok, buf = cv2.imencode(".webp", bgr,
                               [int(cv2.IMWRITE_WEBP_QUALITY), int(codec[4:])])
    assert ok
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def _rescale(img, side):
    h, w = img.shape[:2]
    s = side / float(max(h, w))
    if abs(s - 1.0) < 1e-6:
        return img
    interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=interp)


def _logo_case(size, codec, kind):
    """640 原生角标用例 → 缩放 + 编码；真值同几何变换。"""
    base = BASE_KINDS[kind](7, w=640, h=640)
    marked = CASES["corner_logo"][0](base)
    gt = gt_mask(base, marked)
    img = _encode(_rescale(marked, size), codec)
    return img, cv2.resize(gt, (img.shape[1], img.shape[0]),
                           interpolation=cv2.INTER_NEAREST)


def _logo_recall(img, g):
    return metrics(det.detect_logo_watermark(
        det._ensure_rgb(img), img.shape[0] * img.shape[1],
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO)[0], g)["recall"]


# ---------------------------------------------------------------------------
# 1. 补充通路：救回被平滑削弱的用例（阶段 69 核心）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_adaptive_pass_recovers_webp_logo():
    """480² WebP 角标：固定阈值 0%，补上自适应并集后必须恢复到接近满召回。"""
    size, codec, kind = _WEBP_LOGO_CASE
    img, g = _logo_case(size, codec, kind)
    assert _logo_recall(img, g) >= 0.9


@pytest.mark.slow
def test_union_guard_case_still_detected():
    """替换式修复会破坏的用例：并集修复下必须仍为满召回。"""
    size, codec, kind = _UNION_GUARD_CASE
    img, g = _logo_case(size, codec, kind)
    assert _logo_recall(img, g) >= 0.9


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["photo", "textured"])
@pytest.mark.parametrize("codec", ["png", "jpeg75", "webp75"])
def test_logo_recall_across_codecs(kind, codec):
    """三种编码在两个尺度上均须满召回（角标通路鲁棒性基线）。"""
    for size in (480, 640):
        img, g = _logo_case(size, codec, kind)
        assert _logo_recall(img, g) >= 0.9, f"{size}²/{codec}/{kind} 召回不足"


# ---------------------------------------------------------------------------
# 2. 主通路不变 + 并集语义
# ---------------------------------------------------------------------------

def test_adaptive_pair_is_none_for_high_gradient_image():
    """高梯度图像（med + K·MAD ≥ 主阈值）不启用补充通路 → 主通路行为不变。"""
    # 强纹理 + 高对比棋盘：梯度中位数远高于 50。
    board = np.zeros((200, 200, 3), np.uint8)
    board[::4, :] = 255
    board[:, ::4] = 255
    gray = cv2.cvtColor(board, cv2.COLOR_RGB2GRAY)
    assert det._canny_adaptive_pair(gray) is None


def test_adaptive_pair_within_bounds():
    """自适应阈值只允许在 [下限, 主通路值] 内取值，且 hi = 3×lo ≤ 主值。"""
    smooth = np.full((200, 200, 3), 128, np.uint8)
    smooth[80:120, 80:120] = 160          # 极弱边缘 → 触发补充通路
    gray = cv2.cvtColor(smooth, cv2.COLOR_RGB2GRAY)
    pair = det._canny_adaptive_pair(gray)
    assert pair is not None
    lo, hi = pair
    assert config.DETECT_LOGO_ADAPT_LO_MIN <= lo < config.DETECT_LOGO_CANNY_LO
    assert hi <= config.DETECT_LOGO_CANNY_HI


def test_adaptive_disabled_by_config(monkeypatch):
    """开关关闭时不做自适应（行为回到主通路）。"""
    monkeypatch.setattr(config, "DETECT_LOGO_CANNY_ADAPT", False)
    smooth = np.full((200, 200, 3), 128, np.uint8)
    smooth[80:120, 80:120] = 160
    assert det._canny_adaptive_pair(cv2.cvtColor(smooth, cv2.COLOR_RGB2GRAY)) is None


@pytest.mark.slow
def test_union_is_superset_of_primary(monkeypatch):
    """并集严格包含主通路：关闭补充通路所得的掩码 ⊆ 开启后的掩码。"""
    size, codec, kind = _WEBP_LOGO_CASE
    img, _ = _logo_case(size, codec, kind)
    rgb = det._ensure_rgb(img)
    area = rgb.shape[0] * rgb.shape[1]

    monkeypatch.setattr(config, "DETECT_LOGO_CANNY_ADAPT", False)
    base_mask, _ = det.detect_logo_watermark(
        rgb, area, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO)
    monkeypatch.setattr(config, "DETECT_LOGO_CANNY_ADAPT", True)
    full_mask, _ = det.detect_logo_watermark(
        rgb, area, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO)

    assert np.all((base_mask > 0) <= (full_mask > 0))
    assert np.count_nonzero(full_mask) >= np.count_nonzero(base_mask)


@pytest.mark.slow
def test_regions_are_deduplicated():
    """两套阈值命中的同一轮廓不得重复计入区域列表。"""
    size, codec, kind = _WEBP_LOGO_CASE
    img, _ = _logo_case(size, codec, kind)
    rgb = det._ensure_rgb(img)
    area = rgb.shape[0] * rgb.shape[1]
    _, regions = det.detect_logo_watermark(
        rgb, area, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO)
    assert regions, "并集后应有区域"
    for i, a in enumerate(regions):
        for b in regions[i + 1:]:
            ix = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
            iy = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
            inter = ix * iy
            union = a.w * a.h + b.w * b.h - inter
            assert union <= 0 or inter / float(union) <= 0.5, "存在重复区域"
