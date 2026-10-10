# 284 · `core/preset_recommender` 续审 — 钉死首轮遗漏的决策边界

> 5j 深度复审链第 9 站（接 276–283）。本轮与 280 审过的 `utils/recommend_view` 形成上下游对照闭环。
> 结论：**零真缺陷**，首轮遗漏的是「未钉边界」而非代码缺陷（与 276–283 同构）。

## 背景

`core/preset_recommender`（626 行，阶段 25 推荐决策核心）是覆盖度荒漠榜长驻高位模块——它决定用哪个检测预设，错了会「把检得出的变成检不出」。首轮审计已把**语义主线**钉得很密：
- **阶段 25**（`test_preset_recommender.py`）：各类合成图 → 预设映射（gradient/grid/faint_tiled/tiled/logo/text/plain→general）；`RECOMMEND_ENABLED` 关闭回退；评分 8 键齐全、confidence ∈[0,1]、`to_dict` 可序列化、`recommend_preset_for_path`；服务层透传。
- **阶段 109**（`test_preset_recommend_audit109.py`）：**P0**——`_periodicity` 由 AND 改 OR（与 detector 对齐，一维条纹不再被推给 recall=0 的 `ai_text_heavy`；旧 AND 实现副本反证守卫非空）；真实照片零副作用；**死参数** `sensitivity` 行为+源码+docstring 三重钉死；i18n 出口守卫（核心层 `__str__` 中文不得被字符串化）。
- 精度套件：`test_preset_recommend_accuracy.py`。

首轮聚焦「语义正确的图案 → 正确预设」，但**遗漏了各特征提取器的退化输入边界与若干决策不变量**。

## 方法

探针（`.scratch/probe_preset_recommender_reaudit.py`，10 组）实跑确认：
`_ensure_rgb` 输入矩阵 / `_dominant_period` 退化输入 / `_marks_at` 兼容路径与全 NaN / 常量 score / `_spectral_regularity` 零掩码 / `_gradient_features` 均匀图 / 极小图走完整管线 / confidence 不变量 / to_dict 深拷贝。**行为全部正确**。

兜底 `translucent` 分支用桩隔离其余特征族（`_marks_at` 直接给 marks）确定性命中。

## 覆盖清单（31 例守卫）

| 类别 | 守卫 | 钉死的不变量 |
|---|---|---|
| `_ensure_rgb` 输入矩阵 | None→`ValueError`；2D 灰度→堆叠 3 通道等值；RGBA 剥 alpha 保留 RGB；(H,W,2)/1D/4D→`ValueError`；float32→uint8 | 图像规整的完整契约 |
| `_dominant_period` 退化 | 空 / 长度<8 / 全零 / 常量(std<eps) ⇒ 恒 `(‑1, 0)`；正常条纹 ⇒ `period>0` | 找不到周期时不误报（对照组防恒返回 -1） |
| `_marks_at` 边界 | **兼容路径 residual=None** 返回 0/255 掩码；全 NaN（含/不含 residual）⇒ 全零；常量 score(MAD=0) ⇒ 零标记 | 稳健统计门限的退化安全 |
| `_spectral_regularity` | 全零掩码 ⇒ `0.0` | 零能量不除零 |
| `_gradient_features` | 均匀图（var_gray<1e-6）⇒ `(0,0,0)` | 否则 `explained` 会是 NaN |
| 极小图 | (1,1)/(4,4)/(16,16) 下 corner/text 助手不崩；走完整 `recommend_preset` ⇒ general 兜底、conf=0 | 小图不触发形状异常 |
| 决策不变量 | **confidence == max(scores)**；scores 恒 8 键、全在 [0,1]；triggered ⊆ scores 键 | 多处消费方据此显示置信度 |
| 数据结构 | `to_dict` **深拷贝**（改返回值不污染原对象）；空 triggered 的 `__str__` 含「无」；默认 confidence=0.0 | 快照语义 |
| 兜底分支 | 无局部信号但半透明覆盖 ≥0.3 ⇒ general + `triggered=["translucent"]` | 「无强信号」仍记一笔便于展示 |

## 结论 · 归因

- **零真缺陷**。首轮（25/109/精度套件）已修全部真缺陷（含 109 的 AND→OR P0）。
- 本轮补的是**退化输入与决策不变量**——例如 `confidence == max(scores)` 是 UI/报告多处显示置信度的依据，此前无任何测试断言它；一旦有人改 `confidence` 的取法（如改成加权平均），界面显示会与实际分值脱节而不被发现。

## 验证

- 新增 `tests/test_preset_recommender_branches_audit.py`：**31 例守卫全绿**。
- 全回归（284 + 25基础 + 109审计 + 精度 + batch_recommend + recommend_view×2，`-m 'not slow'`）：**91 例全绿**、EXIT=0、无夹具泄漏。
  （`-m slow` 的两条需真实数据集/检测器，已由既有门禁覆盖，不在本轮范围。）

## 自反证伪

- 若 `_ensure_rgb` 去掉维度校验，`test_ensure_rgb_rejects_bad_dims` 将失败。
- 若 `_dominant_period` 去掉 `std < 1e-6` 守卫，常量输入将触发除零/NaN，`test_dominant_period_degenerate_returns_minus_one` 将失败。
- 若 `to_dict` 去掉 `dict()/list()` 深拷贝，`test_to_dict_is_deep_copy` 将失败。
- 若 `confidence` 改成非 `max(scores)`，`test_confidence_equals_max_scores` 将失败。
- 若兜底分支的 `trans_act >= 0.3` 被改，`test_fallback_translucent_branch` 将失败。

## 交付

- 守卫：`tests/test_preset_recommender_branches_audit.py`（31 例）
- 探针：`.scratch/probe_preset_recommender_reaudit.py`
- 登记：`scripts/audit_selector.py` AUDITED_SINCE 注释补 284
- 记忆：`.workbuddy/memory/MEMORY.md` 进度指针 + 本日工作日志