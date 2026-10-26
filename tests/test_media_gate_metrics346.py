# -*- coding: utf-8 -*-
"""阶段 346 · 门禁的「改动占比」必须有容差——重编码噪声不是损伤。

起因：一次完整门禁里 `media_gate` 打出「视频 非水印区改动 **94.9%** | 全图改动 97.5%」，
数字大得吓人。实测（同一场景，`scripts/media_gate` 自己的构件复现）：

    非水印区 94.9% 的像素差 >0，但 48.8% 只差 1~3 级、45.6% 差 4~8 级，
    |差|>8 的只有 **3.0%**、>12 的 0.8%，均值差 **3.8/255**（肉眼不可见）

⇒ 那是**重编码噪声**：视频/PDF 必须重新编码，压缩会让几乎每个像素动 ±1~3。
判据「差 >0 即算被改」在这种产物上必然接近 100% ⇒ **指标失真**，不是大面积误伤。
以前这个数会让人要么误判、要么干脆不看 ⇒ 本阶段给「改动占比」加容差（DIFF_TOLERANCE=8）。

**同时必须钉住容差没有把真损伤藏起来**：差 ≥17 的像素仍要被算成「改动」。
"""
from __future__ import annotations

import numpy as np

import scripts.media_gate as mg


def _frame(v=0):
    """v>0 时整帧抬 v 级（模拟重编码噪声），**保持 uint8**（diff_stats 走 cv2.absdiff，
    传 int16 会被 OpenCV 拒：输出数组类型必须显式指定）。"""
    # 画布要够大：BBOX_DILATE=48 会把 bbox 外扩 48px，64px 的画布会被整片罩住 ⇒
    # 「非水印区」就只剩 0 个像素，outside_diff/outside_changed 恒为 0（第一版就栽在这）。
    a = np.full((256, 256, 3), 100, dtype=np.uint8)
    if not v:
        return a
    return np.clip(a.astype(np.int16) + v, 0, 255).astype(np.uint8)


def _bbox():
    return (100, 100, 20, 20)


def test_identical_frames_have_zero_change():
    a = _frame()
    st = mg.diff_stats(a, a.copy(), _bbox())
    assert st["outside_changed"] == 0.0
    assert st["whole_changed"] == 0.0
    assert st["outside_diff"] == 0.0


def test_reencode_noise_is_not_counted_as_damage():
    """整体 +2（压缩噪声量级）⇒ 改动占比应当是 0，而不是 100%。"""
    a = _frame(0)
    b = _frame(2)
    st = mg.diff_stats(a, b, _bbox())
    assert st["outside_changed"] == 0.0, (
        f"±2 的重编码噪声被算成了改动：{st}")
    assert st["whole_changed"] == 0.0, st
    assert st["outside_diff"] == 2.0, st


def test_real_damage_is_still_detected():
    """容差不许把真损伤藏起来：bbox 内 ±30 仍必须被算成「改动」。"""
    a = _frame(0)
    b = _frame(0).copy()
    b[102:118, 102:118] += 30
    st = mg.diff_stats(a, b, _bbox())
    assert st["whole_changed"] > 0.0, "真正的改动必须被算出来"
    assert st["bbox_diff"] > 20.0, st


def test_tolerance_is_documented_and_used():
    """容差常量必须在 0~255 范围内、且被 diff_stats 真正使用（防止有人改回 >0）。"""
    assert 0 < mg.DIFF_TOLERANCE <= 32, mg.DIFF_TOLERANCE
    src = open(mg.__file__, encoding="utf-8").read()
    body = src.split("def diff_stats")[1].split("def build_pdf")[0]
    assert "DIFF_TOLERANCE" in body, "diff_stats 里没有用容差 ⇒ 又变回 >0 判定"
    assert "(outside > 0)" not in body and "(d > 0)" not in body, "仍有 >0 判定残留"
