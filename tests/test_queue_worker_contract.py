"""阶段 161 · `services/queue_worker.BatchQueue` 未覆盖面契约守卫。

背景
----
`services/queue_worker` 是审计排名里**比值最高**（264 代码 / 14 引用测试）的模块，既有覆盖
（`tests/test_services_audit.py` 的 B1/B2）只钉了「取消要 yield / TaskCancelledError 不重试」。
下方这些契约此前**零测试**，阶段 161 探针在此发现一处实质缺陷：

- `__init__` 里 `max_concurrency` / `max_size` / `timeout` 用 `or` 判定 ⇒ 调用方**显式传入
  的 0 被静默吞掉、回退 config**（而 `retry` 用了正确的 `is not None` 写法 —— 同一风险面两种写法）。
- 更实质：`retry` **无下界校验** ⇒ `retry < 0` 时 `attempts = retry + 1 <= 0`，`for` 循环一次都不
  执行 ⇒ 任务**永远停在 RUNNING**，破坏 `run()` 的终态不变量。

修复：四参数统一 `is not None`；并在构造期显式拒绝 `max_concurrency < 1` / `max_size < 1` /
`retry < 0`（显式失败优于静默覆盖或下游崩溃）。

本文件把上述契约 + 此前未测的 summary / reset / 满队列 / 空队列 / 重试耗尽 / 终态不变量钉死。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from services.queue_worker import (  # noqa: E402
    BatchQueue, QueueFullError, TaskStatus,
)

_TERMINAL = (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)


def _ok_proc(t, cb):
    cb(0.5, "半")
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# 1 · 构造期参数契约（阶段 161 修复点）
# --------------------------------------------------------------------------- #
def test_default_reads_config():
    q = BatchQueue()
    assert q.max_concurrency == config.BATCH_CONCURRENCY
    assert q.max_size == config.QUEUE_MAX_SIZE
    assert q.retry == config.QUEUE_TASK_RETRY
    assert q.timeout == config.QUEUE_TASK_TIMEOUT


def test_explicit_zero_is_not_silently_replaced():
    """显式传入的 0 是非法值，必须**显式失败**，不得静默回退 config（旧实现用 `or`）。"""
    with pytest.raises(ValueError):
        BatchQueue(max_concurrency=0)
    with pytest.raises(ValueError):
        BatchQueue(max_size=0)


def test_explicit_retry_zero_is_honored():
    """`retry=0` 合法且必须生效（旧实现此处已用 `is not None`，守卫钉住不被回退）。"""
    q = BatchQueue(retry=0)
    assert q.retry == 0


def test_negative_retry_rejected():
    """`retry < 0` 会让 for 循环不执行 ⇒ 任务卡在 RUNNING ⇒ 必须构造期拒绝。"""
    with pytest.raises(ValueError):
        BatchQueue(retry=-1)


# --------------------------------------------------------------------------- #
# 2 · 终态不变量 + 计数对账
# --------------------------------------------------------------------------- #
def _assert_terminal_and_counts(q, out):
    tasks = q.tasks()
    bad = [t.name for t in tasks if t.status not in _TERMINAL]
    assert not bad, f"run() 完整消费后仍有非终态任务：{bad}"
    s = q.summary()
    assert s["total"] == len(out), "summary.total 应等于 yield 数"
    assert s["running"] == 0 and s["pending"] == 0
    assert s["finished"] == s["done"] + s["failed"] + s["cancelled"]
    assert len(tasks) == s["total"]


def test_terminal_invariant_normal():
    q = BatchQueue(max_concurrency=4, max_size=50, retry=0, timeout=30)
    for i in range(12):
        q.submit(f"n{i}.png", f"/fake/{i}.png")
    out = list(q.run(_ok_proc))
    _assert_terminal_and_counts(q, out)
    assert all(t.status == TaskStatus.DONE for t in out)


def test_terminal_invariant_mid_cancel():
    q = BatchQueue(max_concurrency=1, max_size=50, retry=0, timeout=30)
    for i in range(8):
        q.submit(f"c{i}.png", f"/fake/{i}.png")

    def proc(t, cb):
        if t.name == "c0.png":
            q.cancel()
        return {"status": "ok"}

    out = list(q.run(proc))
    _assert_terminal_and_counts(q, out)
    assert q.count_cancelled() >= 1


def test_cancel_before_run_yields_all():
    q = BatchQueue(max_concurrency=2, max_size=50, retry=0, timeout=30)
    for i in range(5):
        q.submit(f"b{i}.png", f"/fake/{i}.png")
    q.cancel()
    out = list(q.run(_ok_proc))
    _assert_terminal_and_counts(q, out)
    assert len(out) == 5 and all(t.status == TaskStatus.CANCELLED for t in out)


# --------------------------------------------------------------------------- #
# 3 · 满队列 / 空队列 / reset
# --------------------------------------------------------------------------- #
def test_queue_full_raises():
    q = BatchQueue(max_concurrency=1, max_size=3, retry=0, timeout=30)
    for i in range(3):
        q.submit(f"f{i}.png", f"/fake/{i}.png")
    with pytest.raises(QueueFullError):
        q.submit("overflow.png", "/x.png")
    assert q.summary()["total"] == 3


def test_empty_run_yields_nothing():
    q = BatchQueue(max_concurrency=1, max_size=5, retry=0, timeout=30)
    assert list(q.run(_ok_proc)) == []


def test_reset_clears_and_uncancels():
    q = BatchQueue(max_concurrency=1, max_size=5, retry=0, timeout=30)
    q.submit("r.png", "/r.png")
    q.cancel()
    q.reset()
    assert q.summary()["total"] == 0
    assert q.is_cancelled() is False
    assert q.tasks() == []


# --------------------------------------------------------------------------- #
# 4 · 重试耗尽 / summary / to_dict
# --------------------------------------------------------------------------- #
def test_retry_exhaustion_counts():
    q = BatchQueue(max_concurrency=1, max_size=5, retry=2, timeout=30)
    q.submit("x.png", "/x.png")
    calls = []

    def proc(t, cb):
        calls.append(1)
        raise RuntimeError("always fails")

    out = list(q.run(proc))
    assert out[0].status == TaskStatus.FAILED
    assert len(calls) == 3, f"retry=2 应尝试 3 次，实际 {len(calls)}"
    assert out[0].retries == 2


def test_summary_keys_and_progress():
    q = BatchQueue(max_concurrency=2, max_size=5, retry=0, timeout=30)
    for i in range(4):
        q.submit(f"s{i}.png", f"/fake/{i}.png")
    empty = q.summary()
    assert set(empty) == {"total", "done", "failed", "cancelled", "running",
                          "pending", "finished", "progress", "current"}
    assert empty["progress"] == 0.0
    out = list(q.run(_ok_proc))
    s = q.summary()
    assert s["progress"] == 1.0
    assert s["current"] == ""          # 全部结束后无 RUNNING
    assert len(out) == 4


def test_to_dict_excludes_internal_fields():
    q = BatchQueue(max_concurrency=1, max_size=5, retry=0, timeout=30)
    q.submit("t.png", "/t.png")
    out = list(q.run(_ok_proc))
    d = out[0].to_dict()
    assert set(d) == {"id", "name", "path", "status", "progress", "message",
                      "error", "retries"}
    assert d["status"] == "done"        # TaskStatus(str,Enum) → 可 JSON 序列化
    assert "result" not in d and "_cancelled" not in d
