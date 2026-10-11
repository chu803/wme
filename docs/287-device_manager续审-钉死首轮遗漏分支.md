# 287 · core/device_manager 续审 — 钉死首轮遗漏的缓存/探测/节流分支

> 5j 深度复审链第 12 站。阶段 89（设备与模型管理真值审计）的 `device_manager` 子模块的二次复审。
> 荒漠指标：396 行 / 16 引用 = **比 24.8**（本轮候选次高，仅次于 286 的 model_manager）。

## 结论
**零真缺陷**。该模块首轮（阶段 89 真值审计 + D1 `test_device_manager_gpu.py` 把 CUDA 分支从「死代码路径风险」钉成可机判定契约）已把 auto/cuda/cpu 三模式主路径 + 显存安全水位降级 + fp16 门控覆盖扎实。本轮补的是**首轮未机控的分支不变量**。探针 27 项实跑全部正确，新增 16 例守卫全绿，全回归 **72 例全绿**（含 device_manager 续审+GPU 契约+core_audit89+ocr 设备选择+GPU 数值守卫+model_manager），EXIT=0、无夹具泄漏。

## 覆盖清单（16 例守卫 + 探针 27 项）
| 类别 | 钉死的不变量 |
|---|---|
| `select_device` 缓存复用 | auto 复用已选 cuda / 已选 cpu（**不重新探测**，这是有意语义）；模式变化（cpu↔cuda）才重新探测；同模式返回**同一对象**（身份相等） |
| `select_device(None)` | 走 `config.DEVICE_MODE` 默认（auto），无 GPU 时退回 cpu |
| `_detect_cuda` torch 缺失 | `import torch` 失败 → 返回 None（不抛异常，退回 CPU 路径） |
| `_detect_cuda` props 异常 | `get_device_properties` 抛异常 → 返回 None，且 `select_device("auto")` 退回 cpu |
| `_query_free_vram_mb` NVML | 成功路径返回剩余 MB 且 `nvmlShutdown` 被调用；mem 失败 / init 失败两条异常路径均返回 None 且 `nvmlShutdown` 在 finally 中仍调用（**无 NVML 资源泄漏**，阶段 14 已修、本轮钉死） |
| `apply_cpu_threads` | 默认 `CPU_THREADS=0` → 守卫 `n and n>0` 成立为假 → 不调 `torch.set_num_threads`、不设 env（no-op）；显式 `n=2` → 调用且写入 `OMP_NUM_THREADS`/`MKL_NUM_THREADS` |
| `empty_cache` 节流 | 首次调用执行；立即二次调用被节流跳过；`force=True` 绕过；`config.DEVICE_EMPTY_CACHE_AFTER_RUN=False` 时完全不调用（阶段 14 节流、本轮钉死边界） |
| `use_fp16` | `config.DEVICE_USE_FP16=False` 时（即便 GPU）返回 False |
| `_DeviceHandle` | `torch_device` 在 cpu→`torch.device("cpu")`、cuda→`torch.device("cuda:0")`；`is_gpu` 正确委托底层 `DeviceInfo` |

## 归因 / 重点
最值得指出的是 **`select_device` 的缓存复用语义是首轮完全漏测的**：
- `auto` 模式会**复用之前已定下来的设备**（无论 GPU/CPU），不再重新探测。这是有意设计（避免每次推理都重探抖动），但如果不钉死，后人可能误改成「每次 auto 都重新探测显存」，在批量/视频逐帧场景引入不必要的探测开销。
- 反之，模式**显式变化**（cpu↔cuda）才会重新探测——这条「何时重探、何时复用」的边界，正是缓存层最易退化的地方。

另：`_query_free_vram_mb` 的 **NVML try/finally 资源泄漏守卫**（阶段 14 修的「静默容错把资源泄漏藏起来」）此前无任何测试直接命中三条异常路径；本轮用注入式 fake pynvml 三连测，确认 `nvmlShutdown` 在成功/异常路径都必被调用。

## 交付与落盘
- 守卫：`tests/test_device_manager_branches_audit.py`（16 例）
- 探针：`.scratch/probe_device_manager_reaudit.py`（27 项全 OK）
- 文档：`docs/287-device_manager续审-钉死首轮遗漏分支.md`
- 登记/记忆：`audit_selector.py` 补 287；`MEMORY.md`（264-287）+ `2026-10-11.md`
- 提交：**287**
- 离线包：`.scratch/wme.bundle`（含本提交 + 完整历史）

## 下一站候选（5j 续审链）
已做 12 站：276–280 零缺陷、281 修复 1 缺陷、282–284 零缺陷、285 修复 3 缺陷、286–287 零缺陷。
余下候选：`logger`(90) / `theme`·`app`(91)。
