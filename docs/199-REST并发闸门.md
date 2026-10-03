# 阶段 199 · REST 并发闸门（对应优化清单 D1「并发策略」）

> 用户 2026-10-03 说「继续」后自主推进。D1（185 清单里的「并发策略」项）与 198 的
> 「D1 产品化 GPU/API」是**同名不同物**——本阶段做的是前者，恰好是 185 清单里
> 最后一个未动的纯代码项。

## 归因：缺口是「实测出来的」，不是读出来的

先按 LESSONS 91/209 的模式做**配置项存亡审计**（`SERVER_MAX_THREADS` 等 7 个并发相关
配置项是否真有执行点），结果全部有真实引用——**没有存亡**。真正的缺口在别处：

| 配置项 | 承诺 | 实际执行点 |
|---|---|---|
| `SERVER_MAX_THREADS`（注释：「**队列最大并发请求数**」） | 限流 | `ui/app.py:494`（Gradio `max_threads`）+ `main.py:181`（打印）——**REST 侧完全不生效** |
| `server.py` 的 `ThreadingHTTPServer` | — | `server.py:414` 起服务，**每连接一线程、无上限** |

**为什么这是真缺口**：本服务 5 个端点（`/api/remove`·`detect`·`inpaint`·`batch`）
**每次调用都要跑一次完整推理**（检测 + LaMa 修复）。若无上限，N 个并发请求 = N 线程
同时抢 CPU/显存 ⇒ 单请求时延成倍恶化 ⇒ **直接冲垮阶段 186 立的 P95 门禁**。
这正是 185 清单 D1 的原文判断：「多用户/大图并发时 CPU 争用，可能拖垮 P95 ⇒ 与 A5 互为前提」。

## 交付

### `config.py`：新增 `API_MAX_CONCURRENCY`

- 走既有 `_env_int` ⇒ 自动获得 `WME_` 前缀与范围校验（LESSONS 209）；
- 默认 **2**（保守：单张 LaMa 推理本身就吃满 CPU/GPU，留余量给界面）；
- `0` = 关闭闸门（本地调试逃生门）；
- 取值范围 `0~64`。

### `server.py`：`_BoundedThreadingHTTPServer`

- 继承 `ThreadingHTTPServer`，用 `BoundedSemaphore` 在**工作线程内**取闸门——
  **accept 循环永不被阻塞**（若在 accept 侧阻塞，客户端会「连不上」而非「变慢」）；
- 语义刻意选**排队**而非拒绝 ⇒ 客户端只会变慢、不会拿到错误码，P95 可预测；
- **`/api/health` 与 `/` 豁免**：健康探针必须始终可答，否则长推理占满槽位时
  编排器/浏览器会把「繁忙」误判为「死服」（误杀 / 白屏）；
  豁免判定用 `MSG_PEEK` **只窥探不消费**首行（此刻 handler 的 `rfile` 尚未创建），
  任何异常都按「不豁免」处理（fail-closed：不给未鉴权重活开免检通道）；
- `concurrency_stats()` 暴露 `{max_concurrency, active, peak, gated}`，
  `/api/health` 的 `concurrency` 字段如实回报，服务方可自省「繁忙」还是「死服」。

## 验证

pytest 在本沙箱受删除护栏拦截（沿用 198 的诚实边界），改用**直接调用测试函数**，
全部为真实 HTTP 真并发：

| 项 | 结果 |
|---|---|
| `tests/test_server_concurrency.py`（7 例） | 7/7 通过 |
| ├ 峰值不超上限（`max=2` + 6 并发 ⇒ `peak ≤ 2`） | ✅ |
| ├ 超额**排队**而非拒绝（4 并发 / 上限 1 ⇒ 全 200） | ✅ |
| ├ health 豁免（闸门占满时 health < 0.3s，实测 **0.016s**） | ✅ |
| ├ health 自省字段（`concurrency` 四字段） | ✅ |
| ├ `max=0` 关闭闸门且 `gated=False` | ✅ |
| ├ **注入式反例**：限流峰值必须 < 不限流峰值 | ✅ |
| └ config 契约（`_env_int` + `WME_` 前缀） | ✅ |
| **注入式反例有效性**（破坏闸门 → 守卫必须变红） | ✅ 峰值 6 > 上限 2、限流侧 4 > 1 两条立刻变红 |
| `tests/test_server_endpoints.py`（既有 5 端点） | 5/5 无回归 |
| `server.py` 模块自检 | 5/5 通过 |
| `test_legacy_selfcheck`（undef_scan + 自检登记） | 10/10 通过 |
| `tests/test_config*.py` | 26/31，5 条失败为**既有失败**（`DENOISE_STRENGTH_DEFAULT` / `DETECT_GRID_*_COVERAGE` / feather odd，与本阶段无关） |

## 守卫抓出的真实缺陷（设计缺陷，由测试当场抓出）

**指标语义混淆**：首版把豁免请求也计入 `_active`/`_peak`，实测出现
`peak=2` 而 `max_concurrency=1` —— **指标超过了它自己声称守护的上限**，
「峰值 ≤ 上限」这条不变式在指标上自相矛盾。
这不是测试写错，是实现缺陷：豁免请求不占闸门槽位，就不该计入闸门占用指标。
已修（豁免请求不进 active/peak 统计），并在代码注释里写明这条指标语义。

**测试自身缺陷**：`get_capabilities()` **首次调用实测 1.823s**（加载修复插件 + 探测设备），
不预热会让「health 是否被阻塞」这类时序断言随机变红。加 `autouse` 预热 fixture
（LESSONS 200：统计量须高于噪声地板）。

## 剩余 / 边界

- 默认值 **2 是判断值，不是实测最优**：沙箱无 GPU、无法实测最优并发；用户真机
  （RTX 5060）上应实测后调 `WME_API_MAX_CONCURRENCY`。
- 闸门只限**并发数**，不保证**P95**：真机仍须跑 `perf_probe --stage full` 验收。

## 事故留档：git 仓库重建

同阶段内 `git stash push` 在沙箱中被 **SIGTERM 中断**，导致 `.git/refs` 与
`.git/objects` 受损（`git fsck` 报大量 `missing blob`，历史不可恢复）。

处置（按 LESSONS 202「归档优于删除」）：
1. 用 `git fsck` 确认历史不可恢复，工作区源码**全程完好、零代码损失**；
2. 把残损 `.git` **rename** 为 `.git_broken_20261003_154623/` 留存复核（未删除）；
3. 重建干净仓库（`git init -b main` + 仓库级身份）并提交基线。

顺带修掉一个阻塞首次提交的问题：根目录有个 0 字节的 **`NUL`** 文件（Windows 保留
设备名，某次命令误重定向产生），git 无法索引它（`short read while indexing NUL`）。
已由 `.git/info/exclude` 排除——**放 info/exclude 而非 .gitignore**，因为后者需先被
提交才生效，会与首次提交互相阻塞（LESSONS 202 的「移动后必须补忽略规则」同族，
但这里要注意**规则自身的生效时机**）。

## 教训 → LESSONS 212

受限环境里 **`git stash` 这类会重写 `.git` 的操作，可能被 SIGTERM 打断并留下半损坏的仓库**
（`refs/` 与 `objects/` 成片丢失，`fsck` 报大量 missing blob）。判据：① 在受限沙箱里
**优先用「临时改文件 + 跑测试 + 还原」代替 `git stash`**——前者只动工作区文件，
即使被中断也只丢一次改动，不会损坏版本库；② 若 `.git` 受损，先 `git fsck` 判定
「历史是否可恢复」，**只要工作区文件完好就直接重建仓库**（`rename` 归档残损 `.git`
而不是删除，符合 202）；③ 首次提交可能被**与本次改动无关的脏文件**阻塞
（本例是 Windows 保留设备名 `NUL`），此时用 `.git/info/exclude` 而非 `.gitignore`——
**忽略规则本身也要先提交才生效**，放进 info/exclude 才能打破这个死锁。
