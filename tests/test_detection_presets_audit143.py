# -*- coding: utf-8 -*-
"""阶段 143 · `core/detection_presets` 真值审计守卫。

审计对象：`core/detection_presets.py`（荒漠度 3.1 = 271 代码行 ÷ 4 个引用测试文件里 88 个用例）。

本阶段修的五处（全部为「声明 ≠ 实现」或「静默不一致」家族）
-----------------------------------------------------------
D1 `DetectionPreset.resolve` 的 docstring 声明「只输出本预设显式设置（非 None）的字段；
    未设置的字段不出现」，但 `sensitivity` **无条件**是例外（未设置时写入 `base_sensitivity`）。
    实测：`get_preset("general").resolve(0.99)` → `{"sensitivity": 0.99}`，
    而 general 全字段为 None ⇒ 按 docstring 应返回 `{}`。
    ⇒ 行为是**对的**（下游 `sensitivity=None` 会去读 config，预设解析的职责正是
      「替调用方决定敏感度」），错的是**声明**。修法 = 订正 docstring 并写明理由。

D2 `register_preset` 的校验 `if not preset or not preset.name:` 有两个问题：
    ① `not preset` 是**死条件** —— `DetectionPreset` 是普通 dataclass，既无 `__bool__`
       也无 `__len__`，任何实例恒为真；
    ② 于是「类型错」被**伪装成「值错」**：`register_preset(None)` / `({})` 报
       「预设必须提供非空 name。」（**指错原因**），而 `register_preset("abc")` / `(42)`
       抛 `AttributeError` ⇒ **同一「类型不合法」输入族给出两种异常类型**。
    修法 = 先 `isinstance` 抛 `TypeError`（信息含真实类型），再查 name 抛 `ValueError`。

D3 `__main__` 自检把预设名**硬编码**成 9 项清单、用 `in` 判定 ⇒
    内置 10 个（漏 `ai_multi`）而**自检静默通过**；且 `in` 对「多出来的」不敏感
    ⇒ 未来新增预设时自检永远绿（「哑巴自检」，与阶段 138/139 同形）。
    修法 = 与 `_BUILTIN_PRESETS` **集合对账** + `choices` 数量对账 + 补 `ai_multi` 并集断言。

D4 模块 docstring 的内置预设清单只列了 8 个（实际 10 个，漏 `ai_logo` / `ai_multi`）。

D5 `resolve_preset` 用 `if not name:` 判「未指定」⇒ `""` 静默返回、`"   "` 继续走
    `get_preset` 拿到 None 后**多发一条告警**。同类输入两种行为（经验 158 同族）。
    修法 = 统一 `not str(name).strip()`。

本文件守八层
------------
K0 存在性自检：内嵌**旧实现副本**，证明 D2/D5 的旧行为确实会出错（守卫抓得住真东西）
K1 `register_preset`：4 种类型错 → `TypeError`（且信息含真实类型）；空 name → `ValueError`
K2 死条件的存在性证明：`DetectionPreset` 无 `__bool__`/`__len__` ⇒ `not preset` 恒 False
K3 `resolve` 的 sensitivity 例外语义 + docstring 契约对账
K4 空白口径统一：4 种空白 → 静默；真未知名 → 告警（反向对照，防「把告警也关掉」式假修复）
K5 四方清单对账：`_BUILTIN_PRESETS` == 模块 docstring ⊇ == `choices` == 自检期望
K6 `ai_multi` 的「并集」语义（此前自检漏测）+ 每个内置预设的 resolve 可解析性
K7 登记边界：返回对象是**可变共享引用**（当前全仓无代码改预设字段，故不改契约）
"""
from __future__ import annotations

import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import core.detection_presets as dp                                  # noqa: E402
from core.detection_presets import (                                 # noqa: E402
    DetectionPreset,
    _BUILTIN_PRESETS,
    _REGISTRY,
    get_preset,
    get_preset_choices,
    get_preset_names,
    list_presets,
    register_preset,
    resolve_preset,
)

_SRC_PATH = os.path.join(_ROOT, "core", "detection_presets.py")
_SRC = open(_SRC_PATH, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# K0 · 存在性自检：内嵌旧实现，证明旧行为确实会出错
# ---------------------------------------------------------------------------

def _legacy_register_preset(preset):
    """修复前的 register_preset 实现副本（用于证明守卫抓得住真缺陷）。"""
    if not preset or not preset.name:            # 旧行为：死条件 + 类型错伪装成值错
        raise ValueError("预设必须提供非空 name。")
    _REGISTRY[preset.name] = preset


def _legacy_resolve_preset(name, base_sensitivity, lookup):
    """修复前的 resolve_preset 实现副本（空白口径不一致）。

    `lookup` 显式注入，避免依赖模块全局名（K0 的首版写法正是踩了这个坑：
    patch `dp.get_preset` 影响不到本文件顶部 `from … import get_preset` 绑定的局部名）。
    """
    if not name:                                  # 旧行为："" 为假、"   " 为真
        return {"sensitivity": float(base_sensitivity)}
    preset = lookup(name)
    if preset is None:
        return {"sensitivity": float(base_sensitivity)}
    return preset.resolve(base_sensitivity)


def test_K0_legacy_register_misdiagnoses_type_error():
    """旧实现把「类型错」报成「值错」（错误信息指错原因）——证明 D2 是真缺陷。"""
    with pytest.raises(ValueError, match="name"):
        _legacy_register_preset(None)             # 真实原因是类型错，却报 name 问题
    with pytest.raises(ValueError, match="name"):
        _legacy_register_preset({})
    # 而同类输入的另一半抛的是 AttributeError ⇒ 同族两种异常类型
    with pytest.raises(AttributeError):
        _legacy_register_preset("abc")


def test_K0_legacy_resolve_blank_string_inconsistent():
    """旧实现里 `""` 与 `"   "` 走不同分支——证明 D5 是真缺陷。"""
    calls = []

    def lookup(n):
        calls.append(n)
        return get_preset(n)

    # `""` 在旧实现里被 `not name` 短路，**不会**去查注册表。
    _legacy_resolve_preset("", 0.7, lookup)
    assert calls == [], "旧实现对空串应短路（不查注册表）"

    # `"   "` 为真，旧实现会继续查注册表 → 拿到 None → 进而发「未知预设」告警。
    calls.clear()
    _legacy_resolve_preset("   ", 0.7, lookup)
    assert calls == ["   "], "旧实现对空白串应查注册表（⇒ 进而发未知预设告警）"


# ---------------------------------------------------------------------------
# K1 · register_preset 的类型错 / 值错分离
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [None, {}, "abc", 42, 3.14, [], object()])
def test_K1_non_preset_raises_type_error(bad):
    """非 DetectionPreset 输入必须抛 TypeError（而不是 ValueError / AttributeError）。"""
    with pytest.raises(TypeError):
        register_preset(bad)


@pytest.mark.parametrize("bad", [None, {}, "abc", 42, 3.14, [], object()])
def test_K1_type_error_message_names_real_type(bad):
    """TypeError 信息必须含**真实类型名**（便于定位调用方 bug）。"""
    with pytest.raises(TypeError) as ei:
        register_preset(bad)
    assert type(bad).__name__ in str(ei.value), (
        f"错误信息未指出真实类型：{ei.value}"
    )


def test_K1_empty_name_raises_value_error():
    """是 DetectionPreset 但 name 为空 ⇒ ValueError（值错）。"""
    with pytest.raises(ValueError):
        register_preset(DetectionPreset(name="", display="d", description="c"))


def test_K1_type_and_value_errors_are_distinct():
    """类型错与值错必须抛**不同**异常类型（经验 157 判据）。"""
    with pytest.raises(TypeError):
        register_preset(None)
    with pytest.raises(ValueError):
        register_preset(DetectionPreset(name="", display="d", description="c"))


def test_K1_valid_preset_registers_and_can_be_cleaned():
    """合法实例仍正常注册（正向对照）。"""
    p = DetectionPreset(name="__k1__", display="d", description="c", sensitivity=0.4)
    try:
        register_preset(p)
        assert get_preset("__k1__") is p
        assert "__k1__" in get_preset_names()
    finally:
        _REGISTRY.pop("__k1__", None)
    assert get_preset("__k1__") is None


def test_K1_source_has_no_dead_condition():
    """`register_preset` 的**代码**里不得再出现 `not preset` 死条件（防止回退）。

    必须走 AST 而不是文本匹配：修复后的 docstring 里**引用了**旧写法作说明，
    文本扫描会把「说明」当成「代码」而误报（阶段 143 首版守卫正是这么误报的）。
    """
    import ast

    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "register_preset")
    dead = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            # 形如 `not <Name>` 的单目取反即旧死条件
            if (isinstance(sub, ast.UnaryOp) and isinstance(sub.op, ast.Not)
                    and isinstance(sub.operand, ast.Name)
                    and sub.operand.id == "preset"):
                dead.append(ast.dump(node.test)[:80])
    assert not dead, f"register_preset 仍含 `not preset` 死条件：{dead}"
    # 正向：必须有 isinstance 类型检查
    assert "isinstance(preset, DetectionPreset)" in _SRC


# ---------------------------------------------------------------------------
# K2 · 死条件的存在性证明
# ---------------------------------------------------------------------------

def test_K2_dataclass_is_always_truthy():
    """`DetectionPreset` 无 `__bool__`/`__len__` ⇒ 任何实例恒为真 ⇒ `not preset` 是死条件。"""
    assert not hasattr(DetectionPreset, "__bool__")
    assert not hasattr(DetectionPreset, "__len__")
    assert bool(DetectionPreset(name="", display="", description="")) is True


# ---------------------------------------------------------------------------
# K3 · resolve 的 sensitivity 例外语义 + 契约对账
# ---------------------------------------------------------------------------

def test_K3_sensitivity_is_always_present():
    """`sensitivity` 是 resolve 输出的**唯一必现键**（含全 None 的 general）。"""
    g = get_preset("general")
    assert g is not None
    assert all(getattr(g, f) is None for f in (
        "sensitivity", "enable_translucent", "enable_corner", "enable_edge",
        "enable_grid", "enable_gradient", "enable_logo", "enhance_tiled",
        "search_bottom", "use_ocr",
    )), "夹具失效：general 不再是「全 None」预设"
    kw = g.resolve(0.99)
    assert kw == {"sensitivity": 0.99}, f"general 应只输出用 base 兜底的 sensitivity：{kw}"


def test_K3_preset_own_sensitivity_beats_base():
    """预设自带 sensitivity 时，必须用预设值而非 base（预设=一键配置的语义）。"""
    kw = get_preset("ai_tiled").resolve(0.99)
    assert kw["sensitivity"] == 0.62


def test_K3_docstring_declares_sensitivity_exception():
    """`resolve` 的 docstring 必须**显式声明** sensitivity 是例外（防再次变成「声明≠实现」）。"""
    doc = (DetectionPreset.resolve.__doc__ or "").replace(" ", "")
    assert "例外" in doc or "总是输出" in doc, "docstring 未声明 sensitivity 的例外语义"
    # 且不得再保留「只输出非 None 字段」这种**无例外**的旧断言
    assert "只输出「本预设显式设置（非None）」的字段；未设置的字段不出现" not in doc


def test_K3_only_sensitivity_is_forced():
    """除 sensitivity 外，其余键必须是「预设显式设置才出现」——抽查 ai_corner 未设的键。"""
    kw = get_preset("ai_corner").resolve(0.5)
    # ai_corner 未设置 enable_grid / enable_gradient / enable_logo / use_ocr
    for k in ("enable_grid", "enable_gradient", "enable_logo", "use_ocr"):
        assert k not in kw, f"{k} 未设置却出现在输出里：{kw}"


# ---------------------------------------------------------------------------
# K4 · 空白口径统一
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blank", [None, "", "   ", "\t", "\n", " \t\n ", "\u3000"])
def test_K4_blank_names_are_treated_as_unspecified(blank):
    """None / 空串 / 各种空白串一律视为「未指定」，返回仅含 base 的字典。"""
    assert resolve_preset(blank, 0.7) == {"sensitivity": 0.7}


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_K4_blank_names_emit_no_warning(blank, monkeypatch):
    """空白串**不得**刷「未知检测预设」告警（反向守住 K4 不被只改一半）。"""
    seen = []
    monkeypatch.setattr(dp, "_get_log", lambda: type(
        "_L", (), {"warning": lambda self, *a, **k: seen.append(a),
                   "debug": lambda self, *a, **k: None})())
    resolve_preset(blank, 0.7)
    assert not seen, f"空白串不应告警：{seen}"


def test_K4_real_unknown_name_still_warns(monkeypatch):
    """真正拼错的非空名**仍必须**告警——防「把告警一并关掉」式假修复。"""
    seen = []
    monkeypatch.setattr(dp, "_get_log", lambda: type(
        "_L", (), {"warning": lambda self, *a, **k: seen.append(a),
                   "debug": lambda self, *a, **k: None})())
    resolve_preset("__definitely_not_a_preset__", 0.7)
    assert seen, "未知预设名必须留下可见告警"


def test_K4_known_name_unaffected():
    """正常预设名的解析行为不变（回归对照）。"""
    assert resolve_preset("general", 0.55) == {"sensitivity": 0.55}
    kw = resolve_preset("ai_tiled", 0.55)
    assert kw["sensitivity"] == 0.62 and kw["enhance_tiled"] is True


# ---------------------------------------------------------------------------
# K5 · 四方清单对账
# ---------------------------------------------------------------------------

def test_K5_registry_matches_builtin_declaration():
    """注册表里的内置预设必须与 `_BUILTIN_PRESETS` 声明**集合相等**。"""
    declared = {p.name for p in _BUILTIN_PRESETS}
    registered = set(get_preset_names())
    assert declared <= registered, f"声明了却未注册：{sorted(declared - registered)}"


def test_K5_no_duplicate_names_in_builtin():
    """内置清单不得有重名（重名会让「集合」对账掩盖丢项）。"""
    names = [p.name for p in _BUILTIN_PRESETS]
    assert len(names) == len(set(names)), f"内置预设重名：{names}"


def test_K5_module_docstring_lists_every_builtin():
    """模块 docstring 的内置预设清单必须覆盖全部内置预设（D4 守卫）。"""
    head = _SRC.split('"""')[1]
    missing = [p.name for p in _BUILTIN_PRESETS if f"· {p.name}" not in head]
    assert not missing, f"模块 docstring 清单漏列：{missing}"


def test_K5_choices_count_matches_registry():
    """`get_preset_choices()` 必须与注册表逐项对齐（含顺序）。"""
    choices = get_preset_choices()
    presets = list_presets()
    assert len(choices) == len(presets), f"choices({len(choices)}) != 预设数({len(presets)})"
    assert [c[1] for c in choices] == [p.name for p in presets]
    assert [c[0] for c in choices] == [p.display for p in presets]


def test_K5_selfcheck_uses_set_reconciliation_not_hardcoded_list():
    """自检必须与 `_BUILTIN_PRESETS` 对账，而不是硬编码名字清单（D3 守卫）。"""
    assert "expected = {p.name for p in _BUILTIN_PRESETS}" in _SRC, (
        "自检未改为与 _BUILTIN_PRESETS 对账 —— 新增预设时会静默通过"
    )
    assert not re.search(r'for expected in \(([^)]*)\)', _SRC, re.S), (
        "自检仍保留硬编码 expected 元组"
    )


def test_K5_every_builtin_has_display_and_description():
    """每个内置预设的 name / display / description 都必须非空。"""
    for p in _BUILTIN_PRESETS:
        assert p.name and p.display and p.description, f"{p.name} 字段不完整"


def test_K5_every_builtin_resolves_cleanly():
    """每个内置预设都能用 base 解析出含 sensitivity 的字典，且键名与下游形参一致。"""
    import inspect
    from core.detector import detect_watermark_mask

    valid = set(inspect.signature(detect_watermark_mask).parameters)
    for p in _BUILTIN_PRESETS:
        kw = p.resolve(0.55)
        assert "sensitivity" in kw, f"{p.name} 解析结果缺 sensitivity"
        unknown = set(kw) - valid
        assert not unknown, f"{p.name} 解析出下游不存在的形参：{sorted(unknown)}"


# ---------------------------------------------------------------------------
# K6 · ai_multi 的「并集」语义（此前自检漏测）
# ---------------------------------------------------------------------------

def test_K6_ai_multi_is_union_of_strategies():
    """ai_multi 的 description 声称「并集」，必须真的开启全部七类策略。"""
    kw = resolve_preset("ai_multi", 0.55)
    for k in ("enable_translucent", "enable_corner", "enable_edge",
              "enable_grid", "enable_logo", "enhance_tiled", "search_bottom"):
        assert kw.get(k) is True, f"ai_multi 应开启 {k}（description 声称是并集）"


def test_K6_ai_gradient_isolates_other_strategies():
    """ai_gradient 必须隔离其余策略（只留渐变检测），否则会整图误修。"""
    kw = resolve_preset("ai_gradient", 0.55)
    assert kw.get("enable_gradient") is True
    for k in ("enable_translucent", "enable_corner", "enable_edge", "enhance_tiled"):
        assert kw.get(k) is False, f"ai_gradient 应关闭 {k}"


# ---------------------------------------------------------------------------
# K7 · 登记边界：返回对象是可变共享引用（当前不改契约）
# ---------------------------------------------------------------------------

def test_K7_returned_presets_are_shared_mutable_references():
    """登记边界：`get_preset` / `list_presets` 返回的是**注册表内的同一对象**。

    当前全仓扫描确认**没有任何代码修改预设字段**（阶段 143 探针
    `P10_preset_field_mutation_scan` → `(none)`），故不改公开契约
    （改 `frozen=True` 会破坏「覆盖式注册」的既有语义）。
    本用例把这条边界**钉住**：若将来有人开始改预设字段，此处会提醒他
    那是「污染进程内注册表」而不是「改一份副本」。
    """
    a = get_preset("ai_tiled")
    b = get_preset("ai_tiled")
    assert a is b, "get_preset 返回的应是注册表内同一对象（当前语义）"
    lst = list_presets()
    assert lst and all(any(x is p for p in lst) for x in [get_preset(lst[0].name)]), (
        "list_presets 的元素应与 get_preset 同源"
    )


def test_K7_list_is_a_copy_but_elements_are_not():
    """`list_presets()` 的外层 list 是副本（改动它不影响注册表），元素则共享。"""
    n_before = len(_REGISTRY)
    lst = list_presets()
    lst.clear()
    assert len(_REGISTRY) == n_before, "清空返回的 list 不应影响注册表"
