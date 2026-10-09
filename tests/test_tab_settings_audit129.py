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
    """挂捕获 handler 并把级别降到 DEBUG。

    ⚠ **阶段 247 修全局级别泄漏**：原实现只`addHandler` + 调用方 `removeHandler`
      （handler 确实无泄漏），但 `lg.setLevel(logging.DEBUG)` **从不还原**
      ⇒ `ui.tab_settings`（产品真实使用的 logger 名）的级别被永久留在 DEBUG，
      同进程后续用例全部输出 DEBUG 噪声，并可能**淹没** WARN 记录使断言失真。
      现返回旧级别，由调用方在 `finally` 里 `setLevel` 还原。
    """
    h = _Capture()
    lg = logging.getLogger("ui.tab_settings")
    prev_level = lg.level          # ← 阶段 247：先存旧级别，供调用方还原
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    return h, lg, prev_level


def _sr_snapshot():
    """阶段 248：`apply_superres_config` 会**写 config 全局量**，调用方必须还原。

    实测（248 运行期检测器）：本文件曾把 `REAL_SUPERRES_SCALE` 2→4、
    `REAL_SUPERRES_BACKEND` esrgan→auto 泄漏给后续用例。返回快照供 `finally` 还原。
    """
    return (config.REAL_SUPERRES_ENABLED, config.REAL_SUPERRES_SCALE,
            config.REAL_SUPERRES_BACKEND)


def _sr_restore(snap):
    (config.REAL_SUPERRES_ENABLED,
     config.REAL_SUPERRES_SCALE,
     config.REAL_SUPERRES_BACKEND) = snap


def test_d2_save_failure_is_observable(monkeypatch):
    """save_user_overrides 返回 False 时必须留下 WARNING 痕迹。"""
    monkeypatch.setattr(config, "save_user_overrides", lambda patch: False)
    h, lg, prev_level = _capture_logs()
    sr = _sr_snapshot()              # 阶段 248：apply_superres_config 会写 config
    try:
        ts.apply_superres_config(True, 4, "auto", True)
    finally:
        lg.removeHandler(h)
        lg.setLevel(prev_level)      # 阶段 247：还原全局级别（handler 与 level 都还原）
        _sr_restore(sr)              # 阶段 248：还原 apply_superres_config 写入的 config
    assert any("未生效" in m for m in h.msgs), h.msgs


def test_d2_save_success_no_warning(monkeypatch):
    """save_user_overrides 成功时不应产生「未生效」告警。"""
    monkeypatch.setattr(config, "save_user_overrides", lambda patch: True)
    h, lg, prev_level = _capture_logs()
    sr = _sr_snapshot()              # 阶段 248：同 test_d2_save_failure_is_observable
    try:
        ts.apply_superres_config(True, 4, "auto", True)
    finally:
        lg.removeHandler(h)
        lg.setLevel(prev_level)      # 阶段 247：还原全局级别
        _sr_restore(sr)              # 阶段 248：还原 apply_superres_config 写入的 config
    assert not any("未生效" in m for m in h.msgs), h.msgs


def test_d2_no_save_when_checkbox_off(monkeypatch):
    """未勾选「保存为默认」时不得调用 save_user_overrides。"""
    calls = {"n": 0}

    def _boom(patch):
        calls["n"] += 1
        return True

    monkeypatch.setattr(config, "save_user_overrides", _boom)
    sr = _sr_snapshot()               # 阶段 248：本用例同样会写 config（原**完全无还原**）
    try:
        ts.apply_superres_config(True, 4, "auto", False)
    finally:
        _sr_restore(sr)
    assert calls["n"] == 0


# ==============================================================================
# 阶段 247 · 全局**级别**泄漏守卫
# ==============================================================================
# 守卫设计迭代了四版，每次都被「注入式反向验证」打回（LESSONS #263/#264）：
#   v1 外部文件靠字母序观测跑完后的状态 => 顺序依赖（246 已批）。
#   v2 文件内「布场 -> 后置断言」=> **布场者自己就是泄漏源**，断言必然失败。
#   v3 文件内「自证配对写法」=> **哑巴**：注入删除真实调用方的还原后仍全绿
#      （只验证了写法能工作，没验证真实调用方是否配对）。
#   v4（采用）：直接断言**真实调用点的源码**含还原 —— 唯一能钉死「真实调用方是否
#      配对」且**不依赖顺序、不自造污染**的做法。
# ==============================================================================
_REAL_CAPTURE_CALLERS = ("test_d2_save_failure_is_observable",
                        "test_d2_save_success_no_warning")

_NEXT_DEF = chr(10) + "def "


def _this_file_src() -> str:
    import os
    return open(os.path.abspath(__file__), encoding="utf-8").read()


def test_real_capture_logs_callers_restore_level():
    """**钉死真实调用点**：每个用 _capture_logs() 的用例都必须还原级别。

    读本文件源码、逐个点名断言调用点含 setLevel(prev_level)。理由：
    运行期观测（含后置断言）**覆盖不到「漏还原的调用点」**——漏还原的那次调用若
    发生在其它用例之前，后置断言早已跑过；而这里逐个点名，漏一个即红。
    """
    src = _this_file_src()
    missing = []
    for fn in _REAL_CAPTURE_CALLERS:
        start = src.find("def " + fn + "(")
        assert start != -1, f"前提失效：找不到调用点 {fn}"
        end = src.find(_NEXT_DEF, start + 1)
        body = src[start:end if end != -1 else len(src)]
        if "_capture_logs()" not in body:
            continue                      # 该用例未用捕获，不在本守卫范围
        if "setLevel(prev_level)" not in body:
            missing.append(fn)
    assert not missing, (
        "以下真实调用点用了 _capture_logs() 却没有在 finally 里"
        " setLevel(prev_level) 还原全局级别：" + repr(missing) +
        "\n  => _capture_logs 会把 'ui.tab_settings' 级别降到 DEBUG；"
        "调用方不还原 => 同进程后续用例输出 DEBUG 噪声，"
        "并可能淹没 WARN 记录使「无告警」类断言失真（阶段 247 实测）")


def test_capture_logs_returns_previous_level():
    """前提守卫：_capture_logs() 必须返回**旧级别**，否则调用方无从还原。"""
    lg = logging.getLogger("ui.tab_settings")
    sentinel = lg.level
    h, lg2, prev = _capture_logs()
    try:
        assert prev == sentinel, (
            f"_capture_logs 返回的旧级别 {prev} != 进入前的 {sentinel}")
        assert lg2 is lg, "前提失效：_capture_logs 未操作同一个全局 logger"
    finally:
        lg2.removeHandler(h)
        lg2.setLevel(prev)
    assert lg.level == sentinel, "还原后级别仍与进入前不一致"
