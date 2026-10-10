# -*- coding: utf-8 -*-
"""
阶段 279 · `services/report_service` 深度续审（5j 复审链延续，接 276/277/278）。

首轮审计（阶段 17 基础单测 + 阶段 166 契约口径 + 阶段 169 i18n）已覆盖：
  · build_entries 过滤「success+存在」、原图缺失退化为单图、质量失败不丢条目；
  · _summary_stats / _quality_summary 四态（empty/none/partial/full）+ 计数对账；
  · HTML/PDF 汇总口径一致（阶段 88 缺陷回归）；
  · records_per_page<1 显式 ValueError；
  · include_quality 关闭不调用 evaluate_quality；
  · PDF 在 fitz 缺失时回退 HTML 且 .pdf 改写 .html（fallback 分支）。

本轮按「契约输入类别枚举」钉死**首轮遗漏的成功/边界分支不变量**：
  · build_entries 各字段在 None/空 输入下的默认值（name/engine/mode/params/ts）；
  · status 过滤边界：只有精确 "success" 入列，其余态（pending/cancelled/running/failed）一律过滤；
  · result_path=None 即使 status=success 也过滤；
  · original_path 空串 → 退化为单图（与 None 同路径）；
  · ts=0 → "—"；ts>0 → 19 位格式串；
  · 原图存在 + include_quality=True → quality 被赋值（成功路径分支，首轮只在失败/关闭侧验过）；
  · generate_html 空条目不崩且含空占位；质量 None/有值 卡片两分支；recommendation 渲染；
  · export_report fmt 归一（大写/None）+ 显式 html 下 out_path 末位 .pdf 改写 .html（非 fallback 分支）。

零真缺陷（与 276/277/278 同构）：首轮遗漏的是「未钉边界」而非代码缺陷。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import report_service as rs
from utils.i18n import t as _t


def _q(level: str, score: float):
    """轻量质量报告替身（只需 .score / .level）。"""
    return SimpleNamespace(score=score, level=level, detail="d")


@pytest.fixture
def imgs(tmp_path):
    o, r = tmp_path / "o.png", tmp_path / "r.png"
    cv2.imwrite(str(o), np.full((40, 60, 3), 200, np.uint8))
    cv2.imwrite(str(r), np.full((40, 60, 3), 60, np.uint8))
    return o, r


# --------------------------------------------------------------------------- #
# 1 · build_entries 字段默认值（成功路径）
# --------------------------------------------------------------------------- #
def test_field_name_none_defaults_to_image(imgs):
    o, r = imgs
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r), original_name=None)],
        include_quality=False)[0]
    assert e["name"] == "image"


def test_field_name_empty_defaults_to_image(imgs):
    o, r = imgs
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r), original_name="")],
        include_quality=False)[0]
    assert e["name"] == "image"


def test_field_engine_mode_none_default_to_auto(imgs):
    o, r = imgs
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r), engine=None, mode=None)],
        include_quality=False)[0]
    assert e["engine"] == "auto"
    assert e["mode"] == "auto"


def test_field_params_none_defaults_to_empty(imgs):
    o, r = imgs
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r), params=None)],
        include_quality=False)[0]
    assert e["params"] == ""


def test_ts_zero_renders_dash(imgs):
    o, r = imgs
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r), ts=0.0)],
        include_quality=False)[0]
    assert e["time_str"] == "—"


def test_ts_positive_renders_formatted(imgs):
    o, r = imgs
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r), ts=1700000000.0)],
        include_quality=False)[0]
    # "YYYY-MM-DD HH:MM:SS" 恰为 19 字符，且第 5 位是 "-"。
    assert len(e["time_str"]) == 19
    assert e["time_str"][4] == "-"


# --------------------------------------------------------------------------- #
# 2 · status 过滤边界
# --------------------------------------------------------------------------- #
def test_only_exact_success_status_kept(imgs):
    o, r = imgs
    srcs = [rs.ReportSource(original_path=str(o), result_path=str(r), status=s)
            for s in ("success", "pending", "cancelled", "running", "failed")]
    entries = rs.build_entries(srcs, include_quality=False)
    assert len(entries) == 1, "只有精确 'success' 入列，其余态一律过滤"
    assert entries[0]["name"]


def test_result_path_none_skipped_even_if_success(imgs):
    o, _ = imgs
    srcs = [rs.ReportSource(original_path=str(o), result_path=None, status="success")]
    assert rs.build_entries(srcs, include_quality=False) == [], "result_path=None 即使 success 也过滤"


def test_original_path_empty_string_degrades_to_single(imgs):
    o, r = imgs
    e = rs.build_entries(
        [rs.ReportSource(original_path="", result_path=str(r))],
        include_quality=False)[0]
    assert e["img_is_compare"] is False, "original_path 空串与 None 同走单图退化"


# --------------------------------------------------------------------------- #
# 3 · 成功路径的质量评估分支
# --------------------------------------------------------------------------- #
def test_build_entries_quality_evaluated_when_original_present(imgs, monkeypatch):
    o, r = imgs
    fake = _q("good", 91)
    monkeypatch.setattr(rs, "evaluate_quality", lambda a, b: fake)
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r))],
        include_quality=True)[0]
    assert e["quality"] is fake, "原图存在 + include_quality=True ⇒ quality 被赋值"
    assert e["img_is_compare"] is True


def test_build_entries_passes_recommendation_through(imgs):
    o, r = imgs
    rec = {"preset": "auto", "confidence": 0.9}
    e = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r), recommendation=rec)],
        include_quality=False)[0]
    assert e["recommendation"] is rec


# --------------------------------------------------------------------------- #
# 4 · generate_html 边界分支
# --------------------------------------------------------------------------- #
def test_generate_html_empty_entries_no_crash(tmp_path):
    out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out.close()
    try:
        p = rs.generate_html([], out_path=out.name)
        text = Path(p).read_text(encoding="utf-8")
        assert "data:image/png;base64," not in text, "空条目不应内嵌任何图"
        assert _t("report.empty_records") in text, "空条目须渲染空占位提示"
    finally:
        if Path(out.name).exists():
            Path(out.name).unlink()


def test_generate_html_card_quality_none_branch(imgs, tmp_path):
    o, r = imgs
    entries = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r))],
        include_quality=False)
    entries[0]["quality"] = None
    out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out.close()
    try:
        p = rs.generate_html(entries, out_path=out.name)
        text = Path(p).read_text(encoding="utf-8")
        assert _t("report.unassessed_quality") in text, "quality=None 卡片须渲染「未评估质量」"
    finally:
        if Path(out.name).exists():
            Path(out.name).unlink()


def test_generate_html_card_quality_score_branch(imgs, tmp_path):
    o, r = imgs
    entries = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r))],
        include_quality=False)
    entries[0]["quality"] = _q("good", 88)
    out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out.close()
    try:
        p = rs.generate_html(entries, out_path=out.name)
        text = Path(p).read_text(encoding="utf-8")
        assert "88" in text, "quality 有值卡片须渲染分数"
    finally:
        if Path(out.name).exists():
            Path(out.name).unlink()


def test_generate_html_renders_recommendation(imgs, tmp_path, monkeypatch):
    o, r = imgs
    monkeypatch.setattr(rs, "reason_line", lambda rec: "RECLINE_MARKER")
    monkeypatch.setattr(rs, "bars_html", lambda rec: "RECBARS_MARKER")
    entries = rs.build_entries(
        [rs.ReportSource(original_path=str(o), result_path=str(r),
                         recommendation={"preset": "auto", "confidence": 0.9})],
        include_quality=False)
    out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out.close()
    try:
        p = rs.generate_html(entries, out_path=out.name)
        text = Path(p).read_text(encoding="utf-8")
        assert "RECLINE_MARKER" in text, "recommendation 须渲染理由行"
        assert "RECBARS_MARKER" in text, "recommendation 须渲染分数条"
    finally:
        if Path(out.name).exists():
            Path(out.name).unlink()


# --------------------------------------------------------------------------- #
# 5 · export_report fmt 归一 + .pdf→.html 改写（非 fallback 分支）
# --------------------------------------------------------------------------- #
def test_export_report_default_fmt_html(imgs, tmp_path):
    o, r = imgs
    p = rs.export_report(
        [rs.ReportSource(original_path=str(o), result_path=str(r))],
        fmt=None, out_path=str(tmp_path / "x.html"))
    assert p.lower().endswith(".html")
    assert Path(p).exists()


def test_export_report_html_out_path_pdf_rewritten(imgs, tmp_path):
    o, r = imgs
    # 显式 fmt=html，但 out_path 末位是 .pdf ⇒ 应改写扩展名避免「HTML 内容配 PDF 名」
    # （这是 line 564 的非 fallback 分支，首轮只在 ImportError 回退后才间接验到）。
    p = rs.export_report(
        [rs.ReportSource(original_path=str(o), result_path=str(r))],
        fmt="html", out_path=str(tmp_path / "report.pdf"))
    assert p.lower().endswith(".html")
    assert Path(p).exists()
    assert not Path(str(tmp_path / "report.pdf")).exists(), "原 .pdf 路径不应被写出"


def test_export_report_uppercase_pdf_fmt_normalized(imgs, tmp_path):
    o, r = imgs
    # fmt="PDF" 大写应归一为 pdf；环境有 fitz 走真 PDF，无则回退 html（均返回路径且存在）。
    p = rs.export_report(
        [rs.ReportSource(original_path=str(o), result_path=str(r))],
        fmt="PDF", out_path=str(tmp_path / "u.pdf"))
    assert p.lower().endswith((".pdf", ".html"))
    assert Path(p).exists()
