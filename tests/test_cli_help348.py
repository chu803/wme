# -*- coding: utf-8 -*-
"""阶段 348 · `cli.py --help` 必须能跑完（argparse 的 help 文本里不能出现裸 `%`）。

缺陷：`--preserve-untouched` 的 help 文本里有「实测误伤 50%→约 1%」⇒ argparse 对 help 做
**% 风格**的格式化 ⇒ `ValueError: unsupported format character '?' (0x2192)` ⇒
**`wme-cli --help` 直接崩**（用户第一次试命令就撞上，而不是用起来才发现）。

判据来源：argparse 的 `help` 会经过 `%` 展开 ⇒ 文本里的字面百分号必须写成 `%%`。
守卫用**真跑一次 `--help`**来验，而不是去 grep 源码（grep 找不到「哪些 help 里有 %」这件事的真凶：
它在 `add_argument` 的 kwarg 里，不在 print 里）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_help() -> subprocess.CompletedProcess:
    # ⚠ 必须显式 `encoding="utf-8"`：子进程（cli.py 自带 force_utf8_stdio）输出的是 UTF-8，
    # 而父进程默认按 **locale(中文 Windows = GBK)** 解码 ⇒ 读线程抛 UnicodeDecodeError ⇒
    # `proc.stdout` 直接变成 **None**（实测踩过）。这正是阶段 331 修的那类问题的翻版。
    return subprocess.run(
        [sys.executable, str(ROOT / "cli.py"), "--help"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )


def test_cli_help_exits_zero():
    """`--help` 必须正常退出并打印 usage（第一次上手命令就崩是不可接受的）。"""
    proc = _run_help()
    assert proc.returncode == 0, (
        f"--help 退出码 {proc.returncode}\nstdout:\n{proc.stdout[-1500:]}\n"
        f"stderr:\n{proc.stderr[-1500:]}")
    assert "usage" in proc.stdout.lower(), proc.stdout[-500:]


def test_help_text_keeps_percent_readable():
    """`%%` 在 argparse 里渲染回 `%` ⇒ 用户看到的仍是「50%→约 1%」（别为了不崩而把数字删掉）。"""
    out = _run_help().stdout
    assert "50%" in out, "help 文本里的百分比应正常显示（50%% → 50%）"
    assert "→" in out, "箭头也要在"
