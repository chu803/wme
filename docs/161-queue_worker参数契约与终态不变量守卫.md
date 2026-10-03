# 阶段 161 · `queue_worker` 参数契约与终态不变量守卫

## 动机

`scripts/` 覆盖度荒漠已补齐（阶段 152–160），按方法论转回「覆盖度荒漠榜」。全量排名榜首是
`services/queue_worker`（264 代码 / 14 引用测试 / 比值 **18.9**）——它处于 `services/` 承接层
（「承上启下的层最容易成为盲区」），且是**并发 + 取消 + 重试**的高危区。

既有覆盖（`tests/test_services_audit.py` 的 B1/B2）只钉了「取消要 yield / TaskCancelledError 不重试」；
`__init__` 的参数处理、`summary` / `reset` / 满队列 / 空队列 / 终态不变量此前**零测试**。

## 探针（`probe_161.py`）

### (a) 主路径 by design 正确
- **计数对账 + 终态不变量**：正常（12 任务）、中途取消（done=1/cancelled=7）、入队后取消（5 cancelled）
  三种场景下 `summary` 的 `total/finished/done/failed/cancelled` 与 yield 数**一致**，且 `running==pending==0`；
- 满队列正确抛 `QueueFullError`；空队列 `run()` 产出 0；`reset()` 清空且解除取消；
- 重试耗尽：`retry=2` → processor 调用 3 次、`retries=2`、`FAILED`；
- 运行中 `summary()["running"]` 快照准确（`[2,2,1,0]`）。

### (b) 抓到两处
1. **（P2·静默覆盖）**`__init__` 用 `or` 判定 `max_concurrency` / `max_size` / `timeout`：

   | 传入 | 旧实现实际值 |
   |---|---|
   | `max_concurrency=0, max_size=0, timeout=0` | 静默回退 config（1 / 500 / 600） |

   而同一函数里 `retry` 用的是正确的 `is not None` —— **同一风险面两种写法**（显式 0 被静默吞掉、
   调用方意图被覆盖）。
2. **（P1·潜在卡死）**`retry` 无下界校验：`retry < 0` ⇒ `attempts = retry + 1 <= 0` ⇒
   `for attempt in range(attempts)` **一次都不执行** ⇒ `_execute` 直接 `return task`，
   而 `task.status` 仍停在 **RUNNING** ⇒ 破坏 `run()` 的「完整消费后无 RUNNING/PENDING」终态不变量，
   界面进度条会永久卡住。

## 最小修复

- 四个参数**统一 `is not None`**：缺省才取 config，消除 `or` 的静默覆盖。
- **构造期显式拒绝** `max_concurrency < 1` / `max_size < 1` / `retry < 0`，抛 `ValueError`
  （显式失败优于「静默用别的值」或「交给 ThreadPoolExecutor 崩溃 / 卡在 RUNNING」）。

## 守卫

`tests/test_queue_worker_contract.py`（**14 例**）：

- 默认读 config / **显式 0 不再被静默替换（ValueError）** / `retry=0` 生效 / **`retry<0` 被拒**；
- **终态不变量**（正常 / 中途取消 / 入队后取消）：无 PENDING·RUNNING + 计数与 yield 对账；
- 满队列抛 `QueueFullError` / 空队列 0 产出 / `reset` 清空并解除取消；
- 重试耗尽（3 次调用、`retries=2`）；`summary` 键集与 `progress`；`to_dict` 键集（排除 `result`/`_cancelled`）。

## 注入式反例（守卫有牙）

`inject_161.py`（系统 temp + `retention=all`，零删除）：把 `__init__` 换回 `or` 版本 →
pytest **exit=1（红，`test_explicit_zero_is_not_silently_replaced` / `test_negative_retry_rejected`）**；
还原字节级一致 sha=`24478f059213dec7…` → **exit=0（绿）**。守卫有牙：YES。

## 回归

新 14 + `tests/test_services_audit.py` + `tests/test_legacy_selfcheck.py` = **107 例全绿**。

## 诚实边界

- **生成器 + ThreadPoolExecutor**：`run()` 是生成器，`with ThreadPoolExecutor(...)` 的关闭由生成器
  推进/关闭驱动；若消费者提前 `break`，executor 关闭被推迟到生成器被 GC——资源窗口问题，非静默数据损坏。
- **processor 抛 `BaseException`**（非 `Exception`，如 `KeyboardInterrupt`）不会被 `_execute` 捕获，
  会经 `fut.result()` 穿透中断整批——docstring 只承诺 `Exception` 面，登记为边界。
- `Task._cancelled`（逐任务取消标记）**当前无公开设值入口**，只被读取——属「预留扩展」，
  与 `tests/test_exception_contract.py` E6（取消信号双实现）相关，登记为边界，本阶段不动。

## 产物

`docs/161-queue_worker参数契约与终态不变量守卫.md`
