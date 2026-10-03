# -*- coding: utf-8 -*-
"""Stage 131 回归守卫：services/pdf_service 三处真缺陷（D1/D2/D3）。

D1：`_parse_pages` 在钳制到 [1,total] 之前就遍历 `range(start,end+1)` ⇒ 超大范围
    （"1-99999999"）触发巨量空转（实测 "1-5000000" 在 3 页文档上 186ms）。
    修复后先与 [1,total] 求交再遍历。
D2：`get_pdf_version` 曾返回 `fitz.__doc__`（多行文档串），现返回 `fitz.__version__`。
D3：`get_pdf_info` 的 page_size 实际不含「约 A4」后缀，docstring 已订正。
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import services.pdf_service as ps


# ==============================================================================
# D1 · 页码范围先求交再遍历
# ==============================================================================
def test_large_page_range_is_bounded():
    """超大页码范围必须在极短时间内完成（修复前 ≈ O(end-start) 空转）。"""
    t0 = time.perf_counter()
    out = ps._parse_pages("1-200000000", 3)
    dt = time.perf_counter() - t0
    assert out == [0, 1, 2]
    # 修复前 2e8 次空转约需数秒；修复后与 total 同阶。给 2s 宽松上界。
    assert dt < 2.0, f"超大范围耗时 {dt:.3f}s，疑似退化为逐页空转"


def test_page_range_correctness_unchanged():
    """回归：常规范围的解析结果不变。"""
    assert ps._parse_pages(None, 3) == [0, 1, 2]
    assert ps._parse_pages("all", 3) == [0, 1, 2]
    assert ps._parse_pages("1-2", 3) == [0, 1]
    assert ps._parse_pages("1,3", 3) == [0, 2]
    assert ps._parse_pages("3-1", 3) == [0, 1, 2]
    assert ps._parse_pages("99", 3) == []
    assert ps._parse_pages("2-99", 3) == [1, 2]
    assert ps._parse_pages("0-5", 3) == [0, 1, 2]


# ==============================================================================
# D2 · 版本号是短字符串，不是文档串
# ==============================================================================
def test_get_pdf_version_is_short_version_string():
    """版本号应为单行短串；修复前返回含换行/平台信息的多行文档串。"""
    v = ps.get_pdf_version()
    assert isinstance(v, str) and v
    assert "\n" not in v, f"版本号不应含换行：{v!r}"
    assert len(v) < 40, f"版本号过长（疑似仍返回文档串）：{v!r}"
    if v != "未安装":
        assert any(ch.isdigit() for ch in v), f"版本号应含数字：{v!r}"


# ==============================================================================
# D3 · page_size 实产形态与 docstring 一致
# ==============================================================================
def test_page_size_doc_consistency():
    """docstring 不应再举例 `595 × 842 点（约 A4）`（实现从未产出该后缀）。"""
    doc = ps.get_pdf_info.__doc__ or ""
    assert "点（约 A4）" not in doc, "docstring 仍举实现不产出的「点（约 A4）」示例"


def test_get_pdf_info_page_size_shape(tmp_path):
    """真实 PDF：page_size 形如 'W × H 点'（无额外后缀）。"""
    fitz = pytest.importorskip("fitz")
    src = str(tmp_path / "t.pdf")
    d = fitz.open()
    d.new_page(width=595, height=842)
    d.save(src)
    d.close()
    info = ps.get_pdf_info(src)
    assert info["ok"] is True
    assert info["page_size"] == "595 × 842 点"
