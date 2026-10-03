"""阶段 105 · 掩码预处理「羽化不得冲淡内部」的确定性守卫。

背景（docs/105-*）
------------------
`inpaint()` 的融合权重直接取 `_prepare_mask()` 的返回值：
    m3 = proc_mask/255 ;  blended = image*(1-m3*s) + result*(m3*s)
所以 `proc_mask < 255` 的地方修复**只按比例施加**。旧实现（`proc = 高斯羽化(二值核)`）
对细笔画是毁灭性的：核 5 作用在 1px 笔画上，峰值只剩约 0.33×255 ≈ 85 ⇒ 融合权重 0.33
⇒「修了，但只修了三成」，留下明显残影。阶段 65 因此把膨胀调到 2（靠加粗笔画补回权重），
代价是掩码外 2px 全强度重绘：外溢 29.3%、过度重绘像素 2~5 倍。

阶段 105 修法：`proc = max(羽化(二值核), 二值核)` —— 羽化只许在掩码**外侧**加软坡。
本文件把这条机制钉死为可注入验证的断言：
 · H1 存在性证明：裸羽化确实会冲淡细笔画（若哪天 H1 变红，说明前提已变）；
 · H3 内部一律 255（正面守卫）；
 · H4 引擎实收掩码不得为空（防「细笔画静默不修」）；
 · H5 羽化不得改变掩码内的修复结果（端到端等价性）；
 · H6 外溢上界：改动区 ⊆ dilate(掩码, ceil(羽化/2))，且大面积掩码外溢 ≤5%；
 · H7 反例自检：内存里换回「羽化自由衰减」的旧实现，H3/H4/H5 的判据必须全部失效
   —— 守卫若对真实存在过的缺陷无反应，就是哑巴守卫（阶段 95 教训）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core.image_utils import feather_mask, threshold_mask  # noqa: E402
from core.inpainter import _prepare_mask, inpaint  # noqa: E402

THRESH = config.MASK_THRESHOLD_DEFAULT


def _line_scene(width=1, h=200, w=400, x0=200):
    """平滑渐变底图 + width 像素宽的暗色竖线（模拟水印笔画）+ 0/255 掩码。"""
    base = np.zeros((h, w, 3), np.uint8)
    xs = np.linspace(60, 200, w)
    for c in range(3):
        base[:, :, c] = np.tile((xs + 10 * c).astype(np.uint8), (h, 1))
    marked = base.copy()
    marked[:, x0:x0 + width] = 30
    mask = np.zeros((h, w), np.uint8)
    mask[:, x0:x0 + width] = 255
    return base, marked, mask


# ---------------------------------------------------------------------------
# H1 · 存在性证明：裸羽化确实冲淡细笔画（机制前提）
# ---------------------------------------------------------------------------

def test_H1_raw_feather_attenuates_thin_stroke():
    """1px 笔画经裸羽化后峰值必须落到阈值以下 —— 这是「必须 max 回去」的前提。

    若本断言变红（即裸羽化不再冲淡），说明 `feather_mask` 或阈值语义已变，
    H3/H4 就在守一个不存在的问题，需要重新评估。
    """
    _, _, mask = _line_scene(width=1)
    binary = threshold_mask(mask, thresh=THRESH)
    raw = feather_mask(binary, ksize=5)
    assert raw.max() < THRESH, (
        f"裸羽化后 1px 笔画峰值 {raw.max():.0f} 未低于阈值 {THRESH} —— 机制前提已变")


# ---------------------------------------------------------------------------
# H3 · 正面守卫：内部一律 255
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("width", [1, 2, 3, 5])
@pytest.mark.parametrize("feather", [0, 3, 5, 7])
@pytest.mark.parametrize("dilate", [0, 2])
def test_H3_interior_is_full_strength(width, feather, dilate):
    """掩码（经膨胀+阈值）内部的融合权重必须严格等于 255，任何宽度/羽化/膨胀组合都不许衰减。"""
    _, _, mask = _line_scene(width=width)
    proc = _prepare_mask(mask, dilate=dilate, feather=feather, thresh=THRESH)
    # 参照核 = 「膨胀 → 阈值」，与 proc 的内部定义同口径。
    from core.image_utils import dilate_mask
    ref = dilate_mask(mask, iterations=dilate) if dilate > 0 else mask.copy()
    ref = threshold_mask(ref, thresh=THRESH) > 0
    assert ref.any(), "夹具失效：参照核为空"
    assert proc[ref].min() == 255, (
        f"width={width} feather={feather} dilate={dilate}：掩码内部出现 "
        f"{proc[ref].min():.0f} < 255 —— 羽化又冲淡了内部")


@pytest.mark.parametrize("feather", [0, 3, 5, 7])
def test_H4_engine_mask_is_not_emptied_for_thin_strokes(feather):
    """1px 笔画必须仍然出现在「引擎实收掩码」里（否则是静默不修的 P0）。

    `bin_mask = threshold_mask(proc, thresh)`：若 proc 被羽化压到阈值以下，
    引擎收不到这块 ⇒ 不报错、不提示、修复静默不发生（与阶段 102 同族的静默空操作）。
    """
    _, _, mask = _line_scene(width=1)
    proc = _prepare_mask(mask, dilate=0, feather=feather, thresh=THRESH)
    bin_mask = threshold_mask(proc, thresh=THRESH)
    assert int(bin_mask.max()) == 255, (
        f"feather={feather}：1px 笔画的引擎掩码被清零 —— 细笔画会静默不修")
    assert int(np.count_nonzero(bin_mask)) >= int(np.count_nonzero(mask)), (
        f"feather={feather}：引擎掩码面积 {np.count_nonzero(bin_mask)} "
        f"小于给定掩码 {np.count_nonzero(mask)}")


# ---------------------------------------------------------------------------
# H5 · 端到端等价性：羽化不得改变掩码内的修复结果
# ---------------------------------------------------------------------------

def test_H5_feather_does_not_change_result_inside_mask():
    """同一图同一掩码，feather=0 与 feather=默认 的输出在**掩码内**必须逐像素一致。

    旧实现下 (0,0) 与 (0,5) 的掩码内结果差异很大（细笔画权重从 1.0 掉到 ~0.3）。
    """
    _, marked, mask = _line_scene(width=1)
    out0 = inpaint(marked, mask, engine="opencv", dilate=0, feather=0,
                   strength=1.0, denoise=0)
    out5 = inpaint(marked, mask, engine="opencv", dilate=0,
                   feather=config.MASK_FEATHER_DEFAULT, strength=1.0, denoise=0)
    sel = mask > 0
    diff = np.abs(out0.astype(np.int16) - out5.astype(np.int16))[sel].mean()
    assert diff <= 1.0, (
        f"掩码内 feather=0 与 feather={config.MASK_FEATHER_DEFAULT} 的平均差异 "
        f"{diff:.2f} > 1 灰阶 —— 羽化仍在冲淡掩码内部")
    assert not np.array_equal(out0, marked), "夹具失效：修复没有产生任何改动"


# ---------------------------------------------------------------------------
# H6 · 外溢上界（阶段 105 的主目标）
# ---------------------------------------------------------------------------

def test_H6_changed_pixels_stay_within_feather_support():
    """默认配置下，改动区必须落在 dilate(掩码, ceil(feather/2)) 之内（支撑包含关系）。

    这是「外溢」的数学上界：融合权重 `proc>0` 的支撑 = 二值核膨胀「羽化核半径」。
    一旦有人把膨胀调回 >0，本断言立刻红（膨胀的支撑扩张没有上界可言，只能靠红线守）。
    """
    _, marked, mask = _line_scene(width=3)
    f = config.MASK_FEATHER_DEFAULT
    out = inpaint(marked, mask, engine="opencv",
                  dilate=config.MASK_DILATE_DEFAULT, feather=f,
                  strength=1.0, denoise=0)
    changed = np.any(out != marked, axis=2)
    radius = (f + 1) // 2
    n = 2 * radius + 1
    import cv2
    allowed = cv2.dilate((mask > 0).astype(np.uint8),
                         np.ones((n, n), np.uint8)) > 0
    outside = int(np.count_nonzero(changed & ~allowed))
    assert outside == 0, (
        f"有 {outside} 个改动像素落在 dilate(掩码,{radius}) 之外 —— 支撑扩张超出羽化半径")


def test_H6_spill_ratio_under_five_percent_on_large_mask():
    """大面积掩码上的外溢率 ≤5%（阶段 105 的对外指标；小掩码的周长/面积比天然偏高）。"""
    h, w = 240, 400
    base, marked, mask = _line_scene(width=1, h=h, w=w)
    mask = np.zeros((h, w), np.uint8)
    mask[20:220, 40:360] = 255                     # 320×200 的实体块
    marked = base.copy()
    marked[mask > 0] = 40
    out = inpaint(marked, mask, engine="opencv",
                  dilate=config.MASK_DILATE_DEFAULT,
                  feather=config.MASK_FEATHER_DEFAULT, strength=1.0, denoise=0)
    changed = np.any(out != marked, axis=2)
    n_chg = int(changed.sum())
    assert n_chg > 0, "夹具失效：修复没有产生任何改动"
    spill = int(np.count_nonzero(changed & ~(mask > 0))) / n_chg
    assert spill <= 0.05, f"外溢 {spill:.1%} > 5%（改动 {n_chg} px）"


# ---------------------------------------------------------------------------
# H7 · 反例自检：还原旧实现，上述判据必须失效（证明守卫不是哑巴）
# ---------------------------------------------------------------------------

def _legacy_prepare(mask, dilate, feather, thresh):
    """阶段 105 之前的 `_prepare_mask`：羽化自由衰减（会冲淡细笔画）。"""
    from core.image_utils import dilate_mask, feather_mask
    m = mask
    if dilate > 0:
        m = dilate_mask(m, iterations=dilate)
    binary = threshold_mask(m, thresh=thresh)
    return feather_mask(binary, ksize=feather)


def test_H7_legacy_prepare_is_detected(monkeypatch):
    """在内存里换回旧实现，逐条确认 H1/H3/H4/H5 的判据都会失败。

    这是「守卫非空转」的证明：不改源码、完全可逆，且对**真实存在过的**缺陷生效。
    若哪天本用例的某个断言反过来失败，说明守卫已与该缺陷脱钩。
    """
    import core.inpainter as ip
    monkeypatch.setattr(ip, "_prepare_mask", _legacy_prepare)

    # H1：裸羽化确实把 1px 笔画压到阈值以下
    _, _, mask1 = _line_scene(width=1)
    ref = threshold_mask(mask1, thresh=THRESH) > 0
    legacy_bin = threshold_mask(mask1, thresh=THRESH)
    assert feather_mask(legacy_bin, ksize=5).max() < THRESH, "H1 前提失效"

    # H3：掩码内部出现 <255 的权重（旧实现下 H3 必然失败）
    proc = _legacy_prepare(mask1, 0, config.MASK_FEATHER_DEFAULT, THRESH)
    assert proc[ref].min() < 255, "旧实现竟然没有冲淡内部 —— H3 的判据与缺陷脱钩"

    # H4：1px 笔画的引擎掩码被清零（静默不修）
    assert int(threshold_mask(proc, thresh=THRESH).max()) == 0, "H4 的判据与缺陷脱钩"

    # H5：feather=0 与 feather=5 的掩码内结果差异远大于 1 灰阶
    _, marked, mask = _line_scene(width=1)
    out0 = inpaint(marked, mask, engine="opencv", dilate=0, feather=0,
                   strength=1.0, denoise=0)
    out5 = inpaint(marked, mask, engine="opencv", dilate=0,
                   feather=config.MASK_FEATHER_DEFAULT, strength=1.0, denoise=0)
    sel = mask > 0
    diff = np.abs(out0.astype(np.int16) - out5.astype(np.int16))[sel].mean()
    assert diff > 1.0, f"旧实现下掩码内差异仅 {diff:.2f} —— H5 的判据与缺陷脱钩"
