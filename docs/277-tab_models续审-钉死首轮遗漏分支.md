# 阶段 277 · `ui/tab_models` 续审（5j 深度复审）— 钉死首轮遗漏的「成功/边界分支路由」

## 背景

`ui/tab_models` 是模型管理标签页（阶段 12 新增，阶段 19 接入 i18n），薄封装
`core.model_manager`，只做界面呈现与事件绑定。阶段 94 首轮审计（`test_ui_audit94.py`）
聚焦**缺陷**三类：

- **D1** 注册表半初始化（`_ensure_init` 先置 `_INITED=True`）；
- **D2** 构建期 `get_active_model()` 裸调会让整个 app 起不来；
- **D3** 异常必须统一转文案（阶段 81 契约 `format_exception_for_ui`）。

阶段 162（`test_tab_models_i18n.py`）补了「全角分隔符泄漏」。

但**首轮只审了「出错时别崩」，没逐条钉住各回调在「正常 / 边界输入」下的返回值
不变量**。这正是 5j 续审要补的「首轮遗漏」——与 276（tab_auto）同法：枚举契约输入
类别 → 桩掉重型依赖 → 注入式验证分支路由 → 守卫钉死。

## 方法

桩掉 `tm._mm`（可控 `FakeMM`，按 `boom_*` 键抛 `OSError("INJECTED")`、
其余按 `ctrl` 返回受控数据）+ 桩掉 `tm.gr.update`（`Recorder` 捕获实参，因为
`gr.update()` 返回对象**无 `.value` 属性**，无法直接 introspect）。

只验证**分支路由与返回不变量**，不依赖真实模型注册表 / GPU / 网络。每条断言用
「当前语言下的 `t(key)` 子串」或「语言无关的 structural token」
（`'200.5 MB'` / `'*'` / `'—'` / `'/models/lama.pt'` / `'INJECTED'`），对 zh/en 双语稳定。

## 覆盖清单（首轮遗漏的成功/边界分支）

| 函数 | 分支 | 守卫不变量 |
|---|---|---|
| `_render_models_md` | size_mb 正常 | 含 `'200.5 MB'`、`'*'`（active 星标）、`loaded_tag`、`note` |
| | expected_mb（无 size_mb） | 含 `'300'`，且不含 `'200.5'` |
| | size 全未知 | 含 `t("models.size_unknown")`，不含 `'MB'` |
| | 空槽 | `== t("models.empty")` |
| | `list_models` 抛错 | 含 `'INJECTED'`（`read_fail` 文案） |
| | `get_active_model` 抛错 | 含 `'INJECTED'` |
| `_render_status_md` | cpu（无 GPU） | 含 `status_device_cpu`，**不含** vram 行 |
| | gpu（有 GPU） | 含 `status_device_gpu` + `status_vram` 行 |
| | fp16 开 / 关 | 分别含 `status_fp16_on` / `status_fp16_off` |
| | `path=None` | 含 `'—'` 占位 |
| | `get_model_info` 抛错 | 含 `'INJECTED'` |
| `on_set_active` | 空名 | 4 元组，`info == t("models.no_slot")` |
| | ok | `gr.update(choices=['lama','big'], value='big')` + `set_active_done` |
| | fail | `gr.update()`（空参） + `set_active_fail` |
| `on_download` | 空名 | 回退 active → 含 `'/models/lama.pt'` |
| | 进度分数映射 | `_cb` 把 `done/total` 映射为 `0.5`（50/100） |
| | `total=0` | 不抛 `ZeroDivision`，映射为 `0.0` |
| `on_verify` | `None` 名 | 2 元组 |
| | ok / fail | 分别含 `verify_pass` / `verify_fail` |
| `on_rollback` | `None` 名 | 2 元组 |
| | ok / fail | 分别含 `rollback_done` / `rollback_fail` |
| `on_reload` | ok+已载 / ok+未载 / fail | 3 元组；分别含 `reload_loaded_yes/no` / `reload_fail` |
| `on_detail` | `None` 名 | 返回 str |
| | missing / ok / fail | 分别含 `detail_missing` / `verify_pass` / `verify_fail` |
| `_safe_active_model` | 正常 / 抛错回退 | 返回 `'lama'` |
| `on_refresh` | 正常 | 4 元组 + `gr.update(choices=['lama','big'], value='lama')` + `refresh_done` |

## 结论 · 归因 · 验证

- **结论**：**零真缺陷**。首轮审计（94/162）已修掉全部真缺陷；本轮 33 例守卫证实
  所有**成功/边界分支的路由与返回不变量**均 by design 正确，只是「未被钉死」。
- **归因**：首轮审计方法学偏「缺陷驱动」——只问「出错时是否崩」，没问「正常时返回
  形状/文案是否稳定」。5j 续审用「契约输入类别枚举」补上这层，属**边界登记**而非
  缺陷修复（与 276 tab_auto 同构）。
- **验证**：
  - 新守卫 `tests/test_tab_models_branches_audit.py` **33 例全绿**；
  - 与既有 `test_ui_audit94.py`（20 例）+ `test_tab_models_i18n.py` 联跑 **53 例全绿**，
    无夹具泄漏（`tm._mm` / `tm.gr.update` 均 fixture 还原）；
  - 注入式反证：`boom_*` 分支断言 `'INJECTED'` 真实命中异常转文案路径。
- **守卫落点**：`tests/test_tab_models_branches_audit.py`（新增，33 例）。
- **登记**：`scripts/audit_selector.py` `AUDITED_SINCE` 注释补 277 续审注记。

## 自反证伪（self-falsification）

- 若 `gr.update` 行为在未来 Gradio 版本改为有 `.value` 属性，本轮取道 `Recorder`
  捕获实参的方式**不受影响**（不依赖 introspect `.value`）。
- 若某国国际化键被删，`t(key)` 断言会因「返回键名本身」而失败——这正是想要的
  「键缺失即红」护栏，而非误报。
- 进度分数映射守卫只验 `_cb` 的**第二次**调用（第一次是 `download_start` 的 `0.0`
  初始进度），已用 `calls[1]` 索引规避，不会因初始进度变化而假绿。
