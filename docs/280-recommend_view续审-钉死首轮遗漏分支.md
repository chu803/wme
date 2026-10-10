# 280 · `utils/recommend_view` 续审 — 钉死首轮遗漏的成功/边界分支

> 5j 深度复审链第 5 站（接 276 `ui/tab_auto` / 277 `ui/tab_models` / 278 `services/queue_worker` / 279 `services/report_service`）。
> 方法论：**对已审模块按「契约输入类别枚举」做更深一轮**，钉死首轮遗漏分支。
> 结论：**零真缺陷**，首轮遗漏的是「未钉边界」而非代码缺陷（与 276–279 同构）。

## 背景

`utils/recommend_view`（268 行，阶段 28 共享渲染器）把 `Recommendation` 渲染成 `reason_line` / `bars_html` / `markdown` 三种人类可读输出，供 UI 与报告复用。
首轮审计分两层：
- **阶段 28 基础单测**（`test_recommend_view.py`）：三渲染器主路径（标签翻译、置信度、触发特征、空值安全）、`get_recommendation_dict` 的 None/Recommendation/dict 透传/空预设判无效、未知键标签回退、报告 HTML 含/不含推荐区块。
- **阶段 98 i18n 双语化**：分隔符/行模板/空态文案走 i18n、英文侧无中文残留、`bars_html` 条宽与 `_char_bar` 一致钳制到 [0,1]。

首轮聚焦「主路径 + 双语一致」，但**成功路径与边界输入的返回值不变量未钉**：钳制边界、宽度防溢出、未知键过滤、空态文案分支、防御型异常、三渲染器对无效输入的返回差异。

## 方法

1. **枚举契约输入类别**：对 `get_recommendation_dict` / `preset_label` / `_feats_text` / `reason_line` / `_char_bar` / `bars_html` / `markdown` 的每个入参列出「成功/边界/异常」取值。
2. **注入式验证**：直接构造 dict / Recommendation 对象 / 抛错对象，断言返回值不变量，**不依赖 GPU/网络**。
3. **守卫钉死**：把每个被确认的不变量写成机器可判定断言（避免依赖具体语言字面量，统一用 `t(...)` 取当前语言译文）。

## 覆盖清单（16 例守卫）

| 类别 | 守卫 | 钉死的不变量 |
|---|---|---|
| `_char_bar` 钳制 | clamp 上界 `>1`→满条(12█) | 分值越界必须封顶 |
| | clamp 下界 `<0`→空条(12░) | 负值必须封底 |
| | 任意输入长度恒为 `_BAR_LEN` | 长度不随输入漂移 |
| `bars_html` 宽度 | 分值 `>1` → `width:100%` 封顶、绝不 `width:150%` | **阶段 98 修复点防回归**（核心） |
| 未知键过滤 | `scores` 含未知键 → 不渲染该键 | 只渲染 `_FEATURE_KEYS` 内已知特征 |
| 正分值按序 | 只渲染 `>0` 特征、顺序跟随 `_FEATURE_KEYS`；零/负跳过 | 过滤 + 稳定排序 |
| 全零返回空 | `scores` 全 0 → 返回空串 | 无有效条时不出空块 |
| `reason_line` 空态 | `triggered=[]` → 渲染「无」文案（不崩） | 空触发特征分支 |
| `markdown` 空态 | `triggered=[]` → none 文案 / `scores` 全 0 → scores_none 行 | 两处空态分支 |
| `markdown` 钳制 | 分值 `>1` → `_char_bar` 钳到满条 | 字符条与 HTML 条一致钳制 |
| `get_recommendation_dict` 防御 | `to_dict` 抛错 → 返回 None | 防御型异常分支 |
| 偏量 dict | 含预设但缺字段的 dict → 原样透传 | 不全量填充、不崩 |
| `preset_label` 空名 | `""` → `""` | 空名守卫 |
| 三渲染器返回差异 | `reason_line`/`bars_html` 无效→`""`；`markdown` 无效→失败文案（非空） | 阶段 25 原义差异 |

## 结论 · 归因

- **零真缺陷**。首轮审计（28/98）已修全部真缺陷（阶段 98 的 `bars_html` 宽度溢出修复、i18n 双语化）。
- 本轮补的是**边界钳制与空态分支**的未钉不变量——这正是「荒漠度低 ≠ 审完」：首轮靠主路径覆盖了可译性，但 `_char_bar` 钳制两端、`width:100%` 封顶、未知键过滤这些「防水位线」一旦被改坏，不会被任何既有测试抓到。
- 16 例守卫全部落在真实执行路径上（非法构造直接驱动真实函数），非复刻品。

## 验证

- 新增 `tests/test_recommend_view_branches_audit.py`：**16 例守卫全绿**。
- 与既有 `test_recommend_view.py`（阶段 28/98）联跑：全部通过、**无夹具泄漏**（EXIT=0，共 24 例）。

## 自反证伪（self-falsification）

- 若有人把 `bars_html` 宽度钳制 `max(0.0, min(1.0, v))` 误删，`test_bars_html_width_clamped_to_100pct` 将失败（`width:150%` 出现）。
- 若 `_char_bar` 去掉 `max(0.0, min(1.0, ...))`，`test_char_bar_clamps_upper/lower` 将失败。
- 若 `bars_html` 改为遍历 `scores` 全部键（不再限制 `_FEATURE_KEYS`），`test_bars_html_ignores_unknown_score_keys` 将失败（未知键被渲染）。
- 若 `get_recommendation_dict` 去掉 `to_dict` 的 `try/except`，`test_get_recommendation_dict_to_dict_raises_returns_none` 将抛未捕获异常。

## 交付

- 守卫：`tests/test_recommend_view_branches_audit.py`（16 例）
- 登记：`scripts/audit_selector.py` AUDITED_SINCE 注释补 280
- 记忆：`.workbuddy/memory/MEMORY.md` 进度指针 + 本日工作日志
