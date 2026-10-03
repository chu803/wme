# -*- coding: utf-8 -*-
"""
阶段 49 —— 检测器「过度检测」缺陷的回归测试。

背景
----
本项目开发到 48 个阶段时，检测器存在一个从未被测出的严重缺陷：
**无论图上有没有水印，都会把大片画面标记为水印。**

根因是半透明 / 平铺 / 网格三条通路都用「分位数」取阈值——

    thr = percentile(score, 100 × (1 − 0.85 × sensitivity))

这恒定选中固定比例的像素（sensitivity=0.55 时约 46.8%），与图像内容无关，
检测器因此完全丧失了「判定无水印」的能力。叠加「包围盒填充掩码」的放大后，
实测干净纹理图误报率 93.9%、干净照片图 88.0%。

这个缺陷能潜伏 48 个阶段，唯一原因是**缺少基线测试**：
此前的检测器测试只验证「不报错、流程通、覆盖率在 0~1 之间」，
从未断言「一张没有水印的图应该输出空掩码」。

本文件补上这条基线，并锁定以下修复：
  1. 绝对证据门限（_evidence_gate）：绝对幅度 ∧ 相对显著
  2. 形状感知掩码（_components_mask）：按连通域真实形状，而非包围盒
  3. 网格周期性判据（_periodic_strength）：连续残差场的二维 FFT 自相关
  4. 边缘小字几何约束（_projection_boxes）：收缩到墨迹实际范围 + 尺度合理性
  5. 全局覆盖率上限（DETECT_MAX_COVERAGE_RATIO）

量化效果（scripts/detector_diag.py，三种合成底图 × 四类水印）：
    干净图平均误报率  69.7% → 1.6%
    平均精确率         7.9% → 41.2%
    平均 IoU            7.9% → 35.7%
"""

import os as _os
import sys as _sys

import numpy as np
import pytest
import cv2

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

_SCRIPTS = _os.path.join(_ROOT, "scripts")
if _SCRIPTS not in _sys.path:
    _sys.path.insert(0, _SCRIPTS)

import config  # noqa: E402
from core.detector import (  # noqa: E402
    detect_watermark_mask,
    detect_translucent,
    detect_tiled_translucent,
    detect_grid_watermark,
    _residual_fields,
    _robust_sigma,
    _evidence_gate,
    _components_mask,
    _periodic_strength,
    _sensitivity_to_ksigma,
    _regions_to_mask,
    _projection_boxes,
)
import make_demo_assets as mda  # noqa: E402
from detector_diag import BASE_KINDS, gt_mask  # noqa: E402


# ==============================================================================
# 第 1 组：核心基线 —— 干净图不得被标记为水印
# ------------------------------------------------------------------------------
# 这是整个文件最重要的部分，也是此前 48 个阶段唯一缺失的测试。
# ==============================================================================

# 允许的最大误报率。取 5% 而非 2%：合成纹理是刻意构造的高频噪声，
# 比真实照片更恶劣；留一定余量避免测试对随机种子过于敏感。
_MAX_CLEAN_FPR = 0.05


@pytest.mark.parametrize("base_kind", ["gradient", "textured", "photo"])
def test_clean_image_yields_near_empty_mask(base_kind):
    """
    干净图（三种底图）在四个常用预设下，误报率都必须远低于 5%。

    这是「过度检测」缺陷的直接回归测试：修复前本项在纹理/照片底图上
    分别高达 93.9% / 88.0%，修复后三种底图均在 1% 量级。
    """
    base = BASE_KINDS[base_kind](7)
    for preset in ("general", "ai_faint_tiled", "ai_grid", "ai_logo"):
        res = detect_watermark_mask(base, preset=preset, use_ocr=False)
        fpr = float(np.count_nonzero(res.mask > 0)) / res.mask.size
        assert fpr < _MAX_CLEAN_FPR, (
            f"底图 {base_kind} 在预设 {preset} 下误报率 {fpr:.1%}，"
            f"超过上限 {_MAX_CLEAN_FPR:.0%}（过度检测缺陷复发）"
        )


def test_clean_image_far_below_watermarked_image():
    """
    同一张图「加水印前」的覆盖率必须显著低于「加水印后」。

    单看误报率不足以证明检测器有分辨力——一个恒返回空掩码的检测器也能
    通过上一项测试。本项确保它确实在响应水印，而不是一律说不。
    """
    base = BASE_KINDS["photo"](7)
    clean_res = detect_watermark_mask(base, preset="ai_logo", use_ocr=False)
    marked = mda.wm_corner_logo(base)
    marked_res = detect_watermark_mask(marked, preset="ai_logo", use_ocr=False)

    clean_cov = float(np.count_nonzero(clean_res.mask > 0)) / clean_res.mask.size
    marked_cov = float(np.count_nonzero(marked_res.mask > 0)) / marked_res.mask.size
    assert marked_cov > clean_cov * 3, (
        f"加水印后覆盖率 {marked_cov:.2%} 未显著高于干净图 {clean_cov:.2%}，"
        f"检测器对水印缺乏响应"
    )


# ==============================================================================
# 第 2 组：检测质量不能倒退 —— 真实召回与精确率下限
# ==============================================================================

def test_corner_logo_detection_quality():
    """
    角落 LOGO 在类照片底图上应同时保住高召回与高精确率。

    修复前：召回 100% 但精确率仅 3.2%（靠把整图都标上换来的假召回）；
    修复后：召回 100%、精确率 62%，IoU 由 3.2% 提升到 62%。
    """
    base = BASE_KINDS["photo"](7)
    marked = mda.wm_corner_logo(base)
    gt = gt_mask(base, marked) > 0
    res = detect_watermark_mask(marked, preset="ai_logo", use_ocr=False)
    pred = res.mask > 0

    recall = np.count_nonzero(pred & gt) / max(int(gt.sum()), 1)
    precision = np.count_nonzero(pred & gt) / max(int(pred.sum()), 1)
    assert recall > 0.5, f"角落 LOGO 召回率 {recall:.1%} 过低"
    # 修复前精确率仅 3.2%，这里把 30% 设为硬下限，防止过度检测复发。
    assert precision > 0.30, f"角落 LOGO 精确率 {precision:.1%} 过低（疑似过度检测）"


def test_grid_watermark_detection_quality():
    """
    网格水印应被真正检出（修复前在照片底图上 IoU 仅 4.3%，修复后 27.9%）。

    修复前网格检测靠「二值图行列投影自相关」判周期，该判据在随机散点上
    也给出 0.55~0.60 的虚高相关，导致干净图被误判为有网格、真实网格反而
    淹没在误报里。改用连续残差场的二维 FFT 自相关后才有真正的区分度。
    """
    base = BASE_KINDS["photo"](7)
    marked = mda.wm_grid(base)
    gt = gt_mask(base, marked) > 0
    res = detect_watermark_mask(marked, preset="ai_grid", use_ocr=False)
    pred = res.mask > 0

    recall = np.count_nonzero(pred & gt) / max(int(gt.sum()), 1)
    iou = np.count_nonzero(pred & gt) / max(int(np.count_nonzero(pred | gt)), 1)
    assert recall > 0.5, f"网格水印召回率 {recall:.1%} 过低"
    assert iou > 0.10, f"网格水印 IoU {iou:.1%} 过低（修复前约 4%）"


# ==============================================================================
# 第 3 组：_evidence_gate —— 绝对证据门限
# ==============================================================================

def test_evidence_gate_combines_both_conditions():
    """
    门限是「绝对幅度 ∧ 相对显著」的交集，两个条件缺一不可。

    只用相对显著：平滑图 σ≈0 时任何微小涨落都算多倍 σ（失效）。
    只用绝对幅度：纹理图残差天然很大，整片纹理都会被判为水印（失效）。
    """
    residual = np.array([[100.0, 100.0], [0.0, 0.0]], dtype=np.float32)
    signed = np.array([[10.0, 0.5], [10.0, 0.5]], dtype=np.float32)

    # 条件一单独看：(0,0) 与 (0,1) 都满足 |残差| ≥ 50。
    # 条件二单独看：(0,0) 与 (1,0) 都满足 |归一化| ≥ 1。
    # 交集：只有 (0,0) 同时满足。
    out = _evidence_gate(residual, signed, min_delta=50.0, k_sigma=1.0)
    assert out[0, 0] == 255, "同时满足两条件的像素应通过"
    assert out[0, 1] == 0, "相对显著不足的像素应被滤掉"
    assert out[1, 0] == 0, "绝对幅度不足的像素应被滤掉"
    assert out[1, 1] == 0


def test_evidence_gate_rejects_pure_noise():
    """
    纯噪声场不应产生大面积命中（这是分位数阈值做不到的）。

    分位数阈值恒定选中固定比例像素；绝对门限则让「无证据 → 无命中」成立。
    """
    rng = np.random.default_rng(0)
    # 标准正态噪声：局部标准差已知为 1，归一化残差 ≈ 标准正态。
    signed = rng.standard_normal((256, 256)).astype(np.float32)
    residual = np.abs(signed) * 10.0

    out = _evidence_gate(residual, signed, min_delta=0.0, k_sigma=2.5)
    hit_ratio = float(np.count_nonzero(out > 0)) / out.size
    # 标准正态 |x| ≥ 2.5σ 的理论比例约 1.24%，留 3 倍余量。
    assert hit_ratio < 0.04, f"纯噪声命中率 {hit_ratio:.2%} 过高，门限未生效"


def test_robust_sigma_estimates_noise_not_signal():
    """
    MAD 估计的 σ 必须反映背景噪声，而非被水印抬高（否则会自我 masking）。

    构造：90% 像素为 σ=1 的噪声，10% 像素为幅度 50 的强水印。
    若用标准差，σ 会被拉到 15 以上，导致门限水涨船高、反而检测不到水印。
    MAD 只认中位数，应稳定在 1 附近。
    """
    rng = np.random.default_rng(1)
    field = rng.standard_normal((256, 256)).astype(np.float32)
    field[:26, :] = 50.0  # 约 10% 的强水印污染

    sigma = _robust_sigma(field)
    assert 0.7 < sigma < 1.5, f"稳健 σ 估计为 {sigma:.2f}，应接近真实噪声 1.0"
    # 对比：标准差会被污染抬高一个数量级，正是我们要避免的。
    assert float(field.std()) > 10.0, "本用例的污染强度不足以体现 MAD 的优势"


def test_sensitivity_to_ksigma_monotonic_and_anchored():
    """敏感度越高 → 门限越低；且在 0.5 处等于配置锚点值。"""
    lo = _sensitivity_to_ksigma(1.0)
    mid = _sensitivity_to_ksigma(0.5)
    hi = _sensitivity_to_ksigma(0.0)
    assert lo < mid < hi, "敏感度与门限应单调反向"
    assert abs(mid - config.DETECT_SIGMA_K) < 1e-9, "0.5 处应等于配置锚点"
    # 越界输入应被裁剪而非产生荒谬值。
    assert _sensitivity_to_ksigma(99.0) == _sensitivity_to_ksigma(1.0)
    assert _sensitivity_to_ksigma(-99.0) == _sensitivity_to_ksigma(0.0)


# ==============================================================================
# 第 4 组：_components_mask —— 形状感知掩码
# ==============================================================================

def test_components_mask_preserves_shape_not_bbox():
    """
    掩码必须按连通域真实形状填充，而不是包围盒。

    一条对角线笔画的包围盒几乎覆盖整张图；形状感知掩码只覆盖笔画本身。
    修复前正是这个放大把 46.8% 的候选密度推到 93.8% 的覆盖率。
    """
    h, w = 100, 100
    binary = np.zeros((h, w), dtype=np.uint8)
    # 画 3px 宽的对角线：1px 细线会被 _components_mask 内部的开运算抹掉
    # （开运算本就是用来去噪点的），那样就测不到「形状 vs 包围盒」了。
    for i in range(0, 100, 4):
        binary[i:i + 3, i:i + 3] = 255        # 主对角线
        binary[i:i + 3, 99 - i:102 - i] = 255  # 副对角线

    mask = _components_mask(binary, min_area=1, max_area=h * w, h=h, w=w, dilate=0)
    cov = float(np.count_nonzero(mask > 0)) / mask.size

    # 两条 100px 长的 3px 宽对角线 → 约 600px，占 6%。包围盒会给出接近 100%。
    assert cov < 0.20, f"形状掩码覆盖率 {cov:.1%}，疑似退化成包围盒填充"
    assert cov > 0.0, "对角线笔画应被保留"


def test_components_mask_respects_area_bounds():
    """面积过滤与膨胀参数都应生效。"""
    binary = np.zeros((60, 60), dtype=np.uint8)
    binary[5:15, 5:15] = 255     # 100px，保留
    binary[40:42, 40:42] = 255   # 4px，过滤掉

    mask = _components_mask(binary, min_area=50, max_area=400,
                            h=60, w=60, dilate=0)
    assert mask[10, 10] == 255, "大块应保留"
    assert mask[41, 41] == 0, "小于 min_area 的碎块应被丢弃"

    # 膨胀应让掩码变大。
    grown = _components_mask(binary, min_area=50, max_area=400,
                             h=60, w=60, dilate=3)
    assert np.count_nonzero(grown) > np.count_nonzero(mask), "膨胀未生效"


# ==============================================================================
# 第 5 组：_periodic_strength —— 网格周期性判据
# ==============================================================================

def test_periodic_strength_detects_true_periodicity():
    """
    规则平铺图案应有强自相关峰；随机纹理不应有。

    这是网格检测的主判据。旧判据（二值图行列投影自相关）在随机纹理上
    给出 0.55~0.60 的虚高值，与本项要检测的 0.119~0.188 无法区分。
    """
    rng = np.random.default_rng(2)
    n = 256
    # 规则网格：每 32px 放一个 4×4 的实心块。
    # 注意别用 [::32, ::32] = 1.0 —— 那只会选中 8×8=64 个孤立像素，
    # 信号能量（约 56）会被 65536 个噪声像素（约 5900）彻底淹没，
    # 自相关峰值只有 0.018，测不出任何东西。
    periodic = np.zeros((n, n), dtype=np.float32)
    for gy in range(0, n, 32):
        for gx in range(0, n, 32):
            periodic[gy:gy + 4, gx:gx + 4] = 2.0
    periodic += rng.standard_normal((n, n)).astype(np.float32) * 0.3

    # 纯随机纹理（同强度噪声，无周期性）。
    aperiodic = rng.standard_normal((n, n)).astype(np.float32) * 0.3

    peak_p, py, px = _periodic_strength(periodic)
    peak_a, _, _ = _periodic_strength(aperiodic)

    assert peak_p > config.DETECT_GRID_MIN_ACF, (
        f"规则网格自相关峰值 {peak_p:.3f} 未超过阈值 "
        f"{config.DETECT_GRID_MIN_ACF}（漏检）"
    )
    assert peak_a < config.DETECT_GRID_MIN_ACF, (
        f"随机纹理自相关峰值 {peak_a:.3f} 超过阈值（误报）"
    )
    # 周期沿两轴分别求：可分离网格的二维峰落在轴上（acf(32,0)=a > acf(32,32)=a²），
    # 所以必须分别取 acf[:,0] 与 acf[0,:] 的峰，才能还原出真实的 (32, 32)。
    assert py % 32 == 0, f"纵向周期 {py} 与真实周期 32 不符"
    assert px % 32 == 0, f"横向周期 {px} 与真实周期 32 不符"


def test_periodic_strength_handles_degenerate_input():
    """常量场、空场等退化输入不得抛异常，且应报告「无周期性」。"""
    assert _periodic_strength(np.zeros((64, 64), np.float32)) == (0.0, 0, 0)
    assert _periodic_strength(np.full((64, 64), 3.0, np.float32)) == (0.0, 0, 0)
    # 含 NaN/Inf 时也应稳健（nan_to_num 兜底）。
    bad = np.full((64, 64), np.nan, np.float32)
    bad[::16, ::16] = 1.0
    peak, _py, _px = _periodic_strength(bad)
    assert np.isfinite(peak)


# ==============================================================================
# 第 5.5 组：_fold_template / _tile_out —— 周期折叠模板
# ==============================================================================

def test_fold_template_averages_out_noise():
    """
    折叠 N 个 tile 求平均应把噪声压低 √N 倍，而保留周期信号。

    这是网格掩码精度的关键：单个 tile 里的水印常常弱到无法与纹理区分，
    但几十个 tile 对齐平均后，信号相干叠加、噪声随机相消。
    """
    from core.detector import _fold_template, _tile_out

    rng = np.random.default_rng(3)
    n = 256
    period = 32
    field = rng.standard_normal((n, n)).astype(np.float32)
    # 在每个 tile 的同一位置 (10,10) 叠加一个幅度 3.0 的信号。
    # 幅度取 3.0 是为了让「折叠平均后」的信号（3.0 ± 1/√64）稳稳高于 2.0，
    # 不至于因为某个随机种子的噪声均值偏一点就 flaky。
    for ty in range(0, n, period):
        for tx in range(0, n, period):
            field[ty + 10, tx + 10] += 3.0

    template, n_tiles = _fold_template(field, period, period)
    assert n_tiles == (n // period) ** 2, "tile 计数错误"
    # 信号位置应显著高于其余位置（其余已被平均压平）。
    assert template[10, 10] > 2.0, f"周期信号未被保留：{template[10, 10]:.3f}"
    assert float(np.abs(template).max()) >= template[10, 10]
    # 折叠后的背景噪声应远小于原始噪声（√64 = 8 倍增益）。
    bg = np.delete(template.ravel(), 10 * period + 10)
    assert float(np.abs(bg).mean()) < 0.3, "噪声未被折叠平均压低"

    # 平铺回去应覆盖整幅尺寸。
    tmpl_mask = (np.abs(template) > 2.0).astype(np.uint8) * 255
    full = _tile_out(tmpl_mask, n, n)
    assert full.shape == (n, n)
    assert np.count_nonzero(full) == (n // period) ** 2, "平铺后命中数应等于 tile 数"


def test_fold_template_handles_degenerate_period():
    """周期非法（0 / 负数 / 超过图幅）时应退化为「不折叠」，不得越界。"""
    from core.detector import _fold_template

    field = np.arange(64 * 64, dtype=np.float32).reshape(64, 64)
    for py, px in ((0, 0), (-5, 16), (16, 0), (999, 999)):
        tmpl, n = _fold_template(field, py, px)
        assert tmpl.size > 0 and n >= 1
        # 不允许出现超出原图尺寸的模板。
        assert tmpl.shape[0] <= 64 and tmpl.shape[1] <= 64


# ==============================================================================
# 第 6 组：_projection_boxes —— 边缘小字几何约束
# ==============================================================================

def test_projection_boxes_rejects_full_width_bands():
    """
    铺满整条带的「暗带」不应被当成一行文字。

    修复前每个投影命中段都生成横跨整条带的包围盒，产出 640×6、8×640 这类
    绝不可能是文字的框，在干净图上造成 0.8%~3.4% 的误报。
    """
    # 构造一个「下半部分整体偏暗」的带：投影会命中，但它是一条暗带而非文字。
    band = np.full((80, 640), 200, dtype=np.uint8)
    band[40:, :] = 60  # 下半整条变暗，横跨全部 640 列

    boxes = _projection_boxes(band, "horizontal")
    for (_x, _y, bw, bh) in boxes:
        assert bw < 0.85 * 640, (
            f"出现了横跨整条带的包围盒（宽 {bw}），几何约束未生效"
        )


def test_projection_boxes_locates_text_run():
    """
    真实的一行文字应被定位到其横向范围内，而不是整条带。

    注意带的高度要显著大于文字行高：投影阈值是 mean + 1.5·std，
    若文字行占了带的一大半，mean 被抬高、阈值随之抬高，反而检不出。
    """
    band = np.full((120, 640), 200, dtype=np.uint8)
    # 在 y∈[40,65]、x∈[50,250] 处画一排离散笔画（模拟一行文字）。
    for x in range(50, 250, 5):
        band[40:65, x:x + 3] = 40

    boxes = _projection_boxes(band, "horizontal")
    assert boxes, "真实文字行未被检出"
    x0, y0, bw, bh = boxes[0]
    # 横向范围应贴近 [50, 250]，远小于整条带宽度 640。
    assert bw < 0.6 * 640, f"文字框宽度 {bw} 未收缩到墨迹实际范围"
    assert bh >= 4, f"文字框高度 {bh} 过薄，疑似投影噪声"
    # 尺度上限参照「带的长边」（此处为宽 640，上限 0.15×640=96px），
    # 而不是短边——短边只有 120px，会把真实文字也一并拒掉。
    assert bh <= 0.15 * 640, f"文字框高度 {bh} 过厚，疑似整条暗带"


# ==============================================================================
# 第 7 组：全局覆盖率上限
# ==============================================================================

def test_coverage_cap_limits_total_mask(monkeypatch):
    """
    覆盖率超过上限时，必须裁剪到上限内（宁可漏检，不可滥杀）。

    设计依据：把大半个画面交给修复算法重绘 = 照片被彻底毁掉；
    而漏掉一小块水印只是残留瑕疵，用户还能手动补。
    """
    img = np.full((200, 200, 3), 128, dtype=np.uint8)
    # 每 10px 画一个亮块，制造大量候选区域，逼迫覆盖率冲高。
    for y in range(0, 200, 10):
        for x in range(0, 200, 10):
            cv2.circle(img, (x + 5, y + 5), 4, (250, 250, 250), -1)

    monkeypatch.setattr(config, "DETECT_SIGMA_K", 0.5)      # 极度敏感
    monkeypatch.setattr(config, "DETECT_MIN_DELTA", 0.0)
    monkeypatch.setattr(config, "DETECT_MAX_COVERAGE_RATIO", 0.30)
    monkeypatch.setattr(config, "DETECT_MAX_AREA_RATIO", 0.9)

    res = detect_watermark_mask(img, preset="general", use_ocr=False)
    cov = float(np.count_nonzero(res.mask > 0)) / res.mask.size
    # 留一点形态学膨胀的余量（膨胀在裁剪之后不再执行，故只需小余量）。
    assert cov <= 0.35, f"覆盖率 {cov:.1%} 未被上限 30% 约束住"


def test_gradient_preset_exempt_from_coverage_cap(monkeypatch):
    """
    全局渐变预设按设计整图标记，不受覆盖率上限约束。

    否则 ai_gradient 会被这道安全闸彻底清空——那是用户主动选择的模式。
    """
    h, w = 160, 160
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.uint8)
    img[:, :, 0] = (xx / w * 255).astype(np.uint8)
    img[:, :, 1] = (yy / h * 255).astype(np.uint8)
    img[:, :, 2] = 128

    monkeypatch.setattr(config, "DETECT_MAX_COVERAGE_RATIO", 0.10)

    res = detect_watermark_mask(img, preset="ai_gradient", use_ocr=False)
    if res.regions:  # 渐变判据未必在每张合成图上都触发
        cov = float(np.count_nonzero(res.mask > 0)) / res.mask.size
        assert cov > 0.10, "渐变预设被覆盖率上限误伤，应予以豁免"


# ==============================================================================
# 第 8 组：配置自检新增项
# ==============================================================================

def test_config_checks_cover_new_thresholds(monkeypatch):
    """阶段 49 新增的两条配置校验（检查 14 / 15）必须生效。"""
    # 先确认默认配置本身是干净的（避免后面的告警检查被默认值干扰）。
    assert config.validate_config() == [] or all(
        "DETECT_SIGMA_K" not in p and "DETECT_MAX_COVERAGE_RATIO" not in p
        for p in config.validate_config()
    )

    monkeypatch.setattr(config, "DETECT_SIGMA_K", 0.8)
    assert any("DETECT_SIGMA_K" in p for p in config.validate_config()), \
        "相对显著倍数过低时应告警"

    monkeypatch.setattr(config, "DETECT_MAX_COVERAGE_RATIO", 0.05)
    assert any("DETECT_MAX_COVERAGE_RATIO" in p for p in config.validate_config()), \
        "覆盖率上限过低时应告警"


def test_new_config_keys_have_sane_defaults():
    """阶段 49 新增的配置项应处于实测有效区间内。"""
    assert 0.0 <= config.DETECT_MIN_DELTA <= 64.0
    # 低于 1.5 时诊断显示误报率会回升到 8% 以上。
    assert config.DETECT_SIGMA_K >= 1.5
    assert 0.05 <= config.DETECT_MAX_COVERAGE_RATIO <= 1.0
    assert 0.0 <= config.DETECT_GRID_MIN_ACF <= 1.0
    # 默认值应落在「干净图 0.052」与「网格水印 0.119」之间。
    assert 0.06 <= config.DETECT_GRID_MIN_ACF <= 0.11


# ==============================================================================
# 第 9 组：向后兼容 —— 关闭形状感知掩码应回退到修复前行为
# ==============================================================================

def test_shape_aware_mask_switch_is_reversible(monkeypatch):
    """
    DETECT_SHAPE_AWARE_MASK=False 应回退到「包围盒填充」的旧行为。

    保留这个开关是为了排查与对比：若新掩码在生产环境出问题，
    可一键回到旧逻辑而不必回滚代码。
    """
    base = BASE_KINDS["photo"](7)
    marked = mda.wm_corner_logo(base)

    monkeypatch.setattr(config, "DETECT_SHAPE_AWARE_MASK", False)
    res_old = detect_watermark_mask(marked, preset="ai_logo", use_ocr=False)
    monkeypatch.setattr(config, "DETECT_SHAPE_AWARE_MASK", True)
    res_new = detect_watermark_mask(marked, preset="ai_logo", use_ocr=False)

    cov_old = float(np.count_nonzero(res_old.mask > 0)) / res_old.mask.size
    cov_new = float(np.count_nonzero(res_new.mask > 0)) / res_new.mask.size
    # 形状感知掩码不应比包围盒更大。
    assert cov_new <= cov_old + 1e-6, (
        f"形状感知掩码覆盖率 {cov_new:.2%} 反而大于包围盒 {cov_old:.2%}"
    )
