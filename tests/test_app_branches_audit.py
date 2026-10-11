# -*- coding: utf-8 -*-
"""阶段 290 续审守卫：ui/app.py 首轮（阶段 91）遗漏的分支不变量。

首轮 `tests/test_ui_audit91.py`（424 行）已经把**四个真缺陷**钉死了：
  A1 `_resolve_theme()` 死代码（config.SERVER_THEME 名存实亡）
  A2 `_VALID_THEMES` 假守卫（拒绝真实主题 / 放行虚构主题，且非法值会联网）
  B1 品牌色 6 处字面量跨 3 文件
  U3 `build_app()` 不清 i18n 注册表（实测 288 → 576 线性累积）
  U4 `_tab_rename_map_json()` 绕过 t_in 守卫会抛 KeyError

因此本轮**补的是首轮未机控的分支不变量**——它们全都不在首轮断言范围内，
改坏时 `test_ui_audit91.py` 依然全绿：

  A. `_find_free_port` 五条语义（首轮完全不测）：
     0.0.0.0/""/None → 127.0.0.1 探测 · 空闲即返回起始端口 ·
     全部占用 → None · 跳过占用端口 · max_try<=0 退化输入
  B. `launch_app` 全部返回码分支（首轮只测了 rc==0 这一条）：
     自动端口探测失败=1 · OSError=1 · Exception=1 · KeyboardInterrupt=0 ·
     SERVER_SHOW_API 决定 footer_links 是否含 "api"
  C. 主题派发（首轮只验 brand/builtin，**未验 288 新增的最深层兜底**）：
     `build_app_theme()` 双层 except 可能返回字符串 "soft"，该字符串经
     launch_app 派发后仍必须是 Gradio 可接受的内置主题名（288×290 跨模块不变量）。
  D. 超分控件注册表（**同源 U3 的兄弟，首轮零守卫**）：阶段 96 给 build_app()
     加了 `clear_superres_widgets()`，理由与 U3 完全相同（重复构建线性累积，
     使设置页「应用真实超分」指向已废弃组件），但从未有任何用例盯过它。
  E. JS 产物完整性：`_overlay_css()` 被塞进 `style.textContent = '__OVERLAY_CSS__';`
     —— 一旦它含单引号或换行，整段注入 JS 会在浏览器里语法错误（静默失效）。
  F. 注入 JS 的 config 开关真的翻转 DRAG_ENABLED / SHORTCUT_ENABLED。
  G. `_on_lang` 返回形状 = len(registered_components()) + 1（更新 + 状态条），
     且未知语言降级不崩。

守卫策略（沿用项目经验 263「不得测复刻品」+ 264「须指真实调用点」）：
  · 端口探测用**假 socket** 拦截 connect_ex 的地址与返回码，不真开端口；
  · launch 用**鸭子类型替身**捕获实参，绝不真起服务；
  · C 组刻意 monkeypatch **`app_mod.build_app_theme`**（app.py 是 from-import，
    真实调用点绑定在 app 模块命名空间），并断言「补丁确实生效」——
    否则守卫会测一个根本没走到的分支而假绿。
"""

import io
import json
import os

import pytest

import config
from ui import app as app_mod
from ui import components as comp
from ui import i18n_rt
from ui import theme as theme_mod
from utils import brand
from utils import i18n


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
class _FakeApp:
    """鸭子类型替身：只记录 launch() 收到的实参，绝不真的起服务。"""

    def __init__(self, exc=None):
        self.exc = exc
        self.kwargs = None

    def launch(self, **kwargs):
        self.kwargs = kwargs
        if self.exc is not None:
            raise self.exc


def _update_value(x):
    """兼容两种 gr.update 产物：旧版 Update 对象 / Gradio 6.x 的 dict。

    实测（gradio 6.22）：`gr.update(value='x')` 返回
    `{'value': 'x', '__type__': 'update'}`——是**普通 dict**，不是对象。
    """
    if isinstance(x, dict):
        return x.get("value")
    return getattr(x, "value", None)


def _is_update(x):
    if isinstance(x, dict):
        return x.get("__type__") == "update"
    return type(x).__name__ == "Update"


@pytest.fixture(autouse=True)
def _isolate_language_state():
    """**每个用例前后**还原语言状态（进程内 _LANG + 持久化文件）。

    起因（本轮实测踩到，非假想）：`_on_lang()` → `apply_language()` 内部会
    `set_language()` **并 `save_language()`**——即它同时改**进程全局**与
    **磁盘状态文件** `cache/i18n_state.json`。而 `load_language()` 的优先级是
    环境变量 > 持久化文件 > config > 默认，于是持久化文件里的 `en-US`
    会**在后续每一个全新 pytest 进程里生效**，压过 `config.UI_LANGUAGE=zh-CN`。

    实测后果：某次探针跑过 `_on_lang("en-US")` 后，隔离运行
    test_exceptions_i18n / test_services_audit88 / test_tab_auto / test_tab_batch
    共 14 例「期望中文」的全红（实际拿到英文），把持久化文件改回 zh-CN 后
    144 例全绿。⇒ 语言是**跨文件、跨进程**的共享全局状态，夹具不还原就是在
    制造和他人的连带损伤（经验 246：泄漏根因常在夹具）。
    """
    from utils import i18n as _i18n

    state_path = _i18n._state_path()
    existed = os.path.exists(state_path)
    prev_file = None
    if existed:
        with io.open(state_path, "r", encoding="utf-8", newline="") as fh:
            prev_file = fh.read()
    prev_lang = _i18n._LANG
    try:
        yield
    finally:
        _i18n._LANG = prev_lang
        if existed:
            with io.open(state_path, "w", encoding="utf-8", newline="") as fh:
                fh.write(prev_file)
        elif os.path.exists(state_path):
            os.remove(state_path)


@pytest.fixture
def launch_env(monkeypatch):
    """把 launch_app 依赖的 config 项全部固定成确定值（monkeypatch 自动还原）。"""
    monkeypatch.setattr(config, "SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SERVER_PORT", 7860)
    monkeypatch.setattr(config, "SERVER_PORT_MAX_TRY", 5)
    monkeypatch.setattr(config, "SERVER_AUTO_PORT", False)
    monkeypatch.setattr(config, "SERVER_SHARE", False)
    monkeypatch.setattr(config, "SERVER_OPEN_BROWSER", False)
    monkeypatch.setattr(config, "SERVER_MAX_THREADS", 4)
    monkeypatch.setattr(config, "SERVER_SHOW_API", False)
    monkeypatch.setattr(config, "SERVER_THEME", "brand")
    return config


@pytest.fixture
def free_port_probe(monkeypatch):
    """假 socket：拦截 _find_free_port 的 connect_ex（地址 + 返回码）。

    返回一个 setter：`probe(rc_list)` 设定后续 connect_ex 的返回值序列
    （0 = 端口被占用，非 0 = 空闲，与 POSIX 语义一致）。
    """

    class _FakeSock:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, t):
            pass

        def connect_ex(self, addr):
            state["calls"].append(addr)
            return state["rc"].pop(0) if state["rc"] else 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    state = {"calls": [], "rc": []}
    monkeypatch.setattr(app_mod._socket, "socket", _FakeSock)

    def _set(rc):
        state["calls"] = []
        state["rc"] = list(rc)

    _set([])
    _set_state = _set
    _set_state.state = state
    return _set_state


@pytest.fixture(scope="module")
def double_build():
    """连续构建两次 app，返回关键注册表计数（供 D 组断言不变量）。

    只构建一次（module 级）——build_app() 会连带加载插件/探测设备，代价高，
    而 D 组要的只是「两次构建的注册表规模相等」这一个量。
    """
    i18n_rt.clear_registry()
    comp.clear_superres_widgets()
    try:
        app_mod.build_app()
        first = {
            "i18n": len(i18n_rt.registered_components()),
            "superres": len(comp.get_superres_widgets()),
            "superres_keys": [sorted(e.keys()) for e in comp.get_superres_widgets()],
        }
        app_mod.build_app()
        second = {
            "i18n": len(i18n_rt.registered_components()),
            "superres": len(comp.get_superres_widgets()),
        }
        yield {"first": first, "second": second}
    finally:
        i18n_rt.clear_registry()
        comp.clear_superres_widgets()


# =========================================================================== #
# A. _find_free_port —— 首轮完全不测的五条语义
# =========================================================================== #

@pytest.mark.parametrize("host", ["0.0.0.0", "", None])
def test_find_free_port_probes_loopback_for_wildcard_host(free_port_probe, host):
    """0.0.0.0 / "" / None 必须改用 127.0.0.1 探测。

    否则在「监听 0.0.0.0」的配置下，探测会打到 0.0.0.0 本身，
    connect_ex 语义与可用性都不可靠，可能误判端口被占用。
    """
    free_port_probe([1])
    app_mod._find_free_port(host, 7860, 3)
    assert free_port_probe.state["calls"], "根本没有发起探测"
    assert free_port_probe.state["calls"][0][0] == "127.0.0.1"


def test_find_free_port_returns_start_when_free(free_port_probe):
    free_port_probe([1, 1, 1])
    assert app_mod._find_free_port("127.0.0.1", 7860, 5) == 7860


def test_find_free_port_returns_none_when_all_occupied(free_port_probe):
    """全部占用必须返回 None（launch_app 据此返回 1），不得抛异常。"""
    free_port_probe([0] * 5)
    assert app_mod._find_free_port("127.0.0.1", 7860, 5) is None


def test_find_free_port_skips_occupied_offsets(free_port_probe):
    free_port_probe([0, 0, 1])
    assert app_mod._find_free_port("127.0.0.1", 7860, 5) == 7862


@pytest.mark.parametrize("max_try", [0, -3])
def test_find_free_port_degenerate_max_try_returns_none(free_port_probe, max_try):
    """max_try<=0 是退化输入：range() 为空 → None，不得崩。"""
    assert app_mod._find_free_port("127.0.0.1", 7860, max_try) is None


# =========================================================================== #
# B. launch_app 返回码分支 —— 首轮只测了 rc==0
# =========================================================================== #

def test_launch_app_fixed_port_success_returns_zero(launch_env):
    fake = _FakeApp()
    assert app_mod.launch_app(fake) == 0
    assert fake.kwargs is not None, "launch_app 未调用 app.launch()"


def test_launch_app_returns_one_when_auto_port_exhausted(launch_env, monkeypatch):
    """自动端口探测失败必须 return 1，且**不调用** app.launch()。"""
    launch_env.SERVER_AUTO_PORT = True
    monkeypatch.setattr(app_mod, "_find_free_port", lambda *a, **k: None)
    fake = _FakeApp()
    assert app_mod.launch_app(fake) == 1
    assert fake.kwargs is None, "端口都没找到还去启动服务了"


@pytest.mark.parametrize("exc", [OSError("port in use"), RuntimeError("boom")])
def test_launch_app_returns_one_on_launch_failure(launch_env, exc):
    assert app_mod.launch_app(_FakeApp(exc=exc)) == 1


def test_launch_app_returns_zero_on_keyboard_interrupt(launch_env):
    """Ctrl+C 是正常退出路径（用户主动停止），必须返回 0 而非失败码。"""
    assert app_mod.launch_app(_FakeApp(exc=KeyboardInterrupt())) == 0


def test_launch_app_uses_detected_port_when_config_port_busy(launch_env, monkeypatch):
    """探测到的端口与配置端口不同时，必须把**探测结果**传给 launch。"""
    launch_env.SERVER_AUTO_PORT = True
    monkeypatch.setattr(app_mod, "_find_free_port", lambda *a, **k: 7999)
    fake = _FakeApp()
    assert app_mod.launch_app(fake) == 0
    assert fake.kwargs["server_port"] == 7999


@pytest.mark.parametrize("show_api,expect_api", [(True, True), (False, False)])
def test_show_api_flag_controls_footer_links(launch_env, show_api, expect_api):
    """SERVER_SHOW_API 决定页脚是否挂 Gradio 的 API 文档入口。"""
    launch_env.SERVER_SHOW_API = show_api
    fake = _FakeApp()
    app_mod.launch_app(fake)
    links = fake.kwargs["footer_links"]
    assert ("api" in links) is expect_api
    assert {"gradio", "settings"} <= set(links), "基础页脚链接丢失"


# =========================================================================== #
# C. 主题派发 —— 含 288 新增的「最深层兜底」跨模块不变量
# =========================================================================== #

def test_builtin_theme_name_is_passed_as_string(launch_env):
    launch_env.SERVER_THEME = "monochrome"
    fake = _FakeApp()
    app_mod.launch_app(fake)
    assert fake.kwargs["theme"] == "monochrome"


def test_brand_theme_deep_fallback_string_is_still_launch_safe(launch_env, monkeypatch):
    """288 的最深层兜底：`build_app_theme()` 连 `gr.themes.Soft()` 都构造不出时
    返回字符串 "soft"。该字符串经 launch_app 派发后**仍必须是合法内置主题**，
    否则品牌主题这条路径会以非法主题名进入 Gradio（288 与 290 的跨模块契约）。

    补丁打在 `app_mod.build_app_theme` ——app.py 是 from-import，真实调用点
    绑定在 app 模块命名空间；打 theme_mod 上不生效（经验 264）。
    """
    monkeypatch.setattr(app_mod, "build_app_theme", lambda: "soft")
    monkeypatch.setattr(app_mod, "resolve_theme_name", lambda v: theme_mod.BRAND_THEME_NAME)
    names = theme_mod.builtin_theme_names()
    if not names:
        pytest.skip("Gradio 运行时主题注册表不可读")
    fake = _FakeApp()
    rc = app_mod.launch_app(fake)
    theme_arg = fake.kwargs["theme"]
    # 先证明补丁真的命中了执行路径（否则本断言是假绿）。
    assert theme_arg == "soft", f"补丁未生效，实际拿到 {theme_arg!r}——守卫测错了对象"
    assert theme_arg in names, "288 的兜底字符串不是合法内置主题，会让品牌主题路径失效"
    assert rc == 0


def test_resolve_theme_is_single_consumer_of_config(launch_env):
    """`_resolve_theme()` 必须是 config.SERVER_THEME 的唯一消费入口（阶段 91 的 A1）。

    回归护栏：一旦有人绕过它直接读 config.SERVER_THEME，A1 的修复就名存实亡。

    必须按 **AST 实际属性访问**判定，不能对源码做子串匹配——launch_app 的
    注释里本就写着 "config.SERVER_THEME 取 brand 时…"（经验 250：匹配前先剥注释）。
    """
    import ast
    import inspect
    import textwrap

    launch_env.SERVER_THEME = "ocean"
    assert app_mod._resolve_theme() == theme_mod.resolve_theme_name("ocean")

    tree = ast.parse(textwrap.dedent(inspect.getsource(app_mod.launch_app)))
    direct_reads = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "config"
        and node.attr == "SERVER_THEME"
    ]
    assert not direct_reads, (
        "launch_app 直接读 config.SERVER_THEME，绕过了 _resolve_theme() 守卫"
    )
    # 且它确实经由 _resolve_theme() 取值（正向确认，不只靠「没找到坏味道」）。
    assert "_resolve_theme()" in ast.unparse(tree)


# =========================================================================== #
# D. 超分控件注册表 —— 同源 U3 的兄弟，首轮零守卫
# =========================================================================== #

def test_build_app_does_not_accumulate_superres_widgets(double_build):
    """阶段 96 给 build_app() 加了 clear_superres_widgets()，理由与 U3 相同：

    重复构建会让注册表线性累积，使设置页「应用真实超分」指向**已废弃的
    Blocks 的组件**。U3（i18n 注册表）首轮有守卫，这条同源兄弟却一个用例都没有。
    """
    assert double_build["first"]["superres"] > 0, "首次构建未登记任何超分控件"
    assert double_build["second"]["superres"] == double_build["first"]["superres"], (
        f"超分控件注册表线性累积：{double_build['first']['superres']} -> "
        f"{double_build['second']['superres']}"
    )


def test_superres_widget_entries_keep_three_keys(double_build):
    """每组必须是 {enabled, scale, backend}——设置页按这三个键回写。"""
    for keys in double_build["first"]["superres_keys"]:
        assert keys == ["backend", "enabled", "scale"], f"超分控件键集漂移：{keys}"


def test_get_superres_widgets_returns_copy():
    """必须返回副本：调用方若就地修改会污染注册表。"""
    comp.clear_superres_widgets()
    try:
        comp.register_superres_widget("e", "s", "b")
        snap = comp.get_superres_widgets()
        snap.clear()
        assert len(comp.get_superres_widgets()) == 1, "get_superres_widgets() 返回的是活表"
    finally:
        comp.clear_superres_widgets()


def test_build_app_actually_calls_both_registry_clears(monkeypatch):
    """结构性守卫：build_app() 必须同时清 i18n 与超分两个注册表。

    U3 只被「结果」盯住（计数不变），若有人把 clear_superres_widgets()
    挪走、而恰好没有组件登记超分控件，上面的计数断言会假绿——这里钉调用点。
    """
    calls = []
    orig_csw = app_mod.clear_superres_widgets
    orig_cr = app_mod.clear_registry
    monkeypatch.setattr(app_mod, "clear_superres_widgets", lambda: (calls.append("superres"), orig_csw())[1])
    monkeypatch.setattr(app_mod, "clear_registry", lambda: (calls.append("i18n"), orig_cr())[1])
    app_mod.build_app()
    assert "superres" in calls, "build_app() 未调用 clear_superres_widgets()"
    assert "i18n" in calls, "build_app() 未调用 clear_registry()"
    i18n_rt.clear_registry()
    comp.clear_superres_widgets()


# =========================================================================== #
# E. 注入 JS 产物完整性
# =========================================================================== #

def test_overlay_css_cannot_break_js_string_literal():
    """`_overlay_css()` 被塞进 `style.textContent = '__OVERLAY_CSS__';`。

    它一旦含**单引号或换行**，整段注入 JS 在浏览器里就是语法错误——
    拖拽浮层样式静默失效，且后端测试全绿（无任何自动化会失败）。
    """
    css = app_mod._overlay_css()
    assert "'" not in css, "overlay CSS 含单引号，会截断注入 JS 的字符串字面量"
    assert "\n" not in css and "\r" not in css, "overlay CSS 含换行，会截断注入 JS 的字符串字面量"


def test_custom_js_has_no_unsubstituted_placeholder():
    """所有占位符必须被替换干净——残留即前端拿到字面量 '__DRAG__'。"""
    js = app_mod._build_custom_js()
    for ph in ("__OVERLAY_CSS__", "__DRAG__", "__SHORTCUT__"):
        assert ph not in js, f"占位符 {ph} 未被替换"


# =========================================================================== #
# F. config 开关真的翻转注入的 JS
# =========================================================================== #

def test_dragdrop_switch_flips_drag_enabled(monkeypatch):
    monkeypatch.setattr(config, "UI_GLOBAL_DRAGDROP", True)
    assert "const DRAG_ENABLED = true;" in app_mod._build_custom_js()
    monkeypatch.setattr(config, "UI_GLOBAL_DRAGDROP", False)
    assert "const DRAG_ENABLED = false;" in app_mod._build_custom_js()


def test_shortcut_switch_flips_shortcut_enabled(monkeypatch):
    monkeypatch.setattr(config, "UI_SHORTCUTS_ENABLED", True)
    assert "const SHORTCUT_ENABLED = true;" in app_mod._build_custom_js()
    monkeypatch.setattr(config, "UI_SHORTCUTS_ENABLED", False)
    assert "const SHORTCUT_ENABLED = false;" in app_mod._build_custom_js()


def test_drag_and_shortcut_switches_are_independent(monkeypatch):
    """两个开关互不牵连（曾经耦合会让人只想关一个功能却关掉两个）。"""
    monkeypatch.setattr(config, "UI_GLOBAL_DRAGDROP", True)
    monkeypatch.setattr(config, "UI_SHORTCUTS_ENABLED", False)
    js = app_mod._build_custom_js()
    assert "const DRAG_ENABLED = true;" in js
    assert "const SHORTCUT_ENABLED = false;" in js


def test_injected_js_targets_match_real_component_elem_ids():
    """注入 JS 引用的 elem_id 必须真实存在——否则快捷键/拖拽静默失效。

    沿用项目经验「单一真值来源要用哨兵」：这里从各 tab 源码里 AST 抽出真实
    elem_id，与 JS 中的候选目标/按钮 id 取交集断言。
    """
    import ast
    import os
    import re

    js = app_mod._build_custom_js()
    referenced = set(re.findall(r"'(single_editor|auto_upload|batch_files|batch_folder|"
                                r"media_pdf|media_video|single_run|auto_detect|auto_repair|"
                                r"batch_run|batch_cancel|history_refresh|history_clear)'", js))

    ui_dir = os.path.dirname(os.path.abspath(app_mod.__file__))
    declared = set()
    for fn in sorted(os.listdir(ui_dir)):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(ui_dir, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                kw = getattr(node, "keywords", [])
                for k in kw:
                    if k.arg == "elem_id" and isinstance(k.value, ast.Constant) \
                            and isinstance(k.value.value, str):
                        declared.add(k.value.value)

    missing = referenced - declared
    assert not missing, (
        f"注入 JS 引用了不存在的 elem_id {sorted(missing)}——"
        f"拖拽/快捷键会静默失效（新增或改名控件后必须同步）"
    )


# =========================================================================== #
# G. _on_lang 返回形状
# =========================================================================== #

def test_on_lang_returns_updates_plus_status():
    """返回长度必须 = len(registered_components()) + 1（末尾追加语言状态条）。

    少一个 → Gradio 报 outputs 数量不匹配；多一个 → 同上。这是事件契约。
    """
    i18n_rt.clear_registry()
    try:
        i18n_rt.register(object(), lambda: {"value": "x"})
        res = app_mod._on_lang("en-US")
        assert len(res) == len(i18n_rt.registered_components()) + 1
        assert _is_update(res[-1]), "末位必须是语言状态条的 gr.update"
        expected = i18n.t("lang.applied", name=i18n.t("lang.en"))
        assert _update_value(res[-1]) == expected
    finally:
        i18n_rt.clear_registry()


def test_on_lang_unknown_language_degrades_gracefully():
    """未知语言码不得抛异常（Gradio 在某些路径会回传空串/旧值）。"""
    i18n_rt.clear_registry()
    try:
        i18n_rt.register(object(), lambda: {"value": "x"})
        res = app_mod._on_lang("fr-FR")
        assert len(res) == len(i18n_rt.registered_components()) + 1
    finally:
        i18n_rt.clear_registry()


def test_compliance_brief_tracks_translation_table():
    """合规声明必须实时取 t()（随语言切换刷新），不得冻结成字面量。"""
    assert app_mod._compliance_brief() == i18n.t("app.compliance")
    assert app_mod._compliance_brief().strip(), "合规声明为空"


# =========================================================================== #
# H. 标签重命名映射的 JS 侧产物
# =========================================================================== #

def test_lang_js_embeds_every_main_tab_id():
    js = app_mod._build_lang_js()
    for tid in app_mod._MAIN_TAB_IDS:
        assert f'"{tid}"' in js, f"注入 JS 的 MAP 缺少页签 {tid}"


def test_tab_rename_map_keys_are_complete_and_wellformed():
    data = json.loads(app_mod._tab_rename_map_json())
    expected = set(app_mod._MAIN_TAB_IDS) | {f"media_{s}" for s in app_mod._MEDIA_SUB_TAB_IDS}
    assert set(data) == expected
    for tid, entry in data.items():
        assert set(entry) == {"zh-CN", "en-US"}, f"{tid} 缺语言分支"
        for lang, val in entry.items():
            assert isinstance(val, str) and val, f"{tid}/{lang} 译文为空"


def test_tab_rename_map_has_no_script_breakout():
    """映射被内联进 <script>，含 </script> 会提前闭合脚本块（XSS 向量）。"""
    raw = app_mod._tab_rename_map_json()
    assert "</script>" not in raw.lower()
    assert "<script" not in raw.lower()


def test_lang_js_renames_via_button_id():
    """Gradio 6 的 TabItem 不支持运行时改标题，只能改 DOM 按钮文本。

    锁死这条机制：若改成改 TabItem 本身的重命名方式，此守卫会失败并提醒复核。
    """
    js = app_mod._build_lang_js()
    assert "getElementById(id + '-button')" in js
    assert "btn.textContent" in js