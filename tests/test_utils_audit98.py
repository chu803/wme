# -*- coding: utf-8 -*-
"""
阶段 98：utils/recommend_view 审计 —— 注入式反例守卫。

审计结论（见 docs/98-*.md）：
  D1 真缺陷（可达）——渲染器内嵌硬编码中文：分隔符「、」、行标点「：」、
      空态「（无）」、无推荐时的 exc「无推荐结果」。英文界面必漏中文，
      违反模块 docstring「翻译统一走 i18n，绝不暴露裸键」。
  D2 一致性缺陷（低危）——bars_html 未钳制分值，_char_bar 钳制 ⇒
      上游 >1 分会让 style="width:>100%" 溢出容器。
  D3 边界（SSOT 漂移风险）——_FEATURE_KEYS 是 core.preset_recommender
      scores 键的硬编码副本（当前一致）⇒ 加漂移守卫。

四组守卫：
  I1  全渲染器在 en-US 下不得残留中文字符（含空态 / 无推荐分支）；
      且源码里出现的每个 detect.recommend.* 键必须中英双全。
  I2  bars_html 条宽钳制到 [0,1]（与 _char_bar 对齐），展示值仍为原始值。
  I3  _FEATURE_KEYS 与 core.preset_recommender 的 scores 键**同名同序**
      （ast 从源码提取，不依赖 RECOMMEND_ENABLED）。
  I4  源码级：utils/recommend_view.py 函数体字符串字面量不得含中文
      （docstring 与 __main__ 自检除外）——这一类缺陷的通解守卫。
"""

import ast
import io
import os

import pytest

from utils.i18n import set_language, t, t_in
from utils.recommend_view import (
    _FEATURE_KEYS,
    bars_html,
    feature_label,
    markdown,
    reason_line,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RV_PATH = os.path.join(_ROOT, "utils", "recommend_view.py")
_PR_PATH = os.path.join(_ROOT, "core", "preset_recommender.py")

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
EMPTY_SCORES = {"preset": "ai_tiled", "scores": {}, "triggered": [], "confidence": 0.0}
MULTI = dict(SAMPLE, triggered=["tiled", "translucent"], confidence=0.6)


def _first_cjk(s: str):
    return next((c for c in s if "\u4e00" <= c <= "\u9fff"), None)


@pytest.fixture(autouse=True)
def _zh_default():
    """用例前后恢复简体中文，避免污染其它测试模块。"""
    set_language("zh-CN")
    yield
    set_language("zh-CN")


# ==============================================================================
# I1 · D1 真缺陷守卫：英文界面不得漏中文
# ==============================================================================
@pytest.mark.parametrize("rec", [SAMPLE, EMPTY_SCORES, MULTI, None])
def test_i1_en_renderers_have_no_cjk(rec):
    """注入反例：en-US 下四种输入的三种渲染器输出都不得含中文字符。"""
    set_language("en-US")
    for name, out in (
        ("reason_line", reason_line(rec)),
        ("bars_html", bars_html(rec)),
        ("markdown", markdown(rec)),
    ):
        bad = _first_cjk(out)
        assert bad is None, f"{name} 在 en-US 下漏出中文 {bad!r}：{out!r}"


def test_i1_zh_still_chinese_and_separators_localized():
    """反向守卫：zh 侧仍必须是中文（防止「改成英文常量」式假修复）。"""
    set_language("zh-CN")
    md = markdown(SAMPLE)
    assert _first_cjk(md) is not None, "zh-CN 下 markdown 应含中文文案"
    assert t("detect.recommend.list_sep") == "、"
    assert t("detect.recommend.list_sep").join(
        [feature_label("tiled"), feature_label("translucent")]
    ) in reason_line(MULTI), "reason_line 的多特征应使用本地化分隔符"
    set_language("en-US")
    assert t("detect.recommend.list_sep") == ", "
    assert t("detect.recommend.list_sep").join(
        [feature_label("tiled"), feature_label("translucent")]
    ) in reason_line(MULTI), "en-US 的 reason_line 应改用「, 」分隔"
    assert "、" not in reason_line(MULTI), "en-US 不得残留中文顿号"


def _rv_recommend_keys():
    """从源码 ast 提取渲染器实际用到的 detect.recommend.* 常量键 + f-string 键。"""
    tree = ast.parse(io.open(_RV_PATH, encoding="utf-8").read())
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "t" and node.args):
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str) \
                    and a0.value.startswith("detect.recommend."):
                keys.add(a0.value)
    # f-string 动态拼出的键：feature_label / preset_label 两条通路。
    keys |= {f"detect.recommend.feature.{k}" for k in _FEATURE_KEYS}
    return sorted(keys)


def test_i1_referenced_keys_exist_in_both_languages():
    """源码引用的每个键都必须中英双全——「新增键只加一边」的机器判据。"""
    keys = _rv_recommend_keys()
    assert "detect.recommend.list_sep" in keys
    assert "detect.recommend.score_line" in keys
    assert "detect.recommend.scores_none" in keys
    assert "detect.recommend.msg.no_result" in keys
    for key in keys:
        for lang in ("zh-CN", "en-US"):
            assert t_in(lang, key) != key, f"{key} 缺 {lang} 翻译"


# ==============================================================================
# I2 · D2 一致性守卫：条宽钳制
# ==============================================================================
def test_i2_bars_html_width_is_clamped():
    """注入反例：分值 1.8（超界）时宽度应被钳到 100%，而非 180%。"""
    html = bars_html({"preset": "ai_tiled",
                      "scores": {"tiled": 1.8, "text": 0.5},
                      "triggered": [], "confidence": 0.9})
    assert "width:100%" in html, f"超界分值未钳制：{html}"
    assert "width:180%" not in html
    assert "width:180" not in html
    # 正常区间不受影响。
    assert "width:71%" in bars_html(SAMPLE)


def test_i2_bars_html_display_value_is_raw():
    """展示值仍取原始分（与 markdown 的 **{v:.2f}** 口径一致），只钳宽度。"""
    html = bars_html({"preset": "ai_tiled", "scores": {"tiled": 1.8},
                      "triggered": [], "confidence": 0.9})
    assert "1.80" in html, "展示值应为原始分 1.80（钳制只作用于条宽）"


def test_i2_char_bar_and_bars_html_same_clamp_rule():
    """两种分数条对同一超界输入的「钳制后比例」必须一致（防止两条通路再分叉）。"""
    from utils.recommend_view import _char_bar
    assert _char_bar(1.8).count("█") == 12, "字符条应钳满 12 格"
    assert "width:100%" in bars_html({"preset": "ai_tiled", "scores": {"tiled": 1.8},
                                      "triggered": [], "confidence": 1.0})


# ==============================================================================
# I3 · D3 边界守卫：_FEATURE_KEYS 与推荐器 scores 键同名同序
# ==============================================================================
def _recommender_score_keys():
    """用 ast 从 core/preset_recommender.py 提取 scores 字典字面量的键（有序）。"""
    tree = ast.parse(io.open(_PR_PATH, encoding="utf-8").read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "scores" and isinstance(node.value, ast.Dict):
            found.append([k.value for k in node.value.keys
                          if isinstance(k, ast.Constant) and isinstance(k.value, str)])
    assert len(found) == 1, f"应恰好找到 1 处 scores 字典字面量，实际 {len(found)}"
    return found[0]


def test_i3_feature_keys_match_recommender_scores():
    """SSOT 漂移守卫：展示键集必须与推荐器 scores 键**同名同序**。"""
    canonical = _recommender_score_keys()
    assert list(_FEATURE_KEYS) == canonical, (
        "utils/recommend_view._FEATURE_KEYS 与 core.preset_recommender 的 scores "
        f"键已漂移：{list(_FEATURE_KEYS)} != {canonical}"
    )


def test_i3_every_score_key_has_i18n_label():
    """每个展示键都必须有本地化特征文案（新增键时忘配文案会被这里拦住）。"""
    for k in _FEATURE_KEYS:
        for lang in ("zh-CN", "en-US"):
            assert t_in(lang, f"detect.recommend.feature.{k}") != \
                f"detect.recommend.feature.{k}", f"feature.{k} 缺 {lang} 翻译"


# ==============================================================================
# I4 · 通解守卫：模块函数体不得内嵌中文长字面量
# ==============================================================================
def _code_string_literals(path):
    """收集「非 docstring、且不在 __main__ 自检块内」的字符串字面量。"""
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    docstrings, skip = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and isinstance(node.test.left, ast.Name) \
                and node.test.left.id == "__name__":
            skip |= {id(sub) for sub in ast.walk(node)}
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings and id(n) not in skip]


def test_i4_no_cjk_literals_in_module_body():
    """注入反例：函数体里一旦再写中文文案（i18n 泄漏）本用例立即变红。"""
    bad = [_s for _s in _code_string_literals(_RV_PATH) if _first_cjk(_s)]
    assert not bad, f"utils/recommend_view.py 函数体内仍内嵌中文文案：{bad}"


def test_i4_guard_itself_detects_a_planted_leak(tmp_path):
    """守卫自检：人为植入一段中文文案时，判据必须能抓出来（否则守卫是摆设）。"""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "def f():\n"
        "    return '\u65e0\u63a8\u8350\u7ed3\u679c'\n",
        encoding="utf-8",
    )
    bad = [_s for _s in _code_string_literals(str(planted)) if _first_cjk(_s)]
    assert bad == ["\u65e0\u63a8\u8350\u7ed3\u679c"], "守卫未能识别植入的中文文案"
