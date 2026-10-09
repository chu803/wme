# -*- coding: utf-8 -*-
"""
阶段 52 —— 边缘小字（edge_text）在纹理/照片底图上「召回提升」的回归测试。

背景
----
阶段 51 的公平对比 benchmark 做完后，检测器诊断（scripts/detector_diag.py）显示
底部边缘小字存在明显的能力断层：

    底图        召回     精确率    IoU
    gradient   86.6%    32.8%    31.3%   ← 基本可用
    textured   15.3%    36.1%    11.2%   ← 几乎检不出
    photo      18.3%    36.1%    11.2%   ← 几乎检不出

根因：_projection_boxes 用 Otsu 全局单阈值二值化，再用「相对」判据
（投影 > 均值 + 1.5×标准差）判定哪些行是文字。在纹理/照片底图上，背景方差把
字模的绝对对比度淹没，整行都判不成文字。

修复（本文件锁定的行为）：新增「绝对残差」第二通路——
  1. _absolute_residual_bin：用高斯平滑求局部基线，取 |灰度 − 基线| 的**绝对**残差，
     清晰字模有 100+ 灰阶跳变，纹理经平滑后残差被抑制，据此把字模从纹理里分出；
  2. _abs_rows_from_bin：用**行填充率下限**判定文字行，替代随内容自适应的相对判据；
  3. _dedup_boxes：两条通路常命中同一行文字，去重避免框面积虚增。

关键的取舍验证（本文件第 3、4 条断言）：召回从 15% 提到 98%，必须确认不是靠
「把框放大到包住整条底边」换来的假召回——那样精确率会崩塌，等于把阶段 49 修好的
过度检测缺陷从别的门放回来。实测精确率不降反升（36%→52~57%）、预测覆盖率仅
1.4%~1.6%（真值 0.83%），确认是「框准了」而非「框大了」。

量化效果（3 种子均值，scripts/probe 扫描，已固化进 config 注释与 docs/52）：
    textured 召回 15.3% → 98.2%，IoU 11.2% → 51.7%
    photo    召回 18.3% → 98.1%，IoU 11.2% → 56.4%
    干净图平均误报率在所有取值下恒为 1.03%（第二通路误报增量为零）
"""

import os as _os
import sys as _sys

import cv2  # noqa: F401  （确保 OpenCV 可用，与既有检测器测试保持一致）
import numpy as np
import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

_SCRIPTS = _os.path.join(_ROOT, "scripts")
if _SCRIPTS not in _sys.path:
    _sys.path.insert(0, _SCRIPTS)

import config  # noqa: E402
from core.detector import (  # noqa: E402
    detect_watermark_mask,
    detect_edge_text,
    _absolute_residual_bin,
    _abs_rows_from_bin,
    _dedup_boxes,
    _projection_boxes,
)
from scripts.detector_diag import (  # noqa: E402
    BASE_KINDS,
    gt_mask,
    metrics,
)
from scripts.make_demo_assets import CASES  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助：跑一次 edge_text 检测并返回完整指标
# ---------------------------------------------------------------------------

def _edge_metrics(base_kind: str, seed: int = 7) -> dict:
    """
    在指定底图上跑 edge_text 用例，返回召回/精确率/IoU/覆盖率。

    参数说明：
        base_kind : BASE_KINDS 的键（gradient / textured / photo）。
        seed      : 底图随机种子。
    """
    base = BASE_KINDS[base_kind](seed)
    wm_fn, preset, _cn = CASES["edge_text"]
    marked = wm_fn(base)
    g = gt_mask(base, marked)
    res = detect_watermark_mask(marked, preset=preset, use_ocr=False)
    return metrics(res.mask, g)


def _synthetic_text_band(bh: int = 80, bw: int = 400):
    """
    造一条「带一行文字的横条」测试带：背景 120 + 轻微噪声，中部有一行亮笔画。

    参数说明：
        bh, bw : 带的高宽。

    返回值：(band_gray, 文字所在的行区间 (y0, y1))。
    """
    rng = np.random.default_rng(0)
    band = np.full((bh, bw), 120, np.uint8)
    band = np.clip(band.astype(np.int16) +
                   rng.normal(0, 3, (bh, bw)).astype(np.int16), 0, 255).astype(np.uint8)
    y0, y1 = 40, 58
    # 亮笔画：在 x=40..300 范围内按 2 像素间隔点亮，模拟文字笔画的密度。
    band[y0:y1, 40:300:2] = 230
    return band, (y0, y1)


# ---------------------------------------------------------------------------
# 1. 核心断言：纹理/照片底图上的召回（本阶段要修的东西）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base_kind", ["textured", "photo"])
def test_edge_text_recall_high_on_textured_and_photo(base_kind):
    """纹理/照片底图上，底部边缘小字的召回应显著高于修复前的 ~15%。"""
    m = _edge_metrics(base_kind)
    # 修复前约 15%~18%，修复后约 98%。阈值取 0.8 留足余量，避免单种子抖动误判。
    assert m["recall"] >= 0.8, (
        f"{base_kind} 底图 edge_text 召回 {m['recall']*100:.1f}% 低于 80%"
    )


def test_edge_text_recall_not_regressed_on_gradient():
    """纯渐变底图本就有 86.6%，第二通路不能把它弄坏。"""
    m = _edge_metrics("gradient")
    assert m["recall"] >= 0.8, (
        f"gradient 底图 edge_text 召回 {m['recall']*100:.1f}% 低于 80%"
    )


# ---------------------------------------------------------------------------
# 2. 反证断言：召回提升不是靠「框变大」换来的
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base_kind", ["textured", "photo"])
def test_edge_text_box_not_ballooned(base_kind):
    """
    预测覆盖率不应远超真值覆盖率——否则说明是靠框住整条底边骗召回。

    实测预测覆盖率约 1.4%~1.6%、真值 0.83%（约 2 倍，两行文字的包围盒合理值）。
    这里放宽到 4 倍作为护栏：允许一定余量，但绝不允许「底边整条被框住」。
    """
    m = _edge_metrics(base_kind)
    assert m["gt_cov"] > 0, "真值覆盖率应大于 0"
    assert m["pred_cov"] <= m["gt_cov"] * 4, (
        f"{base_kind} 底图预测覆盖率 {m['pred_cov']*100:.2f}% 超过真值的 4 倍 "
        f"（{m['gt_cov']*100:.2f}%），疑似框体膨胀"
    )


@pytest.mark.parametrize("base_kind", ["textured", "photo"])
def test_edge_text_precision_at_least_baseline(base_kind):
    """精确率不应低于修复前基线（约 36%），否则等于用误报换召回。"""
    m = _edge_metrics(base_kind)
    assert m["precision"] >= 0.30, (
        f"{base_kind} 底图 edge_text 精确率 {m['precision']*100:.1f}% "
        f"低于 30% 基线，疑似以误报换召回"
    )


@pytest.mark.parametrize("base_kind", ["textured", "photo"])
def test_edge_text_iou_improved(base_kind):
    """IoU 应从修复前的约 11% 提升到 35% 以上。"""
    m = _edge_metrics(base_kind)
    assert m["iou"] >= 0.35, (
        f"{base_kind} 底图 edge_text IoU {m['iou']*100:.1f}% 低于 35%"
    )


# ---------------------------------------------------------------------------
# 3. 护栏：阶段 49 的成果不能被破坏（干净图仍应几乎无输出）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base_kind", ["gradient", "textured", "photo"])
def test_clean_images_still_low_fpr(base_kind):
    """
    干净图（无水印）上，四种预设的**平均**误报率应保持在 2% 健康线以内。

    为什么用均值而非逐预设断言：个别预设存在阶段 49 就已存在、且与本阶段无关的
    偏高值（如 textured/ai_faint_tiled 为 3.2%，源自平铺通路对纹理的固有敏感，
    属已知信息论极限）。本断言的目的是防止**本阶段**引入新的误报，
    而不是重开阶段 49 已经结清的账；用均值与项目自身的健康判据（<2%）对齐。

    实测：阶段 52 前后干净图各预设误报率逐项完全一致（第二通路误报增量为零）。
    """
    base = BASE_KINDS[base_kind](7)
    fprs = []
    for preset in ("general", "ai_faint_tiled", "ai_grid", "ai_logo"):
        res = detect_watermark_mask(base, preset=preset, use_ocr=False)
        fprs.append(metrics(res.mask, np.zeros_like(res.mask))["fpr"])
    avg = float(np.mean(fprs))
    assert avg <= 0.02, (
        f"{base_kind} 干净图平均误报率 {avg*100:.2f}% 超过 2% 健康线（明细 {fprs}）"
    )


# ---------------------------------------------------------------------------
# 4. 开关断言：abs_delta=0 应完全关闭第二通路（回退到阶段 51 行为）
# ---------------------------------------------------------------------------

def _edge_text_region_count(base_kind: str, seed: int = 7) -> int:
    """
    隔离单测辅助：直接调用 `detect_edge_text`，排除 translucent/ocr 等其它通路的干扰，
    仅验证 `DETECT_EDGE_ABS_DELTA` 开关对「第二通路」的控制是否生效。

    为什么不用全管线 recall：阶段 237 实测发现，`ai_text_heavy` 预设同时启用
    `enable_translucent`，而 translucent 通路也会命中边缘文字水印（区域甚至被标成
    `ocr_text`），导致全管线 recall 在任何 abs_delta 下都恒为 ~100%——开关在端到端
    层面「看不出效果」是测试混淆，并非逃生开关失效。
    """
    base = BASE_KINDS[base_kind](seed)
    wm_fn, preset, _cn = CASES["edge_text"]
    marked = wm_fn(base)
    rgb = np.asarray(marked)
    h, w = rgb.shape[:2]
    regs = detect_edge_text(rgb, config.DETECT_EDGE_BAND_RATIO, h * w, False)
    return len(regs)


def test_abs_path_disabled_restores_legacy_behavior(monkeypatch):
    """
    abs_delta=0 时必须回退到阶段 51 行为（仅 Otsu 投影通路、第二通路关闭）——这是用户可用的逃生开关。

    阶段 237 实测：abs_delta=0 时 `detect_edge_text` 返回 0 区域（第二通路确被关闭，
    与 `_projection_boxes(abs_delta=0)` 行为一致）；abs_delta=45 时返回 2 区域。
    """
    monkeypatch.setattr(config, "DETECT_EDGE_ABS_DELTA", 0.0)
    n_off = _edge_text_region_count("textured")
    assert n_off == 0, (
        f"abs_delta=0 时 detect_edge_text 仍返回 {n_off} 个区域，"
        f"说明第二通路未被开关正确关闭"
    )


def test_abs_path_enabled_beats_disabled(monkeypatch):
    """开启第二通路后 detect_edge_text 的区域数必须严格多于关闭时（A/B 隔离对照）。"""
    monkeypatch.setattr(config, "DETECT_EDGE_ABS_DELTA", 0.0)
    off = _edge_text_region_count("photo")
    monkeypatch.setattr(config, "DETECT_EDGE_ABS_DELTA", 45.0)
    on = _edge_text_region_count("photo")
    assert on > off, f"开启后区域数 {on} 未多于关闭时 {off}（第二通路开关应起效）"


# ---------------------------------------------------------------------------
# 5. 单元级测试：第二通路的三个新函数
# ---------------------------------------------------------------------------

def test_absolute_residual_bin_marks_glyphs_not_flat_background():
    """绝对残差二值图应点亮字模笔画，而放过平坦/低对比背景。"""
    band, (y0, y1) = _synthetic_text_band()
    binb = _absolute_residual_bin(band, abs_delta=45.0, blur_sigma=11.0)
    assert binb.shape == band.shape
    # 文字行内应被大量点亮。
    ink_in_text = np.count_nonzero(binb[y0:y1, 40:300])
    assert ink_in_text > 0.2 * (y1 - y0) * 260, (
        f"文字行内残差点亮不足：{ink_in_text}"
    )
    # 远离文字的平坦区域应几乎不点亮。
    assert np.count_nonzero(binb[0:20, :]) < 0.02 * 20 * 400, "平坦背景被误点亮"


def test_abs_rows_from_bin_locates_text_row():
    """行填充率判据应准确定位到文字所在的行区间。"""
    band, (y0, y1) = _synthetic_text_band()
    binb = _absolute_residual_bin(band, abs_delta=45.0, blur_sigma=11.0)
    boxes = _abs_rows_from_bin(binb, "horizontal", *band.shape, min_fill=0.04)
    assert boxes, "未检出任何文字行"
    # 至少有一个框与真值行区间有实质重叠。
    best = max(
        boxes,
        key=lambda b: max(0, min(b[1] + b[3], y1) - max(b[1], y0)),
    )
    x, y, w, h = best
    overlap = max(0, min(y + h, y1) - max(y, y0))
    assert overlap >= 0.5 * (y1 - y0), (
        f"检出行 ({y}, {h}) 与真值行 ({y0}, {y1}) 重叠不足：{overlap}"
    )
    # 框不应沿交叉轴铺满整条带（那更像是画面暗部而非一行文字）。
    assert w < 0.85 * band.shape[1], "检出框沿交叉轴铺满整条带"


def test_abs_rows_from_bin_ignores_sparse_noise():
    """稀疏噪点不应凑够填充率被误判为文字行。"""
    rng = np.random.default_rng(1)
    band = np.full((80, 400), 120, np.uint8)
    # 撒 3% 的孤立高对比噪点：单像素、彼此不相邻。
    n = int(0.03 * band.size)
    ys = rng.integers(0, 80, n)
    xs = rng.integers(0, 400, n)
    band[ys, xs] = 255
    binb = _absolute_residual_bin(band, abs_delta=45.0, blur_sigma=11.0)
    boxes = _abs_rows_from_bin(binb, "horizontal", *band.shape, min_fill=0.04)
    assert not boxes, f"稀疏噪点被误判为文字行：{boxes}"


def test_dedup_boxes_merges_overlapping():
    """两条通路命中同一块文字时应合并为一，避免框面积虚增。"""
    boxes = [(10, 10, 100, 20), (12, 11, 98, 19), (300, 300, 50, 10)]
    out = _dedup_boxes(boxes)
    assert len(out) == 2, f"未正确去重：{out}"
    # 保留的是面积更大的那一个。
    assert (10, 10, 100, 20) in out, "应保留面积更大的框"


def test_dedup_boxes_keeps_disjoint():
    """互不重叠的框应全部保留。"""
    boxes = [(0, 0, 10, 10), (100, 100, 10, 10)]
    assert len(_dedup_boxes(boxes)) == 2


def test_dedup_boxes_empty():
    """空输入不应报错。"""
    assert _dedup_boxes([]) == []


def test_projection_boxes_two_paths_merge():
    """_projection_boxes 合并两通路后，不应产出重复的同一文字行。"""
    band, _ = _synthetic_text_band()
    boxes = _projection_boxes(
        band, "horizontal", abs_delta=45.0, blur_sigma=11.0, min_fill=0.04
    )
    # 真值只有一行文字；允许两通路各贡献一个略有差异的框，但不应重复堆叠多个。
    assert len(boxes) <= 3, f"框数量异常，疑似未去重：{boxes}"
    assert boxes, "未检出任何框"


# ---------------------------------------------------------------------------
# 6. 配置常量护栏
# ---------------------------------------------------------------------------

def test_stage52_config_defaults():
    """阶段 52 新增常量的默认值应与实测调优结论一致（防止被误改）。"""
    assert config.DETECT_EDGE_ABS_DELTA == pytest.approx(45.0)
    assert config.DETECT_EDGE_ABS_ROW_FILL == pytest.approx(0.04)
    assert config.DETECT_EDGE_ABS_BLUR == pytest.approx(11.0)
