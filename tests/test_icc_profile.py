"""阶段 73 · ICC 色彩配置端到端保留测试。

背景（docs/73-*）：`cv2.imdecode` 丢弃 ICC、`cv2.imencode` 无法嵌入 ICC——Display-P3
等宽色域输入经「读 → 修 → 写」后色彩配置丢失，在不做色彩管理的查看器里偏色。
修复：加载端 `load_image_full` 返回内嵌 ICC；保存端 `save_image(..., icc=)` 经
Pillow 写回（PNG/JPEG/WEBP/TIFF 实测均支持 icc_profile）。

本文件锁定：ICC 随导出写回、未提供时不写、alpha + ICC 共存、load_image_full 语义、
save_result 透传。ICC 字节本身用 Pillow ImageCms 生成的 sRGB profile。
"""
import os
import sys

import numpy as np
import pytest
from PIL import Image, ImageCms

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core import image_utils as iu  # noqa: E402
from services import file_service  # noqa: E402


def _icc_srgb() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _rgb() -> np.ndarray:
    arr = np.zeros((40, 60, 3), np.uint8)
    arr[10:30, 15:45] = (200, 60, 40)
    return arr


# ---------------------------------------------------------------------------
# 1. save_image(icc=)：四格式全部写出 ICC
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["PNG", "JPG", "WEBP", "TIFF"])
def test_save_with_icc_embeds_profile(fmt, tmp_path):
    p = str(tmp_path / f"out.{fmt.lower()}")
    iu.save_image(p, _rgb(), fmt=fmt, quality=95, icc=_icc_srgb())
    im = Image.open(p)
    assert len(im.info.get("icc_profile", b"") or b"") > 0, f"{fmt} 未写出 ICC"


@pytest.mark.parametrize("fmt", ["PNG", "JPG", "WEBP", "TIFF"])
def test_save_without_icc_writes_none(fmt, tmp_path):
    """未提供 icc 时不写 ICC（与旧行为一致——sRGB 图不受影响）。"""
    p = str(tmp_path / f"out.{fmt.lower()}")
    iu.save_image(p, _rgb(), fmt=fmt, quality=95)
    assert not (Image.open(p).info.get("icc_profile") or b""), f"{fmt} 凭空出现 ICC"


def test_save_with_icc_pixel_content_preserved(tmp_path):
    """无损格式（PNG/TIFF）带 ICC 写出后像素仍逐位一致。"""
    for fmt in ("PNG", "TIFF"):
        p = str(tmp_path / f"out.{fmt.lower()}")
        iu.save_image(p, _rgb(), fmt=fmt, icc=_icc_srgb())
        assert np.array_equal(np.array(Image.open(p).convert("RGB")), _rgb()), fmt


def test_icc_with_alpha_coexists(tmp_path):
    """透明通道与 ICC 共存：RGBA 写出且两者都完整。"""
    rgb = _rgb()
    alpha = np.full(rgb.shape[:2], 255, np.uint8)
    alpha[10:30, 15:45] = 0
    p = str(tmp_path / "out.png")
    iu.save_image(p, rgb, fmt="PNG", alpha=alpha, icc=_icc_srgb())
    im = Image.open(p)
    assert im.mode == "RGBA"
    assert np.array_equal(np.array(im)[:, :, 3], alpha), "alpha 被改写"
    assert len(im.info.get("icc_profile", b"") or b"") > 0, "ICC 丢失"


def test_icc_with_flatten_does_not_leak_hidden_rgb(tmp_path):
    """JPG（合成到白底）+ ICC：隐藏 RGB 不得显形。"""
    rgb = _rgb().copy()
    hidden = (10, 200, 30)
    rgb[0:5, 0:5] = hidden                       # 透明区将携带的"隐藏"内容
    alpha = np.full(rgb.shape[:2], 255, np.uint8)
    alpha[0:5, 0:5] = 0
    p = str(tmp_path / "out.jpg")
    iu.save_image(p, rgb, fmt="JPG", quality=100, alpha=alpha, icc=_icc_srgb())
    back = np.array(Image.open(p).convert("RGB"))
    assert not np.any(np.all(back == np.array(hidden), axis=2)), "隐藏 RGB 显形"


# ---------------------------------------------------------------------------
# 2. load_image_full：返回 (rgb, alpha, icc)
# ---------------------------------------------------------------------------

def test_load_image_full_returns_icc(tmp_path):
    p = str(tmp_path / "src.png")
    Image.fromarray(_rgb()).save(p, icc_profile=_icc_srgb())
    rgb, alpha, icc = iu.load_image_full(p)
    assert rgb.shape == (40, 60, 3) and alpha is None
    assert icc == _icc_srgb(), "ICC 字节应逐位一致"


def test_load_image_full_without_icc(tmp_path):
    p = str(tmp_path / "src.png")
    Image.fromarray(_rgb()).save(p)
    _, _, icc = iu.load_image_full(p)
    assert icc is None


def test_load_image_full_with_alpha_and_icc(tmp_path):
    rgb = _rgb()
    alpha = np.full(rgb.shape[:2], 255, np.uint8)
    alpha[:, :20] = 0
    p = str(tmp_path / "src.png")
    Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA").save(
        p, icc_profile=_icc_srgb())
    rgb2, alpha2, icc = iu.load_image_full(p)
    assert np.array_equal(alpha2, alpha)
    assert icc == _icc_srgb()


def test_read_icc_profile_missing_file(tmp_path):
    assert iu.read_icc_profile(str(tmp_path / "nope.png")) is None


# ---------------------------------------------------------------------------
# 3. save_result 透传 + AVIF 分支
# ---------------------------------------------------------------------------

def test_save_result_accepts_icc(tmp_path, monkeypatch):
    """UI 保存入口 `save_result` 支持 icc 透传。"""
    import utils.path_helper as ph

    monkeypatch.setattr(ph, "get_output_dir", lambda: str(tmp_path))
    saved = file_service.save_result(_rgb(), "icc样例", fmt="PNG", icc=_icc_srgb())
    assert len(Image.open(saved).info.get("icc_profile", b"") or b"") > 0


def test_avif_with_icc_via_pillow_or_degrade(tmp_path):
    """AVIF + ICC：有 Pillow AVIF 插件时 ICC 随文件保留；否则降级 PNG（不崩溃）。"""
    pytest.importorskip("PIL")
    p = str(tmp_path / "out.avif")
    saved = iu.save_image(p, _rgb(), fmt="AVIF", quality=95, icc=_icc_srgb())
    assert os.path.exists(saved)
    if saved.endswith(".avif"):
        im = Image.open(saved)
        assert len(im.info.get("icc_profile", b"") or b"") > 0
    else:
        assert saved.endswith(".png")   # 降级路径（本环境无 AVIF 编码器时）
