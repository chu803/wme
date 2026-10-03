"""阶段 70 · 输入形态规范化的回归测试。

背景（docs/70-*）：此前所有真值审计都在合成 uint8 PNG 上进行，**输入形态**这一层从未被审视。
实测两类真实缺陷：

① **EXIF 方向被忽略**：`cv2.imdecode` 不应用 EXIF Orientation（实测 Orientation=6 时加载到
   未旋转像素 80×120，而查看器显示 120×80；Orientation=3 时内容上下颠倒 maxdiff=197），
   且保存端不写 EXIF —— **导出图相对输入在查看器中"看起来被旋转"**；「底部小字/角标」这类
   锚定位置的判据还会在错误的边上搜索。
② **16 位图穿透**：`.tif/.tiff` 在白名单内，16 位 PNG/TIFF 解码为 uint16，而全链路常量按
   8 位设计 —— **修复阶段直接抛 `icvInpaint ... Unsupported format`（硬失败）**，用户在
   "检测看起来正常"之后才遇到错误。

修复：在加载端统一规范化（先位深、后方向；rgb 与 alpha 同步变换），并让校验层报告
校正后的宽高。本测试锁定：与 Pillow 参考逐位一致、零回归（普通 uint8 PNG 走原路径）、
端到端可用、两个开关的关断行为。
"""
import os
import sys

import cv2
import numpy as np
import pytest
from PIL import Image, ImageOps

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core import image_utils as iu  # noqa: E402
from services.validators import validate_image_file  # noqa: E402

_ALL_ORIENTATIONS = (1, 2, 3, 4, 5, 6, 7, 8)


def _asym(h=60, w=90):
    """强非对称底图：左上红块 + 右下蓝块 + 左下角十字，保证 8 种变换两两可区分。"""
    arr = np.full((h, w, 3), 200, np.uint8)
    arr[0:15, 0:20] = (230, 20, 20)
    arr[h - 15:h, w - 20:w] = (20, 20, 230)
    arr[h - 30:h - 20, 10:15] = (10, 200, 10)
    arr[h - 25:h - 15, 5:20] = (10, 200, 10)
    return arr


def _write_with_exif(path, arr, orientation):
    """按指定 EXIF 方向写入（像素保持原样，仅打标签）；PNG 无损、JPEG 贴近真实照片。"""
    im = Image.fromarray(arr)
    exif = im.getexif()
    exif[274] = orientation
    if str(path).lower().endswith((".jpg", ".jpeg")):
        im.save(path, exif=exif, quality=100, subsampling=0)
    else:
        im.save(path, exif=exif)
    return path


# ---------------------------------------------------------------------------
# 1. EXIF 方向：与 Pillow 参考逐位一致（阶段 70 核心）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext", ["png", "jpg"])
@pytest.mark.parametrize("orientation", _ALL_ORIENTATIONS)
def test_exif_orientation_matches_pil(tmp_path, orientation, ext):
    """8 个方向 × PNG/JPEG：加载结果必须与 `ImageOps.exif_transpose` 参考一致。

    PNG 为无损，可逐位断言；JPEG 有编码噪声，放宽到 ±2 逐像素容差
    （两侧走同一解码器，实际差异仅为 JPEG 量化噪声）。
    """
    arr = _asym()
    p = str(tmp_path / f"o{orientation}.{ext}")
    _write_with_exif(p, arr, orientation)
    ref = np.array(ImageOps.exif_transpose(Image.open(p)).convert("RGB"))
    got = iu.load_image(p)
    assert got.shape == ref.shape, f"{ext}/Orientation={orientation} 尺寸不一致"
    if ext == "png":
        assert np.array_equal(got, ref)
    else:
        assert np.max(np.abs(got.astype(int) - ref.astype(int))) <= 2


@pytest.mark.parametrize("orientation", _ALL_ORIENTATIONS)
def test_apply_orientation_matches_pil(tmp_path, orientation):
    """方向变换本身（不经过加载器）也必须与 Pillow 的文件级参考一致（PNG 无损）。"""
    arr = _asym()
    p = str(tmp_path / f"dir{orientation}.png")
    _write_with_exif(p, arr, orientation)
    ref = np.array(ImageOps.exif_transpose(Image.open(p)).convert("RGB"))
    got = iu._apply_orientation(arr, orientation)
    assert np.array_equal(got, ref), f"Orientation={orientation} 变换不一致"


def test_orientation_reader_without_exif(tmp_path):
    """无 EXIF 标签 / 不存在的文件都必须返回 1，绝不抛异常。"""
    plain = str(tmp_path / "plain.png")
    Image.fromarray(_asym()).save(plain)
    assert iu.read_exif_orientation(plain) == 1
    assert iu.read_exif_orientation(str(tmp_path / "missing.png")) == 1


def test_exif_switch_off_keeps_legacy(tmp_path, monkeypatch):
    """关掉开关后行为回到旧版：不校正方向（尺寸保持原始像素方向）。"""
    arr = _asym()
    p = str(tmp_path / "o6.jpg")
    _write_with_exif(p, arr, 6)
    monkeypatch.setattr(config, "IMAGE_APPLY_EXIF_ORIENTATION", False)
    got = iu.load_image(p)
    assert got.shape[:2] == arr.shape[:2], "关闭开关后不应再校正方向"


# ---------------------------------------------------------------------------
# 2. 零回归：普通 8 位无 EXIF 输入走原路径
# ---------------------------------------------------------------------------

def test_plain_uint8_png_unchanged(tmp_path):
    """最常见的输入（8 位 PNG、无 EXIF）必须逐位等于 cv2 解码结果。"""
    arr = _asym()
    p = str(tmp_path / "plain.png")
    Image.fromarray(arr).save(p)
    with open(p, "rb") as f:
        ref = cv2.cvtColor(cv2.imdecode(np.frombuffer(f.read(), np.uint8),
                                        cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)
    got = iu.load_image(p)
    assert np.array_equal(got, ref)


# ---------------------------------------------------------------------------
# 3. 位深规范化：16 位输入
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext", ["png", "tiff"])
def test_16bit_normalized_to_uint8(tmp_path, ext):
    """16 位存储的 8 位数据（v = k×257）必须精确还原为 k，且 dtype 为 uint8。"""
    arr = _asym()
    p = str(tmp_path / f"deep16.{ext}")
    assert cv2.imwrite(p, cv2.cvtColor(arr.astype(np.uint16) * 257,
                                       cv2.COLOR_RGB2BGR))
    got = iu.load_image(p)
    assert got.dtype == np.uint8
    assert np.max(np.abs(got.astype(int) - arr.astype(int))) <= 1


def test_16bit_switch_off_keeps_uint16(tmp_path, monkeypatch):
    """关掉位深开关后保持 uint16（文档化旧行为，便于排查）。"""
    arr = _asym()
    p = str(tmp_path / "deep16.png")
    assert cv2.imwrite(p, cv2.cvtColor(arr.astype(np.uint16) * 257,
                                       cv2.COLOR_RGB2BGR))
    monkeypatch.setattr(config, "IMAGE_NORMALIZE_BIT_DEPTH", False)
    assert iu.load_image(p).dtype == np.uint16


@pytest.mark.slow
def test_end_to_end_repair_works_on_16bit(tmp_path):
    """16 位输入走完「检测 → 修复」不得抛异常（修复前是 icvInpaint 硬失败）。"""
    from core.detector import detect_watermark_mask
    from core.inpainter import inpaint
    from scripts.detector_diag import BASE_KINDS
    from scripts.make_demo_assets import CASES

    base = BASE_KINDS["textured"](7, w=640, h=640)
    marked = CASES["corner_logo"][0](base)
    p = str(tmp_path / "marked16.png")
    assert cv2.imwrite(p, cv2.cvtColor(marked.astype(np.uint16) * 257,
                                       cv2.COLOR_RGB2BGR))
    arr = iu.load_image(p)
    assert arr.dtype == np.uint8
    res = detect_watermark_mask(arr, preset="ai_logo", use_ocr=False)
    mask = res.mask[:, :, 0] if res.mask.ndim == 3 else res.mask
    assert int((mask > 0).sum()) > 0
    out = inpaint(arr, mask, engine="opencv")
    assert out.dtype == np.uint8 and out.shape == arr.shape


# ---------------------------------------------------------------------------
# 4. Alpha 与方向的一致性
# ---------------------------------------------------------------------------

def test_alpha_transformed_with_orientation(tmp_path):
    """带 alpha 的 PNG：alpha 必须与 rgb 做同一方向变换（透明块位置一致）。"""
    arr = _asym()
    alpha = np.full(arr.shape[:2], 255, np.uint8)
    alpha[0:15, 0:20] = 0                      # 与左上红块同位置的全透明
    # PNG 的 EXIF 支持有限，这里直接用方向变换 + alpha 双通道一致性验证：
    rgb_o = iu._apply_orientation(arr, 6)
    a_o = iu._apply_orientation(alpha, 6)
    assert rgb_o.shape[:2] == a_o.shape[:2]
    # 透明区应仍与"左上红块"重合（红块随方向变换后位置已知）
    ys, xs = np.nonzero(a_o == 0)
    assert len(ys) > 0
    assert np.all(rgb_o[ys, xs] == np.array([230, 20, 20]))


def test_load_with_alpha_returns_alpha(tmp_path):
    """带透明区的 PNG：`load_image_with_alpha` 必须返回 alpha 且透明像素数正确。"""
    arr = _asym()
    alpha = np.full(arr.shape[:2], 255, np.uint8)
    alpha[10:30, 10:40] = 0
    p = str(tmp_path / "rgba.png")
    Image.fromarray(np.dstack([arr, alpha]), mode="RGBA").save(p)
    rgb, got_alpha = iu.load_image_with_alpha(p)
    assert got_alpha is not None
    assert int((got_alpha == 0).sum()) == int((alpha == 0).sum())
    assert rgb.shape[:2] == alpha.shape[:2]


# ---------------------------------------------------------------------------
# 5. 校验层报告校正后的宽高
# ---------------------------------------------------------------------------

def test_validator_reports_corrected_size(tmp_path):
    """EXIF=6 的 90×60 图，校验层应报告校正后的 60×90（与加载端一致）。"""
    arr = _asym(60, 90)
    p = str(tmp_path / "o6.jpg")
    _write_with_exif(p, arr, 6)
    meta = validate_image_file(p)
    assert (meta.width, meta.height) == (60, 90)
    assert iu.load_image(p).shape[:2] == (meta.height, meta.width)


def test_validator_plain_size_unchanged(tmp_path):
    """无 EXIF 的普通图，校验层宽高与像素一致。"""
    arr = _asym(60, 90)
    p = str(tmp_path / "plain.jpg")
    _write_with_exif(p, arr, 1)
    meta = validate_image_file(p)
    assert (meta.width, meta.height) == (90, 60)


# ---------------------------------------------------------------------------
# 6. 动图：明确拒绝（不得静默截取首帧）
# ---------------------------------------------------------------------------

def _write_animated_webp(path, arr):
    Image.fromarray(arr).save(path, save_all=True,
                              append_images=[Image.fromarray(255 - arr)])


def test_animated_input_rejected_by_validator(tmp_path):
    """动图 WebP 的扩展名在白名单内，但内容不受支持——必须明确拒绝而非静默降级。"""
    from utils.exceptions import UnsupportedFormatError

    p = str(tmp_path / "anim.webp")
    _write_animated_webp(p, _asym())
    with pytest.raises(UnsupportedFormatError):
        validate_image_file(p)


def test_animated_input_rejected_by_loader(tmp_path):
    """直接调用加载器（绕过校验）也必须拒绝，避免静默只取首帧。"""
    from utils.exceptions import UnsupportedFormatError

    p = str(tmp_path / "anim.webp")
    _write_animated_webp(p, _asym())
    with pytest.raises(UnsupportedFormatError):
        iu.load_image(p)


def test_read_image_flags_static(tmp_path):
    """静态图：帧数恒为 1；方向按 EXIF 读出。"""
    p = str(tmp_path / "static.png")
    _write_with_exif(p, _asym(), 1)
    assert iu.read_image_flags(p) == (1, 1)
    p6 = str(tmp_path / "o6.png")
    _write_with_exif(p6, _asym(), 6)
    assert iu.read_image_flags(p6) == (6, 1)


def test_read_image_flags_missing_file_falls_back(tmp_path):
    """文件不存在时回退 (1, 1)，绝不抛异常（元数据读取必须是软失败）。"""
    assert iu.read_image_flags(str(tmp_path / "nope.png")) == (1, 1)


# ---------------------------------------------------------------------------
# 阶段 73 · 32 位浮点 TIFF 的真值验证
# ---------------------------------------------------------------------------

def test_float32_tiff_unit_range_roundtrip(tmp_path):
    """[0,1] float32 TIFF：按  语义（×255）应**逐位**还原 uint8 真值。

    此前该形态只有"合理映射"的注释、无真值测试——本测试补上（docs/73-*）。
    构造：uint8 视觉真值 → /255 写成 float32 TIFF（cv2 对称路径：写 BGR、读 BGR）。
    """
    u8 = np.zeros((24, 40, 3), np.uint8)
    cols = np.arange(40, dtype=np.uint8) * 6
    rows = np.arange(24, dtype=np.uint8) * 10
    u8[..., 0] = cols[None, :]
    u8[..., 1] = rows[:, None]
    u8[..., 2] = 128
    f32 = u8.astype(np.float32) / 255.0
    ok, buf = cv2.imencode(".tiff", f32[..., ::-1].copy())   # cv2 期望 BGR
    assert ok
    p = tmp_path / "f32.tiff"
    p.write_bytes(buf.tobytes())
    got = iu.load_image(str(p))
    assert got.dtype == np.uint8 and got.shape == u8.shape
    assert np.array_equal(got, u8), "[0,1] 浮点 TIFF 未逐位还原"


def test_float32_tiff_full_range_roundtrip(tmp_path):
    """[0,255] float32 TIFF（max>1）：按原值截断映射，同样逐位还原。"""
    f32 = np.zeros((16, 32, 3), np.float32)
    f32[..., 0] = np.linspace(0, 255, 32, dtype=np.float32)[None, :]
    f32[..., 1] = 200.0
    f32[..., 2] = 55.5
    ok, buf = cv2.imencode(".tiff", f32[..., ::-1].copy())
    assert ok
    p = tmp_path / "f32full.tiff"
    p.write_bytes(buf.tobytes())
    got = iu.load_image(str(p))
    # 链路净效果：加载端 cvtColor 做一次 BGR<->RGB 交换，故 RGB 语义真值即 f32 本身。
    expect = np.clip(f32, 0, 255).astype(np.uint8)
    assert np.array_equal(got, expect)
