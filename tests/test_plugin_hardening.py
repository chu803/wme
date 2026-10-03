# -*- coding: utf-8 -*-
"""插件框架加固回归测试（阶段 77）。

本文件锁定阶段 77「插件系统真值审计」修复的六项缺陷，防止回归：

  P1 插件不得静默覆盖内建引擎；引擎清单必须与注册表（真实执行对象）一致。
  P2 引擎名必须经归一化 + 正则校验，非法名（空格/点号/中文/数字开头/超长）一律拒绝。
  P3 load_plugins() 返回值语义统一为「本次实际加载的文件数」。
  P4 插件加载失败后不得在 sys.modules 残留半初始化模块。
  P5 插件目录首次不存在时不得永久放弃扫描（后建目录仍可发现）。
  P6 同名插件重复注册默认拒绝（先到先得），不再按字母序静默覆盖。

另有阶段 77 新增能力的护栏：`reload_plugins()`、`get_plugin_report()`、`source` 追溯。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import config
from core import plugins as pg
from core.plugins import InpaintEnginePlugin


# ==============================================================================
# 基础设施
# ==============================================================================
class _DummyEngine(InpaintEnginePlugin):
    """可复用的哑引擎（合法名由用例覆盖）。"""

    name = "dummy_engine"
    display_name = "哑引擎"
    description = "用于测试"

    def inpaint(self, image, mask, *, device=None, progress=None):  # pragma: no cover
        return image


@pytest.fixture
def pg_iso(monkeypatch):
    """隔离插件框架的模块级状态，用例结束后完整还原。"""
    reg_snapshot = dict(pg._REGISTRY)
    dirs_snapshot = set(pg._SCANNED_DIRS)
    loaded_snapshot = pg._LOADED
    report_snapshot = {k: list(v) if isinstance(v, list) else v for k, v in pg._LAST_REPORT.items()}
    yield pg
    pg._REGISTRY.clear()
    pg._REGISTRY.update(reg_snapshot)
    pg._SCANNED_DIRS.clear()
    pg._SCANNED_DIRS.update(dirs_snapshot)
    pg._LOADED = loaded_snapshot
    for k, v in report_snapshot.items():
        pg._LAST_REPORT[k] = v


def _write_plugin(directory: Path, filename: str, engine_name: str, display: str = "插件引擎") -> Path:
    """在目录中落一个最小可用插件文件。"""
    path = directory / filename
    path.write_text(
        "from core.plugins import InpaintEnginePlugin, register_engine\n"
        "class _P(InpaintEnginePlugin):\n"
        f"    name = {engine_name!r}\n"
        f"    display_name = {display!r}\n"
        "    def inpaint(self, image, mask, *, device=None, progress=None):\n"
        "        return image\n"
        "register_engine(_P)\n",
        encoding="utf-8",
    )
    return path


def _fresh_scan(monkeypatch, pg_mod, dirs):
    """把插件目录指向 dirs 并从零开始扫描一次。"""
    monkeypatch.setattr(config, "PLUGIN_DIRS", list(dirs))
    monkeypatch.setattr(config, "PLUGIN_ENABLED", [])
    pg_mod._SCANNED_DIRS.clear()
    pg_mod._LOADED = False


# ==============================================================================
# P2 · 引擎名合法性校验
# ==============================================================================
@pytest.mark.parametrize(
    "bad_name",
    [
        "Bad Name!",    # 含空格与感叹号
        "My-Engine",    # 含连字符
        "中文引擎",      # 非 ASCII
        "with.dot",     # 含点号（与 i18n 路径分隔符冲突）
        "9leading",     # 数字开头
        "a" * 33,       # 超长（>32）
        "  ",           # 纯空白（归一化后为空）
        "a b",          # 含内部空格
    ],
)
def test_illegal_engine_name_rejected(pg_iso, bad_name):
    """非法引擎名一律拒绝注册，且不进入注册表。"""
    inst = _DummyEngine()
    inst.name = bad_name
    assert pg_iso.register_engine(inst) is False
    key = str(bad_name).strip().lower()
    assert key not in pg_iso._REGISTRY


def test_empty_engine_name_rejected(pg_iso):
    """空名（含纯空白）拒绝注册。"""
    inst = _DummyEngine()
    inst.name = ""
    assert pg_iso.register_engine(inst) is False


def test_uppercase_name_normalized_and_registered(pg_iso):
    """大写名归一化为小写后注册（向后兼容历史写法）。"""
    inst = _DummyEngine()
    inst.name = "Upper_Engine"
    try:
        assert pg_iso.register_engine(inst) is True
        assert pg_iso.has_engine("upper_engine") is True
        assert pg_iso.get_engine("UPPER_ENGINE") is not None
    finally:
        pg_iso._REGISTRY.pop("upper_engine", None)


def test_legal_name_boundaries(pg_iso):
    """合法名边界：单字母 / 下划线 / 32 字符上限。"""
    for good in ("a", "ok_name_1", "x" * 32):
        inst = _DummyEngine()
        inst.name = good
        try:
            assert pg_iso.register_engine(inst) is True
        finally:
            pg_iso._REGISTRY.pop(good, None)


# ==============================================================================
# P1 / P6 · 覆盖保护与清单一致性
# ==============================================================================
def test_builtin_engine_cannot_be_overridden_by_default(pg_iso):
    """插件不能覆盖内建引擎（默认），内建实例保持不变。"""
    original = pg_iso.get_engine("lama")
    assert original is not None

    class _FakeLama(_DummyEngine):
        name = "lama"
        display_name = "伪装引擎"

    assert pg_iso.register_engine(_FakeLama()) is False
    assert pg_iso.get_engine("lama") is original, "内建实例不应被替换"
    assert pg_iso.get_engine("lama").display_name == "LaMa 深度学习"


def test_engine_list_matches_registry_after_blocked_override(pg_iso):
    """拦截覆盖后，清单仍应与真实执行对象一致（P1 不变量）。"""
    class _FakeLama(_DummyEngine):
        name = "lama"
        display_name = "伪装引擎"

    pg_iso.register_engine(_FakeLama())
    info = next(e for e in pg_iso.list_engines() if e.name == "lama")
    live = pg_iso.get_engine("lama")
    assert info.display_name == live.display_name
    assert info.builtin is True


def test_override_allowed_when_flag_on_and_list_is_truthful(pg_iso, monkeypatch):
    """开启逃生舱后允许覆盖，且清单如实反映覆盖者（不再显示内建描述）。"""
    monkeypatch.setattr(config, "PLUGIN_ALLOW_OVERRIDE", True)

    class _FakeLama(_DummyEngine):
        name = "lama"
        display_name = "伪装引擎"
        description = "我不是真的 LaMa"

    try:
        assert pg_iso.register_engine(_FakeLama()) is True
        info = next(e for e in pg_iso.list_engines() if e.name == "lama")
        live = pg_iso.get_engine("lama")
        assert info.display_name == "伪装引擎"
        assert info.builtin is False
        assert info.description == "我不是真的 LaMa"
        assert info.display_name == live.display_name, "清单必须等于实际执行对象"
    finally:
        pg_iso._REGISTRY.pop("lama", None)
        pg_iso.register_engine(pg._BuiltinLama)


def test_duplicate_plugin_first_wins(pg_iso, tmp_path, monkeypatch):
    """同名插件：先加载者胜出，后者被拒绝（不再按字母序静默覆盖）。"""
    _write_plugin(tmp_path, "a_first.py", "dup_engine", "第一个")
    _write_plugin(tmp_path, "b_second.py", "dup_engine", "第二个")
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    pg_iso._REGISTRY.pop("dup_engine", None)
    try:
        pg_iso.load_plugins()
        eng = pg_iso.get_engine("dup_engine")
        assert eng is not None and eng.display_name == "第一个"
    finally:
        pg_iso._REGISTRY.pop("dup_engine", None)


def test_explicit_allow_override_parameter(pg_iso, monkeypatch):
    """register_engine(allow_override=) 可逐次覆盖配置（供 reload / 测试使用）。"""
    monkeypatch.setattr(config, "PLUGIN_ALLOW_OVERRIDE", False)

    class _First(_DummyEngine):
        name = "ovr_engine"
        display_name = "老的"

    class _Second(_DummyEngine):
        name = "ovr_engine"
        display_name = "新的"

    try:
        assert pg_iso.register_engine(_First()) is True
        assert pg_iso.register_engine(_Second()) is False, "默认拒绝"
        assert pg_iso.register_engine(_Second(), allow_override=True) is True
        assert pg_iso.get_engine("ovr_engine").display_name == "新的"
    finally:
        pg_iso._REGISTRY.pop("ovr_engine", None)


# ==============================================================================
# P3 · load_plugins() 返回值语义
# ==============================================================================
def test_load_plugins_return_semantics(pg_iso, tmp_path, monkeypatch):
    """首次返回实际加载文件数；重复调用返回 0（不再返回注册表规模）。"""
    _write_plugin(tmp_path, "one_plugin.py", "one_engine")
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    try:
        first = pg_iso.load_plugins()
        second = pg_iso.load_plugins()
        assert first == 1, f"期望加载 1 个文件，实得 {first}"
        assert second == 0, "重复调用应表示「本次没有加载任何文件」"
    finally:
        pg_iso._REGISTRY.pop("one_engine", None)


def test_load_plugins_counts_only_success(pg_iso, tmp_path, monkeypatch):
    """失败文件不计入返回值。"""
    _write_plugin(tmp_path, "ok_plugin.py", "ok_engine")
    (tmp_path / "boom_plugin.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    try:
        assert pg_iso.load_plugins() == 1
    finally:
        pg_iso._REGISTRY.pop("ok_engine", None)


# ==============================================================================
# P4 · 失败模块不得残留
# ==============================================================================
def test_failed_plugin_leaves_no_sys_modules_residue(pg_iso, tmp_path, monkeypatch):
    """崩溃插件：不留 sys.modules 残留，且失败原因进入报告。"""
    (tmp_path / "explode_plugin.py").write_text("raise RuntimeError('炸了')\n", encoding="utf-8")
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    pg_iso.load_plugins()

    residue = [k for k in sys.modules if k.startswith("wme_plugins.explode")]
    assert residue == [], f"失败模块不应残留于 sys.modules：{residue}"

    failed = pg_iso.get_plugin_report()["failed"]
    assert any("explode_plugin.py" in item["file"] for item in failed)
    assert any("炸了" in item["error"] for item in failed)


# ==============================================================================
# P5 · 目录延迟创建仍可发现
# ==============================================================================
def test_missing_dir_does_not_permanently_give_up(pg_iso, tmp_path, monkeypatch):
    """首次扫描时目录不存在 → 不置完成位；目录创建后可被发现。"""
    target = tmp_path / "not_yet"
    _fresh_scan(monkeypatch, pg_iso, [target])
    pg_iso.load_plugins()
    assert pg_iso._LOADED is False, "目录全不存在时不应标记为已完成扫描"

    target.mkdir(parents=True)
    _write_plugin(target, "late_plugin.py", "late_engine")
    try:
        assert pg_iso.has_engine("late_engine") is True, "后建目录的插件应能被发现"
    finally:
        pg_iso._REGISTRY.pop("late_engine", None)


def test_existing_dir_sets_loaded_flag(pg_iso, tmp_path, monkeypatch):
    """目录存在（即使没有插件）→ 置完成位，避免反复 stat。"""
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    pg_iso.load_plugins()
    assert pg_iso._LOADED is True


# ==============================================================================
# 阶段 77 新增能力 · source 追溯 / 报告 / 热加载 / 启用清单
# ==============================================================================
def test_source_backfilled_for_plugin(pg_iso, tmp_path, monkeypatch):
    """插件实例的 source 指向其来源文件；内建引擎 source 为空。"""
    _write_plugin(tmp_path, "src_plugin.py", "src_engine")
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    try:
        pg_iso.load_plugins()
        eng = pg_iso.get_engine("src_engine")
        assert eng is not None and str(eng.source).endswith("src_plugin.py")
        info = next(e for e in pg_iso.list_engines() if e.name == "src_engine")
        assert info.source.endswith("src_plugin.py")
        lama_info = next(e for e in pg_iso.list_engines() if e.name == "lama")
        assert lama_info.source == ""
    finally:
        pg_iso._REGISTRY.pop("src_engine", None)


def test_plugin_report_shape(pg_iso, tmp_path, monkeypatch):
    """get_plugin_report() 返回结构化结果（loaded/failed/skipped/overridden/engines）。"""
    _write_plugin(tmp_path, "rep_plugin.py", "rep_engine")
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    try:
        pg_iso.load_plugins()
        rep = pg_iso.get_plugin_report()
        assert set(rep) >= {"loaded", "failed", "skipped", "overridden", "engines"}
        assert any(str(p).endswith("rep_plugin.py") for p in rep["loaded"])
        assert any(e["name"] == "rep_engine" for e in rep["engines"])
    finally:
        pg_iso._REGISTRY.pop("rep_engine", None)


def test_enabled_filter_skips_and_reports(pg_iso, tmp_path, monkeypatch):
    """PLUGIN_ENABLED 不在清单内的插件被跳过，且计入 report.skipped。"""
    _write_plugin(tmp_path, "want_plugin.py", "want_engine")
    _write_plugin(tmp_path, "skip_plugin.py", "skip_engine")
    monkeypatch.setattr(config, "PLUGIN_DIRS", [str(tmp_path)])
    monkeypatch.setattr(config, "PLUGIN_ENABLED", ["want_plugin"])
    pg_iso._SCANNED_DIRS.clear()
    pg_iso._LOADED = False
    try:
        assert pg_iso.load_plugins() == 1
        assert pg_iso.has_engine("want_engine") is True
        assert pg_iso.has_engine("skip_engine") is False
        skipped = pg_iso.get_plugin_report()["skipped"]
        assert any("skip_plugin.py" in item["file"] for item in skipped)
    finally:
        pg_iso._REGISTRY.pop("want_engine", None)


def test_reload_plugins_drops_non_builtin_and_rescans(pg_iso, tmp_path, monkeypatch):
    """reload_plugins() 注销非内建引擎并重扫；内建引擎始终保留。"""
    _write_plugin(tmp_path, "hot_plugin.py", "hot_engine", "热加载 v1")
    _fresh_scan(monkeypatch, pg_iso, [tmp_path])
    pg_iso.load_plugins()
    assert pg_iso.has_engine("hot_engine") is True

    # 改写插件后热加载，应换成新定义（若不注销旧引擎，会被覆盖保护拦下）。
    _write_plugin(tmp_path, "hot_plugin.py", "hot_engine", "热加载 v2")
    try:
        pg_iso.reload_plugins()
        eng = pg_iso.get_engine("hot_engine")
        assert eng is not None and eng.display_name == "热加载 v2"
        assert pg_iso.has_engine("lama") is True, "内建引擎必须保留"
        assert pg_iso.has_engine("opencv") is True
    finally:
        pg_iso._REGISTRY.pop("hot_engine", None)


def test_builtin_present_and_auto_first(pg_iso):
    """引擎清单不变量：auto 恒居首，内建 lama/opencv 恒在。"""
    choices = pg_iso.get_engine_choices()
    assert choices[0] == "auto"
    assert "lama" in choices and "opencv" in choices


def test_engine_detail_includes_source_field(pg_iso):
    """引擎详情字典含阶段 77 新增的 source 字段。"""
    for item in pg_iso.get_engine_detail():
        assert "source" in item
