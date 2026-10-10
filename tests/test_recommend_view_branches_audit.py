# -*- coding: utf-8 -*-
"""
阶段 280 · `utils/recommend_view` 深度续审（5j 复审链延续，接 276/277/278/279）。

首轮审计（阶段 28 基础单测 + 阶段 98 i18n 双语化 + 报告集成）已覆盖：
  · reason_line / bars_html / markdown 主路径（标签翻译、置信度、触发特征、空值安全）；
  · get_recommendation_dict 的 None / Recommendation 对象 / dict 透传 / 空预设判无效；
  · 未知预设·特征标签回退（不暴露裸键）；报告 HTML 含/不含推荐区块。

本轮按「契约输入类别枚举」钉死**首轮遗漏的成功/边界分支不变量**：
  · `_char_bar` 钳制两端：>1 → 满条(12█)；<0 → 空条(12░)；中段长度恒为 _BAR_LEN；
  · `bars_html` 宽度钳制（阶段 98 修复点）：分值 >1 必须 `width:100%` 封顶，绝不 `width:150%` 溢出；
  · `bars_html` 只渲染 `_FEATURE_KEYS` 内、且分值 >0 的特征，顺序稳定；未知键被忽略；
  · `reason_line` / `markdown` 在 `triggered=[]` 与 `scores` 全 ≤0 时的空态文案分支；
  · `get_recommendation_dict` 对「有 to_dict 但抛错」的对象返回 None（防御分支）；
  · `preset_label("")` → ""（空名守卫）；
  · 三渲染器对「无效输入」的返回差异：reason_line/bars_html → ""，markdown → 失败文案（阶段 25 原义）。

零真缺陷（与 276/277/278/279 同构）：首轮遗漏的是「未钉边界」而非代码缺陷。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.i18n import t as _t
from utils.recommend_view import (
    get_recommendation_dict,
    reason_line,
    bars_html,
    markdown,
    _char_bar,
    _BAR_LEN,
    _FEATURE_KEYS,
    preset_label,
)
from core.preset_recommender import Recommendation


@pytest.fixture(autouse=True)
def _lang_zh():
    """阶段 249：语言设置在夹具内设置，避免收集期污染（与既有套件一致）。"""
    from utils.i18n import set_language
    set_language("zh-CN")


# --------------------------------------------------------------------------- #
# 1 · _char_bar 钳制两端
# --------------------------------------------------------------------------- #
def test_char_bar_clamps_upper():
    assert _char_bar(1.5) == "█" * _BAR_LEN, "分值 >1 必须钳到满条"


def test_char_bar_clamps_lower():
    assert _char_bar(-0.3) == "░" * _BAR_LEN, "分值 <0 必须钳到空条"


def test_char_bar_length_constant():
    for v in (0.0, 0.25, 0.5, 0.99, 1.0):
        assert len(_char_bar(v)) == _BAR_LEN, "任意输入长度恒为 _BAR_LEN"


# --------------------------------------------------------------------------- #
# 2 · bars_html 宽度钳制（阶段 98 修复点：防溢出）
# --------------------------------------------------------------------------- #
def test_bars_html_width_clamped_to_100pct():
    rec = {"preset": "ai_tiled", "scores": {"tiled": 1.5}, "triggered": ["tiled"], "confidence": 0.9}
    html = bars_html(rec)
    assert "width:100%" in html, "分值 >1 必须封顶 width:100%"
    assert "width:150%" not in html, "绝不允许 width:150% 溢出容器（阶段 98 修复点）"


def test_bars_html_ignores_unknown_score_keys():
    rec = {"preset": "ai_tiled", "scores": {"tiled": 0.71, "mystery_feat": 0.99},
           "triggered": ["tiled"], "confidence": 0.9}
    html = bars_html(rec)
    assert "平铺" in html and "0.71" in html
    # 未知键不得被渲染（feature_label 未知键回退为原键，若被渲染会出现该字符串）。
    assert "mystery_feat" not in html, "只渲染 _FEATURE_KEYS 内已知特征"


def test_bars_html_renders_only_positive_in_order():
    rec = {"preset": "ai_tiled",
           "scores": {"tiled": 0.71, "text": 0.05, "translucent": 0.0, "grid": -0.2},
           "triggered": ["tiled"], "confidence": 0.9}
    html = bars_html(rec)
    # 正分值特征渲染、零/负分值跳过。
    assert "0.71" in html and "0.05" in html
    assert "translucent" not in html and "grid" not in html
    assert html.find("平铺") < html.find("文字") or html.find("平铺") < html.find("文本"), \
        "渲染顺序须跟随 _FEATURE_KEYS"


def test_bars_html_all_zero_scores_returns_empty():
    rec = {"preset": "ai_tiled",
           "scores": {k: 0.0 for k in _FEATURE_KEYS},
           "triggered": [], "confidence": 0.0}
    assert bars_html(rec) == "", "全部分值 ≤0 时须返回空串"


# --------------------------------------------------------------------------- #
# 3 · reason_line / markdown 空态文案分支
# --------------------------------------------------------------------------- #
def test_reason_line_empty_triggered_uses_none_text():
    rec = {"preset": "ai_tiled", "scores": {}, "triggered": [], "confidence": 0.71}
    rl = reason_line(rec)
    assert _t("detect.recommend.feature.none") in rl, "triggered 为空须渲染「无」文案而非崩溃"
    assert "71%" in rl


def test_markdown_empty_triggered_uses_none_text():
    rec = {"preset": "ai_tiled", "scores": {"tiled": 0.71}, "triggered": [], "confidence": 0.71}
    md = markdown(rec)
    assert _t("detect.recommend.feature.none") in md, "markdown 空 triggered 须渲染 none 文案"


def test_markdown_all_zero_scores_uses_scores_none():
    rec = {"preset": "ai_tiled",
           "scores": {k: 0.0 for k in _FEATURE_KEYS},
           "triggered": ["tiled"], "confidence": 0.5}
    md = markdown(rec)
    assert _t("detect.recommend.scores_none") in md, "分值全为 0 须渲染 scores_none 行"


def test_markdown_positive_score_clamps_char_bar():
    rec = {"preset": "ai_tiled", "scores": {"tiled": 1.5}, "triggered": ["tiled"], "confidence": 0.9}
    md = markdown(rec)
    assert "█" * _BAR_LEN in md, "markdown 分值 >1 经 _char_bar 钳到满条"


# --------------------------------------------------------------------------- #
# 4 · get_recommendation_dict 防御分支
# --------------------------------------------------------------------------- #
def test_get_recommendation_dict_to_dict_raises_returns_none():
    class Boom:
        def to_dict(self):
            raise RuntimeError("boom")
    assert get_recommendation_dict(Boom()) is None, "to_dict 抛错须防御返回 None"


def test_get_recommendation_dict_partial_dict_passthrough():
    partial = {"preset": "ai_tiled"}  # 缺 scores/triggered/confidence
    assert get_recommendation_dict(partial) is partial, "含预设的偏量 dict 须原样透传"


def test_preset_label_empty_returns_empty():
    assert preset_label("") == "", "空预设名须返回空串（不崩、不落 i18n）"


# --------------------------------------------------------------------------- #
# 5 · 三渲染器对「无效输入」的返回差异（阶段 25 原义）
# --------------------------------------------------------------------------- #
def test_invalid_input_return_difference():
    # reason_line / bars_html 对无效输入返回空串；markdown 返回失败文案（非空串）。
    assert reason_line({}) == ""
    assert bars_html({}) == ""
    md = markdown({})
    assert md != "" and _t("detect.recommend.msg.no_result") in md


def test_reason_line_accepts_recommendation_object():
    rec = Recommendation(preset="ai_grid", scores={"grid": 0.9},
                         triggered=["grid"], confidence=0.9)
    rl = reason_line(rec)
    assert rl != "" and "90%" in rl, "reason_line 须接受 Recommendation 对象（走 to_dict 路径）"
