# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/mask_generator.py
 文件说明 : 手动水印掩码构建模块。
            负责把 Gradio ImageEditor 中用户「画笔涂抹」与「矩形框选」的结果，
            标准化为单通道 uint8（0/255）掩码，供 core.inpainter.inpaint() 使用。
            本模块属于算法层（core），不依赖 Gradio，可独立测试。

            兼容性说明：
              · Gradio 4.x 的 ImageEditor 返回 (image, mask) 元组；
              · Gradio 6.x 的 ImageEditor 返回 dict，键为
                'background'（原图）、'layers'（用户绘制的全部图层）、
                'composite'（合成图）。掩码由各图层的 alpha 通道合并得到。
            本模块同时兼容上述两种格式，并对图层尺寸做对齐。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
            本工具专用于去除使用者本人生成的 AI 图片上的可见水印 / 署名标识；
            严禁用于清除他人版权作品的水印或署名。
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from typing import Optional, Tuple

import numpy as np

import config
from utils.exceptions import InvalidMaskError
from utils.logger import get_logger

log = get_logger("core.mask_generator")


def _require_cv2():
    """
    延迟导入 cv2，避免在「仅做掩码逻辑单测」的场景下强依赖 OpenCV。

    返回值：
        cv2 模块对象。

    异常：
        ImportError : 未安装 OpenCV 时抛出中文提示。
    """
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "缺少 OpenCV 依赖（cv2）。请先执行：pip install -r requirements.txt"
        ) from exc
    return cv2


def _mask_to_gray_uint8(mask: np.ndarray) -> np.ndarray:
    """
    把 Gradio 返回的掩码（形态不确定）转换成单通道 uint8 灰度图。

    参数说明：
        mask : 可能是以下任一形态：
               · None                → 由调用方处理，这里不接收 None
               · (H, W)              → 已是单通道，直接返回
               · (H, W, 3) RGB       → 取亮度通道
               · (H, W, 4) RGBA      → 优先用 alpha 通道（画笔绘制在透明层，alpha=255）

    返回值：
        (H, W) uint8 灰度数组，值域 0~255。
    """
    if mask is None:
        raise InvalidMaskError("掩码为 None，无法转换为灰度图。")

    if mask.ndim == 2:
        return mask.astype(np.uint8)

    if mask.ndim == 3:
        channels = mask.shape[2]
        if channels == 4:
            # 透明通道最能准确表达「用户画了什么」。
            return mask[:, :, 3].astype(np.uint8)
        if channels == 3:
            cv2 = _require_cv2()
            gray = cv2.cvtColor(mask.astype(np.uint8), cv2.COLOR_RGB2GRAY)
            return gray.astype(np.uint8)
        if channels == 1:
            return mask[:, :, 0].astype(np.uint8)

    raise InvalidMaskError(
        f"无法识别的掩码维度：ndim={mask.ndim}，shape={mask.shape}。"
        "请确认 ImageEditor 返回格式是否为 numpy 数组。"
    )


def normalize_editor_mask(mask: Optional[np.ndarray], threshold: int = 10) -> np.ndarray:
    """
    将 Gradio 的掩码标准化为「二值掩码」（0/255）。

    参数说明：
        mask     : Gradio ImageEditor 返回的掩码数组（2D / 3D / 4D / None）。
        threshold : 灰度阈值，大于该值的像素视为「需要修复」（默认 10）。

    返回值：
        (H, W) uint8 掩码。若 mask 为 None，返回全 0 的单像素掩码。
    """
    if mask is None:
        return np.zeros((1, 1), dtype=np.uint8)

    gray = _mask_to_gray_uint8(mask)
    bin_mask = (gray > threshold).astype(np.uint8) * 255
    return bin_mask


def _resize_if_needed(bin_mask: np.ndarray, th: int, tw: int) -> np.ndarray:
    """若掩码尺寸与 (th, tw) 不一致，用最近邻缩放到目标尺寸。"""
    if bin_mask.shape[0] != th or bin_mask.shape[1] != tw:
        cv2 = _require_cv2()
        bin_mask = cv2.resize(bin_mask, (tw, th), interpolation=cv2.INTER_NEAREST)
    return bin_mask


def build_mask_from_editor(
    editor_value,
    brush_threshold: int = 10,
    target_size: Optional[Tuple[int, int]] = None,
) -> np.ndarray:
    """
    从 Gradio ImageEditor 的返回值中提取标准掩码。

    参数说明：
        editor_value   : ImageEditor 作为函数输入时 Gradio 传入的值，支持：
                         · dict（Gradio 6.x）：含 'background'/'layers'/'composite'。
                           掩码 = 合并所有图层(layers)的 alpha 通道。
                         · (image, mask) 元组（Gradio 4.x）：直接取 mask。
                         · 单 ndarray（个别版本）：当作 image，无掩码。
                         · None：未上传。
        brush_threshold : 掩码二值化阈值。
        target_size     : 期望掩码尺寸 (height, width)。提供时强制对齐到该尺寸。

    返回值：
        (H, W) uint8 掩码（0/255）。无任何涂抹区域时返回全 0 掩码。

    说明：
        画笔涂抹与矩形框选在 ImageEditor 里都绘制在「图层」上，掩码由各图层
        透明通道合并得到，因此本函数同时覆盖两种手动模式。
    """
    # ---------- 情况 1：Gradio 6.x 的 dict 返回值 ----------
    if isinstance(editor_value, dict):
        background = editor_value.get("background")
        layers = editor_value.get("layers") or []

        # 目标尺寸优先级：target_size > 背景图尺寸 > 首个有效图层尺寸。
        # 阶段 123 修复：旧逻辑在「无 background 且无 target_size」时直接返回
        # (1,1) 空掩码，会静默丢弃用户已涂抹的 layers（数据丢失）。
        if target_size is not None:
            th, tw = target_size
        elif background is not None and isinstance(background, np.ndarray) and background.ndim >= 2:
            th, tw = background.shape[:2]
        else:
            th, tw = None, None
            for layer in layers:
                if layer is None:
                    continue
                if isinstance(layer, np.ndarray) and layer.ndim >= 2:
                    th, tw = layer.shape[:2]
                    break
            if th is None:
                return np.zeros((1, 1), dtype=np.uint8)

        # 合并所有图层的 alpha，得到覆盖「全部涂抹/框选区域」的掩码。
        # 注意：图层灰度化**统一走 _mask_to_gray_uint8**（与模块内其它入口一致），
        # 既能正确处理 (H,W,1) 这类单通道图层（阶段 123 修复：旧分支对
        # (H,W,1) 误入 cvtColor(RGB2GRAY) 而崩溃），也让 (H,W,2) 等畸形输入
        # 得到 InvalidMaskError 而非 OpenCV 底层崩溃。
        merged = np.zeros((th, tw), dtype=np.uint8)
        for layer in layers:
            if layer is None:
                continue
            if not isinstance(layer, np.ndarray) or layer.ndim < 2:
                continue
            gray = _mask_to_gray_uint8(layer)
            layer_bin = (gray > brush_threshold).astype(np.uint8) * 255
            layer_bin = _resize_if_needed(layer_bin, th, tw)
            merged = np.maximum(merged, layer_bin)
        return merged

    # ---------- 情况 2：Gradio 4.x 的 (image, mask) 元组 / 单数组 ----------
    if editor_value is None:
        return np.zeros((1, 1), dtype=np.uint8)

    image = None
    mask = None
    if isinstance(editor_value, (tuple, list)):
        if len(editor_value) >= 1:
            image = editor_value[0]
        if len(editor_value) >= 2:
            mask = editor_value[1]
    elif isinstance(editor_value, np.ndarray):
        image = editor_value
    else:
        raise InvalidMaskError(
            f"ImageEditor 返回值形态无法解析：type={type(editor_value)}。"
            "请确认组件配置为 type='numpy'。"
        )

    if mask is None:
        if image is not None and isinstance(image, np.ndarray) and image.ndim >= 2:
            h, w = image.shape[:2]
            return np.zeros((h, w), dtype=np.uint8)
        return np.zeros((1, 1), dtype=np.uint8)

    bin_mask = normalize_editor_mask(mask, threshold=brush_threshold)
    if target_size is not None:
        bin_mask = _resize_if_needed(bin_mask, target_size[0], target_size[1])
    elif image is not None and isinstance(image, np.ndarray) and image.ndim >= 2:
        bin_mask = _resize_if_needed(bin_mask, image.shape[0], image.shape[1])
    return bin_mask


def count_mask_pixels(mask: np.ndarray) -> int:
    """
    统计掩码中「需要修复」的像素数量，用于空掩码检测与日志。

    参数说明：
        mask : 单通道 0/255 掩码。

    返回值：
        非零像素个数（int）。
    """
    if mask is None or mask.size == 0:
        return 0
    return int(np.count_nonzero(mask > 0))


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    # 用例 1：4 通道 RGBA 掩码（模拟 Gradio 4.x 单 mask）。
    rgba = np.zeros((50, 50, 4), dtype=np.uint8)
    rgba[20:30, 20:30, 3] = 255
    m1 = normalize_editor_mask(rgba, threshold=10)
    assert m1[25, 25] == 255 and m1[0, 0] == 0, "RGBA 掩码标准化错误"
    print("用例1通过：RGBA 掩码标准化正确。")

    # 用例 2：空掩码（None）→ 全 0。
    assert normalize_editor_mask(None).sum() == 0
    print("用例2通过：空掩码返回全黑。")

    # 用例 3：Gradio 6.x dict 返回值（background + 两个图层）。
    bg = np.zeros((100, 100, 3), dtype=np.uint8)
    layer_a = np.zeros((100, 100, 4), dtype=np.uint8)
    layer_a[10:20, 10:20, 3] = 255           # 图层 A：一块
    layer_b = np.zeros((100, 100, 4), dtype=np.uint8)
    layer_b[40:60, 40:60, 3] = 200           # 图层 B：另一块（alpha=200 也被算入）
    editor6 = {"background": bg, "layers": [layer_a, layer_b], "composite": bg}
    out6 = build_mask_from_editor(editor6, brush_threshold=10)
    assert out6.shape == (100, 100), "6.x 字典解析尺寸错误"
    assert count_mask_pixels(out6) == 10 * 10 + 20 * 20, "6.x 图层合并像素数错误"
    print("用例3通过：Gradio 6.x dict 返回值解析正确。")

    # 用例 4：Gradio 4.x 元组返回值。
    fake_img = np.zeros((100, 100, 3), dtype=np.uint8)
    fake_mask_3d = np.zeros((100, 100, 3), dtype=np.uint8)
    fake_mask_3d[40:60, 40:60] = 255
    out4 = build_mask_from_editor((fake_img, fake_mask_3d), brush_threshold=10)
    assert out4.shape == (100, 100) and count_mask_pixels(out4) == 20 * 20
    print("用例4通过：Gradio 4.x 元组返回值解析正确。")

    # 用例 5：dict 无 background（仅 layers）时仍应提取掩码，不得静默丢数据。
    layer_only = np.zeros((100, 100, 4), dtype=np.uint8)
    layer_only[30:40, 30:40, 3] = 255
    editor_no_bg = {"background": None, "layers": [layer_only], "composite": None}
    out5 = build_mask_from_editor(editor_no_bg, brush_threshold=10)
    assert out5.shape == (100, 100), "无 background 时尺寸推断错误"
    assert count_mask_pixels(out5) == 10 * 10, "无 background 时掩码丢失"
    print("用例5通过：dict 无 background 时仍正确提取图层掩码。")

    # 用例 6：(H,W,1) 单通道图层应被正确处理（与 _mask_to_gray_uint8 一致）。
    layer1c = np.zeros((100, 100, 1), dtype=np.uint8)
    layer1c[10:20, 10:20, 0] = 200
    editor1c = {"background": bg, "layers": [layer1c], "composite": bg}
    out6 = build_mask_from_editor(editor1c, brush_threshold=10)
    assert out6.shape == (100, 100), "单通道图层尺寸错误"
    assert count_mask_pixels(out6) == 10 * 10, "单通道图层未被识别"
    print("用例6通过：单通道 (H,W,1) 图层标准化正确。")

    print("mask_generator.py 自检全部通过。")
