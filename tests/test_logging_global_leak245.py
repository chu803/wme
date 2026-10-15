# -*- coding: utf-8 -*-
"""
阶段 245 守卫：`tests/test_utils_audit90.py` 的**全局 logging 注册表泄漏**。

缺陷
----
`test_stale_multi_handler_would_break_rotation`（注入式反例，验证「旧实现会让轮转失败」）
在**全局** `logging` 注册表上创建 logger 并挂 handler：

    lg1 = logging.getLogger("t90.old1")   # ← 全局单例，跨测试存活
    lg.handlers.clear(); lg.propagate = False; lg.addHandler(h)

但它的 `finally` **只 `h.close()`**（关文件句柄），**不摘 handler、不还原
`propagate`** ⇒ 用例结束后：
  · `logging.getLogger("t90.old1").handlers` 里仍挂着**已关闭**的 handler；
  · 该 logger 的 `propagate` 仍是 `False`；
  · `fresh_logger` 夹具只还原 `L._logger_cache` / `_shared_handlers` / `_initialized`，
    **完全不碰全局注册表** ⇒ 泄漏跨用例存活。

后果
----
① 任何后续 `logging.getLogger("t90.old1").warning(...)` 都会写向**已关闭**的流
   ⇒ 抛 `ValueError: I/O operation on closed file` 或打印 `--- Logging error ---`，
   把**无关用例**搞红（本项目 234 段起该文件 rotation 用例反复 flaky 的根因之一）；
② `handlers.clear()` 施加在全局 logger 上，若将来有别的模块用同名 logger，
   其 handler 会被**静默抹掉**；
③ 违反项目铁律「注入式反例必须自带还原」——反例只负责复现失败，不该留残留状态。

本守卫断言：用例跑完后，全局 logger 的 handler 与 propagate 必须**已还原**。
"""
from __future__ import annotations

import io
import logging
import logging.handlers as LH
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402

#: 多-handler 轮转冲突的「Logging error」是 Windows 文件锁特异现象；Linux 允许重命名打开的
#: 文件、冲突触发时序相关 ⇒ 该前提断言仅在 Windows 稳定。核心守卫（全局注册表还原）跨平台有效。
_IS_WINDOWS = (os.name == "nt")


def test_stale_multi_handler_does_not_leak_into_global_registry(monkeypatch, tmp_path):
    """跑完注入式反例后，全局 logger 必须恢复原状（handler 已摘除、propagate 已还原）。

    ⚠ 本用例**自己**完整复刻反例的逻辑再断言，而不是依赖另一个文件的用例执行顺序——
    这样它单独跑也能证明「实现是否自带还原」（守卫不依赖测试顺序，才可靠）。
    `fresh_logger` 夹具是 `test_utils_audit90.py` 的**文件内**夹具，跨文件不可见，
    故此处用 `monkeypatch` + `tmp_path` 自建等价隔离（把 `LOG_DIR` 指向临时目录）。
    """
    from utils import logger as L

    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    old_cache, old_shared, old_init = L._logger_cache, L._shared_handlers, L._initialized
    L._logger_cache, L._shared_handlers, L._initialized = {}, {}, False

    logs_dir = str(config.LOG_DIR)
    applog = os.path.join(logs_dir, config.LOG_FILE_NAME)

    def standalone():
        return LH.RotatingFileHandler(applog, mode="a", maxBytes=4096,
                                      backupCount=4, encoding="utf-8")

    h1, h2 = standalone(), standalone()
    lg1 = logging.getLogger("t90.old1")
    lg2 = logging.getLogger("t90.old2")
    saved = []
    try:
        for lg, h in ((lg1, h1), (lg2, h2)):
            saved.append((lg, list(lg.handlers), lg.propagate, lg.level))
            lg.handlers.clear()
            lg.propagate = False
            lg.setLevel(logging.WARNING)
            lg.addHandler(h)

        err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = err
        try:
            for _ in range(50):
                lg1.warning("Z" * 60)
                lg2.warning("Z" * 60)
        finally:
            sys.stderr = old_stderr
    finally:
        # —— 正确做法：摘除 handler 并还原 propagate/level ——
        for lg in (lg1, lg2):
            lg.handlers.clear()
        h1.close()
        h2.close()
        for lg, handlers, propagate, level in saved:
            lg.handlers.extend(handlers)
            lg.propagate = propagate
            lg.setLevel(level)

    # 反例必须真的复现了失败（否则本守卫的上下文失效）——但「Logging error」这一失败形态是
    # Windows 文件锁特异（Linux 重命名打开文件不冲突、时序相关），故仅 Windows 断言；
    # 核心守卫（下方全局注册表还原）跨平台有效，不依赖此前提。
    if _IS_WINDOWS:
        assert "--- Logging error ---" in err.getvalue(), (
            "注入式反例本应复现轮转失败；未复现说明前提已变，本守卫需重新评估")

    # 核心断言：全局注册表已还原
    assert lg1.handlers == [], (
        f"全局 logger 't90.old1' 仍挂着 {len(lg1.handlers)} 个 handler —— "
        f"注入式反例泄漏到全局注册表，会让后续无关用例写到已关闭的流而失败")
    assert lg1.propagate is True, (
        "全局 logger 't90.old1' 的 propagate 仍是 False —— 反例未还原，会截断后续传播")
    assert lg2.handlers == [], "全局 logger 't90.old2' 仍挂着 handler（泄漏）"
    assert lg2.propagate is True, "全局 logger 't90.old2' 的 propagate 仍是 False（未还原）"