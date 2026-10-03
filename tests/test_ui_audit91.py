# -*- coding: utf-8 -*-
"""阶段 91 真值审计守卫：ui 层首批（theme / app）+ 品牌令牌单一真值来源。

背景（全部由 `%TEMP%/wme_s91_probe2.py` 实测取得，非推测）：

  A1  `ui/app.py` 的 `_resolve_theme()` / `_VALID_THEMES` 是**死代码**（AST 级
      零调用点），`config.SERVER_THEME` 全仓无人消费：取 'soft' / 'monochrome'
      / 'not-a-theme' 三值时，`launch()` 收到的 theme 实参**完全相同**。
  A2  `_VALID_THEMES` 是**假守卫**：Gradio 6.22 的运行时注册表
      （`gradio.utils.BUILT_IN_THEMES`）只有 base/default/monochrome/soft/
      glass/origin/citrus/ocean/mario；白名单里的 `gradio-dark` / `huggingface`
      并不存在，而真实存在的 5 个反被拒。且 `get_theme()` 对未注册名会走
      `Theme.from_hub()` 去 HuggingFace 联网下载（本项目网络不可达）。
  B1  品牌主色在修复前有 **6 处字面量、跨 3 个文件**（theme.py 常量 + CSS 内联、
      app.py 注入 JS、export_service.py 对比图），且存在「37,99,235」与
      「37, 99, 235」两种空格变体——改一处不全局生效，且**无任何自动化会失败**。
  U3  `build_app()` 从不调用 `clear_registry()`：重复构建使注册表线性累积
      （实测 288 → 576），新事件 outputs 里混入上一次构建的废弃组件。
  U4  `_tab_rename_map_json()` 直接下标 `TRANSLATIONS[lang][...][key]`，绕过了
      `t()` 的「缺失回退」守卫——键一旦改名会在构建期抛 KeyError 而非降级。

守卫策略（对齐项目经验 23「单一真值来源要用哨兵，而非字符串断言」）：
  品牌令牌用**哨兵替换** -> 断言新值出现在每个出口、旧值消失；
  主题解析用**假 launch 捕获实参** -> 断言配置真的改变了传给 Gradio 的值。
"""

import ast
import json
import os
from typing import Optional

import numpy as np
import pytest

import config
from ui import app as app_mod
from ui import i18n_rt, theme as theme_mod
from services import export_service as _es
from utils import brand
from utils import i18n

# --------------------------------------------------------------------------- #
# 常量与工具
# --------------------------------------------------------------------------- #

#: 阶段 91 修复前的品牌色字面量（用于断言"旧副本已消失"）。
LEGACY_LITERALS = ["#2563eb", "#1d4ed8", "#0ea5e9", "37, 99, 235", "37,99,235",
                   "30, 41, 59", "30,41,59"]

SENTINEL_PRIMARY_HEX = "#123456"
SENTINEL_ACCENT_HEX = "#654321"
SENTINEL_HOVER_HEX = "#abcdef"
SENTINEL_INK_HEX = "#0a0b0c"


def _sentinel_rgb(hex_color: str):
    return brand.hex_to_rgb(hex_color)


def _black(h: int = 64, w: int = 64):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _with_box(h: int = 64, w: int = 64):
    a = np.full((h, w, 3), 255, dtype=np.uint8)
    a[h // 4: h // 2, w // 4: w // 2] = 0
    return a


def _count_color(arr, rgb, tol: int = 2) -> int:
    """统计与 rgb 差不超过 tol 的像素个数（抗锯齿文字的容差比较）。"""
    d = np.abs(arr.astype(np.int16) - np.array(rgb, dtype=np.int16))
    return int((d.max(axis=-1) <= tol).sum())


class _FakeApp:
    """鸭子类型替身：只记录 launch() 收到的实参，绝不真的起服务。"""

    def __init__(self):
        self.kwargs: Optional[dict] = None

    def launch(self, **kwargs):
        self.kwargs = kwargs


def _capture_launch(monkeypatch, theme_value):
    """用给定 config.SERVER_THEME 调用 launch_app，返回它传给 launch 的实参。"""
    monkeypatch.setattr(config, "SERVER_THEME", theme_value)
    monkeypatch.setattr(config, "SERVER_AUTO_PORT", False)  # 避免真探测端口
    fake = _FakeApp()
    rc = app_mod.launch_app(fake)
    assert rc == 0, f"launch_app 返回退出码 {rc}"
    assert fake.kwargs is not None, "launch_app 未调用 app.launch()"
    return fake.kwargs


@pytest.fixture
def sentinel_brand(monkeypatch):
    """把所有品牌令牌换成哨兵值（消费方必须"调用时读取"才能看到）。"""
    monkeypatch.setattr(brand, "PRIMARY_HEX", SENTINEL_PRIMARY_HEX)
    monkeypatch.setattr(brand, "ACCENT_HEX", SENTINEL_ACCENT_HEX)
    monkeypatch.setattr(brand, "PRIMARY_HOVER_HEX", SENTINEL_HOVER_HEX)
    monkeypatch.setattr(brand, "INK_HEX", SENTINEL_INK_HEX)
    yield


# =========================================================================== #
# A1 + A2：config.SERVER_THEME 真正生效 / 白名单来自 Gradio 运行时
# =========================================================================== #

def test_server_theme_brand_uses_custom_theme_object(monkeypatch):
    """默认 'brand' -> 交给 launch 的是品牌定制 Theme 对象。"""
    import gradio as gr

    kw = _capture_launch(monkeypatch, "brand")
    assert isinstance(kw["theme"], gr.themes.Base)
    assert not isinstance(kw["theme"], str)


def test_server_theme_builtin_name_is_passed_through(monkeypatch):
    """填合法内置主题名 -> 原样传给 launch（配置真的生效，不再是死配置）。"""
    names = theme_mod.builtin_theme_names()
    assert names, "Gradio 运行时主题注册表不可读，无法验证透传"
    target = "monochrome" if "monochrome" in names else names[0]
    kw = _capture_launch(monkeypatch, target)
    assert kw["theme"] == target
    # 大小写与空白不敏感。
    kw2 = _capture_launch(monkeypatch, f"  {target.upper()}  ")
    assert kw2["theme"] == target


def test_server_theme_distinct_values_change_launch_theme(monkeypatch):
    """回归护栏：修复前 soft / monochrome / 非法值 三者实参完全相同。"""
    names = theme_mod.builtin_theme_names()
    if not names:
        pytest.skip("Gradio 运行时主题注册表不可读")
    builtin = "ocean" if "ocean" in names else names[0]
    t_brand = _capture_launch(monkeypatch, "brand")["theme"]
    t_builtin = _capture_launch(monkeypatch, builtin)["theme"]
    t_bogus = _capture_launch(monkeypatch, "definitely-not-a-theme-91")["theme"]

    assert t_builtin == builtin and t_builtin != t_brand, "内置主题名未生效"
    # 非法值回退品牌主题：与 brand 同类型，且都不是那个非法字符串。
    assert not isinstance(t_bogus, str), "非法主题名被原样透传（会触发联网下载）"
    assert type(t_bogus).__name__ == type(t_brand).__name__


def test_invalid_theme_never_reaches_hub_lookup(monkeypatch):
    """非法主题名必须在本地被拦住，绝不走 Theme.from_hub()（联网）。"""
    import gradio as gr

    calls = []

    def _record_from_hub(*args, **kwargs):
        calls.append((args, kwargs))
        return gr.themes.Soft()

    monkeypatch.setattr(gr.themes.Base, "from_hub", _record_from_hub)
    _capture_launch(monkeypatch, "definitely-not-a-theme-91")
    _capture_launch(monkeypatch, "gradio-dark")  # 阶段 91 前白名单里的"假想成员"
    assert calls == [], f"非法主题名触发了 Hub 拉取：{calls}"


def test_builtin_theme_names_come_from_gradio_runtime():
    """白名单必须是 Gradio 运行时注册表的投影，不是硬编码副本。"""
    from gradio.utils import BUILT_IN_THEMES

    expected = tuple(sorted(str(k).lower() for k in BUILT_IN_THEMES))
    assert theme_mod.builtin_theme_names() == expected


def test_resolve_theme_name_accepts_every_runtime_theme():
    """运行时列出的每个主题名都必须被接受（不被假白名单误拒）。"""
    names = theme_mod.builtin_theme_names()
    if not names:
        pytest.skip("Gradio 运行时主题注册表不可读")
    for n in names:
        assert theme_mod.resolve_theme_name(n) == n, f"合法主题 {n} 被误拒"
        assert theme_mod.resolve_theme_name(n.upper()) == n
    # 至少存在一个"阶段 91 前的白名单里没有"的真实主题，证明假白名单确实会误拒。
    assert {"glass", "ocean", "citrus", "origin", "mario"} & set(names)


def test_brand_sentinel_never_leaks_old_whitelist_name():
    """resolve 对任意输入都不得返回阶段 91 前白名单里的虚构名。"""
    for bogus in ("gradio-dark", "huggingface-not-real"):
        assert theme_mod.resolve_theme_name(bogus) == theme_mod.BRAND_THEME_NAME


def test_hardcoded_theme_whitelist_is_gone():
    """死代码白名单必须被删除（保留即等于"看起来有守卫"）。"""
    assert not hasattr(app_mod, "_VALID_THEMES")
    assert hasattr(app_mod, "_resolve_theme"), "_resolve_theme 应保留并被真正调用"


# =========================================================================== #
# B1：品牌令牌单一真值来源（哨兵）
# =========================================================================== #

def test_theme_module_exposes_no_duplicate_color_constants():
    """ui/theme.py 不得再自持一份颜色常量（唯一来源是 utils.brand）。"""
    for stale in ("PRIMARY_COLOR", "PRIMARY_HOVER", "ACCENT_COLOR", "PAGE_BG"):
        assert not hasattr(theme_mod, stale), f"ui/theme.py 仍持有副本常量 {stale}"


def test_app_css_reads_brand_tokens_at_call_time(sentinel_brand):
    css = theme_mod.build_app_css()
    assert SENTINEL_PRIMARY_HEX in css, "CSS 未使用主色哨兵"
    assert SENTINEL_ACCENT_HEX in css, "CSS 未使用辅色哨兵"
    assert f"linear-gradient(90deg, {SENTINEL_PRIMARY_HEX}, {SENTINEL_ACCENT_HEX})" in css
    assert brand.rgba(_sentinel_rgb(SENTINEL_PRIMARY_HEX), 0.35) in css
    for lit in LEGACY_LITERALS:
        assert lit not in css, f"CSS 残留旧字面量 {lit}"


def test_app_css_constant_matches_builder():
    """模块常量 APP_CSS 与 build_app_css() 必须一致（防止有人绕过 builder）。"""
    assert theme_mod.APP_CSS == theme_mod.build_app_css()


def test_custom_js_reads_brand_tokens_at_call_time(sentinel_brand):
    js = app_mod._build_custom_js()
    assert SENTINEL_PRIMARY_HEX in js, "注入 JS 未使用主色哨兵"
    assert SENTINEL_HOVER_HEX in js, "注入 JS 未使用 hover 哨兵"
    assert brand.rgba(_sentinel_rgb(SENTINEL_PRIMARY_HEX), 0.12) in js
    assert brand.rgba(_sentinel_rgb(SENTINEL_PRIMARY_HEX), 0.35) in js
    assert "__OVERLAY_CSS__" not in js, "占位符未被替换"
    for lit in LEGACY_LITERALS:
        assert lit not in js, f"注入 JS 残留旧字面量 {lit}"


def _iter_strings(obj):
    """递归收集嵌套结构里的所有字符串（Theme.to_dict() 含 LocalFont 等非 JSON 对象）。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _iter_strings(v)


def test_theme_object_reads_brand_tokens_at_call_time(sentinel_brand):
    """被 `set()` 覆盖的主题键必须来自哨兵。

    注意：不能对整棵主题字典做"不得出现旧字面量"的扫描——`Theme.to_dict()`
    里包含 `primary_hue=blue` / `secondary_hue=sky` 的**调色板刻度**，而
    Gradio 的 blue-600 / blue-700 / sky-500 与阶段 6 选定的品牌色**恰好同值**
    （这正是当年选择它们的原因）。因此只断言"被覆盖的键"由哨兵驱动。
    """
    th = theme_mod.build_app_theme()
    d = th.to_dict()

    def _flat(obj, out):
        if isinstance(obj, dict):
            for k, v in obj.items():
                out[k] = v
                _flat(v, out)
        return out

    flat = _flat(d, {})
    assert flat.get("button_primary_background_fill") == SENTINEL_PRIMARY_HEX
    assert flat.get("button_primary_background_fill_hover") == SENTINEL_HOVER_HEX
    assert flat.get("slider_color") == SENTINEL_PRIMARY_HEX
    assert flat.get("link_text_color") == SENTINEL_ACCENT_HEX
    assert flat.get("body_background_fill") == brand.PAGE_BG_HEX
    assert flat.get("block_background_fill_dark") == SENTINEL_INK_HEX
    blob = "\n".join(_iter_strings(d))
    assert SENTINEL_PRIMARY_HEX in blob, "哨兵主色未进入序列化主题"
    assert SENTINEL_ACCENT_HEX in blob, "哨兵辅色未进入序列化主题"


def test_launch_default_css_equals_app_css(monkeypatch):
    kw = _capture_launch(monkeypatch, "brand")
    assert kw["css"] == theme_mod.APP_CSS


def test_export_compare_html_reads_brand_tokens(sentinel_brand):
    html = _es.make_before_after_html(_black(), _with_box())
    assert brand.rgba(_sentinel_rgb(SENTINEL_PRIMARY_HEX), 0.75) in html
    assert brand.rgba(_sentinel_rgb(SENTINEL_INK_HEX), 0.75) in html
    for lit in ("37,99,235", "30,41,59"):
        assert lit not in html, f"对比视图 HTML 残留旧字面量 {lit}"


def test_export_compare_text_reads_brand_tokens(sentinel_brand):
    """After 文字像素必须是哨兵色，且不得残留旧蓝。"""
    arr = _es.make_compare_array(_black(), _with_box(), label=True)
    assert arr.shape[2] == 3
    assert _count_color(arr, _sentinel_rgb(SENTINEL_PRIMARY_HEX)) > 0, \
        "After 文字未使用品牌主色哨兵"
    assert _count_color(arr, brand.hex_to_rgb("#2563eb")) == 0, "残留旧品牌蓝"


def test_contact_sheet_default_bg_is_brand_ink():
    import inspect

    sig = inspect.signature(_es.make_contact_sheet)
    assert sig.parameters["bg"].default == brand.hex_to_rgb(brand.INK_HEX)


def test_no_brand_literals_left_in_ui_and_export():
    """结构性护栏：ui/ 与 export_service 内不得再出现品牌色字面量。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [os.path.join(root, "ui", f) for f in sorted(os.listdir(os.path.join(root, "ui")))
               if f.endswith(".py")]
    targets.append(os.path.join(root, "services", "export_service.py"))
    offenders = {}
    for path in targets:
        text = open(path, encoding="utf-8").read()
        hit = [lit for lit in LEGACY_LITERALS if lit in text]
        if hit:
            offenders[os.path.basename(path)] = hit
    assert not offenders, f"品牌色字面量回流：{offenders}"


def test_brand_token_table_covers_all_public_tokens():
    """新增令牌必须登记进 HEX_TOKENS（否则守卫会漏掉它）。"""
    assert set(brand.HEX_TOKENS) == {
        "PRIMARY_HEX", "PRIMARY_HOVER_HEX", "ACCENT_HEX",
        "PAGE_BG_HEX", "INK_HEX", "SURFACE_HEX",
    }
    for name, value in brand.HEX_TOKENS.items():
        assert brand.hex_to_rgb(value) is not None, f"令牌 {name} 不是合法颜色"


def test_brand_hex_to_rgb_rejects_garbage():
    for bad in ("#12345", "#gggggg", "", "not-a-color"):
        with pytest.raises(ValueError):
            brand.hex_to_rgb(bad)


# =========================================================================== #
# U3：build_app() 必须清空 i18n 运行时注册表（不线性累积）
# =========================================================================== #

def test_build_app_resets_i18n_registry():
    i18n_rt.clear_registry()
    try:
        app1 = app_mod.build_app()
        n1 = len(i18n_rt.registered_components())
        assert n1 > 0, "首次构建后注册表为空（i18n 组件未注册？）"

        app2 = app_mod.build_app()
        n2 = len(i18n_rt.registered_components())
        assert n2 == n1, f"注册表线性累积：{n1} -> {n2}"

        # 注册表里的组件必须全部属于最近一次构建的 Blocks。
        owner2 = {id(b) for b in app2.blocks.values()}
        stray = [c for c in i18n_rt.registered_components() if id(c) not in owner2]
        assert not stray, f"注册表残留上一次构建的废弃组件 {len(stray)} 个"

        # apply_language 的返回长度必须与 outputs 长度一致（构造契约）。
        updates = i18n_rt.apply_language("en-US")
        assert len(updates) == len(i18n_rt.registered_components())
        i18n_rt.apply_language("zh-CN")
        assert app1 is not None
    finally:
        i18n_rt.clear_registry()


# =========================================================================== #
# U4：标签标题映射走 i18n 守卫 + elem_id 对账
# =========================================================================== #

def test_t_in_shares_guard_with_t():
    assert i18n.t_in("zh-CN", "no.such.key.91") == "no.such.key.91"
    assert i18n.t_in("xx-XX", "app.tab.single") == i18n.t_in("zh-CN", "app.tab.single")
    assert i18n.t_in("en-US", "app.tab.single") != i18n.t_in("zh-CN", "app.tab.single")


def test_tab_rename_map_uses_guarded_lookup():
    """每个键都必须真实解析（t_in 的降级是静默的，测试必须把它变成硬失败）。"""
    data = json.loads(app_mod._tab_rename_map_json())
    for tid in app_mod._MAIN_TAB_IDS:
        key = f"app.tab.{tid}"
        assert i18n.t_in("zh-CN", key) != key, f"缺少中文键 {key}"
        assert i18n.t_in("en-US", key) != key, f"缺少英文键 {key}"
        assert data[tid]["zh-CN"] == i18n.t_in("zh-CN", key)
        assert data[tid]["en-US"] == i18n.t_in("en-US", key)
    for sub in app_mod._MEDIA_SUB_TAB_IDS:
        key = f"media.{sub}.tab"
        assert i18n.t_in("zh-CN", key) != key, f"缺少中文键 {key}"
        assert data[f"media_{sub}"]["en-US"] == i18n.t_in("en-US", key)


def test_tab_rename_map_degrades_when_key_missing(monkeypatch):
    """标签键缺失时必须降级为键名，不得在界面构建期抛 KeyError。

    这是 U4 的哨兵测试：修复前直接下标 `TRANSLATIONS[lang][...][tid]`，
    键一旦被改名就会抛 KeyError 把整个 build_app() 拖死；修复后走 t_in，
    与 t() 共用「缺失 → 回退 zh-CN → 回退键名」的守卫。
    """
    stripped = json.loads(json.dumps(i18n.TRANSLATIONS))  # 深层拷贝，不动全局
    for lang in stripped:
        stripped[lang].get("app", {}).get("tab", {}).pop("single", None)
        stripped[lang].get("media", {}).get("pdf", {}).pop("tab", None)
    monkeypatch.setattr(i18n, "TRANSLATIONS", stripped)

    data = json.loads(app_mod._tab_rename_map_json())  # 不得抛 KeyError
    assert data["single"]["zh-CN"] == "app.tab.single"
    assert data["single"]["en-US"] == "app.tab.single"
    assert data["media_pdf"]["zh-CN"] == "media.pdf.tab"
    # 未缺失的键仍应正常取到译文。
    assert data["auto"]["zh-CN"] == stripped["zh-CN"]["app"]["tab"]["auto"]


def test_main_tab_ids_match_real_tabitem_elem_ids():
    """_MAIN_TAB_IDS 必须与 ui/app.py 里真实的 TabItem elem_id 集合一致。"""
    src = open(app_mod.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    elem_ids = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "TabItem":
            for kw in node.keywords:
                if kw.arg == "elem_id" and isinstance(kw.value, ast.Constant):
                    elem_ids.append(kw.value.value)
    assert set(elem_ids) == set(app_mod._MAIN_TAB_IDS), (
        f"TabItem elem_id={sorted(set(elem_ids))} 与 _MAIN_TAB_IDS="
        f"{sorted(app_mod._MAIN_TAB_IDS)} 不一致（新增/改名标签页后必须同步）"
    )
