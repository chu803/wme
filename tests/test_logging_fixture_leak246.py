# -*- coding: utf-8 -*-
"""
阶段 246 守卫 · **夹具级**全局 handler 泄漏（`tests/test_utils_audit90.py::fresh_logger`）

背景（承接 245）
--------------
245 修了「注入式反例往全局 logging 注册表挂 handler 不还原」的**单点**泄漏。
246 深挖发现：那只是**症状**。真正的根因在**夹具** `fresh_logger` 的 teardown：

    # 修复前
    for h in list(L._shared_handlers.values()):
        h.close()          # ← 只关句柄，**从不** lg.removeHandler(h)

于是**每个**用 `fresh_logger.get_logger(...)` 建过 logger 的用例，都把一批
**已关闭**的 handler 留在**全局** `logging` 注册表里跨用例存活。
246 实测（修复前）：跑完 `test_utils_audit90.py` 后，**24 个** `t90.*` logger
每个挂着 **5 个已关闭的 handler**。

危害：后续任何用例用到同名 logger 就会写向已关闭的流 ⇒
`ValueError: I/O operation on closed file` / `--- Logging error ---` ⇒ **假红**。

本守卫的设计（刻意规避「顺序依赖」陷阱）
----------------------------------------
第一版守卫名叫 `test_zz_probe246.py`——靠**文件名字母序排到被测文件之后**来观测。
这**是错的**：`test_logging_*` < `test_utils_*`，它实际排在**前面**（idx 84 vs 180），
根本观测不到任何东西 ⇒ 守卫恒绿 = 哑巴。
**教训（245 已立，此处差点重犯）**：守卫**不得**依赖执行顺序。

故本守卫改为**自带被测逻辑**：内联复刻「建 logger → 用例体 → 夹具 teardown」这一
完整循环，然后断言 teardown 后全局 logger 上**不残留本项目的已关闭 handler**。
这样它**单独跑、任意顺序跑都有效**。

⚠ 判定只针对**被测代码**留下的残留：pytest 自己的捕获 handler
（`_pytest.logging` 等模块的 `LogCaptureHandler`）由 pytest 插件在会话级管理，
摘除时机不受我们控制，**不属缺陷**——否则守卫会给出无法满足的假红。
"""
from __future__ import annotations

import io
import logging
import os
import pathlib
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402


def _is_pytest_owned(h) -> bool:
    mod = type(h).__module__ or ""
    return mod.startswith("_pytest") or mod.startswith("pytest")


def _leaked(name: str) -> list:
    """返回该全局 logger 上「本项目的、且已关闭的」handler。"""
    lg = logging.Logger.manager.loggerDict.get(name)
    if not isinstance(lg, logging.Logger):
        return []
    out = []
    for h in lg.handlers:
        if _is_pytest_owned(h):
            continue
        closed = getattr(h, "_closed", False) or getattr(
            getattr(h, "stream", None), "closed", False)
        if closed:
            out.append(type(h).__module__ + "." + type(h).__name__)
    return out


def test_fixture_teardown_detaches_handlers_from_global_loggers():
    """夹具 teardown 必须「先摘除、后关闭」，不得把关闭的 handler 留在全局注册表。

    内联复刻 `fresh_logger` 的完整生命周期（建 → 用 → teardown），
    因此**不依赖任何其它文件的执行顺序**，单独跑即可判定。
    """
    from utils import logger as L

    probe_name = "t246.fixtureprobe"
    pre = logging.Logger.manager.loggerDict.get(probe_name)
    if isinstance(pre, logging.Logger):
        pre.handlers.clear()          # 清场：确保起点干净

    tmp = tempfile.mkdtemp(prefix="t246_fixture_")
    old_dir, old_cache = config.LOG_DIR, L._logger_cache
    old_shared, old_init = L._shared_handlers, L._initialized
    # ⚠ `config.LOG_DIR` 必须是 **Path**（`utils.logger` 内部用 `LOG_DIR / LOG_FILE_NAME`）——
    #   真实夹具传的是 pytest 的 `tmp_path`（Path）。若传 str 会TypeError。
    config.LOG_DIR = pathlib.Path(tmp)
    L._logger_cache, L._shared_handlers, L._initialized = {}, {}, False

    try:
        # ——「用例体」：通过夹具同款路径建一个 logger ——
        lg = L.get_logger(probe_name)
        assert lg is logging.getLogger(probe_name), (
            "前提失效：get_logger 未返回全局注册表里的同一个 logger")
        assert len(lg.handlers) > 0, (
            "前提失效：夹具路径没有挂任何 handler，无从测泄漏")

        # ——「teardown」：与 tests/test_utils_audit90.py 的夹具保持一致 ——
        for l in list(L._logger_cache.values()):
            try:
                for h in list(l.handlers):
                    l.removeHandler(h)
            except Exception:  # noqa: BLE001
                pass
        for h in list(L._shared_handlers.values()):
            try:
                h.close()
            except Exception:
                pass
        L._shared_handlers, L._logger_cache = old_shared, old_cache
        L._initialized = old_init
    finally:
        config.LOG_DIR = old_dir

    leaked = _leaked(probe_name)
    assert not leaked, (
        f"夹具 teardown 后，全局 logger {probe_name!r} 仍残留本项目已关闭的 handler："
        f"{leaked}\n  ⇒ 根因：teardown 只 close() 不 removeHandler()。"
        f"必须「先摘除、后关闭」——只 close 会把关闭的 handler 留在全局注册表，"
        f"跨用例存活，后续用例写向已关闭的流而假红。"
    )
    lg.handlers.clear()               # 守卫自己不留污染


def test_detach_then_close_is_the_required_order():
    """判据自检：证明「只 close 不摘除」确实会留下残留（否则上面那条守卫是哑巴）。"""
    name = "t246.ordercheck"
    pre = logging.Logger.manager.loggerDict.get(name)
    if isinstance(pre, logging.Logger):
        pre.handlers.clear()

    tmp = tempfile.mkdtemp(prefix="t246_order_")
    applog = os.path.join(tmp, "app.log")
    lg = logging.getLogger(name)
    lg.setLevel(logging.WARNING)
    h = logging.FileHandler(applog, encoding="utf-8")
    lg.addHandler(h)

    # 错误的 teardown：只 close
    h.close()
    assert _leaked(name), (
        "前提失效：只 close() 未留下已关闭 handler——本环境 logging 行为与假设不同，"
        "夹具泄漏守卫需重新评估")

    # 正确的 teardown：先摘除
    lg.removeHandler(h)
    assert not _leaked(name), (
        "前提失效：removeHandler 后仍判为泄漏——守卫逻辑本身有误")
    lg.handlers.clear()


def test_probe_teardown_order_matches_the_real_fixture():
    """守卫内的 teardown 复刻必须与**被测夹具真实代码**一致，否则守卫测的是另一份东西。

    这是防「守卫与实现漂移」的自检：从 `tests/test_utils_audit90.py` 的源码里
    读出 `fresh_logger` 的 teardown 片段，断言它确实含 `removeHandler`
    （而非只有 `close()`）。实现若退化回「只 close」，本用例会红。
    """
    src_path = os.path.join(_ROOT, "tests", "test_utils_audit90.py")
    src = open(src_path, encoding="utf-8").read()
    # 取夹具定义片段（从 def fresh_logger 到下一个顶层 def/注释块）
    start = src.find("def fresh_logger(")
    assert start != -1, "前提失效：找不到 fresh_logger 夹具定义"
    end = src.find("\ndef ", start + 1)
    fixture_src = src[start:end if end != -1 else len(src)]

    assert "removeHandler" in fixture_src, (
        "fresh_logger 夹具的 teardown 缺少 removeHandler —— "
        "它只 close() 不摘除，会把已关闭的 handler 留在全局 logging 注册表"
        f"（阶段 246 实测：修复前该文件跑完残留 24 个 t90.* logger × 5 个关闭 handler）")
    assert ".close()" in fixture_src, (
        "前提失效：夹具 teardown 里连 close() 都没有，与文档描述不符，请重新评估")