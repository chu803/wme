# -*- coding: utf-8 -*-
"""
阶段 17 批量对比与报告 · 单元测试。

覆盖：
  · build_entries：只保留「成功 + 结果文件存在」；原图缺失退化为单结果图。
  · generate_html：自包含（base64 内嵌）+ 含标题与汇总统计。
  · generate_pdf：PyMuPDF 生成真实 PDF（含 CJK 字体，超大图不溢出）。
  · 质量开关：include_quality 关闭时不评估（quality=None）。
  · export_report：无成功记录时抛 FileNotFoundError；PDF 在 fitz 缺失回退 HTML。
"""
import os
import tempfile

import numpy as np
import cv2
import pytest

from services.report_service import (
    ReportSource,
    build_entries,
    generate_html,
    generate_pdf,
    export_report,
)


def _save(arr: np.ndarray) -> str:
    """把 RGB 数组写成临时 PNG，返回路径。"""
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(f.name, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    f.close()
    return f.name


def _src(original_path, result_path, status="success", **kw):
    return ReportSource(
        original_path=original_path, result_path=result_path,
        original_name=kw.get("name", "图.png"),
        engine=kw.get("engine", "opencv"), mode=kw.get("mode", "auto"),
        params=kw.get("params", "力度=50"), ts=kw.get("ts", 0.0),
        status=status,
    )


def test_build_entries_keeps_success_skips_missing():
    base = np.full((200, 300, 3), 235, np.uint8)
    o = _save(base)
    good = _save(np.full((200, 300, 3), 60, np.uint8))
    missing = _save(base)
    os.remove(missing)  # 结果文件不存在 → 应被过滤
    try:
        srcs = [
            _src(o, good, name="A.png"),
            _src(o, missing, name="B.png"),          # 结果缺失
            _src(o, good, name="C.png", status="failed"),  # 失败
        ]
        entries = build_entries(srcs, include_quality=False)
        assert len(entries) == 1
        assert entries[0]["name"] == "A.png"
        assert entries[0]["img_is_compare"] is True
        assert entries[0]["quality"] is None
    finally:
        for p in (o, good, missing):
            if os.path.exists(p):
                os.remove(p)


def test_build_entries_original_missing_falls_to_single():
    """原图缺失时退化为仅结果图（img_is_compare=False）。"""
    result = _save(np.full((200, 300, 3), 60, np.uint8))
    try:
        entries = build_entries(
            [_src(None, result, name="X.png")], include_quality=False
        )
        assert len(entries) == 1
        assert entries[0]["img_is_compare"] is False
    finally:
        os.remove(result)


def test_generate_html_self_contained():
    o = _save(np.full((200, 300, 3), 235, np.uint8))
    r = _save(np.full((200, 300, 3), 60, np.uint8))
    out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    out.close()
    try:
        path = generate_html(
            build_entries([_src(o, r)], include_quality=False),
            title="测试报告", out_path=out.name,
        )
        assert path.endswith(".html")
        text = open(path, "r", encoding="utf-8").read()
        assert "data:image/png;base64," in text
        assert "测试报告" in text
        assert "共 <b>1</b>" in text  # 汇总统计
    finally:
        os.remove(o)
        os.remove(r)
        if os.path.exists(out.name):
            os.remove(out.name)


def test_generate_pdf_real_file():
    # 阶段 206 修：本用例生成**真实 PDF**（`generate_pdf` 惰性 import fitz），
    # 而 fitz（PyMuPDF）是可选依赖（requirements-min 不含）⇒ 缺它就 skip，
    # 否则 CI 的 min 环境上直接 `ModuleNotFoundError` 失败。
    pytest.importorskip("fitz")
    o = _save(np.full((400, 600, 3), 235, np.uint8))
    r = _save(np.full((400, 600, 3), 60, np.uint8))
    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    out.close()
    try:
        path = generate_pdf(
            build_entries([_src(o, r)], include_quality=False),
            title="PDF测试", out_path=out.name,
        )
        assert path.endswith(".pdf")
        assert os.path.getsize(path) > 0
        # 头部含 %PDF 魔数即可确认是真实 PDF。
        with open(path, "rb") as f:
            assert f.read(5) == b"%PDF-"
    finally:
        os.remove(o)
        os.remove(r)
        if os.path.exists(out.name):
            os.remove(out.name)


def test_include_quality_toggle():
    o = _save(np.full((200, 300, 3), 235, np.uint8))
    r = _save(np.full((200, 300, 3), 60, np.uint8))
    try:
        off = build_entries([_src(o, r)], include_quality=False)
        on = build_entries([_src(o, r)], include_quality=True)
        assert off[0]["quality"] is None
        assert on[0]["quality"] is not None
        assert isinstance(on[0]["quality"].score, (int, float))
    finally:
        os.remove(o)
        os.remove(r)


def test_export_report_no_records_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        export_report([], fmt="html")


def test_export_report_pdf_fallback_when_no_fitz(monkeypatch):
    """模拟 PyMuPDF 不可用时，PDF 请求应回退为 HTML 输出。"""
    import builtins
    o = _save(np.full((200, 300, 3), 235, np.uint8))
    r = _save(np.full((200, 300, 3), 60, np.uint8))
    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    out.close()
    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "fitz":
            raise ImportError("no fitz")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    try:
        # 回退后文件名被改写为 .html。
        path = export_report([_src(o, r)], fmt="pdf", out_path=out.name)
        assert path.lower().endswith(".html")
        assert os.path.exists(path)
    finally:
        os.remove(o)
        os.remove(r)
        for ext in (".pdf", ".html"):
            cand = out.name if out.name.endswith(ext) else out.name[:-4] + ext
            if os.path.exists(cand):
                os.remove(cand)
