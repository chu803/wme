# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/queue_worker.py
 文件说明 : 任务队列引擎（阶段 5 核心服务）。
            负责批量图片的「排队 → 并发执行 → 进度汇报 → 成功/失败日志」，
            与界面层（ui）完全解耦：它不依赖 Gradio，只通过回调（callback）
            把进度抛给上层。上层（ui/tab_batch）决定如何用 Gradio 组件呈现。

            设计要点：
            1. 并发：使用 ThreadPoolExecutor，并发数由 config.BATCH_CONCURRENCY 控制。
            2. 流式进度：run() 是一个生成器（generator），每完成一个任务就 yield 一次，
               上层可借此实时刷新进度条与日志（Gradio 的 generator 回调天然支持流式）。
            3. 线程安全：所有共享状态都通过 threading.Lock 保护，避免并发写入错乱。
            4. 取消：cancel() 设置标志位，未开始的任务不再启动；进行中的任务会在
               下一个检查点（processor 内的 on_progress 回调）感知到并安全退出。
            5. 重试：单个任务失败后可按 config.QUEUE_TASK_RETRY 自动重试（应对偶发故障）。

            本模块归属「服务层」（services），只允许调用 core / utils / config，
            严禁 import ui（否则会破坏阶段 1 定下的分层依赖铁律）。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import threading as _threading
import time as _time
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterator, Optional

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from utils.exceptions import QueueFullError, TaskCancelledError, TaskError
from utils.logger import get_logger

log = get_logger("services.queue_worker")


class TaskStatus(str, Enum):
    """单个任务的枚举状态。继承 str 便于直接 JSON 序列化与比较。"""

    PENDING = "pending"      # 已入队，等待执行
    RUNNING = "running"      # 正在执行
    DONE = "done"            # 执行成功
    FAILED = "failed"        # 执行失败（含重试后仍失败）
    CANCELLED = "cancelled"  # 被用户取消


@dataclass
class Task:
    """
    单个批量任务的轻量描述。

    id        : 全局唯一任务号（uuid4 前 8 位，便于日志阅读）。
    name      : 展示名（通常取自文件名）。
    path      : 待处理图片路径（绝对路径，支持中文）。
    status    : 当前状态，见 TaskStatus。
    progress  : 0~1 的进度（由 processor 的 on_progress 回调更新）。
    message   : 人类可读的进度/结果描述。
    result    : processor 返回的任意结果对象（成功时），失败时为 None。
    error     : 失败时的错误字符串。
    retries   : 已发生的重试次数。
    _cancelled: 该任务自身的取消标记（与队列级取消标志联动）。
    """

    id: str
    name: str
    path: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    message: str = ""
    result: Optional[dict] = None
    error: Optional[str] = None
    retries: int = 0
    _cancelled: bool = field(default=False, repr=False)

    def to_dict(self) -> dict:
        """导出为可 JSON 序列化的字典（用于界面/日志）。"""
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "message": self.message,
            "error": self.error,
            "retries": self.retries,
        }


# processor 的进度回调签名：on_progress(progress: float, message: str) -> None
ProgressCallback = Callable[[float, str], None]
# processor 本体：接收 (task, on_progress)，返回结果字典（成功）或抛异常（失败）
Processor = Callable[[Task, ProgressCallback], dict]


class BatchQueue:
    """
    批量任务队列引擎。

    用法（由上层 ui 调用）：
        q = BatchQueue()                       # 读取 config 默认参数
        q.submit("a.png", "/path/a.png")
        q.submit("b.png", "/path/b.png")
        for task in q.run(processor):          # 生成器，逐个任务产出
            update_ui(task)                    # 刷新进度条 + 日志
    """

    def __init__(
        self,
        max_concurrency: Optional[int] = None,
        max_size: Optional[int] = None,
        retry: Optional[int] = None,
        timeout: Optional[int] = None,
    ):
        """
        初始化队列。任何参数缺省时从 config 取值。

        参数说明：
            max_concurrency : 并发线程数（config.BATCH_CONCURRENCY）。
            max_size        : 队列最大长度（config.QUEUE_MAX_SIZE）。
            retry           : 单任务失败重试次数（config.QUEUE_TASK_RETRY）。
            timeout         : 单任务超时秒数（config.QUEUE_TASK_TIMEOUT），预留扩展。
        """
        # 阶段 161：四个参数统一用 `is not None` 判定，不再用 `or`——`or` 会把调用方
        # **显式传入的 0 静默吞掉**、回退到 config（「静默覆盖调用方意图」）。此前只有
        # `retry` 用了正确写法，是同一风险面的两种写法（家族：同值不同源 / 兜底绕过）。
        # 改为：缺省（None）才取 config；显式非法值直接抛 ValueError（**显式失败**优于
        # 「静默用别的值」或「交给下游崩溃 / 卡在 RUNNING」）。
        self.max_concurrency = (max_concurrency if max_concurrency is not None
                                else config.BATCH_CONCURRENCY)
        self.max_size = max_size if max_size is not None else config.QUEUE_MAX_SIZE
        self.retry = retry if retry is not None else config.QUEUE_TASK_RETRY
        self.timeout = timeout if timeout is not None else config.QUEUE_TASK_TIMEOUT

        # 阶段 161：下界校验。`retry < 0` 会让 `attempts = retry + 1 <= 0` ⇒ `for` 循环
        # 一次都不执行 ⇒ 任务**永远停在 RUNNING**（既不 DONE/FAILED/CANCELLED），
        # 破坏 run() 的终态不变量；`max_concurrency < 1` 会让 ThreadPoolExecutor 抛
        # ValueError；`max_size < 1` 则使队列无法容纳任何任务。均在构造期显式拒绝。
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency 必须 ≥ 1，得到 {self.max_concurrency}")
        if self.max_size < 1:
            raise ValueError(f"max_size 必须 ≥ 1，得到 {self.max_size}")
        if self.retry < 0:
            raise ValueError(f"retry 必须 ≥ 0，得到 {self.retry}")

        self._tasks: list[Task] = []
        self._index: dict[str, Task] = {}
        self._lock = _threading.RLock()      # 保护 _tasks / _index / 取消标志
        self._cancelled = False

    # --------------------------------------------------------------------------
    # 入队
    # --------------------------------------------------------------------------
    def submit(self, name: str, path: str) -> Task:
        """
        提交一个任务到队列。

        参数说明：
            name : 展示名（文件名）。
            path : 图片绝对路径。

        返回值：
            新创建的 Task 对象。

        异常：
            QueueFullError : 队列已达 max_size。
        """
        with self._lock:
            if len(self._tasks) >= self.max_size:
                raise QueueFullError(
                    f"任务队列已满（上限 {self.max_size}）。"
                    f"请等待当前批次完成，或分批提交。"
                )
            task = Task(
                id=_uuid.uuid4().hex[:8],
                name=name,
                path=path,
            )
            self._tasks.append(task)
            self._index[task.id] = task
            log.info("任务入队：%s (%s)，队列长度=%d", name, task.id, len(self._tasks))
            return task

    def reset(self) -> None:
        """清空队列（仅在确知无进行中任务时调用，例如界面重置）。"""
        with self._lock:
            self._tasks.clear()
            self._index.clear()
            self._cancelled = False

    # --------------------------------------------------------------------------
    # 取消
    # --------------------------------------------------------------------------
    def cancel(self) -> None:
        """
        请求取消：设置队列级取消标志。
        效果：尚未开始的任务标记为 CANCELLED 不再启动；进行中的任务会在其
        processor 的 on_progress 检查点感知到取消并安全退出。
        """
        with self._lock:
            self._cancelled = True
        log.info("已请求取消剩余任务。")

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    # --------------------------------------------------------------------------
    # 执行（生成器：每完成一个任务 yield 一次）
    # --------------------------------------------------------------------------
    def run(self, processor: Processor, on_update: Optional[Callable[[Task], None]] = None) -> Iterator[Task]:
        """
        并发执行队列中所有任务，逐个 yield 完成（或状态变更）的 Task。

        参数说明：
            processor : 单任务处理器，签名见 Processor 类型。
            on_update : 每完成一个任务后的可选回调（界面层可在此刷新组件）。

        返回值：
            一个迭代器，依次产出完成后的 Task 对象。

        说明：
            本方法本身是线程安全的：通过 ThreadPoolExecutor 控制并发，
            用 as_completed 实现「谁先完成谁先 yield」，保证进度实时滚动。
        """
        with self._lock:
            tasks = list(self._tasks)
            cancelled = self._cancelled

        if not tasks:
            log.info("队列为空，无需执行。")
            return

        total = len(tasks)
        completed = 0
        log.info("开始批量执行：共 %d 个任务，并发数 %d。", total, self.max_concurrency)

        # 提交时若已取消，直接把所有 PENDING 标记为 CANCELLED。
        # 阶段 87 修复：这些任务**必须逐个 yield**。
        #   run() 的契约是「逐个 yield 完成（或状态变更）的 Task」，而原实现在这里
        #   直接 return——状态确实被改成了 CANCELLED，但上层通过
        #   `for t in q.run(processor)` **一个对象都收不到**（阶段 87 实测：
        #   入队 5 个全部取消 → yield 0 个，summary() 却显示 cancelled=5）。
        #   用户在界面上的观感是：点了「取消」，进度条与日志毫无反应。
        if cancelled:
            for t in tasks:
                if t.status == TaskStatus.PENDING:
                    with self._lock:
                        t.status = TaskStatus.CANCELLED
                        t.message = "已取消"
                    completed += 1
                    if on_update:
                        on_update(t)
                    log.info("任务进度 %d/%d：%s -> %s",
                             completed, total, t.name, t.status.value)
                    yield t
            return

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as ex:
            futures = {}
            skipped: list[Task] = []
            for t in tasks:
                # 提交前再检查一次取消标志：未启动的任务直接取消。
                if self.is_cancelled():
                    with self._lock:
                        if t.status == TaskStatus.PENDING:
                            t.status = TaskStatus.CANCELLED
                            t.message = "已取消"
                            skipped.append(t)
                    continue
                futures[ex.submit(self._execute, t, processor)] = t

            # 阶段 87 修复：提交阶段被跳过（未启动即取消）的任务同样要 yield 出去，
            # 与上面 `if cancelled:` 分支同因——状态变更必须通知到上层。
            for t in skipped:
                completed += 1
                if on_update:
                    on_update(t)
                log.info("任务进度 %d/%d：%s -> %s",
                         completed, total, t.name, t.status.value)
                yield t

            # as_completed：哪个任务先结束，就先处理哪个，保证进度流畅。
            for fut in as_completed(futures):
                task = futures[fut]
                # _execute 内部已把结果写回 task，这里取回即可。
                _ = fut.result()
                completed += 1
                if on_update:
                    on_update(task)
                log.info(
                    "任务进度 %d/%d：%s -> %s", completed, total, task.name, task.status.value
                )
                yield task

        log.info("批量执行结束：成功 %d / 失败 %d / 取消 %d。",
                 self.count_done(), self.count_failed(), self.count_cancelled())

    # --------------------------------------------------------------------------
    # 单任务执行包装（在线程池线程内运行）
    # --------------------------------------------------------------------------
    def _execute(self, task: Task, processor: Processor) -> Task:
        """
        在线程内执行单个任务：含状态机切换、重试、取消感知、异常捕获。

        返回值：
            更新后的 task 对象（供 run 的 as_completed 取回）。
        """
        with self._lock:
            task.status = TaskStatus.RUNNING
            task.message = "处理中…"

        def _on_progress(p: float, msg: str) -> None:
            with self._lock:
                task.progress = max(0.0, min(1.0, float(p)))
                task.message = msg
                # 检查点：若队列或任务自身被取消，立即抛出，中断 processor。
                if self._cancelled or task._cancelled:
                    raise _CancelSignal()

        attempts = self.retry + 1
        for attempt in range(attempts):
            try:
                if self._cancelled or task._cancelled:
                    raise _CancelSignal()
                result = processor(task, _on_progress)
                with self._lock:
                    task.status = TaskStatus.DONE
                    task.progress = 1.0
                    task.result = result
                    task.message = result.get("status", "完成") if isinstance(result, dict) else "完成"
                    task.error = None
                return task
            except (_CancelSignal, TaskCancelledError):
                # 阶段 87 修复：TaskCancelledError 表达「用户主动取消」这一**业务状态**，
                # 但原实现没有为它设分支，于是它落入下面的宽泛 `except Exception`，
                # 被当成「偶发故障」重试 retry+1 次后标记 FAILED。
                # 实测（阶段 87 探针）：processor 抛 TaskCancelledError("用户主动取消")
                #   → 重试 3 次 → status=failed，error 却是「这是您主动取消的操作，
                #   无需处理。」——既白白重试（processor 可能有副作用），
                #   又向用户给出自相矛盾的报告。
                # 注：此处**刻意不合并** _CancelSignal 与 TaskCancelledError 两个类——
                #   阶段 81 已裁决二者语义不等价（前者是生成器内部的流程控制信号，
                #   后者是业务状态，合并需重设批量队列状态机），
                #   本轮只让二者在这里得到**相同的处理**，不动裁决。
                with self._lock:
                    task.status = TaskStatus.CANCELLED
                    task.message = "已取消"
                    task.error = None
                return task
            except Exception as exc:  # noqa: BLE001 - 单任务失败不应拖垮整个批次
                if attempt < attempts - 1:
                    # 偶发错误：等待后重试。
                    task.retries = attempt + 1
                    log.warning("任务 %s 第 %d 次失败，准备重试：%s",
                                task.name, attempt + 1, exc)
                    _time.sleep(0.5 * (attempt + 1))
                    continue
                # 重试耗尽：标记失败。
                with self._lock:
                    task.status = TaskStatus.FAILED
                    task.error = str(exc)
                    task.message = f"失败：{exc}"
                    task.progress = 0.0
                log.error("任务 %s 最终失败：%s", task.name, exc)
                return task
        return task

    # --------------------------------------------------------------------------
    # 统计快照（用于界面进度条与日志汇总）
    # --------------------------------------------------------------------------
    def summary(self) -> dict:
        """
        返回当前队列的聚合状态快照。

        返回值：
            dict，含 total/done/failed/cancelled/pending/running 与进度比例。
        """
        with self._lock:
            total = len(self._tasks)
            done = self.count_done()
            failed = self.count_failed()
            cancelled = self.count_cancelled()
            running = sum(1 for t in self._tasks if t.status == TaskStatus.RUNNING)
            pending = sum(1 for t in self._tasks if t.status == TaskStatus.PENDING)
            # 当前正在处理的任务名（用于界面「正在处理：xxx」提示）；无则空串。
            current = next(
                (t.name for t in self._tasks if t.status == TaskStatus.RUNNING),
                "",
            )
        finished = done + failed + cancelled
        progress = (finished / total) if total else 0.0
        return {
            "total": total,
            "done": done,
            "failed": failed,
            "cancelled": cancelled,
            "running": running,
            "pending": pending,
            "finished": finished,
            "progress": progress,
            "current": current,
        }

    def count_done(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks if t.status == TaskStatus.DONE)

    def count_failed(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks if t.status == TaskStatus.FAILED)

    def count_cancelled(self) -> int:
        with self._lock:
            return sum(1 for t in self._tasks if t.status == TaskStatus.CANCELLED)

    def tasks(self) -> list[Task]:
        """返回任务列表的浅拷贝（线程安全读取）。"""
        with self._lock:
            return list(self._tasks)


class _CancelSignal(Exception):
    """内部信号：用于在 on_progress 检查点中断 processor（不希望泄漏到外部）。"""

    pass


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    import random

    def _fake_processor(task: Task, on_progress: ProgressCallback) -> dict:
        """模拟处理器：分步推进进度，随机失败一次以验证重试。"""
        for step in range(1, 5):
            on_progress(step / 4.0, f"步骤 {step}/4")
            _time.sleep(0.05)
        if random.random() < 0.15:
            raise RuntimeError("模拟的偶发失败")
        return {"status": f"完成 {task.name}"}

    q = BatchQueue(max_concurrency=3, max_size=10, retry=1, timeout=10)
    for i in range(7):
        q.submit(f"img_{i:02d}.jpg", f"/fake/img_{i:02d}.jpg")

    snap = q.summary()
    assert snap["total"] == 7, "任务总数错误"
    print(f"入队完成：{snap['total']} 个任务")

    done_count = 0
    failed_count = 0
    for finished in q.run(_fake_processor):
        s = q.summary()
        if finished.status == TaskStatus.DONE:
            done_count += 1
        elif finished.status == TaskStatus.FAILED:
            failed_count += 1
        print(f"  进度 {s['finished']}/{s['total']} "
              f"(成功 {s['done']} / 失败 {s['failed']}) "
              f"<- {finished.name}: {finished.status.value}")

    assert done_count + failed_count == 7, "产出数量不等于入队数量"
    print(f"执行结束：成功 {done_count}，失败 {failed_count}")
    print("queue_worker.py 自检全部通过。")
