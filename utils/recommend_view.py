# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/recommend_view.py
 文件说明 : 推荐理由可视化共享渲染器（阶段 28，utils 层）。

            把 core.preset_recommender.Recommendation（机器可读）渲染成
            「人类可读 + 可可视化」的文案，供三处复用：
              · reason_line(dict) : 单行紧凑理由（预设 + 置信度 + 触发特征）
                                    —— 用于批量/媒体日志、PDF 报告文本。
              · bars_html(dict)   : 自带 inline CSS 的水平分数条（HTML）
                                    —— 用于 HTML 报告卡片可视化。
              · markdown(dict)    : Markdown（含字符分数条）
                                    —— 用于自动页「智能推荐」按钮结果。

            设计约束：
            · 位于 utils 层，只依赖 utils.i18n，不引入 gradio / torch / 任何
              界面库；因此 services 与 ui 都可安全 import（不破坏分层依赖）。
            · 翻译统一走 i18n（detect.preset.<name>.title / detect.recommend.*），
              键值缺失时回退到原始名，绝不暴露裸键。
            · 阶段 98 补齐：分隔符 / 行模板 / 空态文案也一律走 i18n（此前四处硬
              编码中文——「、」「：」「无推荐结果」「（无）」——英文界面会漏中文）；
              bars_html 的条宽与 _char_bar 一致钳制到 [0,1]。
版 本 号 : 1.1.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import html as _html
import os as _os
import sys as _sys
from typing import Dict, List, Optional, Union

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import utils.i18n` 在直接运行
# （python utils/recommend_view.py）时也可用。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from utils.i18n import t

# 8 个特征按稳定顺序展示（与 core.preset_recommender 的 scores 键一致）。
_FEATURE_KEYS = (
    "gradient", "grid", "tiled", "translucent",
    "solid_logo", "corner_watermark", "text", "signature", "regularity",
)
_BAR_LEN = 12  # 字符分数条长度（0~1 映射到 12 格）


# ==============================================================================
# 归一化与标签
# ==============================================================================
def get_recommendation_dict(rec) -> Optional[dict]:
    """
    把 Recommendation 对象或 dict 规整成 dict；无效（None / 无预设）返回 None。

    参数说明：
        rec : Recommendation 实例、dict，或 None。
    返回值：
        {"preset", "scores", "triggered", "confidence"} 或 None。
    """
    if rec is None:
        return None
    if isinstance(rec, dict):
        if not rec.get("preset"):
            return None
        return rec
    try:
        d = rec.to_dict()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return None
    if not d.get("preset"):
        return None
    return d


def preset_label(name: str) -> str:
    """把内部预设名映射成可读标签；i18n 缺失时回退原始名。"""
    if not name:
        return name or ""
    label = t(f"detect.preset.{name}.title")
    if label.startswith("detect.preset."):
        return name
    return label


def feature_label(key: str) -> str:
    """把特征键映射成本地化文案；缺失回退原始键。"""
    label = t(f"detect.recommend.feature.{key}")
    if label.startswith("detect.recommend.feature."):
        return key
    return label


def _feats_text(triggered: List[str]) -> str:
    """把触发特征列表拼成可读串（无则取 none 文案）。"""
    if not triggered:
        return t("detect.recommend.feature.none")
    # 分隔符走 i18n（阶段 98）：中文用「、」，英文用「, 」——此前硬编码「、」
    # 会让英文界面的理由行出现中文标点。
    return t("detect.recommend.list_sep").join(feature_label(k) for k in triggered)


# ==============================================================================
# 渲染器
# ==============================================================================
def reason_line(rec) -> str:
    """
    单行紧凑理由：推荐 <label>（置信度 72% · 触发 平铺、渐变）。

    无推荐结果时返回空串（调用方据此决定是否展示）。
    """
    d = get_recommendation_dict(rec)
    if not d:
        return ""
    label = preset_label(d.get("preset", ""))
    conf = float(d.get("confidence", 0.0) or 0.0)
    feats = _feats_text(d.get("triggered", []) or [])
    return t("detect.recommend.reason", preset=label, conf=conf, feats=feats)


def _char_bar(fill: float) -> str:
    """把 0~1 映射成 █/░ 字符条（长度 _BAR_LEN）。"""
    fill = max(0.0, min(1.0, float(fill)))
    n = int(round(fill * _BAR_LEN))
    return "█" * n + "░" * (_BAR_LEN - n)


def bars_html(rec) -> str:
    """
    自带 inline <style> 的水平分数条（HTML 片段）。

    只渲染分数 > 0 的特征；无有效推荐时返回空串（调用方据此跳过）。
    用于 HTML 报告卡片，直观「可视化」各特征激活强度。
    """
    d = get_recommendation_dict(rec)
    if not d:
        return ""
    scores = d.get("scores", {}) or {}
    rows = []
    for k in _FEATURE_KEYS:
        v = float(scores.get(k, 0.0) or 0.0)
        if v <= 0:
            continue
        # 阶段 98：宽度必须钳制到 [0,1]，与 _char_bar 保持一致；否则上游出现 >1
        # 的分值时 style="width:150%" 会溢出容器。展示值仍用原始 v（与 markdown 一致）。
        w = int(round(max(0.0, min(1.0, v)) * 100))
        rows.append(
            '<div class="rbar">'
            f'<span class="rl">{_html.escape(feature_label(k))}</span>'
            '<span class="rt"><span class="rf" style="width:'
            f'{w}%"></span></span>'
            f'<span class="rv">{v:.2f}</span>'
            '</div>'
        )
    if not rows:
        return ""
    style = (
        '<style>.rbar{display:flex;align-items:center;gap:6px;'
        'font-size:12px;margin:2px 0;}'
        '.rbar .rl{width:96px;text-align:right;color:#4b5563;}'
        '.rbar .rt{flex:1;background:#eef2f7;border-radius:6px;'
        'height:10px;overflow:hidden;}'
        '.rbar .rf{display:block;height:10px;'
        'background:linear-gradient(90deg,#6366f1,#22d3ee);}'
        '.rbar .rv{width:38px;color:#6b7280;text-align:right;}</style>'
    )
    return style + '<div class="rbars">' + "".join(rows) + "</div>"


def markdown(rec) -> str:
    """
    Markdown（含字符分数条）。用于自动页「智能推荐」按钮的结果展示。

    无推荐时返回失败文案（与 stage 25 原 _build_recommend_markdown 行为一致）。
    """
    d = get_recommendation_dict(rec)
    if not d:
        # 阶段 98：exc 走 i18n（此前硬编码「无推荐结果」，英文界面漏中文）。
        return t("detect.recommend.msg.fail",
                 exc=t("detect.recommend.msg.no_result"))
    label = preset_label(d.get("preset", ""))
    title = t(
        "detect.recommend.title",
        preset=label,
        conf=float(d.get("confidence", 0.0) or 0.0),
    )
    lines = [title, ""]
    triggered = d.get("triggered", []) or []
    if triggered:
        for feat in triggered:
            lines.append(f"- ✅ {feature_label(feat)}")
    else:
        lines.append(f"- {t('detect.recommend.feature.none')}")
    lines.append("")
    lines.append(t("detect.recommend.scores_title"))
    scores = d.get("scores", {}) or {}
    shown = False
    for k in _FEATURE_KEYS:
        v = float(scores.get(k, 0.0) or 0.0)
        if v <= 0:
            continue
        # 行模板走 i18n（阶段 98）：中文「：」/英文「: 」不再硬编码。
        lines.append(t("detect.recommend.score_line",
                       name=feature_label(k), bar=_char_bar(v), val=f"{v:.2f}"))
        shown = True
    if not shown:
        lines.append("  - " + t("detect.recommend.scores_none"))
    return "\n".join(lines)


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    from utils.i18n import set_language
    set_language("zh-CN")  # 自检用简体中文断言。

    sample = {
        "preset": "ai_tiled",
        "scores": {
            "gradient": 0.0, "grid": 0.0, "tiled": 0.71,
            "translucent": 0.4, "solid_logo": 0.0, "text": 0.05,
            "signature": 0.0, "regularity": 0.13,
        },
        "triggered": ["tiled"],
        "confidence": 0.71,
    }
    rl = reason_line(sample)
    assert "ai_tiled" not in rl, f"reason_line 应显示可读标签而非裸键：{rl}"
    assert "72%" in rl or "71%" in rl, f"reason_line 应含置信度百分比：{rl}"
    assert "平铺" in rl, f"reason_line 应含触发特征文案：{rl}"
    print("用例1通过：reason_line →", rl)

    html = bars_html(sample)
    assert "rf" in html and "平铺" in html, "bars_html 应含分数条与特征标签"
    print("用例2通过：bars_html 长度", len(html))

    md = markdown(sample)
    assert "ai_tiled" not in md, "markdown 应显示可读标签"
    assert "█" in md and "0.71" in md, "markdown 应含字符条与分值"
    print("用例3通过：markdown 含分数条")

    assert reason_line(None) == "", "None 应返回空串"
    assert get_recommendation_dict({"preset": ""}) is None, "空预设应判无效"
    print("用例4通过：空值/无效输入安全返回")

    # 标签回退：未知键不应暴露裸键。
    assert feature_label("__nope__") == "__nope__"

    # 阶段 98：英文侧不得残留任何中文字符——分隔符、行模板、空态文案都要走 i18n。
    empty = {"preset": "ai_tiled", "scores": {}, "triggered": [], "confidence": 0.0}
    set_language("en-US")
    _cj = lambda s: next((c for c in s if "\u4e00" <= c <= "\u9fff"), None)
    for _txt in (reason_line(sample), bars_html(sample), markdown(sample),
                 markdown(None), markdown(empty)):
        _bad = _cj(_txt)
        assert _bad is None, f"英文界面漏出中文 {_bad!r}：{_txt!r}"
    set_language("zh-CN")
    print("用例5通过：英文侧无中文残留")

    print("preset_recommend_view.py 自检全部通过。")
