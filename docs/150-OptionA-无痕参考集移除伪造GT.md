# 阶段 150 · Option A 执行：无痕参考集移除伪造真值

> 接阶段 149（真实基准 GT 模板化伪造审计）。用户于结论处选 **A**：把 30 张图作为**无痕自然参考集**，移除 `ground_truth.json` 里 60 条模板化伪造的 `corner_logo` 标注。

## 1. 决策依据（为何选 A 而非 B）

- **A（已选）**：30 张图本就是无痕自然参考集（自然风光/街拍/人像，无 AI 角标），不该带水印真值 → 移除 60 条伪造标注，`entries:[]`。
- **B（未选）**：用户提供真实带水印 AI 图走 `inbox/` 收编重做真值 → 本轮不做，但**路径保留**：日后仍可经 `inbox/` 收编，届时 `committed_missing` 自动解除。

选 A 的核心收益：**让门禁诚实**——`ai_logo` 承诺处理但**零可信真值**时，原来靠伪造 60 标注撑出的「绿灯 + recall≥0.98」一并撤掉，门禁回到正确的红灯，杜绝「用假证据长期报绿」。

## 2. 改动清单

### 2.1 数据文件（核心）
- `benchmark_real/ground_truth.json`：`entries` 由 30×2=60 条置为 `[]`；旧文件备份于 `benchmark_real/ground_truth.json.bak_149_faked`；新增 `_note` 说明来由。
- 验收（实测）：`python scripts/real_asset_preflight.py` → `ai_logo` 判定「已承诺」但「真值图数 0 < 下限 3；承诺类型缺真值证据 ⇒ real_gate 硬失败」，**EXIT=1**，`committed_missing=['ai_logo']` 红灯。

### 2.2 绑定测试（4 个文件，修订后全绿）
| 文件 | 测试 | 旧断言 | 新断言 |
|---|---|---|---|
| `test_real_asset_preflight.py` | `test_real_dataset_annotations_are_all_manual` | `n_marks==60` / `labeled_by=={manual:measured}` | `n_marks==0` + `auto==[]` + `committed is True` |
| `test_real_asset_preflight.py` | `test_real_scope_matches_config_commitment` | `committed_missing==[]` | `committed_missing==['ai_logo']` |
| `test_watermark_scope.py` | `test_red_line_coverage_is_exactly_ai_logo` | `covered=={'ai_logo'}` | `covered==set()`（无真实标注 ⇒ 受红线约束类型为 0） |
| `test_watermark_scope.py` | `test_real_dataset_coverage_matches_declaration` | `ai_logo==60` / `missing==4` | `ai_logo==0` / `missing==5` |
| `test_watermark_scope95.py` | `test_real_dataset_scope_reports_commitment_face` | `committed_labeled==['ai_logo']` / `committed_missing==[]` / `labeled==['corner_logo']` | `committed_labeled==[]` / `committed_missing==['ai_logo']` / `labeled==[]` |
| `test_watermark_scope95.py` | `test_real_dataset_is_strict_scope_green` | 断言数据集**绿灯** | 改名 `..._red_until_truth`，断言 **红灯**（`complete is False` + `evaluate_real` 失败 + 含「承诺」提示） |

### 2.3 误绑测试解耦（3 处，避免测试绑死在 transient 真实覆盖上）
- `test_gt_integrity_flags_templated_current_real_data`：原直接读真实文件断言守卫能抓模板化；现改吃**合成**模板化真值（`_templated_truth(30)`），守卫逻辑与真实文件状态解耦。
- `test_metrics_unchanged_by_scope_semantics`：原读真实 scope 断言绿灯 + recall=1.0；现改**合成**完整 scope（`_declared_scope(labeled_styles=("corner_logo",))`），只隔离验证「范围语义改动不篡改指标值」这一不变式。
- `test_readme_commitment_column_matches_promise_face`：原禁止承诺类型整行出现「未验证」；现**只查「承诺级别」列**（第 4 格）禁「未验证」，允许「验证状态」列（第 5 格）诚实标「未验证」——解决与 `test_readme_unverified_rows_marked` 的正冲突（后者要求缺真实证据的类型在行内标未验证）。

### 2.4 README 当前态声明
- L21 角落 LOGO 验证状态：`合成用例已验证；真实图**未验证**（阶段 149 证实原 60 个标注为模板化伪造、阶段 150 已移除，待 inbox 收编真实水印图）`。
- L37 承诺代价说明：改为「阶段 150 起真实图基准为 30 张无痕自然参考集（0 个标注）；`ai_logo` 当前 `committed_missing=['ai_logo']` 红灯；其真实 recall/精确率指标**未定义**而非 1.0」。
- L1233 S1 缺口表：原「60 标注 `{'corner_logo':60}`」→ 注明「曾标 60，阶段 149 证实模板化伪造、阶段 150 已移除 ⇒ 当前 0 条真实真值」；后果栏「`recall≥0.98` 建立在伪造真值上，**已作废**」。
- L1712 现状统计表：`ai_logo` 标注数 **60→0**，判定「承诺但未验证（待 inbox 收编真实图）」。
- L1721 守卫说明：由「钉住现有 60 条全为 manual:*」→「钉住不得出现 `auto:*` 预填（阶段 150 起真实基准 0 条标注）」。

## 3. 验收
- `scripts/real_asset_preflight.py` 实测：1 项结构问题（`[承诺] 已承诺却缺真值证据：ai_logo`），**EXIT=1**。
- 4 文件 pytest 全绿：`test_real_asset_preflight` / `test_watermark_scope` / `test_watermark_scope95` / `test_real_gate`（无 FAILURES）。

## 4. 诚实边界（重要）
- 移除后，`ai_logo` 的「真实召回/精确率/掩码指标」现在**未定义**，不再有任何「角标 recall≥0.98」类声明。任何引用该数字处均已标「作废/未验证」。
- 这是**期望的红灯状态**：一旦用户经 `inbox/` 收编真实水印图并人工标注，`committed_missing` 自动解除、相关红灯转绿，且 `test_real_dataset_is_strict_scope_red_until_truth` 应随之翻转回绿灯（届时改回 `..._green`）。

## 5. 遗留
- ① 历史产物清理（`exports/`451、`outputs/`97、`cache/`11074，见阶段 148 扫描报告）——仍待用户确认，未擅自删除。
- ② 真实水印图覆盖：Option A 已选「无痕参考集」；你仍可提供 3~5 张/类（边缘小字/网格点阵/平铺文字/全局渐变）走 `inbox/` 收编真实水印图并人工标注。
