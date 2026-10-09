# -*- coding: utf-8 -*-
"""
阶段 D1 / 257 守卫 · 异步作业 worker 池并发标定（契约层，非重负载）。

校准结论（真实 GPU 标定，见 docs/257-D1异步作业worker池并发标定.md）：
  · 单例 worker 数 = config.API_ASYNC_MAX_WORKERS（默认 2，本地单用户延迟甜点）。
  · 该值必须落在 [1, 8] 安全区间。
  · max_workers=1 ⇒ 作业严格串行；max_workers>=2 ⇒ 并发作业可同时 running 并全部终态。

本守卫只验证「契约」，不重跑 GPU 负载（重负载由探针实测背书，见 docs）。
每个用例用独立 JobManager + 临时 job_dir，避免污染生产单例 JOB_MANAGER。
"""
import threading
import time

import pytest

import job_manager as JM
import config


def test_config_async_max_workers_in_bounds():
    """API_ASYNC_MAX_WORKERS 必须在 [1,8]，防有人把并发调到 0 或离谱大值。"""
    assert 1 <= config.API_ASYNC_MAX_WORKERS <= 8


def test_singleton_honors_config_max_workers():
    """生产单例的 worker 数必须等于配置值（D1 的核心交付：不可配置→可配置）。"""
    assert JM.JOB_MANAGER.max_workers == config.API_ASYNC_MAX_WORKERS
    assert JM.JOB_MANAGER.max_workers >= 1


def test_concurrent_jobs_all_complete(tmp_path):
    """max_workers=3 时，3 个并发提交都应到达 success（并发执行契约）。"""
    mgr = JM.JobManager(max_workers=3, job_dir=str(tmp_path))

    def runner(job, cb):
        time.sleep(0.05)
        cb(1.0)

    mgr.register("dummy", runner)
    jids = [mgr.submit("dummy", {"i": i}) for i in range(3)]
    assert all(j is not None for j in jids)

    done = {}
    deadline = time.time() + 10
    while len(done) < 3 and time.time() < deadline:
        for jid in jids:
            j = mgr.get(jid)
            if j and j.status in ("success", "failed"):
                done[jid] = j.status
        time.sleep(0.02)

    assert len(done) == 3
    assert all(s == "success" for s in done.values())


def test_max_workers_one_serializes(tmp_path):
    """max_workers=1 ⇒ 两个作业严格串行：job0 的 end 必在 job1 的 start 之前。"""
    order = []
    lock = threading.Lock()

    def runner(job, cb):
        with lock:
            order.append(("start", job.id))
        time.sleep(0.15)
        with lock:
            order.append(("end", job.id))
        cb(1.0)

    mgr = JM.JobManager(max_workers=1, job_dir=str(tmp_path))
    mgr.register("dummy", runner)
    jids = [mgr.submit("dummy", {}) for _ in range(2)]

    done = {}
    deadline = time.time() + 10
    while len(done) < 2 and time.time() < deadline:
        for jid in jids:
            j = mgr.get(jid)
            if j and j.status in ("success", "failed"):
                done[jid] = j.status
        time.sleep(0.02)

    assert len(done) == 2
    starts = [i for i, (ev, _) in enumerate(order) if ev == "start"]
    ends = [i for i, (ev, _) in enumerate(order) if ev == "end"]
    # 串行语义：第一个作业结束 必须早于 第二个作业开始
    assert ends[0] < starts[1], f"串行契约被破坏: {order}"


def test_explicit_max_workers_honored():
    """显式传入的 max_workers 必须被 JobManager 记录，便于测试/未来任务独立标定。"""
    mgr = JM.JobManager(max_workers=4, job_dir=str(tmp_path_optional()))
    try:
        assert mgr.max_workers == 4
    finally:
        pass


def tmp_path_optional():
    import tempfile
    import os
    d = os.path.join(tempfile.gettempdir(), "wme_d1_test_tmp")
    os.makedirs(d, exist_ok=True)
    return d
