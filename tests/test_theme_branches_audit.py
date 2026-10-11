# -*- coding: utf-8 -*-
"""
阶段 288 续审守卫：ui/theme.py 首轮 (阶段 91, tests/test_ui_audit91.py) 未机控的
「安全兜底分支」不变量钉死。

阶段 91 已覆盖：品牌令牌单一真值来源（哨兵）、builtin_theme_names 来自 Gradio
运行时注册表、resolve_theme_name 接受每个运行时主题/拒绝非法名（不触 Hub 联网）、
APP_CSS == build_app_css()。

本套件补齐首轮漏掉的三类兜底：
  1. build_app_theme() 双层 try/except 兜底（.set 失败 / 连 Soft() 都失败）。
  2. builtin_theme_names() 注册表不可读时返回 ()（不误放行任何内置名）。
  3. resolve_theme_name() 在 names=() 与空串/纯空白/None 输入下的回退语义。

探针 (.scratch/probe_theme_reaudit.py) 全绿，结论：0 真缺陷，仅补齐分支守卫。
"""
import gradio as gr

from ui import theme as theme_mod
from utils import brand


# --------------------------------------------------------------------------- #
# F1 + F2：build_app_theme() 双层兜底
# --------------------------------------------------------------------------- #
def test_build_app_theme_outer_set_failure_is_caught(monkeypatch):
    """外层 `.set(...)` 抛异常时，build_app_theme() 不崩溃，
    返回一个可交给 launch() 的兜底值（Base 实例 或 字符串 'soft'）。"""
    class _BoomSet(gr.themes.Soft):
        def set(self, **kwargs):
            raise RuntimeError("forced set failure")

    monkeypatch.setattr(gr.themes, "Soft", _BoomSet)
    t = theme_mod.build_app_theme()
    assert t is not None, "外层 set 失败不应返回 None（必须可 launch）"
    assert isinstance(t, gr.themes.Base) or t == "soft", \
        f"兜底值必须是 Base 实例或 'soft'，实际 {type(t).__name__}"


def test_build_app_theme_double_failure_falls_back_to_soft_string(monkeypatch):
    """连 gr.themes.Soft() 构造都失败时，最终兜底为字符串 'soft'（Gradio 解析为
    与 Soft() 同款的默认主题，launch 仍接受）。"""
    class _BoomAll:
        def __init__(self, *a, **k):
            raise RuntimeError("boom all")

    monkeypatch.setattr(gr.themes, "Soft", _BoomAll)
    t = theme_mod.build_app_theme()
    assert t == "soft", f"双重失败必须兜底为 'soft' 字符串，实际 {t!r}"


# --------------------------------------------------------------------------- #
# F3：builtin_theme_names() 注册表不可读 -> ()
# --------------------------------------------------------------------------- #
def test_builtin_theme_names_returns_empty_tuple_when_registry_unreadable(monkeypatch):
    """Gradio 运行时注册表不可读时返回 ()，调用方据此把任何非 brand 名判为非法回退，
    绝不误放行（否则会走到 Theme.from_hub() 联网下载）。"""

    class _BrokenReg:
        def keys(self):
            raise RuntimeError("broken registry")

    monkeypatch.setattr(gr.utils, "BUILT_IN_THEMES", _BrokenReg())
    assert theme_mod.builtin_theme_names() == (), \
        "注册表不可读时必须返回空元组，不能误放行任何内置名"


# --------------------------------------------------------------------------- #
# F4：resolve_theme_name 在 names=() 时任何非 brand 名都回退 brand
# --------------------------------------------------------------------------- #
def test_resolve_theme_name_falls_back_brand_when_names_empty(monkeypatch):
    monkeypatch.setattr(theme_mod, "builtin_theme_names", lambda: ())
    for v in ("default", "monochrome", "glass", "ocean", "", "   ", None, "gradio-dark"):
        assert theme_mod.resolve_theme_name(v) == theme_mod.BRAND_THEME_NAME, \
            f"names=() 时 {v!r} 必须回退 brand（不误放行）"
    # brand 自身在 names=() 时仍命中首分支返回 brand
    assert theme_mod.resolve_theme_name("brand") == theme_mod.BRAND_THEME_NAME


# --------------------------------------------------------------------------- #
# F5：resolve_theme_name 边界输入（真实注册表）
# --------------------------------------------------------------------------- #
def test_resolve_theme_name_edge_inputs():
    assert theme_mod.resolve_theme_name("") == theme_mod.BRAND_THEME_NAME, "空串 -> brand"
    assert theme_mod.resolve_theme_name("   ") == theme_mod.BRAND_THEME_NAME, "纯空白 -> brand"
    assert theme_mod.resolve_theme_name(None) == theme_mod.BRAND_THEME_NAME, \
        "None -> brand（str(None)='None' 既非 brand 也不在白名单）"
    assert theme_mod.resolve_theme_name("BRAND") == theme_mod.BRAND_THEME_NAME, \
        "brand 大小写不敏感"


def test_resolve_theme_name_passes_through_valid_builtin():
    names = theme_mod.builtin_theme_names()
    assert names, "Gradio 运行时主题注册表不可读，无法验证透传"
    tgt = "monochrome" if "monochrome" in names else names[0]
    assert theme_mod.resolve_theme_name(tgt) == tgt, "合法内置名透传（配置生效）"
    assert theme_mod.resolve_theme_name(tgt.upper()) == tgt, "大写归一为小写"


# --------------------------------------------------------------------------- #
# F6：build_app_css 占位符全替换 + APP_CSS 常量一致（调用时读取品牌令牌）
# --------------------------------------------------------------------------- #
def test_build_app_css_has_no_residual_placeholders():
    css = theme_mod.build_app_css()
    assert "__PRIMARY__" not in css
    assert "__ACCENT__" not in css
    assert "__PRIMARY_RGBA_35__" not in css


def test_app_css_constant_matches_builder():
    assert theme_mod.APP_CSS == theme_mod.build_app_css(), \
        "APP_CSS 常量必须与 build_app_css() 一致（防绕过 builder）"


def test_build_app_css_reads_brand_tokens_at_call_time(monkeypatch):
    sentinel_p, sentinel_a = "#123456", "#654321"
    monkeypatch.setattr(brand, "PRIMARY_HEX", sentinel_p)
    monkeypatch.setattr(brand, "ACCENT_HEX", sentinel_a)
    css = theme_mod.build_app_css()
    assert sentinel_p in css and sentinel_a in css, \
        "build_app_css() 必须在调用时读取品牌令牌（单一真值来源）"
