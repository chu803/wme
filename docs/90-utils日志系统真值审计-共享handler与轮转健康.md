# 阶段 90 · utils 层日志系统真值审计：共享 handler 与轮转健康

> 对象：`utils/logger.py`（**审计前 237 行 / 修复后 307 行**）——**全仓唯一真正的零覆盖模块**（测试引用 0）。
> 方法：沿阶段 89 修正后的选点指标（模块行数 ÷ 导入该模块的测试文件中 `def test_` 个数），
> 以及「探针量化 → 区分真缺陷 → 修复 → 注入式反例 → 双门禁」的既定流程。

---

## 0. 为什么是它

阶段 89 重扫覆盖度时发现：`utils/logger.py` **237 行、被 85 处调用、测试引用 0**，
是全仓唯一比值无穷大的模块。它位于**所有**模块的横切面——日志系统一旦有缺陷，
影响面是「所有模块的排查能力」，而不是某一个功能。

修正后的分布（`core/` 审计起点见阶段 89）：

| 模块 | 行数 | 导入它的测试文件中的 `def test_` 数 | 判读 |
|---|---|---|---|
| `utils/logger.py` | 237 | **0** | ⚠️ 零覆盖，且是全站横切面 |
| `services/postprocess_service.py` | 655 | 8 | 待审 |
| `services/auto_pipeline.py` | 575 | 10 | 待审 |
| `ui/`（整层） | 6,796 | — | 最大盲区（仍待审） |

---

## 1. 探针量化（`%TEMP%/s90_probe/`，项目内零残留）

日志目录用 `WME_LOG_DIR` 指到临时目录，探针不往项目 `logs/` 写任何东西。

### 1.1 候选清单（L1–L4）

| # | 候选 | 判定 |
|---|---|---|
| **L1** | 每个 logger 各建一套 handler → 多个 RotatingFileHandler 持有同一 `app.log` 并独立轮转 | **真缺陷（P0 级，Windows 特有）** |
| **L2** | `_resolve_level` docstring 称支持「中文/英文级别名」，实际只有 5 个英文键 | **真缺陷（声明≠实现 + 静默降级）** |
| **L3** | `_ColorFormatter` 注释称「颜色只加在级别名前」「复制不混 ANSI」，实际整行着色 | **真缺陷（声明≠实现，文档级）** |
| **L4** | `logger.handlers.clear()` 前不 `close()` 旧 handler | **真缺陷（资源，低可达性）** |

### 1.2 L1 的决定性证据（对照实验）

固定 `maxBytes=4096`、每个 logger 写 600 行，只改变 logger 数量：

| logger 数 | 指向 app.log 的 handler 数 | 轮转异常 | 备份序列 | app.log 大小 |
|---|---|---|---|---|
| 1 | 1 | **0** | `.1 .2 .3 .4 .5`（连续） | 2688 |
| 2 | 2 | **31 × PermissionError** | `.1 .3 .5`（**有空洞**） | 2442 |
| 4 | 4 | **171 × PermissionError** | `.1 .5`（**严重空洞**） | 672 |

**根因**：`RotatingFileHandler.doRollover()` 先关自己的流、再 `os.rename(app.log → app.log.1)`；
但**其它 logger 的句柄仍开着 app.log**，Windows 上改名直接抛 `PermissionError`
（Python 的 `open()` 不带 `FILE_SHARE_DELETE`）。后果：

1. 轮转**失败** → `app.log` 不再受 `LOG_MAX_SIZE_MB` 约束，**无限增长**；
2. 备份序列出现空洞 → **日志行丢失**（4 logger 时最终仅剩 672 字节）。

**当前状态是「即将触发」**：`logs/app.log` 实测已 6.1 MB，阈值 10 MB，且全站有 85 处
`get_logger` 调用、约 30 个不同 name。

### 1.3 L2 / L3 / L4 证据

```
L2  _LEVEL_MAP 的键 = ['CRITICAL','DEBUG','ERROR','INFO','WARNING']   # 无中文、无别名
    _resolve_level('WARN')  -> 20 (INFO)     # 静默降级，不是 WARNING
    _resolve_level('FATAL') -> 20 (INFO)     # 静默降级，不是 CRITICAL
    _resolve_level('警告')   -> 20 (INFO)
    _resolve_level('')      -> 20 (INFO)
    _resolve_level(None)    -> 旧实现 name.upper() 直接 AttributeError

L3  格式化结果 = '\x1b[31mERROR 正文内容\x1b[0m'
    ANSI 位置 = [0, 15]   → 整行着色；复制必然混入 ANSI（与注释相反）

L4  预置 handler 调用 get_logger 后：已从 handlers 移除，但 stream.closed = False
```

---

## 2. 修复

### L1（核心）：handler 全进程共享

原实现里每个 `get_logger(name)` 都新建「控制台 + app.log + error.log」三个 handler。
改为 **全进程共享一套**：`_shared_handlers = {"console", "file", "error"}`，
由 `_ensure_shared_handlers()` 惰性构建并复用；`get_logger` 只把**同一批对象**挂到各 logger 上。

- 每个文件**只有唯一属主** → 轮转不再互相踩踏；
- handler 对象数从 `O(logger 数 × 3)` 降到常数 **3**；
- 打开的文件句柄同样从 30+ 降到 2。

级别（console / file）取自全局 `config`，对所有 logger 天然一致，因此共享是安全的。
文件日志仍用**普通 Formatter**（无 ANSI）。

> 附带收益：`test_shared_handlers_reused_across_many_loggers` 断言 8 个 logger 共 24 个
> handler **引用**但只有 **3 个对象**——这条断言直接锁住了修复的本质。

### L2：补齐别名 + 安全兜底 + 修正 docstring

```python
_LEVEL_MAP 新增: "WARN"→WARNING, "FATAL"→CRITICAL, "NOTSET"→NOTSET
_resolve_level: name.upper()  →  str(name).strip().upper()
docstring      : 去掉「中文」，说明只支持英文名 + 别名，并注明兜底偏宽松
```

`str(name)` 使 `None` 等非字符串安全兜底而不抛 `AttributeError`；`.strip()` 容忍配置里的空格。

### L3：把注释改成真实行为

颜色**作用于整行**（行首上色、行尾复位），并非「只加在级别名前」；
「复制不混 ANSI」在控制台着色下不可能成立，故不再作为设计承诺。
文件日志用普通 Formatter、完全不含 ANSI——这一点保留并测试钉住。

### L4：移除前先 `close()`

```python
for handler in list(logger.handlers):
    if handler not in shared.values():   # 共享 handler 绝不能关
        try: handler.close()
        except Exception: pass
logger.handlers.clear()
```

> **实测细节**：Python 3.13 的 `StreamHandler.close()` **只**把 `_closed` 置真并从内部
> 注册表移除，**不关闭底层 stream**（因为流常是 `sys.stdout`）；只有 `FileHandler.close()`
> 才真正 close 文件句柄。故测试对两者分别断言（句柄已释放 / 已停用）。

---

## 3. 已验证为非缺陷 / 边界（各写成测试钉住）

| 项 | 结论 |
|---|---|
| `logger.setLevel(min(console_level, file_level))` | **正确**：取更细的一级保证不漏记 |
| `propagate = False` | **正确**：防向上冒泡到未配置的 root 造成重复/丢失 |
| 单例缓存 `_logger_cache` | **正确**：同一 name 只配置一次 |
| colorama 缺失时 `_HAS_COLOR=False` 且 `Fore=Style=None` | **正确**：双保险降级，不报错 |
| 文件日志用普通 Formatter | **正确**：不含 ANSI，可 grep/归档 |
| UI **不**暴露日志级别 | **边界登记**：因此「缓存导致运行时改级别失效」在 UI 路径**不可达**；改级别需重启（`WME_LOG_LEVEL` 环境变量） |
| `print_banner` 直接 `print` 不走日志 | **设计**：供启动最早期、日志尚未就绪时打印横幅 |

---

## 4. 验证

- **注入式反例**：逐个回退 L1 / L2a / L2b / L4 四处修复 → 守卫按预期失败
  （L1→3 例、L2a→1 例、L2b→2 例、L4→1 例），`try/finally` 还原 + **逐字节一致校验**。
- **新增测试**：`tests/test_utils_audit90.py`（22 例，含 1 例环境 skip）。
- **全量**：`1151 → 1173 例`，零失败（见 README §4）。
- **双门禁**：`scripts/quality_gate.py` + `scripts/real_gate.py` 均零回归
  （日志系统是纯横切面，不参与检测/修复数值，故门禁数值逐字节不变）。

> **工程坑（已写进记忆）**：注入脚本会用 `subprocess` 跑 pytest；若复用**同一个**
> `--basetemp`，pytest 收尾会清理该目录 → 撞沙箱「批量删除需确认」。
> 解法：**每次运行用唯一 basetemp**（永不删除既有目录）。
> 另：前台 120s 超时被 SIGTERM 强杀时，Python **不会**执行 `finally`，
> 可能把目标文件留在「已回退」状态 → 注入脚本必须**先落一份外部备份**。

---

## 5. 新增经验（64~67）

- **64**：**横切面模块的缺陷，影响面是「所有模块的排查能力」**——日志/异常/配置这类
  被全站调用的模块，即使不参与核心数值，也应优先审计（零覆盖 = 零守卫）。
- **65**：**「资源被谁持有」要在进程级别清点**：同一文件被 N 个 handler 各自持有时，
  「写」看起来正常，只有**轮转/改名/删除**才暴露——差异在**操作类型**上，不在数据上。
  验证必须**触发轮转**（构造小 `maxBytes`），不能只写一写看有没有输出。
- **66**：**对照实验要只改一个变量**：logger 数量 1 / 2 / 4，其余固定 → 结论干净可复现
  （0 / 31 / 171 次异常）。
- **67**：**库函数的「同名语义」必须实测，不能凭记忆**：Python 3.13 的
  `StreamHandler.close()` **不关流**、`FileHandler.close()` 才关流——凭直觉写断言会写错。

---

## 6. 产物

- 修复：`utils/logger.py`（L1 共享 handler / L2 别名与兜底 / L3 注释 / L4 close）
- 测试：`tests/test_utils_audit90.py`（22 例）
- 探针（`%TEMP%/s90_probe/`）：`probe_logger.py` / `probe2_rotation.py` /
  `probe3_verify.py` / `inject_check90.py`
- 文档：本文件
