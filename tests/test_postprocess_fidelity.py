"""阶段 60 · 后处理默认链的「去水印保真」回归测试。

背景（docs/60-*）：质量门禁测的是裸 inpaint，而 UI 实际输出是后处理增强后的结果。
用合成 ground truth 审计默认增强链（色彩还原 + 边缘融合 + 锐化）发现三个保真缺陷：
  1) 旧 edge_blend 把「原图」混回掩码边界带 —— 掩码内侧原图是水印像素，等于把刚擦掉
     的水印边缘重新涂回去（corner_logo 掩码区 PSNR 38.4 → 12.3 dB）；
  2) 旧 color_match 用「全图外部」统计 + std 重缩放 —— 渐变底图上把修复区拖向错误明暗
     （-18 dB）；即使改局部环带均值对齐，对稀疏/条带掩码（网格点阵、文字行）仍错位
     （-7 dB）→ 阶段 60 默认关闭色彩还原；
  3) 锐化施加到掩码内 —— unsharp 放大 inpaint 平滑面残留（gradient grid/edge -6~7 dB）
     → 锐化只作用于掩码外。

修复后默认链 = 安全的边界柔化（只用 result 自身、薄掩码跳过）+ 掩码外锐化 + 色彩还原
默认关闭：实测**掩码区 PSNR 零退化**（最差 Δ=0.0 dB，多例 +0.2~0.9）。本测试锁定这些
语义与「默认输出不比裸 inpaint 差」的红线。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import cv2  # noqa: E402
from services.postprocess_service import (  # noqa: E402
    postprocess,
    _color_match,
    _edge_blend,
    _sharpen,
)


def _masked_psnr(a, b, region):
    if not region.any():
        return float("nan")
    d = (a.astype(np.float64) - b.astype(np.float64))[region]
    mse = float(np.mean(d * d))
    return 100.0 if mse <= 1e-9 else 10.0 * np.log10(255.0 ** 2 / mse)


def _synth_solid():
    """水印块场景：均匀底 + 中央深色块（水印在掩码内）。"""
    h, w = 300, 400
    orig = np.full((h, w, 3), 210, np.uint8)
    cv2.rectangle(orig, (90, 90), (300, 200), (40, 40, 40), -1)  # 水印（原图）
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (90, 90), (300, 200), 255, -1)
    # 修复结果：块区填成与周围一致的干净背景（"擦干净"的理想输出）。
    result = np.full((h, w, 3), 210, np.uint8)
    cv2.rectangle(result, (90, 90), (300, 200), (205, 205, 205), -1)
    return orig, mask, result


# ---------------------------------------------------------------------------
# 1. edge_blend 语义：绝不把原图（水印）像素混回掩码内
# ---------------------------------------------------------------------------

def test_edge_blend_does_not_reintroduce_original_inside_mask():
    """掩码内（远离边界部分）必须保持 result，不得混回水印原图的深色像素。"""
    orig, mask, result = _synth_solid()
    out = _edge_blend(result, mask, orig, band=3)
    inside = mask > 0
    # 内部核（腐蚀 band 后）完全不混合原图 → 等于 result。
    k = 7
    core = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    assert np.any(core)
    assert np.array_equal(out[core], result[core]), \
        "边界柔化不得改动掩码内部核（会重新引入水印像素）"
    # 掩码整体均值仍接近干净 result（旧实现会显著向深色原图靠拢）。
    assert float(out[inside].mean()) > 150, \
        "掩码区被重新混入水印深色（旧 edge_blend 语义回归）"


def test_edge_blend_skips_thin_mask():
    """薄/条带掩码（无内部核）跳过边界柔化：输出与输入逐像素一致。"""
    h, w = 300, 400
    result = np.full((h, w, 3), 200, np.uint8)
    orig = result.copy()
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (0, 148), (w, 152), 255, -1)  # 4px 高的细条
    out = _edge_blend(result, mask, orig, band=3)
    assert np.array_equal(out, result), "无内部核的薄掩码应跳过边界柔化"


def test_sharpen_masked_area_preserved_by_postprocess():
    """默认链（含掩码外锐化）不得改动掩码内像素（薄掩码下边缘/色彩均被跳过/关闭）。"""
    orig, mask, result = _synth_solid()
    # 薄掩码：4px 高条带 → edge_blend 跳过；color_match 默认关闭 → 掩码内仅受锐化影响，
    # 而锐化被限制在掩码外 → 掩码内应与 raw 完全一致。
    mask2 = np.zeros_like(mask)
    cv2.rectangle(mask2, (0, 148), (400, 152), 255, -1)
    out = postprocess(result, original_rgb=orig, mask=mask2, superres=False,
                      real_superres=False)
    inside = mask2 > 0
    assert np.array_equal(out[inside], result[inside]), \
        "默认链不得改动掩码内像素（擦除结果应原样保留）"


# ---------------------------------------------------------------------------
# 2. color_match 语义（默认关闭，但保留的开启路径必须无害）
# ---------------------------------------------------------------------------

def test_color_match_default_off():
    """阶段 60 收敛：色彩还原默认关闭（修复区颜色不被二次移动）。"""
    assert config.POSTPROCESS_COLOR_MATCH is False


def test_color_match_uses_local_ring_mean_only():
    """开启 color_match 时：只向紧邻环带做均值对齐（不向全图外部、不重缩放 std）。"""
    h, w = 200, 600
    # 横向渐变底：左暗(40) → 右亮(220)。
    ramp = np.tile(np.linspace(40, 220, w, dtype=np.float32), (h, 1))
    orig = np.stack([ramp] * 3, axis=2).astype(np.uint8)
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (400, 60), (560, 140), 255, -1)   # 落在亮区
    # 修复结果比真实背景偏暗一点（模拟轻微色偏）。
    result = orig.copy()
    cv2.rectangle(result, (400, 60), (560, 140),
                  (int(orig[100, 480, 0] - 25),) * 3, -1)
    out = _color_match(result, mask, orig, strength=0.8)
    inside = mask > 0
    ring_mean = float(orig[:, :, 0][(cv2.dilate(mask, np.ones((3, 3), np.uint8),
                                                iterations=12) > 0)
                                    & (cv2.dilate(mask, np.ones((3, 3), np.uint8),
                                                  iterations=3) == 0)].mean())
    whole_ext_mean = float(orig[:, :, 0][mask == 0].mean())
    # 局部环带均值应显著高于全图外部均值（块在亮区），向环带对齐即向"亮"修正。
    assert ring_mean > whole_ext_mean + 20
    after = float(out[inside][:, 0].mean())
    before = float(result[inside][:, 0].mean())
    assert abs(after - ring_mean) < abs(before - ring_mean), \
        "color_match 应向紧邻环带均值对齐（而非全图外部均值）"


# ---------------------------------------------------------------------------
# 3. 默认链保真红线（真实管线，慢）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_default_chain_never_degrades_masked_psnr():
    """端到端：默认后处理链不得让掩码区 PSNR 低于裸 inpaint（最差 Δ=0.0，实测多例为正）。"""
    import core.detector as det
    from core.inpainter import inpaint
    from scripts.detector_diag import BASE_KINDS
    from scripts.make_demo_assets import CASES

    worst = float("inf")
    for kind in ("gradient", "textured", "photo"):
        base = BASE_KINDS[kind](7, w=640, h=640)
        for case in ("corner_logo", "grid", "edge_text"):
            marked = CASES[case][0](base)
            res = det.detect_watermark_mask(marked, preset=CASES[case][1],
                                            use_ocr=False)
            mask = res.mask
            if mask.ndim == 3:
                mask = mask[:, :, 0]
            mb = mask > 0
            raw = inpaint(marked, mask, engine="opencv")
            pp = postprocess(raw, original_rgb=marked, mask=mask,
                             superres=False, real_superres=False)
            delta = _masked_psnr(pp, base, mb) - _masked_psnr(raw, base, mb)
            worst = min(worst, delta)
    assert worst >= -0.01, \
        f"默认后处理链掩码区 PSNR 退化 {worst:.2f} dB（应为 ≥0）"
