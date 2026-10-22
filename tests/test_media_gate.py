# -*- coding: utf-8 -*-
"""阶段 315 · 媒体门禁（scripts/media_gate.py）自身的守卫。

门禁最怕两件事：**一开门就红**（没人信）与**永远是绿的哑巴**。
本文件用不跑全链路的廉价手段钉住：
  · 判据本身正确（水印区没变 ⇒ 必须报问题；变了 ⇒ 不报）
  · 退出码契约（缺依赖/资产 ⇒ 2，不是 0）
  · 接入统一入口（`--gates` 的步骤清单里有它，否则等于没接）
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import media_gate as mg  # noqa: E402


def _flat(n: int = 64) -> np.ndarray:
    return np.full((n, n, 3), 120, np.uint8)


def test_diff_stats_flags_watermark_change():
    """判据不为哑巴：水印区被改 ⇒ bbox_diff 必须显著；没被改 ⇒ 必须近 0。"""
    a = _flat()
    bbox = (10, 10, 40, 40)
    same = mg.diff_stats(a, a.copy(), bbox)
    assert same["bbox_diff"] == 0.0
    assert same["whole_changed"] == 0.0

    b = a.copy()
    b[12:48, 12:48] = 250                      # 只改水印区
    got = mg.diff_stats(a, b, bbox)
    assert got["bbox_diff"] > mg.MIN_BBOX_DIFF, f"水印区变了却测不出：{got}"
    assert got["outside_changed"] == 0.0, f"非水印区不该被记为改动：{got}"


def test_diff_stats_exposes_outside_change_as_diagnostic():
    """全图都被改（后处理就是这种形态）⇒ 诊断指标必须看见，不能是 0。

    ⚠ 图必须显著大于 bbox + 2×BBOX_DILATE，否则「非水印区」被外扩框盖满，
    outside 恒为 0（64px 图就踩到过这个，不是判据错）。
    """
    a, b = _flat(256), _flat(256)
    b[:, :, 0] = np.clip(b[:, :, 0].astype(int) + 1, 0, 255).astype(np.uint8)
    s = mg.diff_stats(a, b, (8, 8, 40, 40))
    assert s["outside_changed"] > 0.5, f"非水印区改动未被曝光：{s}"


def test_skip_when_assets_missing(monkeypatch, tmp_path):
    """资产缺失必须 rc=2（跳过），绝不能 rc=0（假绿）。"""
    monkeypatch.setattr(mg, "GT_PATH", str(tmp_path / "nope.json"))
    assert mg.main([]) == 2


def test_skip_when_no_usable_cases(monkeypatch, tmp_path):
    monkeypatch.setattr(mg, "GT_PATH", str(tmp_path / "gt.json"))
    (tmp_path / "gt.json").write_text('{"entries": []}', encoding="utf-8")
    monkeypatch.setattr(mg, "IMAGES_DIR", str(tmp_path))
    assert mg.main([]) == 2


def test_wired_into_run_all_checks_gates():
    """`--gates` 步骤清单必须包含媒体门禁，否则等于没接进 CI。"""
    from scripts import run_all_checks as rac

    steps = rac.build_steps(full=False, gates=True, root=_ROOT)
    names = [s.name for s in steps]
    assert "media_gate" in names, f"媒体门禁未接入门禁步骤：{names}"


def test_media_gate_not_in_default_steps():
    """反向：默认（不带 --gates）不跑媒体门禁（需 GPU/耗时，保持 CI 轻量）。"""
    from scripts import run_all_checks as rac

    names = [s.name for s in rac.build_steps(full=False, gates=False, root=_ROOT)]
    assert "media_gate" not in names, names


def test_min_bbox_diff_is_nonzero():
    """红线阈值不得被改成 0（那样 R2 恒过，守卫变装饰）。"""
    assert mg.MIN_BBOX_DIFF >= 1.0, f"阈值过松：{mg.MIN_BBOX_DIFF}"
