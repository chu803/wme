"""阶段 58 · 网格通路高分辨率召回收敛的回归测试。

背景：网格水印叠加层是**绝对像素绘制**（不随输入分辨率缩放），而 grid 通路的
min_area 是纯相对量（随面积平方增长）——640² 时门限 20px < 网格点 ~28px 恰好能过，
896² 时门限已涨到 40px，真网格被整体滤空（召回 ~0，1024 AI 图静默漏检）。
阶段 58 把 grid 通路的 min_area 夹在「参考分辨率(640)等效绝对上限 20px」以内：
<640 维持纯相对（既有小图/单元测试行为不变），≥640 恒 ≤20px，绝对尺寸的网格点全程可过。

本测试锁定：① 896/1024 召回恢复；② 640 口径与阶段 54 发布值逐位一致（零回归）；
③ 干净图误报不反弹；④ 夹紧语义正确。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
from scripts.detector_diag import BASE_KINDS, gt_mask, metrics  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402

_CLEAN_PRESETS = ("general", "ai_faint_tiled", "ai_grid", "ai_logo")


def _detect(img, preset):
    m = det.detect_watermark_mask(img, preset=preset, use_ocr=False).mask
    return m[:, :, 0] if m.ndim == 3 else m


def _grid_metrics(size, kind, seed=7):
    """在 size×size 底图上合成 grid 水印，返回检测相对真值的指标字典。"""
    base = BASE_KINDS[kind](seed, w=size, h=size)
    wm_fn, preset, _ = CASES["grid"]
    marked = wm_fn(base)
    g = gt_mask(base, marked)
    return metrics(_detect(marked, preset), g)


# ---------------------------------------------------------------------------
# 1. 高分辨率召回恢复（阶段 58 核心）
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("size", [896, 1024])
@pytest.mark.parametrize("kind", ["gradient", "photo"])
def test_grid_recall_restored_high_res(size, kind):
    """896/1024 上网格召回必须恢复（阶段 58 前 gradient/photo 召回 ~0）。"""
    m = _grid_metrics(size, kind)
    assert m["recall"] >= 0.90, (
        f"{kind} {size}² 网格召回 {m['recall'] * 100:.1f}% < 90%（应 ≥90%）")
    assert m["iou"] >= 0.30, f"{kind} {size}² 网格 IoU {m['iou'] * 100:.1f}% < 30%"
    assert m["precision"] >= 0.30, (
        f"{kind} {size}² 网格精确率 {m['precision'] * 100:.1f}% < 30%")


# ---------------------------------------------------------------------------
# 2. 640 口径零回归（与阶段 54 发布值逐位一致）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_grid_640_gradient_unchanged():
    """gradient 640：与当前发布值一致（召回 ≥99% / 精确率 65.1% / IoU 65.0%）。

    阶段 103 同步说明：阶段 54 的原发布值为 38.6% / 38.5%。阶段 103 的「框内证据
    二次确认」把该口径下的精确率抬到 65.1%、IoU 抬到 65.0%，而**召回 99.7% 不变**
    （严格改进）。此处按新值同步夹具——**不是放宽断言**，容差仍是最初的 abs=0.02
    （阶段 81 教训：重标指标会连带打破把旧基线写死的用例，修法是同步夹具）。
    """
    m = _grid_metrics(640, "gradient")
    assert m["recall"] >= 0.99
    assert m["precision"] == pytest.approx(0.651, abs=0.02)
    assert m["iou"] == pytest.approx(0.650, abs=0.02)


@pytest.mark.slow
def test_grid_640_textured_unchanged():
    """textured 640：55.4% / 88.6%（召回未动，精确率较阶段 54 的 33.3% 提升 66%）。

    阶段 104 同步说明：阶段 54 的原发布值为 33.3% / 88.7%。阶段 104 给平铺增强
    通路接上「纹理稠密」守卫后，纹理底图上本通路不再把整片纹理当候选
    ⇒ 该口径精确率 33.3% → **55.4%**、IoU 0.517，而**召回 88.7% → 88.6%
    逐位不变**（严格改进）。容差保持 abs=0.02（同阶段 81/103 教训：同步夹具，
    不放宽断言）。

    为什么不是 63.3%：阶段 104 的单守卫隔离实验（docs/104 §8）显示「两道全接」
    可将本口径推到 63.3%，但同时会把「纹理 + 极淡平铺」整类抹零、把
    「渐变 + 网格」砍掉 28% 召回，并在真实照片上只多换 2.5~3.4 个百分点的削减
    —— 故最终只接纹理守卫，本夹具同步为该配置下的实测值。
    """
    m = _grid_metrics(640, "textured")
    assert m["recall"] >= 0.85
    assert m["precision"] == pytest.approx(0.554, abs=0.02)
    assert m["iou"] == pytest.approx(0.517, abs=0.02)


# ---------------------------------------------------------------------------
# 3. 小图碎片行为不被破坏（回归护栏：360²/12px 单元网格必须仍检出）
# ---------------------------------------------------------------------------

def test_grid_small_fragment_kept():
    """小图上纯相对 min_area（<20px）依旧放行碎片状真网格（阶段 57 回退的守护）。"""
    cv2 = det._require_cv2()
    gh = gw = 360
    grid = np.full((gh, gw, 3), 235, dtype=np.uint8)
    for gy in range(19, gh, 38):
        for gx in range(19, gw, 38):
            grid[gy - 6:gy + 6, gx - 6:gx + 6] = 110
    mask, regions = det.detect_grid_watermark(
        grid, 0.6, gh * gw,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    assert len(regions) > 0, "360²/12px 单元强网格被误滤（小图碎片回归）"
    assert np.any(mask > 0)


# ---------------------------------------------------------------------------
# 4. 干净图误报不反弹（高分辨率档抽查）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_clean_fpr_high_res_no_regression():
    """1024² 干净图四预设平均误报必须 < 2%（阶段 57 红线在高分辨率同样成立）。"""
    for kind in ("textured", "photo"):
        base = BASE_KINDS[kind](7, w=1024, h=1024)
        fprs = []
        for preset in _CLEAN_PRESETS:
            m = _detect(base, preset)
            fprs.append(float(np.count_nonzero(m > 0)) / m.size)
        assert np.mean(fprs) < 0.02, (
            f"{kind} 1024² 干净误报 {np.mean(fprs) * 100:.2f}% ≥ 2%")


# ---------------------------------------------------------------------------
# 5. 夹紧语义（纯逻辑，快）
# ---------------------------------------------------------------------------

def test_grid_min_area_cap_semantics():
    """grid min_area = min(相对, 640 等效上限 20px)；小图维持纯相对。"""
    # <640：相对值低于上限 → 用相对（360² 与旧行为一致）。
    assert det._min_area_px_floor(0.0002, relax=0.25) == 20
    small = max(1, min(int(360 * 360 * 0.0002 * 0.25),
                       det._min_area_px_floor(0.0002, relax=0.25)))
    assert small == 6            # 与阶段 57 前纯相对一致
    # ≥640：被夹在 20px（不再随面积平方增长）。
    for size in (896, 1024, 2048):
        rel = int(size * size * 0.0002 * 0.25)
        capped = max(1, min(rel, det._min_area_px_floor(0.0002, relax=0.25)))
        assert capped == 20, f"{size}² 上限应恒为 20px，实际 {capped}"
    # 640 边界：相对值恰等于上限 → min 不变。
    rel640 = int(640 * 640 * 0.0002 * 0.25)
    assert rel640 == 20
