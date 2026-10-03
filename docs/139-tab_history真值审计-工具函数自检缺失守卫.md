# 139 · ui/tab_history 真值审计——工具函数自检缺失守卫

> 日期：2026-09-26 ｜ 对象：`ui/tab_history.py`（荒漠度 4.8 = 514 代码行 ÷ 108 引用测试 / 9 文件）
> 方法：探针量化 → 分类 → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 登记

## 一、探针量化

| # | 候选 | 探针结果 | 判定 |
|---|---|---|---|
| D1 | 无 `__main__` 自检 | 模块纯 UI 但**含 6 个非 gradio 工具函数**（`_fmt_dur` 边界 / `_format_time` / `_fmt_bytes` / `_record_detail` 字段合成 / `build_disk_usage_md`），从未被任何自动化验证 | **defect（守卫缺失，与 138 同模式）** |
| — | 模块级 `_CACHE = get_cache()` 在 import 触发 | 已审@132/144 范式：仅读索引不破坏数据 | by design |
| — | `_record_detail` 视频/PDF 分支、画廊 compare 回退、五个删除/清理守卫 | 行为正确且已有阶段 43/46/47/38 强守卫 | by design |
| 观察 | `_LAST_SELECTED_ID` / `_HISTORY_ID_MAP` 模块级可变状态跨调用累积 | 与 Gradio 事件回调设计一致；测试通过 monkeypatch 隔离 | by design |

## 二、最小修复（ui/tab_history.py）

新增 `_run_selfcheck()` + `if __name__` 块，五组断言：
1. **`_fmt_dur` 边界**（0 / 59.9 / 60 / 125 / 3661.5）—— 与 `tab_media._human_seconds` 同口径；
2. **`_format_time` 格式**（`YYYY-MM-DD HH:MM:SS`，19 字符 + 4 个固定分隔符）；
3. **`_fmt_bytes`**（0 / 1024）；
4. **`_record_detail`** 合成 `HistoryRecord`（success / image 类型）→ 详情含 ✅、原图名、引擎、模式；
5. **`build_disk_usage_md`** 桩缓存替换 `module._CACHE` 跑完恢复（try/finally 守卫）。

`__main__` 用 `SystemExit("自检失败：…")` 非零退出，删除任何 catch-all 降级。

## 三、验证

| 门禁 | 结果 |
|---|---|
| 守卫测试 `tests/test_tab_history_audit139.py`（7 用例：_fmt_dur 边界 / _format_time 格式 / _fmt_bytes / 合成记录 / 桩缓存 / 子进程自检绿 / 模块 _CACHE 副作用保护） | ✅ 7/7 绿 |
| 子进程守卫登记 `test_legacy_selfcheck.py` MODULES += `("ui/tab_history.py", [])`（纯内存 + 桩缓存跑完恢复） | ✅ 可登记 |
| 注入式反例（`s139_inject.py`：`_fmt_dur` 中 `int(sec // 60)` → `0`，复现「分钟计算退化为常量」） | BUGGY 下守卫 **rc=1 红**；还原 sha256=`0d1c69b27b4cc2cf` **逐字节一致**；回绿 rc=0 |
| `audit_selector.py --check` | ✅ EXIT=0，登记 38 条 |

## 四、登记

- `AUDITED_SINCE` += `"ui/tab_history": 139`。
- 无新增登记未修项；与 138/152 同属「工具函数无机器守卫」模式，LESSONS 已覆盖。
- 下一站（`--unaudited`）：`services/quality_service` 3.6 → `utils/brand` 3.3 → `core/inpainter` 3.3。