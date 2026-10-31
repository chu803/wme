# -*- coding: utf-8 -*-
"""阶段 362 · OS 便携性：测试与展示路径都不许假设「跑在 Windows 上」。

现场（CI run #39，Ubuntu runner，5m36s 才红 ⇒ 装依赖/探针/收集全过了，红在用例级）：

1. `tests/test_preset_recommender.py::test_process_file_auto_preset_threading`
   清理临时文件时调用 `powershell Remove-Item` ⇒ `FileNotFoundError: 'powershell'`。
   **根因不是"Linux 没有 powershell"，而是本机开发环境的限制（沙箱 safe-delete 闩锁）
   被写进了产品测试的可执行文件依赖** —— 用例于是只能在这台机器上跑。
   正解：让 pytest 自己管（`tmp_path`），用例**不做任何删除**。
2. `utils/path_helper.display_path` 在 POSIX 上不会为「另一个挂载点」抛 `ValueError`，
   而是给出 `../Z:\\somewhere\\baseline.json` 这种**逃逸出 base** 的路径 ⇒
   `test_real_gate.py::test_display_path_survives_cross_drive` 红。
   正解：按函数自己写的契约（「表达不了相对就退回绝对」）把**逃逸也算表达不了** ⇒ 两个 OS 同答案。
3. 诊断产物 `gate_output.txt` 被 `tee` 到仓库根目录 ⇒ `test_repo_hygiene` 判散落文件（阶段 356 引入）。
   正解：写进 `logs/`（目录本来就不算散落文件，且与 356 的步骤日志同处）。

本守卫钉三件事：
  ① `tests/` 里不许出现 Windows 专属可执行文件调用（AST 判定，文本判据会把注释也算进去）；
  ② 反例：同一段扫描逻辑必须能揪出注入的 `powershell` 调用（否则①是恒绿的装饰）；
  ③ `display_path` 的「逃逸出 base ⇒ 退回绝对」在**任何 OS** 上都成立（不 skipif，不靠平台分支）。
"""
from __future__ import annotations

import ast
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from utils.path_helper import display_path  # noqa: E402

#: Windows 专属可执行文件（在 Linux runner 上直接 `FileNotFoundError`）。
_WINDOWS_ONLY = {"powershell", "powershell.exe", "pwsh", "cmd", "cmd.exe",
                 "where.exe", "del", "rd", "reg", "schtasks"}

_TEST_FILES = sorted((_ROOT / "tests").glob("test_*.py"))


#: 真的会**起进程**的调用；只有这些的第一个参数才是可执行文件名。
#: ⚠ 第一版没限定函数名，结果把 `re.search('CMD python3 -c …')`（Docker 守卫里的正则）
#: 当成命令调用误报了一处——判据必须按「它真会执行什么」来写，而不是「文本里出现了什么」。
_SHELL_CALLS = {
    ("subprocess", "run"), ("subprocess", "Popen"), ("subprocess", "call"),
    ("subprocess", "check_output"), ("subprocess", "check_call"),
    ("os", "system"), ("os", "popen"),
}
_SHELL_CALL_NAMES = {"run", "Popen", "call", "check_output", "check_call", "system", "popen"}


def _call_exe_name(call: ast.Call):
    """若该调用真的会起进程，返回它的可执行文件名；否则 `None`。"""
    f = call.func
    if isinstance(f, ast.Attribute):
        owner = f.value.id if isinstance(f.value, ast.Name) else None
        if (owner, f.attr) not in _SHELL_CALLS:
            return None
    elif isinstance(f, ast.Name):
        if f.id not in _SHELL_CALL_NAMES:
            return None
    else:
        return None
    if not call.args:
        return None
    a = call.args[0]
    if isinstance(a, ast.List) and a.elts and isinstance(a.elts[0], ast.Constant):
        return a.elts[0].value if isinstance(a.elts[0].value, str) else None
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        parts = a.value.split()
        return parts[0] if parts else None
    return None


def scan_source(src: str, filename: str = "<memory>") -> list:
    """扫描源码，返回「调用了 Windows 专属可执行文件」的行号列表（纯函数，便于反例验证）。"""
    hits = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            exe = _call_exe_name(node)
            if exe and exe.lower() in _WINDOWS_ONLY:
                hits.append(node.lineno)
    return hits


def test_tests_do_not_call_windows_only_executables():
    """① `tests/` 里任何用例都不得调用 Windows 专属程序（CI 的 runner 是 Ubuntu）。"""
    offenders = []
    for p in _TEST_FILES:
        lines = scan_source(p.read_text(encoding="utf-8"), p.name)
        offenders += [f"{p.name}:{ln}" for ln in lines]
    assert not offenders, (
        "测试里出现了 Windows 专属可执行文件调用 ⇒ 只能在开发机上跑，"
        f"CI（ubuntu-latest）会 FileNotFoundError：{offenders}\n"
        "处置：临时文件交给 pytest 的 tmp_path 管，用例自己**不做删除**。"
    )


def test_scanner_actually_catches_the_injected_offender():
    """② 反例：同一段扫描逻辑必须揪出注入的 `powershell` 调用（不然①是恒绿装饰）。

    ⚠ 下面几段字符串只是**喂给 `ast.parse` 的源码样本**，不会被执行。
    """
    bad = "import subprocess\n" \
          "def test_x():\n" \
          "    subprocess.run(['powershell', '-NoProfile', '-Command', 'Remove-Item -Force x'])\n"
    assert scan_source(bad) == [3], "扫描器抓不到 powershell ⇒ ①那条判据不含信息"
    shell_str = "import os\nos.system('cmd /c dir')\n"
    assert scan_source(shell_str) == [2], "扫描器抓不到 shell 字符串形态"
    # 必须**不误报**：注释/文档里提到 powershell 不算（第一版用文本判据就是栽在这上面）
    harmless = "# 前一版用 powershell 删文件，CI 上会炸\ndef f():\n    return 1\n"
    assert scan_source(harmless) == [], "注释里的 powershell 被当成调用 ⇒ 文本判据复发"
    # 非 Windows 专属的程序也不该报
    assert scan_source("import subprocess\nsubprocess.run(['git', 'status'])\n") == []
    # **不起进程的调用**参数里出现 `cmd` 也不算（第一版误报的就是 Docker 守卫里的正则文本）
    assert scan_source("import re\nre.search('CMD python3 -c hello', s)\n") == [], \
        "正则/普通函数的字符串参数被当成命令 ⇒ 判据太宽，会把守卫自己报红"


def test_escaped_display_path_falls_back_to_absolute_on_any_os():
    """③ 路径逃逸出 base ⇒ 退回绝对路径，**两个 OS 同答案**（不靠 skipif 蒙过去）。

    ⚠ 比较时统一分隔符：Windows 上 `Path("/elsewhere/x").as` 字符串会被规范化成反斜杠，
    那是 pathlib 的既有行为（分隔符与 OS 一致），**不是**本条要钉的点；
    本条钉的是「不许出现 `../…` 这种逃逸出 base 的展示路径」。
    """
    out = display_path("/elsewhere/somewhere/baseline.json", "/base").replace("\\", "/")
    assert out == "/elsewhere/somewhere/baseline.json", (
        f"POSIX 上 relpath 不抛 ValueError 而是给出 `../…` 逃逸路径；"
        f"按 display_path 自述的契约应退回绝对路径，实得 {out!r}"
    )
    assert not out.startswith("..")
    # 同 base 之下仍给相对路径（别把功能退化成「一律绝对」，那是另一种难读）
    assert display_path("/base/b/c.json", "/base").replace("\\", "/") == "b/c.json"


def test_display_path_still_returns_relative_for_nested_paths(tmp_path):
    """③b 与 OS 无关的正例：真在 base 下面的路径，仍要相对（守住「别退化成绝对」）。"""
    base = tmp_path / "a"
    base.mkdir()
    target = base / "b" / "c.json"
    assert display_path(target, base) == str(pathlib.Path("b") / "c.json")
