# 阶段 255 · API 异步作业模型（PDF / 视频 / 模型下载）

> 收口阶段 251 的「未决」项：PDF 擦除 / 视频擦除 / 模型下载这三类**长任务**此前在
> `server.py` 里**始终没有 HTTP 入口**——用法页原话：「均需先设计异步作业模型」。
> 本阶段把长任务从「同步 POST 阻塞推理闸门」改为「提交即返回 + 后台 worker 线程异步执行」。

---

## 一、结论

- 新增 `job_manager.py`（stdlib-only，与全项目零依赖 REST 风格一致），提供进程内异步作业管理器。
- `server.py` 接线三端点：
  - `POST /api/jobs` `{kind, params}` → 立即返回 `{status:"queued", job_id, poll}`（**毫秒级，只入队**）。
  - `GET  /api/jobs/{id}` → 轮询 状态 / 进度(0~1) / 结果 / 错误。
  - `GET  /api/jobs/{id}/file` → 流式下载结果文件（PDF/视频）。
- 长任务在**并发闸门之外的 daemon worker 线程**执行，故不再占用推理闸门槽位。
- 轮询端点是**只读**，**豁免并发闸门**（与 199 fail-closed 一致：推理类端口绝不豁免）。
- 守卫 `tests/test_api_async_jobs255.py` **11 例全绿**（真起 socket、注入式契约、用确定性轻量 runner 避开真实推理与删除护栏）。
- 收口阶段 251 的「未决」项：PDF / 视频 / 模型下载长任务 HTTP 入口已闭合。

---

## 二、归因（为什么之前不做 / 为什么现在做）

- **之前不能做同步**：PDF/视频擦除是分钟级、模型下载是数十 MB~数百 MB 的网络长任务。
  若做成同步 `POST /api/remove` 那种「请求线程内跑完再返回」，会**占住并发闸门槽位数分钟~数十分钟**，
  把其它推理请求全堵在队列里（闸门语义是「排队等待」，但队列头部被一个长任务占着，后面全饿死）。
- **现在做异步**：把「重活」剥离到 worker 线程，HTTP 请求线程只做「建 job + 入队」两件轻活立即返回；
  真正执行在闸门之外 ⇒ 闸门只管「接收请求」，不管「后台算多久」。
- **用户授权**：「你决定为项目服务」→ 选择收口 251 遗留的异步作业缺口（不依赖用户侧资产、不依赖 Docker）。

---

## 三、设计

### 3.1 并发模型（关键）

```
客户端 ──POST /api/jobs──▶ [HTTP 请求线程] 建 job + 入队 ──▶ 立即返回 job_id（毫秒）
                                                  │
                                                  ▼
                                          [Queue] ──▶ [worker 线程 ×N] 跑重活（闸门之外）
                                                  │                         │
                                                  ▼                         ▼
                                          状态写回内存 Job           结果文件落 job_dir
                                                  │
客户端 ──GET /api/jobs/{id}──▶ [HTTP 请求线程，只读豁免] 读内存 Job ──▶ 返回状态/进度/结果
客户端 ──GET /api/jobs/{id}/file──▶ 流式回写结果文件
```

- **提交（submit）**：在 HTTP 请求线程内只做轻活 → 毫秒级，可放心排在并发闸门里（POST 不豁免，符合「动作不豁免」）。
- **执行（worker）**：完全在 `_BoundedThreadingHTTPServer` 闸门**之外** → 长任务不占推理闸门槽位。
- **轮询（poll）**：`GET /api/jobs/*` 是只读轻量查询 → **豁免闸门**（与 `/api/health`、`/api/models*` 同列），
  服务繁忙时也能查进度（否则最需要自省时被闸门挡住，见 251 论据）。

### 3.2 任务类型

- 内置三类：`pdf` / `video` / `model_download`，复用 `api.remove_watermark_pdf` /
  `api.remove_watermark_video` / `api.download_model`，与 GUI/CLI/库四入口行为完全一致。
- 可扩展：`JobManager.register(kind, fn)` 注册自定义 runner（测试与未来任务复用同一扩展点）。
- 进度回调签名统一为 `cb(progress: 0~1, message: str)`；模型下载的 `progress_cb(downloaded, total, speed)` 在其内归一化。

### 3.3 结果序列化

- `PdfResult` / `VideoResult` 是 dataclass（含 `field(default_factory)` 与私有字段如 `_rec_conf_sum`）。
- `_result_to_dict` 用 `asdict` 转普通 dict 并**剔除以 `_` 开头的私有字段**，保证 JSON 可序列化。

---

## 四、改动清单

| 文件 | 改动 |
|---|---|
| `job_manager.py`（新增，~313 行） | `Job` dataclass + `JobManager`（submit/register/get/_worker/_run_pdf/_run_video/_run_model_download）+ 模块单例 `JOB_MANAGER` |
| `server.py` | ① `import job_manager as JM`；② `_is_exempt_from_gate` 增加 `/api/jobs`；③ `do_GET` 增 `/api/jobs/` 轮询/下载分支；④ `do_POST` 增 `/api/jobs` 提交分支；⑤ 新增 `_handle_get_job` / `_send_file` 方法；⑥ `start_server` 打印新端点；⑦ `_USAGE_HTML` 增「⑧ 异步长任务」节并更新「豁免列表」与「尚不提供」说明 |
| `server.py`（`__main__` 自检） | **附带修复阶段 233 遗留漂移**：用例 2/3 补 `allow_auto_erase=true`（见第六节） |
| `tests/test_api_async_jobs255.py`（新增） | 11 例守卫，真起 socket + 确定性轻量 runner `test_ping` |

---

## 五、验证

| 验证项 | 方式 | 结果 |
|---|---|---|
| 接线冒烟（8 项） | `.scratch/smoke255.py` 真起 socket，自定义 `test_ping` runner | 8/8 全绿（提交/轮询/文件/未知类型/缺 kind/不存在/无文件 404/豁免判定） |
| 回归守卫 | `pytest tests/test_api_async_jobs255.py` | **11 passed** |
| 回归 251 | `pytest tests/test_api_readonly_endpoints251.py` | **15 passed**（闸门豁免逻辑未被破坏） |
| 全模块自检 | `python server.py`（`__main__`） | 用例 1~5 全过（含真实 GPU 推理：device=cuda，质量分 97 good） |

---

## 六、附带发现 · 阶段 233 自检漂移（诚实归因）

运行 `server.py` 自检时，用例 2（`/api/remove`）**红**，但**与 255 无关**：

- 真因：阶段 233 全自动擦除确认门（`API_REQUIRE_AUTO_ERASE_CONFIRM=True`）对「未显式指定 preset 的自动擦除」返回
  `needs_confirmation`；而 `server.py` 的 `__main__` 自检写于 233 **之前**，从未适配该契约 → 自检静默变红。
- 处置：**不削弱断言**，而是让自检显式表态 `allow_auto_erase=true`（用例 2/3）以走通真实擦除链路，符合当前 API 契约。
- 教训（已记入 LESSONS #273）：新增契约门（如 233）后必须同步更新既有自检脚本，否则自检会静默失真。

---

## 七、自反证伪（self-falsification）

- **内存态、进程退出即丢**：`JobManager` 不落盘，结果文件留在 `tempfile.gettempdir()/wme_async_jobs`。
  进程重启后历史作业不可查（符合「本地服务」定位，但与「任务持久化」无关——本阶段不做持久化）。
- **非分布式**：单进程内的 `queue.Queue` + worker 线程，不跨进程、不跨实例。若未来多副本部署，需外接 broker（非本阶段范围）。
- **未覆盖真实长任务端到端**：守卫用确定性 `test_ping` runner 验证 HTTP 全链路，**不跑真实 PDF/视频/下载**
  （避免触发真实推理/网络与删除护栏）。真实 runner 的代码路径（`_run_pdf/_run_video/_run_model_download`）仅经静态与类型审查，
  未在本守卫内执行——其正确性由 `api.py` 既有守卫保证（四入口共用同一核心）。
- **`max_jobs=512` 内存上限**：超上限剔除最早的已终态作业（只删内存记录，保留输出文件），长驻服务需关注内存增长。
- **未做鉴权**：异步作业端点与既有 REST 一致，仍依赖 `API_HOST=127.0.0.1` 默认本机 + 用户侧防火墙/鉴权（项目既有约束，非本阶段新增风险）。

---

## 八、对「图片/PDF/视频全源」承诺的影响

阶段 251 补齐了只读查询；本阶段补齐了 PDF/视频/模型下载的**长任务 HTTP 入口** ⇒ 
「全本地离线 + 图片/PDF/视频全源 + 免手涂 8 预设」的产品化承诺在 API 侧已无遗留缺口。
仅剩 `POST /api/models/active`（切换模型，写操作需确认+鉴权）因属另一项变更、需先设计确认流程，仍不开放。
