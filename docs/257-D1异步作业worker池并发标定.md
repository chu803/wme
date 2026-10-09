# 阶段 257 · D1 异步作业 worker 池并发标定

> 收口 D1（并发策略）：把「异步作业模型的 worker 池并发数」从**硬编码不可配置**变为
> **可配置常量 `API_ASYNC_MAX_WORKERS`（默认 2）**，并用 RTX 5060 真实标定其默认值。

---

## 一、背景与杠杆定位

异步作业模型（255）把 PDF / 视频 / 模型下载等长任务放进 worker 线程，运行在 `_BoundedThreadingHTTPServer`
并发闸门**之外**。其并发度由 `JobManager(max_workers=...)` 决定：

- 模块级单例 `JOB_MANAGER = JobManager()` 此前**硬编码默认 `max_workers=2`**；
- 该值**不可配置**，用户无法按自己的负载（批量吞吐优先 vs 单请求延迟优先）调参。

D1 目标：用真实 GPU 标定一个证据驱动的默认值，并将其配置化。

> 注：这是**长任务 worker 池**的并发，与 HTTP 层 `API_MAX_CONCURRENCY`（闸门，默认 4）
> 是**两个独立杠杆**——worker 池在闸门之外，二者互不约束。

---

## 二、探针与标定结果（RTX 5060 Laptop，8123 MiB）

探针：对 `max_workers ∈ {1,2,3,4}` 各起独立 `JobManager`，并发提交等量真实 2 页 PDF
（1200×900，真实跑 detect + LaMa inpaint），测量墙钟、成功率、峰值 VRAM、单作业耗时。

| k | 墙钟(s) | 成功 | 峰值VRAM(MB) | 单作业均耗时(s) | 聚合吞吐(页/s, 相对k=1) |
|---|---|---|---|---|---|
| 1 | 12.06 | 1 | 613.8 | 11.86 | 0.166 (1.00×) |
| 2 | 10.02 | 2 | 818.2 | **8.62** | 0.399 (2.40×) |
| 3 | 13.64 | 3 | 1007.5 | 10.70 | 0.440 (2.65×) |
| 4 | 17.09 | 4 | 1272.7 | 12.82 | 0.468 (2.82×) |

### 解读
- **VRAM 随 k 线性但极轻**：k=4 仅占 8123 MiB 的 **16%** ⇒ VRAM **不是瓶颈**；瓶颈是 GPU 算力争用。
- **单请求延迟甜点在 k=2**：8.62s，比串行（k=1）快 **27%**；k≥3 起单作业延迟回升（争用导致）。
- **聚合吞吐随 k 缓增但收益递减**：k=2→3 仅 +10% 吞吐、+24% 延迟；k=3→4 仅 +6% 吞吐、+20% 延迟。
- **本工具定位本地单用户**，最相关指标是「我的文件多久好」= **单请求延迟**，故甜点为 **k=2**：
  延迟最优 + 仅 ~10% VRAM + 视频作业（逐帧 inpaint，单帧峰值同量级）仍有大余量。

> 若面向**批量吞吐优先**（多用户 / 队列场景），可上调至 **4**：吞吐 +82%、单作业延迟约 +18%，
> VRAM 仍仅 ~16%，安全。

---

## 三、结论与改动

1. **默认值定为 `API_ASYNC_MAX_WORKERS = 2`**（与原有硬编码值一致，行为零回归），
   但**配置化**：环境变量 `WME_API_ASYNC_MAX_WORKERS`（范围 [1,8]）可覆盖。
2. `job_manager.JobManager` 新增 `self.max_workers` 属性（记录实际 worker 数），
   模块级单例改为 `JOB_MANAGER = JobManager(max_workers=config.API_ASYNC_MAX_WORKERS)`。
3. 守卫 `tests/test_api_async_concurrency_d1.py`（5 例，纯线程、不碰 GPU）：
   - 配置值落在 [1,8]；
   - 生产单例 worker 数 == 配置值；
   - 并发提交（max_workers=3）全部到达 success；
   - **max_workers=1 ⇒ 严格串行**（job0 end 早于 job1 start）；
   - 显式传入 max_workers 被记录。

---

## 四、自反证伪（诚实边界）

- 探针仅覆盖 **2 页 PDF @1200×900** 这一种负载；**未覆盖**超长/超高分辨率视频
  （多帧串行 inpaint，单作业墙钟可数分钟）与**模型下载**（网络瓶颈、几乎不占 GPU）。
  但这些场景的每帧/每时刻 VRAM 峰值与 PDF 同量级，k=4 仍仅 16% 卡容，安全余量充足；
  一旦未来出现单作业显存需求逼近卡容，应下调默认而非上调。
- `max_workers` 是进程内 worker 线程数，**非分布式**；多进程 / 多机扩展不在此杠杆范畴。
- 标定结论依赖 RTX 5060 Laptop（Blackwell，8123 MiB）；在显存更小的卡上，
  若单作业 VRAM 占比升高，应下调默认值——故保留 [1,8] 的可覆盖区间。
- 真实 GPU 负载的端到端正确性由 256 守卫背书；本阶段守卫只验证**并发契约**，不重跑重负载。

---

## 五、验证

- `pytest tests/test_api_async_concurrency_d1.py tests/test_api_async_jobs255.py` → **16 passed**。
- `pytest tests/test_api_async_jobs_e2e256.py` → 2 passed（重负载回归，job_manager 改动零影响）。
- 探针脚本：`.scratch/probe_d1_concurrency.py`（测量可复现）。
