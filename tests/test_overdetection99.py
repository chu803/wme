# -*- coding: utf-8 -*-
"""
阶段 99 —— 过检收敛（`DETECT_MAX_COVERAGE_RATIO` 0.30 → 0.15）的回归与守卫。

背景（docs/99）
--------------
阶段 93 给真实图门禁补了「过检面积三线」，并登记了目标值
（coverage ≤ 0.05 / ratio ≤ 5），但一直没被兑现：30 张真实图实测
coverage **0.1618**、ratio **16.62×**，而红线是 0.25 / 25× —— 也就是说
**把过检原样退回收敛前水平，门禁也不会红**。

阶段 99 用探针把 16.18% 的面积按 kind 归因（corner_logo 9.0% / edge_text 4.8% /
translucent 2.4%），并把闸从 0.30 收敛为 0.15：

    cap=0.30 → coverage 0.1618 / ratio 16.62 / mask_iou 0.0635 / recall 1.0000
    cap=0.15 → coverage 0.1266 / ratio 13.00 / mask_iou 0.0699 / recall 1.0000

本文件锁四件事：
  1. **收敛值本身**（防被改回 0.30）；
  2. **该值高于全部已登记合成用例的覆盖率**（防被压到会切断合法用例的程度）；
  3. **这道闸是活的**（会咬、会按面积裁剪）——用确定性合成图，不依赖真实图是否存在；
  4. **红线 + 漂移能抓住「回退到收敛前」**——本阶段最重要的一条：
     只改行为不重标红线，收敛等于没做。
"""

from __future__ import annotations

import copy
import json
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                       # noqa: E402
from scripts import real_gate as rg  # noqa: E402
from core import detector as D      # noqa: E402

_SYN_BASELINE = os.path.join(_ROOT, "docs", "assets", "quality_baseline.json")
_REAL_BASELINE = os.path.join(_ROOT, "benchmark_real", "quality_baseline.json")

#: **当前**真实图实测值（`benchmark_real/quality_baseline.json` 的锚点）。
#: 阶段 99 = 覆盖率闸收敛（0.30→0.15）后；**阶段 100 = 角标 MSER 降级通路默认关闭后**；
#: **阶段 106 = 框内证据几何收敛（pad 2→1、k_scale 0.5→0.65）后**
#: （0.0745/7.65/0.1163 → 0.0365/3.75/0.1705；recall 1.0000 不变）。
#: ⚠ **阶段 183 重锚**：上述 106 值**全部标定于伪造真值期**——阶段 149 判定原 60 条
#:   corner_logo GT 系模板复制，172 收编 10 张真实豆包图并逐图实测真值后，177 全面重算
#:   并重建 `quality_baseline.json`。但 177 **只改了实现侧**（real_gate.py 红线 / 基线文件 /
#:   docs），**未同步本常量** ⇒ `test_real_baseline_matches_converged_values` 长期常红
#:   （「常红即失效」：漂移不变式守卫实际已死）。183 按当前基线文件重锚，并已用
#:   `real_gate.py --presets general --no-area`（杠杆 OFF 默认）复现：逐位一致。
CONVERGED = {"mask_coverage": 0.0291, "coverage_ratio": 2.80, "mask_iou": 0.2975}
#: 逐级回退锚点：99 级（只做了覆盖率闸）与 86 级（两个都没做）。
#: 全部落在新红线（0.045 / 4.35× / 0.10）外侧：mask_coverage 与 ratio 越高越坏
#: （0.1266/0.1618 > 0.045、13.00/16.62 > 4.35），mask_iou 越低越坏
#: （0.0699/0.0635 < 0.10）⇒ 两级回退都被**红线与漂移双重**拦下。
STAGE99_LEVEL = {"mask_coverage": 0.1266, "coverage_ratio": 13.00, "mask_iou": 0.0699}
STAGE86_LEVEL = {"mask_coverage": 0.1618, "coverage_ratio": 16.62, "mask_iou": 0.0635}
#: 阶段 100 级（相对阶段 106 即回退）：mask_coverage 0.0745 > 0.045、
#: ratio 7.65 > 4.35 ⇒ 越红线；而 mask_iou 0.1163 **高于**新红线 0.10
#: ⇒ 该指标只由漂移层拦（−31.8% > 15%）。这正是阶段 106「双约束」标定的边界，
#: 由 `test_reverting_to_stage100_level_caught_by_drift` 如实锁定。
STAGE100_LEVEL = {"mask_coverage": 0.0745, "coverage_ratio": 7.65, "mask_iou": 0.1163}


def _busy_image(n: int = 768, seed: int = 5) -> np.ndarray:
    """
    造一张「密集高对比标记 + 低频起伏底」的确定性图。

    它的作用是让检测器产出**远超**新默认 0.15 的覆盖率（实测 ~12.4%），
    从而可以在不依赖 `benchmark_real/images/`（gitignored）的前提下
    单测覆盖率闸的裁剪行为。随机噪声图不行——探测器会把纯噪声整体抑制掉（实测 0 区域）。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:n, 0:n]
    bg = np.full((n, n, 3), 128.0, dtype=np.float32)
    bg += (30.0 * np.sin(xx / 60.0))[..., None]
    for _ in range(160):
        cx, cy = rng.integers(20, n - 20, 2)
        s = int(rng.integers(14, 44))
        v = int(rng.integers(0, 60))
        bg[cy:cy + s, cx:cx + s] = v
    return np.clip(bg, 0, 255).astype(np.uint8)


def _coverage(img: np.ndarray, cap: float, monkeypatch) -> float:
    """在指定覆盖率上限下跑一次检测，返回掩码覆盖率。"""
    monkeypatch.setattr(config, "DETECT_MAX_COVERAGE_RATIO", cap)
    res = D.detect_watermark_mask(img, preset="general", use_ocr=False)
    mask = res.mask[:, :, 0] if res.mask.ndim == 3 else res.mask
    n = int(np.prod(img.shape[:2]))
    return int(np.count_nonzero(mask > 0)) / n


# ===========================================================================
# 1 · 收敛值本身
# ===========================================================================

def test_coverage_cap_default_is_converged():
    """默认覆盖率上限必须是阶段 99 收敛后的 0.15，不是收敛前的 0.30。"""
    assert config.DETECT_MAX_COVERAGE_RATIO == pytest.approx(0.15), (
        "DETECT_MAX_COVERAGE_RATIO 被改回 0.30（或其它值）——过检收敛已回退。"
        "改写理由必须同时更新 docs/99、real_gate 契约表与基线快照。"
    )


def test_cap_stays_above_config_validation_floor():
    """0.15 必须仍在配置自检的「未过低」区间内（校验 15 的门限是 0.10）。

    若有人把闸压到 0.10 以下，config.validate_config() 会开始报问题——
    那是「整图水印会被裁掉」的告警，本测试顺带把它锁住。
    """
    assert config.DETECT_MAX_COVERAGE_RATIO >= 0.10
    assert not any("DETECT_MAX_COVERAGE_RATIO" in p for p in config.validate_config())


# ===========================================================================
# 2 · 该值高于全部已登记合成用例（「不误杀」的直接证据）
# ===========================================================================

def test_cap_is_above_every_registered_synthetic_case():
    """默认上限必须**高于合成门禁全部 12 个用例的实测覆盖率**。

    这是「0.15 不会切断合法用例」的直接证据，而不是推断：合成门禁在 0.15 下
    逐项一字不变，正是因为没有任何用例的覆盖率够得着这道闸。
    若日后新增的大面积用例（如整幅平铺）覆盖率超过它，本测试会先红，
    强制在「收紧闸」与「该用例应被裁」之间做一次显式决策。
    """
    if not os.path.exists(_SYN_BASELINE):
        pytest.skip("合成基线快照不存在（未跑过 quality_gate.py --update）")
    with open(_SYN_BASELINE, encoding="utf-8") as f:
        syn = json.load(f)
    worst = max(r["pred_cov"] for r in syn["rows"])
    assert worst < config.DETECT_MAX_COVERAGE_RATIO, (
        f"合成用例最大覆盖率 {worst:.4f} 已达到/超过闸值 "
        f"{config.DETECT_MAX_COVERAGE_RATIO}——该用例会被裁，需显式决策"
    )


# ===========================================================================
# 3 · 闸是活的：会咬、按面积从小到大保留
# ===========================================================================

def test_cap_bites_and_crops_monotonically(monkeypatch):
    """闸值越小，保留的覆盖率越小——单调、且真的会裁剪。"""
    img = _busy_image()
    free = _coverage(img, 1.00, monkeypatch)
    assert free > 0.10, f"构造图未被检出足够面积（{free:.4f}），无法验证闸的行为"
    tight = _coverage(img, 0.02, monkeypatch)
    assert tight <= 0.02 + 1e-6, f"闸值 0.02 但覆盖率仍为 {tight:.4f}，闸未生效"
    assert tight < free, "收紧闸值后覆盖率未下降，闸是装饰品"


def test_cap_does_not_bite_at_default_on_synthetic_scale_content(monkeypatch):
    """默认 0.15 对「合成尺度」的内容**不生效**——这是取值的第二条依据。

    同一张图上 cap=1.00 与 cap=0.15 必须给出**逐位相同**的覆盖率：
    任何差异都说明 0.15 已经开始裁剪正常用例。
    """
    img = _busy_image()
    free = _coverage(img, 1.00, monkeypatch)
    at_default = _coverage(img, config.DETECT_MAX_COVERAGE_RATIO, monkeypatch)
    assert at_default == free, (
        f"默认闸值已开始裁剪：cap=1.00 时 {free:.4f}、cap={config.DETECT_MAX_COVERAGE_RATIO} "
        f"时 {at_default:.4f}——0.15 应高于全部合成尺度用例"
    )


# ===========================================================================
# 4 · 红线夹逼 + 漂移能抓住「回退到收敛前」
# ===========================================================================

@pytest.mark.parametrize("key", ["mask_coverage", "coverage_ratio"])
def test_max_red_lines_bracket_converged_measured(key):
    """`max` 型红线必须**严格宽于收敛后实测、且不超过其 2 倍**。"""
    _dir, thr = rg.REAL_PRESET_CONTRACT["general"][key]
    measured = CONVERGED[key]
    assert measured < thr, f"{key} 红线 {thr} 比收敛后实测 {measured} 还紧"
    assert thr <= measured * 2.0, f"{key} 红线 {thr} 超过实测的 2 倍，等于哑巴"


def test_mask_iou_red_line_brackets_converged_measured():
    _dir, thr = rg.REAL_PRESET_CONTRACT["general"]["mask_iou"]
    measured = CONVERGED["mask_iou"]
    assert measured / 2.0 <= thr < measured, f"mask_iou 红线 {thr} 未夹在合理区间"


def _real_baseline() -> dict:
    if not os.path.exists(_REAL_BASELINE):
        pytest.skip("真实图基线快照不存在（未跑过 real_gate.py --update）")
    with open(_REAL_BASELINE, encoding="utf-8") as f:
        return json.load(f)


def test_real_baseline_matches_converged_values():
    """基线快照必须已刷新到收敛后的值——否则漂移比对是在跟旧世界比。"""
    base = _real_baseline()
    gen = base["aggregate"]["per_preset"]["general"]
    for key, expected in CONVERGED.items():
        assert gen[key] == pytest.approx(expected, abs=1e-4), (
            f"基线 {key}={gen[key]} 与收敛后实测 {expected} 不一致——"
            f"未跑 `python scripts/real_gate.py --update`"
        )


@pytest.mark.parametrize("level,values", [
    ("阶段 99", STAGE99_LEVEL),
    ("阶段 86", STAGE86_LEVEL),
])
def test_injection_reverting_to_a_previous_level_is_caught(level, values):
    """**核心注入反例**：把过检退回任一历史水平，必须被**红线与漂移双重**拦下。

    构造一份「除过检三项外全等于基线、而三项=历史值」的 summary，与**真实基线**比对。

    阶段 99 的记录（当时的能力边界）：退回 0.1618 时**只有漂移抓得住**，
    红线（0.25 / 25× / 0.042）全在外侧——那是「只改行为不重标红线 = 收敛等于没做」
    的活证据。阶段 100 把红线压到实测的约 1/1.5（0.11 / 11.5× / 0.077）之后，
    **两级回退都落在红线外侧**，红线与漂移会同时报，故本测试由「要求漂移报」
    升级为「要求两者都报」。

    阶段 100 新增一级回退锚点 `STAGE99_LEVEL`：本阶段（MSER 关闭）把过检又降了一档，
    只守「回到 86 级」会漏掉「回到 99 级」这一更近、也更可能发生的回退。

    若本测试变红，说明红线或容差被放宽到了抓不住回退的程度。
    """
    base = _real_baseline()
    now = copy.deepcopy(base)
    gen = now["aggregate"]["per_preset"]["general"]
    for key, value in values.items():
        gen[key] = value

    ok, problems = rg.evaluate_real(now, base)
    assert not ok, f"过检退回{level}水平却仍判通过——收敛没有护栏"

    for key in values:
        hits = [p for p in problems if key in p]
        assert hits, f"{level} 回退：{key} 未被任何判定拦下（problems={problems}）"
        assert any("漂移" in p for p in hits), (
            f"{level} 回退：{key} 未由漂移判定拦下"
        )
        # 阶段 100 起红线也应报——否则说明红线被放宽回「哑巴」状态（阶段 99 的教训）。
        assert any("红线" in p for p in hits), (
            f"{level} 回退：{key} 只被漂移拦下、红线未报——红线已被放宽到守不住回退"
        )
    assert any("mask_coverage" in p and "上升" in p for p in problems), (
        "mask_coverage 是「越高越坏」，回退措辞应为「上升」而非「下滑」"
    )


def test_reverting_to_stage100_level_caught_by_drift():
    """阶段 106 的改进幅度（19~25%）小于红线余量 ⇒ 「退回阶段 100 级」由**漂移**兜住。

    这是阶段 106「双约束」标定（`scripts/e2e_real_probe.py` 同名说明）的边界如实登记：
    · mask_coverage 0.0745 > 新红线 0.045、coverage_ratio 7.65 > 4.35 ⇒ **红线也报**；
    · mask_iou 0.1163 **高于**新红线 0.10（该线只守「整幅塌陷」）⇒ 仅**漂移**报
      （−31.8% 远超 15% 容差）。
    若本用例变红，说明漂移容差被放宽到抓不住「退回上一档」——那正是阶段 75 警告的
    「改进可静默回退」。
    """
    base = _real_baseline()
    now = copy.deepcopy(base)
    gen = now["aggregate"]["per_preset"]["general"]
    for key, value in STAGE100_LEVEL.items():
        gen[key] = value

    ok, problems = rg.evaluate_real(now, base)
    assert not ok, "过检退回阶段 100 水平却仍判通过——收敛没有护栏"
    for key in STAGE100_LEVEL:
        hits = [p for p in problems if key in p]
        assert any("漂移" in p for p in hits), (
            f"阶段 100 级回退：{key} 未由漂移判定拦下（problems={problems}）")
    for key in ("mask_coverage", "coverage_ratio"):
        assert any("红线" in p for p in problems if key in p), (
            f"阶段 100 级回退：{key} 未由红线拦下（该级两指标应同时越新红线）")
