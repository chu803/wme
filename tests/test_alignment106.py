"""阶段 106 · 掩码—实体对齐收尾（overdraw 双向失准收敛）的回归测试。

阶段目标：把检测掩码与「水印实体」之间的面积差收回 —— 只削**假阳性**，不动真值。
本阶段落地三处（全部可逆、全部有探针标定）：

  ① `DETECT_GRID_TMPL_K_MULT = 1.3`
     网格通路的折叠模板门限此前只乘 `k`，从不乘余量 ⇒ 纹理经 N 个 tile 平均后残留的
     伪结构勉强越过门限、平铺后铺满全图（实测占网格通路假阳性面积的 82%）。
     抬到 1.3：**FP −76%，TP 逐位不变**（1.6 起开始掉 TP）。
  ② `DETECT_BOX_REFINE_PAD: 2 → 1`
     `_refine_regions_to_mask` 用 ellipse(2·pad+1) 膨胀证据，pad=2 时**每个证据像素
     变成 5×5 块**，对 1~2px 笔画是约 5× 的面积放大。
  ③ `DETECT_BOX_REFINE_K_SCALE: 0.5 → 0.65`
     pad 收敛后，阶段 103 里「k_scale=1.0 会伤 faint_tiled」的**几何耦合消失**，
     0.65 在新几何下对四类**单侧占优**（removal 持平或更好、overdraw/collateral 更低）。

本文件锁定：默认值、效果方向、红线「双约束」编码、以及一条承重值不可被误改。
"""
import os
import re
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
from core.detector import DetectionRegion  # noqa: E402
from scripts.detector_diag import BASE_KINDS, gt_mask  # noqa: E402
from scripts.e2e_real_probe import E2E_LIMITS  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402

#: 阶段 106 **改动前**（阶段 105 基线）的 10 图实测值 —— 红线必须低于它们，
#: 否则「把改动回退」不会被任何自动判定抓住（经验 75/97：改进必须被门禁守住）。
_PRE_106 = {
    "faint_tiled": {"overdraw": 1.445, "collateral": 2.129},
    "edge_text": {"overdraw": 4.996, "collateral": 1.193},
    "grid": {"overdraw": 6.206, "collateral": 1.727},
}


# ---------------------------------------------------------------------------
# 1. 默认值锁定
# ---------------------------------------------------------------------------

def test_defaults_locked():
    """三条改动的默认值必须与探针标定一致（防止被无声改回）。"""
    assert config.DETECT_GRID_TMPL_K_MULT == pytest.approx(1.3)
    assert config.DETECT_BOX_REFINE_PAD == 1
    assert config.DETECT_BOX_REFINE_K_SCALE == pytest.approx(0.65)


def test_merge_dilate_default_and_doc():
    """合并收口膨胀默认 1，且 config 里必须保留「不可关闭」的承重提示。

    依据（阶段 106 探针 `%TEMP%/s106_mergeG.py`）：把它设为 0 能再降 overdraw，
    但会把 corner_logo（唯一承诺类型）的 removal 从 0.3555 打到 **0.2264**（−36%，
    越过 e2e 红线 0.25）⇒ 这 1px 覆盖的是水印抗锯齿/羽化边缘，是承重结构。
    本用例把「这条警告必须留在源码里」变成机器可判定的约束。
    """
    assert config.DETECT_MERGE_DILATE == 1
    with open(os.path.join(_ROOT, "config.py"), "r", encoding="utf-8") as f:
        src = f.read()
    block = re.search(
        r"# 【合并阶段收口膨胀半径】.*?DETECT_MERGE_DILATE: Final\[int\] = .*?\n",
        src, re.S,
    )
    assert block, "config.py 里找不到 DETECT_MERGE_DILATE 的说明块"
    text = block.group(0)
    assert "不可关闭" in text, (
        "DETECT_MERGE_DILATE 的说明块丢失了「不可关闭」的承重提示 —— "
        "后人会以「降低 overdraw」为由把它设为 0，从而击穿 corner_logo 的 removal")
    assert "0.2264" in text, "说明块应保留「设为 0 会打穿 corner_logo removal」的实测证据"


# ---------------------------------------------------------------------------
# 2. 效果方向：模板门限抬升削假阳性而保真值
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_grid_tmpl_mult_cuts_fp_keeps_truth(monkeypatch):
    """网格模板门限 ×1.3：掩码面积**严格变小**，而真值覆盖**基本不变**。

    双向断言：这条守卫既要求「面积变小」（证明判据生效），又要求「真值覆盖不被吃掉」
    （证明不是无差别砍面积）—— 只测一侧的指标与恒绿门禁一样是哑巴（经验 97）。
    """
    cv2 = det._require_cv2()
    wm_fn, preset, _ = CASES["grid"]
    checked = 0
    for seed in (7, 11, 23):
        base = BASE_KINDS["textured"](seed, w=640, h=640)
        marked = wm_fn(base)
        truth = gt_mask(base, marked)
        n_truth = int(np.count_nonzero(truth))
        if n_truth == 0:
            continue
        area, cov = {}, {}
        for mult in (1.0, 1.3):
            monkeypatch.setattr(config, "DETECT_GRID_TMPL_K_MULT", mult)
            mask, _regions = det.detect_grid_watermark(
                marked, 0.6, 640 * 640,
                config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
            )
            m = np.asarray(mask) > 0
            area[mult] = int(np.count_nonzero(m))
            cov[mult] = int(np.count_nonzero(m & truth)) / n_truth
        if area[1.0] == 0:
            continue                       # 该 seed 上网格判据未触发，不构成对照
        checked += 1
        assert area[1.3] < area[1.0], (
            f"seed={seed}：模板门限 ×1.3 后掩码面积未变小（{area[1.3]} vs {area[1.0]}）"
            "—— 判据可能已失效")
        assert cov[1.3] >= cov[1.0] * 0.95, (
            f"seed={seed}：模板门限 ×1.3 把真值覆盖从 {cov[1.0]:.3f} 吃到 {cov[1.3]:.3f}"
            "（>5%）—— 削的是真水印而不是假阳性")
    assert checked >= 1, "三个 seed 上网格通路均未触发，本对照失效（需换底图种类）"


@pytest.mark.slow
def test_grid_tmpl_mult_revert_is_visible(monkeypatch):
    """反例自检：把倍数改回 1.0（旧行为）后掩码面积**必须回升** —— 守卫不是哑巴。"""
    wm_fn, _preset, _ = CASES["grid"]
    regained = False
    for seed in (7, 11, 23):
        base = BASE_KINDS["textured"](seed, w=640, h=640)
        marked = wm_fn(base)
        sizes = []
        for mult in (1.3, 1.0):
            monkeypatch.setattr(config, "DETECT_GRID_TMPL_K_MULT", mult)
            mask, _r = det.detect_grid_watermark(
                marked, 0.6, 640 * 640,
                config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
            )
            sizes.append(int(np.count_nonzero(np.asarray(mask) > 0)))
        if sizes[0] > 0 or sizes[1] > 0:
            if sizes[1] > sizes[0]:
                regained = True
            break
    assert regained, "改回 1.0 后掩码面积没有回升 ⇒ 本守卫测不到东西（哑巴守卫）"


# ---------------------------------------------------------------------------
# 3. 效果方向：框内证据膨胀 pad 单调收缩
# ---------------------------------------------------------------------------

def test_box_refine_pad_monotone_shrink():
    """pad 越小、框内掩码越小；且 pad=1（默认）严格小于 pad=2（旧值）。

    用 1px 细笔画（最坏情形）验证阶段 106 的机制解释：pad=2 的 5×5 膨胀
    会把每个笔画像素放大成 25 像素的块。
    """
    h, w = 200, 200
    evidence = np.zeros((h, w), np.uint8)
    evidence[100, 40:160] = 255                     # 1px 高、120px 长的笔画
    region = DetectionRegion(x=30, y=90, w=140, h=20, kind="edge_text",
                             confidence=0.6)
    areas = {}
    for pad in (0, 1, 2):
        m, dropped = det._refine_regions_to_mask([region], evidence, h, w, pad)
        assert dropped == 0
        areas[pad] = int(np.count_nonzero(m > 0))
    assert areas[0] < areas[1] < areas[2], f"pad 面积未单调递增：{areas}"
    assert areas[1] < areas[2], "pad=1 未比 pad=2 更小 —— 阶段 106 的收敛未生效"
    # 机制量：pad=2 把 120px 笔画放大到 ~(4*140+120)*5 量级（含两端外扩）
    assert areas[2] >= 3 * areas[0], (
        f"pad=2 相对 pad=0 的面积放大仅 {areas[2] / max(1, areas[0]):.2f}× —— "
        "与「5×5 膨胀对细笔画是几何放大器」的机制不符")


def test_evidence_degenerate_contract_intact():
    """证据场退化时 `_refine_regions_to_mask` 返回空 + dropped=n（调用方须回退整框）。

    这是阶段 103 的契约，阶段 106 改 pad/k_scale 不得破坏它 —— 否则「证据判据看不见」
    会被误读成「没有水印」而静默漏检。
    """
    h, w = 60, 60
    zeros = np.zeros((h, w), np.uint8)
    assert det._evidence_is_degenerate(zeros) is True
    region = DetectionRegion(x=10, y=10, w=20, h=10, kind="edge_text",
                             confidence=0.6)
    m, dropped = det._refine_regions_to_mask([region], zeros, h, w, 1)
    assert dropped == 1 and not np.any(m > 0), "退化证据场未按契约整框丢弃"


# ---------------------------------------------------------------------------
# 4. 红线「双约束」编码（数据守卫，无需跑门禁）
# ---------------------------------------------------------------------------

def test_redlines_are_below_pre_change_values():
    """每个被重标项的红线必须**低于阶段 106 之前的实测值**。

    约束来源：旧规则「新值 × 1.30」在改进幅度 <30% 时会让**回退后的数值落在红线内**
    （例：grid overdraw 旧值 6.206 < 1.30×5.015=6.52）⇒ 红线形同虚设。
    本用例把「回退必红」这条约束变成机器可判定的（经验 75/97）。
    """
    for case, metrics in _PRE_106.items():
        lim = E2E_LIMITS[case]
        for key, old in metrics.items():
            assert lim[key] < old, (
                f"{case}.{key} 红线 {lim[key]:g} 未低于改动前实测 {old:g} —— "
                "把改动回退后门禁不会变红（改进可静默回退，经验 75）")


def test_redlines_single_sided_for_improvement():
    """改进不会被判失败：红线方向必须单侧（removal 下界 / 其余上界）。

    数据结构本身即保证（每个 key 一个阈值）；此处断言当前值都满足红线，
    即「本阶段落地后门禁在基线上是绿的」这一事实被显式记录。
    """
    cur = {                                   # 阶段 106 落地后的 10 图实测
        "faint_tiled": {"removal": 0.0041, "collateral": 1.815,
                        "overdraw": 1.187, "spill": 0.009},
        "corner_logo": {"removal": 0.3555, "collateral": 1.053,
                        "overdraw": 0.886, "spill": 0.007},
        "edge_text": {"removal": 0.2415, "collateral": 1.024,
                      "overdraw": 3.799, "spill": 0.012},
        "grid": {"removal": 0.5063, "collateral": 1.515,
                 "overdraw": 5.015, "spill": 0.007},
    }
    for case, vals in cur.items():
        lim = E2E_LIMITS[case]
        assert vals["removal"] >= lim["removal"], f"{case} removal 越线"
        for key in ("collateral", "overdraw", "spill"):
            assert vals[key] <= lim[key], f"{case}.{key} 越线"
