# -*- coding: utf-8 -*-
"""阶段 278 · `services/queue_worker` 续审（5j 深度复审）— 钉死首轮遗漏的分支不变量。

背景
----
`services/queue_worker` 在阶段 88 首审（`test_services_audit.py` 的 B1/B2：取消要 yield
/ TaskCancelledError 不重试），阶段 161 又做了**契约级深挖**（`test_queue_worker_contract.py`，
发现并修复了 `or` 静默覆盖 + `retry<0` 永久 RUNNING 两处实质缺陷，并钉死构造参数契约 /
终态不变量 / 满空队列 / 重置 / 重试耗尽 / summary / to_dict）。

两轮都聚焦「**出错与边界参数**」，漏钉一批**成功路径与字段级不变量**——这些在正常使用时
决定上层 UI 能否正确渲染，却**零测试**：
· `_on_progress` 把进度夹紧到 [0,1]（处理器若传 -0.5 / 2.0 不应污染进度条）；
· DONE 时 `message` 取自 `result["status"]`（dict）或回退「完成」（非 dict）；
· DONE / FAILED / CANCELLED 三态的 `result` / `error` / `progress` 字段不变量；
· `on_update` 回调每个产出任务触发一次且回传同一对象；
· `run()` 是惰性生成器（不消费就不执行——防止「调了 run 却没动」的隐性 bug）；
· `tasks()` 返回拷贝（上层改返回值不影响内部状态）；
· `submit` 返回的 Task 字段（id/name/path/status）正确；
· 显式合法值被采纳（不回退 config）；
· `cancel()` 置位 `is_cancelled()`；
· 取消产出的 CANCELLED 任务 `error/result` 为 None。

方法
----
不依赖 GPU / 网络；用可控 processor（成功 / 抛错 / 取消）驱动各分支，断言 Task 字段与
生成器行为。与 161/88 套件无重复（161 已覆盖参数契约与终态计数，本文件只补字段级与
行为级遗漏）。

零真缺陷预期：上述都是「未钉边界」而非代码缺陷（与 276/277 同构）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.queue_worker import BatchQueue, TaskStatus  # noqa: E402


def _q(**kw):
    kw.setdefault("max_concurrency", 1)
    kw.setdefault("max_size", 10)
    kw.setdefault("retry", 0)
    kw.setdefault("timeout", 10)
    return BatchQueue(**kw)


# ===========================================================================
# 1 · _on_progress 进度夹紧 + 回调更新
# ===========================================================================
def test_progress_clamped_to_unit_range():
    seen = []

    def proc(t, cb):
        cb(-0.5, "under")
        seen.append(t.progress)
        cb(0.5, "mid")
        seen.append(t.progress)
        cb(2.0, "over")
        seen.append(t.progress)
        return {"status": "ok"}

    q = _q()
    q.submit("a.png", "/a.png")
    list(q.run(proc))
    # 下溢→0.0，中值→0.5，上溢→1.0（且回调确实写入了 task.progress）
    assert seen == [0.0, 0.5, 1.0]


# ===========================================================================
# 2 · DONE 的 message 映射 + 字段不变量
# ===========================================================================
def test_done_message_from_dict_status():
    q = _q()
    q.submit("b.png", "/b.png")
    out = list(q.run(lambda t, cb: {"status": "CUSTOM_DONE"}))
    assert out[0].status == TaskStatus.DONE
    assert out[0].message == "CUSTOM_DONE"


def test_done_message_non_dict_result():
    q = _q()
    q.submit("c.png", "/c.png")
    out = list(q.run(lambda t, cb: "plain-string"))
    assert out[0].status == TaskStatus.DONE
    assert out[0].message == "完成"          # 非 dict 结果回退默认文案


def test_done_field_invariants():
    q = _q()
    q.submit("d.png", "/d.png")
    o = list(q.run(lambda t, cb: {"status": "x"}))[0]
    assert o.result == {"status": "x"}
    assert o.error is None
    assert o.progress == 1.0
    assert o.status == TaskStatus.DONE


# ===========================================================================
# 3 · FAILED 字段不变量
# ===========================================================================
def test_failed_field_invariants():
    def proc(t, cb):
        raise RuntimeError("boom")

    q = _q()
    q.submit("e.png", "/e.png")
    o = list(q.run(proc))[0]
    assert o.status == TaskStatus.FAILED
    assert o.error == "boom"
    assert o.result is None
    assert o.progress == 0.0


# ===========================================================================
# 4 · on_update 回调
# ===========================================================================
def test_on_update_called_once_per_task():
    calls = []
    q = _q(max_concurrency=2)
    for i in range(3):
        q.submit(f"u{i}.png", f"/u{i}.png")
    out = list(q.run(lambda t, cb: {"status": "ok"}, on_update=lambda t: calls.append(t)))
    assert len(calls) == 3                     # 每产出任务一次
    assert calls == out                        # 回传的是同一批 Task 对象


# ===========================================================================
# 5 · cancel 置位 + 取消产出字段
# ===========================================================================
def test_cancel_sets_cancelled_flag():
    q = _q()
    q.cancel()
    assert q.is_cancelled() is True


def test_cancelled_fields_error_none_result_none():
    q = _q()
    for i in range(4):
        q.submit(f"cb{i}.png", f"/cb{i}.png")
    q.cancel()
    oc = list(q.run(lambda t, cb: {"status": "ok"}))
    assert len(oc) == 4
    assert all(t.status == TaskStatus.CANCELLED and t.error is None
               and t.result is None for t in oc)


# ===========================================================================
# 6 · tasks() 拷贝 / submit 字段
# ===========================================================================
def test_tasks_returns_copy():
    q = _q()
    q.submit("t.png", "/t.png")
    ts = q.tasks()
    ts.append("injected")                     # 改返回值不应污染内部
    assert len(q.tasks()) == 1


def test_submit_returns_task_fields():
    q = _q()
    tk = q.submit("s.png", "/s.png")
    assert tk.name == "s.png"
    assert tk.path == "/s.png"
    assert tk.status == TaskStatus.PENDING
    assert len(tk.id) == 8
    assert all(c in "0123456789abcdef" for c in tk.id)  # uuid4().hex[:8]


# ===========================================================================
# 7 · run 是惰性生成器
# ===========================================================================
def test_run_is_lazy_generator():
    q = _q()
    q.submit("l.png", "/l.png")
    gen = q.run(lambda t, cb: {"status": "ok"})
    assert all(t.status == TaskStatus.PENDING for t in q.tasks())  # 未消费不执行
    out = list(gen)
    assert out[0].status == TaskStatus.DONE


# ===========================================================================
# 8 · 显式合法值被采纳（不回退 config）
# ===========================================================================
def test_explicit_valid_values_honored():
    q = BatchQueue(max_concurrency=2, max_size=7, retry=1, timeout=42)
    assert q.max_concurrency == 2
    assert q.max_size == 7
    assert q.retry == 1
    assert q.timeout == 42


# ===========================================================================
# 9 · 计数辅助（混合结果）
# ===========================================================================
def test_count_helpers_after_mixed():
    q = _q()
    for i in range(3):
        q.submit(f"m{i}.png", f"/m{i}.png")

    def proc(t, cb):
        if t.name == "m0.png":
            raise RuntimeError("fail")
        return {"status": "ok"}

    list(q.run(proc))
    assert q.count_done() == 2
    assert q.count_failed() == 1
    assert q.count_cancelled() == 0
