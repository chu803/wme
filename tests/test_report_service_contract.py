"""阶段 166 · `services/report_service` 汇总口径 + 条目组装契约守卫。

背景
----
`services/report_service`（349 代码 / 28 引用测试）把一批处理结果汇总成可交付的 **HTML/PDF 报告**。
阶段 88 已修过「HTML 与 PDF 各自拼汇总串 ⇒ 同一份数据两种结论」的缺陷（改为 `_quality_summary`
统一决定「有哪些段」）。本文件把该口径与 `build_entries` 的过滤/退化逻辑钉成可判定契约：

  · `_summary_stats` / `_quality_summary` 的四态（empty / none / partial / full）+ 计数对账；
  · `build_entries`：只收「success + 结果存在」；原图缺失退化为单图；质量失败不拖垮条目；
  · HTML 与 PDF 的汇总数字**必须一致**（阶段 88 的回归护栏）；
  · 无条目不产出空报告（明确报错）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import report_service as rs  # noqa: E402


def _q(level: str, score: float):
    """轻量质量报告替身（只需 .score / .level）。"""
    return SimpleNamespace(score=score, level=level, detail="d")


def _entry(quality, name="a.png"):
    return {"name": name, "engine": "opencv", "mode": "auto", "params": "",
            "time_str": "—", "img_b64": "", "img_w": 1, "img_h": 1,
            "img_is_compare": True, "quality": quality, "result_path": "/x",
            "recommendation": None}


# --------------------------------------------------------------------------- #
# 1 · 汇总口径四态
# --------------------------------------------------------------------------- #
def test_summary_empty():
    stats = rs._summary_stats([])
    assert stats == {"total": 0, "good": 0, "warn": 0, "review": 0,
                     "assessed": 0, "unassessed": 0, "avg": None}
    assert rs._quality_summary(stats) == {"mode": "empty", "counts": [], "note": "", "avg": None}


def test_summary_none_all_unassessed():
    """一条都没评估 ⇒ mode=none + 显式说明（**不得**渲染成「良好 0 · 告警 0 · 待复核 0」）。"""
    stats = rs._summary_stats([_entry(None), _entry(None)])
    assert (stats["assessed"], stats["unassessed"], stats["avg"]) == (0, 2, None)
    qs = rs._quality_summary(stats)
    assert qs["mode"] == "none" and qs["counts"] == [] and qs["note"]


def test_summary_full_and_avg():
    entries = [_entry(_q("good", 90)), _entry(_q("warn", 70)), _entry(_q("review", 40))]
    stats = rs._summary_stats(entries)
    assert (stats["good"], stats["warn"], stats["review"]) == (1, 1, 1)
    assert (stats["assessed"], stats["unassessed"]) == (3, 0)
    assert stats["avg"] == 66.7
    qs = rs._quality_summary(stats)
    assert qs["mode"] == "full" and qs["avg"] == 66.7
    assert dict(qs["counts"]) == {"良好": 1, "告警": 1, "待复核": 1}


def test_summary_partial_lists_unassessed():
    """部分未评估 ⇒ 必须补「未评估 N」，否则各项之和与总条数对不上。"""
    entries = [_entry(_q("good", 90)), _entry(None), _entry(None)]
    stats = rs._summary_stats(entries)
    assert (stats["assessed"], stats["unassessed"]) == (1, 2)
    qs = rs._quality_summary(stats)
    assert qs["mode"] == "partial"
    d = dict(qs["counts"])
    assert d["未评估"] == 2
    assert sum(d.values()) == stats["total"], "各项计数之和必须等于总条数"


def test_summary_counts_sum_to_total_for_all_modes():
    for entries in ([], [_entry(None)], [_entry(_q("good", 1))],
                    [_entry(_q("warn", 1)), _entry(None)]):
        stats = rs._summary_stats(entries)
        qs = rs._quality_summary(stats)
        if qs["counts"]:
            assert sum(v for _, v in qs["counts"]) == stats["total"]


# --------------------------------------------------------------------------- #
# 2 · build_entries 过滤与退化
# --------------------------------------------------------------------------- #
@pytest.fixture
def imgs(tmp_path):
    o, r = tmp_path / "o.png", tmp_path / "r.png"
    cv2.imwrite(str(o), np.full((40, 60, 3), 200, np.uint8))
    cv2.imwrite(str(r), np.full((40, 60, 3), 60, np.uint8))
    return o, r


def test_build_entries_skips_missing_or_failed(imgs, tmp_path, monkeypatch):
    o, r = imgs
    monkeypatch.setattr(rs, "evaluate_quality", lambda a, b: _q("good", 88))
    srcs = [
        rs.ReportSource(original_path=str(o), result_path=str(r), status="success"),
        rs.ReportSource(original_path=str(o), result_path=str(tmp_path / "gone.png")),  # 不存在
        rs.ReportSource(original_path=str(o), result_path=str(r), status="failed"),      # 失败
    ]
    entries = rs.build_entries(srcs, include_quality=True)
    assert len(entries) == 1, "只应收「success 且结果文件存在」的记录"


def test_build_entries_degrades_to_single_when_original_missing(imgs, monkeypatch):
    o, r = imgs
    monkeypatch.setattr(rs, "evaluate_quality", lambda a, b: _q("good", 88))
    entries = rs.build_entries(
        [rs.ReportSource(original_path=str(o / "nope.png"), result_path=str(r))],
        include_quality=True)
    assert len(entries) == 1
    assert entries[0]["img_is_compare"] is False, "原图缺失应退化为单图"
    assert entries[0]["quality"] is None, "原图缺失时不做质量评估"


def test_build_entries_quality_failure_keeps_entry(imgs, monkeypatch):
    """质量评估抛错 ⇒ 该条 quality=None 但**条目仍保留**（不得整条丢弃）。"""
    o, r = imgs

    def boom(a, b):
        raise RuntimeError("quality boom")
    monkeypatch.setattr(rs, "evaluate_quality", boom)
    entries = rs.build_entries([rs.ReportSource(original_path=str(o), result_path=str(r))],
                               include_quality=True)
    assert len(entries) == 1 and entries[0]["quality"] is None
    assert entries[0]["img_is_compare"] is True


def test_build_entries_include_quality_off(imgs, monkeypatch):
    o, r = imgs
    called = []
    monkeypatch.setattr(rs, "evaluate_quality", lambda a, b: called.append(1) or _q("good", 1))
    rs.build_entries([rs.ReportSource(original_path=str(o), result_path=str(r))],
                     include_quality=False)
    assert called == [], "关闭质量评估时不得调用 evaluate_quality"


def test_compare_png_base64_and_dims(imgs):
    o, r = imgs
    png, w, h = rs._compare_png(str(o), str(r), 1000)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert w > 0 and h > 0
    assert w > 60, "拼接图宽度应大于单图宽度"


# --------------------------------------------------------------------------- #
# 3 · 下采样保持宽高比
# --------------------------------------------------------------------------- #
def test_maybe_downscale_preserves_aspect():
    arr = np.zeros((100, 200, 3), np.uint8)
    out = rs._maybe_downscale(arr, 50)
    assert out.shape[:2] == (25, 50)


def test_maybe_downscale_noop_when_within_limit():
    arr = np.zeros((10, 20, 3), np.uint8)
    assert rs._maybe_downscale(arr, 100).shape == arr.shape
    assert rs._maybe_downscale(arr, 0).shape == arr.shape, "max_side=0 ⇒ 不限（不下采样）"


# --------------------------------------------------------------------------- #
# 4 · HTML / PDF 汇总一致（阶段 88 回归）+ 空报告
# --------------------------------------------------------------------------- #
def test_export_report_no_entries_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        rs.export_report([], fmt="html", out_path=str(tmp_path / "x.html"))


def test_generate_pdf_rejects_nonpositive_records_per_page(tmp_path):
    """阶段 166 修复点：`records_per_page <= 0` 会让「一页几条」的判据恒真 ⇒ 静默退化成
    「一图一页」。数量类参数缺下界应显式拒绝，而不是悄悄换行为。"""
    pytest.importorskip("fitz")
    for bad in (0, -1):
        with pytest.raises(ValueError):
            rs.generate_pdf([], out_path=str(tmp_path / f"x{bad}.pdf"), records_per_page=bad)


def test_html_and_pdf_share_the_same_summary(imgs, tmp_path, monkeypatch):
    o, r = imgs
    monkeypatch.setattr(rs, "evaluate_quality", lambda a, b: _q("warn", 72))
    srcs = [rs.ReportSource(original_path=str(o), result_path=str(r), original_name="a.png")]
    entries = rs.build_entries(srcs, include_quality=True, max_compare_side=200)

    html_path = rs.generate_html(entries, out_path=str(tmp_path / "rep.html"))
    html = Path(html_path).read_text(encoding="utf-8")
    assert "共 <b>1</b> 条" in html
    assert "告警 <b>1</b>" in html, "HTML 汇总应含逐项计数"

    fitz = pytest.importorskip("fitz")
    pdf_path = rs.generate_pdf(entries, out_path=str(tmp_path / "rep.pdf"))
    doc = fitz.open(pdf_path)
    text = "".join(p.get_text() for p in doc)
    doc.close()
    assert "共 1 条" in text, "PDF 汇总应与 HTML 同口径（总条数）"
    assert "告警 1" in text, "PDF 汇总应与 HTML 同口径（逐项计数）"
