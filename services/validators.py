# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/validators.py
 文件说明 : 输入校验服务层。
            负责在「真正处理」之前，对输入做合规性与安全性校验：
              · 图片：格式白名单 / 体积上限 / 解码校验 / 像素数上限
              · PDF ：格式白名单 / 体积上限 / 能否正常打开 / 页数
              · 视频：格式白名单 / 体积上限 / 能否正常解码 / 帧率与尺寸
            所有校验失败都抛出 utils.exceptions 中定义的中文异常，便于 UI 直接展示。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os
# 阶段 147：本文件在第 71 / 237 / 305 行的注解里用了 `_os.PathLike`，但**从未导入 `_os`**
#   —— 之所以一直没炸，是因为第 17 行的 `from __future__ import annotations` 让**所有注解
#   延迟为字符串、永不求值**。这类缺陷运行时完全静默，但 `typing.get_type_hints()` /
#   文档生成 / 类型检查器会立刻 `NameError`。由 `scripts/undef_scan.py`（AST 静态枚举）发现。
#   此处补 `import os as _os` 而不是把注解改成 `os.PathLike`：`_os` 是本仓库既有的
#   别名风格（`utils/path_helper.py` / `ui/tab_history.py` 同），改动面更小。
import os as _os
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path。
# ------------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from dataclasses import dataclass
from typing import Optional

import numpy as np

import config
from core.image_utils import (
    ImageMeta,
    _reject_animated,
    get_image_meta,
    read_image_flags,
)
from utils.exceptions import (
    UnsupportedFormatError,
    CorruptedImageError,
    ImageTooLargeError,
    UnsupportedMediaError,
    CorruptedMediaError,
    MediaTooLargeError,
)
from utils.logger import get_logger
from utils.path_helper import (
    is_supported_input,
    is_supported_pdf_input,
    is_supported_video_input,
)

log = get_logger("services.validators")


def _require_cv2():
    """延迟导入 cv2，避免无 OpenCV 环境下导入即报错。"""
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "缺少 OpenCV 依赖（cv2）。请先执行：pip install -r requirements.txt"
        ) from exc
    return cv2


def validate_image_file(
    path: str | _os.PathLike,
    *,
    max_mb: Optional[int] = None,
    max_pixels: Optional[int] = None,
):
    """
    校验磁盘上的图片文件是否「格式合法、体积合规、可正常解码、分辨率不超限」。

    参数说明：
        path       : 图片路径（支持中文路径）。
        max_mb     : 体积上限（MB），缺省取 config.MAX_IMAGE_FILE_MB。
        max_pixels : 像素数上限，缺省取 config.MAX_IMAGE_PIXELS。

    返回值：
        ImageMeta 实例（含 width/height/channels/mode/npixels）。

    异常：
        FileNotFoundError      : 文件不存在。
        UnsupportedFormatError : 格式不在白名单。
        ImageTooLargeError     : 体积或分辨率超限。
        CorruptedImageError    : 文件损坏、无法解码。
    """
    p = str(path)
    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到图片文件：{p}")

    # 1) 格式白名单。
    if not is_supported_input(p):
        raise UnsupportedFormatError(
            f"不支持的图片格式：{os.path.splitext(p)[1]}\n"
            f"仅支持：{', '.join(config.SUPPORTED_INPUT_FORMATS)}"
        )

    # 2) 体积上限。
    size_mb = os.path.getsize(p) / (1024 * 1024)
    limit_mb = max_mb if max_mb is not None else config.MAX_IMAGE_FILE_MB
    if size_mb > limit_mb:
        raise ImageTooLargeError(
            f"图片体积 {size_mb:.1f} MB 超过上限 {limit_mb} MB。"
            f"请压缩后再上传，或使用「批量处理」的分块能力（阶段 10）。"
        )

    # 3) 解码校验 + 4) 分辨率上限。
    cv2 = _require_cv2()
    with open(p, "rb") as f:
        raw = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise CorruptedImageError(f"图片解码失败，文件可能已损坏：{p}")

    # 阶段 70：① 明确拒绝动图（扩展名在白名单内的动图 WebP / APNG 此前会被静默截首帧）；
    #          ② 按 EXIF 方向把宽高交换后报告——加载端会把方向烘焙进像素，界面标签需一致。
    orientation, frames = read_image_flags(p)
    if frames > 1:
        _reject_animated(p, frames)
    meta = get_image_meta(img)
    if orientation in (5, 6, 7, 8):
        meta = ImageMeta(width=meta.height, height=meta.width,
                         channels=meta.channels, mode=meta.mode,
                         npixels=meta.npixels)
    limit_px = max_pixels if max_pixels is not None else config.MAX_IMAGE_PIXELS
    if meta.npixels > limit_px:
        raise ImageTooLargeError(
            f"图片像素数 {meta.npixels} 超过上限 {limit_px}。"
            f"请先缩小图片尺寸或裁剪后再处理。"
        )

    return meta


def validate_numpy_image(
    arr: np.ndarray,
    *,
    max_pixels: Optional[int] = None,
):
    """
    校验已经进入内存的 numpy 图片数组（Gradio ImageEditor 的 type="numpy" 直接给出）。

    参数说明：
        arr        : H×W×3 / H×W×4 / H×W 的数组。dtype 非 uint8 时**仅告警不拒绝**
                     （阶段 130 订正：此前 docstring 误称非 uint8 会抛 ValueError）。
        max_pixels : 像素数上限，缺省取 config.MAX_IMAGE_PIXELS。

    返回值：
        ImageMeta 实例。

    异常：
        ValueError           : 数组形态非法（非 numpy 数组，或 ndim 不在 {2, 3}）。
        ImageTooLargeError    : 分辨率超过上限。
    """
    if not isinstance(arr, np.ndarray):
        raise ValueError(f"期望 numpy 数组，实际收到：{type(arr)}")
    if arr.ndim not in (2, 3):
        raise ValueError(f"图片数组维度非法：ndim={arr.ndim}（应为 2 或 3）")
    if arr.dtype != np.uint8:
        # 允许非 uint8 但给出警告，后续 inpaint 会自行转换。
        log.warning("图片数组并非 uint8（dtype=%s），部分算法可能需转换。", arr.dtype)

    meta = get_image_meta(arr)
    limit_px = max_pixels if max_pixels is not None else config.MAX_IMAGE_PIXELS
    if meta.npixels > limit_px:
        raise ImageTooLargeError(
            f"图片像素数 {meta.npixels} 超过上限 {limit_px}。"
            f"请缩小图片尺寸后再处理。"
        )
    return meta


def validate_output_format(fmt: str) -> str:
    """
    校验导出格式是否受支持，返回标准化的大写格式名。

    参数说明：
        fmt : 用户选择的格式（如 "png" / "JPG" / "webp"）。

    返回值：
        标准化后的大写格式名（如 "PNG"）。

    异常：
        ValueError : 格式不在 SUPPORTED_OUTPUT_FORMATS 内。
    """
    upper = str(fmt).upper()
    if upper not in config.SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"不支持的导出格式：{fmt}\n"
            f"仅支持：{', '.join(config.SUPPORTED_OUTPUT_FORMATS)}"
        )
    return upper


# ==============================================================================
# 第 5 节：媒体输入校验（PDF / 视频，阶段 44 新增）
# ------------------------------------------------------------------------------
# 说明：图片走上面的 validate_image_file；PDF / 视频是「媒体文档」，解码依赖
#       PyMuPDF / OpenCV，白名单与体积上限也各不相同，单独抽两个函数，便于
#       tab_media 在处理前先做一道「预校验闸门」，把明显非法的输入挡在门外。
# ==============================================================================

@dataclass
class PdfMeta:
    """PDF 文件的基础元信息（供 UI 展示与预校验使用）。"""

    path: str
    size_mb: float
    pages: int
    title: Optional[str] = None
    encrypted: bool = False


@dataclass
class VideoMeta:
    """视频文件的基础元信息（供 UI 展示与预校验使用）。"""

    path: str
    size_mb: float
    fps: float
    frame_count: int
    width: int
    height: int


def _file_size_mb(path: str) -> float:
    """返回文件体积（MB，保留 2 位小数）。"""
    return round(os.path.getsize(path) / (1024 * 1024), 2)


def validate_pdf_file(path: str | _os.PathLike, *, max_mb: Optional[int] = None) -> PdfMeta:
    """
    校验磁盘上的 PDF 文件是否「格式合法、体积合规、可正常打开」。

    参数说明：
        path   : PDF 路径（支持中文路径）。
        max_mb : 体积上限（MB），缺省取 config.MAX_PDF_FILE_MB。

    返回值：
        PdfMeta 实例（含路径、体积、页数、标题、是否加密）。

    异常：
        FileNotFoundError       : 文件不存在。
        UnsupportedMediaError   : 格式不在白名单（非 .pdf）。
        MediaTooLargeError      : 体积超过上限。
        CorruptedMediaError     : 文件损坏、无法用 PyMuPDF 打开。
        ImportError             : 缺少 PyMuPDF 依赖（fitz）。
    """
    p = str(path)
    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到 PDF 文件：{p}")

    # 1) 格式白名单。
    if not is_supported_pdf_input(p):
        raise UnsupportedMediaError(
            f"不支持的文档格式：{os.path.splitext(p)[1]}\n"
            f"仅支持：{', '.join(config.SUPPORTED_DOC_FORMATS)}"
        )

    # 2) 体积上限。
    size_mb = _file_size_mb(p)
    limit_mb = max_mb if max_mb is not None else config.MAX_PDF_FILE_MB
    if size_mb > limit_mb:
        raise MediaTooLargeError(
            f"PDF 体积 {size_mb:.1f} MB 超过上限 {limit_mb} MB。"
            f"请先拆分章节或压缩后再上传。"
        )

    # 3) 可正常打开（过滤损坏 / 非 PDF 内容）。
    try:
        import fitz  # type: ignore  PyMuPDF
    except ImportError as exc:
        raise ImportError(
            "缺少 PyMuPDF 依赖（fitz）。请先执行：pip install -r requirements.txt"
        ) from exc

    try:
        doc = fitz.open(p)
    except Exception as exc:  # PyMuPDF 对损坏文件会抛 FitzFileDataError 等
        raise CorruptedMediaError(f"PDF 打开失败，文件可能已损坏：{p}") from exc

    try:
        pages = doc.page_count
        title = (doc.metadata or {}).get("title")
        encrypted = bool(doc.is_encrypted)
    finally:
        doc.close()

    return PdfMeta(
        path=p,
        size_mb=size_mb,
        pages=pages,
        title=title,
        encrypted=encrypted,
    )


def validate_video_file(
    path: str | _os.PathLike, *, max_mb: Optional[int] = None
) -> VideoMeta:
    """
    校验磁盘上的视频文件是否「格式合法、体积合规、可正常解码」。

    参数说明：
        path   : 视频路径（支持中文路径）。
        max_mb : 体积上限（MB），缺省取 config.MAX_VIDEO_FILE_MB。

    返回值：
        VideoMeta 实例（含路径、体积、帧率、帧数、宽高）。

    异常：
        FileNotFoundError       : 文件不存在。
        UnsupportedMediaError   : 格式不在白名单。
        MediaTooLargeError      : 体积超过上限。
        CorruptedMediaError     : 文件损坏、无法用 OpenCV 解码。
        ImportError             : 缺少 OpenCV 依赖（cv2）。
    """
    p = str(path)
    if not os.path.exists(p):
        raise FileNotFoundError(f"找不到视频文件：{p}")

    # 1) 格式白名单。
    if not is_supported_video_input(p):
        raise UnsupportedMediaError(
            f"不支持的视频格式：{os.path.splitext(p)[1]}\n"
            f"仅支持：{', '.join(config.SUPPORTED_VIDEO_FORMATS)}"
        )

    # 2) 体积上限。
    size_mb = _file_size_mb(p)
    limit_mb = max_mb if max_mb is not None else config.MAX_VIDEO_FILE_MB
    if size_mb > limit_mb:
        raise MediaTooLargeError(
            f"视频体积 {size_mb:.1f} MB 超过上限 {limit_mb} MB。"
            f"请先裁剪片段或压缩后再上传。"
        )

    # 3) 可正常解码（过滤损坏 / 非视频内容）。
    cv2 = _require_cv2()
    cap = cv2.VideoCapture(p)
    try:
        if not cap.isOpened():
            raise CorruptedMediaError(f"视频无法打开，文件可能已损坏：{p}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        cap.release()

    return VideoMeta(
        path=p,
        size_mb=size_mb,
        fps=round(fps, 3),
        frame_count=max(frame_count, 0),
        width=max(width, 0),
        height=max(height, 0),
    )


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    import tempfile

    cv2 = _require_cv2()

    # 用例 1：构造一张合法小图，写入临时文件，校验应通过。
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp.name, np.zeros((40, 40, 3), dtype=np.uint8))
    tmp.close()
    meta = validate_image_file(tmp.name)
    assert meta.npixels == 1600, "小图校验像素数错误"
    print("用例1通过：合法小图校验通过。")

    # 用例 2：损坏文件应抛 CorruptedImageError。
    bad = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    bad.write(b"not an image at all")
    bad.close()
    try:
        validate_image_file(bad.name)
        raise AssertionError("损坏文件未被识别")
    except CorruptedImageError:
        print("用例2通过：损坏文件被正确拦截。")

    # 用例 3：numpy 数组校验。
    m3 = validate_numpy_image(np.zeros((10, 10, 3), dtype=np.uint8))
    assert m3.npixels == 100
    print("用例3通过：numpy 数组校验通过。")

    # 用例 4：非 PDF 扩展名应抛 UnsupportedMediaError。
    #   注意（阶段 130 修复）：校验函数**先查文件存在性、再查格式白名单**，因此
    #   必须用「真实存在但扩展名不支持」的文件；此前传不存在的 "note.txt" 会先抛
    #   FileNotFoundError（未被 except 捕获）⇒ 自检在此崩溃（exit 1）。
    txt = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
    txt.write(b"not a pdf at all")
    txt.close()
    try:
        validate_pdf_file(txt.name)
        raise AssertionError("非 PDF 文件未被识别")
    except UnsupportedMediaError:
        print("用例4通过：非 PDF 格式被正确拦截。")

    # 用例 5：非视频扩展名应抛 UnsupportedMediaError（同样需文件真实存在）。
    mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    mp3.write(b"\x00\x01\x02")
    mp3.close()
    try:
        validate_video_file(mp3.name)
        raise AssertionError("非视频文件未被识别")
    except UnsupportedMediaError:
        print("用例5通过：非视频格式被正确拦截。")

    # 用例 6：不存在的 PDF 应抛 FileNotFoundError（存在性检查优先）。
    try:
        validate_pdf_file("missing.pdf")
        raise AssertionError("缺失文件未被识别")
    except FileNotFoundError:
        print("用例6通过：缺失文件被正确拦截。")

    os.remove(tmp.name)
    os.remove(bad.name)
    os.remove(txt.name)
    os.remove(mp3.name)
    print("validators.py 自检全部通过。")
