"""阶段 71 · 导出侧透明通道保真与往返保真的回归测试。

背景（docs/71-*）：修复结果在写盘前还要过一次编码器，这层此前从未被审计。实测缺陷：

  **透明通道被静默丢弃并暴露隐藏 RGB**——`save_image` 此前只在「PNG 且 PNG_KEEP_ALPHA」
  时写 4 通道，其余情况直接把原始 RGB 写出。而真实 PNG 的透明区域常驻留不可见 RGB
  （例如被擦掉 Logo 的原色），于是导出 JPG/WebP/TIFF 后「隐藏内容」以彩色块形式显形
  （实测透明块变红块）。且 **WebP/TIFF 的 cv2 编码器本可携带 alpha**（实测 4 通道
  原样保留）却被无谓丢弃；`PNG_KEEP_ALPHA` 的注释早先声称「False：透明区域统一填充
  为白色」，实现从未如此——与阶段 70 同族的「声明 ≠ 实现」。

修复：① alpha 可保留时（PNG/WEBP/TIFF，`OUTPUT_ALPHA_FORMATS`）写出 4 通道；
     ② 否则按 `OUTPUT_FLATTEN_BG_COLOR` 合成到背景（`out = rgb×a + bg×(1−a)`），
        绝不直接丢弃 alpha。

本测试锁定：① 支持 alpha 的格式逐位保留；② JPG 合成到背景且**不暴露隐藏 RGB**（缺陷护栏）；
③ PNG/TIFF 往返无损（逐位）；④ JPG/WebP q95 往返保真下限；⑤ 关断与尺寸不匹配的边界行为。
"""
import os
import sys

import cv2
import numpy as np
import pytest
from PIL import Image

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core import image_utils as iu  # noqa: E402

#: 透明区域里「本不该被看见」的 RGB（缺陷护栏：导出后不得显形）。
_HIDDEN_RGB = (220, 20, 20)
_BG = (255, 255, 255)


def _hidden_alpha_image():
    """一张「透明区驻留隐藏 RGB」的图：红块 + 全零 alpha（真实 PNG 的典型形态）。"""
    rgb = np.full((60, 80, 3), 200, np.uint8)
    rgb[10:40, 10:40] = _HIDDEN_RGB
    alpha = np.full((60, 80), 255, np.uint8)
    alpha[10:40, 10:40] = 0
    return rgb, alpha


def _psnr(a, b):
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 10.0 * np.log10(255.0 ** 2 / mse) if mse > 1e-12 else 100.0


# ---------------------------------------------------------------------------
# 1. 支持 alpha 的格式：写出 4 通道、逐位保留
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["PNG", "WEBP", "TIFF"])
def test_alpha_preserved_for_capable_formats(tmp_path, fmt):
    """PNG/WebP/TIFF 的 cv2 编码器都能写 4 通道：alpha 必须逐位保留。"""
    rgb, alpha = _hidden_alpha_image()
    p = str(tmp_path / f"out.{fmt.lower()}")
    iu.save_image(p, rgb, fmt=fmt, alpha=alpha)
    im = Image.open(p)
    assert im.mode == "RGBA", f"{fmt} 应保留透明通道，实际 {im.mode}"
    back = np.array(im)
    assert np.array_equal(back[:, :, 3], alpha), f"{fmt} alpha 内容被改写"
    # 隐藏 RGB 仍在原位（被 alpha 隐藏，属正常存储；不得被合成交换）。
    # WEBP 为有损编码，允许 ±1 量化噪声（该区域本就被 alpha 遮蔽，视觉无关）。
    tol = 1 if fmt == "WEBP" else 0
    assert np.max(np.abs(back[20, 20, :3].astype(int) - np.array(_HIDDEN_RGB))) <= tol


# ---------------------------------------------------------------------------
# 2. 不支持 alpha 的格式：合成到背景，隐藏 RGB 不得显形（缺陷护栏）
# ---------------------------------------------------------------------------

def test_jpg_composites_over_background(tmp_path):
    """导出 JPG：透明区必须是背景色（白），而**不是**隐藏 RGB 显形。"""
    rgb, alpha = _hidden_alpha_image()
    p = str(tmp_path / "out.jpg")
    iu.save_image(p, rgb, fmt="JPG", alpha=alpha, quality=100)
    back = np.array(Image.open(p).convert("RGB"))
    assert back.shape[:2] == rgb.shape[:2]
    # 透明区中心 = 背景色（允许 JPEG 编码 ±2 噪声）。
    assert np.max(np.abs(back[20, 20].astype(int) - np.array(_BG))) <= 2
    # 不透明区保持原样。
    assert tuple(int(v) for v in back[50, 70]) == (200, 200, 200)


def test_alpha_hidden_rgb_never_exposed(tmp_path):
    """缺陷护栏：无论什么格式，隐藏 RGB 都不得以「不透明像素」的形式显形。"""
    rgb, alpha = _hidden_alpha_image()
    for fmt in ("PNG", "JPG", "WEBP", "TIFF"):
        p = str(tmp_path / f"x.{fmt.lower()}")
        iu.save_image(p, rgb, fmt=fmt, alpha=alpha, quality=100)
        im = Image.open(p)
        if im.mode == "RGBA":
            continue                      # alpha 保留 → 隐藏 RGB 被透明度遮蔽，合法
        back = np.array(im.convert("RGB"))
        visible = back.reshape(-1, 3)
        dist = np.abs(visible.astype(int) - np.array(_HIDDEN_RGB)).sum(axis=1)
        assert int(dist.min()) > 0, f"{fmt} 把隐藏 RGB 原样写进了不透明像素"


def test_keep_alpha_disabled_composites(tmp_path, monkeypatch):
    """`PNG_KEEP_ALPHA=False`：即便 PNG 也应合成到背景（落实配置注释里承诺的语义）。"""
    rgb, alpha = _hidden_alpha_image()
    p = str(tmp_path / "out.png")
    monkeypatch.setattr(config, "PNG_KEEP_ALPHA", False)
    iu.save_image(p, rgb, fmt="PNG", alpha=alpha)
    im = Image.open(p)
    assert im.mode == "RGB"
    back = np.array(im)
    assert tuple(int(v) for v in back[20, 20]) == _BG


def test_alpha_shape_mismatch_is_ignored(tmp_path):
    """alpha 尺寸不匹配：不得崩溃，也不得误用（按无 alpha 处理并告警）。"""
    rgb, _ = _hidden_alpha_image()
    bad_alpha = np.zeros((10, 10), np.uint8)
    p = str(tmp_path / "out.png")
    iu.save_image(p, rgb, fmt="PNG", alpha=bad_alpha)
    im = Image.open(p)
    assert im.mode == "RGB"                       # alpha 被忽略 → 无透明
    assert np.array_equal(np.array(im), rgb)


# ---------------------------------------------------------------------------
# 3. 往返保真
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["PNG", "TIFF"])
def test_lossless_roundtrip(tmp_path, fmt):
    """PNG/TIFF 是无损格式：写出再读回应逐位一致。"""
    rng = np.random.default_rng(7)
    arr = rng.integers(0, 256, (80, 120, 3), dtype=np.uint8)
    p = str(tmp_path / f"rt.{fmt.lower()}")
    iu.save_image(p, arr, fmt=fmt)
    assert np.array_equal(np.array(Image.open(p).convert("RGB")), arr)


@pytest.mark.parametrize("kind", ["gradient", "textured", "photo"])
def test_jpeg_webp_quality_floor(tmp_path, kind):
    """q95 往返保真下限：JPG/WebP ≥ 40 dB（实测最小 44.5，留 4.5 dB 余量）。"""
    from scripts.detector_diag import BASE_KINDS

    base = BASE_KINDS[kind](7, w=640, h=640)
    for fmt in ("JPG", "WEBP"):
        p = str(tmp_path / f"q.{fmt.lower()}")
        iu.save_image(p, base, fmt=fmt, quality=95)
        back = np.array(Image.open(p).convert("RGB"))
        assert _psnr(base, back) >= 40.0, f"{kind}/{fmt} 往返保真跌破 40 dB"


def test_output_dimensions_preserved(tmp_path):
    """各格式的输出尺寸必须与输入一致。"""
    arr = _hidden_alpha_image()[0]
    for fmt in ("PNG", "JPG", "WEBP", "TIFF"):
        p = str(tmp_path / f"s.{fmt.lower()}")
        iu.save_image(p, arr, fmt=fmt)
        im = Image.open(p)
        assert im.size == (arr.shape[1], arr.shape[0]), f"{fmt} 尺寸被改变"


# ---------------------------------------------------------------------------
# 4. AVIF：有透明通道时不得静默丢弃（编码器缺失则降级 PNG 且合成）
# ---------------------------------------------------------------------------

def test_avif_with_alpha_never_exposes_hidden_rgb(tmp_path):
    """AVIF 路径：无论落到哪级回退，隐藏 RGB 都不得显形。"""
    rgb, alpha = _hidden_alpha_image()
    p = str(tmp_path / "out.avif")
    saved = iu.save_image(p, rgb, fmt="AVIF", alpha=alpha, quality=95)
    assert os.path.exists(saved)
    im = Image.open(saved)
    if im.mode == "RGBA":
        assert np.array_equal(np.array(im)[:, :, 3], alpha)   # alpha 保留
    else:
        back = np.array(im.convert("RGB"))
        assert np.max(np.abs(back[20, 20].astype(int) - np.array(_BG))) <= 2


def test_avif_fallback_png_flattens(tmp_path, monkeypatch):
    """强制 Pillow 路径失败 → 降级 PNG 时必须按背景色合成（不得暴露隐藏 RGB）。"""
    from unittest.mock import patch

    rgb, alpha = _hidden_alpha_image()
    p = str(tmp_path / "out.avif")
    monkeypatch.setattr(config, "PNG_KEEP_ALPHA", True)
    with patch.object(iu, "_save_avif_via_pillow", return_value=False):
        # cv2 的 AVIF 编码器在本环境同样不可用 → 走第 ③ 级 PNG 降级。
        try:
            saved = iu.save_image(p, rgb, fmt="AVIF", alpha=alpha, quality=95)
        except cv2.error:
            pytest.skip("当前 OpenCV 的 AVIF 编码器行为异常，跳过该回退分支")
    assert saved.endswith(".png"), "降级后应为 PNG"
    back = np.array(Image.open(saved).convert("RGB"))
    assert np.max(np.abs(back[20, 20].astype(int) - np.array(_BG))) <= 2
