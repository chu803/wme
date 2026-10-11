# 阶段 289 续审报告 · utils/logger.py 首轮遗漏的兜底分支/公共 API 不变量

| 项 | 内容 |
|---|---|
| 模块 | `utils/logger.py`（307 行，公共日志系统：双通道 + 共享 handler + 轮转 + 着色） |
| 首轮 | 阶段 90（`tests/test_utils_audit90.py`，24 例，覆盖 L1 共享 handler/轮转健康、L2 级别别名、L3 整行着色、L4 陈旧 handler 关闭 + 夹具 teardown 不泄漏、min(level)/propagate 委托） |
| 续审手法 | 5j 链：读源码 → 读首轮测试 → 写运行时探针 → 分类（真缺陷 vs by design）→ 补守卫 → 全回归 → 提交 → 刷新 bundle → 记记忆 |
| 真缺陷 | **0** |
| 新增守卫 | `tests/test_logger_branches_audit.py`，7 例，全绿 |
| 全回归 | `tests/test_logger_branches_audit.py` + `tests/test_utils_audit90.py` = **24 passed + 1 skipped（colorama 存在时跳过无色断言），EXIT=0** |
| 荒漠比值 | 2.1（logger，re-audit 链剩余候选中次高；最高 theme 已在 288 审完） |

---

## 1. 首轮覆盖盘点（已知 covered）

- L1 共享 handler / 文件唯一属主（8 个 logger 只有 3 个真实 handler 对象；轮转备份序列连续无空洞）。
- L1 回归：多 logger 轮转健康（1/4/8 参数化）；注入式反例复现「旧多 handler 形态轮转失败」。
- L2 `_resolve_level` 标准名/别名（WARN/FATAL/NOTSET）/大小写空白不敏感/None 安全兜底/对账 config 合法级别。
- L3 `_ColorFormatter` 整行着色（colorama 时行首上色行尾复位）；文件 Formatter 无 ANSI。
- L4 陈旧非共享 handler 关闭（FileHandler 关 fd / StreamHandler 标记关闭）；共享 handler 不被关闭。
- 阶段 246 夹具 teardown 守卫（`test_zz_*` 两例，先摘除后关闭、可重复幂等）。

## 2. 探针量化（`.scratch/probe_logger_reaudit.py`，全绿）

| 探针 | 验证点 | 结果 |
|---|---|---|
| F1 | `_ColorFormatter.format(use_color=False)` → 无 ANSI、返回纯文本（即便环境有 colorama） | ✓ |
| F2 | `_HAS_COLOR=False` 时 `format(use_color=True)` → 无 ANSI | ✓ |
| F3 | `print_banner("启动横幅")` → 恰好三行、首尾等号分隔行等长、中间为文本、不创建/写日志文件 | ✓ |
| F4 | `_ensure_global_config()` 幂等：首调置 `_initialized`+建目录，二次调用早返回不重建 | ✓ |
| F5 | `_ensure_shared_handlers()` 二次调用返回同一字典、handler 对象身份一致（不重建→防 L1 回归）；error handler 恒 ERROR 级 | ✓ |
| F6 | `get_logger()` 默认名 `"WME"`，返回同名 logger 且入缓存 | ✓ |
| F7 | `_resolve_level(int / 字节串)` 经 `str()` 转换安全兜底 INFO；自定义 fallback 生效 | ✓ |

## 3. 分类：真缺陷 vs by design

- 全部探针行为正确，**0 真缺陷**。首轮遗漏的是「未钉分支/未覆盖公共 API」，非代码缺陷。
- 探针过程踩到一处**探针自身错误**（非代码缺陷）：首版把 `config.LOG_DIR` 设为 `tempfile.mkdtemp()` 的 **str**，而 `logger.py` 内部用 `config.LOG_DIR / config.LOG_FILE_NAME` 需要 `pathlib.Path`，触发 `TypeError: unsupported operand type(s) for /: 'str' and 'str'`。修正为 `Path(tmp)` 后全绿——与 287 探针 `select_device` 顺序坑、`283` 桩错误同族：先拿过程量、别信一次可疑结果（LESSON 252/269）。

## 4. 新增守卫（`tests/test_logger_branches_audit.py`，7 例）

1. `test_color_formatter_no_ansi_when_use_color_false` — F1
2. `test_color_formatter_no_ansi_when_color_disabled` — F2（monkeypatch `_HAS_COLOR=False`）
3. `test_print_banner_wraps_text_in_three_lines` — F3（capsys 捕获 + 不建 app.log）
4. `test_ensure_global_config_is_idempotent` — F4
5. `test_ensure_shared_handlers_reuses_same_objects` — F5（复用同一批 + error 恒 ERROR）
6. `test_get_logger_default_name_is_wme` — F6
7. `test_resolve_level_non_string_inputs` — F7

## 5. 结论

`utils/logger.py` 首轮已修复 L1 共享 handler/轮转健康等真问题；本轮补的是首轮未机控的**安全兜底分支与公共 API 契约**（最关键是 `print_banner` 此前 0% 覆盖、`_ensure_shared_handlers` 复用身份守卫防 L1 回归）。共 7 例守卫全绿，全回归 24 passed + 1 skipped（colorama 存在跳过无色断言）、EXIT=0。**0 真缺陷**。re-audit 链剩余唯一候选：`ui/app`（阶段 91）。
