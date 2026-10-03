# -*- coding: utf-8 -*-
"""阶段 84 · 纹理稠密守卫（F1 残余收敛）的单元测试。

背景（docs/82 遗留缺口 / docs/84）：自然光影守卫按「边缘是否锐利」放行，
而真实照片上的**锐利自然纹理**（树叶、枝干、草地、织物、砖墙）边缘本身就锐利，
整片纹理照样通过 → 占真实图残余 FP 的大头。

`_suppress_texture_like` 用**梯度稠密度**区分：
    density = #{p : gmag(p) > 0.5 × mean(gmag)} / #p
水印是「结构化图形」（笔画内部平坦、只在轮廓陡变）→ 梯度集中在少数像素 → 低；
自然纹理「处处高频」→ 高。density 是比值 → 与振幅/尺度解耦。

双口径标定（docs/84）：真实图 precision +30%（29/30 图改善、0 图退化）、
recall 不降、IoU 不降；合成门禁逐位零回归。
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.detector import _suppress_texture_like  # noqa: E402


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------

def _cand_from_boxes(boxes, shape):
    m = np.zeros(shape, dtype=np.uint8)
    for x, y, w, h in boxes:
        m[y:y + h, x:x + w] = 255
    return m


def _texture_residual(shape, box, amp=60.0, seed=12345):
    """「处处高频」残差：有种子均匀噪声（自然纹理代理）→ density≈0.82。

    注意**不要**用 2px 周期棋盘格：它是 Sobel 算子的零空间（相邻列取值互补，
    加权和恒为 0），gmag 会退化成全 0 —— 本测试第一版就踩了这个坑，
    表现为「纹理块没被抑制、且两个阈值下结果完全相同」。
    """
    f = np.zeros(shape, dtype=np.float32)
    x, y, w, h = box
    rng = np.random.default_rng(seed)
    f[y:y + h, x:x + w] = rng.uniform(0.0, amp, size=(h, w))
    return f


def _stripe_residual(shape, box, hi=90.0, lo=20.0, period=4):
    """中等稠密度残差：4px 周期竖条纹 → density≈0.46（介于纹理与水印之间）。

    用于阈值单调性测试——提供第三个分档，让「保留数随阈值单调」有可观测的台阶。
    """
    f = np.zeros(shape, dtype=np.float32)
    x, y, w, h = box
    for i, cx in enumerate(range(x, x + w, period)):
        f[y:y + h, cx:cx + max(1, period // 2)] = hi if i % 2 == 0 else lo
    return f


def _glyph_residual(shape, box, amp=80.0, inset=10):
    """「结构化图形」残差：组件内部一块平坦高台，梯度只出现在高台轮廓（density≈0.10）。

    与真实水印一致——阶跃位于组件**内部**（贴齐 bbox 边界时 Sobel 边界反射
    会让梯度失真，这是阶段 82 测试踩过的坑）。
    """
    f = np.zeros(shape, dtype=np.float32)
    x, y, w, h = box
    f[y + inset:y + h - inset, x + inset:x + w - inset] = amp
    return f


# ---------------------------------------------------------------------------
# 1 · 判别方向
# ---------------------------------------------------------------------------

def test_dense_texture_suppressed_sparse_glyph_kept():
    """处处高频的连通域被抑制；结构化的（内部平坦+轮廓陡变）被保留。"""
    shape = (160, 160)
    tex_box = (8, 8, 48, 48)
    glyph_box = (96, 96, 48, 48)
    cand = _cand_from_boxes([tex_box, glyph_box], shape)
    residual = (_texture_residual(shape, tex_box)
                + _glyph_residual(shape, glyph_box))

    out = _suppress_texture_like(cand, residual)

    assert not out[8:56, 8:56].any(), "纹理块应被抑制"
    assert out[96:144, 96:144].all(), "结构化块应被保留"


def test_flat_residual_component_is_kept():
    """残差完全平坦（gmean≈0，无可分梯度）→ 保留，交给既有面积过滤表态。"""
    shape = (64, 64)
    cand = _cand_from_boxes([(8, 8, 24, 24)], shape)
    residual = np.zeros(shape, dtype=np.float32)
    out = _suppress_texture_like(cand, residual)
    assert out[8:32, 8:32].all()


def test_share_constant_is_half():
    """`过均值`比例常数必须是 0.5：1.0 在高斯型分布下恒约 0.5，无区分度。"""
    assert config.DETECT_TEXTURE_GUARD_SHARE == 0.5


# ---------------------------------------------------------------------------
# 2 · 不变性（该判据的核心价值：与振幅/尺度解耦）
# ---------------------------------------------------------------------------

def test_density_invariant_to_amplitude():
    """残差整体放大 N 倍 → 判定结果**逐位相同**（density 是比值）。

    这正是它优于「梯度均值/绝对锐度」类判据的地方，也是它不需要 scale 参数的原因。
    """
    shape = (160, 160)
    boxes = [(8, 8, 48, 48), (96, 96, 48, 48)]
    cand = _cand_from_boxes(boxes, shape)
    residual = (_texture_residual(shape, boxes[0], amp=60.0)
                + _glyph_residual(shape, boxes[1], amp=80.0))

    out_1x = _suppress_texture_like(cand, residual)
    out_7x = _suppress_texture_like(cand, residual * 7.0)
    out_faint = _suppress_texture_like(cand, residual * 0.1)

    assert (out_1x == out_7x).all(), "放大 7 倍后判定必须不变"
    assert (out_1x == out_faint).all(), "缩小到 1/10 后判定必须不变"


def test_no_scale_parameter_needed():
    """签名刻意不含 scale（与自然光影守卫不同）——density 与尺度解耦。"""
    import inspect
    params = list(inspect.signature(_suppress_texture_like).parameters)
    assert params == ["cand", "residual_abs"]


# ---------------------------------------------------------------------------
# 3 · 阈值方向与开关
# ---------------------------------------------------------------------------

def test_threshold_monotonicity(monkeypatch):
    """阈值越高 → 抑制越少（保留像素数单调不减）。

    三个分档（稠密度 ≈0.82 / 0.46 / 0.10）保证曲线有三个可观测台阶。
    """
    shape = (160, 160)
    # 三个块必须**留出间隙**：8-连通下角点相邻会被合并成单个连通域
    # （本测试第二版踩过：两个块在 (112,112) 共角 → 合成一个组件，台阶少一个）。
    boxes = [(8, 8, 40, 40), (60, 60, 40, 40), (112, 112, 40, 40)]
    cand = _cand_from_boxes(boxes, shape)
    residual = (_texture_residual(shape, boxes[0])
                + _stripe_residual(shape, boxes[1])
                + _glyph_residual(shape, boxes[2]))
    counts = []
    for th in (0.05, 0.20, 0.60, 0.99):
        monkeypatch.setattr(config, "DETECT_TEXTURE_GUARD_DENSITY", th)
        counts.append(int((_suppress_texture_like(cand, residual) > 0).sum()))
    assert counts == sorted(counts), f"应单调不减，实际 {counts}"
    assert len(set(counts)) == 4, f"三档构造应给出四个不同台阶，实际 {counts}"


def test_guard_respects_config_flag(monkeypatch):
    """开关关闭时原样返回（零行为变更）。"""
    shape = (64, 64)
    cand = _cand_from_boxes([(8, 8, 32, 32)], shape)
    residual = _texture_residual(shape, (8, 8, 32, 32))
    monkeypatch.setattr(config, "DETECT_TRANSLUCENT_TEXTURE_GUARD", False)
    out = _suppress_texture_like(cand, residual)
    assert (out == cand).all()


def test_config_flag_defaults_on():
    assert config.DETECT_TRANSLUCENT_TEXTURE_GUARD is True


def test_default_threshold_is_in_calibrated_plateau():
    """默认阈值必须落在标定平台区 (0.62, 0.70] 内。

    平台区来自 docs/84 的双口径扫描：阈值 ≤0.62 时会打断第 02 图的召回
    （TP 1.00→0.50），≥0.64 起逐图零退化；>0.70 精度收益迅速衰减。
    本断言防止有人把阈值调回危险区或调到收益消失区。
    """
    th = config.DETECT_TEXTURE_GUARD_DENSITY
    assert 0.62 < th <= 0.70, f"默认阈值 {th} 不在标定平台区内"


# ---------------------------------------------------------------------------
# 4 · 安全性
# ---------------------------------------------------------------------------

def test_empty_and_tiny_components_are_safe():
    """空掩码不崩溃；极小组件不因统计量不稳定被误杀。"""
    shape = (48, 48)
    cand = np.zeros(shape, dtype=np.uint8)
    residual = np.zeros(shape, dtype=np.float32)
    assert not _suppress_texture_like(cand, residual).any()

    cand[0:2, 0:2] = 255
    out = _suppress_texture_like(cand, residual)
    assert out.shape == cand.shape
    assert out.dtype == cand.dtype


def test_single_pixel_component_does_not_crash():
    shape = (16, 16)
    cand = np.zeros(shape, dtype=np.uint8)
    cand[7, 7] = 255
    residual = np.ones(shape, dtype=np.float32) * 50.0
    out = _suppress_texture_like(cand, residual)
    assert out.shape == cand.shape


# ---------------------------------------------------------------------------
# 5 · 接线（经验 20：端到端测试抓接线错误）
# ---------------------------------------------------------------------------

def test_wired_into_detect_translucent(monkeypatch):
    """translucent 通路必须真的调用本守卫（防止「写了但没接上」）。"""
    import core.detector as det

    calls = {"n": 0}
    original = det._suppress_texture_like

    def spy(cand, residual_abs):
        calls["n"] += 1
        return original(cand, residual_abs)

    monkeypatch.setattr(det, "_suppress_texture_like", spy)
    img = np.full((96, 96, 3), 60, dtype=np.uint8)
    img[30:60, 30:60] = 120
    det.detect_watermark_mask(img, preset="general", use_ocr=False)
    assert calls["n"] >= 1, "detect_translucent 未调用纹理稠密守卫"


def test_guard_runs_after_natural_light_guard(monkeypatch):
    """两个守卫为**串联**关系：自然光影守卫必须先执行（顺序即语义）。"""
    import core.detector as det

    order = []
    orig_light = det._suppress_natural_light
    orig_tex = det._suppress_texture_like

    def spy_light(cand, residual_abs, scale):
        order.append("light")
        return orig_light(cand, residual_abs, scale)

    def spy_tex(cand, residual_abs):
        order.append("texture")
        return orig_tex(cand, residual_abs)

    monkeypatch.setattr(det, "_suppress_natural_light", spy_light)
    monkeypatch.setattr(det, "_suppress_texture_like", spy_tex)
    img = np.full((96, 96, 3), 60, dtype=np.uint8)
    img[30:60, 30:60] = 120
    det.detect_watermark_mask(img, preset="general", use_ocr=False)
    assert order[:2] == ["light", "texture"], f"守卫顺序错误：{order}"


def test_pipeline_guard_failure_falls_back(monkeypatch):
    """守卫抛异常时整条 translucent 检测的既有兜底路径仍工作（经验 18）。"""
    import core.detector as det

    def boom(*args, **kwargs):
        raise RuntimeError("探针异常")

    monkeypatch.setattr(det, "_suppress_texture_like", boom)
    img = np.full((64, 64, 3), 40, dtype=np.uint8)
    img[24:40, 24:40] = (90, 90, 90)
    res = det.detect_watermark_mask(img, use_ocr=False)
    assert res is not None
