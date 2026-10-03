# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/quality.py
 文件说明 : 无参考修复质量评估（阶段 56）。

 用途
 ----
 「双跑择优」场景：同一张图用多个引擎修复后，在没有 ground truth 的情况下，
 挑出「修复区与周边背景衔接最自然」的结果。核心函数
 `no_reference_inpaint_score(repaired, source, mask)` 返回越高越好的分数。

 核心思想（seamless consistency / 无缝一致性）
 --------------------------------------------
 好的修复应让「被修复区域」在局部统计上与其紧邻的「未修复背景环」无缝衔接。
 我们分别度量修复区与背景环在多层特征（每通道 mean / std / 梯度 mean / 梯度 std）
 上的差异，差异越小越无缝，分数越高。

 诚实声明（重要）
 ----------------
 本指标是**启发式**，未经真实照片 ground truth 验证（项目 P0 待用户供图）。
 阶段 51 已证明：合成数据上 OpenCV 8/8 占优、LaMa 全负。因此本指标在合成数据上
 的「选对」只是 trivial 地复现「选 OpenCV」，**无法证明它在真实难图上能正确挑出 LaMa**。
 `scripts/validate_selector.py` 用合成 ground truth 做了诚实的一致性校验，但真实世界的
 选优效力仍需 P0 真实图验证。本模块因此**只接入诊断工具（`--input-dir` 对比），
 不进入默认修复热路径**，避免每次修复都翻倍耗时且收益未经验证。

 依赖：仅 numpy / cv2 / config（零 gradio、零核心修复依赖，可被任意层安全调用）。
==============================================================================
"""
from __future__ import annotations

import os as _os
import sys as _sys

# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

try:
    import cv2  # noqa: E402
except Exception:  # noqa: BLE001
    cv2 = None  # 仅在真正调用打分函数时才会用到；导入期不强制（便于无 OpenCV 环境做纯单测显式跳过）

__all__ = ["no_reference_inpaint_score"]


def _mask_bin(mask: np.ndarray) -> np.ndarray:
    """把任意值域掩码规整成单通道 0/1 uint8 二值掩码。"""
    m = mask.astype(np.uint8)
    if m.ndim == 3:
        m = m[:, :, 0]
    return (m > 0).astype(np.uint8)


def _interior_gradients(ch: np.ndarray, bin_mask: np.ndarray) -> np.ndarray:
    """区域内梯度样本：梯度像素的左右（或上下）邻居**都在区域内**。

    目的：消除「区域↔区域外」的边界突变梯度，使修复区与背景环可比
    ——背景环的内边界正贴着水印块，若把整图梯度算进来，环的梯度特征会
    包含「水印↔背景」的突变，导致即便完美修复也不可比。

    重要——掩码必须先规整为**布尔**数组（阶段 85 修复的缺陷）
    --------------------------------------------------------
    掩码在真实调用链上是 uint8（`threshold_mask` 返回 0/255，检测器掩码同）。
    若直接写 `gx[mask_u8]`，numpy 会按**整数花式索引**解释：取第 0 / 第 255 行，
    得到的是**与掩码无关**的错误样本；更严重的是结果会从 (H, W−1) 膨胀成
    (H, W−1, W)——640² 图 → 5.23 亿个 float64 → **3.9 GiB**，真实尺寸下直接
    MemoryError（1024² → ~17 GiB）。故此处统一先做 `inside = bin_mask > 0`。
    """
    inside = bin_mask > 0
    gx = np.abs(np.diff(ch, axis=1))            # (H, W-1)
    gy = np.abs(np.diff(ch, axis=0))            # (H-1, W)
    in_gx = inside[:, :-1] & inside[:, 1:]      # 左右都在区域内
    in_gy = inside[:-1, :] & inside[1:, :]      # 上下都在区域内
    return np.concatenate([gx[in_gx].ravel(), gy[in_gy].ravel()]).astype(np.float64)


def _features(arr: np.ndarray, bin_mask: np.ndarray) -> np.ndarray:
    """对 arr 的 bin_mask>0 区域提取 (mean, std, 梯度mean, 梯度std) ×通道 特征。

    返回值长度 4×C；区域为空则返回全 0（调用方据此识别「无区域」）。
    """
    work = arr
    if work.ndim == 2:
        work = work[:, :, None]
    inside = bin_mask > 0
    feats: list[float] = []
    for c in range(work.shape[2]):
        ch = work[:, :, c].astype(np.float64)
        region = ch[inside]
        if region.size == 0:
            feats.extend([0.0, 0.0, 0.0, 0.0])
            continue
        mu = float(region.mean())
        sd = float(region.std())
        g = _interior_gradients(ch, bin_mask)
        if g.size:
            gm, gs = float(g.mean()), float(g.std())
        else:
            gm, gs = 0.0, 0.0
        feats.extend([mu, sd, gm, gs])
    return np.array(feats, dtype=np.float64)


def no_reference_inpaint_score(
    repaired: np.ndarray,
    source: np.ndarray,
    mask: np.ndarray,
    ring_width: int = 18,
) -> float:
    """无参考修复质量打分（越高越好）。

    参数
 ----
        repaired   : 修复结果 H×W×(1|3) uint8（RGB 或灰度均可）。
        source     : 原输入（带水印）H×W×(1|3) uint8，用作「真实背景」参考。
        mask       : 掩码 H×W（任意值域，0/非0 即可）。
        ring_width : 背景环膨胀迭代次数（≈ 环宽像素），默认 18。

    做法
 ----
        1. 以掩码膨胀 ring_width 后减去掩码，得到「紧邻掩码的未修复背景环」；
        2. 分别提取修复区与背景环的 (mean, std, 梯度mean, 梯度std) 四特征；
        3. 按背景环特征值做归一化后求差异，score = −mean(|Δ|/scale)（差异越小分越高）。

    退化情形
 ------
        · 掩码为空 或 背景环为空 → 返回 0.0（无法判断，不偏袒任一引擎）；
        · cv2 不可用时抛出 RuntimeError（由调用方决定如何处理）。
    """
    if cv2 is None:
        raise RuntimeError("no_reference_inpaint_score 需要 cv2（OpenCV）才能计算背景环。")

    mask_bin = _mask_bin(mask)
    if mask_bin.sum() == 0:
        return 0.0

    ring = cv2.dilate(mask_bin, np.ones((3, 3), np.uint8), iterations=ring_width)
    ring = (ring > 0) & (mask_bin == 0)
    if ring.sum() == 0:
        return 0.0

    f_rep = _features(repaired, mask_bin)
    f_ctx = _features(source, ring)

    # 归一化：以背景环特征幅值为尺度，避免不同通道量级差异主导。
    # 下限 1.0 防止「背景环统计近 0」（如纯色/平滑区）时差异被无限放大。
    scale = np.maximum(np.abs(f_ctx), 1.0)
    diff = np.abs(f_rep - f_ctx) / scale
    # 越高越好：差异越小越无缝。
    return float(-diff.mean())
