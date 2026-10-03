# 阶段 124 真值审计：`services/compare_service`

> 选点：`scripts/audit_selector.py --unaudited` 榜首 **`services/compare_service` 10.3**（216 代码行 / 21 引用 / 2 测试文件）。
> 模块是全站唯一的前后对比渲染入口（拼接 / 滑块 / 热图 + 差异摘要），属于服务层，严禁 import ui/gradio。
> 流程：探针量化 → 分类 → 修复 → 回归 → 注入 → 测试 → 文档 → 日志。

## 1. 候选点与量化（探针 `s124_probe.py`）

| 候选 | 探针实测 | 判定 |
|---|---|---|
| **F1** `summarize_pair` 的 `upscaled` 字段 | 结果比原图小（80×60 vs 160×120）时，`upscaled=True`（应为 False） | **真缺陷（语义误报）** |
| 边界 `shrink_for_view` 地板 | `max_side=32` 时限制为 64（文档写「长边 ≤ max_side」） | **设计如此**：防退化安全地板，不改 |

> 关键观察：现有 `test_summarize_pair_upscaled` 只覆盖了「结果更大 → True」，**没有任何用例覆盖「结果更小」**——正是 F1 的盲区。结果比原图小在「用户提供较小结果图」时是真实可达的输入组合，会误导查看器把缩小结果标成「已放大」。

## 2. 根因

旧代码：
```python
rh, rw = b3.shape[:2]          # 结果原始尺寸
a, b = align_pair(a3, b3)      # 结果被缩回原图尺寸
h, w = a.shape[:2]             # 对齐后尺寸（= 原图）
...
"upscaled": bool((rw, rh) != (w, h)),   # ← 用「raw结果 != 对齐尺寸」判定
```
`align_pair` 把结果统一缩到原图尺寸，于是 `raw结果尺寸` 与 `对齐尺寸` 只在「结果≠原图」时就不等——**无论放大还是缩小都会得到 True**。文档语义是「结果是否被放大」，应只关心「结果原始尺寸 > 原图原始尺寸」。

## 3. 修复（最小改动，语义对齐文档）

```python
oh, ow = a3.shape[:2]          # 原图原始尺寸
rh, rw = b3.shape[:2]          # 结果原始尺寸
...
"upscaled": bool((rw, rh) > (ow, oh)),   # 基于两者原始尺寸比较
```
- 结果更大 → `(raw结果) > (raw原图)` → True ✓（保留既有行为）
- 结果更小 → False ✓（修复）
- 等长等宽 → False ✓

## 4. 验证

| 项 | 结果 |
|---|---|
| 模块 `__main__` 自检（新增用例 6：结果更小 → upscaled=False） | **EXIT=0**，6/6 用例通过 |
| `tests/test_compare_service_audit124.py` | 4 例全绿（F1 守卫 / 等长 / 放大回归 / 子进程自检） |
| 受影响回归 `tests/test_compare_viewer.py` + `test_ui_audit96.py` | EXIT=0（无回归） |
| `scripts/audit_selector.py --check` | EXIT=0 |

### 🧪 注入式反例（**已验** —— 回退修复后守卫必红）
回退 `upscaled` 为旧式 `!=` → 跑 `test_f1_smaller_result_not_upscaled` ⇒ **FAIL**（`upscaled` 又变 True）；还原 sha256 逐字节一致。

## 5. 边界登记

- F1 仅在「结果尺寸 ≠ 原图尺寸」时显现；正常超分路径（结果≥原图）一直正确，故长期未被发现。
- `shrink_for_view` 的 `limit = max(64, limit)` 地板：与文档「长边 ≤ max_side」字面不符，但属防退化安全地板（避免缩成 1px 退化图 / 除零），**判定为设计如此，未改**；若日后要让缩略图能小于 64，应单独加开关而非去掉地板。
- 不扩大接口契约；`result_width/height` 仍如实报结果原始尺寸（修复不影响它们）。

## 6. 交付物

| 文件 | 变更 |
|---|---|
| `services/compare_service.py` | F1 修复（`upscaled` 改比原始尺寸）+ 自检新增用例 6 |
| `tests/test_compare_service_audit124.py` | 新增 4 例守卫（含子进程实跑自检，与阶段 122 同构） |
| `docs/124-…md` | 本报告 |
| `scripts/audit_selector.py` | `AUDITED_SINCE["services/compare_service"]=124` + 注释 |
| `README.md` / `TODO.md` / `LESSONS.md` / `MEMORY.md` / 工作日志 | 同步 |
