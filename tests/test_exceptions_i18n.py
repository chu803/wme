# -*- coding: utf-8 -*-
"""
阶段 168 守卫：异常文案 i18n 泄漏收口。

背景（经验 #197）：异常渲染的标题 / 建议 / 结构标签原本全部硬编码中文，经
`format_exception_for_ui` → `t("single.msg.fail", exc=...)` 拼进英文界面，造成
「外层模板翻译了、内层异常仍是中文」的泄漏。

本守卫钉住三条契约：
  1. 覆盖：所有 `WatermarkEraserError` 子类的 `default_title` 与 `default_suggestions`
     每一条都必须出现在 `_PHRASE_EN` 译文表中 —— 漏翻（新增异常忘登记 EN）立即红。
  2. 渲染：en-US 下 `to_text` / `to_markdown` / `to_dict` 的结构标签、标题、建议
     全部为英文，且**不含任何 CJK / 全角标点**（❌ 符号除外，它非 CJK）。
  3. 结构标签表：en-US 侧零 CJK；zh-CN 侧仍含中文（确保没把原文清空）。
"""

import re

import pytest

import utils.exceptions as E
from utils.exceptions import WatermarkEraserError

# CJK 与全角标点范围（❌ U+274C 不在其中，属语言中立符号，允许保留）。
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def _all_subclasses():
    """模块内定义的所有 WatermarkEraserError 子类（含基类自身）。"""
    return [
        obj
        for name, obj in vars(E).items()
        if isinstance(obj, type)
        and issubclass(obj, WatermarkEraserError)
    ]


# ------------------------------------------------------------------------------
# 契约 1：覆盖 —— 所有默认文案必须有英文译文
# ------------------------------------------------------------------------------

def test_all_default_titles_have_en():
    for cls in _all_subclasses():
        title = cls.default_title
        assert title in E._PHRASE_EN, (
            f"{cls.__name__}.default_title 缺少英文译文：{title!r}"
        )


def test_all_default_suggestions_have_en():
    for cls in _all_subclasses():
        for idx, sugg in enumerate(cls.default_suggestions):
            assert sugg in E._PHRASE_EN, (
                f"{cls.__name__}.default_suggestions[{idx}] 缺少英文译文：{sugg!r}"
            )


# ------------------------------------------------------------------------------
# 契约 2：en-US 渲染零中文泄漏
# ------------------------------------------------------------------------------

# 覆盖各子树的代表类（资源 / 配置 / 模型 / 图片 / 媒体 / 网络 / 任务 / 服务）。
_REPRESENTATIVE = [
    E.OutOfVRAMError,
    E.OutOfMemoryError,
    E.DiskFullError,
    E.ConfigError,
    E.InvalidConfigValueError,
    E.ModelNotFoundError,
    E.ModelCorruptedError,
    E.ModelLoadError,
    E.ModelDownloadError,
    E.UnsupportedFormatError,
    E.CorruptedImageError,
    E.ImageTooLargeError,
    E.EmptyMaskError,
    E.InvalidMaskError,
    E.DetectionError,
    E.UnsupportedMediaError,
    E.CorruptedMediaError,
    E.MediaTooLargeError,
    E.NetworkError,
    E.DownloadTimeoutError,
    E.QueueFullError,
    E.TaskTimeoutError,
    E.TaskCancelledError,
    E.PortInUseError,
]


@pytest.mark.parametrize("cls", _REPRESENTATIVE, ids=lambda c: c.__name__)
def test_en_markdown_no_cjk_when_reason_empty(cls):
    """reason / detail / cause 均为空时，en-US 渲染必须零 CJK。"""
    exc = cls(reason="", cause=None)
    md = exc.to_markdown(lang="en-US")
    assert not _has_cjk(md), f"{cls.__name__} en-US 仍含中文：{md!r}"


@pytest.mark.parametrize("cls", _REPRESENTATIVE, ids=lambda c: c.__name__)
def test_en_text_no_cjk_when_reason_empty(cls):
    exc = cls(reason="", cause=None)
    txt = exc.to_text(lang="en-US")
    assert not _has_cjk(txt), f"{cls.__name__} en-US to_text 仍含中文：{txt!r}"


@pytest.mark.parametrize("cls", _REPRESENTATIVE, ids=lambda c: c.__name__)
def test_en_dict_no_cjk_when_reason_empty(cls):
    exc = cls(reason="", cause=None)
    d = exc.to_dict(lang="en-US")
    blob = " ".join(str(v) for v in d.values())
    assert not _has_cjk(blob), f"{cls.__name__} en-US to_dict 仍含中文：{d!r}"


def test_en_render_contains_english_labels_and_not_chinese():
    """en-US 渲染必须出现英文结构标签、且**不**出现中文结构标签。"""
    md = E.OutOfVRAMError(reason="", cause=None).to_markdown(lang="en-US")
    assert "GPU out of memory" in md          # 标题已译
    assert "**Suggested fix**" in md          # Markdown 建议标签已译
    assert "Underlying exception" not in md   # 无 cause 时不该出现
    # 中文结构标签必须消失
    assert "原因" not in md
    assert "**解决建议**" not in md
    assert "建议" not in md


def test_en_render_preserves_dynamic_reason():
    """reason 是调用方传入的动态内容（可能本地化语言不一），须原样透传。"""
    exc = E.OutOfVRAMError(reason="显存不足（动态内容）", cause=None)
    md = exc.to_markdown(lang="en-US")
    assert "GPU out of memory" in md          # 结构/标题已英文化
    assert "显存不足（动态内容）" in md        # 动态 reason 原样保留（设计如此）


def test_format_exception_for_ui_is_lang_aware():
    """UI 统一入口在全局 en-US 下应产出英文 markdown。"""
    orig = E._get_language
    E._get_language = lambda: "en-US"
    try:
        out = E.format_exception_for_ui(E.OutOfVRAMError(reason="", cause=None))
        assert "GPU out of memory" in out
        assert not _has_cjk(out)
    finally:
        E._get_language = orig


def test_format_exception_for_ui_passthrough_non_watermark():
    """非本项目异常仍原样返回 str（零回归）。"""
    assert E.format_exception_for_ui(ValueError("boom")) == "boom"


# ------------------------------------------------------------------------------
# 契约 3：结构标签表本身的中英文完整性
# ------------------------------------------------------------------------------

def test_struct_labels_en_has_no_cjk():
    for key, val in E._STRUCT_LABELS["en-US"].items():
        assert not _has_cjk(val), f"_STRUCT_LABELS['en-US']['{key}'] 含中文：{val!r}"


def test_struct_labels_zh_retains_chinese():
    # 至少标题包装与建议标签在 zh-CN 下仍是中文（确保没误清空原文）。
    assert "【" in E._STRUCT_LABELS["zh-CN"]["title_wrap"]
    assert "建议" in E._STRUCT_LABELS["zh-CN"]["md_suggest"]


def test_zh_render_intact_by_default():
    """默认（zh-CN）渲染不被破坏 —— 中文标题与结构标签仍在。"""
    md = E.OutOfVRAMError().to_markdown()
    assert "显卡显存不足" in md
    assert "**解决建议**" in md
