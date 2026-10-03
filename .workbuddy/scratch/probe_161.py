# -*- coding: utf-8 -*-
"""Stage 161 探针：queue_worker 未覆盖面（不变量 / 计数 / reset / 满队列 / or 回退）。

重点量化「run() 完整消费后不得残留 PENDING/RUNNING」这一终态不变量，
以及 summary 计数与 yield 数是否对账。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "D:/work 111/2026-08-03-17-38-51")
import config  # noqa: E402
from services.queue_worker import BatchQueue, TaskStatus, QueueFullError  # noqa: E402


def _terminal_invariant(tag, tasks):
    bad = [(t.name, t.status.value) for t in tasks
           if t.status not in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)]
    print(f"[{tag}] 终态不变量（无 PENDING/RUNNING）: {'OK' if not bad else 'VIOLATION ' + str(bad)}")
    return not bad


def _counts_match_yield(tag, q, out):
    s = q.summary()
    yielded = len(out)
    print(f"[{tag}] summary total={s['total']} done={s['done']} failed={s['failed']} "
          f"cancelled={s['cancelled']} running={s['running']} pending={s['pending']} "
          f"finished={s['finished']} progress={s['progress']:.2f} | yielded={yielded}")
    ok = (s["total"] == yielded) and (s["running"] == 0) and (s["pending"] == 0) \
        and (s["finished"] == s["done"] + s["failed"] + s["cancelled"])
    print(f"[{tag}] 计数对账: {'OK' if ok else 'MISMATCH'}")
    return ok


def test_normal_ok():
    q = BatchQueue(max_concurrency=4, max_size=50, retry=0, timeout=30)
    for i in range(12):
        q.submit(f"n{i}.png", f"/fake/{i}.png")
    out = list(q.run(lambda t, cb: (cb(0.5, "半"), {"status": "ok"})[1]))
    _counts_match_yield("正常", q, out)
    _terminal_invariant("正常", q.tasks())
    print()


def test_mid_cancel_invariant():
    q = BatchQueue(max_concurrency=1, max_size=50, retry=0, timeout=30)
    for i in range(8):
        q.submit(f"c{i}.png", f"/fake/{i}.png")

    def proc(t, cb):
        if t.name == "c0.png":
            q.cancel()
        return {"status": "ok"}

    out = list(q.run(proc))
    _counts_match_yield("中途取消", q, out)
    _terminal_invariant("中途取消", q.tasks())
    print()


def test_cancel_before_run():
    q = BatchQueue(max_concurrency=2, max_size=50, retry=0, timeout=30)
    for i in range(5):
        q.submit(f"b{i}.png", f"/fake/{i}.png")
    q.cancel()
    out = list(q.run(lambda t, cb: {"status": "ok"}))
    _counts_match_yield("入队后取消", q, out)
    _terminal_invariant("入队后取消", q.tasks())
    print()


def test_full_queue():
    q = BatchQueue(max_concurrency=1, max_size=3, retry=0, timeout=30)
    for i in range(3):
        q.submit(f"f{i}.png", f"/fake/{i}.png")
    try:
        q.submit("overflow.png", "/x.png")
        print("[满队列] 未抛 QueueFullError —— 缺陷！")
    except QueueFullError as e:
        print(f"[满队列] 正确抛 QueueFullError: {e}")
    print(f"[满队列] 队列长度={q.summary()['total']}（期望 3）")
    print()


def test_empty_run():
    q = BatchQueue(max_concurrency=1, max_size=5, retry=0, timeout=30)
    out = list(q.run(lambda t, cb: {"status": "ok"}))
    print(f"[空队列] run() 产出 {len(out)} 个（期望 0）")
    print()


def test_reset():
    q = BatchQueue(max_concurrency=1, max_size=5, retry=0, timeout=30)
    q.submit("r.png", "/r.png")
    q.cancel()
    q.reset()
    s = q.summary()
    print(f"[reset] total={s['total']}（期望 0）| is_cancelled={q.is_cancelled()}（期望 False）")
    print()


def test_or_fallback():
    print(f"[or 回退] config.BATCH_CONCURRENCY={config.BATCH_CONCURRENCY} "
          f"QUEUE_MAX_SIZE={config.QUEUE_MAX_SIZE} QUEUE_TASK_TIMEOUT={config.QUEUE_TASK_TIMEOUT}")
    q0 = BatchQueue(max_concurrency=0, max_size=0, timeout=0, retry=0)
    print(f"[or 回退] 传入 max_concurrency=0,max_size=0,timeout=0 → 实际 "
          f"concurrency={q0.max_concurrency} max_size={q0.max_size} timeout={q0.timeout} "
          f"(retry={q0.retry})")
    print("     （若实际值==config 默认 ⇒ 0 被 or 静默吞掉；retry 用 is not None 故 0 生效）")
    print()


def test_retry_exhaustion():
    q = BatchQueue(max_concurrency=1, max_size=5, retry=2, timeout=30)
    q.submit("x.png", "/x.png")
    calls = []

    def proc(t, cb):
        calls.append(1)
        raise RuntimeError("always fails")

    out = list(q.run(proc))
    t = out[0]
    print(f"[重试耗尽] retry=2 → processor 调用 {len(calls)} 次（期望 3）| "
          f"status={t.status.value} retries={t.retries}（期望 2）")
    print()


def test_during_run_summary_running():
    q = BatchQueue(max_concurrency=2, max_size=5, retry=0, timeout=30)
    for i in range(4):
        q.submit(f"s{i}.png", f"/fake/{i}.png")
    seen_running = []

    def proc(t, cb):
        time.sleep(0.05)
        return {"status": "ok"}

    gen = q.run(proc)
    for _ in gen:
        seen_running.append(q.summary()["running"])
    print(f"[运行中] 每次 yield 后 running 快照={seen_running}（终态应全 0）")
    print()


if __name__ == "__main__":
    test_normal_ok()
    test_mid_cancel_invariant()
    test_cancel_before_run()
    test_full_queue()
    test_empty_run()
    test_reset()
    test_or_fallback()
    test_retry_exhaustion()
    test_during_run_summary_running()
    print("PROBE DONE")
