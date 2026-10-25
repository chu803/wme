# -*- coding: utf-8 -*-
"""阶段 322b · 角标信号门禁（scripts/corner_signal_gate.py）自身的守卫。

同族立场（315/322a）：门禁最怕「一开门就红」与「永远是绿的哑巴」。
本文件不跑 108 张图，只用廉价手段钉住判据、匹配口径与退出码契约——
其中**匹配口径**是这个门禁最容易悄悄失效的地方（GT 的 id 有截断、有短名，
错一个规则就会把 49 张角标真值数成 75 张，命中率假跌、门禁假红）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import corner_signal_gate as cg  # noqa: E402


def _lists(n_hit: int, n_miss: int = 0, n_fp: int = 0):
    hits = [f"h{i}.jpg" for i in range(n_hit)]
    misses = [f"m{i}.jpg" for i in range(n_miss)]
    fps = [f"f{i}.jpg" for i in range(n_fp)]
    return hits, misses, fps


# ---------------------------------------------------------------------------
# ① 判据
# ---------------------------------------------------------------------------

def test_flat_run_is_clean():
    hits, misses, fps = _lists(cg.CORNER_HIT_FLOOR, n_miss=13)
    problems, m = cg.judge(hits, misses, fps, 49, set(cg.KNOWN_MISSING_GT_IDS), 0)
    assert problems == [], problems
    assert m["hit_rate"] == round(cg.CORNER_HIT_FLOOR / 49, 4)


def test_hit_floor_breach_fires_r1():
    hits, misses, fps = _lists(cg.CORNER_HIT_FLOOR - 1, n_miss=20)
    problems, _ = cg.judge(hits, misses, fps, 49, set(cg.KNOWN_MISSING_GT_IDS), 0)
    assert any(p.startswith("R1") for p in problems), problems


def test_any_false_positive_fires_r2():
    """误命中必须是 0：这是 450 选 0.30 而非 0.25 的理由，没有余量可留。"""
    hits, misses, fps = _lists(38, n_miss=11, n_fp=1)
    problems, _ = cg.judge(hits, misses, fps, 49, set(cg.KNOWN_MISSING_GT_IDS), 0)
    assert any(p.startswith("R2") for p in problems), problems


def test_zero_corner_truth_is_never_green():
    """GT 解析坏掉（一张角标都没有）⇒ 必须报，不能因为 hits>=0 就绿。"""
    problems, _ = cg.judge([], [], [], 0, set(cg.KNOWN_MISSING_GT_IDS), 0)
    assert any(p.startswith("R0") for p in problems), problems


def test_classification_must_close():
    """命中+漏 ≠ 角标总数 ⇒ 分类逻辑与扫描脱节（比如某类图被漏遍历），必须报。"""
    hits, misses, fps = _lists(38, n_miss=5)
    problems, _ = cg.judge(hits, misses, fps, 49, set(cg.KNOWN_MISSING_GT_IDS), 0)
    assert any(p.startswith("R0 角标图分类不闭合") for p in problems), problems


def test_unreadable_images_are_a_red_line():
    """读不回 = 覆盖不全。首轮实测栽在中文文件名上（30 张），绝不能当成通过。"""
    hits, misses, fps = _lists(38, n_miss=11)
    problems, _ = cg.judge(hits, misses, fps, 49, set(cg.KNOWN_MISSING_GT_IDS), 30)
    assert any("读不回" in p for p in problems), problems


def test_new_missing_gt_id_fires_but_known_list_is_exemption():
    hits, misses, fps = _lists(38, n_miss=11)
    known = set(cg.KNOWN_MISSING_GT_IDS)
    problems, _ = cg.judge(hits, misses, fps, 49, known, 0)
    assert problems == [], problems
    problems2, _ = cg.judge(hits, misses, fps, 49, known | {"ai_logo_99_new"}, 0)
    assert any("新增丢失" in p for p in problems2), problems2


def test_recovered_image_forces_ledger_update():
    """已知缺口的图又回来了 ⇒ 必须显式更新清单，不许留着当永久的免死金牌。"""
    hits, misses, fps = _lists(38, n_miss=11)
    problems, _ = cg.judge(hits, misses, fps, 49, set(cg.KNOWN_MISSING_GT_IDS) - {"q3_2"}, 0)
    assert any("KNOWN_MISSING_GT_IDS" in p for p in problems), problems


def test_floors_are_not_relaxed_to_zero():
    assert cg.CORNER_HIT_FLOOR >= 30, cg.CORNER_HIT_FLOOR
    assert cg.NATURAL_FP_MAX == 0, "误命中红线改成 >0 就等于放弃精确率承诺"
    assert len(cg.KNOWN_MISSING_GT_IDS) == 5, "已知缺口清单变动要连同 docs/322b 一起改"


# ---------------------------------------------------------------------------
# ② GT ↔ 文件名匹配口径（本门禁最脆的地方）
# ---------------------------------------------------------------------------

BY_ID = {
    "a1": {"image_id": "a1", "watermarks": [{"bbox": [1702, 1893, 340, 128]}]},
    "1": {"image_id": "1", "watermarks": [{"bbox": [1702, 1893, 340, 128]}]},
    "q1": {"image_id": "q1", "watermarks": [{"bbox": [1702, 1893, 340, 128]}]},
    "q1_2": {"image_id": "q1_2", "watermarks": [{"bbox": [1702, 1893, 340, 128]}]},
    "ai_logo_02_snow_mountain": {"image_id": "ai_logo_02_snow_mountain",
                                 "watermarks": [{"bbox": [1702, 1893, 340, 128]}]},
    "ai_logo_03_white_coffee_": {"image_id": "ai_logo_03_white_coffee_",
                                 "watermarks": [{"bbox": [1702, 1893, 340, 128]}]},
}


@pytest.mark.parametrize("stem,expect", [
    ("a1", "a1"),                                        # 精确同名
    ("ai_logo_02_snow_mountain_lake", "ai_logo_02_snow_mountain"),   # id + "_" 前缀
    ("ai_logo_03_white_coffee_cup", "ai_logo_03_white_coffee_"),     # 24 字符截断遗留
    ("c9_natural", None),
])
def test_entry_for_expected_forms(stem, expect):
    got = cg._entry_for(stem, BY_ID)
    if expect is None:
        assert got is None, (stem, got)
    else:
        assert got["image_id"] == expect, (stem, got)


def test_short_id_must_not_claim_long_stem():
    """`1` 认领 `10_东京街头.jpg` 是实测踩过的坑：角标真值从 49 张涨到 75 张、命中率假跌到 0.51。"""
    assert cg._entry_for("10_东京街头", BY_ID) is None


def test_orphan_id_is_not_claimed_by_its_sibling():
    """`q1_2`（图从未入库）不能被 `q1` 的文件认领——否则「数据集缺图」被洗成「匹配成功」。"""
    assert cg._entry_for("q1", BY_ID)["image_id"] == "q1"
    assert cg._entry_for("q1;", BY_ID) is None


def test_bottom_right_is_proportional():
    ok = cg._is_bottom_right((1702, 1893, 340, 128), 2048, 2048)
    left = cg._is_bottom_right((50, 1893, 340, 128), 2048, 2048)      # 左下
    top = cg._is_bottom_right((1702, 50, 340, 128), 2048, 2048)       # 右上偏上
    assert ok and not left and not top
    # 小图同样能判（写死像素的旧口径在这里会一律判否）
    assert cg._is_bottom_right((85, 95, 17, 6), 102, 102)
    assert not cg._is_bottom_right((), 102, 102)
    assert not cg._is_bottom_right((10, 10, 0, 0), 102, 102)


# ---------------------------------------------------------------------------
# ③ IO 与退出码契约
# ---------------------------------------------------------------------------

def test_read_image_handles_chinese_filenames(tmp_path):
    import cv2

    img = np.full((40, 30, 3), 120, np.uint8)
    p = str(tmp_path / "28_复古摩托车.jpg")
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    buf.tofile(p)
    got = cg._read_image(p)
    assert got is not None, "中文路径必须走 fromfile+imdecode（cv2.imread 会返回 None）"
    assert got.shape[:2] == (40, 30)


def test_skip_when_dataset_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "IMAGES_DIR", str(tmp_path / "nope"))
    assert cg.main([]) == 2


def test_skip_when_gt_missing(monkeypatch, tmp_path):
    (tmp_path / "img.jpg").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cg, "IMAGES_DIR", str(tmp_path))
    monkeypatch.setattr(cg, "GT_PATH", str(tmp_path / "no_gt.json"))
    assert cg.main([]) == 2


def test_wired_into_run_all_checks_gates():
    from scripts import run_all_checks as rac

    names = [s.name for s in rac.build_steps(full=False, gates=True, root=_ROOT)]
    assert "corner_signal_gate" in names, names


def test_not_in_default_steps():
    from scripts import run_all_checks as rac

    names = [s.name for s in rac.build_steps(full=False, gates=False, root=_ROOT)]
    assert "corner_signal_gate" not in names, names
