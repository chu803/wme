# -*- coding: utf-8 -*-
"""
阶段 262 守卫 · 真·超分「开启但无权重」不得被当作质量增益（待决项① 决策依据固化）。

事实（探针实测，见 docs/262；仓库内 `config.user_overrides.json` 已 git 追踪地把
`REAL_SUPERRES_ENABLED=true / SCALE=2 / BACKEND=esrgan` 打开）：

  | 配置          | 输出尺寸      | 耗时(s) | 掩码区 PSNR |
  |---------------|---------------|---------|-------------|
  | 关            | 300×400(原)   | 0.028   | **7.71 dB** |
  | 开 scale=2    | 600×800(2×)   | 2.633   | 7.68 dB     |
  | 开scale=4    | 1200×1600(4×) | 0.146   | 7.65 dB     |

⇒ 开启后 PSNR **不升反微降**，耗时显著上升；且据阶段 115，**无训练权重时 esrgan
退化为纯双三次**（`used_real=False`）⇒ 当前开启的「真实超分」**不带来任何学习式
质量增益**，只是放大尺寸 + 增耗时 = **净负收益**。

本守卫把该决策依据机器化：
1. `used_real` 契约：无权重时必须为 False，且 note 含「双三次」——不得声称真实增强；
2. 质量不改善：开启（退化双三次）时掩码区 PSNR **不得显著优于**关闭时；
3. 尺寸放大生效但无学习式增强时，`REAL_SUPERRES_ENABLED=True` 的净收益仅是尺寸，
   不得被文档/注释描述为「提升清晰度」（防115 那类「声明≠实现」复发）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core import superres as SR  # noqa: E402


# ---------------------------------------------------------------------------
# 1) used_real / note 契约（无权重 ⇒ 双三次，不得声称真实增强）
# ---------------------------------------------------------------------------
@pytest.mark.torch_required
def test_esrgan_without_weights_reports_bicubic_not_real():
    """无训练权重时：used_real=False 且 note 明示「双三次」。

    这是 docs/262 决策的事实基础——若哪天能真正加载权重，used_real 才为 True。
    """
    pytest.importorskip("torch")
    backend = SR.get_superres_backend("esrgan", model_path="", model_dir="")
    assert backend is not None, "torch 可用时 esrgan 后端应可构造"
    arr = np.full((64, 64, 3), 128, np.uint8)
    out, used_real, note = backend.upscale(arr, 2)
    assert out is not None, "无权重也应产出放大图（纯双三次）"
    assert used_real is False, "无训练权重时 used_real 必须为 False（无学习式增强）"
    assert "双三次" in note, f"note 须如实标注降级为双三次，实际：{note!r}"


# ---------------------------------------------------------------------------
# 2) 质量不改善（开启退化双三次 ≠ 质量增益）
# ---------------------------------------------------------------------------
@pytest.mark.torch_required
def test_bicubic_fallback_does_not_improve_mask_quality():
    """开启（退化双三次）时掩码区 PSNR 不得显著优于原尺寸结果。

    量化 docs/262 的关键结论：开启超分**不带来质量增益**。
    这里用「放大后下采样回原尺寸 vs 原图」与「原修复结果 vs 原图」比较 PSNR，
    断言放大版**没有显著变好**（阈值 0.5 dB 容忍数值噪声）。
    """
    pytest.importorskip("torch")
    rng = np.random.default_rng(0)
    original = (rng.random((128, 128, 3)) * 255).astype(np.uint8)
    repaired = original.copy()
    mask = np.zeros((128, 128), np.uint8)
    mask[40:88, 40:88] = 255
    repaired[mask > 0] = 127          # 模拟修复区

    def psnr(a, b, m):
        sel = m > 0
        if not sel.any():
            return float("inf")
        mse = ((a[sel].astype(np.float64) - b[sel].astype(np.float64)) ** 2).mean()
        return float("inf") if mse <= 1e-12 else 10 * np.log10(255.0 ** 2 / mse)

    base = psnr(repaired, original, mask)

    backend = SR.get_superres_backend("esrgan", model_path="", model_dir="")
    out, used_real, _note = backend.upscale(repaired, 2)
    assert used_real is False
    # 双三次放大 → 最近邻下采样回原尺寸，比较质量
    import cv2
    down = cv2.resize(out, (128, 128), interpolation=cv2.INTER_AREA)
    upscaled = psnr(down, original, mask)

    # 放大（退化双三次）不构成质量改善——允许极小数值差异，但不得显著更好。
    assert upscaled <= base + 0.5, (
        f"退化双三次放大竟显著提升了掩码区质量（{base:.2f} → {upscaled:.2f} dB）；"
        f"若这是真实学习式增强，说明 used_real 判定或权重状态有误"
    )


# ---------------------------------------------------------------------------
# 3) 防「声明≠实现」复发：开启超分不得被描述为提升清晰度
# ---------------------------------------------------------------------------
def test_no_claim_of_quality_gain_without_weights():
    """无训练权重（used_real=False）时，代码注释/文档不得声称「学习式增强已生效」。

    防115 同类缺陷复发（当时note 误称「双三次放大的真实 torch 推理」）。
    这里检查 `core/superres.py` 的关键 note 模板：退化路径必须同时含
    「已退化为纯双三次」与「可启用学习式增强」两个语义，不得反向宣称。
    """
    src = open(os.path.join(_ROOT, "core", "superres.py"), encoding="utf-8").read()
    assert "已退化为纯双三次放大" in src, "退化路径 note 模板缺失（无权重须明示双三次）"
    assert "可启用学习式增强" in src, "note 须提示配置权重路径以启用学习式增强"