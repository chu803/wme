# -*- coding: utf-8 -*-
"""
阶段 248 守卫 · conftest `_isolate_config_globals` 的**兜底有效性**。

背景
----
248 的**运行期检测器**抓到：`test_tab_settings_audit129` 调 `apply_superres_config()`
（该函数会写 `config` 全局量）却不还原 ⇒ `REAL_SUPERRES_SCALE/BACKEND` 跨用例泄漏；
同批还有 `test_settings_superres` 的同类写法。逐点修完后，另加 **conftest autouse
夹具** `_isolate_config_globals`（快照→还原 config 全部大写属性）作**结构性防护**，
与既有 `_isolate_i18n_language` 同款。

本守卫做什么
------------
用一个**故意泄漏**的用例 + 一个**后置断言**用例，验证夹具确实把 config 还原了：
  · 夹具**生效** ⇒ 后置断言通过（本文件绿）；
  · 夹具**被删/改坏** ⇒ 后置断言**变红**（本守卫即报警）。

⚠ 顺序依赖说明（有意为之且可接受）
--------------------------------
本守卫的判据「上一用例泄漏 ⇒ 本用例看到干净值」**天然依赖执行顺序**。
`pytest` 保证**同一文件内按定义顺序**执行，故 `test_01`（泄漏）必在
`test_02`（断言）之前。若用 `-k` 只选 `test_02`，它看到默认值、**平凡通过**——
即守卫在「未覆盖」时**退化但不误报**（这是可接受的降级，不是哑巴：
只要两个用例都跑，夹具坏掉就一定红）。

（对照：阶段 245/246 批评过「跨文件靠文件名字母序」的顺序依赖——那种**不可靠**，
因为文件顺序受外部因素影响；本文件内的定义顺序是 pytest 的**契约**，可靠。）
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402

#: 故意用的哨兵值（远大于合法范围，确保不可能与真实默认值撞车）
_SENTINEL_INT = 999999
_SENTINEL_STR = "ZZZ-248-LEAK-SENTINEL"


def test_01_deliberately_leak_config_without_restore():
    """**故意**直接写 config 全局量且**不还原**（模拟「忘记还原」的用例）。

    本用例**不是**在测产品代码，而是**布场**：给下一个用例留一个「若夹具失效
    便会观察到」的污染。
    """
    config.REAL_SUPERRES_SCALE = _SENTINEL_INT
    config.REAL_SUPERRES_BACKEND = _SENTINEL_STR
    # 只断言「确实改了」（保证布场成功），不断言别的
    assert config.REAL_SUPERRES_SCALE == _SENTINEL_INT
    assert config.REAL_SUPERRES_BACKEND == _SENTINEL_STR


def test_02_fixture_restored_config_between_cases():
    """**后置断言**：上一个用例的污染必须已被 autouse 夹具还原。

    若 `conftest._isolate_config_globals` 被删除 / `autouse` 被关掉 / 还原逻辑写坏，
    本用例会看到哨兵值 ⇒ **变红报警**（这正是本守卫存在的意义）。
    """
    assert config.REAL_SUPERRES_SCALE != _SENTINEL_INT, (
        f"config.REAL_SUPERRES_SCALE 仍是被上一个用例污染的值 {_SENTINEL_INT} —— "
        f"conftest 的 `_isolate_config_globals` 夹具未生效（autouse 被关？还原逻辑坏？）"
        f"⇒ 该类「忘记还原 config」的污染会跨用例泄漏（阶段 248 实测过真实案例）")
    assert config.REAL_SUPERRES_BACKEND != _SENTINEL_STR, (
        f"config.REAL_SUPERRES_BACKEND 仍是被污染的值 {_SENTINEL_STR!r} —— "
        f"同上，夹具未还原")