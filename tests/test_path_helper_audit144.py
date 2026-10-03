# -*- coding: utf-8 -*-
"""阶段 144 · `utils/path_helper` 真值审计守卫。

审计对象：`utils/path_helper.py`（荒漠度 2.1 = 183 代码行 / 22978 字节 ÷ 4 个引用测试文件里 89 个用例）。
前置：阶段 78 已重写 `sanitize_filename` 的清洗管线（N1–N5），阶段 83 已给 `display_path`
加跨盘符 `try/except`。本轮审的是**阶段 78 未覆盖的三条边界**。

本阶段修的五处
--------------
F1 **`fallback` 绕过清洗管线**（P1）：`sanitize_filename(".", fallback="a/b")` → `"a/b"`
    —— 含路径分隔符；`fallback="..\\\\..\\\\etc"` → `"..\\..\\etc"`；`fallback="evil*name"`
    → `"evil*name"`。第 5 步 `stem = fallback` **跳过了第 2/3 步清洗**（那时清洗已执行完），
    而函数契约是返回「清洗后的**安全**文件名」。
F2 **`max_length` 不是硬上限**（声明订正）：`budget = max(1, max_length - len(suffix))`
    ⇒ `sanitize_filename("abc.png", 4)` → `"a.png"`（长度 5 > 4）。
    行为是**唯一合理的**（扩展名与 ≥1 字符主名都不该被截空），故**只改声明**。
F3 **`assert_file_readable` 的 docstring 漏列 `IsADirectoryError`**：实际传目录会抛它，
    按 docstring 只 `except FileNotFoundError` 的调用方会让异常逃逸。
F4 **`get_output_dir(sub_folder)` 可越出输出目录**：`"../escape"` → `<根>/escape`、
    `"/abs"` → `D:\\abs`、`"C:\\\\x"` → `C:\\x`。该函数会**真实创建目录**，
    而 docstring 承诺「在输出目录**下**」。
F5 **`make_unique_path` 的 `Path(...).name` 分隔符语义是平台相关的**：
    `Path("a\\\\b.txt").name` 在 Windows 是 `"b.txt"`，在 POSIX 是 `"a\\\\b.txt"`
    ⇒ docstring 的「剥离任何目录成分」**只在 Windows 成立**。

本文件守七层
------------
L0 存在性自检：内嵌旧实现副本，证明 F1/F4/F5 的旧行为确实出错
L1 F1：fallback 必须走清洗（路径分隔符 / 非法字符 / 控制字符 / 空串 / 保留名）
L2 F2：`max_length` 极小值时的**实际行为**钉住 + 声明必须写明「目标值而非硬上限」
L3 F3：docstring 的「异常」段必须同时列出两个异常，且实际行为不变
L4 F4：`get_output_dir` 对 8 类越界输入必须**始终留在输出目录内**
L5 F5：`make_unique_path` 必须剥离 `/` 与 `\\` **两种**分隔符（不依赖平台）
L6 回归：阶段 78 的 N1–N5 与阶段 83 的跨盘符行为不得回退
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import tempfile
import shutil
from pathlib import Path, PurePosixPath

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                                                        # noqa: E402
from utils import path_helper as ph                                  # noqa: E402
from utils.path_helper import (                                      # noqa: E402
    _ILLEGAL_RE,
    _CONTROL_RE,
    _scrub,
    assert_file_readable,
    build_output_filename,
    display_path,
    get_output_dir,
    make_unique_path,
    normalize_unicode,
    sanitize_filename,
)

_SRC = open(os.path.join(_ROOT, "utils", "path_helper.py"), encoding="utf-8").read()


# ---------------------------------------------------------------------------
# L0 · 存在性自检：内嵌旧实现，证明三条缺陷真实存在
# ---------------------------------------------------------------------------

def _legacy_sanitize_fallback(fallback):
    """旧实现第 5 步：`stem = fallback` —— **不经任何清洗**。"""
    return fallback


def _legacy_unique_name(desired_name):
    """旧实现：`Path(str(desired_name)).name` —— 分隔符语义随平台变。"""
    return Path(str(desired_name)).name or "output"


def _legacy_output_dir(base, sub_folder):
    """旧实现：`base / sub_folder` —— 未做任何约束。"""
    if sub_folder:
        return base / sub_folder
    return base


def test_L0_legacy_fallback_passes_illegal_chars_through():
    """旧实现原样返回 fallback ⇒ 路径分隔符可注入（证明 F1 是真缺陷）。"""
    for fb in ("a/b", "..\\..\\etc", "evil*name"):
        got = _legacy_sanitize_fallback(fb)
        assert got == fb
        assert any(c in got for c in "/\\*"), f"{fb!r} 本应含非法字符"


def test_L0_legacy_unique_name_posix_does_not_strip_backslash():
    """`PurePosixPath("a\\b.txt").name` 不剥离反斜杠 ⇒ 证明 F5 的平台相关是真缺陷。"""
    assert Path("a\\b.txt").name == "b.txt", "本机应是被支持的 Windows 语义"
    assert PurePosixPath("a\\b.txt").name == "a\\b.txt", (
        "POSIX 语义下反斜杠不是分隔符 —— 旧实现无法剥离"
    )
    # 新旧对比
    assert _legacy_unique_name("a\\b.txt") == "b.txt"          # Windows 侥幸正确
    assert PurePosixPath("a\\b.txt").name != "b.txt"            # POSIX 失效


def test_L0_legacy_output_dir_escapes(tmp_path):
    """旧实现可直接越出输出目录（证明 F4 是真缺陷）。"""
    base = tmp_path / "outputs"
    base.mkdir()
    escaped = _legacy_output_dir(base, "../escape")
    assert not str(os.path.normpath(str(escaped))).startswith(str(base)), (
        "旧实现确实能越出输出目录"
    )


# ---------------------------------------------------------------------------
# L1 · F1：fallback 必须走清洗
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fallback,want", [
    ("未命名", "未命名"),
    ("a/b", "a_b"),
    # `..\..\etc` → _scrub 得 `.._.._etc` → 第④步 strip(" .") 吃掉首部的点 → `_.._etc`
    # （与 name 路径**同规则**：name 的首部点同样会被 strip 掉）
    ("..\\..\\etc", "_.._etc"),
    ("evil*name", "evil_name"),
    ("a<b>c:d", "a_b_c_d"),
    ("x\x00y", "x_y"),            # 控制字符
    ("x\ty", "x_y"),
])
def test_L1_fallback_is_sanitized(fallback, want):
    """fallback 必须经过与 name **同一套**处理（控制字符 / 非法字符 / 首尾空格与点）。"""
    got = sanitize_filename(".", 200, fallback=fallback)
    assert got == want, f"fallback={fallback!r} 未被正确清洗：{got!r}"


def test_L1_fallback_never_contains_path_separator():
    """核心回归：fallback 的产物**永不含路径分隔符**（否则会写到子目录）。"""
    for fb in ("a/b", "..\\..\\etc", "//x//y", "/", "\\", "a/b/c/d/e"):
        got = sanitize_filename(".", 200, fallback=fb)
        assert "/" not in got and "\\" not in got, f"fallback={fb!r} 漏出分隔符：{got!r}"


def test_L1_fallback_never_contains_illegal_chars():
    """非法字符集合必须全部被清掉。"""
    for fb in ("<>:\"|?*", "a<b", "x?y", "p|q"):
        got = sanitize_filename(".", 200, fallback=fb)
        assert not _ILLEGAL_RE.search(got), f"fallback={fb!r} 漏出非法字符：{got!r}"


def test_L1_fallback_still_gets_reserved_name_and_length_control():
    """fallback 仍须经过保留设备名与长度控制（这两步本就在 fallback 赋值之后）。"""
    assert sanitize_filename(".", 200, fallback="CON") == "_CON"
    assert sanitize_filename(".", 200, fallback="com1") == "_com1"
    long_fb = "x" * 500
    assert len(sanitize_filename(".", 50, fallback=long_fb)) <= 50


@pytest.mark.parametrize("fallback", ["", "   ", "???", "<>:", "..."])
def test_L1_degenerate_fallback_falls_back_to_builtin(fallback):
    """fallback 本身清洗后为空时，必须兜底到内置名（不得产出空主名）。"""
    got = sanitize_filename(".", 200, fallback=fallback)
    assert got, f"fallback={fallback!r} 产出了空主名"
    assert got.strip(" .") != "", f"fallback={fallback!r} 产物全是点/空格：{got!r}"


def test_L1_scrub_is_the_single_source():
    """`_scrub` 必须是 name 与 fallback 共用的**同一步**（避免两处规则漂移）。"""
    assert _scrub("a/b*") == "a_b_"
    assert _scrub("x\x00y") == "x_y"
    # 源码层面：清洗管线只应出现一次 `_ILLEGAL_RE.sub`
    assert _SRC.count("_ILLEGAL_RE.sub") == 1, "非法字符替换出现了多处 ⇒ 规则可能漂移"


def test_L1_normal_name_behavior_unchanged():
    """回归：正常 name 的清洗结果不得因 F1 改动而变化（阶段 78 的 N1–N5）。

    ⚠ 注意两处**既有**行为（不是本阶段引入，且已被既有测试/注释锁定）：
      · `a.//evil` 的连续下划线**不在本函数折叠**（折叠逻辑在 `build_output_filename`
        的 `re.sub(r"__+", "_", ...)`）⇒ 结果是 `a.__evil`；
      · 首点会被第 ④ 步的 `strip(" .")` 去掉（`_split_name` 只是「不把它当扩展名」），
        故 `.gitignore` → `gitignore` —— 与既有测试
        `sanitize_filename("...") == "未命名"` 的「点号被 strip 掉」注释同源。
    """
    assert sanitize_filename("我的照片/2024?.png") == "我的照片_2024_.png"
    assert sanitize_filename("a.//evil") == "a.__evil"
    assert sanitize_filename("a.png ") == "a.png"
    assert sanitize_filename("CON.png") == "_CON.png"
    assert sanitize_filename(".gitignore") == "gitignore"   # 首点由 strip(" .") 去掉


# ---------------------------------------------------------------------------
# L2 · F2：max_length 的实际行为 + 声明必须写准
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,max_length,want", [
    ("abc.png", 200, "abc.png"),
    ("abc.png", 7, "abc.png"),
    ("abc.png", 5, "a.png"),
    ("abc.png", 4, "a.png"),     # 5 > 4：主名 ≥1 字符的下限在此显现
    ("abc.png", 1, "a.png"),
    ("abc.png", 0, "a.png"),
    ("a", 0, "a"),
])
def test_L2_max_length_actual_behavior(name, max_length, want):
    """钉住 `max_length` 极小值时的实际产出（主名至少保留 1 字符）。"""
    assert sanitize_filename(name, max_length) == want


def test_L2_max_length_is_target_not_hard_cap():
    """当 `max_length < len(suffix) + 1` 时，结果**必然超出** `max_length`（当前设计）。"""
    out = sanitize_filename("abc.png", 4)
    assert len(out) > 4, "本用例钉住的正是「可超出」这一事实"


def test_L2_docstring_declares_target_not_hard_cap():
    """docstring 必须写明 `max_length` 是**目标值而非硬上限**（防声明再次失真）。"""
    doc = sanitize_filename.__doc__ or ""
    assert "目标" in doc and "硬上限" in doc, (
        "docstring 未写明 max_length 是目标值而非硬上限"
    )
    # 不得再保留「整个文件名的最大长度」这种**绝对**表述
    assert "（主名 + 扩展名，不含目录）的最大长度" not in doc


def test_L2_normal_max_length_still_respected():
    """常规 max_length 下长度控制仍然生效（回归）。"""
    assert len(sanitize_filename("a" * 100 + ".png", 20)) <= 20
    assert len(sanitize_filename("a" * 100 + ".png", 120)) <= 120


# ---------------------------------------------------------------------------
# L3 · F3：assert_file_readable 的异常声明
# ---------------------------------------------------------------------------

def test_L3_docstring_lists_both_exceptions():
    """docstring 的「异常」段必须同时列出 FileNotFoundError 与 IsADirectoryError。"""
    doc = assert_file_readable.__doc__ or ""
    assert "FileNotFoundError :" in doc
    assert "IsADirectoryError :" in doc, "docstring 漏列 IsADirectoryError（声明 ≠ 实现）"


def test_L3_directory_raises_isadirectoryerror(tmp_path):
    """传目录必须抛 IsADirectoryError（实际行为，不得因补声明而改变）。"""
    with pytest.raises(IsADirectoryError):
        assert_file_readable(tmp_path)


def test_L3_missing_file_raises_filenotfounderror(tmp_path):
    """传缺失文件必须抛 FileNotFoundError（回归）。"""
    with pytest.raises(FileNotFoundError):
        assert_file_readable(tmp_path / "nope.txt")


def test_L3_regular_file_passes(tmp_path):
    """正常文件必须原样返回 Path（正向对照）。"""
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    got = assert_file_readable(f)
    assert Path(got) == f


# ---------------------------------------------------------------------------
# L4 · F4：get_output_dir 不得越出输出目录
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sub", [
    "2026-08-03", "a", "../escape", "..", "../..", "/abs", "C:\\x", "a/b",
    "..\\..\\etc", ".", "", "   ", "./x", "a/../../b",
])
def test_L4_sub_folder_stays_inside_output_dir(sub, monkeypatch, tmp_path):
    """任何 `sub_folder` 都必须让结果落在输出目录内（含其子目录）。"""
    base = tmp_path / "outputs"
    base.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", base)
    got = get_output_dir(sub)
    got_n = os.path.normpath(str(got))
    base_n = os.path.normpath(str(base))
    inside = (got_n == base_n) or got_n.startswith(base_n + os.sep)
    assert inside, f"sub_folder={sub!r} 越出输出目录：{got_n}"


def test_L4_legit_sub_folder_still_creates_nested_dir(monkeypatch, tmp_path):
    """合法的子目录名仍必须被采纳并真实创建（正向对照，防「一律忽略」式假修复）。"""
    base = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUT_DIR", base)
    got = get_output_dir("2026-08-03")
    assert got == base / "2026-08-03"
    assert got.is_dir(), "合法子目录必须被创建"


def test_L4_dot_segments_degrade_to_root(monkeypatch, tmp_path):
    """`.` / `..` / 空串 应退化为「返回输出根目录」，而不是拼出「父目录(1)」。"""
    base = tmp_path / "outputs"
    base.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", base)
    for sub in (".", "..", "", "   "):
        assert get_output_dir(sub) == base, f"sub={sub!r} 未退化为输出根目录"


def test_L4_no_arg_still_returns_root(monkeypatch, tmp_path):
    """无参调用（唯一现存调用方式）行为不变。"""
    base = tmp_path / "outputs"
    monkeypatch.setattr(config, "OUTPUT_DIR", base)
    assert get_output_dir() == base
    assert get_output_dir(None) == base


# ---------------------------------------------------------------------------
# L5 · F5：make_unique_path 必须剥离两种分隔符
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ("a/b.txt", "b.txt"),
    ("a\\b.txt", "b.txt"),          # ← F5 的核心：反斜杠（POSIX 上也须剥离）
    ("C:\\x\\y.png", "y.png"),
    ("a/b/../c.txt", "c.txt"),
    ("a//b.txt", "b.txt"),
    ("..", "output"),
    (".", "output"),
    ("", "output"),
    ("/", "output"),
    ("\\", "output"),
])
def test_L5_strips_both_separators(tmp_path, raw, want):
    """`/` 与 `\\` 两种目录分隔符都必须被剥离（不依赖 `Path` 的平台语义）。"""
    got = Path(make_unique_path(tmp_path, raw)).name
    assert got == want, f"raw={raw!r} → {got!r}（期望 {want!r}）"


def test_L5_result_never_contains_separator(tmp_path):
    """产物名字不得含任何分隔符（守住「无法越出 target_dir」）。"""
    for raw in ("a/b.txt", "a\\b.txt", "/x", "\\x", "a/b\\c/d.txt"):
        got = Path(make_unique_path(tmp_path, raw)).name
        assert "/" not in got and "\\" not in got, f"raw={raw!r} 漏出分隔符：{got!r}"


def test_L5_result_stays_inside_target_dir(tmp_path):
    """产物路径必须在 target_dir 之内（纵深防御的承诺）。"""
    for raw in ("../escape.txt", "..\\..\\escape.txt", "/abs.txt", "a/../../b.txt"):
        p = make_unique_path(tmp_path, raw)
        assert Path(p).parent == Path(tmp_path), f"raw={raw!r} 越出目录：{p}"


def test_L5_no_platform_dependency_in_source():
    """源码不得再用 `Path(str(...)).name` 取词干（该语义随平台变）。"""
    assert "Path(str(desired_name)).name" not in _SRC, (
        "仍在使用平台相关的 Path(...).name 取词干"
    )
    assert 'replace("\\\\", "/")' in _SRC, "未显式兼容反斜杠分隔符"


@pytest.mark.parametrize("raw,want", [
    ("a.txt", "a(2).txt"),
])
def test_L5_sequence_algorithm_unchanged(tmp_path, raw, want):
    """序号算法不得因 F5 改动而回退。"""
    for nm in ("a.txt", "a(1).txt"):
        (tmp_path / nm).write_text("", encoding="utf-8")
    assert Path(make_unique_path(tmp_path, raw)).name == want


def test_L5_dotfile_and_multi_dot_unchanged(tmp_path):
    """点开头与多重点名的既有行为不变。"""
    assert Path(make_unique_path(tmp_path, ".gitignore")).name == ".gitignore"
    assert Path(make_unique_path(tmp_path, "a.b.c.png")).name == "a.b.c.png"


# ---------------------------------------------------------------------------
# L6 · 回归：阶段 78 / 83 的行为不得回退
# ---------------------------------------------------------------------------

def test_L6_build_output_filename_n1_fix_preserved():
    """阶段 78 的 N1 修复：模板无扩展名时**安全追加**，绝不截断主名里的点。"""
    got = build_output_filename("my.photo", "png", date_str="20260803", time_str="125053")
    assert got == "my.photo_cleaned_125053.png"
    # 模板写死扩展名时仍应被目标格式替换
    assert build_output_filename("shot.jpg", "webp").endswith(".webp")


def test_L6_sanitize_n2_n5_fixes_preserved():
    """阶段 78 的 N2–N5：扩展名位置不再是清洗盲区。"""
    assert sanitize_filename("a.//evil") == "a.__evil"      # 分隔符已清；下划线不在此折叠
    assert sanitize_filename("a.png ") == "a.png"
    assert sanitize_filename("CON.png") == "_CON.png"
    assert sanitize_filename("x\x00y.png") == "x_y.png"


def test_L6_display_path_cross_drive_never_raises(tmp_path):
    """阶段 83：`display_path` 在任何情况下都不得抛异常（展示层不该让功能失败）。"""
    for p in (tmp_path / "x.txt", "C:/definitely/other/drive.txt", "", "/"):
        out = display_path(p, base=tmp_path)
        assert isinstance(out, str) and out


def test_L6_normalize_unicode_unchanged():
    """NFKC 归一化行为不变。"""
    assert normalize_unicode("ＡＢＣ") == "ABC"
    assert normalize_unicode("１２３") == "123"


def test_L6_module_selfcheck_still_registered():
    """模块自检必须仍在 `test_legacy_selfcheck.MODULES` 中（子进程守卫）。"""
    from tests.test_legacy_selfcheck import MODULES
    assert any(m[0] == "utils/path_helper.py" for m in MODULES), (
        "utils/path_helper.py 未登记子进程自检守卫"
    )
