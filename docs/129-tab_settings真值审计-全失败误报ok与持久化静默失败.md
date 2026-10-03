# Stage 129 — ui/tab_settings.py 真值审计：全失败误报 ok + 持久化静默失败

> 审计方法：探针量化 → 分类（缺陷 vs by-design） → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 文档 → 记忆同步。
> 审计对象：`ui/tab_settings.py`（设置与导出标签页，阶段 6 核心界面）。
> 荒漠度：`audit_selector.py --unaudited` 榜首 **7.4**（code 464 / refs 63 / files 5）。

## 0. 接线契约核对（技能 5h）
先按「控件数 == 形参数 == 输出数」逐条核对全部 10 个 `.click` 绑定，**全部对齐**，无错位：
`on_export_history`(2/2/2)、`on_export_compares`(1/1/2)、`on_clear_cache`(0/0/2)、`on_prune_cache`(0/0/2)、
`build_snapshot_md`(0/0/1)、`apply_superres_config`(4/4/(3N+2))、`apply_cache_config`(4/4/2)、
`apply_processing_prefs`(5/5/2)、`scan_orphans_md`(0/0/1)、`purge_orphans`(3/3/3)。

## 1. 探针量化

探针 `s129_probe.py`（写 `%TEMP%`，缓存替身 + 日志捕获，项目零残留）实测：

| 项 | 修复前 | 修复后 |
|---|---|---|
| `purge_orphans`（want=3 / removed=0）info | **「✅ 已清理 0 个孤儿文件，释放 0 B。」** | 「⚠️ 已清理 0 个…，另有 3 个因被占用或权限不足而跳过。」 |
| `save_user_overrides` 返回 False 时 tab_settings 日志条数 | **0（完全无痕）** | 1（WARNING） |

## 2. 两处真缺陷

### D1（真缺陷）：全删除失败被误报为「清理成功」
- **症状**：`purge_orphans` 判 partial 的条件是 `if removed and removed < want`——`removed and` 把「want>0 但一个都没删掉」（全部被占用/权限拒绝）**短路进 ok 分支**，报「✅ 已清理 0 个」，掩盖「0 成功 / N 失败」。**既有测试 `test_orphan_cleanup.py::test_purge_orphans_partial_message` 的 docstring 自己写明「removed=0 走 ok 分支」并绕开**——即缺陷已被知晓、以规避方式存活。
- **定性**：真缺陷（失败路径的精确原因被泛化结论覆盖，属经验 9「容错成为藏身处」同族）。
- **最小修复**：改为纯数量比较 `if removed < want:`（0 < want 即 partial，含失败数）。

### D2（真缺陷）：三处「保存为默认」的持久化失败完全无痕
- **症状**：`apply_superres_config` / `apply_cache_config` / `apply_processing_prefs` 均以
  `try: config.save_user_overrides({...}) except Exception: log.warning(...)` 包住持久化。但
  `config.save_user_overrides` 的内部契约是「**失败返回 False、从不抛异常**」 ⇒ ① `except` **永不触发**（死兜底）；② 返回的 `False` 被丢弃 ⇒ 用户勾选「保存为默认」而写盘失败时，**既不报错也不告警**。
- **定性**：真缺陷（声明 ≠ 实现：兜底逻辑看似处理失败、实则无法处理）。
- **最小修复**：消费返回值，`ok = config.save_user_overrides(...); if not ok: log.warning("…未生效…")`，三处一致。

### 已排除（by-design，不修）
- 白名单 `_USER_OVERRIDABLE_KEYS` 与校验器 `_USER_OVERRIDE_VALIDATORS` 覆盖 tab_settings 用到的全部 11 键，无静默丢弃。
- 共享 `info` / `dl` 组件被多个回调复用：设计如此（单条状态行 + 单一下载位）。

## 3. 守卫与注入式反例

- 回归守卫：`tests/test_tab_settings_audit129.py`（6 用例：D1 全失败 partial / 全成功 ok / want=0 ok；D2 失败告警 / 成功无告警 / 未勾选不调用）。
- 既有测试订正：`test_orphan_cleanup.py::test_purge_orphans_partial_message` 的 docstring 原写「removed=0 走 ok 分支」，已更新为反映修复后的口径。
- 注入式反例：`s129_inject.py`（写 `%TEMP%`）——FIXED 守卫通过 → BUGGY（恢复 `removed and` 短路 + 去掉 ok 消费）→ 守卫捕获回归 → 还原 FIXED，`sha256=a753fc7978a8259b6c55c93588efda7c03b1302c60ef0d3e3c041004e5eb0a79` 字节一致。

## 4. 影响面与回归
- 两处修复均为「汇报口径订正 + 失败可观测」，不改正常路径行为。
- 本阶段全测：`test_tab_settings_audit129.py`(6) + `test_orphan_cleanup` / `test_cache_policy` / `test_processing_prefs` / `test_settings_superres` / `test_cache_disk` / `test_i18n_coverage` = **102 例 EXIT=0**。

## 5. 边界登记
- D2 修复只到 **日志层**（WARNING）；「保存为默认」失败时 **UI 文案未追加提示**（需新增 i18n 键，属界面语义变更，收益 < 回归风险，故登记不改）。运维可经日志发现。
