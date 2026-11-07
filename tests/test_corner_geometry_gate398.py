# -*- coding: utf-8 -*-
"""阶段 398：角标几何覆盖率门禁的守卫。

这道门禁把「承诺类的擦净率对角标长什么样敏感」变成**可跟踪、可拦截的数字**：
挑战集 4 种几何（右下小板 / 左下小板 / 半透明板 / 无底板纯文字），
检出率与完全擦净率各有一条下限；挑战集由脚本**确定性生成**（不入库）。

守卫：
  · 红线常量存在且在合理量级；
  · 判据函数红绿两态正确（越线判红、达标判绿）；
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
    assert 0.0 < config.CORNER_GEOMETRY_MIN_ERASED <= 1.0


def test_judge_flags_low_coverage():
    p = _probe()
    bad = {"n_logo": 4, "detected_rate": 0.75, "erased_rate": 0.25}
    problems = p.judge(bad)
    assert problems, "低于红线必须判红"
    assert any("检出率" in x for x in problems)
    assert any("擦净率" in x for x in problems)


def test_judge_passes_current_coverage():
    p = _probe()
    ok = {"n_logo": 4, "detected_rate": 1.0, "erased_rate": 0.75}
    assert p.judge(ok) == []


def test_judge_flags_missing_samples():
    p = _probe()
    assert p.judge({"n_logo": 0, "detected_rate": 0.0, "erased_rate": 0.0})


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
