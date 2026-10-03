# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/file_service.py
 文件说明 : 文件服务层。
            统一管理「上传落盘、读取、缩略图生成、历史/缓存目录」等文件 IO。
            阶段 5 的批量处理与任务队列、阶段 2 的文件缓存都将复用本模块，
            避免各标签页各自读写路径导致的混乱。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import shutil as _shutil
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from typing import Optional, Tuple

import numpy as np

import config
from core.image_utils import load_image, load_image_with_alpha, save_image
from utils.logger import get_logger
from utils.path_helper import (
    sanitize_filename,
    make_unique_path,
)

log = get_logger("services.file_service")

# ------------------------------------------------------------------------------
# 目录定义：全部派生自 config.CACHE_DIR，统一管理、便于清理。
# ------------------------------------------------------------------------------
UPLOAD_DIR: _os.PathLike = config.CACHE_DIR / "uploads"   # 用户上传原图落盘
THUMB_DIR: _os.PathLike = config.CACHE_DIR / "thumbs"     # 缩略图
HISTORY_DIR: _os.PathLike = config.CACHE_DIR / "history"  # 处理历史索引与记录
TEMP_UPLOAD_MAX_MB: int = config.MAX_IMAGE_FILE_MB


def ensure_dirs() -> None:
    """
    确保全部工作目录存在。程序启动（app.build_app）时调用一次即可。
    """
    for d in (UPLOAD_DIR, THUMB_DIR, HISTORY_DIR):
        _os.makedirs(d, exist_ok=True)


def _require_cv2():
    """延迟导入 cv2。"""
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "缺少 OpenCV 依赖（cv2）。请先执行：pip install -r requirements.txt"
        ) from exc
    return cv2


def save_upload(src_path: str | _os.PathLike, original_name: str) -> _os.PathLike:
    """
    把用户上传的临时文件复制（落盘）到上传目录，文件名做唯一化与清洗。

    参数说明：
        src_path       : Gradio 上传得到的临时文件路径。
        original_name  : 原始文件名（用于保留可读的主名）。

    返回值：
        落盘后的稳定路径（PathLike）。

    说明：
        不直接用临时路径作为处理依据——Gradio 的临时文件在会话结束可能被清理，
        落盘后可支撑「文件缓存：保存近期处理记录，不用重复上传」。
    """
    ensure_dirs()
    src = str(src_path)
    stem = _os.path.splitext(original_name)[0] or "upload"
    ext = _os.path.splitext(original_name)[1].lower() or ".png"
    safe_stem = sanitize_filename(stem, max_length=120)
    desired = f"{safe_stem}{ext}"
    target = make_unique_path(UPLOAD_DIR, desired)
    _shutil.copyfile(src, target)
    log.info("上传文件已落盘：%s", target)
    return target


def load_for_display(path: str | _os.PathLike) -> np.ndarray:
    """
    读取图片为 RGB uint8 数组，供 Gradio 组件展示与 inpaint 输入。

    参数说明：
        path : 图片路径（支持中文路径）。

    返回值：
        H×W×3 uint8 数组（RGB）。
    """
    return load_image(path)


def load_with_alpha(path: str | _os.PathLike) -> Tuple[np.ndarray, Optional[np.ndarray]]:
    """
    读取图片并同时返回透明通道（若有），用于保留 PNG 透明背景。

    参数说明：
        path : 图片路径。

    返回值：
        (rgb, alpha) 元组。rgb 为 H×W×3；无透明通道时 alpha 为 None。
    """
    return load_image_with_alpha(path)


def _as_rgb3(arr: np.ndarray) -> np.ndarray:
    """
    把常见图像数组归一化为 H×W×3（阶段 135）。

    本模块的 make_thumbnail 是「视频抽帧 / PDF 首页渲染 / 历史重处理」三条
    调用路径的唯一形状防线，而这三处的数组都来自外部解码器，通道形态不受
    本模块控制（pdf_service._page_to_array 的灰度补通道/丢 alpha 是同范式）。

    规则：
        · 2D 灰度 (H×W)        → 三通道复制（值不变）。
        · 3 通道 (H×W×3)       → 原样返回。
        · 4 通道 (H×W×4, RGBA) → 丢弃 alpha（与 load_image 合同一致）。
        · 其他形状             → ValueError（清晰报错，绝不深层崩溃）。
    """
    a = np.asarray(arr)
    if a.ndim == 2:
        return np.stack([a, a, a], axis=-1)
    if a.ndim == 3 and a.shape[2] == 3:
        return a
    if a.ndim == 3 and a.shape[2] == 4:
        return a[:, :, :3]
    raise ValueError(
        f"make_thumbnail 需要灰度(H×W)/RGB(H×W×3)/RGBA(H×W×4) 数组，实际 shape={a.shape}"
    )


def make_thumbnail(arr: np.ndarray, size: Optional[int] = None) -> np.ndarray:
    """
    生成缩略图（保持长宽比，居中填充到正方形）。

    参数说明：
        arr  : 原图 uint8 数组，接受灰度(H×W) / RGB(H×W×3) / RGBA(H×W×4)；
               灰度自动复制为三通道，RGBA 丢弃 alpha（阶段 135 归一化）。
        size : 边长（默认取 config.CACHE_THUMBNAIL_SIZE）。

    返回值：
        size×size×3 uint8 缩略图。

    异常：
        ValueError : 数组形状不受支持，或任一边长为 0（空图像）。
    """
    cv2 = _require_cv2()
    arr = _as_rgb3(arr)
    size = size or config.CACHE_THUMBNAIL_SIZE
    h, w = arr.shape[:2]
    if h == 0 or w == 0:
        raise ValueError(f"空图像（{h}×{w}），无法生成缩略图")
    scale = size / max(h, w)
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    resized = cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_AREA)
    # 居中粘贴到正方形画布（灰底），避免不同比例图片在列表里错位。
    canvas = np.full((size, size, 3), 240, dtype=np.uint8)
    y0 = (size - nh) // 2
    x0 = (size - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = resized
    return canvas


def save_thumbnail(arr: np.ndarray, stem: str) -> _os.PathLike:
    """
    把缩略图写入缩略图目录。

    参数说明：
        arr  : 缩略图数组。
        stem : 文件主名（不含扩展名）。

    返回值：
        缩略图路径。
    """
    ensure_dirs()
    name = sanitize_filename(stem, max_length=120) + ".png"
    target = make_unique_path(THUMB_DIR, name)
    save_image(target, arr, fmt="PNG")
    return target


def save_result(
    arr: np.ndarray,
    original_stem: str,
    *,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    alpha: Optional[np.ndarray] = None,
    icc: Optional[bytes] = None,
) -> str:
    """
    把处理结果保存到 outputs 目录，文件名遵循 config.OUTPUT_NAME_TEMPLATE。

    参数说明：
        arr           : 修复结果 H×W×3 uint8。
        original_stem : 原文件主名（用于生成输出文件名）。
        fmt           : 导出格式（PNG/JPG/WEBP），缺省取 config.DEFAULT_OUTPUT_FORMAT。
        quality       : 画质 1~100，缺省取 config.DEFAULT_OUTPUT_QUALITY。
        alpha         : 透明通道（PNG 保留透明时用）。

    返回值：
        实际写出文件的绝对路径（字符串）。
    """
    from utils.path_helper import build_output_filename, get_output_dir

    out_fmt = (fmt or config.DEFAULT_OUTPUT_FORMAT).upper()
    ext = out_fmt.lower()
    fname = build_output_filename(original_stem, ext)
    out_dir = get_output_dir()
    target = make_unique_path(out_dir, fname)
    saved = save_image(target, arr, fmt=out_fmt, quality=quality, alpha=alpha, icc=icc)
    log.info("处理结果已保存：%s", saved)
    return saved


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    cv2 = _require_cv2()
    ensure_dirs()

    # 用例 1：落盘上传文件。
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp.name, np.zeros((60, 80, 3), dtype=np.uint8))
    tmp.close()
    saved = save_upload(tmp.name, "我的测试图.png")
    assert _os.path.exists(saved), "落盘失败"
    print(f"用例1通过：上传文件落盘到 {saved}")

    # 用例 2：缩略图尺寸。
    arr = np.zeros((200, 300, 3), dtype=np.uint8)
    thumb = make_thumbnail(arr, size=128)
    assert thumb.shape == (128, 128, 3), "缩略图尺寸错误"
    print("用例2通过：缩略图生成正确。")

    # 用例 4（阶段 135）：灰度 / RGBA / 退化尺寸。
    g_thumb = make_thumbnail(np.zeros((200, 300), dtype=np.uint8), size=64)
    assert g_thumb.shape == (64, 64, 3), "灰度缩略图归一化失败"
    a_thumb = make_thumbnail(np.zeros((200, 300, 4), dtype=np.uint8), size=64)
    assert a_thumb.shape == (64, 64, 3), "RGBA 缩略图归一化失败"
    try:
        make_thumbnail(np.zeros((0, 100, 3), dtype=np.uint8), size=64)
        raise SystemExit("用例4失败：退化尺寸未抛 ValueError")
    except ValueError:
        pass
    print("用例4通过：灰度/RGBA 归一化与退化尺寸守卫正确。")

    # 用例 3：保存结果。
    out = save_result(np.zeros((50, 50, 3), dtype=np.uint8), "样例", fmt="PNG")
    assert _os.path.exists(out), "结果保存失败"
    print(f"用例3通过：结果保存至 {out}")

    _os.remove(tmp.name)
    print("file_service.py 自检全部通过。")
