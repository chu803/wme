# 阶段 164 · `ui/tab_batch` 助手契约 + i18n 泄漏守卫

## 动机

覆盖度荒漠榜次席 = `ui/tab_batch`（644 代码 / 40 引用测试 / 比值 16.1）。既有覆盖集中在 `run_batch`
的队列接线 / 历史登记 / 结果导出 / 对比视图；而**批量页真正的输入规整与决策助手**以及**任务级消息的 i18n**
此前无守卫。

## 探针 A：纯逻辑助手 —— 全部 by design 正确（21 例守卫）

| 助手 | 契约 |
|---|---|
| `_normalize_file_value` | None/str/dict/list（含 `{'path'}`/`{'name'}`/None/未知标量）→ 纯路径列表、去重保序；未知标量 → `[]` |
| `_mask_path_for` | 非目录 → None；按**主名**跨扩展名匹配；多候选按 `_MASK_EXTS` 顺序取首个（确定性） |
| `_resolve_batch_preset` | 关推荐 ⇒ fallback；推荐到具体预设 ⇒ 用它；推荐回默认 ⇒ fallback；推荐异常 ⇒ fallback 不拖垮批次 |
| `_preset_display` | 已知 → 展示名；未知 → 原样 |
| `_build_editor_value` | `layers[0].alpha == mask`、标记色番茄红、background 同一对象 |

## 探针 B：i18n —— 抓到真实泄漏

`ui/tab_batch` 有 **13+ 处用户可见硬编码中文**（经**加强字符类**扫描发现，该类比项目既有
CJK-only 守卫覆盖面更广）：

- `_process_one` 的 `raise ValueError(f"跳过（…）")` / `"读取失败：…"` / `"未找到同名掩码文件（…）"` /
  `"未检测到水印区域（…）"` / `"修复未产出结果"` —— **会经 `batch.log_fail` 渲染进进度日志**（真泄漏）；
- 进度文案 `on_progress(…, "已读取图片，正在准备掩码…")` 等 6 处（存入 `task.message`）；
- 画廊说明 `f"{name} · 质量{q.score}分{icon}"`、预设分布分隔符 `"，"`、进度条 `f"排队 {n} 张"` / `f"完成 {a}/{b}"`。

⇒ en-US 下这些位置仍显示中文（`跳 过（…）`、`· 质量88分`、`排队 3 张`）。

## 最小修复

新增 **17 个 i18n 键**（`batch.task_*` / `batch.progress_*` / `batch.caption_quality` / `batch.list_sep`，
zh/en 两表同步），`_process_one` / `_on_update` / `run_batch` 的相关字面量全部改走 `t()`。
（`—` 等**语言中性占位符**刻意保留，不纳入扫描字符类。）

## 守卫

`tests/test_tab_batch_helpers.py`（**40 例**）：纯助手 21 例 + 17 个新键**双语参数化**
（zh 必须含中文/全角、en 必须不含）+ **源码级 AST 扫描**（剔除 docstring / `t()` 实参 / `log.*` 实参，
用加强字符类断言 tab_batch 无用户可见硬编码非 ASCII）+ 外层模板本地化断言。

## 注入式反例（守卫有牙）

`inject_164.py`：把画廊说明改回硬编码中文 → 源码级守卫 **红**；还原**原始字节 sha**
`ea806571ed2e7365…` → **绿**。本脚本按经验 #188 编写：读写 `newline=''`、**sha 对原始字节计算**，
故该 CRLF 文件被**逐字节**保留（raw sha 前后一致，非文本 sha 自比）。

## 回归

新 40 + `test_i18n_audit137` + `test_image_history_record` + `test_ui_wiring_contract110`
+ `test_processing_prefs` + `test_legacy_selfcheck` = **168 例全绿**。

## 诚实边界

- **更深一层的同类泄漏（本阶段新发现，登记不改）**：`utils/exceptions.py` 的异常文案是**中文**
  （`【不支持的图片格式】\n原因：…\n建议：…`）。tab_batch 修好外层模板后，`{reason}` 仍把中文带进
  en-US 的整条消息。这是**独立的一层**，不属 tab_batch；本轮只登记（见经验 189），留待专门阶段处理。
- `on_progress` 的消息目前只进 `task.message`（不在 UI 渲染），本轮一并本地化以防未来渲染面扩大。
- 同类硬编码**全角标点**在 `tab_history` / `components` / `tab_media` 仍存（阶段 162/164 已登记整类收口方向）。

## 产物

`docs/164-tab_batch助手与i18n守卫.md`
