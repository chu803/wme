"""阶段 65 · 掩码「膨胀 / 羽化」参数真值标定的回归测试（阶段 105 修正）。

背景（docs/65-*）：`MASK_DILATE_DEFAULT` 曾为 8（从未标定）。真值扫描（textured/photo ×
corner/edge/grid）发现**膨胀单调有害**——残留不随膨胀下降，过度重绘线性上升
（grid 过度像素 2484→38135，15 倍），根因是检测器掩码本身已带 padding（pad=4 + 形态学），
再叠大膨胀属重复扩张；唯一边际收益在**细字**（edge_text，dilate 2~4 残留最低）。
跨用例平均残留比：0 → 1.000、**2 → 0.977（最优）**、4 → 1.044、8 → 1.192。
故默认收敛为 2；羽化 0~15 影响 <1%，维持 5。

⚠️ 阶段 105 修正（docs/105-*）
------------------------------
上一条「细字需要膨胀」是**混杂结论**：阶段 65 的扫描只在 dilate 维上变档，feather 仅在
`d == 默认值` 时才换挡，**(dilate=0, feather=0) 这个角从未被测**，于是把「高斯羽化把
1~2px 笔画的融合权重压到约 0.3」的代价记到了膨胀头上。补齐交叉档：
    edge_text 残留  (d0,f0) 9.09 / 8.48   (d0,f5) 12.72 / 12.64   (d2,f5) 9.95 / 8.44
⇒ f=0 时不膨胀已与旧默认持平。阶段 105 修掉「羽化冲淡掩码内部」后
（`_prepare_mask` 第 4 步 `max(soft, binary)`），(d0,f5) 残留 9.10 / 8.48、
过度重绘像素 1498 / 1515（旧默认 3137 / 2889）、外溢 0.7%（旧默认 29.3%）
⇒ 默认膨胀**归零**，新默认在残留、破坏面积、外溢三项上全面优于旧默认。

本测试锁定：① 默认膨胀必须为 0（不许重新引入外扩）；② 默认羽化不得冲淡掩码内部；
③ 审计红线判定语义（对照组为裸掩码 (0,0)）；④ 关键收益/代价关系（slow）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
from scripts.mask_param_audit import (  # noqa: E402
    CASES_AUDITED,
    KINDS_AUDITED,
    MAX_DEFAULT_OVERPAINT_RATIO,
    MAX_MEAN_RESIDUAL_RATIO,
    analyse,
    evaluate,
)


def _rows(default_dilate=None, default_feather=None, residual_ratio=1.0,
          overpaint_ratio=1.0, residual=10.0, changed=1000):
    """构造审计报告：对照组固定为裸掩码 (0,0)，默认配置由 config 取。"""
    d_def = config.MASK_DILATE_DEFAULT if default_dilate is None else default_dilate
    f_def = config.MASK_FEATHER_DEFAULT if default_feather is None else default_feather
    rows = []
    for kind in KINDS_AUDITED:
        for case in CASES_AUDITED:
            rows.append({"kind": kind, "case": case, "dilate": 0, "feather": 0,
                         "residual": residual, "overpaint": 1.0,
                         "changed_px": changed, "gt_cov": 1.0})
            rows.append({"kind": kind, "case": case, "dilate": d_def, "feather": f_def,
                         "residual": residual * residual_ratio,
                         "overpaint": 1.0,
                         "changed_px": int(changed * overpaint_ratio), "gt_cov": 1.0})
    return {"config": {"seed": 7, "default_dilate": d_def,
                       "default_feather": f_def,
                       "max_overpaint_ratio": MAX_DEFAULT_OVERPAINT_RATIO},
            "rows": rows}


# ---------------------------------------------------------------------------
# 1. 默认值护栏（快）
# ---------------------------------------------------------------------------

def test_default_dilate_calibrated():
    """默认膨胀必须为 0：任何 >0 的外扩都会按 D·周长/面积 直接变成掩码外改动。

    阶段 105 实测（真实照片 10 图 × 4 类）：外溢 dilate 0→0.0%、1→17.8%、2→30.0%，
    且 collateral 1.799→2.376、ΔPSNR −6.69→−7.34 dB、去痕率 25.4%→24.3% 三项同时变差。
    """
    assert config.MASK_DILATE_DEFAULT == 0, (
        f"MASK_DILATE_DEFAULT={config.MASK_DILATE_DEFAULT} 重新引入了掩码外扩；"
        "阶段 105 已证明「细字需要膨胀」是被羽化混杂出来的伪需求，"
        "外溢 ≤5% 与 D≥1 在数学上不可兼得")


def test_default_feather_kept():
    """羽化实测对残留影响 <1%（修正语义后），维持默认 5（若有人改动应显式评审）。"""
    assert config.MASK_FEATHER_DEFAULT == 5


# ---------------------------------------------------------------------------
# 2. 红线判定语义（纯函数，快）
# ---------------------------------------------------------------------------

def test_evaluate_passes_calibrated_report():
    ok, problems = evaluate(_rows())
    assert ok, problems


def test_evaluate_flags_overpaint_ratio():
    ok, problems = evaluate(_rows(overpaint_ratio=MAX_DEFAULT_OVERPAINT_RATIO + 1))
    assert not ok
    assert any("过度重绘红线" in p for p in problems)


def test_evaluate_flags_mean_residual_regression():
    ok, problems = evaluate(_rows(residual_ratio=MAX_MEAN_RESIDUAL_RATIO + 0.3))
    assert not ok
    assert any("残留红线" in p for p in problems)


def test_evaluate_control_group_is_naked_mask():
    """对照组必须是裸掩码 (0,0)：拿「默认 vs dilate=0（默认羽化）」当基准会退化成恒等。

    反例：若把对照组换成「dilate=0 + 默认羽化」，默认膨胀归零后两者就是同一行，
    比值恒为 1.0 ⇒ 红线变成哑巴（阶段 95 教训）。本测试把对照组行整体挪走，
    `evaluate` 必须判定为**报告不完整**，而不是静默通过。
    """
    rows = _rows()["rows"]
    for r in rows:                      # 把 (0,0) 对照行改成 (0, 默认羽化) ⇒ 对照组消失
        if r["dilate"] == 0:
            r["feather"] = config.MASK_FEATHER_DEFAULT
    report = {"config": {}, "rows": rows}
    ok, problems = evaluate(report)
    assert not ok, "缺少裸掩码 (0,0) 对照组时必须判失败，否则红线会静默退化"
    assert any("对照组缺失" in p for p in problems), problems


# ---------------------------------------------------------------------------
# 3. 关键关系（slow：真实 inpaint）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_default_beats_legacy_8_on_grid():
    """网格：默认(0) 相对旧默认(8) 残留不升且过度重绘 ≤ 一半（旧默认已证有害）。"""
    d_def = config.MASK_DILATE_DEFAULT
    for kind in ("textured", "photo"):
        r0 = analyse(kind, "grid", d_def, config.MASK_FEATHER_DEFAULT)
        r8 = analyse(kind, "grid", 8, config.MASK_FEATHER_DEFAULT)
        assert r0["residual"] <= r8["residual"] + 0.05, (
            f"{kind}/grid 默认膨胀残留 {r0['residual']:.2f} 高于旧默认(8) {r8['residual']:.2f}")
        assert r0["changed_px"] <= 0.5 * r8["changed_px"], (
            f"{kind}/grid 默认膨胀改动像素 {r0['changed_px']} 未显著少于旧默认(8) "
            f"{r8['changed_px']}")


@pytest.mark.slow
def test_default_feather_does_not_attenuate_thin_text():
    """细字：默认 (dilate=0, feather=5) 的残留不得劣于裸掩码 (0,0)。

    这是阶段 105 修正后的**主守卫**：旧实现里羽化会把 1~2px 笔画的融合权重压到约 0.3，
    于是 (0,5) 残留 12.72/12.64 远差于 (0,0) 的 9.09/8.48（那正是旧默认把膨胀调到 2 的
    唯一理由）。修复后两者应几乎相等 —— 一旦有人把 `max(soft, binary)` 去掉，本测试立刻红。
    """
    f_def = config.MASK_FEATHER_DEFAULT
    d_def = config.MASK_DILATE_DEFAULT
    for kind in ("textured", "photo"):
        control = analyse(kind, "edge_text", 0, 0)
        default = analyse(kind, "edge_text", d_def, f_def)
        assert default["residual"] <= 1.02 * control["residual"] + 0.05, (
            f"{kind}/edge_text 默认残留 {default['residual']:.2f} 劣于裸掩码 "
            f"{control['residual']:.2f} —— 羽化又在冲淡掩码内部")


@pytest.mark.slow
def test_default_feather_does_not_attenuate_grid():
    """网格（更细的笔画）同样不得被羽化冲淡 —— 反向对照，防「只修了细字」。"""
    f_def = config.MASK_FEATHER_DEFAULT
    d_def = config.MASK_DILATE_DEFAULT
    for kind in ("textured", "photo"):
        control = analyse(kind, "grid", 0, 0)
        default = analyse(kind, "grid", d_def, f_def)
        assert default["residual"] <= 1.02 * control["residual"] + 0.05, (
            f"{kind}/grid 默认残留 {default['residual']:.2f} 劣于裸掩码 "
            f"{control['residual']:.2f}")


@pytest.mark.slow
def test_default_cuts_overpaint_on_thin_targets():
    """细字/网格：默认配置的过度重绘像素必须显著低于旧默认 (2,5)（阶段 105 的直接收益）。"""
    f_def = config.MASK_FEATHER_DEFAULT
    d_def = config.MASK_DILATE_DEFAULT
    for kind in ("textured", "photo"):
        for case in ("edge_text", "grid"):
            new = analyse(kind, case, d_def, f_def)
            old = analyse(kind, case, 2, 5)
            assert new["changed_px"] <= 0.7 * old["changed_px"], (
                f"{kind}/{case} 默认改动像素 {new['changed_px']} 未显著低于旧默认(2,5) "
                f"{old['changed_px']}")
