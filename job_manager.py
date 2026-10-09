# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : job_manager.py
 文件说明 : 异步作业模型（阶段 255 新增）—— 把「长任务」从 HTTP 请求线程里剥离。

            背景（阶段 251 遗留）：server.py 的 PDF / 视频 / 模型下载**只暴露了只读
            查询端点**，长任务本身（remove_watermark_pdf / remove_watermark_video /
            download_model）尚未有 HTTP 入口。直接把它们做成同步 POST 会**占住并发
            闸门槽位数分钟**（视频可达数十分钟），把其它推理请求全堵在队列里。

            本模块用「提交即返回 job_id + 后台 worker 线程」把长任务异步化：
              · POST /api/jobs {kind, params}  → 立即返回 {job_id, "queued"}（不阻塞）；
              · 重活在 worker 线程跑，**在并发闸门之外**，故不占推理闸门槽位；
              · GET  /api/jobs/{id}             → 轮询状态 / 进度 / 结果；
              · GET  /api/jobs/{id}/file        → 下载结果文件。

            与项目分层铁律一致：
              · stdlib only（threading/uuid/queue/dataclasses），不引入新依赖；
              · 复用 api.remove_watermark_pdf / remove_watermark_video / download_model，
                与 GUI/CLI/库四入口行为完全一致；
              · 任务类型可扩展：register(kind, fn) 注册自定义 runner（测试与未来任务复用）。

 版 本 号 : 1.0.0
==============================================================================
"""
from __future__ import annotations

import os as _os
import sys as _sys
import base64
import json
import tempfile
import threading
import queue
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config  # noqa: E402
from utils.logger import get_logger  # noqa: E402

log = get_logger("job_manager")

# worker 回调签名：fn(job, set_progress)，其中
#   set_progress(value: float 0~1, message: str = "")
RunnerFn = Callable[[Any, Callable[[float, str], None]], None]

JOB_STATUSES = ("queued", "running", "success", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ==============================================================================
# 第 1 节：作业数据类
# ==============================================================================
@dataclass
class Job:
    """单个异步作业的状态（内存态，不落盘——进程退出即丢，符合本地服务定位）。"""

    id: str
    kind: str
    status: str = "queued"                 # queued / running / success / failed
    progress: float = 0.0                   # 0.0 ~ 1.0
    message: str = ""                       # 最近一步的中文提示（给前端展示）
    params: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    out_path: Optional[str] = None          # 结果文件绝对路径（GET .../file 用）
    created_at: str = ""
    updated_at: str = ""


def job_to_dict(job: Job) -> Dict[str, Any]:
    """把 Job 转成可 JSON 序列化的字典（剔除不可序列化项）。"""
    d = asdict(job)
    return d


# ==============================================================================
# 第 2 节：作业管理器
# ==============================================================================
class JobManager:
    """
    进程内的异步作业管理器：提交 → 队列 → worker 线程执行 → 状态写回。

    并发模型（关键）：
        · 提交（submit）只在 HTTP 处理线程里干「建 job + 入队」两件轻活，
          立即返回，故 POST /api/jobs 是毫秒级、可放心排在并发闸门里；
        · 真正的重活在 worker 线程执行，**完全在 _BoundedThreadingHTTPServer
          的闸门之外**——闸门只管「接收请求」，不管「后台算多久」。
          ⇒ 长任务不再占用推理闸门槽位，其它推理请求照常排队/执行。
    """

    def __init__(self, max_workers: int = 2, max_jobs: int = 512,
                 job_dir: Optional[str] = None) -> None:
        self.max_workers = max(1, int(max_workers))
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._runners: Dict[str, RunnerFn] = {}
        self._max_jobs = max(1, int(max_jobs))
        self._job_dir = job_dir or _os.path.join(
            tempfile.gettempdir(), "wme_async_jobs")
        _os.makedirs(self._job_dir, exist_ok=True)
        self._register_builtins()
        for _ in range(self.max_workers):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()

    # ------------------------------------------------------------------ #
    # 任务注册
    # ------------------------------------------------------------------ #
    def register(self, kind: str, fn: RunnerFn) -> None:
        """注册一个任务 runner（覆盖同名）。测试与未来任务类型复用同一扩展点。"""
        self._runners[kind] = fn

    def _register_builtins(self) -> None:
        self.register("pdf", self._run_pdf)
        self.register("video", self._run_video)
        self.register("model_download", self._run_model_download)

    # ------------------------------------------------------------------ #
    # 提交
    # ------------------------------------------------------------------ #
    def submit(self, kind: str, params: Dict[str, Any],
               runner: Optional[RunnerFn] = None) -> Optional[str]:
        """
        提交一个作业，立即返回 job_id（或 None 表示未知类型）。

        参数：
            kind    : 任务类型（需在 _runners 中已注册）。
            params  : 任务参数（原样透传给 runner，HTTP 层负责校验/裁剪）。
            runner  : 可选自定义 runner（测试用；生产调用传 None 走内置）。
        """
        fn = runner or self._runners.get(kind)
        if fn is None:
            return None
        jid = uuid.uuid4().hex[:12]
        now = _now()
        job = Job(id=jid, kind=kind, params=params or {},
                  created_at=now, updated_at=now)
        with self._lock:
            self._jobs[jid] = job
            self._evict_if_needed_locked()
        self._queue.put((jid, fn))
        return jid

    def _evict_if_needed_locked(self) -> None:
        """超 max_jobs 时，剔除最早的已终态作业（只删内存记录，保留输出文件）。"""
        if len(self._jobs) <= self._max_jobs:
            return
        terminal = [j for j in self._jobs.values()
                    if j.status in ("success", "failed")]
        terminal.sort(key=lambda j: j.created_at)
        excess = len(self._jobs) - self._max_jobs
        for j in terminal[:excess]:
            self._jobs.pop(j.id, None)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def get(self, jid: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(jid)

    def list_ids(self) -> list[str]:
        with self._lock:
            return [j.id for j in sorted(
                self._jobs.values(), key=lambda x: x.created_at)]

    # ------------------------------------------------------------------ #
    # worker
    # ------------------------------------------------------------------ #
    def _progress(self, job: Job) -> Callable[[float, str], None]:
        def cb(value: float, message: str = "") -> None:
            try:
                value = max(0.0, min(1.0, float(value)))
            except Exception:  # noqa: BLE001
                value = job.progress
            with self._lock:
                job.progress = value
                if message:
                    job.message = message
                job.updated_at = _now()
        return cb

    def _worker(self) -> None:
        while True:
            jid, fn = self._queue.get()
            try:
                with self._lock:
                    job = self._jobs.get(jid)
                    if job is None:
                        continue
                    job.status = "running"
                    job.updated_at = _now()
                try:
                    fn(job, self._progress(job))
                    with self._lock:
                        job.status = "success"
                        job.progress = 1.0
                        job.updated_at = _now()
                except Exception as exc:  # noqa: BLE001 - 单作业失败不影响 worker
                    with self._lock:
                        job.status = "failed"
                        job.error = f"{type(exc).__name__}: {exc}"
                        job.updated_at = _now()
                    log.warning("异步作业 %s 失败: %s", jid, job.error)
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------ #
    # 结果序列化（把 PdfResult/VideoResult 这类 dataclass 转普通 dict）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _result_to_dict(obj: Any) -> Dict[str, Any]:
        try:
            d = asdict(obj)
            # 去掉私有字段（如 VideoResult._rec_conf_sum / _rec_conf_n）
            return {k: v for k, v in d.items() if not k.startswith("_")}
        except Exception:  # noqa: BLE001
            return {"raw": str(obj)}

    # ------------------------------------------------------------------ #
    # 内置 runner：PDF
    # ------------------------------------------------------------------ #
    def _run_pdf(self, job: Job, cb: Callable[[float, str], None]) -> None:
        from api import remove_watermark_pdf  # 延迟导入：保持启动轻量

        params = job.params
        data = params.get("file")
        if not isinstance(data, str) or not data:
            raise ValueError("缺少 file 字段（PDF 的 base64）")
        raw = base64.b64decode(data)
        src = _os.path.join(self._job_dir, f"{job.id}_in.pdf")
        with open(src, "wb") as f:
            f.write(raw)

        dst = _os.path.join(self._job_dir, f"{job.id}_out.pdf")
        clean = {k: v for k, v in params.items() if k not in ("file",)}
        res = remove_watermark_pdf(src, dst=dst,
                                   on_progress=lambda p, m: cb(p, m),
                                   **clean)
        with self._lock:
            job.result = self._result_to_dict(res)
            job.out_path = res.out_path if getattr(res, "out_path", None) else None
        try:
            _os.remove(src)  # 仅删输入临时文件（1 次删除，不触发护栏）
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # 内置 runner：视频
    # ------------------------------------------------------------------ #
    def _run_video(self, job: Job, cb: Callable[[float, str], None]) -> None:
        from api import remove_watermark_video  # 延迟导入

        params = job.params
        data = params.get("file")
        if not isinstance(data, str) or not data:
            raise ValueError("缺少 file 字段（视频的 base64）")
        raw = base64.b64decode(data)
        ext = params.get("ext", "mp4")
        src = _os.path.join(self._job_dir, f"{job.id}_in.{ext}")
        with open(src, "wb") as f:
            f.write(raw)

        dst = _os.path.join(self._job_dir, f"{job.id}_out.mp4")
        clean = {k: v for k, v in params.items() if k not in ("file", "ext")}
        res = remove_watermark_video(src, dst=dst,
                                    on_progress=lambda p, m: cb(p, m),
                                    **clean)
        with self._lock:
            job.result = self._result_to_dict(res)
            job.out_path = res.out_path if getattr(res, "out_path", None) else None
        try:
            _os.remove(src)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # 内置 runner：模型下载（长网络任务）
    # ------------------------------------------------------------------ #
    def _run_model_download(self, job: Job, cb: Callable[[float, str], None]) -> None:
        from api import download_model  # 延迟导入

        name = job.params.get("name")
        # download_model 的 progress_cb 签名 (downloaded, total, speed_kbps)
        path = download_model(
            name,
            progress_cb=lambda d, t, s: cb((d / t) if t else 0.0,
                                           f"已下载 {int(d / 1024)}KB"),
        )
        with self._lock:
            job.result = {"downloaded": True, "name": name or "(active)",
                          "path": path}


# ------------------------------------------------------------------------------
# 模块级单例（server.py 直接引用；测试可 import 后 register 自定义任务）
# ------------------------------------------------------------------------------
JOB_MANAGER = JobManager(max_workers=config.API_ASYNC_MAX_WORKERS)
