# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 139 守卫测试：ui/tab_history 工具函数与自检真实性
------------------------------------------------------------------------------
 背景（docs/139）：
   tab_history 原无 __main__ 自检，工具函数（_fmt_dur 边界 / _format_time /
   _record_detail / build_disk_usage_md）从未被任何自动化验证（与 138
   components 同模式：守卫缺失）。
   修复：模块尾部加 _run_selfcheck() + __main__ SystemExit 非零守卫；
   本文件固化边界契约并验证子进程自检。
 注入验证：破坏 _fmt_dur 边界后本文件 + legacy 守卫必须红。
==============================================================================
"""

from __future__ import annotations

import os
import subprocess
import sys

from services.cache_service import HistoryRecord
from ui.tab_history import (
    _CACHE,
    _fmt_bytes,
    _fmt_dur,
    _format_time,
    _record_detail,
    build_disk_usage_md,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------------------
# 边界契约（与 __main__ 自检一致）
# ------------------------------------------------------------------------------
def test_fmt_dur_boundaries():
    assert _fmt_dur(0.0) == "0.0 秒"
    assert _fmt_dur(59.9) == "59.9 秒"
    assert _fmt_dur(60.0) == "1 分 0 秒"
    assert _fmt_dur(125.0) == "2 分 5 秒"
    assert _fmt_dur(3661.5).startswith("61 分 ")


def test_format_time_format():
    s = _format_time(1700000000.0)
    assert len(s) == 19 and s[4] == "-" and s[7] == "-" and s[10] == " " \
        and s[13] == ":" and s[16] == ":", f"_format_time 格式异常: {s!r}"


def test_fmt_bytes_basic():
    assert _fmt_bytes(0) == "0 B"
    assert _fmt_bytes(1024) == "1.00 KB"


def test_record_detail_synthetic():
    rec = HistoryRecord(
        id="t1", ts=1700000000.0, original_name="a.png",
        original_path=None, result_path=None, thumb_path=None,
        mode="auto", engine="lama", status="success",
        params="", file_size=1234,
    )
    d = _record_detail(rec)
    assert "✅" in d and "a.png" in d and "lama" in d and "auto" in d


def test_build_disk_usage_md_with_stub(monkeypatch):
    class _StubCache:
        def get_stats(self):
            return {
                "total_bytes": 2048, "total_count": 3,
                "by_kind": {"image": {"count": 3, "bytes": 2048}},
            }

    monkeypatch.setattr("ui.tab_history._CACHE", _StubCache())
    md = build_disk_usage_md()
    assert "1.00 KB" in md or "2.00 KB" in md


def test_selfcheck_subprocess_green():
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "ui", "tab_history.py")],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert r.returncode == 0, f"自检失败：{(r.stderr or r.stdout or '')[-400:]}"
    assert "自检通过" in r.stdout


def test_module_cache_unaffected_after_subprocess():
    # 守卫 import 副作用不能破坏模块级 _CACHE（子进程是独立进程，本进程保护）
    assert _CACHE is not None