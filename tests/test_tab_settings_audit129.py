# -*- coding: utf-8 -*-
"""Stage 129 回归守卫：ui/tab_settings 两处真缺陷（D1/D2）。

D1：`purge_orphans` 用 `if removed and removed < want` 判 partial —— `removed and`
    把「want>0 但一个都没删掉」（全部被占用/权限拒绝）短路进 ok 分支，报
    「已清理 0 个」，掩盖「0 成功 / N 失败」。修复：改为纯数量比较 `removed < want`。
D2：三处「保存为默认」用 `try: config.save_user_overrides(...) except Exception` 兜住持久化，
    但 `save_user_overrides` **从不 raise**（内部捕获、失败返回 bool）⇒ except 永不触发、
    返回的 False 被丢弃 ⇒ 写盘失败完全无痕。修复：消费返回值，失败即 WARNING。
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config
import ui.tab_settings as ts
from utils.i18n import t


# ==============================================================================
# 基础设施
# ==============================================================================
class _FakeCache:
    """可编程的缓存替身：只暴露 scan_orphans / purge_orphans。"""

    def __init__(self, want_count: int, removed: int, freed: int = 0):
        self._want = want_count
        self._removed = removed
        self._freed = freed

    def scan_orphans(self):
        return {
            "thumbs": {"count": self._want, "bytes": 200, "files": []},
            "uploads": {"count": 0, "bytes": 0, "files": []},
            "__total__": {"count": self._want, "bytes": 200},
        }

    def purge_orphans(self, origins):
        return (self._removed, self._freed)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.msgs: list[str] = []

    def emit(self, record):
        self.msgs.append(record.getMessage())


# ==============================================================================
# D1 · purge_orphans 汇报口径
# ==============================================================================
def test_d1_all_failed_reports_partial_not_ok(monkeypatch):
    """want>0 且 removed==0（全失败）必须报 partial（含失败数），不得报 ok。"""
    monkeypatch.setattr(ts, "_CACHE", _FakeCache(want_count=3, removed=0))
    _, info, _ = ts.purge_orphans(True, False, True)
    assert info == t("settings.disk_clean.partial", count=0, size="0 B", failed=3)
    assert info != t("settings.disk_clean.ok", count=0, size="0 B")


def test_d1_full_success_still_ok(monkeypatch):
    """removed==want（全部成功）仍报 ok（回归：修复不得把成功误报为 partial）。"""
    monkeypatch.setattr(ts, "_CACHE", _FakeCache(want_count=2, removed=2, freed=300))
    _, info, _ = ts.purge_orphans(True, False, True)
    assert info == t("settings.disk_clean.ok", count=2, size="300 B")


def test_d1_zero_want_reports_ok(monkeypatch):
    """want==0 且 removed==0（本就没有孤儿）仍报 ok（不得因 0<0 误判）。"""
    monkeypatch.setattr(ts, "_CACHE", _FakeCache(want_count=0, removed=0))
    _, info, _ = ts.purge_orphans(True, False, True)
    assert info == t("settings.disk_clean.ok", count=0, size="0 B")


# ==============================================================================
# D2 · 「保存为默认」失败必须可观测
# ==============================================================================
def _capture_logs():
    h = _Capture()
    lg = logging.getLogger("ui.tab_settings")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    return h, lg


def test_d2_save_failure_is_observable(monkeypatch):
    """save_user_overrides 返回 False 时必须留下 WARNING 痕迹。"""
    monkeypatch.setattr(config, "save_user_overrides", lambda patch: False)
    h, lg = _capture_logs()
    try:
        ts.apply_superres_config(True, 4, "auto", True)
    finally:
        lg.removeHandler(h)
    assert any("未生效" in m for m in h.msgs), h.msgs


def test_d2_save_success_no_warning(monkeypatch):
    """save_user_overrides 成功时不应产生「未生效」告警。"""
    monkeypatch.setattr(config, "save_user_overrides", lambda patch: True)
    h, lg = _capture_logs()
    try:
        ts.apply_superres_config(True, 4, "auto", True)
    finally:
        lg.removeHandler(h)
    assert not any("未生效" in m for m in h.msgs), h.msgs


def test_d2_no_save_when_checkbox_off(monkeypatch):
    """未勾选「保存为默认」时不得调用 save_user_overrides。"""
    calls = {"n": 0}

    def _boom(patch):
        calls["n"] += 1
        return True

    monkeypatch.setattr(config, "save_user_overrides", _boom)
    ts.apply_superres_config(True, 4, "auto", False)
    assert calls["n"] == 0
