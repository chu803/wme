# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/mask_editor.py
 文件说明 : 掩码代数编辑模块（阶段 24 新增，核心层）。

            提供「检测掩码 × 用户精修掩码」的组合运算与程序化掩码编辑原语：
              1. merge_masks(det_mask, user_mask, mode)
                 —— 两种精修模式的掩码合并：
                    · "merge" （合并）：检测掩码 ∪ 用户掩码（检测漏检时补画）。
                    · "manual"（仅精修）：用户掩码 ∩ ¬检测掩码（剥离预填的检测层，
                      只保留用户亲手绘制的部分）。
              2. apply_brush / apply_rect —— 圆形画笔 / 矩形区域 的程序化增删掩码，
                 供脚本化 API（api.py / server.py）与自动化测试复用。
              3. count_mask_pixels —— 复用 core.mask_generator 的单源实现。

            设计约束：
            · 本模块属于「算法层（core）」，零 gradio 依赖，纯 numpy 实现，可独立测试。
            · 只做「掩码代数」，不解析 Gradio ImageEditor 值（那是 core.mask_generator
              的职责）；两层单向依赖：mask_editor → mask_generator（无环）。
            · 所有输入掩码按「0/255 二值」约定处理；尺寸不一致时自动对齐。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from typing import Optional, Tuple

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

from core.mask_generator import count_mask_pixels  # 单源实现，避免重复
from utils.logger import get_logger

log = get_logger("core.mask_editor")

# 掩码精修模式常量（与 config.MASK_EDIT_ALLOWED_MODES 保持一致）。
MASK_MODE_MERGE: str = "merge"    # 合并：检测 ∪ 用户
MASK_MODE_MANUAL: str = "manual"  # 仅精修：用户 ∩ ¬检测

ALLOWED_MODES: Tuple[str, ...] = (MASK_MODE_MERGE, MASK_MODE_MANUAL)


def _as_binary(mask: Optional[np.ndarray]) -> np.ndarray:
    """
    把任意掩码输入归一化为 (H, W) uint8 0/255 二值掩码；None 返回空数组。

    参数：
        mask : None 或 (H, W) / (H, W, 3) / (H, W, 4) 数组（灰 / RGB / RGBA）。

    返回：
        (H, W) uint8 数组，非零视为需要修复（>0 → 255）。
    """
    if mask is None:
        return np.zeros((0, 0), dtype=np.uint8)
    arr = np.asarray(mask)
    if arr.ndim == 3:
        # RGBA 优先取 alpha 通道（画笔图层语义）；RGB 取亮度。
        if arr.shape[2] == 4:
            arr = arr[:, :, 3]
        else:
            arr = arr.mean(axis=2).astype(np.uint8)
    elif arr.ndim != 2:
        raise ValueError(
            f"掩码维度无法识别：ndim={arr.ndim}，shape={arr.shape}。"
            "仅支持 (H,W) / (H,W,3) / (H,W,4)。"
        )
    return (arr > 0).astype(np.uint8) * 255


def _resize_binary(bin_mask: np.ndarray, th: int, tw: int) -> np.ndarray:
    """
    掩码尺寸与 (th, tw) 不一致时，用最近邻缩放到目标尺寸（保持 0/255 二值）。
    """
    if bin_mask.shape[0] == th and bin_mask.shape[1] == tw:
        return bin_mask
    if th <= 0 or tw <= 0:
        return bin_mask
    # 最近邻插值（掩码不允许灰度平滑，避免边缘出现中间值）。
    ys = (np.linspace(0, bin_mask.shape[0] - 1, th)).astype(np.int32)
    xs = (np.linspace(0, bin_mask.shape[1] - 1, tw)).astype(np.int32)
    return bin_mask[np.ix_(ys, xs)]


def _canonical(
    det_mask: Optional[np.ndarray],
    user_mask: Optional[np.ndarray],
    target_shape: Optional[Tuple[int, int]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    把检测掩码与用户掩码规范化为同尺寸的二值掩码。

    目标尺寸优先级：target_shape > 非空掩码中较大的尺寸 > (0, 0)。
    """
    det = _as_binary(det_mask)
    user = _as_binary(user_mask)

    if target_shape is not None and target_shape[0] > 0 and target_shape[1] > 0:
        th, tw = int(target_shape[0]), int(target_shape[1])
    elif det.size and user.size:
        th, tw = max(det.shape[0], user.shape[0]), max(det.shape[1], user.shape[1])
    elif det.size:
        th, tw = det.shape
    elif user.size:
        th, tw = user.shape
    else:
        return np.zeros((0, 0), dtype=np.uint8), np.zeros((0, 0), dtype=np.uint8)

    det = _resize_binary(det, th, tw) if det.size else np.zeros((th, tw), dtype=np.uint8)
    user = _resize_binary(user, th, tw) if user.size else np.zeros((th, tw), dtype=np.uint8)
    return det, user


def merge_masks(
    det_mask: Optional[np.ndarray],
    user_mask: Optional[np.ndarray],
    mode: str,
    target_shape: Optional[Tuple[int, int]] = None,
) -> np.ndarray:
    """
    把「检测掩码」与「用户精修掩码」按指定模式合并成最终修复掩码。

    参数：
        det_mask     : 自动检测得到的掩码（可为 None / 空）。
        user_mask    : 用户精修掩码（编辑器提取 / 脚本传入，可为 None / 空）。
        mode         : "merge"（检测 ∪ 用户）或 "manual"（用户 ∩ ¬检测）。
        target_shape : 目标尺寸 (height, width)，缺省取两掩码较大者。

    返回：
        (H, W) uint8 0/255 掩码。

    语义说明：
        · "merge"（合并补漏）：检测漏检时用户补画 → 两区域都修。
        · "manual"（仅精修）：剥离预填的检测层，只修用户亲手绘制的部分；
          用于「检测结果已预填进编辑器、用户只想用自己的笔迹」的场景。
    """
    mode = str(mode or MASK_MODE_MERGE).lower()
    if mode not in ALLOWED_MODES:
        log.warning("未知掩码精修模式 %r，回退为 %r。", mode, MASK_MODE_MERGE)
        mode = MASK_MODE_MERGE

    det, user = _canonical(det_mask, user_mask, target_shape)
    if det.size == 0:
        return user
    if mode == MASK_MODE_MERGE:
        # 检测 ∪ 用户：np.maximum 保持 0/255。
        return np.maximum(det, user)
    # manual：用户 ∩ ¬检测 —— 只保留用户绘制且不在检测掩码内的像素。
    return np.bitwise_and(user, np.bitwise_not(det))


def apply_brush(
    mask: np.ndarray,
    cy: float,
    cx: float,
    radius: float,
    add: bool = True,
) -> np.ndarray:
    """
    在掩码上以 (cy, cx) 为圆心、radius 为半径，圆形画笔增/删区域。

    参数：
        mask   : (H, W) 二值掩码（0/255）。
        cy/cx  : 圆心坐标（可为浮点，内部取整）。
        radius : 画笔半径（像素），<=0 时返回原掩码副本。
        add    : True=添加（置 255）；False=擦除（置 0）。

    返回：
        新掩码数组（原掩码不被修改）。
    """
    out = np.asarray(mask).copy()
    if out.ndim != 2:
        raise ValueError(f"画笔输入须为 (H,W) 二值掩码，实际 shape={out.shape}")
    r = int(round(float(radius)))
    if r <= 0:
        return out
    h, w = out.shape
    cy_i, cx_i = int(round(float(cy))), int(round(float(cx)))
    if cy_i < 0 or cy_i >= h or cx_i < 0 or cx_i >= w:
        return out  # 圆心越界：无事可做

    y0, y1 = max(0, cy_i - r), min(h, cy_i + r + 1)
    x0, x1 = max(0, cx_i - r), min(w, cx_i + r + 1)
    yy, xx = np.ogrid[y0:y1, x0:x1]
    disk = ((yy - cy_i) ** 2 + (xx - cx_i) ** 2) <= r * r
    if add:
        out[y0:y1, x0:x1][disk] = 255
    else:
        out[y0:y1, x0:x1][disk] = 0
    return out


def apply_rect(
    mask: np.ndarray,
    y0: int,
    x0: int,
    y1: int,
    x1: int,
    add: bool = True,
) -> np.ndarray:
    """
    在掩码上以 (y0,x0)-(y1,x1) 为对角（半开区间 [y0,y1)×[x0,x1)）矩形增/删区域。

    参数：
        mask : (H, W) 二值掩码（0/255）。
        y0/x0/y1/x1 : 矩形边界（会自动夹取到图像范围内；y1<=y0 或 x1<=x0 时无效）。
        add  : True=添加；False=擦除。

    返回：
        新掩码数组。
    """
    out = np.asarray(mask).copy()
    if out.ndim != 2:
        raise ValueError(f"矩形输入须为 (H,W) 二值掩码，实际 shape={out.shape}")
    h, w = out.shape
    ya0, ya1 = max(0, int(y0)), min(h, int(y1))
    xa0, xa1 = max(0, int(x0)), min(w, int(x1))
    if ya1 <= ya0 or xa1 <= xa0:
        return out
    if add:
        out[ya0:ya1, xa0:xa1] = 255
    else:
        out[ya0:ya1, xa0:xa1] = 0
    return out


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    # 用例 1：merge 模式 = 检测 ∪ 用户。
    det = np.zeros((100, 100), dtype=np.uint8)
    det[10:20, 10:20] = 255
    user = np.zeros((100, 100), dtype=np.uint8)
    user[40:50, 40:50] = 255
    m = merge_masks(det, user, MASK_MODE_MERGE)
    assert m.shape == (100, 100) and count_mask_pixels(m) == 10 * 10 + 10 * 10
    assert m[15, 15] == 255 and m[45, 45] == 255 and m[60, 60] == 0
    print("用例1通过：merge 模式并集正确。")

    # 用例 2：manual 模式 = 用户 ∩ ¬检测（剥离预填检测层）。
    det = np.zeros((100, 100), dtype=np.uint8)
    det[10:20, 10:20] = 255
    user = np.zeros((100, 100), dtype=np.uint8)
    user[10:20, 10:20] = 255   # 用户在检测层上（应被剥离）
    user[15:25, 30:40] = 255   # 用户新画（应保留）
    m = merge_masks(det, user, MASK_MODE_MANUAL)
    assert m[15, 15] == 0, "检测层像素应被剥离"
    assert m[20, 35] == 255, "用户新画区域应保留"
    assert count_mask_pixels(m) == 10 * 10  # 15:25 行 × 30:40 列
    print("用例2通过：manual 模式剥离检测层正确。")

    # 用例 3：None / 空掩码分支与尺寸对齐。
    user3 = np.zeros((100, 100), dtype=np.uint8)
    user3[10:20, 10:20] = 255
    assert merge_masks(None, None, MASK_MODE_MERGE).size == 0
    m = merge_masks(None, user3, MASK_MODE_MERGE, target_shape=(100, 100))
    assert m.shape == (100, 100) and count_mask_pixels(m) == 10 * 10
    small = np.zeros((50, 50), dtype=np.uint8)
    small[0:5, 0:5] = 255
    m = merge_masks(small, user3, MASK_MODE_MERGE, target_shape=(100, 100))
    assert m.shape == (100, 100)
    assert m[0, 0] == 255, "小掩码最近邻放大后应保留左上角区域"
    assert m[15, 15] == 255, "用户区域应保留"
    assert count_mask_pixels(m) >= 10 * 10 + 25, "并集像素数应不少于两区域之和的保底值"
    print("用例3通过：None 分支与尺寸对齐正确。")

    # 用例 4：画笔增删。
    base = np.zeros((80, 80), dtype=np.uint8)
    brushed = apply_brush(base, 40, 40, 10)
    assert brushed[40, 40] == 255 and brushed[30, 40] == 255 and brushed[20, 40] == 0
    erased = apply_brush(brushed, 40, 40, 5, add=False)
    assert erased[40, 40] == 0 and erased[30, 40] == 255
    assert base[40, 40] == 0, "原掩码不应被修改"
    print("用例4通过：圆形画笔增删正确。")

    # 用例 5：矩形增删与边界夹取。
    base = np.zeros((50, 50), dtype=np.uint8)
    r = apply_rect(base, 10, 10, 20, 20)
    assert count_mask_pixels(r) == 100
    r2 = apply_rect(r, 5, 5, 60, 60)  # 越界矩形自动夹取：仅覆盖行/列 5..49
    assert r2[5, 5] == 255 and r2[49, 49] == 255 and r2[4, 4] == 0
    assert count_mask_pixels(r2) == 45 * 45
    r3 = apply_rect(r2, 0, 0, 25, 25, add=False)
    assert count_mask_pixels(r3) == 45 * 45 - 20 * 20  # 擦除相交的 5..24 行/列
    print("用例5通过：矩形增删与边界夹取正确。")

    # 用例 6：未知模式回退 merge。
    m = merge_masks(det, user, "bogus")
    assert count_mask_pixels(m) == count_mask_pixels(
        merge_masks(det, user, MASK_MODE_MERGE)
    )
    print("用例6通过：未知模式回退 merge 正确。")

    print("mask_editor.py 自检全部通过。")
