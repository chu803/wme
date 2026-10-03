# -*- coding: utf-8 -*-
"""
阶段 28：推荐理由可视化 —— 共享渲染器 + 报告集成测试。

覆盖：
  · utils.recommend_view 三种渲染器（reason_line / bars_html / markdown）
    的标签翻译、置信度、触发特征、空值安全。
  · services.report_service 把 recommendation 渲染进 HTML/PDF 报告。
"""

import os as _os
import tempfile

import cv2
import numpy as np
import pytest

from utils.i18n import set_language, t
from utils.recommend_view import (
    get_recommendation_dict,
    reason_line,
    bars_html,
    markdown,
    preset_label,
    feature_label,
)
from core.preset_recommender import Recommendation
from services.report_service import ReportSource, build_entries, generate_html

# 测试统一用简体中文断言。
set_language("zh-CN")

SAMPLE = {
    "preset": "ai_tiled",
    "scores": {
        "gradient": 0.0, "grid": 0.0, "tiled": 0.71,
        "translucent": 0.4, "solid_logo": 0.0, "text": 0.05,
        "signature": 0.0, "regularity": 0.13,
    },
    "triggered": ["tiled"],
    "confidence": 0.71,
}


def test_reason_line_basic():
    rl = reason_line(SAMPLE)
    assert "ai_tiled" not in rl, "reason_line 不应暴露裸预设名"
    # 预设标签与触发特征应为翻译后的可读文案（不依赖具体语言）。
    assert preset_label("ai_tiled") in rl, "reason_line 应显示可读预设标签"
    assert "71%" in rl, "reason_line 应含置信度百分比"
    assert feature_label("tiled") in rl, "reason_line 应含触发特征文案"


def test_reason_line_empty():
    assert reason_line(None) == ""
    assert reason_line({}) == ""
    assert reason_line({"preset": ""}) == ""


def test_bars_html():
    html = bars_html(SAMPLE)
    assert "rf" in html, "bars_html 应含分数条样式"
    assert feature_label("tiled") in html, "bars_html 应含特征标签"
    assert "0.71" in html, "bars_html 应含分值"
    # 空值返回空串。
    assert bars_html(None) == ""
    assert bars_html({"preset": "x", "scores": {}}) == ""


def test_markdown():
    md = markdown(SAMPLE)
    assert "ai_tiled" not in md, "markdown 应显示可读标签"
    assert "█" in md, "markdown 应含字符分数条"
    assert "0.71" in md, "markdown 应含分值"
    # 无推荐时回退失败文案（阶段 98：exc 改走 i18n，断言改为取当前语言译文，
    # 不再钉死中文字面量——否则切换语言会让本用例失效）。
    assert t("detect.recommend.msg.no_result") in markdown(None)


def test_get_recommendation_dict():
    assert get_recommendation_dict(None) is None
    # Recommendation 对象 → to_dict。
    rec = Recommendation(preset="ai_grid", scores={"grid": 0.9},
                         triggered=["grid"], confidence=0.9)
    d = get_recommendation_dict(rec)
    assert d["preset"] == "ai_grid"
    # dict 原样返回。
    assert get_recommendation_dict(SAMPLE) is SAMPLE
    # 空预设判无效。
    assert get_recommendation_dict({"preset": ""}) is None


def test_label_fallback():
    # 未知预设/特征：不暴露裸键（保持原样），但不崩。
    assert preset_label("__nope__") == "__nope__"
    assert feature_label("__nope__") == "__nope__"
    # 已知：翻译命中（返回非裸键的可读文案）。
    assert preset_label("ai_tiled") != "ai_tiled"
    assert feature_label("tiled") != "tiled"


def _make_png(path, val=235):
    cv2.imwrite(path, np.full((200, 300, 3), val, dtype=np.uint8))


def test_report_html_includes_recommendation():
    """报告 HTML 应把推荐理由（含分数条）渲染进卡片。"""
    tmp_o = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_r = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_h = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp_o.close()
    tmp_r.close()
    tmp_h.close()
    try:
        _make_png(tmp_o.name, 235)
        _make_png(tmp_r.name, 60)
        src = ReportSource(
            original_path=tmp_o.name, result_path=tmp_r.name,
            original_name="测试图.png", engine="opencv", mode="auto",
            params="力度=50", ts=0.0, recommendation=SAMPLE,
        )
        entries = build_entries([src])
        assert len(entries) == 1
        html_path = generate_html(entries, out_path=tmp_h.name)
        with open(html_path, "r", encoding="utf-8") as f:
            text = f.read()
        # 报告应渲染可读的预设标签与触发特征（不暴露裸键），且含分数条可视化。
        assert preset_label("ai_tiled") in text, "报告应显示推荐预设标签"
        assert feature_label("tiled") in text, "报告应显示触发特征"
        # rbar 是 bars_html 专属标记（base64 图数据里不会偶然出现）。
        assert "rbar" in text, "报告应含分数条可视化"
    finally:
        for p in (tmp_o.name, tmp_r.name, tmp_h.name):
            try:
                _os.remove(p)
            except OSError:
                pass


def test_report_html_without_recommendation():
    """无推荐时不应报错，也不含推荐区块。"""
    tmp_o = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_r = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_h = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tmp_o.close()
    tmp_r.close()
    tmp_h.close()
    try:
        _make_png(tmp_o.name, 235)
        _make_png(tmp_r.name, 60)
        src = ReportSource(
            original_path=tmp_o.name, result_path=tmp_r.name,
            original_name="测试图.png", engine="opencv", mode="auto",
            params="力度=50", ts=0.0, recommendation=None,
        )
        entries = build_entries([src])
        html_path = generate_html(entries, out_path=tmp_h.name)
        with open(html_path, "r", encoding="utf-8") as f:
            text = f.read()
        assert "rbar" not in text, "无推荐时不应出现分数条可视化"
    finally:
        for p in (tmp_o.name, tmp_r.name, tmp_h.name):
            try:
                _os.remove(p)
            except OSError:
                pass
