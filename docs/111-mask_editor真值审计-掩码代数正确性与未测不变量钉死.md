# 阶段 111 · `core/mask_editor` 真值审计：掩码代数正确，未测不变量钉死

> **选点由脚本给**：`audit_selector.py --unaudited` 榜首 **`core/mask_editor` ratio 13.4**
> （174 代码行 / 13 引用用例 / 1 文件）。审计后该模块进入 `AUDITED_SINCE`（阶段 111）。

---

## 1. 结论先行

| 编号 | 判定 | 内容 | 处置 |
|---|---|---|---|
| **F0** | **无真缺陷（核心代数正确）** | 25 项对抗性探针（含既有测试未覆盖的 RGB 亮度 / RGBA alpha / manual 空分支 / 精确上采样 / 浮点圆心 / 模式大小写 / 输出纯度 / 1D 报错等）**全部通过**；`merge`（并集）/ `manual`（剥离检测层）/ 画笔 / 矩形 的语义均符合文档约定 | 不改动算法 |
| **F1** | 未测但正确的不变量 → **钉成守卫** | 既有 13 例只覆盖 happy path；下面对 16 条未测不变量补机器可判定守卫（防日后回归） | 新增 `tests/test_mask_editor_audit111.py` **16 例** |
| **F2** | 「看似缺陷其实正确」的误报（如实登记） | `target_shape` 大于掩码时，最近邻上采样会**放大掩码面积**（10×10 方块 → ~20×20，像素数 100→400）；这是 `target_shape` 的语义，**不是缺陷** | 用断言显式钉死该行为，防止后人误修（经验：别为"面积不守恒"硬改 resize） |
| **F3** | 声明≠实现边界（核对，无改） | docstring 称尺寸对齐"取较大者"，代码确为 `max(det, user)` 逐维取大；RGB 取亮度、RGBA 取 alpha 与注释一致 | 已核对，正确 |

---

## 2. 探针设计与量化（25 项，全绿）

探针 `%TEMP%/s111_probe.py`（项目内零残留）覆盖以下既往未测路径：

| 类别 | 探针 | 结果 |
|---|---|---|
| RGB 亮度 | 白块/近黑(亮度 10)/纯黑 → 255/255/0 | PASS |
| RGBA alpha | alpha=0 不计、alpha=255 计 | PASS |
| manual 空 det | `merge_masks(None, user, manual)` → 返回完整 user | PASS |
| manual 空 user | `merge_masks(det, None, manual)` → 空 | PASS |
| merge 空 user | `merge_masks(det, None, merge)` → 返回 det | PASS |
| manual+target | `det=None + target_shape=(120,120)` → user 上采样到 120×120（面积 100→400） | PASS |
| 精确上采样 | (2,2) 全 255 → (4,4) 全 255 | PASS |
| 精确下采样 | (3,3) 单点 → (1,1) 仍保留该点 | PASS |
| 浮点圆心 | 40.4,40.4 → 四舍五入 (40,40) + 不改原掩码 | PASS |
| 模式大小写 | "MERGE"/"Manual" 均正确解析 | PASS |
| 输出纯度 | 两模式输出恒 uint8 且只含 {0,255} | PASS |
| 负半径/反向矩形/浮点掩码/1D 报错 | 全部健壮性符合预期 | PASS |
| manual 部分重叠 | 重叠笔迹剥离、独有笔迹保留 | PASS |

**唯一一度"失败"的项**（复跑后澄清）：初版探针误把"上采样后像素数守恒"当预期，断言 `count==100`；实测 `count==400`。**这是探针期望写错，不是代码缺陷**——上采样放大面积是 `target_shape` 的应有语义。修正期望后 25/25 全绿。这一误报本身证明审计流程正确区分了"真缺陷"与"看似缺陷"。

---

## 3. F1：钉死的 16 条未测不变量

`tests/test_mask_editor_audit111.py`：

- `test_rgb_luminance_path` — RGB 按亮度二值化（白/近黑/纯黑）。
- `test_rgba_alpha_zero_not_counted` / `test_rgba_alpha_255_counted` — alpha 通道语义。
- `test_manual_empty_det_returns_user` / `test_manual_empty_user_returns_empty` — manual 两空分支。
- `test_merge_empty_user_returns_det` — merge 空 user 分支。
- `test_manual_empty_det_with_target_shape_upscales` — **钉死上采样放大面积行为**（含 200≤count≤400 的容差断言，防止被误修）。
- `test_upsample_preserves_all_pixels` / `test_downsample_keeps_single_point` — 最近邻上/下采样像素保真。
- `test_brush_float_center_rounds` — 浮点圆心四舍五入 + 不改原掩码。
- `test_mode_case_insensitive` — "MERGE"/"Manual" 大小写。
- `test_output_is_pure_binary_uint8`（参数化两模式）— 输出恒 0/255 uint8。
- `test_brush_negative_radius_returns_copy` / `test_rect_reversed_range_noop` / `test_float_mask_one_zero` / `test_manual_partial_overlap_strips_only_overlap` / `test_1d_input_raises`。

---

## 4. 验证

| 检查 | 结果 |
|---|---|
| 探针 | `%TEMP%/s111_probe.py` 25 项全绿（含 1 项初版误报已澄清） |
| 新增守卫 | `tests/test_mask_editor_audit111.py` **16 例** |
| 关联回归 | `test_mask_editor.py`(13) + `test_mask_editor_audit111.py`(16) + `test_audit_selector.py`(31) = **60 例全绿**（EXIT=0） |
| 全量跑 | 见 §5（重跑至稳定路径；受沙箱批量删除护栏限制，见下） |
| 注册 | `core/mask_editor@111` 进 `scripts/audit_selector.py` 的 `AUDITED_SINCE` + 来源注释 |

---

## 5. 诚实边界与登记

- **本模块无算法改动**：审计结论为"核心代数正确"，交付物是**未测不变量的守卫化**而非缺陷修复——这是真值审计的合法产出（阶段 111 的铁律：区分"真缺陷"与"看似缺陷"，不为有产出而硬改）。
- **`target_shape` 面积不守恒是预期语义**：若日后有人想"让合并掩码像素数守恒"，会破坏坐标对齐（最近邻是唯一不产生灰边的中间值选项的掩码缩放），**不应改**。
- **`_as_binary` 的 `(arr>0)` 阈值未动**：该层处理的是程序化生成的检测/笔迹掩码（非 JPEG 压缩图），低阈值可正确捕获抗锯齿软边；引入 JPEG 噪声误判属于"未复现后果"的投机改动，按技能 5g 不动。
- **全量跑（环境受限，如实登记）**：本项目全量 ~1495 用例在本沙箱**无法跑完**——`test_utils_audit90.py`(日志轮转) / `test_perf2.py` / `test_report_service.py` / `test_recommend_view.py` 等多处 `os.remove`/`rmtree` 累计命中**沙箱单轮批量删除护栏**（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，阈值 50/轮），护栏在 `sitecustomize` 层 `SystemExit(1)` **硬杀 pytest 进程**，日志在 ~47%（甚至更早）处被截断，无法得到完整结论。这与阶段 108/109/110 观察到的同一护栏同源——那几轮恰好在晚期才触发、故只表现为 2 例失败；本轮触发更早、直接中断进程。**属环境配置限制，非本轮代码所致**（本轮改动零删除：新测试仅用内存内 numpy 数组，注册表改动不删文件）。
- **可靠门禁**：直接受影响面 = `test_mask_editor_audit111.py`(16) + `test_mask_editor.py`(13) + `test_audit_selector.py`(31) = **60 例全绿**；加 25 项探针全绿 ⇒ 本轮改动的正确性已被充分覆盖。若需 CI 全绿，须给上述删除类用例的临时目录**白名单**或提高护栏阈值（环境配置决策，等用户定）。
