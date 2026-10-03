# Stage 132 — services/cache_service.py 真值审计：delete 漏删缩略图 + 破坏性自检

> 审计方法：探针量化 → 分类（缺陷 vs by-design） → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 文档 → 记忆同步。
> 审计对象：`services/cache_service.py`（处理记录缓存服务，阶段 5 核心服务，638 代码行）。
> 荒漠度：`audit_selector.py --unaudited` 榜首 **6.0**（code 638 / refs 106 / files 9）。

## 1. 探针量化

| 项 | 修复前 | 修复后 |
|---|---|---|
| `cache.delete(id)` 后缩略图是否存在 | **True（未删除）** | False（已删除） |
| 兄弟方法 `delete_by_filter()` / `clear()` 后缩略图 | False（正确） | False |
| 自检子进程运行后，真实历史索引 | **被 `clear()` 清空** | 原样保留 |

## 2. 两处真缺陷

### D1（真缺陷，行为级）：`delete()` 的缩略图清理是死代码
- **症状**：`delete()` 在锁内**先把记录从 `_records` 移除**，之后才 `rec = self.get(record_id)` 取它的 `thumb_path` 删缩略图——此时 `get` 在已移除的列表里查找**必然返回 None** ⇒ 缩略图清理**永远不执行**。而 docstring 明确承诺「删除一条记录，并清理其缩略图文件」。该路径由 `ui/tab_history.py` 的删除按钮调用，**用户可达**：历史页每删一条记录就漏一个缩略图，日积月累正是 `scan_orphans` 要回收的孤儿。
- **定性**：真缺陷（声明 ≠ 实现 + 顺序错误的死代码）。**同文件兄弟方法 `delete_by_filter` / `clear` / `prune` 都是「先快照、后删除」，全部正确**——唯有 `delete` 错序，互为对照。
- **最小修复**：在移除记录**之前**用 `next(...)` 快照 `thumb_path`，锁外据此删除。

### D2（真缺陷，隐患级）：`__main__` 自检破坏真实用户数据
- **症状**：自检 `cache = CacheService(enabled=True)` 用的是**真实** `INDEX_PATH`（`config.CACHE_DIR/history/...`），紧接着 `cache.clear()` ⇒ 会**清空用户真实的历史索引并删除其缩略图**。这也使该自检**无法被登记进子进程守卫**（测试一跑就毁用户数据）。
- **定性**：真缺陷（破坏性自检；同类模块的自检均用 `tempfile` 隔离）。
- **最小修复**：自检开头把 `file_service` 与本模块的 `UPLOAD_DIR/THUMB_DIR/HISTORY_DIR` 及 `INDEX_PATH` 统一重定向到 `mkdtemp()` 临时目录，结束 `rmtree`；随后把它安全登记进 `test_legacy_selfcheck.py` 的 `MODULES`。

### 已排除（by-design，不修）
- `add_failure` 预留的死方法（无调用点）：其记录 `result_path=None`，会在 `_load` 时被「结果文件缺失」过滤掉——因该方法从未被调用，无用户可见后果，登记为边界。
- 删除只清缩略图、保留结果成品图与上传副本：文档化的既有约定（成品图是用户资产）。

## 3. 守卫与注入式反例

- 回归守卫：`tests/test_cache_service_audit132.py`（5 用例：delete 删缩略图 / 缺失记录返回 False / 兄弟方法不回归 / 无 thumb 记录不报错 / 自检源级隔离断言）。
- 子进程守卫：`tests/test_legacy_selfcheck.py` 的 `MODULES` 新增 `("services/cache_service.py", [])`（隔离后可安全登记，Stage 122 铁律）。
- 注入式反例：`s132_inject.py`（写 `%TEMP%`）——FIXED 守卫通过 → BUGGY（恢复「先移除后 get」+ 去掉自检隔离）→ 守卫捕获回归 → 还原 FIXED，`sha256=8803d5cf1fc65d80c282ab4587ac35efeffb34b6a2ca1479bc3c648f3b6e1386` 字节一致。

## 4. 影响面与回归
- D1 只补上既有 docstring 已承诺的缩略图清理，不改变索引删除语义；D2 只改自检运行环境。
- 本阶段全测：`test_cache_service_audit132.py`(5) + `test_cache_disk.py` / `test_cache_policy.py` / `test_orphan_cleanup.py` + 子进程自检 = **50 例 EXIT=0**。
