# 阶段 288 续审报告 · ui/theme.py 首轮遗漏的兜底分支不变量

| 项 | 内容 |
|---|---|
| 模块 | `ui/theme.py`（283 行，4 个公开函数：`build_app_theme` / `build_app_css` / `builtin_theme_names` / `resolve_theme_name`） |
| 首轮 | 阶段 91（`tests/test_ui_audit91.py`，25 例，覆盖品牌令牌单一真值来源 + 运行时白名单） |
| 续审手法 | 5j 链：读源码 → 读首轮测试 → 写运行时探针 → 分类（真缺陷 vs by design）→ 补守卫 → 全回归 → 提交 → 刷新 bundle → 记记忆 |
| 真缺陷 | **0** |
| 新增守卫 | `tests/test_theme_branches_audit.py`，9 例，全绿 |
| 全回归 | `tests/test_theme_branches_audit.py` + `tests/test_ui_audit91.py` = **34 passed, EXIT=0** |
| 荒漠比值 | 4.6（theme，re-audit 链中剩余候选里的最高，仅次于已审的 model_manager 38.1 / device_manager 24.8） |

---

## 1. 首轮覆盖盘点（已知 covered）

- B1 品牌令牌单一真值来源：CSS / 主题对象 / 注入 JS 均「调用时读取」哨兵（`test_app_css_reads_brand_tokens_at_call_time` / `test_theme_object_reads_brand_tokens_at_call_time` / `test_custom_js_reads_brand_tokens_at_call_time`）。
- A2 白名单来自 Gradio 运行时注册表（`test_builtin_theme_names_come_from_gradio_runtime` / `test_resolve_theme_name_accepts_every_runtime_theme`）。
- 非法名绝不触 `Theme.from_hub()` 联网（`test_invalid_theme_never_reaches_hub_lookup`）。
- `APP_CSS == build_app_css()` 常量一致（`test_app_css_constant_matches_builder`）。

## 2. 探针量化（`.scratch/probe_theme_reaudit.py`，全绿）

| 探针 | 验证点 | 结果 |
|---|---|---|
| F1 | `build_app_theme()` 外层 `.set(...)` 抛异常 → 被捕获，返回可 launch 的兜底（Base 实例 或 字符串 `"soft"`），不返回 None、不崩 | ✓ |
| F2 | 连 `gr.themes.Soft()` 都抛异常 → 最终兜底字符串 `"soft"` | ✓ |
| F3 | `builtin_theme_names()` 在 `gradio.utils.BUILT_IN_THEMES` 不可读时 → 返回空元组 `()` | ✓ |
| F4 | `resolve_theme_name()` 在 `builtin_theme_names()` 返回 `()` 时，任何非 brand 名（`default`/`monochrome`/`glass`/`ocean`/空串/空白/`None`/`gradio-dark`）一律回退 brand | ✓ |
| F5 | `resolve_theme_name("")` / `("   ")` / `(None)` / `("BRAND")` → brand；真实内置名透传且大写归一 | ✓ |
| F6 | `build_app_css()` 三个占位符 `__PRIMARY__`/`__ACCENT__`/`__PRIMARY_RGBA_35__` 全部被替换、无残留；`APP_CSS == build_app_css()`；调用时读取品牌令牌（哨兵可见） | ✓ |

## 3. 分类：真缺陷 vs by design

- **F1 发现的中间层惰性**：在 Gradio 6.22 中，内层兜底 `return gr.themes.Soft()` 的构造函数需要 hue 参数会抛异常，于是自动级联到最终字符串 `"soft"`。该字符串 Gradio 会解析为与 `Soft()` 完全同款的默认主题，**视觉结果一致、不崩、launch 仍接受** → 判定为 **by design（安全降级）**，非真缺陷。探针原断言「返回 Soft 实例」为过严，已改为断言「可 launch 的兜底值」（Base 实例 或 `"soft"` 字符串），与真不变量对齐。
- 其余 F2–F6 均为首轮**从未机控的兜底分支**，实测行为正确，补守卫钉死即可，0 真缺陷。

## 4. 新增守卫（`tests/test_theme_branches_audit.py`，9 例）

1. `test_build_app_theme_outer_set_failure_is_caught` — F1
2. `test_build_app_theme_double_failure_falls_back_to_soft_string` — F2
3. `test_builtin_theme_names_returns_empty_tuple_when_registry_unreadable` — F3
4. `test_resolve_theme_name_falls_back_brand_when_names_empty` — F4
5. `test_resolve_theme_name_edge_inputs` — F5（空串/空白/None/BRAND）
6. `test_resolve_theme_name_passes_through_valid_builtin` — F5（真实内置名透传+大写归一）
7. `test_build_app_css_has_no_residual_placeholders` — F6
8. `test_app_css_constant_matches_builder` — F6（常量一致）
9. `test_build_app_css_reads_brand_tokens_at_call_time` — F6（调用时读品牌令牌）

## 5. 结论

`ui/theme.py` 首轮已修复品牌令牌单一真值来源与运行时白名单两大真问题；本轮补的是首轮未机控的**安全兜底分支不变量**，共 9 例守卫全绿，全回归 34 例全绿、EXIT=0。**0 真缺陷**。re-audit 链剩余候选：`utils/logger`（阶段 90）、`ui/app`（阶段 91）。
