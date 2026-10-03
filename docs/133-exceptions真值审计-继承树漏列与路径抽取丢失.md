# 阶段 133 · `utils/exceptions` 真值审计

> 审计对象：`utils/exceptions.py`（未审榜榜首，荒漠度 5.6，396 代码 / 71 引用 / 6 文件）
> 方法：探针量化 → 分类（defect vs by design）→ 最小修复 → 回归守卫 → 注入式反例（sha256 字节级还原）→ 文档 → 记忆同步
> 结论：**命中 3 处真缺陷**（D1 文档契约不完整、D2 路径抽取丢失、D3 自检无断言）

---

## 1. 探针实测（量化）

探针 `s133_probe.py`（落 `%TEMP%`，不污染项目）：

| 项 | 实测 |
|---|---|
| **D1** | `WatermarkEraserError` 子类共 **30 个**；模块 docstring 继承树漏列 **6 个**：`InvalidMaskError` / `DetectionError` / 整个 `MediaError` 子树（`UnsupportedMediaError` / `CorruptedMediaError` / `MediaTooLargeError`）。修复前：`未出现在 docstring 中的 = [6 个]` |
| **D2** | `wrap_unknown_error(FileNotFoundError("找不到图片文件：foo.png"))` → 修复前 `reason='找不到需要的文件：未知路径'`、`detail['文件路径']='None'`——**丢失具体路径**；且本项目自抛的中文消息型异常 `.filename` 恒为 None，走"未知路径"分支丢弃真实消息 |
| **D3** | `__main__` 自检此前**只有 print、零断言**（哑巴自检），任何渲染退化都不会被发现 |

旁证：`InvalidMaskError` / `DetectionError` 在**生产代码**（`mask_generator` / `detector`）中被使用，docstring 树却漏列——读者会误以为"这类异常不存在"。

---

## 2. 分类与处置

| 缺陷 | 类型 | 修复 |
|---|---|---|
| **D1** 继承树漏列 6 子类 | defect（文档契约不完整） | 补齐 docstring 树的 `InvalidMaskError` / `DetectionError` / `MediaError` 子树三行 |
| **D2** 路径抽取丢失 | defect（信息丢失） | 新增 `_error_file_location(error)`：优先 `error.filename`，其次 `str(error)`（消息型异常的原文），最后"未知路径"；两处 `error.filename or '未知路径'` 改为调用它 |
| **D3** 自检无断言 | defect（盲区） | 自检补 4 条断言：渲染非空、智能识别类型正确、继承树完整性、路径抽取正确 |

**D2 修复后实测**（重跑探针）：`reason='找不到需要的文件：找不到图片文件：foo.png'`、`detail['文件路径']='找不到图片文件：foo.png'`（保留路径）；标准 `FileNotFoundError(2,msg,'baz.png')` 也正确含 `baz.png`。

---

## 3. 守卫与回归

- 新增 `tests/test_exceptions_audit133.py`（5 例）：树完整性 / 消息型 FNFE 保留路径 / 标准 FNFE 保留 filename / 消息型 PermissionError 保留路径 / 全部子类均为 `WatermarkEraserError`。
- 既有 `tests/test_exception_contract.py` 不受影响（覆盖充分但不含 D1/D2）。
- `utils/exceptions.py` 登记进 `tests/test_legacy_selfcheck.py` 的 `MODULES`（自检已含断言，安全可登记）。
- 本轮运行：`test_exceptions_audit133.py` + `test_exception_contract.py` + 子进程自检 = **23 例 EXIT=0**。

### 注入式反例（`s133_inject.py`，`%TEMP%`）
- 记录 FIXED sha256 = `93e2370706fdfcf8`
- 注入 BUGGY（D1 删 6 树行 / D2 还原 `error.filename`）→ 守卫 **returncode=1**（回归被捕获）
- 二进制写回复原 → RESTORED sha256 = `93e2370706fdfcf8`（**字节级一致**）

---

## 4. 沉淀（经验 145 / 146 落 `LESSONS.md`）

- **145**：docstring 的「继承树 / 结构图」是契约——凡写"体系含 X 类"，就要核验 X 真的出现在树里；**漏列会让读者误以为该类不存在**，而它可能在生产代码里被用着。判据：凡是带继承树声明的模块，写一条断言遍历全部子类名 ∈ docstring。
- **146**：「抽到异常里的路径」要兼容两种形态——**标准异常带 `filename` 属性**、**消息型异常（本项目自抛的中文消息）`filename` 为 None 但消息里含路径**。只用 `error.filename or '未知路径'` 会把消息型异常的真实路径丢成"未知路径"；正确做法是优先 `filename`、其次异常消息原文、最后兜底。

---

## 5. 下一步（未审榜）
`services/export_service` 5.4 → `services/file_service` 5.4 → `services/video_export` 5.4 → `utils/path_helper` 5.4。
