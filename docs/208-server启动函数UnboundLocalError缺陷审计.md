# 阶段 208 · server.start_server UnboundLocalError 缺陷审计

> 审计目标：`audit_selector --unaudited` 选出的**唯一**未走正式真值审计的模块 `server`
> （ratio 5.3，355 行 / 67 处测试引用 / 6 文件）。happy-path 已被端点与并发测试覆盖，
> 本次聚焦其**错误/边界分支与入口函数**。

## 一、探针量化

先跑既有 server 套件（全绿），再用自建探针 `scripts` 之外的临时探针逐一验证错误路径
（`POST /api/remove` 空 body / 缺 image / 坏 base64、`/api/inpaint` 缺 mask、
`/api/batch` 空列表 / 非列表、`GET/POST` 未知路径、`API_ENABLED=False` 启动守卫）。

错误路径**全部行为正确**（400/404/`{"status":"failed"}`），但探针在最后一个用例 ——
**直接调用 `server.start_server()`** 时崩溃：

```
UnboundLocalError: cannot access local variable 'max_concurrency'
where it is not associated with a value
  File "server.py", line 413, in start_server
    max_concurrency = (config.API_MAX_CONCURRENCY if max_concurrency is None
```

## 二、缺陷（P0 · 入口函数崩溃）

`start_server(host, port)` 的**签名里没有 `max_concurrency` 参数**，但函数体第 413 行
引用了它：

```python
def start_server(host=None, port=None) -> None:        # ← 缺 max_concurrency
    ...
    max_concurrency = (config.API_MAX_CONCURRENCY if max_concurrency is None
                       else max_concurrency)            # ← 引用未定义名
```

Python 把函数体内被赋值的 `max_concurrency` 视为**局部变量**，于是 `max_concurrency is None`
这处读取在赋值完成前触发 `UnboundLocalError`。

**爆炸半径**：`main.py:443` 正是 `return start_server(host=args.api_host, port=args.api_port)`
—— 即 `main.py --api` 启动 REST 服务的唯一入口。结论：**用户跑 `main.py --api` 必崩**，
REST 服务在生产中根本起不来。

附带后果：`if not config.API_ENABLED: raise RuntimeError(...)` 这道**禁用守卫位于第 416 行，
在崩溃点之后**，因此「API 被禁用时应拒绝启动」这条契约**永远不可达（死代码）**。

## 三、根因

签名漏加参数，而函数体、docstring、调用方都**默认它存在**（docstring 第 408–409 行已写明
`max_concurrency : 并发闸门，缺省取 config.API_MAX_CONCURRENCY（默认 4）`）。属典型的
「文档/意图齐全、签名缺失」类缺陷。

**为何长期未被发现**：`start_server` 是服务端入口函数，**没有任何测试调用过它**——
`tests/test_server_endpoints.py` 与 `tests/test_server_concurrency.py` 都直接构造
`_BoundedThreadingHTTPServer` 起服务，绕过了 `start_server`。这正是 LESSONS 221
「**从未执行的路径=未验证**」在**生产入口层**的实例：指标/GUI 都绿，但那条启动链路从没被真跑过。

## 四、修复（最小改动）

仅把文档里早已写明的参数补回签名，函数体逻辑无需改动：

```python
def start_server(host: Optional[str] = None, port: Optional[int] = None,
                max_concurrency: Optional[int] = None) -> None:
```

修复后：`main.py --api` 可正常启动；不传 `max_concurrency` 时回落 `config.API_MAX_CONCURRENCY`
（现 4），传参时生效；`API_ENABLED=False` 时正确抛 `RuntimeError`。

## 五、回归守卫

新增 `tests/test_server_start.py`（4 例，不依赖真实端口绑定）：

| 用例 | 断言 |
|---|---|
| `test_start_server_default_max_concurrency` | 默认接线 `== config.API_MAX_CONCURRENCY` |
| `test_start_server_override_max_concurrency` | 覆盖参数 `== 3` 透传 |
| `test_start_server_disabled_raises` | `API_ENABLED=False` → `RuntimeError` |
| `test_start_server_disabled_no_crash_before_guard` | 修复前守卫前就崩、守卫不可达；修复后守卫正确生效 |

**server 套件 16 例全绿**（4 新增 + 5 端点 + 7 并发）。

## 六、诚实边界

- `start_server` 内的启动 `print`（含中文与 `⚠️`）在**重定向到文件**时可能触发
  `UnicodeEncodeError`（与 LESSONS 170 同源）。该风险此前不暴露，因 `start_server` 是死函数；
  现在变活，列为后续 hardening 项（可套 `force_utf8_stdio`），本次不扩范围。
- 探针为临时脚本（`.scratch/server_audit_probe.py`），未纳入仓库；回归已固化进
  `tests/test_server_start.py`。

## 七、结论 · 审计闭环

`server` 是 `audit_selector` 登记的**最后一个未审计模块**。本阶段补上其入口函数的
P0 缺陷并固化回归 ⇒ **全部 45 个模块的真值审计正式闭环**。
