# 387 · NO_PROXY 的方括号 IPv6 写法会让 httpx 在导入期崩溃（2026-11-06）

## 怎么发现的

跑阶段 386 的全量入口时，`pytest` 只跑了 15 秒就报「44 errors」——**收集阶段**就炸了：

```
ERROR tests/test_app_branches_audit.py - httpx.InvalidURL: Invalid port: ':1]'
ERROR tests/test_cache_disk.py        - httpx.InvalidURL: Invalid port: ':1]'
...（共 44 个模块）
```

## 根因

自动化运行器注入的环境变量里有：

```
NO_PROXY=localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1
```

`httpx` 在**导入时**就解析 `NO_PROXY`（gradio 的导入链里就有 httpx）。
带方括号的 IPv6 写法 `[::1]` 被它当成 `host:port` 解析 ⇒ 抛
`InvalidURL: Invalid port: ':1]'`。

⇒ 这**不是**测试环境的噪声，而是**用户也会踩到的启动风险**：
只要用户的 `NO_PROXY` 里有 `[::1]` 这类写法，**Gradio 在导入时就炸，界面根本起不来**
（和阶段 384 的"装得上起不来"是同一类后果，但原因完全不同）。

## 修法

- 新增 `utils/net_env.py`：`sanitize_proxy_env()` 把 `NO_PROXY` / `no_proxy` 里
  `[...]` 形式的条目改成 httpx 认的裸写法（`::1`），其余原样保留；**导入即执行**。
- `ui/app.py` 在 **`import gradio` 之前**导入它；`conftest.py` 同理
  （否则 44 个用例在收集阶段就红）。
- 守卫 `tests/test_net_env386.py`（4 条）：清洗行为、幂等性，
  以及用 **AST 核对导入顺序**（清洗必须早于 gradio）。

## 踩到的第二个坑：Windows 上 `os.environ` 键名**大小写不敏感**

第一版实现同时处理 `NO_PROXY` 与 `no_proxy` 两个拼写，在 Windows 上它们**是同一个键**，
于是第二遍把第一遍的结果覆盖掉了；测试里 `monkeypatch.delenv("no_proxy")` 更是直接把
`NO_PROXY` 一起删了（`KeyError`）。⇒ 现在按"实际存在的那个键"处理，且只动一个拼写。

## 验证

- 全量入口 `rc=0`（**3470 passed / 39 skipped / 6 deselected**，586.3s）——44 个收集期错误消失。
- 4 条新守卫覆盖清洗逻辑与导入顺序。

## 边界

- 只处理 `[...]` 方括号形式；其它 exotic 写法（如带端口的 IPv6）未覆盖。
- 清洗发生在**导入期**，只影响本进程；不修改用户的持久化环境配置。
