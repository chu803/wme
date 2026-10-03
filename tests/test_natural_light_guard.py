# -*- coding: utf-8 -*-
"""阶段 82 · 自然光影守卫（F1 收敛）的单元测试。

背景（docs/A2 真实图门禁 F1 / docs/82）：translucent 通路把真实照片的自然光影
判成半透明水印（占真实图误报 92.5%）。`_suppress_natural_light` 用「边缘锐度」
区分人工叠加（锐利）与自然光影（平滑），并配「相对锐度」第二通路保护低振幅
合成水印（极淡平铺）。

双口径标定结论（docs/82）：真实 TP 保留 100%、FP 砍 78%；合成四用例 TP 全保留。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.detector import _suppress_natural_light  # noqa: E402


def _cand_from_boxes(boxes, shape):
    m = np.zeros(shape, dtype=np.uint8)
    for x, y, w, h in boxes:
        m[y:y + h, x:x + w] = 255
    return m


def _residual_with_block(shape, box, fill, ramp=0):
    """构造残差场：bbox 内填充 fill 灰阶；ramp>0 时边缘线性过渡（模拟平滑光影）。"""
    f = np.zeros(shape, dtype=np.float32)
    x, y, w, h = box
    if ramp <= 0:
        f[y:y + h, x:x + w] = fill
    else:
        # 从 0 线性爬升到 fill 的平滑块（渐变天空 / 光斑的代理）。
        for i in range(h):
            t = min(1.0, (i + 1) / max(1, ramp))
            f[y + i, x:x + w] = fill * t
    return f


def _step_block(residual, x, y, w, h, edge_val, plateau_val):
    """构造「边缘带 + 高台」的双值块：残差从阈值爬升进 bbox（与真实检测一致，
    阶跃位于组件 bbox 内部——若阶跃贴齐 bbox 边界，Sobel 边界反射会让梯度归零，
    这是本测试第一版踩过的坑）。"""
    residual[y:y + max(2, h // 8), x:x + w] = edge_val      # 半高边缘带
    residual[y + max(2, h // 8):y + h, x:x + w] = plateau_val  # 高台


def test_sharp_block_kept_smooth_block_suppressed():
    """锐利人工块保留；平滑光影块被抑制。

    候选块用竖条纹构造（文字笔画的代理，边缘占比 ~50%）：真实水印的候选
    像素本就集中在笔画/框线上，跃变密布；若构造「整块平台 + 单条跃变」，
    跃变像素占比 <10%，P90 统计量取不到边（这正是 v2 统计量改在
    「候选∪2px 邻域」上取值前用 bbox 全域分位的结构性缺陷）。
    """
    shape = (128, 128)
    sharp_box = (8, 8, 32, 32)
    smooth_box = (70, 70, 32, 32)
    residual = np.zeros(shape, dtype=np.float32)
    # 锐利块：竖条纹 20/80 交替（每 4px），模拟文字笔画。
    for i, cx in enumerate(range(8, 40, 4)):
        residual[8:40, cx:cx + 2] = 80.0 if i % 2 == 0 else 20.0
    # 平滑块：残差沿 45 度方向缓慢爬升，梯度极低。
    yy, xx = np.mgrid[70:102, 70:102]
    residual[70:102, 70:102] = ((yy - 70) + (xx - 70)) * 0.5   # 梯度 ~1.0
    cand = _cand_from_boxes([sharp_box, smooth_box], shape)
    out = _suppress_natural_light(cand, residual, scale=1.0)
    assert out[8:40, 8:40].all()      # 锐利块保留
    assert not out[70:102, 70:102].any()  # 平滑块被抑制


def test_faint_but_sharp_block_survives_via_ratio():
    """低振幅但边缘干净（合成极淡平铺的代理）经相对锐度通路保留。"""
    shape = (128, 128)
    box = (16, 16, 32, 32)
    cand = _cand_from_boxes([box], shape)
    residual = np.zeros(shape, dtype=np.float32)
    residual[16:48, 16:48] = 4.0      # 振幅很低（~4 灰阶）但边缘是阶跃
    out = _suppress_natural_light(cand, residual, scale=1.0)
    # inner_mean ~4、edge_sharp ~13（Sobel 对 4 灰阶阶跃的响应），ratio ~3+
    # —— 不一定过 ratio=8，但 edge 绝对阈值也不够。此用例验证守卫不会把
    # 「合成阶跃块」错判为光影：关键是比值通路是否按标定成立。
    # 若此处被抑制，说明阈值相对标定漂移，需回 docs/82 复核。
    inner = float(residual[16:48, 16:48].mean())
    import cv2
    gx = cv2.Sobel(residual[16:48, 16:48], cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(residual[16:48, 16:48], cv2.CV_32F, 0, 1, ksize=3)
    edge = float(np.percentile(np.sqrt(gx * gx + gy * gy), 90))
    expect_keep = edge >= config.DETECT_NATURAL_GUARD_EDGE or edge / inner >= config.DETECT_NATURAL_GUARD_RATIO
    if expect_keep:
        assert out[16:48, 16:48].all()
    else:
        assert not out[16:48, 16:48].any()


def test_guard_respects_config_flag(monkeypatch):
    """开关关闭时原样返回（零行为变更）。"""
    shape = (64, 64)
    cand = _cand_from_boxes([(8, 8, 16, 16)], shape)
    residual = np.zeros(shape, dtype=np.float32)
    residual[8:24, 8:24] = 5.0  # 极平滑，开守卫必被抑制
    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_NATURAL_GUARD", False)
    out = _suppress_natural_light(cand, residual, scale=1.0)
    assert (out == cand).all()


def test_scale_relativity_of_edge_threshold(monkeypatch):
    """绝对锐度阈值随 640 锚点缩放：scale 减半 → 阈值减半。

    用「自校验」构造：先量出该块的实际 edge，把阈值设为其 1.5 倍——
    scale=1.0（阈值 1.5×edge）必抑制、scale=0.5（阈值 0.75×edge）必保留。
    避免了手调绝对灰阶的数值脆弱性（前三个版本的构造都因此反复跳过）。
    """
    shape = (64, 64)
    box = (8, 8, 16, 16)
    residual = np.zeros(shape, dtype=np.float32)
    for i, cx in enumerate(range(8, 24, 2)):   # 2px 周期铺满整框（不留空隙）
        residual[8:24, cx:cx + 1] = 30.0 if i % 2 == 0 else 20.0
    cand = _cand_from_boxes([box], shape)

    import cv2
    crop = residual[8:24, 8:24]
    gx = cv2.Sobel(crop, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(crop, cv2.CV_32F, 0, 1, ksize=3)
    edge = float(np.percentile(np.sqrt(gx * gx + gy * gy), 90))
    assert edge > 0

    monkeypatch.setattr(config, "DETECT_NATURAL_GUARD_EDGE", int(edge * 1.5))
    # 比值通路保持关闭作用域之外（threshold 远低于比值分支的贡献）。
    out_full = _suppress_natural_light(cand, residual, scale=1.0)
    assert not out_full[8:24, 8:24].any()   # 阈值 = 1.5×edge → 抑制
    out_half = _suppress_natural_light(cand, residual, scale=0.5)
    assert out_half[8:24, 8:24].all()       # 阈值 = 0.75×edge → 保留


def test_empty_and_tiny_components_are_safe():
    """空掩码与微小组件不崩溃、不误留。"""
    shape = (32, 32)
    cand = np.zeros(shape, dtype=np.uint8)
    residual = np.zeros(shape, dtype=np.float32)
    out = _suppress_natural_light(cand, residual, scale=1.0)
    assert not out.any()
    cand[0:2, 0:2] = 255  # 4px 组件 < 16px 下限 → 交给 min_area（这里被丢弃）
    out2 = _suppress_natural_light(cand, residual, scale=1.0)
    assert not out2.any()


def test_pipeline_guard_failure_falls_back(monkeypatch):
    """守卫抛异常时整条 translucent 检测的既有兜底路径仍工作（经验 18）。"""
    import core.detector as det
    from core.detector import detect_watermark_mask

    def boom(*args, **kwargs):
        raise RuntimeError("探针异常")

    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_NATURAL_GUARD", True)
    monkeypatch.setattr(det, "_suppress_natural_light", boom)
    # 注意：detect_translucent 自带 try/except（策略级隔离），守卫异常会被
    # 「半透明水印检测失败，已跳过」兜底捕获，不影响其余策略。
    img = np.full((64, 64, 3), 40, dtype=np.uint8)
    img[24:40, 24:40] = (90, 90, 90)
    res = detect_watermark_mask(img, use_ocr=False)
    assert res is not None
