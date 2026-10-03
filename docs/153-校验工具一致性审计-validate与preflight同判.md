# 阶段 153 · 校验工具一致性审计：`validate` 与 `preflight` 必须同判

## 动机

阶段 152 审计了收编入口后，顺延审 `scripts/build_real_ground_truth.py`——
它与 `real_asset_preflight.py` **都在「校验 `ground_truth.json`」**，
却是两套独立实现。项目铁律「工具矛盾视为 bug」「同一规则不写两遍」，
故按此方向探针。

## 探针（A/B 对照，同一份真值文件分别喂两个工具）

| 真值文件的缺陷 | `validate`（旧） | `preflight` | 判定 |
|---|---|---|---|
| 孤儿条目（`image_id` 不在 manifest） | `[warn]` → **rc=0** | rc=1（`[真值] … 孤儿条目`） | **矛盾** |
| 缺 `labeled_by` | 不检查 → **rc=0** | rc=1 | **矛盾** |
| 空 `watermarks` | 不检查 → **rc=0** | rc=1 | **矛盾** |
| `auto:` 预填 | 不检查 → **rc=0** | rc=1（循环论证） | **矛盾** |
| `bbox` 越界 | 检查 → rc=1 | **不检查** → 视承诺面而定 | 判据不对称 |
| `bbox` 负值 | 检查 → rc=1 | 不检查 | 判据不对称 |

**结论**：`validate` 自己实现了一套**更宽松**的检查（孤儿只 `[warn]`、不查
`labeled_by`/空 `watermarks`/`auto:`），而 `bbox 在图内` 又是它**独有**、
`preflight` 没有的检查。于是同一个真值文件会出现
**「`validate` 绿（`errors=0`）而 `preflight` 红（rc=1）」**——而 `preflight` 才是
承诺面门禁，用户被 `validate` 的绿灯误导，误以为 P0 已闭合。

## 归因

「同一规则两套实现」的经典漂移：两个工具都声称「校验真值」，各自维护一份判据，
**没有单一真值来源**。项目的 `UPGRADE_CHECKLIST` 还把 `validate` 列为升级步骤之一，
使它成为事实上的用户入口，漂移的代价被放大。

## 最小修复（统一到单一实现）

1. **上收判据**：把「`bbox` 必须为非负整数、且须在图内」两条从 `validate`
   上收到 `real_asset_preflight.audit_truth`（预检从此也查越界，判据不再不对称）。
2. **抽出单一实现** `structural_problems(manifest, truth)` = `audit_manifest`
   + `audit_truth` + `auto:` 预填检查。
   - 预检 `main` 改为调用它（行为不变）；
   - `cmd_validate` 改为**惰性导入并调用同一个它**（消灭第二套判据）。
3. **删除死代码**：`_validate_bbox` 与随之无用的 `List`/`Optional` 导入。

## 守卫（新增 `tests/test_truth_tool_consistency.py`，7 例）

- `test_audit_truth_detects_bbox_out_of_bounds`：单一实现层必须有越界检查。
- `test_validate_and_preflight_agree_on_malformed_truth`（参数化 5 例：孤儿 / 缺
  `labeled_by` / 空 `watermarks` / `auto:` / 越界）：**两者必须同为红**。
- `test_validate_and_preflight_both_green_on_clean_truth`：干净真值两者都绿
  （防止「一律判红」的假修复）。

## 注入式反例（守卫有牙）

| 注入 | 结果 |
|---|---|
| A：`validate` 不再识别 `auto:`（只调 `audit_truth`） | 恰 `[auto 预填]` 用例 **FAILED** |
| B：`audit_truth` 去掉越界检查 | 越界单测 + `[bbox 越界]` 用例 **FAILED**（2 条） |

两次注入**各自精准命中**，还原后两文件 sha256 **逐字节一致**，重跑全绿。

## 回归与真实数据

- 7 个相关测试文件 **182 例全绿**。
- 真实数据集（`benchmark_real/`，0 条标注）：预检仍 `rc=1`，**唯一问题仍是**
  `ai_logo` 缺真值证据（诚实红灯未被误伤，无越界误报）；`validate` `rc=0`。

## 诚实边界（重要）

修复后**结构轴**两工具已同判；但**承诺轴**仍合法地不同：
`validate` 只回答「真值文件结构是否健全」，`preflight` 额外回答「承诺类型是否有证据」。
因此对**当前真实数据集**（空真值）仍是 `validate` 绿 / `preflight` 红——这是**设计如此**，
而且 `validate` 的覆盖面横幅会**显式打出**「✖ 承诺类型缺真值证据：ai_logo」，
不会让用户被静默误导。此边界已由 `test_validate_and_preflight_both_green_on_clean_truth`
（干净真值两者皆绿）与参数化用例（结构缺陷两者皆红）分别钉住。

## 经验（同步 `LESSONS.md`）

- **两个工具校验同一对象 ⇒ 必须有单一实现**：各自实现一套判据，必然漂移成
  「A 绿 B 红」的矛盾结论；用户只会相信对自己方便的那一个。
- **判据不对称同样是 bug**：同一检查（如 `bbox 在图内`）只存在于两个工具之一，
  等于给另一个留了盲区——要么都查，要么都不查（上收到共享函数）。
