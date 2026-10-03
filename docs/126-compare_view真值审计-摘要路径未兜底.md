# Stage 126 — ui/compare_view.py 真值审计：摘要路径未兜底（I2 同类残余）

> 审计方法：探针量化 → 分类（缺陷 vs by-design） → 最小修复 → 回归门禁 → 注入式反例 → 守卫测试 → 文档 → 记忆同步。
> 审计对象：`ui/compare_view.py`（前后对比查看器组件，阶段 38 新增，界面层薄封装）。
> 荒漠度：`audit_selector.py --unaudited` 榜首 **8.0**（code 167 / refs 21 / files 2）。

## 1. 探针量化

探针 `s126_probe.py`（写 `%TEMP%`，项目零残留）monkeypatch `_cs.summarize_pair` 抛 `RuntimeError`：

| 项 | 结果 |
|---|---|
| 基线：三模式（concat/slider/heatmap）渲染 | 全部可用 |
| `summarize_pair` 抛错时 `render_pair` | **泄露 `RuntimeError`**（缺陷 D1 成立） |
| 还原 monkeypatch 后 `render_pair` | 正常 |

## 2. 缺陷定性与分类

### D1（真缺陷）：`render_pair` 摘要路径未兜底
- **症状**：`render_pair` 的 docstring 明文承诺「各类异常情况都返回可读提示而非抛错」，阶段 97 的 I2 根因修复只把 `_cs.render_pair_html` 包进 try/except，**同一 return 路径上的 `format_summary`（内部调 `_cs.summarize_pair`）仍是裸调**。`summarize_pair` 的 try/except 只覆盖 `load_image`；其后的 `align_pair`（含 `_require_cv2()`，可抛 ImportError）与 numpy 广播/统计段**无服务级兜底**——一旦抛错，异常穿透到 Gradio 画廊 `.select` 回调，泄露完整 traceback。
- **定性**：真缺陷（违反本模块自身成文承诺；属「根因修复未修全的同类残余」——修复只护住了第一个服务调用，漏了同一 return 语句里的第二个）。
- **最小修复**：`format_summary` 调用包 try/except；失败时**保留已渲染的 HTML**（不连坐渲染结果），摘要退回「条目名 + `compare.viewer.summary_none` 占位」，并打 WARNING 日志。

### 边界（by-design，不修）
- `export_pair` 对 `_cs.export_pair_compare` 不加 UI 层兜底：该服务 docstring 成文承诺「任一图缺失或写盘失败返回 None（不抛异常）」，且内部自带 try/except——依赖成文服务契约，属设计内。
- `render_pair`/`export_pair` 对畸形 `pair` 的防御_unpack（`list(pair)+["",""][:n]`）：异常形状一律落入 `no_select`/`no_pair`/`export_fail` 可读提示，行为正确。

## 3. 守卫与注入式反例

- 回归守卫：`tests/test_compare_view_audit126.py`（4 用例：D1 三模式兜底 + 正常路径不回归 + I2 旧守卫邻位复检）。
- 子进程守卫：`tests/test_legacy_selfcheck.py` 的 `MODULES` 新增 `("ui/compare_view.py", [])`（Stage 122 铁律）。
- 注入式反例：`s126_inject.py`（写 `%TEMP%`）——FIXED 守卫通过 → 写回 BUGGY（兜底还原为裸调，D1 复活）→ 守卫捕获回归 → 还原 FIXED，`sha256=4a23bef34fe8c8e6e7545fd6fafd850830c28e5d654b745d7a19b732cfcf0035` 字节一致。

## 4. 影响面与回归
- 修复只改 `render_pair` 的 return 段（新增 try/except + 失败降级），正常路径行为逐字节不变（`test_compare_viewer.py` 17 例全绿）。
- 全量本阶段验证：`test_compare_view_audit126.py`(4) + `test_compare_viewer.py`(17) + `test_legacy_selfcheck[ui/compare_view.py]`(1) = **22 例 EXIT=0**。
