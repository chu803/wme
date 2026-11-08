# -*- coding: utf-8 -*-
"""阶段 398/399：角标几何覆盖率门禁的守卫。

这道门禁把「承诺类的擦净率对角标长什么样敏感」变成**可跟踪、可拦截的数字**：
挑战集 4 种几何（右下小板 / 左下小板 / 半透明板 / 无底板纯文字）；
检出率是硬红线；"擦没擦净"**不做自动判定**（阶段 399 实测：四轮自动指标都不可靠）
⇒ 改为守住**目视确认过真的擦净了**的参考样本不许退化，其余登记为已知缺口。

守卫：
  · 红线常量存在且在合理量级、参考样本集非空；
  · 判据函数红绿两态正确（检出不足 / 参考样本退化 ⇒ 判红；达标 ⇒ 判绿）；
  · 门禁接进统一入口（--gates），否则等于永不生效；
  · 挑战集可**确定性重建**（缺图时自生成 ⇒ 干净 checkout 上门禁也能跑）。
"""

from __future__ import annotations

import os
import sys

import config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _probe():
    import challenge_probe

    return challenge_probe


def test_redlines_exist():
    assert 0.5 <= config.CORNER_GEOMETRY_MIN_DETECTED <= 1.0
    assert 0.0 < config.CORNER_GEOMETRY_REF_MAX_SHARP <= 1.0
    assert config.CORNER_GEOMETRY_ERASED_REF, (
        "参考样本集不能为空——它由**目视裁定**得出（哪些几何真的能擦净）")


def test_reference_samples_have_fixtures():
    """参考样本必须在挑战集里（否则门禁守的是空气）。"""
    p = _probe()
    manifest = p.ensure_set()
    names = {e["image"] for e in manifest}
    for name in config.CORNER_GEOMETRY_ERASED_REF:
        assert name in names, f"参考样本 {name} 不在挑战集里"


def test_judge_flags_low_detected_rate():
    p = _probe()
    bad = {"n_logo": 4, "detected_rate": 0.75, "rows": []}
    problems = p.judge(bad)
    assert problems, "检出率不足必须判红"
    assert any("检出率" in x for x in problems)


def test_judge_flags_reference_regression():
    """参考样本的**锐边残留**超上限 = 擦净退化 ⇒ 判红（阶段 400：不用 NCC，它不可信）。"""
    p = _probe()
    name = sorted(config.CORNER_GEOMETRY_ERASED_REF)[0]
    bad = {
        "n_logo": 4, "detected_rate": 1.0,
        "rows": [{"image": name, "sharp_visibility": 0.9}],
    }
    problems = p.judge(bad)
    assert problems, "参考样本擦净退化必须判红"
    assert any("退化" in x for x in problems)


def test_judge_passes_current_coverage():
    p = _probe()
    # 阶段 400：参考样本按**锐边残留**判（NCC 不可信，见 docs/400）。
    rows = [{"image": n, "residual": 0.10, "sharp_visibility": 0.2}
            for n in config.CORNER_GEOMETRY_ERASED_REF]
    ok = {"n_logo": 4, "detected_rate": 1.0, "rows": rows}
    assert p.judge(ok) == []


def test_judge_flags_missing_samples():
    p = _probe()
    assert p.judge({"n_logo": 0, "detected_rate": 0.0, "rows": []})


def test_challenge_set_is_rebuildable():
    """挑战集不在库里 ⇒ 必须能确定性重建（否则门禁在干净 checkout 上会空跑）。"""
    p = _probe()
    manifest = p.ensure_set()
    assert len(manifest) >= 12, f"挑战集只有 {len(manifest)} 条"
    assert p.IMAGES.exists() and list(p.IMAGES.glob("*.png"))


def test_gate_is_registered_in_unified_entry():
    from scripts import run_all_checks

    names = [getattr(s, "name", "") for s in run_all_checks.build_steps(gates=True)]
    assert "corner_geometry_gate" in names, "门禁没接进统一入口（--gates），等于永不生效"
