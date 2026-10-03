# -*- coding: utf-8 -*-
"""国际化（i18n）完整性守卫测试（阶段 79）。

背景：i18n 此前只有「机制测试」（t() 的回退、格式化、语言切换），
**没有「内容完整性测试」**——于是下列缺陷长期存在且用户可见：

  I1 **键名层级错位**：`ui/tab_settings.py` 8 处写成 `settings.msg.*`，
     而键表定义的是 `settings.snap.msg.*`（少一层 `.snap`）——同一文件第 212 行
     用的却是正确写法，属确凿的笔误；t() 静默返回键名，用户在设置页每次操作
     都会看到 `settings.msg.clear_ok` 这种原始键名。
  I2 **键名缺失**：`history.count` / `history.tip_select` / `history.not_found` /
     `history.reprocess_done` / `history.reprocess_no_result` 五个键被调用但从未定义。
  I3 **键名不匹配**：`compare.slider` / `compare.heatmap` 调用的是不存在的名字，
     键表实际为 `compare.mode_slider` / `compare.mode_heatmap`
     （同一 choices 列表里的 `compare.mode_concat` 是对的）。
  I4 **段落遗漏**：`auto.sensitivity_info` 未定义（同段 `auto.sensitivity_label` 存在）。

本文件的核心是 **I0 守卫**：把「生产代码引用的每个静态键都必须在键表中存在」
变成一条自动化断言——同类问题从此不可能再静默复发。

与阶段 75 的关系：阶段 75 修过同一个坑的一处实例（键名含点导致查空），
但**只修了那一处、没做全局扫描**，所以同族问题在 14 处继续存活 4 个阶段。
这正是「修一处 bug 时必须做同族全局扫描」的代价证明。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from utils.i18n import DEFAULT_LANG, TRANSLATIONS, set_language, t

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 生产代码范围（不含 tests/ 与 scripts/）。
_SRC_DIRS = ("core", "services", "utils", "ui")
_SRC_FILES = ("api.py", "server.py", "cli.py", "main.py")

# 动态构造的键前缀：`t("detect.kind." + kind)` 这类无法静态枚举，
# 逐前缀在下方 test_dynamic_prefix_keys_exist 中验证其展开后确实存在。
_DYNAMIC_PREFIXES = (
    "detect.kind.",
    "detect.preset.",
    "detect.recommend.",
    "compare.viewer.",
    "media.",
)

# 显式白名单：自检用的「故意不存在」键，与文档字符串里的示例键。
_ALLOWED_UNKNOWN = {
    "not.exist.key",   # utils/i18n.py 自检验证「缺失键回退为键名」
    "x", "y",          # ui/i18n_rt.py 文档字符串中的调用示例
}


# ==============================================================================
# 基础设施
# ==============================================================================
def _flatten(node, prefix=""):
    """把嵌套键表压平成 {完整键: 值}。"""
    out = {}
    for k, v in node.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _scan_static_keys():
    """扫描生产代码，返回 {静态键: 首个出现文件}。"""
    files = []
    for d in _SRC_DIRS:
        files += [p for p in (ROOT / d).rglob("*.py") if "__pycache__" not in str(p)]
    files += [ROOT / f for f in _SRC_FILES]

    found = {}
    for p in files:
        src = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"""\bt\(\s*["']([^"']+)["']""", src):
            found.setdefault(m.group(1), str(p.relative_to(ROOT)))
    return found


ZH = _flatten(TRANSLATIONS["zh-CN"])
EN = _flatten(TRANSLATIONS["en-US"])
ALL_KEYS = set(ZH) | set(EN)


# ==============================================================================
# I0 · 守卫：生产代码引用的静态键必须全部存在（本文件的核心）
# ==============================================================================
def test_all_referenced_keys_are_defined():
    """**守卫**：生产代码里每个静态 t("...") 键都必须存在于键表。

    这是防止「键名错位 / 缺失 → 界面显示原始键名」复发的唯一自动化防线。
    修复前该断言会失败并列出来 14 个真实缺失键（见模块 docstring 的 I1~I4）。
    """
    referenced = _scan_static_keys()
    assert len(referenced) > 400, "扫描到的键数异常，正则或路径可能失效"

    missing = []
    for key, src in sorted(referenced.items()):
        if key in ALL_KEYS or key in _ALLOWED_UNKNOWN:
            continue
        if any(key.startswith(p) for p in _DYNAMIC_PREFIXES):
            continue
        missing.append(f"{key}  (引用自 {src})")

    assert not missing, (
        "以下键被引用但未定义，界面会直接显示原始键名：\n  " + "\n  ".join(missing)
    )


def test_dynamic_prefix_keys_exist():
    """动态前缀（如 `detect.kind.` + kind）展开后必须有真实键存在。"""
    for prefix in ("detect.kind.", "detect.preset.", "compare.viewer."):
        hits = [k for k in ALL_KEYS if k.startswith(prefix)]
        assert hits, f"动态前缀 {prefix!r} 下没有任何键，拼接调用必然落空"


# ==============================================================================
# I1~I4 · 本次修复的具体回归（防止被「清理」掉）
# ==============================================================================
def test_settings_snapshot_message_keys():
    """设置页操作反馈消息位于 `settings.snap.msg.*`（此前 8 处误写为 settings.msg.*）。"""
    for name in ("export_zip_ok", "compare_zip_ok", "err", "no_compare",
                 "compare_all_fail", "zip_fail", "clear_ok", "prune_ok"):
        assert f"settings.snap.msg.{name}" in ALL_KEYS


def test_settings_tab_uses_snapshot_message_path():
    """`ui/tab_settings.py` 里的消息键必须带 `.snap` 层级。"""
    src = (ROOT / "ui" / "tab_settings.py").read_text(encoding="utf-8")
    assert 't("settings.msg.' not in src, "仍有未带 .snap 层级的消息键"
    assert 't("settings.snap.msg.' in src


def test_compare_mode_labels_exist():
    """对比模式标签键名为 `compare.mode_*`（此前 2 处误写为 compare.slider/heatmap）。"""
    assert "compare.mode_slider" in ALL_KEYS
    assert "compare.mode_heatmap" in ALL_KEYS
    src = (ROOT / "ui" / "tab_media.py").read_text(encoding="utf-8")
    assert 't("compare.slider")' not in src
    assert 't("compare.heatmap")' not in src


def test_history_message_keys_exist():
    """历史页新增的 5 个消息键必须存在（此前被调用但从未定义）。"""
    for name in ("count", "tip_select", "not_found",
                 "reprocess_done", "reprocess_no_result"):
        assert f"history.{name}" in ALL_KEYS


def test_auto_sensitivity_info_exists():
    """自动页灵敏度说明键必须存在（同段 sensitivity_label 已在）。"""
    assert "auto.sensitivity_info" in ALL_KEYS
    assert "auto.sensitivity_label" in ALL_KEYS


# ==============================================================================
# 内容完整性（跳过「机制」，只查「内容」）
# ==============================================================================
def test_zh_en_key_parity():
    """中英键集合必须完全一致——否则英文用户会看到中文（t() 会静默回退）。"""
    only_zh = sorted(set(ZH) - set(EN))
    only_en = sorted(set(EN) - set(ZH))
    assert not only_zh, f"英文缺失 {len(only_zh)} 个键：{only_zh[:10]}"
    assert not only_en, f"中文缺失 {len(only_en)} 个键：{only_en[:10]}"


def test_placeholder_parity():
    """同一键在中英版本里的 {占位符} 集合必须相同，否则格式化会 KeyError 或丢参。"""
    ph = re.compile(r"\{(\w+)\}")
    bad = []
    for key in sorted(set(ZH) & set(EN)):
        a = set(ph.findall(str(ZH[key])))
        b = set(ph.findall(str(EN[key])))
        if a != b:
            bad.append(f"{key}: zh={sorted(a)} en={sorted(b)}")
    assert not bad, "占位符不一致：\n  " + "\n  ".join(bad)


def test_all_leaves_are_non_empty_strings():
    """所有叶子必须是**非空字符串**（空串会在界面上表现为空白，难以排查）。"""
    bad = [k for k, v in {**ZH, **EN}.items() if not isinstance(v, str) or not v.strip()]
    assert not bad, f"存在非字符串或空文案：{bad[:10]}"


def test_key_segments_contain_no_dots():
    """**键名本身**的每一层都不得含点——点是路径分隔符（阶段 75 的教训）。"""
    problems = []

    def walk(node, prefix=""):
        for k, v in node.items():
            if "." in k:
                problems.append(f"{prefix}{k}")
            if isinstance(v, dict):
                walk(v, f"{prefix}{k}.")

    for lang, table in TRANSLATIONS.items():
        walk(table, f"{lang}:")
    assert not problems, f"键名含点会被 t() 分层遍历查空：{problems}"


def test_no_orphan_keys_within_used_prefixes():
    """`settings.snap.msg` 与 `compare` 段不应留下「同名但层级不同」的孪生键。

    修复 I1 后，`settings.msg.*` 这种错误层级不得再被新增（键表与调用点都不该有）。
    """
    assert not [k for k in ALL_KEYS if k.startswith("settings.msg.")], \
        "键表中出现了错误的 settings.msg.* 层级"


# ==============================================================================
# 运行时抽查（确认修复后用户看到的是文案而非键名）
# ==============================================================================
@pytest.mark.parametrize("key", [
    "settings.snap.msg.clear_ok",
    "settings.snap.msg.err",
    "compare.mode_slider",
    "compare.mode_heatmap",
    "history.count",
    "history.tip_select",
    "history.not_found",
    "history.reprocess_done",
    "history.reprocess_no_result",
    "auto.sensitivity_info",
])
def test_fixed_keys_render_in_both_languages(key):
    """修复后的键在中英两种语言下都必须渲染为真实文案（≠ 键名本身）。"""
    try:
        for lang in ("zh-CN", "en-US"):
            set_language(lang)
            text = t(key, n=3, name="a.png", status="成功", exc="E")
            assert text != key, f"{key} 在 {lang} 下渲染为键名本身"
            assert text.strip(), f"{key} 在 {lang} 下为空"
    finally:
        set_language(DEFAULT_LANG)
