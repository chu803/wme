# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/image_utils.py
 文件说明 : 图像基础运算工具集：读写（兼容中文路径）、通道转换、
            掩码膨胀/羽化、尺寸对齐、大图分块与加权拼接。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【为什么不直接用 PIL/cv2 的 imread？】
   Windows 上 cv2.imread("中文路径.png") 会静默返回 None，是新手最常见坑。
   本模块统一用「读字节 → cv2.imdecode」的方式，彻底绕开路径编码问题。
==============================================================================
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from utils.exceptions import (
    CorruptedImageError,
    ImageTooLargeError,
    UnsupportedFormatError,
)
from utils.logger import get_logger

log = get_logger("core.image_utils")

# ------------------------------------------------------------------------------
# 图片解码内存缓存（阶段 16 新增）
# ------------------------------------------------------------------------------
# 同一文件在批量处理 / 导出 / 质量评估时经常被重复从磁盘解码（例如批量 _on_update
# 里 evaluate 解码 2 次、对比图又解码 2 次同一对文件）。以 (abspath, mtime_ns, size)
# 为键缓存解码结果，命中即复用，省去磁盘读取 + cv2 解码 + 通道转换的开销。
# 缓存键含修改时间与大小，文件被覆盖重写后自动失效，不会读到陈旧内容。
# 取用时返回数组副本，避免调用方修改污染缓存（保持只读语义）。
import threading as _threading
from collections import OrderedDict as _OrderedDict

_IMAGE_CACHE: "_OrderedDict[tuple, tuple]" = _OrderedDict()
_IMAGE_CACHE_LOCK = _threading.RLock()
_IMAGE_CACHE_ENABLED: "Optional[bool]" = None


def _img_cache_enabled() -> bool:
    global _IMAGE_CACHE_ENABLED
    if _IMAGE_CACHE_ENABLED is None:
        _IMAGE_CACHE_ENABLED = bool(getattr(config, "IMAGE_DECODE_CACHE_ENABLED", True))
    return _IMAGE_CACHE_ENABLED


def _img_cache_key(p: str) -> "Optional[tuple]":
    """以「绝对路径 + 修改时间(ns) + 文件大小」为键，重写后自动失效。"""
    try:
        st = os.stat(p)
    except OSError:
        return None
    return (os.path.abspath(p), st.st_mtime_ns, st.st_size)


def _img_should_cache(npixels: int) -> bool:
    cap = int(getattr(config, "IMAGE_DECODE_CACHE_MAX_PIXELS_SINGLE", 0) or 0)
    return cap <= 0 or npixels <= cap


def _img_cache_get(key: tuple) -> "Optional[tuple]":
    with _IMAGE_CACHE_LOCK:
        if key in _IMAGE_CACHE:
            val = _IMAGE_CACHE[key]
            _IMAGE_CACHE.move_to_end(key)
            return val
    return None


def _img_cache_put(key: tuple, rgb: np.ndarray, alpha: "Optional[np.ndarray]") -> None:
    max_entries = int(getattr(config, "IMAGE_DECODE_CACHE_MAX_ENTRIES", 128) or 128)
    with _IMAGE_CACHE_LOCK:
        _IMAGE_CACHE[key] = (rgb, alpha)
        _IMAGE_CACHE.move_to_end(key)
        while len(_IMAGE_CACHE) > max_entries:
            _IMAGE_CACHE.popitem(last=False)


def clear_image_cache() -> None:
    """清空图片解码缓存（测试或内存紧张时手动调用）。"""
    with _IMAGE_CACHE_LOCK:
        _IMAGE_CACHE.clear()


def read_image_flags(p: str) -> "Tuple[int, int]":
    """
    读取图片的两个「形态标志」：EXIF 方向（1~8）与帧数（≥1），阶段 70 新增。

    只读元数据、不解码像素，开销极小。任何异常（Pillow 不可用、无 EXIF、文件损坏、
    动图头解析失败）都回退为 `(1, 1)` —— **绝不因为读元数据而让加载失败**。
    返回 (orientation, frames)。
    """
    orientation, frames = 1, 1
    try:
        from PIL import Image

        with Image.open(p) as im:
            try:
                orientation = int(im.getexif().get(0x0112, 1) or 1)
            except Exception:
                orientation = 1
            try:
                frames = int(getattr(im, "n_frames", 1) or 1)
            except Exception:
                frames = 1
    except Exception:      # pragma: no cover - 取决于 Pillow/文件
        return 1, 1
    if not (1 <= orientation <= 8):
        orientation = 1
    if not config.IMAGE_APPLY_EXIF_ORIENTATION:
        orientation = 1
    return orientation, max(1, frames)


def read_icc_profile(p: str) -> "Optional[bytes]":
    """
    读取图片内嵌的 ICC 色彩配置（阶段 73 新增）。

    背景：`cv2.imdecode` **会丢弃 ICC**——Display-P3 / AdobeRGB 等宽色域输入经
    「读 → 修 → 写」后颜色配置丢失，在不支持色彩管理的查看器里会**偏色**。
    这里用 Pillow 只读元数据（不解码像素，开销极小）。

    任何异常（Pillow 不可用、无 ICC、文件损坏）都返回 None —— **绝不因为读
    色彩配置而让加载失败**（无 ICC 的 sRGB 图是绝大多数场景）。
    """
    try:
        from PIL import Image

        with Image.open(p) as im:
            data = im.info.get("icc_profile")
        if isinstance(data, (bytes, bytearray)) and data:
            return bytes(data)
    except Exception:      # pragma: no cover - 取决于 Pillow/文件
        pass
    return None


def read_exif_orientation(p: str) -> int:
    """只取 EXIF 方向（`read_image_flags` 的薄封装）。"""
    return read_image_flags(p)[0]


def _reject_animated(p: str, frames: int) -> None:
    """
    明确拒绝动图（阶段 70）。

    动图（GIF 本就不在白名单，但 **动图 WebP / APNG** 的扩展名在白名单内）此前会被
    `cv2.imdecode` **静默截取首帧**——用户以为整段动画被处理，实际只处理了一帧、导出的
    还是静态图。与 GIF 的既有处置保持一致：明确拒绝并给出可操作建议，而不是静默降级。
    """
    raise UnsupportedFormatError(
        f"检测到动图（共 {frames} 帧），本工具只处理静态图片。\n"
        f"请先导出其中一帧为 PNG/JPG 后再试（例如用「画图」「照片」应用另存为 PNG，"
        f"或用截图工具截取目标画面）。"
    )


def _apply_orientation(
    arr: np.ndarray, orientation: int
) -> np.ndarray:
    """
    按 EXIF 方向标签变换数组（阶段 70）。

    EXIF 的 8 个取值与变换的对应关系（与 Pillow 的 `ImageOps.exif_transpose` 一致，
    由 `tests/test_input_normalization.py` 逐一对比锁定）：
        1 不变 / 2 左右镜像 / 3 旋转 180° / 4 上下镜像 /
        5 主对角线转置 / 6 顺时针 90° / 7 副对角线转置 / 8 逆时针 90°
    """
    if orientation <= 1 or orientation > 8:
        return arr
    cv2 = _require_cv2()
    if orientation == 2:
        return cv2.flip(arr, 1)
    if orientation == 3:
        return cv2.rotate(arr, cv2.ROTATE_180)
    if orientation == 4:
        return cv2.flip(arr, 0)
    if orientation == 5:
        return cv2.transpose(arr)
    if orientation == 6:
        return cv2.rotate(arr, cv2.ROTATE_90_CLOCKWISE)
    if orientation == 7:
        return cv2.rotate(cv2.transpose(arr), cv2.ROTATE_180)
    return cv2.rotate(arr, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _normalize_bit_depth(arr: np.ndarray) -> np.ndarray:
    """
    把非 8 位整型/浮点图像线性映射到 uint8（阶段 70）。

    背景：输入白名单含 `.tif/.tiff`，16 位 PNG/TIFF 解码后是 uint16，而全链路常量都按
    8 位设计——实测修复阶段直接抛 `icvInpaint ... Unsupported format`（16 位 3 通道
    不受支持），用户在"检测看起来正常"之后才遇到硬失败。
    映射规则：uint16 → v/257（与 16 位存储的 8 位数据完全一致）；其他整型按位深线性映射；
    浮点按"最大值 ≤1 视为 0~1 归一化，否则视为 0~255"处理。已是 uint8 时原样返回。
    """
    if arr.dtype == np.uint8:
        return arr
    if not config.IMAGE_NORMALIZE_BIT_DEPTH:
        return arr
    if arr.dtype == np.uint16:
        return np.rint(arr.astype(np.float32) / 257.0).clip(0, 255).astype(np.uint8)
    if np.issubdtype(arr.dtype, np.integer):
        info = np.iinfo(arr.dtype)
        span = max(1, int(info.max) - int(info.min))
        scaled = (arr.astype(np.float32) - float(info.min)) * (255.0 / span)
        return np.clip(scaled, 0, 255).astype(np.uint8)
    if np.issubdtype(arr.dtype, np.floating):
        scale = 255.0 if float(np.nanmax(arr)) <= 1.0 + 1e-6 else 1.0
        return np.clip(arr.astype(np.float32) * scale, 0, 255).astype(np.uint8)
    log.warning("非预期的图像 dtype=%s，已按 uint8 截断处理。", arr.dtype)
    return np.clip(arr, 0, 255).astype(np.uint8)


def _decode_image_raw(p: str) -> "Tuple[np.ndarray, Optional[np.ndarray]]":
    """
    真正的解码实现：读字节 → cv2.imdecode → 规范化到 RGB(+alpha)。

    阶段 70 新增两级规范化（顺序重要：先位深、再方向）：
      ① 位深：16 位 PNG/TIFF → uint8（否则修复阶段会因 uint16 3 通道不被支持而硬失败）；
      ② 方向：按 EXIF Orientation 把方向烘焙进像素（`cv2.imdecode` 不应用 EXIF，
         且保存端不写 EXIF —— 不校正会让导出图相对输入"看起来被旋转"）。
    两级都只作用于**非 8 位 / 带方向标签**的输入，普通 uint8 PNG 走原路径，逐位不变。
    """
    cv2 = _require_cv2()
    with open(p, "rb") as f:
        data = f.read()
    raw = np.frombuffer(data, dtype=np.uint8)
    bgr = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if bgr is None:
        raise CorruptedImageError(f"图片解码失败，文件可能已损坏：{p}")
    if bgr.ndim == 2:
        rgb: Optional[np.ndarray] = cv2.cvtColor(bgr, cv2.COLOR_GRAY2RGB)
        alpha: Optional[np.ndarray] = None
    elif bgr.shape[2] == 4:
        rgba = cv2.cvtColor(bgr, cv2.COLOR_BGRA2RGBA)
        rgb, alpha = rgba[:, :, :3], rgba[:, :, 3]
    else:
        rgb, alpha = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), None
    rgb = _normalize_bit_depth(rgb)
    if alpha is not None:
        alpha = _normalize_bit_depth(alpha)
    orientation, frames = read_image_flags(p)
    if frames > 1:
        # 动图：明确拒绝（否则 cv2.imdecode 会静默只取首帧，用户以为整段被处理）。
        _reject_animated(p, frames)
    if orientation != 1:
        rgb = _apply_orientation(rgb, orientation)
        if alpha is not None:
            alpha = _apply_orientation(alpha, orientation)
    return rgb, alpha


def _load_image_cached(p: str) -> "Tuple[np.ndarray, Optional[np.ndarray]]":
    """带解码缓存的加载，返回 (rgb, alpha)。命中缓存时返回副本。"""
    if not _img_cache_enabled():
        return _decode_image_raw(p)
    key = _img_cache_key(p)
    if key is not None:
        hit = _img_cache_get(key)
        if hit is not None:
            rgb, alpha = hit
            return rgb.copy(), (alpha.copy() if alpha is not None else None)
    rgb, alpha = _decode_image_raw(p)
    if key is not None and _img_should_cache(rgb.shape[0] * rgb.shape[1]):
        _img_cache_put(key, rgb, alpha)
    return rgb, alpha



@dataclass
class ImageMeta:
    """
    图片元数据，加载时一并采集，供 UI 展示与体积校验。

    字段说明：
        width   : 宽（像素）。
        height  : 高（像素）。
        channels: 通道数（3=RGB，4=RGBA）。
        mode    : "RGB" 或 "RGBA"。
        npixels : 总像素数（width*height）。
    """

    width: int
    height: int
    channels: int
    mode: str
    npixels: int


def _require_cv2():
    """
    惰性导入 OpenCV 并返回 cv2 模块。

    返回值：
        cv2 模块对象。

    异常：
        ImportError : 未安装 opencv-python 时抛出中文提示。
    """
    try:
        import cv2

        return cv2
    except ImportError as exc:
        raise ImportError(
            "未检测到 OpenCV 库（opencv-python）。\n"
            "请执行：pip install opencv-python-headless"
        ) from exc


def load_image(path: str | os.PathLike) -> np.ndarray:
    """
    从磁盘加载图片，返回 RGB 通道顺序的 uint8 数组（H×W×3）。

    参数说明：
        path : 图片路径（支持中文路径）。

    返回值：
        numpy 数组，shape=(H, W, 3)，dtype=uint8，通道顺序为 RGB。

    异常：
        FileNotFoundError   : 文件不存在。
        UnsupportedFormatError : 扩展名不在白名单。
        CorruptedImageError : 文件损坏无法解码。
        ImageTooLargeError  : 像素数超过 config.MAX_IMAGE_PIXELS。
    """
    p = path_helper_path(str(path))

    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到图片文件：{p}")

    # 扩展名白名单校验（安全：防止把 .exe 当图片读）。
    ext = os.path.splitext(p)[1].lower()
    if ext not in config.SUPPORTED_INPUT_FORMATS:
        raise UnsupportedFormatError(
            f"不支持的图片格式：{ext}\n"
            f"仅支持：{', '.join(config.SUPPORTED_INPUT_FORMATS)}"
        )

    # 关键：经解码缓存加载（命中则复用已解码数组，省去重复磁盘读取 + cv2 解码）。
    rgb, _alpha = _load_image_cached(p)

    meta = get_image_meta(rgb)
    if meta.npixels > config.MAX_IMAGE_PIXELS:
        raise ImageTooLargeError(
            f"图片像素数 {meta.npixels} 超过上限 {config.MAX_IMAGE_PIXELS}。"
            f"请先缩小图片或裁剪后再处理。"
        )

    return rgb


def load_image_with_alpha(path: str | os.PathLike) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """
    加载图片并同时返回 alpha 通道（若有）。

    参数说明：
        path : 图片路径。

    返回值：
        (rgb, alpha) 元组。rgb 为 H×W×3；alpha 为 H×W 单通道 uint8，
        无透明通道时返回 None。
    """
    p = path_helper_path(str(path))
    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到图片文件：{p}")

    # 经解码缓存加载（命中则复用已解码数组，含 alpha 通道）。
    return _load_image_cached(p)


def load_image_full(
    path: str | os.PathLike,
) -> "Tuple[np.ndarray, Optional[np.ndarray], Optional[bytes]]":
    """
    加载图片并同时返回 alpha 通道与内嵌 ICC 色彩配置（阶段 73 新增）。

    参数说明：
        path : 图片路径。

    返回值：
        (rgb, alpha, icc) 元组。rgb 为 H×W×3 uint8（RGB）；alpha 为 H×W uint8
        （无透明通道时 None）；icc 为原始 ICC 配置字节（无内嵌时 None）。
        ICC 的用途：写盘时随结果一并写回（`save_image(..., icc=icc)`），
        保证宽色域输入（Display-P3 等）导出后不偏色。
    """
    p = path_helper_path(str(path))
    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到图片文件：{p}")

    # 经解码缓存加载（命中则复用已解码数组，含 alpha 通道）；ICC 是纯元数据、
    # 开销可忽略，不进缓存。
    rgb, alpha = _load_image_cached(p)
    return rgb, alpha, read_icc_profile(p)


def get_image_meta(arr: np.ndarray) -> ImageMeta:
    """
    从 numpy 数组采集图片元数据。

    参数说明：
        arr : H×W×C 的 numpy 数组。

    返回值：
        ImageMeta 实例。
    """
    h, w = arr.shape[:2]
    c = arr.shape[2] if arr.ndim == 3 else 1
    mode = "RGBA" if c == 4 else "RGB"
    return ImageMeta(width=w, height=h, channels=c, mode=mode, npixels=w * h)


def _flatten_on_background(
    arr: np.ndarray, alpha: np.ndarray, bg: "Tuple[int, int, int]"
) -> np.ndarray:
    """
    把带透明通道的图像按 `out = rgb×a + bg×(1−a)` 合成到纯色背景上（阶段 71）。

    用于「目标格式不支持透明」或「用户关闭保留透明」的场景。**不能简单地丢掉 alpha**：
    PNG 的透明区域往往驻留不可见 RGB（如被擦除 Logo 的原色），丢 alpha 会让它显形。
    """
    a = (alpha.astype(np.float32) / 255.0)[:, :, None]
    bg_arr = np.array(bg, dtype=np.float32)[None, None, :]
    out = arr.astype(np.float32) * a + bg_arr * (1.0 - a)
    return np.clip(out, 0, 255).astype(np.uint8)


def _save_avif(
    path: str,
    arr: np.ndarray,
    alpha: Optional[np.ndarray],
    quality: Optional[int],
    icc: Optional[bytes] = None,
) -> str:
    """
    AVIF 安全保存：OpenCV 多数编译版本不带 AVIF 编码器，因此采用「三级回退」：
        ① 先试 cv2.imencode(".avif")（部分新版 OpenCV 支持）；
        ② 失败则用 Pillow 的 AVIF 插件（需 pillow-avif-plugin）；
        ③ 再失败则降级为 PNG 并给出告警（绝不因格式崩溃）。
    带 ICC 时（阶段 73）：cv2 编码路径无法嵌入 ICC，故**先走 Pillow**（其 AVIF
    插件支持 icc_profile）；两级都失败才降级 PNG（ICC 一并丢弃并告警）。
    """
    cv2 = _require_cv2()
    q = int(quality) if quality is not None else config.DEFAULT_OUTPUT_QUALITY
    alpha_usable = (
        alpha is not None
        and getattr(alpha, "shape", None) is not None
        and tuple(alpha.shape[:2]) == tuple(arr.shape[:2])
    )
    if (alpha_usable and config.PNG_KEEP_ALPHA) or icc:
        # 阶段 71/73：cv2 的 AVIF 编码路径不携带 alpha 与 ICC——有透明通道或带
        # ICC 时优先走 Pillow（RGBA / icc_profile）。
        if _save_avif_via_pillow(path, arr, alpha, q, icc=icc):
            return path
        log.warning("AVIF 的 Pillow 路径不可用，改为按背景色合成后编码：%s", path)
    out_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    # ① OpenCV 原生编码器（部分环境支持；注意：该路径不带 alpha 与 ICC）。
    flag = getattr(cv2, "IMWRITE_AVIF_QUALITY", None)
    params = [flag, q] if flag is not None else []
    try:
        ok, buf = cv2.imencode(".avif", out_bgr, params)
    except cv2.error:  # 不支持的格式会抛错，而非返回 ok=False。
        ok, buf = False, None
    if ok and buf is not None:
        with open(path, "wb") as f:
            f.write(buf.tobytes())
        return path

    # ② Pillow 回退（透明通道与 ICC 一并保留——由 `_save_avif_via_pillow` 统一实现）。
    if _save_avif_via_pillow(path, arr, alpha, q, icc=icc):
        return path

    # ③ 再失败则降级为 PNG（绝不因格式崩溃）；有透明通道时按背景色合成后写出。
    log.warning("AVIF 编码器不可用，降级为 PNG：%s", path)
    png_path = os.path.splitext(path)[0] + ".png"
    flat = (_flatten_on_background(arr, alpha, config.OUTPUT_FLATTEN_BG_COLOR)
            if (alpha is not None
                and tuple(alpha.shape[:2]) == tuple(arr.shape[:2])) else arr)
    cv2.imwrite(png_path, cv2.cvtColor(flat, cv2.COLOR_RGB2BGR))
    return png_path


def _save_avif_via_pillow(
    path: str,
    arr: np.ndarray,
    alpha: Optional[np.ndarray],
    quality: int,
    icc: Optional[bytes] = None,
) -> bool:
    """用 Pillow 的 AVIF 插件保存（含透明通道与 ICC），成功返回 True。"""
    try:
        from PIL import Image

        if alpha is not None and tuple(alpha.shape[:2]) == tuple(arr.shape[:2]):
            rgba = np.dstack([arr, alpha]).astype(np.uint8)
            im = Image.fromarray(rgba, mode="RGBA")
        else:
            im = Image.fromarray(arr.astype(np.uint8), mode="RGB")
        kw = {"icc_profile": icc} if icc else {}
        im.save(path, format="AVIF", quality=quality, **kw)
        log.info("AVIF 经 Pillow 保存：%s", path)
        return True
    except Exception as exc:  # noqa: BLE001 - 插件缺失/编码失败都退回上一级
        log.debug("AVIF 的 Pillow 路径不可用：%s", exc)
        return False


def _save_with_icc(
    path: str,
    out: np.ndarray,
    out_fmt: str,
    quality: Optional[int],
    icc: bytes,
) -> bool:
    """
    用 Pillow 写出并**附带 ICC 色彩配置**（阶段 73 新增）。

    背景：`cv2.imencode` 不支持嵌入 ICC——宽色域输入（Display-P3 等）的结果若不带
    原色彩配置，在不做色彩管理的查看器里会**偏色**。Pillow 的 PNG/JPEG/WEBP/TIFF
    编码器都支持 `icc_profile` 参数（实测 ICC 字节逐位往返）。

    参数 out 是 cv2 通道序（3ch=BGR / 4ch=BGRA），本函数负责转回 RGB(A)。
    成功返回 True；Pillow 不可用或编码失败返回 False（调用方回退 cv2 路径）。
    """
    cv2 = _require_cv2()
    try:
        from PIL import Image

        if out.ndim == 3 and out.shape[2] == 4:
            im = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGRA2RGBA))
        else:
            im = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        kw: dict = {}
        if out_fmt in ("JPG", "JPEG", "WEBP"):
            q = quality if quality is not None else config.DEFAULT_OUTPUT_QUALITY
            kw["quality"] = int(q)
        pil_fmt = {"JPG": "JPEG", "JPEG": "JPEG", "PNG": "PNG",
                   "WEBP": "WEBP", "TIFF": "TIFF"}.get(out_fmt)
        if pil_fmt is None:
            return False
        im.save(path, format=pil_fmt, icc_profile=icc, **kw)
        log.info("已随文件写入 ICC 色彩配置（%d 字节，%s）：%s",
                 len(icc), out_fmt, path)
        return True
    except Exception as exc:  # noqa: BLE001 - Pillow 缺失/编码失败都回退 cv2 路径
        log.debug("ICC 附加保存失败：%s", exc)
        return False


def save_image(
    path: str | os.PathLike,
    arr: np.ndarray,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    alpha: Optional[np.ndarray] = None,
    icc: Optional[bytes] = None,
) -> str:
    """
    把 RGB 数组保存到磁盘，支持中文路径与多种格式。

    参数说明：
        path    : 目标路径。扩展名决定格式；也可由 fmt 参数强制指定。
        arr     : H×W×3 的 uint8 数组（RGB）。
        fmt     : 强制格式 "PNG"/"JPG"/"WEBP"，为 None 时按扩展名推断。
        quality : 导出画质 1~100（仅 JPG/WebP 生效）。
        alpha   : 可选透明通道（H×W uint8）。目标格式支持透明（PNG/WEBP/TIFF，
                  见 `OUTPUT_ALPHA_FORMATS`）时原样保留；不支持（如 JPG）时按
                  `OUTPUT_FLATTEN_BG_COLOR` 合成到背景色——**不会把透明区的隐藏
                  RGB 直接暴露出来**（阶段 71）。
        icc     : 可选 ICC 色彩配置字节（来自 `load_image_full`，阶段 73）。
                  提供时经 Pillow 随结果一并写回（PNG/JPEG/WEBP/TIFF 均支持），
                  保证宽色域输入导出不偏色；Pillow 路径不可用时回退 cv2 路径
                  （ICC 丢弃并告警）。

    返回值：
        实际写出的文件路径（字符串）。

    异常：
        UnsupportedFormatError : 既无法从扩展名也无法从 fmt 推断格式。
    """
    cv2 = _require_cv2()
    p = str(path)
    out_fmt = fmt or os.path.splitext(p)[1].lstrip(".").upper()
    if out_fmt not in config.SUPPORTED_OUTPUT_FORMATS:
        # 兜底：不认识的格式一律按 PNG 处理，保证不丢数据。
        log.warning("未知导出格式 %s，按 PNG 处理。", out_fmt)
        out_fmt = "PNG"
        p = os.path.splitext(p)[0] + ".png"

    # AVIF 走独立的安全保存分支（OpenCV 多数版本不带 AVIF 编码器）。
    if out_fmt == "AVIF":
        return _save_avif(p, arr, alpha, quality, icc=icc)

    # 透明通道策略（阶段 71）：
    #   ① 目标格式支持 alpha（`OUTPUT_ALPHA_FORMATS`，实测 PNG/WebP/TIFF 的 cv2 编码器
    #      都能写 4 通道）且主开关 `PNG_KEEP_ALPHA` 为真 → 写出 4 通道，alpha 原样保留；
    #   ② 否则（JPG 等不支持透明的格式、或用户关闭了保留透明）→ 若确有 alpha，
    #      **先合成到背景色**（`out = rgb×a + bg×(1−a)`），绝不直接丢弃 alpha。
    #   为什么必须合成：真实 PNG 的透明区域常驻留不可见的 RGB（如被擦掉的 Logo 原色），
    #   直接丢 alpha 会把「隐藏内容」以彩色块形式显形——实测透明块导出 JPG 后变成红块。
    #   此前 WebP/TIFF 也被无谓丢弃 alpha（它们本可携带）。
    alpha_usable = (
        alpha is not None
        and arr.ndim == 3
        and getattr(alpha, "shape", None) is not None
        and tuple(alpha.shape[:2]) == tuple(arr.shape[:2])
    )
    if alpha is not None and not alpha_usable:
        log.warning("透明通道尺寸 %s 与图像 %s 不匹配，已忽略。",
                    getattr(alpha, "shape", None), arr.shape)

    if alpha_usable and config.PNG_KEEP_ALPHA and out_fmt in config.OUTPUT_ALPHA_FORMATS:
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        out = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        out[:, :, 3] = alpha
    else:
        base = (_flatten_on_background(arr, alpha, config.OUTPUT_FLATTEN_BG_COLOR)
                if alpha_usable else arr)
        out = cv2.cvtColor(base, cv2.COLOR_RGB2BGR)

    # ICC 色彩配置（阶段 73）：`cv2.imencode` 无法嵌入 ICC，带 ICC 时改走 Pillow
    # 路径（PNG/JPEG/WEBP/TIFF 均支持 icc_profile）。失败则回退下方 cv2 路径
    # （ICC 丢弃并已告警——sRGB 图本就无 ICC，不受影响）。
    if icc:
        if _save_with_icc(p, out, out_fmt, quality, icc):
            return p
        log.warning("ICC 附加失败，按无 ICC 写出（宽色域输入可能偏色）：%s", p)

    # 组装编码参数。
    params: list[int] = []
    if out_fmt == "PNG":
        params = [cv2.IMWRITE_PNG_COMPRESSION, config.PNG_COMPRESS_LEVEL]
    elif out_fmt in ("JPG", "JPEG"):
        q = quality if quality is not None else config.DEFAULT_OUTPUT_QUALITY
        params = [cv2.IMWRITE_JPEG_QUALITY, int(q)]
    elif out_fmt == "WEBP":
        q = quality if quality is not None else config.DEFAULT_OUTPUT_QUALITY
        params = [cv2.IMWRITE_WEBP_QUALITY, int(q)]
    elif out_fmt == "TIFF":
        # TIFF 压缩方式可配置（默认 LZW=5）；常量缺失时退回 OpenCV 默认。
        comp = getattr(cv2, "IMWRITE_TIFF_COMPRESSION", None)
        if comp is not None:
            params = [comp, int(config.TIFF_COMPRESSION)]

    # 关键：用 imencode 把数组编码成字节，再 write 写入文件，
    #       避免中文路径问题。
    ok, buf = cv2.imencode("." + out_fmt.lower(), out, params)
    if not ok:
        raise IOError(f"图片编码失败，格式={out_fmt}")
    with open(p, "wb") as f:
        f.write(buf.tobytes())
    return p


# ------------------------------------------------------------------------------
# 通道转换：numpy ↔ PyTorch 张量
# ------------------------------------------------------------------------------

def rgb_to_tensor(arr: np.ndarray, device_str: str = "cpu") -> "object":
    """
    把 H×W×3 的 uint8 RGB 数组转成模型输入张量 (1,3,H,W) float [0,1]。

    参数说明：
        arr        : H×W×3 uint8 数组。
        device_str : 目标设备（"cpu"/"cuda:0"）。

    返回值：
        torch.Tensor，shape=(1,3,H,W)，值域 [0,1]。
    """
    import torch

    # 归一化到 [0,1] 并调整维度顺序为 (B, C, H, W)。
    t = torch.from_numpy(arr.astype(np.float32) / 255.0)
    t = t.permute(2, 0, 1).unsqueeze(0)
    return t.to(device_str)


def tensor_to_rgb(tensor: "object") -> np.ndarray:
    """
    把模型输出张量 (1,3,H,W) float [0,1] 转回 H×W×3 uint8 RGB 数组。

    参数说明：
        tensor : shape=(1,3,H,W) 的 torch.Tensor。

    返回值：
        H×W×3 uint8 numpy 数组。
    """
    import torch

    # 先把张量搬到 CPU、脱离计算图。
    if hasattr(tensor, "detach"):
        tensor = tensor.detach()
    t = tensor.cpu().clamp(0, 1)
    # (1,3,H,W) → (3,H,W) → (H,W,3)
    arr = t.squeeze(0).permute(1, 2, 0).numpy()
    return (arr * 255.0).round().astype(np.uint8)


def mask_to_tensor(mask: np.ndarray, device_str: str = "cpu") -> "object":
    """
    把 H×W 单通道掩码（0/255 或 0/1）转成模型输入张量 (1,1,H,W) float。

    参数说明：
        mask       : H×W 灰度图，非 0 表示需要修复的区域。
        device_str : 目标设备。

    返回值：
        torch.Tensor，(1,1,H,W)，值域 0 或 1。
    """
    import torch

    m = (mask > 0).astype(np.float32)
    t = torch.from_numpy(m).unsqueeze(0).unsqueeze(0)
    return t.to(device_str)


def tensor_to_mask(tensor: "object") -> np.ndarray:
    """
    把单通道概率掩码张量转回 H×W uint8 掩码（0/255）。

    参数说明：
        tensor : (1,1,H,W) 或 (1,H,W) 张量。

    返回值：
        H×W uint8 数组，255 表示需要修复。
    """
    import torch

    t = tensor.detach().cpu()
    t = t.squeeze().numpy()
    return (np.clip(t, 0, 1) * 255).astype(np.uint8)


# ------------------------------------------------------------------------------
# 掩码后处理：膨胀、羽化、阈值
# ------------------------------------------------------------------------------

def dilate_mask(mask: np.ndarray, iterations: int = 8) -> np.ndarray:
    """
    对掩码做形态学膨胀，把水印区域向外撑大若干像素。

    参数说明：
        mask       : H×W uint8 掩码（0/255）。
        iterations : 膨胀迭代次数（近似 = 扩张像素数）。

    返回值：
        膨胀后的 H×W uint8 掩码。
    """
    if iterations <= 0:
        return mask.copy()
    cv2 = _require_cv2()
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.dilate(mask, kernel, iterations=iterations)


def feather_mask(mask: np.ndarray, ksize: int = 5) -> np.ndarray:
    """
    对掩码做高斯羽化，让修复区与原图接缝更柔和。

    参数说明：
        mask  : H×W uint8 掩码（0/255）。
        ksize : 高斯核边长，必须是正奇数；<=0 表示不羽化。

    返回值：
        羽化后的 float 掩码，值域 0~255（保留渐变，便于加权融合）。
    """
    if ksize <= 0:
        return mask.astype(np.float32)
    cv2 = _require_cv2()
    # 保证为奇数。
    if ksize % 2 == 0:
        ksize += 1
    blurred = cv2.GaussianBlur(mask.astype(np.float32), (ksize, ksize), 0)
    return blurred


def threshold_mask(mask: np.ndarray, thresh: int = 127) -> np.ndarray:
    """
    按阈值把（可能带灰阶的）掩码二值化，得到清晰修复边界。

    参数说明：
        mask  : H×W 单通道掩码（任意值域）。
        thresh: 阈值 0~255，大于等于该值的视为需修复。

    返回值：
        H×W uint8 二值掩码（0/255）。
    """
    cv2 = _require_cv2()
    _, binary = cv2.threshold(mask.astype(np.uint8), thresh, 255, cv2.THRESH_BINARY)
    return binary


# ------------------------------------------------------------------------------
# 尺寸对齐：LaMa 要求宽高为 8 的倍数
# ------------------------------------------------------------------------------

def pad_to_multiple(arr: np.ndarray, multiple: int = config.MODEL_SIZE_ALIGN) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """
    把图像（或掩码）补边到指定倍数的宽高，返回补边结果与各边补了多少。

    参数说明：
        arr     : H×W×(C) 数组。
        multiple: 对齐倍数（LaMa 用 8）。

    返回值：
        (padded, (top, bottom, left, right))，补边量用于事后裁回原尺寸。
    """
    h, w = arr.shape[:2]
    pad_h = (multiple - h % multiple) % multiple
    pad_w = (multiple - w % multiple) % multiple
    top = pad_h // 2
    bottom = pad_h - top
    left = pad_w // 2
    right = pad_w - left
    cv2 = _require_cv2()
    if arr.ndim == 3:
        padded = cv2.copyMakeBorder(arr, top, bottom, left, right, cv2.BORDER_REFLECT_101)
    else:
        padded = cv2.copyMakeBorder(arr, top, bottom, left, right, cv2.BORDER_REFLECT_101)
    return padded, (top, bottom, left, right)


def crop_padding(arr: np.ndarray, pad: Tuple[int, int, int, int]) -> np.ndarray:
    """
    根据 pad_to_multiple 返回的四个补边量，把数组裁回原始尺寸。

    参数说明：
        arr : 补边后的数组。
        pad : (top, bottom, left, right)。

    返回值：
        裁回原尺寸的数组。
    """
    top, bottom, left, right = pad
    h, w = arr.shape[:2]
    return arr[top:h - bottom, left:w - right]


# ------------------------------------------------------------------------------
# 大图分块：切分与加权拼接（消除接缝）
# ------------------------------------------------------------------------------

def split_into_tiles(
    image: np.ndarray,
    mask: np.ndarray,
    tile_size: int = config.TILE_SIZE,
    overlap: int = config.TILE_OVERLAP,
) -> list[dict]:
    """
    将大图与对应掩码切成若干有重叠的小块。

    参数说明：
        image     : H×W×3 原图。
        mask      : H×W 单通道掩码（0/255）。
        tile_size : 每块边长。
        overlap   : 块间重叠像素。

    返回值：
        块列表，每块为 dict：{ "img": 子图, "mask": 子掩码,
        "y": 起始行, "x": 起始列, "skip": 该块是否无需修复 }。
    """
    h, w = image.shape[:2]
    tiles: list[dict] = []
    stride = max(1, tile_size - overlap)
    y = 0
    while y < h:
        x = 0
        while x < w:
            y2 = min(y + tile_size, h)
            x2 = min(x + tile_size, w)
            tile_img = image[y:y2, x:x2]
            tile_mask = mask[y:y2, x:x2]
            # 若整块掩码全黑（没有水印），标记为可跳过。
            skip = bool(config.TILE_SKIP_EMPTY and tile_mask.max() == 0)
            tiles.append({
                "img": tile_img,
                "mask": tile_mask,
                "y": y,
                "x": x,
                "y2": y2,
                "x2": x2,
                "skip": skip,
                # 该块在 y/x 方向是否为「首块 / 末块」，用于拼接时决定权重窗是否需要在该侧衰减。
                "first_y": y == 0,
                "last_y": y2 == h,
                "first_x": x == 0,
                "last_x": x2 == w,
            })
            if x2 == w:
                break
            x += stride
        if y2 == h:
            break
        y += stride
    return tiles


def merge_tiles(
    tiles: list[dict],
    full_h: int,
    full_w: int,
    overlap: int = config.TILE_OVERLAP,
) -> np.ndarray:
    """
    把分块修复结果按「重叠区余弦权重融合」拼接回完整图，消除接缝。

    参数说明：
        tiles    : split_into_tiles 返回的块列表（需含修复后的 "result" 字段）。
        full_h   : 完整图高。
        full_w   : 完整图宽。
        overlap  : 与切分时一致的重叠像素。

    返回值：
        拼接完成的 H×W×3 数组。

    权重窗设计（关键）：
        每块在其所在维度的「首块」侧不衰减（图像边界处权重恒为 1），
        仅在「与相邻块重叠」的一侧做 0→1 / 1→0 余弦斜坡。
        相邻两块在重叠区的斜坡互为相反数式互补，逐像素权重之和恒为 1，
        因此任意像素都不会出现权重为 0 的黑洞，拼接结果严格正确。
    """
    canvas = np.zeros((full_h, full_w, 3), dtype=np.float64)
    weight = np.zeros((full_h, full_w), dtype=np.float64)

    for t in tiles:
        y, x, y2, x2 = t["y"], t["x"], t["y2"], t["x2"]
        res = t["result"].astype(np.float64)
        th, tw = res.shape[:2]
        # 边界感知的一维权重窗：仅在「非首块」侧做上升斜坡，
        # 仅在「非末块」侧做下降斜坡。
        wy = _edge_window(th, overlap, is_first=t.get("first_y", False), is_last=t.get("last_y", False))
        wx = _edge_window(tw, overlap, is_first=t.get("first_x", False), is_last=t.get("last_x", False))
        w2d = np.outer(wy, wx)  # (th, tw)
        canvas[y:y2, x:x2] += res * w2d[:, :, None]
        weight[y:y2, x:x2] += w2d

    # 理论上权重处处 ≥ 1（互补斜坡），这里仅作极端兜底。
    weight[weight <= 0] = 1.0
    merged = canvas / weight[:, :, None]
    return np.clip(merged, 0, 255).astype(np.uint8)


def _edge_window(length: int, overlap: int, is_first: bool, is_last: bool) -> np.ndarray:
    """
    生成一维边界感知权重窗，用于分块重叠区的平滑融合。

    参数说明：
        length   : 该边长度（块在该维度的尺寸）。
        overlap  : 重叠像素数。
        is_first : 该块是否为该维度的首块（True 表示贴着图像边界，左/上侧不衰减）。
        is_last  : 该块是否为该维度的末块（True 表示贴着图像边界，右/下侧不衰减）。

    返回值：
        长度为 length 的权重数组，值域 [0,1]。
        · 首块起点、末块终点恒为 1（不衰减，避免图像边缘被压暗）；
        · 仅在内部与相邻块重叠的一侧做余弦斜坡，与邻块互补。
    """
    w = np.ones(length, dtype=np.float32)
    # 衰减长度受半块尺寸限制，防止极小块时两侧斜坡打架。
    fade = max(1, min(overlap, length // 2))

    if not is_first:
        # 起点侧做 0→1 上升斜坡（与左侧相邻块的下坡互补）。
        for i in range(fade):
            w[i] = 0.5 * (1.0 - math.cos(math.pi * i / fade))
    if not is_last:
        # 终点侧做 1→0 下降斜坡（与右侧相邻块的上坡互补）。
        for i in range(fade):
            w[length - 1 - i] = 0.5 * (1.0 - math.cos(math.pi * i / fade))
    return w


# ------------------------------------------------------------------------------
# 小工具：路径标准化（供本模块内部调用，避免循环 import 配置函数）
# ------------------------------------------------------------------------------

def path_helper_path(p: str) -> str:
    """
    把路径字符串规范化（解析 ~ 与相对符号），返回绝对路径字符串。

    参数说明：
        p : 输入路径。

    返回值：
        规范化后的绝对路径字符串。
    """
    return os.path.abspath(os.path.expanduser(p))


# 自检：构造一张测试图，验证读写、分块拼接往返一致。
if __name__ == "__main__":
    cv2 = _require_cv2()
    # 造一张 600×600 的彩色图与掩码，模拟中央水印。
    test = np.zeros((600, 600, 3), dtype=np.uint8)
    test[:] = (30, 120, 200)
    mask = np.zeros((600, 600), dtype=np.uint8)
    mask[250:350, 250:350] = 255

    # 验证读写往返（中文路径）。
    tmp = os.path.join(config.TEMP_DIR, "测试_读写.png")
    save_image(tmp, test)
    loaded = load_image(tmp)
    assert loaded.shape == test.shape, "读写往返尺寸不一致"

    # 验证分块拼接：原图无修复，拼接后应与原图完全一致。
    tiles = split_into_tiles(test, mask, tile_size=256, overlap=64)
    for t in tiles:
        t["result"] = t["img"]  # 模拟「未修改」
    merged = merge_tiles(tiles, 600, 600, overlap=64)
    assert np.array_equal(merged, test), "分块拼接出现误差"

    # 验证掩码膨胀生效。
    big = dilate_mask(mask, iterations=5)
    assert big.sum() > mask.sum(), "掩码膨胀未生效"

    print(f"image_utils.py 自检通过：读写/分块/掩码处理正常，共 {len(tiles)} 块。")
