# 阶段 123 真值审计：`core/mask_generator`

> 选点：`scripts/audit_selector.py --unaudited` 榜首 **`core/mask_generator` 10.5**（136 代码行 / 13 引用 / 1 测试文件）。
> 模块属于算法层（core），不依赖 Gradio，负责把 ImageEditor 返回的「画笔涂抹 / 矩形框选」标准化为 uint8(0/255) 掩码。
> 流程：探针量化 → 分类 → 修复 → 回归 → 注入 → 测试 → 文档 → 日志。

## 1. 候选点与量化（探针 `s123_probe.py`）

| 候选 | 探针实测 | 判定 |
|---|---|---|
| **A** dict 图层循环对 `(H,W,1)` 图层 | 落 `else` 分支调 `cvtColor(RGB2GRAY)`（需 3 通道）→ **OpenCV 崩溃**；但同模块 `_mask_to_gray_uint8` 能处理 `(H,W,1)` | **真缺陷（崩溃 + 内部不一致）** |
| **B** dict 有 `layers` 但 `background=None`、`target_size=None` | 直接返回 `(1,1)` 空掩码 → **用户涂抹数据静默丢失** | **真缺陷（静默丢数据）** |
| C `_mask_to_gray_uint8` 对 `(H,W,2)` | 抛 `InvalidMaskError`（干净拒绝） | 非缺陷（正确拒绝畸形输入） |
| D tuple 路径 `(H,W,1)` mask | 正常（shape=(100,100)，100 像素） | 非缺陷（仅 dict 图层循环有 A） |
| E 阈值边界 `gray==threshold` | 严格 `>` 正确排除 | 非缺陷（设计如此） |

> 关键观察：**A 是唯一只发生在 dict 图层循环的崩溃**——`_mask_to_gray_uint8` 本就能处理 `(H,W,1)`，dict 循环却另写了一份手动分支且漏了这种形态。这是典型的「同功能两份实现分道扬镳」。

## 2. 修复（最小改动，单一真值来源）

**Fix A**：dict 图层灰度化**统一走 `_mask_to_gray_uint8`**（删除手动 3 分支），消除崩溃，并让 `(H,W,2)` 等畸形输入得到 `InvalidMaskError` 而非 OpenCV 底层崩溃。

**Fix B**：尺寸推断增加「首个有效图层」兜底——`target_size > background > 首个图层`，避免无 background 时静默丢掩码。

两处修复均在 `build_mask_from_editor` 的 dict 分支内，未触及对外契约（`normalize_editor_mask` / `count_mask_pixels` / tuple 路径不变）。

## 3. 验证

| 项 | 结果 |
|---|---|
| 模块 `__main__` 自检（含新增用例 5/6） | **EXIT=0**，6/6 用例通过 |
| `tests/test_mask_generator_audit123.py` | 5 例全绿 |
| `tests/test_legacy_selfcheck.py::mask_generator`（子进程实跑自检） | 通过 |
| 关联回归 `tests/test_mask_editor.py` + `test_mask_editor_audit111.py` | EXIT=0（无回归，mask_editor 引用本模块） |
| `scripts/audit_selector.py --check`（两份登记同源自检） | EXIT=0 |

### 🧪 注入式反例（**已验** —— 回退修复后守卫必红）
回退 Fix A + Fix B（临时落盘）→ 跑守卫测试 → **4 例 FAIL**：
- `test_f1_hw1_layer_dict_no_crash`：`assert (1,1)==(100,100)`（B 回退：丢掩码）
- `test_f1_hw1_layer_matches_internal_helper` / `test_f1_hw2_layer_raises_clean_error`：`(H,W,1)` 与 `(H,W,2)` 走崩溃分支
- `test_f2_dict_without_background_keeps_mask`：再次确认丢掩码

随后**逐字节还原**（sha `7d96bc88…` 一致，纯 LF）。

## 4. 边界登记

- A 的崩溃仅影响「用户图层恰好为单通道」的极端情形；Gradio 默认图层是 RGBA(4 通道)，故线上长期未暴露，但任何下游把单通道数组当图层传入即触发。
- B 仅在「dict 无 background 且无 target_size」时发生；Gradio 6.x 正常流程 background 必存在，属鲁棒性缺陷（防御「调用方未传背景」）。
- 不扩大接口契约；不引入新依赖。

## 5. 交付物

| 文件 | 变更 |
|---|---|
| `core/mask_generator.py` | Fix A（图层统一灰度化）+ Fix B（尺寸兜底）+ 自检新增用例 5/6 |
| `tests/test_mask_generator_audit123.py` | 新增 5 例守卫（A 崩溃 / 内部一致 / 干净拒绝 / B 丢掩码 / 空安全） |
| `docs/123-…md` | 本报告 |
| `scripts/audit_selector.py` | `AUDITED_SINCE["core/mask_generator"]=123` + 注释 |
| `README.md` / `TODO.md` / `LESSONS.md` / `MEMORY.md` / 工作日志 | 同步 |
