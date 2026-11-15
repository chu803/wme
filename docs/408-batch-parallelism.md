# 408 · 批量并行提速：核数感知并发 + 按并发档耦合 torch 线程（C1 方案）

- 日期：2026-11-15
- 前置：阶段 407（真实图门禁基线复校）、任务 #55/#56；探针 #1/#2/#3（`.scratch/profile_thread_scaling407c.py`）
- 设计 spec：`docs/superpowers/specs/2026-11-15-batch-parallelism-design.md`（用户「你决定」拍板 C1）
- 实现计划：`docs/superpowers/plans/2026-11-15-batch-parallelism.md`
- 结论：批量页默认从**串行**改为**核数感知线程并发**，并按并发档耦合 torch intra-op 线程。
  本机 10 张 2048² ai_logo 全流水线实测 **conc1=93.56s → conc10=35.10s ⇒ 2.67×**。
  检测算法/预设/阈值未动，承诺召回与面积红线不变。

## 一、背景与问题

批量页（`ui/tab_batch.py`）默认串行：`BATCH_CONCURRENCY` 默认 1，`BatchQueue()` 无参构造用此默认。
`BatchQueue` 内部是 `ThreadPoolExecutor`，结构上支持并发但默认没开。本机 CPU 检测单图 ≈ 4.9s
（2048² 含水印 general+OCR），占全流水线 ~64%；机器 16 物理 / 24 逻辑核，串行只用一小部分 ⇒ 吞吐有并行空间。

## 二、测量证据（先测后决）

探针均走真实生产路径 `detect_watermark_mask` + 真实 `BatchQueue`，gatevenv（torch+EasyOCR），共享模块级 reader 单例。

- **探针 #2**：nt1 近完美缩放（conc2=1.99× / conc4=3.73×）⇒ **GIL 不是瓶颈**（torch/cv2 释放 GIL）。
  nt1 单图比 nt16 慢 ~4.7× ⇒ **线程数必须与并发耦合，不能全局设 1**。
- **探针 #3**（10 张 2048² ai_logo，7 档扫描）：

| nt | conc | wall | img/s | vs 默认 | mask_match |
| --- | --- | --- | --- | --- | --- |
| 16 | 1 | 51.84s | 0.193 | 1.00×（旧默认） | ✓ |
| 1 | 4 | 73.88s | 0.135 | 0.70×（naive 回归） | ✓ |
| 1 | 10 | 34.69s | 0.288 | 1.49× | ✓ |
| **2** | **10** | **27.91s** | **0.358** | **1.86×（检测最优）** | ✓ |
| 4 | 10 | 30.56s | 0.327 | 1.70×（轻度超订） | ✓ |

- 真最优 = nt2 × conc10 = 1.86×（检测吞吐）；耦合公式 `threads = max(1, 逻辑核 // conc)`，24//10=2 正好推出实测最优。
- 全 7 档 mask_match=True ⇒ 线程调度 + 共享 reader **不改检测结果**。
- naive 改法（只提并发 + 全局 threads=1）= 0.70×，比默认还慢 ⇒ 必须高并发与降线程同时。

## 三、决策

采用 **C（线程并发 + 按并发档耦合 torch 线程）**，默认策略 **C1（直接改核数感知默认）**。
排除 B/A（进程池）：GIL 非瓶颈 ⇒ 进程隔离买不到额外吞吐，只徒增内存（每进程独立 reader ~2.3GB）与重构成本。
C 共享 1 个 reader、保留线程池 ⇒ 队列契约全不动、内存最省。env `WME_BATCH_CONCURRENCY` 仍可覆盖（1–16）。

## 四、改动面（4 个生产文件）

| 文件 | 改动 |
| --- | --- |
| `config.py` | 新增 `_batch_concurrency_default(cores)=min(10,max(2,(cores or 2)//2))`；`BATCH_CONCURRENCY` 默认从 1 改为该值（env 范围 1–16 不变） |
| `core/device_manager.py` | 新增上下文管理器 `batch_cpu_threads(conc)`：进入记录 `torch.get_num_threads()`，设 `clamp(核//conc,1,进入值)`，退出恢复 |
| `ui/tab_batch.py` | 导入 `batch_cpu_threads`，用 `with batch_cpu_threads(queue.max_concurrency)` 包住消费循环；恢复挂已有 finally |
| `core/detector.py` | `_get_ocr_reader` 加 `_OCR_READER_LOCK` + 双重检查，并发首检只建一个 reader |

**不动**：`services/queue_worker.py` 结构、inpainter、预设、检测算法、修复引擎默认（`INPAINT_AUTO_PREFER=opencv`）。

### 规划期细化（与 spec §5 的差异）

spec §5 原文「复用 `apply_cpu_threads`」。规划时发现 `apply_cpu_threads` 会调
`torch.set_num_interop_threads`（**一次性、无法恢复**）并设 cv2/env（探针未动 ⇒ 未测变更）。
故 `batch_cpu_threads` **只耦合可恢复的 torch intra-op 子集**，与探针 #3 实测口径完全一致、爆炸半径最小。
意图（按并发耦合 + 退出恢复）不变。

## 五、端到端实测（不止检测）

探针 #3 只测检测吞吐；本阶段用真实批量流水线（含修复 + 后处理 + PNG 保存）测全链路墙钟。
脚本 `.scratch/bench_batch_e2e_408.py`（gitignore 不入库，本地可复现）：10 张 2048² ai_logo，
`mode=auto engine=auto`，`evaluate/compare/auto_preset/dual_run=False`，**强制缓存未命中**
（`tb._CACHE.find_by_hash→None`，否则 conc1 跑完入缓存、concN 秒回造成假加速）。

| conc | wall_s | img/s | speedup |
| --- | --- | --- | --- |
| 1 | 93.56 | 0.107 | 1.00× |
| 10（新默认） | 35.10 | 0.285 | **2.67×** |

- 端到端 2.67× **高于**检测-only 1.86×：非检测阶段（inpaint/保存）也随 10 路并发并行 ⇒ 偏 Amdahl 上限之上。
- 口径：本机 24 逻辑核 / 16GB / Win、10 张 2048² ai_logo、general+OCR、单次测量；绝对倍率随机器/负载/图尺寸/预设变。
- 教训：首跑 conc10=0.07s（1293×）是**缓存命中假象**，强制未命中重测排除 ⇒ 并发基准必须隔离缓存。

## 六、并发安全与已知权衡

1. **reader 初始化锁**：探针是预热后才并发，未覆盖「并发首次初始化」竞争 ⇒ 加双重检查锁固化（测试 8 线程首检只建 1 个）。
2. **全局线程爆炸半径**：`torch.set_num_threads` 进程全局。高并发批量期间把线程降到 2，若用户**同时**跑单图会连带变慢。
   缓解：finally 必恢复（异常/生成器关闭安全）；conc1 封顶回默认（单图批量不降延迟）；单图 TAB 不走批量路径。
   此为已知权衡（批量是主导负载、并发单图罕见），非缺陷。
3. **核数口径**：钉死 `os.cpu_count()`（逻辑核 24），兜底 `or 2`；误用物理核 16 会推出次优 nt1。
4. **上限 conc10** 是实测值，更高并发未测；env 可覆盖到 16 但属用户自担。

## 七、验证

- 新增测试：`test_ocr_reader_lock.py`（reader 锁，ci311venv）、`test_batch_cpu_threads.py`（公式钳制/恢复/异常/None 兜底，5 例）、
  `test_batch_concurrency_default.py`（默认公式 + env 覆盖，3 例）、`test_batch_wiring.py`（run_batch 接线）。
- 既有测试替身补齐 `max_concurrency`（`test_elapsed_display_d2` / `test_tab_batch_branches_audit` / `test_batch_params_wiring405`）；
  批量回归 112 例全绿；队列契约 26 例全绿。
- **真实图门禁**（`--presets general`，gatevenv，1362.5s）：✅ 通过。四条面积红线与 407 基线**逐项一致**
  （faint_tiled 3.02%≤0.075 / text_heavy 1.49%≤0.0425 / grid 2.71%≤0.125 / gradient 0.00%≤0.05），
  余量体检通过 ⇒ 检测结果未变（general 召回 0.8078）。a1–a4 已知缺口照旧排除（#42）。
- **run_all_checks CI 子集**（ci311venv，ci_equivalent）：✅ 全部通过，3535 passed / 44 skipped / 6 deselected，
  undef_scan / docker_static / corner_signal_gate 均 rc=0。
- **红线重标后 real_gate 复跑**（mask_iou 0.25，gatevenv，1477.2s）：✅ 通过，余量体检过、面积红线逐项一致。
- run_all_checks `--full`（gatevenv 全量含 torch，1864.96s）：3656 passed / 9 skipped / **9 failed**。
  9 条失败**均非本次引入**：8 条环境依赖型 torch 用例（edge_text_recall×4 / mask_param_calibration×2 /
  detector_box_refine / prompted_area_gate）在**基线提交 b03dc66 的 worktree 里同样红**（反例实证，
  系字体渲染/torch 版本环境差异，CI 子集按 torch_required 跳过）；1 条 test_cpu_load_probe 是
  与 real_gate 并行的负载抖动，独占 CPU 复跑即绿。**批量并行改动新增失败 = 0**。

## 七补 · 连带收口 407 遗留（单独提交，非批量并行引入）

CI 子集首跑暴露 2 条**预存在**失败（与批量并行无关）：`test_detector_audit100::test_real_baseline_reflects_the_mser_removal`、
`test_overdetection99::test_real_baseline_matches_converged_values`。根因＝阶段 407 重录基线
（precision 0.093→0.1243 / mask_iou 0.3325→0.4185 / coverage 0.0162→0.0148 / ratio 2.83→2.51）
却漏同步两个硬编码守卫常量。处置（用户拍板「完整修」）：
- `MSER_OFF` / `CONVERGED` 同步到 407 基线值。
- **mask_iou 红线 0.20→0.25**：mask_iou 改善 +26% 后 0.20 松于实测一半（0.4185/2=0.20925），
  违反防哑巴不变式 ⇒ 按 183/243 同款机制重锚到 0.25（留 1.67× 余量，min 型抬高=放宽，不制造新红灯）。
- 全部 7 个红线消费者测试文件 151 例复跑全绿（含夹逼不变式 / 注入反例 / 余量体检）；CI 子集转绿。

## 八、不做（YAGNI）

进程池 B/A（证据排除）、GPU（本机无 NVIDIA）、检测算法/预设/阈值改动、修复引擎默认切换、
UI 并发滑块（C2）/ opt-in 开关（C3）、PNG 保存 level 调整（独立杠杆另案）。

## 九、提交（本地，未推送）

- `b896d5c` fix(批量并行): OCR reader 并发首检加双重检查锁
- `b6889f1` feat(批量并行): 新增 batch_cpu_threads
- `003eb8f` feat(批量并行): BATCH_CONCURRENCY 默认改核数感知 min(10,max(2,核//2))
- `edfb6bf` feat(批量并行): 批量消费循环按 queue.max_concurrency 耦合 torch 线程（含替身补齐）
- 不推送 / 不发布（1.0.13 仍等用户点名）。