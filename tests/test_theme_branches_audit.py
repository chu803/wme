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


def test_compliance_blockquote_keeps_readable_contrast():
    """阶段 377：引用块是浅黄底（#fffbeb），文字必须被钉成深色。

    实测缺陷：深色主题下 Gradio 把 blockquote 里的 <p>/<strong> 染成近白
    rgb(243,244,246)，与浅黄底对比度约 **1.1:1** —— 首屏《合规提醒》几乎读不清
    （真浏览器 computed style 实测，见 docs/377）。

    修法：对 blockquote 的**子元素**单独下规则、并提高特异性 + `!important`
    （Gradio 的 `.dark .prose p` 也是 !important，同权重时按加载顺序可能胜出）。
    """
    css = theme_mod.build_app_css()
    assert "background: #fffbeb" in css, "合规提醒的浅黄底不见了？"
    assert "blockquote p" in css and "blockquote strong" in css, (
        "缺少对 blockquote 子元素的文字颜色规则 ⇒ 深色主题下会被染成近白色")
    seg = css[css.index("blockquote p"):]
    seg = seg[:seg.index("}")] if "}" in seg else seg
    assert "!important" in seg, "子元素规则没有 !important ⇒ 压不过主题规则"


def test_image_editor_placeholder_uses_theme_subdued():
    """阶段 378：Gradio 画布空态提示的对比度由主题的 subdued 色统一兜底。

    实测：该提示在深色主题下只有 3.03:1；浅色主题下用固定浅灰会掉到 2.54:1。
    修法：颜色改用 `var(--body-text-color-subdued)`，由主题的浅/深两套值保证可读
    （主题值见 `test_secondary_text_colors_are_readable_in_both_modes`）。
    """
    css = theme_mod.build_app_css()
    assert ".image-container .empty.wrap" in css, "画布占位文字的规则不见了"
    assert "var(--body-text-color-subdued)" in css, "占位文字没有走主题的 subdued 变量"


def test_secondary_text_colors_are_readable_in_both_modes():
    """阶段 378b：次要文字（info / subdued）必须按明暗给可读值。

    实测（真浏览器 + ?__theme=light）：Gradio Soft 主题在浅色下把 info 说明行设为极浅灰
    （白底 1.91:1）、滑杆刻度与画布占位用 subdued（白底 2.54:1），都低于 4.5:1。
    修法走主题 API 的 `_dark` 变体（CSS 变量方案实测会被 Gradio 的选择器重写打乱）。
    """
    th = theme_mod.build_app_theme()
    assert th.body_text_color_subdued == "#6b7280"
    assert th.body_text_color_subdued_dark == "#9ca3af"
    assert th.block_info_text_color == "#4b5563"
    assert th.block_info_text_color_dark == "#9ca3af"


def test_label_text_color_is_left_to_the_theme():
    """已知边界：标签/标题的文字色**不覆盖**。

    实测：标签元素的底色是主色蓝（Gradio 的复合样式），改文字色会把**默认深色模式**
    的对比度从 5:1 弄坏到 2.87:1。保留 Gradio 原值；浅色模式下标签 3.01:1 记入
    docs/378 的残余清单，等 Gradio 上游或后续单独评估。
    """
    th = theme_mod.build_app_theme()
    assert "block_label_text_color" not in theme_mod.APP_CSS
    assert getattr(th, "block_label_text_color", None) not in ("#1e40af", "#93c5fd")
