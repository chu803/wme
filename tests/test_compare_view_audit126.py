# -*- coding: utf-8 -*-
"""Stage 126 回归守卫：ui/compare_view.render_pair 摘要路径兜底（D1）。

背景：render_pair 的 docstring 承诺「各类异常情况都返回可读提示而非抛错」；
阶段 97 的 I2 修复只把 render_pair_html 包进 try/except，同一 return 路径上的
format_summary（内部调 _cs.summarize_pair，其对齐/统计段无服务级兜底）仍是裸调——
summarize_pair 一旦抛错，异常穿透到 Gradio 画廊回调。本守卫钉死该兜底行为。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import pytest

import ui.compare_view as cv
from utils.i18n import t


def _make_png(path, color=(200, 10, 10)):
    from PIL import Image

    Image.new("RGB", (40, 30), color).save(path)


def test_d1_summarize_raise_is_guarded(tmp_path):
    """D1：summarize_pair 抛错时 render_pair 必须兜底，不得向上泄露异常。"""
    orig = tmp_path / "orig.png"
    res = tmp_path / "res.png"
    _make_png(orig)
    _make_png(res, (10, 200, 10))
    with patch.object(cv._cs, "summarize_pair", side_effect=RuntimeError("boom")):
        html, summary = cv.render_pair((str(orig), str(res), "x.png"), "concat")
    # HTML 已渲染成功，必须保留（只降级摘要，不连坐渲染结果）。
    assert html, "摘要失败不应连坐已渲染的 HTML"
    # 摘要退回「条目名 + 统计不可用」占位。
    assert "x.png" in summary
    assert t("compare.viewer.summary_none") in summary
    # 关键：此处不得抛出 RuntimeError。


def test_d1_summarize_raise_all_modes(tmp_path):
    """D1 兄弟模式：slider / heatmap 下同样兜底（三模式共用同一 return 路径）。"""
    orig = tmp_path / "orig.png"
    res = tmp_path / "res.png"
    _make_png(orig)
    _make_png(res)
    for m in ("concat", "slider", "heatmap"):
        with patch.object(cv._cs, "summarize_pair", side_effect=RuntimeError("boom")):
            html, summary = cv.render_pair((str(orig), str(res), "y.png"), m)
        assert html, f"{m}：HTML 应保留"
        assert t("compare.viewer.summary_none") in summary, f"{m}：摘要应退占位"


def test_normal_path_unaffected(tmp_path):
    """回归：修复不得影响正常路径（三模式 + 摘要含统计行）。"""
    orig = tmp_path / "orig.png"
    res = tmp_path / "res.png"
    _make_png(orig)
    _make_png(res, (10, 200, 10))
    for m in ("concat", "slider", "heatmap"):
        html, summary = cv.render_pair((str(orig), str(res), "z.png"), m)
        assert html, f"{m} 渲染为空"
        assert "z.png" in summary
        # 正常路径不得出现「统计不可用」占位。
        assert t("compare.viewer.summary_none") not in summary


def test_render_engine_raise_still_guarded(tmp_path):
    """回归（I2 旧守卫的邻位复检）：渲染引擎抛错仍返回可读提示。"""
    orig = tmp_path / "orig.png"
    res = tmp_path / "res.png"
    _make_png(orig)
    _make_png(res)
    with patch.object(cv._cs, "render_pair_html", side_effect=RuntimeError("boom")):
        html, summary = cv.render_pair((str(orig), str(res), "w.png"), "concat")
    assert html == ""
    assert summary == t("compare.viewer.render_fail")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
