# -*- coding: utf-8 -*-
"""阶段 336 · 「取消」不能对着空队列声称自己发了信号。

被修掉的缺陷：`_CURRENT_QUEUE` 在批跑开始时赋值（`ui/tab_batch.py`），**从不置回 None**，
而 `cancel_batch()` 只判 `is not None` ⇒ 批跑结束后按「取消」按钮或快捷键 Shift+C，
仍然回一句「⏹ 已发送取消信号，未开始的任务将不再处理…」——**这是对外可见的假陈述**：
队列里已经没有 pending/running 的任务了，什么都没被取消。

修法两处一起收：跑完在 `finally` 里清空 `_CURRENT_QUEUE`；`cancel_batch()` 再按队列**真实状态**
（pending + running）判断，用既有 `cancel_none` 文案说真话（不新增 i18n 键）。
"""
from __future__ import annotations

import pytest

import ui.tab_batch as tb
from services.queue_worker import BatchQueue, TaskStatus


def test_cancel_before_any_run_says_nothing_is_running():
    """还没跑过任何批次 ⇒ 必须是「没有正在运行的批量任务」，不能声称发了信号。"""
    tb._CURRENT_QUEUE = None
    got = tb.cancel_batch()
    assert got == tb.t("batch.msg.cancel_none"), got
    assert got != tb.t("batch.msg.cancel_done")


def test_cancel_on_drained_queue_does_not_claim_a_signal(monkeypatch):
    """**这条就是被修掉的**：队列句柄还在（跑完没清）但已经没有待处理任务。"""
    q = BatchQueue()
    q.submit("a.png", "/tmp/a.png")
    for _ in q.run(lambda task, on_progress: None):   # `run` 是生成器，必须消费才会真跑
        pass
    s = q.summary()
    assert s["pending"] == 0 and s["running"] == 0, s
    called = []
    monkeypatch.setattr(q, "cancel", lambda: called.append(True))
    monkeypatch.setattr(tb, "_CURRENT_QUEUE", q)

    got = tb.cancel_batch()
    assert got == tb.t("batch.msg.cancel_none"), f"空队列却回了「已发送取消信号」：{got}"
    assert not called, "没有任务可取消时不该调 cancel()"


def test_cancel_on_active_queue_really_cancels(monkeypatch):
    """反向锚：真有 pending 任务时必须**真的取消**并如实回报（别把正向行为改坏）。

    注：`cancel()` 只置队列级标志，任务状态要等 `run()` 消费时才改成 CANCELLED，
    所以这里断言「标志已生效 + 文案是已发送」，不去断言 `summary()['cancelled']`。
    """
    q = BatchQueue()
    q.submit("a.png", "/tmp/a.png")
    q.submit("b.png", "/tmp/b.png")
    monkeypatch.setattr(tb, "_CURRENT_QUEUE", q)

    got = tb.cancel_batch()
    assert got == tb.t("batch.msg.cancel_done"), got
    assert q.is_cancelled() is True, "cancel() 必须真的被调用"

    # 取消之后把队列跑完（剩下的会被标记 CANCELLED）⇒ 再按一次必须改口，
    # 不能连着说两句「已发送取消信号」。
    for _ in q.run(lambda task, on_progress: None):
        pass
    assert tb.cancel_batch() == tb.t("batch.msg.cancel_none")


def test_current_queue_is_cleared_when_the_generator_finishes():
    """跑完（含异常/前端提前关闭）必须把 `_CURRENT_QUEUE` 置回 None，
    否则 `cancel_batch` 的 `is not None` 判据又会退化成假陈述。"""
    import inspect
    src = inspect.getsource(tb.run_batch)
    assert "finally:" in src, "生成器没有 finally ⇒ 队列句柄泄漏到下一次取消"
    tail = src.split("finally:")[-1]
    assert "_CURRENT_QUEUE = None" in tail, f"finally 里没有清空队列：{tail[:200]}"


def test_cancel_messages_stay_translated():
    """两条文案都必须走 `t()`（不能把假陈述换成另一句硬编码中文）。"""
    from utils.i18n import t as _t
    for key in ("batch.msg.cancel_none", "batch.msg.cancel_done"):
        zh = _t(key)
        assert zh and zh != key, f"{key} 缺 zh-CN 文案"
    _t("batch.msg.cancel_none")
    _t("batch.msg.cancel_done")
