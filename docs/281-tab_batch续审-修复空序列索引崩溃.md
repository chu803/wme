# 281 · `ui/tab_batch` 续审 — 钉死首轮遗漏分支 + 修复 1 真缺陷

> 5j 深度复审链第 6 站（接 276 `ui/tab_auto` / 277 `ui/tab_models` / 278 `services/queue_worker` / 279 `services/report_service` / 280 `utils/recommend_view`）。
> 结论：**发现并修复 1 个真缺陷**（画廊点选空序列索引崩溃，同根因 3 处），另钉死首轮遗漏的成功/边界分支。
> 与 276–280（零缺陷）不同，本站首轮确实遗漏了一个**会崩溃的边界**。

## 背景

`ui/tab_batch`（897 行，阶段 5 核心界面）是覆盖度荒漠榜长期高位模块。首轮审计分六层，已把**纯助手**钉得很密：
- **阶段 26/164/191**：`test_batch_recommend` / `test_tab_batch_helpers` / `test_ui_tab_seams` —— `_normalize_file_value` / `_mask_path_for` / `_resolve_batch_preset` / `_preset_display` / `_build_editor_value` 的完整契约 + 任务级 i18n 双语。
- **阶段 97**：`test_ui_audit97` —— run_batch 早退 yield 5 值（I1）、render_pair 异常兜底（I2）。
- **阶段 110**：`test_ui_wiring_contract110` —— 全 ui 接线契约（inputs 数量 / 内部尾部形参）。
- **阶段 193**：`test_elapsed_display_d2` —— 耗时行。

但首轮全部聚焦「纯函数 + 出错路径」，**遗漏了 `run_batch` 内部闭包 `_on_update` 的成功子分支**与 **`export_report_from_batch` 全部四分支**，以及一个会崩的边界。

## 真缺陷 · 画廊点选空序列索引崩溃（已修复）

### 现象
`on_gallery_select` / `on_gallery_select_with_mode` / `tab_history.on_select` 三处归整 `(row, col)` 索引时写作：

```python
if isinstance(idx, (tuple, list)) and idx:   # ← 空序列过不了 and idx
    idx = idx[0]
if idx is None or not (0 <= int(idx) < len(...)):   # ← int(()) 抛 TypeError
```

**空**序列 `()` / `[]` 能过 `isinstance(idx, (tuple, list))`，但过不了 `and idx`（空序列 falsy）⇒ 未被规整就落到 `int(idx)`。

### 后果
`int(())` → `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'tuple'`，
**穿透到 Gradio 画廊点选回调**，泄露 traceback，违反函数自身文档承诺「索引越界…置 None 并给出提示文案」。

### 探针实证
`.scratch/probe_tab_batch_reaudit.py` 输出：
```
4 idx=(0, 1)     -> pair=set   html='HTML[concat]'   ← 非空元组正常规整
4 idx=()         -> RAISED TypeError: int() ... not 'tuple'   ← 缺陷
```

### 修复（LESSON 265：同一根因往往不止一处 → 三处一并修）
```python
if isinstance(idx, (tuple, list)):
    idx = idx[0] if idx else None    # 空序列也归一到 None
```
修复点：`ui/tab_batch.py::on_gallery_select`、`ui/tab_batch.py::on_gallery_select_with_mode`、`ui/tab_history.py::on_select`。

## 注入式反向验证（证明守卫非空）

把 3 处退回旧写法后独立进程复跑（`.scratch/inject281_verify.py`）：
```
INJECTED: 已把 3 处退回旧写法
INJECTED_RC= 1
  E  TypeError: int() argument must be ... not 'tuple'   ui\tab_batch.py:647
  E  TypeError: int() argument must be ... not 'list'    ui\tab_batch.py:647
  E  TypeError: int() argument must be ... not 'tuple'   ui\tab_history.py:267
  FAILED test_batch_gallery_select_empty_seq_degrades[bad_idx0/bad_idx1]
  FAILED test_history_gallery_select_empty_seq_degrades
RESTORED: True
```
⇒ 守卫**真命中**缺陷，且源码已完整还原。

## 本轮钉死的其余遗漏边界（24 例守卫）

| 类别 | 守卫 | 钉死的不变量 |
|---|---|---|
| 真缺陷回归 | 空序列 `()`/`[]` → 降级为「未选中」（batch ×2 + history ×1） | 不抛 TypeError |
| | 非空序列 `(0,1)` / `[0]` / 修复后仍正常解析 | 修复不破坏成功路径 |
| `export_report_from_batch` | 空产出 → `(None, report_empty)` | 无产出不报错 |
| | 成功 → `(path, report_ok)` | 返回值形状 |
| | `fmt=None` → 归一为 `'html'` 传给下游 | 缺省不泄漏 None |
| | `FileNotFoundError` → 可读提示 | 不上抛 |
| | 通用异常 → 兜住 + 提示 | 不上抛 |
| `_on_update` DONE | `compare=True` 画廊项是拼接**数组** | Before\|After 可视化 |
| | `compare=False` 画廊项是**路径字符串** | 退回结果图 |
| | 画廊 / 配对表 / 报告源三者**同序同长** | 阶段 38 契约 |
| | 质量 caption 图标 good/warn/review 各一；**未识别 level → 空图标** | `.get(level,"")` 兜底 |
| | FAILED / CANCELLED 各写日志且**不进画廊** | 终态不变量 |
| | 推荐结果 → 追加理由行（含置信度 `90%`） | 阶段 28 |
| | 预设分布 → 末尾 `标签×N` 行 | 阶段 26 |

## 验证

- 新增 `tests/test_tab_batch_branches_audit.py`：**24 例守卫全绿**。
- 全回归（tab_batch + tab_history + 接线 + 耗时 + 审计各套件）：**165 例全绿**、EXIT=0、无夹具泄漏。
- 注入式反向验证：`INJECTED_RC=1`（守卫真命中），源码 `RESTORED=True`。

## 自反证伪

- 若把空序列规整改回 `and idx` 写法，`test_batch_gallery_select_empty_seq_degrades[bad_idx0/1]` 与 `test_history_gallery_select_empty_seq_degrades` 必抛 TypeError（已实证）。
- 若修复误改成「空序列直接取 `idx[0]`」而没判空，非空路径测试仍绿但空序列崩——由 bad_idx 两例兜住。
- 若 `export_report_from_batch` 去掉 `fmt or "html"` 归一，`test_export_from_batch_fmt_none_defaults_to_html` 将捕获 `fmt=None` 下传。
- 若 `_on_update` 的质量图标改为直接 `q.level` 查表而漏 `.get(..., "")`，`unknown_level` 用例将失败。

## 交付

- 守卫：`tests/test_tab_batch_branches_audit.py`（24 例）
- 修复：`ui/tab_batch.py`（2 处）、`ui/tab_history.py`（1 处）
- 探针：`.scratch/probe_tab_batch_reaudit.py`、`.scratch/inject281_verify.py`
- 登记：`scripts/audit_selector.py` AUDITED_SINCE 注释补 281
- 记忆：`.workbuddy/memory/MEMORY.md` 进度指针 + 本日工作日志
