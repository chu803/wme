# 批量并行提速设计（C 方案：线程并发 + 按并发档耦合 torch 线程）

- 日期：2026-11-15
- 状态：设计已获用户批准（「你决定」⇒ 拍板 C1）；待 writing-plans 出实现计划
- 候选阶段号：408（实现完成后按项目惯例落 `docs/408-*.md` + INDEX + CHANGELOG + 日记）
- 关联：阶段 407（真实图门禁基线复校）、任务 #56

## 1. 背景与问题

批量页（`ui/tab_batch.py`）默认**串行**：`BATCH_CONCURRENCY` 默认 1
（`config.py:2497`，env 可调 1–16），`BatchQueue()` 无参构造用此默认
（`ui/tab_batch.py:570`）。`BatchQueue` 内部是 `ThreadPoolExecutor`
（`services/queue_worker.py`），结构上支持并发，但默认没开。

本机 CPU 检测单图 ≈ 4.9s（2048² 含水印 general+OCR，探针与门禁独立佐证），
占 2048² 全流水线成本 ~64%。批量 N 张串行 ⇒ 总耗时 ≈ N × 单图。机器有
16 物理 / 24 逻辑核，串行只用一小部分 ⇒ 吞吐有显著并行空间。

目标：在**不改检测算法、不降承诺召回（0.8078）、不破坏队列契约**的前提下，
提高批量吞吐。

## 2. 测量证据（先测后决，已实测）

探针均走真实生产路径 `detect_watermark_mask` + 真实 `BatchQueue`，gatevenv
（torch+EasyOCR），共享模块级 reader 单例。

### 探针 #1（8 张干净 1024²，torch 默认 16 线程）
- 安全：conc 1/2/3/4 掩码与串行逐项一致 ⇒ 共享 reader 并发无竞争。
- 缩放弱且非单调（conc2=1.16× / conc4=1.41×）：干净照片 GIL-held 占比大 +
  torch intra=16 超订两个混淆 ⇒ 不能据此判线程方案死刑。

### 探针 #2（4 张 2048² 含水印 ai_logo，torch 线程 {16,1}）
| nt | conc | wall | speedup(对 nt 自身 conc1) | mask_match |
| --- | --- | --- | --- | --- |
| 16 | 1 | 19.46s | 1.00× | ✓ |
| 16 | 2 | 14.56s | 1.34× | ✓ |
| 16 | 4 | 17.53s | 1.11×（超订退化） | ✓ |
| 1 | 1 | 91.78s | 1.00× | ✓ |
| 1 | 2 | 46.18s | 1.99× | ✓ |
| 1 | 4 | 24.61s | 3.73× | ✓ |

- nt1 近完美缩放 ⇒ **GIL 不是瓶颈**（torch/cv2 释放 GIL，`detector.py:1528` 注释佐证）。
- nt1 单图 22.9s vs nt16 单图 4.87s ⇒ 单线程 torch 单图慢 ~4.7× ⇒
  **线程数必须与并发耦合，不能全局设 1**。

### 探针 #3（10 张 2048² 含水印 ai_logo，7 档扫描，exit 0）
| nt | conc | wall | img/s | vs 默认(nt16conc1) | ok | mask_match |
| --- | --- | --- | --- | --- | --- | --- |
| 16 | 1 | 51.84s | 0.193 | 1.00×（当前默认） | ✓ | ✓ |
| 16 | 2 | 38.16s | 0.262 | 1.36× | ✓ | ✓ |
| 1 | 4 | 73.88s | 0.135 | 0.70×（回归） | ✓ | ✓ |
| 1 | 8 | 54.13s | 0.185 | 0.96× | ✓ | ✓ |
| 1 | 10 | 34.69s | 0.288 | 1.49× | ✓ | ✓ |
| **2** | **10** | **27.91s** | **0.358** | **1.86×（最优） | ✓ | ✓ |
| 4 | 10 | 30.56s | 0.327 | 1.70× | ✓ | ✓ |

- **真最优 = nt2 × conc10 = 1.86×**（检测吞吐）。nt4 conc10（40 线程）轻度超订回落 1.70×。
- **耦合公式获实证**：`threads = max(1, 逻辑核 // conc)`，24 // 10 = 2 ⇒ 正好推出实测最优 nt2。
- 全 7 档 `mask_match=True`（含跨 nt）⇒ 线程调度 + 共享 reader **不改检测结果**。
- naive 改法（只提并发到 4 + 全局 threads=1）= 0.70×，**比默认还慢** ⇒ 必须高并发与降线程同时。

## 3. 决策

**采用 C（线程并发 + 按并发档耦合 torch 线程），默认策略 C1（直接改核数感知默认）。**

排除 B/A 的理由：探针 #2 已证 GIL 非瓶颈 ⇒ 进程隔离（B 检测进程池 / A 整流水线进程池）
买不到额外吞吐，只徒增内存（B/A 每进程独立 reader ~2.3GB，N 路 ⇒ N×2.3GB，16GB 机器吃紧）
与重构成本（A 要把取消/重试/进度流式全部跨进程重做）。C 共享 1 个 reader、保留线程池 ⇒
队列契约全不动、内存最省。B 仅留作「C 的全局线程爆炸半径若不可接受」时的退路（本次不实现）。

C1 而非 C2（UI 滑块）/C3（opt-in 开关）：最简、无 UI 改动、内存安全（共享 1 reader），
测量判决已支持默认翻转（1.86×、掩码全一致）。env `BATCH_CONCURRENCY` 仍可覆盖（1–16）。

## 4. 架构与机制

保留 `BatchQueue` 的 `ThreadPoolExecutor`（结构不动）。两件**耦合**的事：

1. **并发默认核数感知**：`BATCH_CONCURRENCY` 默认从 1 改为
   `min(10, max(2, os.cpu_count() // 2))`（本机 24 逻辑核 ⇒ 10，即实测最优档；
   小机器自适应下调；上限 10 = 实测值，更高并发未测）。
2. **批量期间耦合 torch 线程**：用现成但生产休眠的 `apply_cpu_threads()`
   （`core/device_manager.py:288`）把 torch 线程调到
   `threads = clamp(os.cpu_count() // conc, 1, 进入批量时的 torch 线程数)`。
   - 本机 conc10 ⇒ clamp(24//10=2, 1, 16) = 2（实测最优）。
   - conc1 ⇒ clamp(24, 1, 16) = 16（**封顶回默认，不降单图延迟**）。
   - 批量结束**恢复**进入前的线程数。

`apply_cpu_threads` 现状：设 `torch.set_num_threads/interop` + `cv2.setNumThreads` +
OMP/MKL env；`config.CPU_THREADS` 默认 0=no-op（`config.py:613`），唯一调用点是
`device_manager.py:386` 的 `__main__` 自检 ⇒ 生产从不设 torch 线程（torch 自带默认 16）。
本方案把这套休眠机制按并发档接进批量路径，不新造轮子。

## 5. 组件与改动面（最小，3 个生产文件 + 测试）

- **`config.py:2497`**：`BATCH_CONCURRENCY` 默认改 `min(10, max(2, os.cpu_count() // 2))`
  （env 范围 1–16 不变；确认 `os` 已导入）。
- **`core/device_manager.py`**：新增上下文管理器 `batch_cpu_threads(concurrency)`：
  进入记录 `torch.get_num_threads()`（及 interop）→ 按公式 `apply_cpu_threads(threads)`；
  退出恢复记录值。复用现有 `apply_cpu_threads`，不改动其行为。
- **`ui/tab_batch.py:716-743`**：用 `batch_cpu_threads(queue.max_concurrency)` 包住
  `queue.run(...)` 消费循环；恢复挂在**已有 finally**（与 `_CURRENT_QUEUE = None` 同处，
  异常 / 前端提前关闭生成器都安全）。
- **`core/detector.py:151`**：`_get_ocr_reader` 创建加 `threading.Lock`（双重检查），
  保证并发首次初始化只建一个 reader（见 §7）。
- **不动**：`services/queue_worker.py` 结构、inpainter、预设、检测算法、修复引擎默认。

## 6. 数据流

```
批量开始
  → conc = queue.max_concurrency（来自新默认 / env 覆盖）
  → 进入 batch_cpu_threads(conc)：记录旧线程 → threads=clamp(cores//conc,1,旧) → apply_cpu_threads(threads)
  → queue.run(_processor)：N 个 worker 线程共享 1 个 reader，每次检测用 threads 个 torch 线程
  → 逐个 yield 完成任务（on_update 单线程消费，不变）
  → finally：恢复旧线程 + _CURRENT_QUEUE=None
```

## 7. 并发安全（两个必须处理的点）

1. **reader 初始化锁**：`_get_ocr_reader`（`detector.py:151`）创建无锁，`_OCR_READER`
   模块级单例（`detector.py:87`）。探针是**预热后**才并发，未覆盖「并发首次初始化」竞争。
   加 `threading.Lock` + 双重检查 ⇒ 并发首检只建一个 reader。小改、防御性、不改检测语义。
2. **全局线程爆炸半径**：`torch.set_num_threads` 是**进程全局**。检测生产调用方有
   批量（`tab_batch.py:400`）、自动/单图（`tab_auto.py:300/456`）、历史（`tab_history.py:426`）、
   API（`api.py:798`）、`services/auto_pipeline.py:289`。高并发批量期间把线程降到 2，
   若用户**同时**跑单图会连带变慢。缓解：
   - finally 必恢复（异常 / 生成器关闭安全）；
   - conc1 封顶回默认（单图批量不降延迟）；
   - 文档写明「高并发批量期间并发单图可能变慢」——批量是主导负载、并发单图罕见，可接受。
   单图 TAB 不走批量路径，无批量运行时始终用默认线程。

## 8. 核数口径

公式用 **`os.cpu_count()`（逻辑核）**，与探针一致（探针打印 cores=24=逻辑核）。
本机 16 物理 / 24 逻辑；torch 默认 intra=16。用逻辑核 24 ⇒ conc10/threads2 复现实测最优；
若误用物理核 16 ⇒ 16//10=1（nt1=1.49×，次优）。故口径钉死逻辑核。

`os.cpu_count()` 理论上可返回 `None`（极少见，Windows 恒有值）⇒ 实现兜底
`cores = os.cpu_count() or 2`，避免 `None // 2` 抛错。clamp(x, lo, hi) = max(lo, min(x, hi))。

## 9. 测试计划

- **扩 `tests/test_device_manager_branches_audit.py`**：
  `batch_cpu_threads` 公式钳制（cores//conc，下限 1、上限进入值）、进入设线程 / 退出恢复、
  异常路径仍恢复。
- **新增 reader 并发初始化测试**：多线程同时首检 ⇒ 只建一个 reader（锁生效）。
- **新增批量掩码跨并发一致回归测试**：同图 conc1 vs concN 掩码一致（探针已证，固化）。
- **队列契约测试全跑**（`tests/test_queue_worker_contract.py`）：结构未动 ⇒ 应全绿。
- **审计硬编码旧默认的测试**：grep `BATCH_CONCURRENCY` / `max_concurrency == 1` 的断言，
  默认改 10 后如有硬编码 1 的用例需改为动态读 config 或更新预期。
- 全量 `run_all_checks`。

## 10. 验证（端到端，不止检测）

- **端到端全流水线基准**：探针 #3 只测**检测**吞吐（load+detect+md5），不含
  inpaint/后处理/PNG 保存。实现后须用真实批量流水线（含修复+保存）测 conc1 vs 新默认
  的端到端墙钟，确认实际加速。检测占 2048² ~64%，Amdahl 估端到端 1.4–1.86×
  （非检测阶段也并发 ⇒ 偏上限）；**以实测为准，不预设结论**。
- **真实图门禁**（`scripts/real_gate.py --presets general`）：检测结果不变（掩码跨并发一致）
  ⇒ 基线应仍绿；跑一遍确认召回 0.8078 / 面积红线不动。
- 单图延迟回归：确认单图 TAB / conc1 批量延迟不劣化（线程封顶回默认）。

## 11. 口径限定与风险

- 1.86× 是**本机 24 逻辑核 / 16GB / Win、10 张 2048² ai_logo、general+OCR、检测-only** 口径。
  绝对倍率随机器 / 负载 / 图尺寸 / 预设变；公式按核数推导 ⇒ 跨机器自适应，但小机器最优档需复测。
- 端到端加速待全流水线基准坐实（§10），不预先承诺具体倍率。
- 全局线程爆炸半径（§7）是已知权衡，非缺陷；文档明示。
- 上限 conc10 是实测值，更高并发未测；env 可覆盖到 16 但属用户自担。

## 12. 不做（YAGNI）

- 进程池 B/A（证据排除：GIL 非瓶颈，进程隔离买不到额外吞吐）。
- GPU（本机无 NVIDIA）。
- 检测算法 / 预设 / 阈值改动（保召回 0.8078）。
- 修复引擎默认切换（`INPAINT_AUTO_PREFER=opencv` 不动）。
- UI 并发滑块（C2）/ opt-in 开关（C3）——本次 C1 不做。
- PNG 保存 level 调整（独立杠杆，另案，不混入本方案）。

## 13. 验收标准

1. 队列契约测试全绿；新增线程公式 / 恢复 / reader 锁 / 掩码一致测试通过。
2. `run_all_checks` 全绿。
3. 真实图门禁 general 召回 ≥ 0.8078、面积绝对红线全绿（检测结果不变）。
4. 端到端全流水线基准实测加速 > 1（具体倍率以实测记录，不预设）。
5. 单图 / conc1 批量延迟不劣化。
6. 落笔 4 处：`docs/408-*.md` + INDEX + CHANGELOG [Unreleased] + 日记。
7. 不推送 / 不发布（1.0.13 仍等用户点名）。