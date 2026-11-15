# 批量并行提速（C1 方案）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把批量页默认串行改为核数感知的线程并发，并按并发档耦合 torch 线程，在本机实测检测吞吐 1.86× 的基础上坐实端到端加速，且不降承诺召回、不破坏队列契约。

**Architecture:** 保留 `BatchQueue` 的 `ThreadPoolExecutor` 结构不动。三件耦合的事：(1) `BATCH_CONCURRENCY` 默认从 1 改为核数感知 `min(10, max(2, 逻辑核//2))`；(2) 新增上下文管理器 `batch_cpu_threads(conc)`，批量期间把 torch intra-op 线程调到 `clamp(逻辑核//conc, 1, 进入值)`，退出恢复；(3) `_get_ocr_reader` 加双重检查锁，保证并发首检只建一个共享 reader。批量消费循环用该上下文管理器包住，恢复挂在已有 finally。

**Tech Stack:** Python 3.11、Gradio 6.x、PyTorch（CPU）、EasyOCR、OpenCV、pytest 9.2.0。

**Spec:** `docs/superpowers/specs/2026-11-15-batch-parallelism-design.md`

## Global Constraints

- 核数口径钉死 **`os.cpu_count()`（逻辑核，本机 24）**，兜底 `or 2`；不用物理核。
- 并发默认上限 **10**（阶段 407 实测最优档，更高未测）；env `BATCH_CONCURRENCY` 范围 **1–16** 不变。
- 线程公式 `threads = clamp(cores // conc, 1, 进入时 torch 线程数)`，`clamp(x,lo,hi)=max(lo,min(x,hi))`；conc1 封顶回默认（不降单图延迟）。
- **只耦合 torch intra-op 线程**（与探针 #3 实测口径完全一致）。规划期细化：spec §5 原文「复用 apply_cpu_threads」，但 `apply_cpu_threads` 会调 `torch.set_num_interop_threads`（**一次性、无法恢复**）并设 cv2/env（探针未动 ⇒ 未测变更）。故上下文管理器只设可恢复的 intra-op 子集，更贴合测量、爆炸半径最小。此细化不改变 spec 的意图（按并发耦合线程 + 退出恢复）。
- 不改检测算法 / 预设 / 阈值；承诺召回 **≥ 0.8078**、面积绝对红线不动；修复引擎默认 `INPAINT_AUTO_PREFER=opencv` 不动。
- 不动 `services/queue_worker.py` 结构、inpainter、UI 组件。
- 全局线程爆炸半径是已知权衡：finally 必恢复（异常 / 生成器关闭安全）；文档明示「高并发批量期间并发单图可能变慢」。
- **不推送 / 不发布**（1.0.13 仍等用户点名）；本地 commit 可自主。
- 落笔 4 处：`docs/408-*.md` + INDEX + CHANGELOG [Unreleased] + 日记。

## 环境与命令口径（每个任务通用）

- 工作目录每轮先确认：`cd /d/qoder/watermark-workbench`（SessionStart hook 会把 cwd 重置回 /d/qoder）。
- **ci311venv**（pytest 9.2.0 + cv2 + numpy，**无 torch**）：`D:/qoder/ci311venv/Scripts/python.exe`（venv 在 `/d/qoder/`，**不在项目内**）。跑无 torch 用例与 `run_all_checks` 默认 CI 子集。
- **gatevenv**（torch + EasyOCR + cv2 + numpy）：`D:/qoder/gatevenv/Scripts/python.exe`。跑 `torch_required` 用例、真实图门禁、端到端基准。
- Windows 编码：命令前置 `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`。
- 直接 pytest 用项目内 basetemp 避开 %TEMP% 清理守卫：`--basetemp=.pytest_plan`。
- 中文路径会毁 cv2.imread ⇒ 测试造图用 `tmp_path`（ASCII）+ `cv2.imwrite`；读图走 `core.image_utils.load_image`。
- commit 消息含反引号时走 `git commit -F <文件>`，避免 shell 静默吃内容。
- 新增 torch 用例文件顶部：`pytest.importorskip("torch")` + `pytestmark = pytest.mark.torch_required`。

## 文件结构

| 文件 | 责任 | 动作 |
| --- | --- | --- |
| `core/detector.py` | OCR reader 单例并发首检安全 | 改：加 `_OCR_READER_LOCK` + 双重检查（87-88、151-193） |
| `core/device_manager.py` | 批量期间耦合 torch 线程并可恢复 | 改：加 `contextlib` 导入 + `batch_cpu_threads`（22、322 后） |
| `config.py` | 核数感知并发默认 | 改：加 `_batch_concurrency_default` helper + 改默认（2493-2497） |
| `ui/tab_batch.py` | 把上下文管理器接进批量消费循环 | 改：导入 + 包住 run 循环（46-58 导入区、716-743） |
| `tests/test_ocr_reader_lock.py` | reader 并发首检只建一个 | 新建（ci311venv，无 torch） |
| `tests/test_batch_cpu_threads.py` | 线程公式钳制 / 恢复 / 异常 / None 兜底 | 新建（gatevenv，torch_required） |
| `tests/test_batch_concurrency_default.py` | 并发默认公式 + env 覆盖 | 新建（ci311venv，无 torch） |
| `tests/test_batch_wiring.py` | run_batch 确实用 conc 进入上下文管理器 | 新建（gatevenv，torch_required） |
| `.scratch/bench_batch_e2e_408.py` | 端到端全流水线 conc1 vs 新默认基准 | 新建（gatevenv，验证用，非门禁） |
| `docs/408-batch-parallelism.md` 等 | 落笔 4 处 | 新建 / 改（Task 5） |

---

### Task 1: OCR reader 并发首检锁

**Files:**
- Modify: `core/detector.py:87-88`（加锁对象）、`core/detector.py:151-193`（双重检查）
- Test: `tests/test_ocr_reader_lock.py`（新建）

**Interfaces:**
- Consumes: 现有 `_get_ocr_reader()`、`_ocr_available()`、模块级 `_OCR_READER`、已导入的 `threading`（detector.py:39）。
- Produces: 模块级 `_OCR_READER_LOCK = threading.Lock()`；`_get_ocr_reader()` 行为不变（仍返回单例或 None），但并发首检只建一个 reader。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_ocr_reader_lock.py`：

```python
# -*- coding: utf-8 -*-
"""阶段 408：OCR reader 并发首次初始化只建一个（双重检查锁）。

无 torch / 无真实 easyocr：伪造 easyocr.Reader（构造慢一点放大竞争窗口），
强制走 CPU 分支（DETECT_OCR_USE_GPU=False ⇒ 不触发 device_manager/torch）。
ci311venv 即可跑。
"""
from __future__ import annotations

import sys
import threading
import time
import types

import core.detector as det


def test_concurrent_first_init_builds_one_reader(monkeypatch):
    calls = []

    class FakeReader:
        def __init__(self, *a, **k):
            calls.append(1)
            time.sleep(0.05)  # 放大竞争窗口：无锁时多线程会同时进入创建

    fake = types.ModuleType("easyocr")
    fake.Reader = FakeReader
    monkeypatch.setitem(sys.modules, "easyocr", fake)
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(det, "_OCR_READER", None)
    monkeypatch.setattr(det.config, "DETECT_OCR_USE_GPU", False, raising=False)

    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(det._get_ocr_reader())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert len(calls) == 1, f"reader 被建了 {len(calls)} 次（应只 1 次）"
    assert results[0] is not None
    assert all(r is results[0] for r in results), "所有线程应拿到同一个 reader"
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/ci311venv/Scripts/python.exe -m pytest tests/test_ocr_reader_lock.py --basetemp=.pytest_plan -v
```
Expected: FAIL — `assert len(calls) == 1` 失败（无锁时 8 个线程各建一个，calls≈8）。

- [ ] **Step 3: 加锁对象**

在 `core/detector.py:88`（`_OCR_AVAILABLE = None` 那行）后加一行：

```python
_OCR_READER = None
_OCR_AVAILABLE = None  # None=尚未探测；True/False=探测结果
_OCR_READER_LOCK = threading.Lock()  # 阶段 408：并发首检只建一个 reader（双重检查）
```

- [ ] **Step 4: 双重检查包住创建块**

把 `core/detector.py` 的 `_get_ocr_reader` 主体（151-193）改为：外层 `if _OCR_READER is None:` 内套 `with _OCR_READER_LOCK:` 再套一层 `if _OCR_READER is None:`，原创建逻辑整体缩进进最内层。改后完整函数体（docstring 保持不变，仅列代码部分）：

```python
    global _OCR_READER
    if not _ocr_available():
        return None
    if _OCR_READER is None:
        with _OCR_READER_LOCK:
            if _OCR_READER is None:
                try:
                    import easyocr  # type: ignore

                    want_gpu = bool(getattr(config, "DETECT_OCR_USE_GPU", True))
                    if want_gpu:
                        try:
                            from core.device_manager import select_device

                            handle = select_device()  # auto 语义：无卡自动退 CPU，不抛
                            want_gpu = getattr(handle.info, "kind", "cpu") == "cuda"
                        except Exception as exc:
                            log.info("OCR 设备探测失败，回退 CPU：%s", exc)
                            want_gpu = False

                    if want_gpu:
                        try:
                            _OCR_READER = easyocr.Reader(
                                ["en", "ch_sim"], gpu=True, verbose=False
                            )
                            log.info("EasyOCR 阅读器初始化完成（GPU 模式）。")
                        except Exception as exc:
                            log.warning("OCR 的 GPU 初始化失败，回退 CPU：%s", exc)
                            _OCR_READER = easyocr.Reader(
                                ["en", "ch_sim"], gpu=False, verbose=False
                            )
                            log.info("EasyOCR 阅读器初始化完成（CPU 模式）。")
                    else:
                        _OCR_READER = easyocr.Reader(
                            ["en", "ch_sim"], gpu=False, verbose=False
                        )
                        log.info("EasyOCR 阅读器初始化完成（CPU 模式）。")
                except Exception as exc:
                    log.warning("EasyOCR 初始化失败，文字检测降级：%s", exc)
                    _OCR_READER = None
    return _OCR_READER
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/ci311venv/Scripts/python.exe -m pytest tests/test_ocr_reader_lock.py --basetemp=.pytest_plan -v
```
Expected: PASS（calls==1，所有线程同一对象）。

- [ ] **Step 6: 提交**

```bash
git add core/detector.py tests/test_ocr_reader_lock.py
git commit -m "fix(批量并行): OCR reader 并发首检加双重检查锁，只建一个共享 reader"
```

---

### Task 2: batch_cpu_threads 上下文管理器

**Files:**
- Modify: `core/device_manager.py:22`（加 contextlib 导入）、`core/device_manager.py:322` 后（加函数）
- Test: `tests/test_batch_cpu_threads.py`（新建）

**Interfaces:**
- Consumes: `os`（device_manager.py:19 已导入）、`torch.get_num_threads()/set_num_threads()`（函数内延迟导入）。
- Produces: `batch_cpu_threads(concurrency: int)` 上下文管理器，`yield target`（int，钳制后的 torch 线程数）；进入设 `torch.set_num_threads(target)`，退出恢复进入值；torch 不可用时 no-op。`target = max(1, min((os.cpu_count() or 2) // max(1,int(conc)), entry))`，`entry=torch.get_num_threads()`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_batch_cpu_threads.py`：

```python
# -*- coding: utf-8 -*-
"""阶段 408：batch_cpu_threads 公式钳制 / 进入设线程 / 退出恢复 / 异常仍恢复。

gatevenv（torch_required）。monkeypatch torch.get/set_num_threads 与 os.cpu_count，
不产生真实线程副作用。
"""
from __future__ import annotations

import pytest

pytest.importorskip("torch")
pytestmark = pytest.mark.torch_required

import config  # noqa: E402,F401  (确保 sys.path 与项目一致)
import core.device_manager as dm  # noqa: E402


def _patch(monkeypatch, cores, entry):
    import torch
    monkeypatch.setattr(dm.os, "cpu_count", lambda: cores)
    monkeypatch.setattr(torch, "get_num_threads", lambda: entry)
    calls = []
    monkeypatch.setattr(torch, "set_num_threads", lambda n: calls.append(n))
    return calls


def test_formula_conc10_and_restore(monkeypatch):
    calls = _patch(monkeypatch, 24, 16)
    with dm.batch_cpu_threads(10) as target:
        assert target == 2          # 24//10=2，clamp(2,1,16)=2（实测最优）
        assert calls == [2]
    assert calls == [2, 16]         # 退出恢复进入值


def test_conc1_caps_to_entry(monkeypatch):
    calls = _patch(monkeypatch, 24, 16)
    with dm.batch_cpu_threads(1) as target:
        assert target == 16         # 24//1=24，clamp(24,1,16)=16（不降单图延迟）
    assert calls == [16, 16]


def test_small_cores_floor_one(monkeypatch):
    calls = _patch(monkeypatch, 4, 16)
    with dm.batch_cpu_threads(10) as target:
        assert target == 1          # 4//10=0，下限 1
    assert calls == [1, 16]


def test_none_cores_fallback(monkeypatch):
    calls = _patch(monkeypatch, None, 16)
    with dm.batch_cpu_threads(10) as target:
        assert target == 1          # (None or 2)//10=0，下限 1
    assert calls == [1, 16]


def test_restores_on_exception(monkeypatch):
    calls = _patch(monkeypatch, 24, 16)
    with pytest.raises(RuntimeError):
        with dm.batch_cpu_threads(10):
            raise RuntimeError("boom")
    assert calls == [2, 16]         # 异常路径仍恢复
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/gatevenv/Scripts/python.exe -m pytest tests/test_batch_cpu_threads.py --basetemp=.pytest_plan -v
```
Expected: FAIL — `AttributeError: module 'core.device_manager' has no attribute 'batch_cpu_threads'`。

- [ ] **Step 3: 加 contextlib 导入**

在 `core/device_manager.py:22`（`from dataclasses import dataclass, field`）后加：

```python
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional
```

- [ ] **Step 4: 加上下文管理器**

在 `core/device_manager.py` 的 `apply_cpu_threads` 函数结束（322 行 `log.debug(...)`）后追加：

```python
@contextmanager
def batch_cpu_threads(concurrency: int):
    """批量期间按并发档耦合 torch intra-op 线程，退出恢复进入前的值（阶段 408）。

    threads = clamp(os.cpu_count() // concurrency, 1, 进入时 torch 线程数)。
    本机 24 逻辑核：conc10 ⇒ 2（阶段 407 探针 #3 实测最优）；conc1 ⇒ 封顶回默认。

    只动 torch intra-op（与探针 #3 口径一致）；不碰 interop
    （set_num_interop_threads 一次性、无法恢复）与 cv2/env（探针未动，避免未测变更）。
    torch 不可用时为 no-op。全局线程爆炸半径见 spec §7：finally 必恢复。
    """
    cores = os.cpu_count() or 2
    conc = max(1, int(concurrency))
    torch = None
    entry = None
    try:
        import torch as _torch

        torch = _torch
        entry = torch.get_num_threads()
    except Exception:
        pass
    hi = entry if entry else cores
    target = max(1, min(cores // conc, hi))
    if torch is not None and entry is not None:
        try:
            torch.set_num_threads(target)
        except Exception:
            pass
    try:
        yield target
    finally:
        if torch is not None and entry is not None:
            try:
                torch.set_num_threads(entry)
            except Exception:
                pass
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/gatevenv/Scripts/python.exe -m pytest tests/test_batch_cpu_threads.py --basetemp=.pytest_plan -v
```
Expected: PASS（5 个用例全绿）。

- [ ] **Step 6: 提交**

```bash
git add core/device_manager.py tests/test_batch_cpu_threads.py
git commit -m "feat(批量并行): 新增 batch_cpu_threads，按并发档耦合 torch 线程并可恢复"
```

---

### Task 3: BATCH_CONCURRENCY 核数感知默认

**Files:**
- Modify: `config.py:2493-2497`（加 helper + 改默认）
- Test: `tests/test_batch_concurrency_default.py`（新建）

**Interfaces:**
- Consumes: `os`（config.py:47 已导入）、`_env_int(key, default, min_value, max_value)`（config.py:104）。
- Produces: `_batch_concurrency_default(cores: int | None) -> int`（纯函数，`min(10, max(2, (cores or 2)//2))`）；`BATCH_CONCURRENCY = _env_int("BATCH_CONCURRENCY", _batch_concurrency_default(os.cpu_count()), 1, 16)`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_batch_concurrency_default.py`：

```python
# -*- coding: utf-8 -*-
"""阶段 408：核数感知批量并发默认公式 + env 覆盖。ci311venv（无 torch）。"""
from __future__ import annotations

import importlib
import os

import config


def test_default_formula_values():
    f = config._batch_concurrency_default
    assert f(24) == 10     # 本机 24 逻辑核 ⇒ 实测最优档
    assert f(16) == 8
    assert f(4) == 2
    assert f(2) == 2       # max(2, 1)=2
    assert f(1) == 2       # 下限 2
    assert f(None) == 2    # 兜底 (None or 2)//2=1 ⇒ max2
    assert f(100) == 10    # 上限 10


def test_env_override_honored(monkeypatch):
    monkeypatch.setenv("BATCH_CONCURRENCY", "3")
    cfg = importlib.reload(config)
    try:
        assert cfg.BATCH_CONCURRENCY == 3
    finally:
        monkeypatch.delenv("BATCH_CONCURRENCY", raising=False)
        importlib.reload(config)


def test_default_within_measured_range():
    # 未设 env 时默认落在 [2,10]；设了 env 则尊重 env（1–16）。
    if os.environ.get("BATCH_CONCURRENCY") is None:
        assert 2 <= config.BATCH_CONCURRENCY <= 10
    else:
        assert 1 <= config.BATCH_CONCURRENCY <= 16
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/ci311venv/Scripts/python.exe -m pytest tests/test_batch_concurrency_default.py --basetemp=.pytest_plan -v
```
Expected: FAIL — `AttributeError: module 'config' has no attribute '_batch_concurrency_default'`。

- [ ] **Step 3: 加 helper 并改默认**

把 `config.py:2493-2497` 这段：

```python
# 【批量处理并发数】
#   同时处理几张图片。注意：显卡模式下建议保持 1，因为显存是瓶颈，
#   开多个并发反而会因为显存争抢而变慢甚至溢出。
#   CPU 模式下可适当调大到 2 ~ 4。
BATCH_CONCURRENCY: Final[int] = _env_int("BATCH_CONCURRENCY", 1, 1, 16)
```

替换为：

```python
def _batch_concurrency_default(cores: "int | None") -> int:
    """核数感知批量并发默认（阶段 408）：min(10, max(2, 逻辑核//2))。

    上限 10 = 阶段 407 探针 #3 实测最优档（更高并发未测）；下限 2 保证小机器也并行。
    口径用 os.cpu_count()（逻辑核），兜底 or 2 防 None。
    """
    return min(10, max(2, (cores or 2) // 2))


# 【批量处理并发数】
#   同时处理几张图片。CPU 模式默认核数感知（本机 24 逻辑核 ⇒ 10，阶段 407 实测最优），
#   批量期间由 core.device_manager.batch_cpu_threads 按并发档耦合 torch 线程。
#   显卡模式建议用 env BATCH_CONCURRENCY=1（显存是瓶颈，多并发会争抢甚至溢出）。
#   env 范围 1–16 不变，可覆盖默认。
BATCH_CONCURRENCY: Final[int] = _env_int(
    "BATCH_CONCURRENCY", _batch_concurrency_default(os.cpu_count()), 1, 16
)
```

- [ ] **Step 4: 跑测试确认通过**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/ci311venv/Scripts/python.exe -m pytest tests/test_batch_concurrency_default.py --basetemp=.pytest_plan -v
```
Expected: PASS（3 个用例全绿）。

- [ ] **Step 5: 审计硬编码旧默认的测试**

Run:
```bash
cd /d/qoder/watermark-workbench
grep -rn "max_concurrency == 1\|BATCH_CONCURRENCY == 1\|BATCH_CONCURRENCY\", 1" tests/ || echo "无硬编码默认==1 断言"
```
Expected: 无硬编码 `==1` 断言（已知 `test_queue_worker_contract.py:49` 动态读 config、`test_queue_worker_branches_audit.py:192` 是显式 `BatchQueue(max_concurrency=2)`，均不受默认改动影响）。若 grep 命中任何硬编码 1，改为动态读 `config.BATCH_CONCURRENCY` 或更新预期后再继续。

- [ ] **Step 6: 跑队列契约确认结构未破坏**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/ci311venv/Scripts/python.exe -m pytest tests/test_queue_worker_contract.py tests/test_queue_worker_branches_audit.py --basetemp=.pytest_plan -q
```
Expected: PASS（契约全绿）。

- [ ] **Step 7: 提交**

```bash
git add config.py tests/test_batch_concurrency_default.py
git commit -m "feat(批量并行): BATCH_CONCURRENCY 默认改核数感知 min(10,max(2,核//2))"
```

---

### Task 4: 接进批量消费循环

**Files:**
- Modify: `ui/tab_batch.py:46-58`（导入区加 batch_cpu_threads）、`ui/tab_batch.py:716-743`（包住 run 循环）
- Test: `tests/test_batch_wiring.py`（新建）

**Interfaces:**
- Consumes: `core.device_manager.batch_cpu_threads`（Task 2）、`queue.max_concurrency`（services/queue_worker.py:144）。
- Produces: `run_batch` 在消费 `queue.run(...)` 期间处于 `batch_cpu_threads(queue.max_concurrency)` 上下文内；退出（含异常 / 生成器关闭）恢复线程，随后已有 finally 清 `_CURRENT_QUEUE`。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_batch_wiring.py`：

```python
# -*- coding: utf-8 -*-
"""阶段 408：run_batch 确实用 queue.max_concurrency 进入 batch_cpu_threads。

gatevenv（torch_required，tab_batch 导入链含 torch）。伪造 BatchQueue（run 空迭代，
不触发真实检测）+ spy 上下文管理器，只验证接线，不做真实修复。
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

pytest.importorskip("torch")
pytestmark = pytest.mark.torch_required

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import ui.tab_batch as tb  # noqa: E402


class _FakeQueue:
    def __init__(self, *a, **k):
        self.max_concurrency = 7
        self._submitted = []

    def submit(self, name, path):
        self._submitted.append((name, path))

    def run(self, processor, on_update=None):
        return iter(())  # 空：不触发真实检测/修复

    def summary(self):
        return {
            "progress": 1.0, "finished": 0, "done": 0, "failed": 0,
            "cancelled": 0, "total": len(self._submitted), "current": None,
            "pending": 0, "running": 0,
        }

    def cancel(self):
        pass


def test_run_batch_wraps_loop_with_batch_cpu_threads(monkeypatch, tmp_path):
    p = tmp_path / "a.png"
    cv2.imwrite(str(p), np.zeros((16, 16, 3), dtype=np.uint8))

    seen = {}

    @contextmanager
    def spy(conc):
        seen["conc"] = conc
        yield conc

    monkeypatch.setattr(tb, "BatchQueue", _FakeQueue)
    monkeypatch.setattr(tb, "batch_cpu_threads", spy)

    gen = tb.run_batch(
        [str(p)], None, None,
        "auto", "auto", 1.0, 0, 0, 128, 0, 1.0,
        False, 1.0, False, 1.0, False, 1.0, False, 1.0, False, 2,
        evaluate=False, compare=False, auto_preset=False, dual_run=False,
    )
    list(gen)  # 消费完整个生成器

    assert seen.get("conc") == 7, "run_batch 应用 queue.max_concurrency 进入上下文管理器"
```

- [ ] **Step 2: 跑测试确认失败**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/gatevenv/Scripts/python.exe -m pytest tests/test_batch_wiring.py --basetemp=.pytest_plan -v
```
Expected: FAIL — `AttributeError: module 'ui.tab_batch' has no attribute 'batch_cpu_threads'`（monkeypatch.setattr 找不到目标）。

- [ ] **Step 3: 加导入**

在 `ui/tab_batch.py` 导入区（46 行 `from core.detector import detect_watermark_mask` 附近）加：

```python
from core.detector import detect_watermark_mask
from core.device_manager import batch_cpu_threads
from core.image_utils import load_image
```

- [ ] **Step 4: 包住 run 循环**

把 `ui/tab_batch.py:716-743` 这段：

```python
    _t0 = _time.perf_counter()
    try:
        for _finished_task in queue.run(_processor, on_update=_on_update):
            s = queue.summary()
            frac = s["progress"]
            progress(frac, t("batch.progress_done", done=s["finished"], total=s["total"]))
            yield frac, "\n".join(log_lines[-200:]), list(gallery), _status_md(s), _agg_stats_md()

        s = queue.summary()
        summary_line = t(
            "batch.summary",
            done=s["done"], failed=s["failed"],
            cancelled=s["cancelled"], total=s["total"],
        )
        log_lines.append(summary_line)
        # 阶段 193（D2）：把整批耗时拼进产物，与单图页 _compose_status 共用 format_elapsed 接缝。
        log_lines.append(format_elapsed(_time.perf_counter() - _t0, key="batch.msg.elapsed"))
        # 阶段 26：智能推荐预设分布（仅在开启推荐且有分布时展示）。
        if auto_preset and _preset_counts:
            _dist = t("batch.list_sep").join(
                f"{_preset_display(k)}×{v}" for k, v in _preset_counts.items()
            )
            log_lines.append(t("batch.log_preset_dist", dist=_dist))
        yield 1.0, "\n".join(log_lines[-200:]), list(gallery), _status_md(s), _agg_stats_md()
    finally:
        # 阶段 336：批次结束（含异常、含前端提前关闭生成器）必须把「当前队列」清空，
        # 否则 `cancel_batch()` 会对着空队列声称「已发送取消信号」。
        _CURRENT_QUEUE = None
```

替换为（在 try 内套 `with batch_cpu_threads(queue.max_concurrency):`，原循环与汇总整体缩进进 with；finally 不变）：

```python
    _t0 = _time.perf_counter()
    try:
        # 阶段 408：批量期间按并发档耦合 torch 线程（退出恢复，异常/生成器关闭安全）。
        with batch_cpu_threads(queue.max_concurrency):
            for _finished_task in queue.run(_processor, on_update=_on_update):
                s = queue.summary()
                frac = s["progress"]
                progress(frac, t("batch.progress_done", done=s["finished"], total=s["total"]))
                yield frac, "\n".join(log_lines[-200:]), list(gallery), _status_md(s), _agg_stats_md()

            s = queue.summary()
            summary_line = t(
                "batch.summary",
                done=s["done"], failed=s["failed"],
                cancelled=s["cancelled"], total=s["total"],
            )
            log_lines.append(summary_line)
            # 阶段 193（D2）：把整批耗时拼进产物，与单图页 _compose_status 共用 format_elapsed 接缝。
            log_lines.append(format_elapsed(_time.perf_counter() - _t0, key="batch.msg.elapsed"))
            # 阶段 26：智能推荐预设分布（仅在开启推荐且有分布时展示）。
            if auto_preset and _preset_counts:
                _dist = t("batch.list_sep").join(
                    f"{_preset_display(k)}×{v}" for k, v in _preset_counts.items()
                )
                log_lines.append(t("batch.log_preset_dist", dist=_dist))
            yield 1.0, "\n".join(log_lines[-200:]), list(gallery), _status_md(s), _agg_stats_md()
    finally:
        # 阶段 336：批次结束（含异常、含前端提前关闭生成器）必须把「当前队列」清空，
        # 否则 `cancel_batch()` 会对着空队列声称「已发送取消信号」。
        _CURRENT_QUEUE = None
```

- [ ] **Step 5: 跑测试确认通过**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/gatevenv/Scripts/python.exe -m pytest tests/test_batch_wiring.py --basetemp=.pytest_plan -v
```
Expected: PASS（seen["conc"]==7）。

- [ ] **Step 6: 提交**

```bash
git add ui/tab_batch.py tests/test_batch_wiring.py
git commit -m "feat(批量并行): 批量消费循环按 queue.max_concurrency 耦合 torch 线程"
```

---

### Task 5: 端到端验证 + 门禁 + 落笔

**Files:**
- Create: `.scratch/bench_batch_e2e_408.py`（端到端基准，验证用）
- Create: `docs/408-batch-parallelism.md`
- Modify: `docs/INDEX.md`、`CHANGELOG.md`（[Unreleased]）、`.workbuddy/memory/2026-11-15.md`（日记）

**Interfaces:**
- Consumes: Task 1-4 的全部改动；真实批量流水线 `run_batch`（含修复 + 保存）；`scripts/real_gate.py`；`run_all_checks`。
- Produces: 端到端实测加速数字（写进 docs/408 与 CHANGELOG）；门禁全绿证据；落笔 4 处。

- [ ] **Step 1: 写端到端基准脚本**

新建 `.scratch/bench_batch_e2e_408.py`（走真实 `run_batch` 全流水线，含 inpaint + 后处理 + PNG 保存；对比 conc1 与新默认；RAM 守卫）：

```python
# -*- coding: utf-8 -*-
"""阶段 408 端到端基准：真实批量流水线 conc1 vs 新默认（含修复+保存）。

探针 #3 只测检测吞吐；本脚本测全流水线墙钟，坐实端到端加速（spec §10）。
RUN: gatevenv，RAM 空闲 >4GB 才跑。Usage: python .scratch/bench_batch_e2e_408.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402
import ui.tab_batch as tb  # noqa: E402


def avail_ram_gb() -> float:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    s = MEMORYSTATUSEX()
    s.dwLength = ctypes.sizeof(s)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s.ullAvailPhys / 1024 ** 3


def pick_images() -> list:
    d = os.path.join(ROOT, "benchmark_real", "images")
    files = sorted(f for f in os.listdir(d)
                   if f.startswith("ai_logo_") and f.lower().endswith(".jpg"))
    return [os.path.join(d, f) for f in files]


def run_once(images: list, conc: int) -> float:
    os.environ["WME_BATCH_CONCURRENCY"] = str(conc)
    # 重新构造队列读取新并发：run_batch 内部 BatchQueue() 读 config.BATCH_CONCURRENCY，
    # 故需 reload config 让默认值随 env 生效。
    import importlib
    importlib.reload(config)
    importlib.reload(tb)
    t0 = time.perf_counter()
    gen = tb.run_batch(
        images, None, None,
        "auto", "auto", 1.0, 0, 0, 128, 0, 1.0,
        False, 1.0, False, 1.0, False, 1.0, False, 1.0, False, 2,
        evaluate=False, compare=False, auto_preset=False, dual_run=False,
    )
    for _ in gen:
        pass
    return time.perf_counter() - t0


def main() -> int:
    free = avail_ram_gb()
    print(f"[ram] available physical: {free:.2f} GB")
    if free < 4.0:
        print("[abort] <4GB free; wait for other jobs to finish.")
        return 2
    images = pick_images()
    n = len(images)
    default_conc = config._batch_concurrency_default(os.cpu_count())
    print(f"[images] {n} ai_logo, cores={os.cpu_count()}, default_conc={default_conc}")

    # 预热（不计时）：加载 reader / 模型，消除冷启动。
    run_once(images[:1], 1)

    wall1 = run_once(images, 1)
    wallN = run_once(images, default_conc)
    print(f"{'conc':>5} {'wall_s':>9} {'img/s':>8} {'speedup':>9}")
    print(f"{1:>5} {wall1:>9.2f} {n/wall1:>8.3f} {1.0:>8.2f}x")
    print(f"{default_conc:>5} {wallN:>9.2f} {n/wallN:>8.3f} {wall1/wallN:>8.2f}x")
    print(f"\n[read] 端到端加速 = {wall1/wallN:.2f}x（检测占 ~64%，Amdahl 估 1.4–1.86×，以本实测为准）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 跑端到端基准（gatevenv，RAM 空闲时）**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/gatevenv/Scripts/python.exe .scratch/bench_batch_e2e_408.py
```
Expected: 打印 conc1 与 conc{default} 的墙钟与加速倍率；加速 > 1（具体倍率以实测记录，不预设）。若 RAM <4GB 返回 2，等空闲再跑。把数字记进 Step 5 的 docs/408。

- [ ] **Step 3: 真实图门禁（检测结果不变）**

Run:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/gatevenv/Scripts/python.exe scripts/real_gate.py --presets general
```
Expected: general 召回 ≥ 0.8078、面积绝对红线全绿（掩码跨并发一致 ⇒ 基线不动）。

- [ ] **Step 4: 全量门禁**

Run（CI 子集 + 全量各一遍）:
```bash
cd /d/qoder/watermark-workbench
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ci311venv/Scripts/python.exe scripts/run_all_checks.py
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 D:/qoder/gatevenv/Scripts/python.exe scripts/run_all_checks.py --full
```
Expected: 两遍全绿（含新增 4 个测试文件、队列契约、reader 锁、线程公式）。若 `run_all_checks.py` 路径/参数与上述不符，先 `ls scripts/` 与 `--help` 核实再跑。

- [ ] **Step 5: 落笔 4 处**

新建 `docs/408-batch-parallelism.md`，记录：背景（默认串行）、测量证据（探针 #3 最优 nt2×conc10=1.86× 检测）、决策（C1）、改动面（4 生产文件）、端到端实测加速（Step 2 数字）、门禁结果（Step 3/4）、已知权衡（全局线程爆炸半径、上限 conc10 未测更高）、核数口径（逻辑核）。同步更新：
- `docs/INDEX.md`：加 408 条目。
- `CHANGELOG.md` [Unreleased]：加「批量默认核数感知并发 + 按并发耦合 torch 线程，端到端实测 X.XX×」。
- `.workbuddy/memory/2026-11-15.md`：记当日实现 + 实测数字 + 规划期细化（只耦合 intra-op 的理由）。

- [ ] **Step 6: 提交（不推送）**

```bash
git add docs/408-batch-parallelism.md docs/INDEX.md CHANGELOG.md .workbuddy/memory/2026-11-15.md .scratch/bench_batch_e2e_408.py
git commit -m "docs(批量并行): 阶段 408 端到端实测加速 + 门禁全绿 + 落笔四处"
```

- [ ] **Step 7: 更新任务状态**

把任务 #56 标 completed；#48（1.0.13 发布+推送）保持 pending（仍等用户点名）。不推送、不发布。

---

## 自审（writing-plans Self-Review）

**1. Spec 覆盖：**
- spec §5 四生产文件 → Task 1（detector）/ Task 2（device_manager）/ Task 3（config）/ Task 4（tab_batch）。✓
- spec §7 reader 锁 → Task 1；全局线程爆炸半径恢复 → Task 2 finally + Task 4 已有 finally。✓
- spec §8 核数口径 + None 兜底 → Task 2/3 公式 `or 2`，Task 2 test_none_cores_fallback。✓
- spec §9 测试计划（公式钳制/恢复/异常/reader 锁/掩码一致/契约/硬编码审计/run_all_checks）→ Task 1-3 测试 + Task 3 Step 5 审计 + Step 6 契约 + Task 5 Step 4。掩码跨并发一致由探针 #3 已证 + Task 5 真实图门禁（检测结果不变）固化。✓
- spec §10 端到端基准 + 真实图门禁 + 单图延迟 → Task 5 Step 2/3；单图延迟由 Task 2 conc1 封顶回默认保证（test_conc1_caps_to_entry）。✓
- spec §13 验收 7 条 → Task 5 全覆盖（含落笔 4 处、不推送）。✓

**2. 占位符扫描：** 无 TBD/TODO；每个代码步给出完整可运行代码。✓

**3. 类型/命名一致：** `batch_cpu_threads(concurrency)` 在 Task 2 定义、Task 4 导入消费，签名一致；`_batch_concurrency_default(cores)` 在 Task 3 定义并测试；`_OCR_READER_LOCK` Task 1 定义并使用；`queue.max_concurrency` 与 queue_worker.py:144 一致。✓

**规划期细化（已记入 Global Constraints）：** spec §5 原文「复用 apply_cpu_threads」，规划时发现其 `set_num_interop_threads` 一次性不可恢复、且 cv2/env 为探针未测变更 ⇒ 上下文管理器只耦合可恢复的 torch intra-op 子集，更贴合探针 #3 实测口径。意图（按并发耦合 + 退出恢复）不变。