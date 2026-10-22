# -*- coding: utf-8 -*-
"""阶段 317 · 修复侧真值探针（`scripts/removal_truth_probe.py`）的度量守卫。

为什么这些用例值得存在：探针产出的 `removal_gain / collateral / det_precision`
**将成为后续「只动水印区」A/B 的唯一尺子**（阶段 316 演练的待决项 A）。
尺子本身错了，后面所有结论都错——所以这里用**纯 numpy 构造**把每根轴钉死，
不跑模型、不依赖 GPU，毫秒级。

关键仪器性质（本文件逐条证明）：
  · 两根轴**互相独立**：只改水印 ⇒ gain 变、collateral 不变；只改水印外 ⇒ gain 不变、collateral 变。
  · 「检测框铺满全图」这种**过度检测**必须被 `det_precision` 抓到（冒烟实测 0.096）。
  · 合成器失灵（truth 为空）要判**用例无效**，不能算成产品 0 分。
"""
import importlib.util
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_spec = importlib.util.spec_from_file_location(
    "removal_truth_probe", os.path.join(_ROOT, "scripts", "removal_truth_probe.py"))
rtp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rtp)

H = W = 128
BX, BY, BW, BH = 90, 96, 26, 20      # 水印落点（右下，避开 truth 外扩区）


def _clean():
    """确定性渐变底图（无随机，跨机器可复现）。"""
    yy, xx = np.mgrid[0:H, 0:W]
    base = ((xx * 255) // W).astype(np.uint8)
    a = np.repeat(base[:, :, None], 3, axis=2).astype(np.int16)
    a[:, :, 1] = ((yy * 200) // H)
    return np.clip(a, 0, 255).astype(np.uint8)


def _marked(clean):
    m = clean.copy()
    m[BY:BY + BH, BX:BX + BW] = 250       # 强不透明角标
    return m


def _truth_area():
    return BW * BH


def test_truth_is_pixel_exact_by_construction():
    t = rtp.truth_mask(_clean(), _marked(_clean()))
    assert int(t.sum()) == _truth_area(), (
        f"truth 应当恰为被改动的像素，实得 {int(t.sum())} ≠ {_truth_area()}")
    assert t[BY:BY + BH, BX:BX + BW].all()
    assert not t[:BY, :BX].any()


def test_perfect_repair_maxes_gain_and_zeroes_collateral():
    clean, marked = _clean(), None
    marked = _marked(clean)
    d = rtp.measure(clean, marked, clean.copy(), np.zeros((H, W), np.uint8))
    assert d["valid"] and d["removal_gain"] == 1.0
    assert d["collateral_pct"] == 0.0 and d["residual"] == 0.0


def test_no_repair_zeroes_gain():
    clean = _clean()
    marked = _marked(clean)
    d = rtp.measure(clean, marked, marked.copy(), None)
    assert d["removal_gain"] == pytest.approx(0.0, abs=1e-3), (
        f"原样返回应得 0 分，实得 {d['removal_gain']}")
    assert d["note"] == "未检出任何水印" and d["det_recall"] == 0.0


def test_axes_are_independent_touching_only_clean_area_moves_collateral_only():
    """只动水印**外**的远处像素 ⇒ gain 必须仍是 1.0，collateral 必须涨。

    这是整把尺子最关键的一条：如果「乱改别处」能骗过 gain，那它就没资格做
    阶段 318「只动水印区」的判据。
    """
    clean = _clean()
    marked = _marked(clean)
    res = clean.copy()                 # 水印擦到位（truth 内 = 真值）
    far = np.zeros((H, W), bool)
    far[0:20, 0:20] = True             # 离 truth+DILATE 足够远
    out = ~rtp._dilate(rtp.truth_mask(clean, marked), rtp.DILATE)
    assert out[far].all(), "反例构造失败：改动区没落在 collateral 带内"
    assert not (far & rtp.truth_mask(clean, marked)).any(), (
        "反例构造失败：改动区与水印重合，测不到独立性")
    res[far] = np.clip(res[far].astype(np.int16) + 60, 0, 255).astype(np.uint8)

    d = rtp.measure(clean, marked, res, None)
    assert d["removal_gain"] == pytest.approx(1.0, abs=1e-3), (
        f"只动别处却拉低了擦除度，两轴不独立：{d['removal_gain']}")
    expected = 100.0 * 400.0 / float(out.sum())
    assert d["collateral_pct"] == pytest.approx(expected, rel=1e-2), (
        f"误伤轴没抓到全量改动，实得 {d['collateral_pct']} 期望 {expected:.2f}")


def test_full_frame_detection_is_caught_by_precision():
    """检测掩码铺满全图（冒烟实测的病）：recall 1.0 但 precision 必须塌。"""
    clean, marked = _clean(), _marked(_clean())
    det = np.full((H, W), 255, np.uint8)
    d = rtp.measure(clean, marked, clean.copy(), det)
    assert d["det_recall"] == 1.0
    assert d["det_precision"] == pytest.approx(_truth_area() / (H * W), abs=1e-3), (
        f"铺满全图的检测未被 precision 抓到：{d['det_precision']}")


def test_empty_truth_marks_case_invalid_not_product_zero():
    clean = _clean()
    d = rtp.measure(clean, clean.copy(), clean.copy(), None)
    assert d["valid"] is False and "truth 为空" in d["reason"]


def test_uint8_wrap_cannot_inflate_residual_into_negative_gain():
    """擦得几乎完美（比真值暗 2）时，gain 必须 ≈1，**不得**因 uint8 回绕变负。

    这条是 317 落地时真实踩到的坑：`np.abs(uint8(98) - uint8(100))` 得 254 而非 2，
    residual 被虚高到 254、gain 算成 −0.69。升位到 int16 后才正确。
    """
    clean = np.full((H, W, 3), 100, np.uint8)
    marked = clean.copy()
    marked[BY:BY + BH, BX:BX + BW] = 250           # 亮角标
    res = clean.copy()
    res[BY:BY + BH, BX:BX + BW] = 98               # 擦完略暗于真值（真·接近完美）
    d = rtp.measure(clean, marked, res, np.zeros((H, W), np.uint8))
    assert d["wm_strength"] == pytest.approx(150.0, rel=1e-3), f"强度被回绕污染：{d['wm_strength']}"
    assert d["residual"] == pytest.approx(2.0, rel=1e-2), f"残留被回绕虚高：{d['residual']}"
    assert d["removal_gain"] == pytest.approx(0.987, abs=0.02), (
        f"近完美擦除的 gain 异常：{d['removal_gain']}")
    assert 0.0 <= d["removal_gain"] <= 1.0, "gain 越界，判据不可用"


def test_dilate_of_empty_mask_stays_empty():
    """空掩码外扩后**仍须为空**——`cv2.dilate` 默认边界值是 DBL_MAX，对膨胀等于
    「图外全是前景」⇒ 会沿四边凭空生出一条 px 宽实心带（阶段 317 实测抓到）。
    不显式 `borderValue=0`，collateral 会少算一整圈画面边缘（角标在右下时，
    左上角被凭空吞进外扩带 ⇒ 真实误伤被漏计）。
    """
    empty = np.zeros((H, W), np.uint8)
    assert rtp._dilate(empty, 24).sum() == 0, (
        f"空掩码被外扩出 {int(rtp._dilate(empty, 24).sum())} 个像素 ⇒ 边界值未置零")


def test_psnr_cap_and_direction():
    clean = _clean()
    assert rtp.psnr(clean, clean) == 99.0
    marked = _marked(clean)
    assert rtp.psnr(marked, clean) < 99.0
    worse = marked.copy()
    worse[:, :] = np.clip(worse.astype(np.int16) + 30, 0, 255).astype(np.uint8)
    assert rtp.psnr(worse, clean) < rtp.psnr(marked, clean), (
        "PSNR 方向性反了，ΔPSNR 判据不可用")
