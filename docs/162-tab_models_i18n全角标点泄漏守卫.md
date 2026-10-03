# 阶段 162 · `ui/tab_models` i18n 契约守卫（兼「全角标点泄漏」这一类）

## 动机

覆盖度荒漠榜（`scripts/` 已补齐）次席 = `ui/tab_models`（281 代码 / 15 引用测试 / 比值 18.7）。
它是「模型管理」页，docstring 自述「只做界面呈现与事件绑定，不含业务逻辑」。本轮审计其
**接线契约**与**行为健壮性**，并顺带扫「用户可见硬编码文案」。

## 探针

### (a) 接线契约（AST，skill 5h）：0 违例
7 个回调的 `inputs` 数 == 形参数 − 框架注入项，`outputs` 数 == `return` 元组长度，**全部对齐**
（且该契约已被 `tests/test_ui_wiring_contract110.py` 项目级覆盖）。

### (b) 行为健壮性（stub `core.model_manager`）：全部兜住
对 `list_models / get_active_model / get_model_info / set_active_model / download_model /
verify_model / rollback_model / reload_active / slot_detail` 逐个注入异常，并附边界输入
（空 `name` / 非法槽名）：10 个 `on_*` 处理器**无一穿透异常**，返回元组长度全部正确。
⇒ 本模块的接线与行为 **by design 正确**。

### (c) 抓到：i18n 泄漏（硬编码全角分隔符）
`_render_status_md` 把分隔符**硬编码**成全角：
```python
lines = [t("models.status_title"), "", f"- {dev_line} ｜ {dev_label}"]
```
项目自身的约定是「分隔符放在**本地化模板**里」——同一页的 `slot_line` 就是 zh 用 `｜`、en 用 `|`。
这一处游离在模板之外 ⇒ **en-US 下仍显示全角 `｜`**（用户可见泄漏）。

**更深一层（守卫盲区）**：项目既有的 i18n 文字量守卫（`test_utils_audit98._first_cjk`、
`test_ui_audit94` 的正则、`test_ui_wiring_contract110` 的 `cjk` lambda）**只扫 CJK 汉字
（U+4E00–U+9FFF）**，因此「全角/CJK 标点（U+FF00–FFEF / U+3000–303F）类泄漏」在结构上
**对守卫不可见**——全角 `｜`（U+FF5C）永远扫不出来。

## 最小修复

- 新增 i18n 键 `models.status_device_line`（zh：`"- {line} ｜ {label}"`，en：`"- {line} | {label}"`），
  与 `slot_line` 的本地化约定一致；`tab_models` 改用它。

## 守卫

`tests/test_tab_models_i18n.py`（**6 例**）：

- **渲染契约**：en-US 状态面板 / 模型清单**不得含任何全角/CJK 字符**；zh-CN 仍须含（反向守卫）；
  分隔符随语言切换（en ASCII `|` / zh 全角 `｜`）；
- **源码级**：AST 扫 `tab_models` 的字符串常量（剔除 docstring、`t(...)` 实参、`log.*(...)` 实参），
  用**加强字符类**（CJK 汉字 + CJK 标点 + 半/全角形式）断言「无用户可见硬编码非 ASCII 文案」——
  这正是既有 CJK-only 守卫的补集。

## 注入式反例（守卫有牙）

`inject_162.py`（系统 temp + `retention=all`，零删除）：把分隔符改回硬编码 `f"- {dev_line} ｜ {dev_label}"`
→ pytest **exit=1（红：渲染契约 + 源码级守卫同时命中）**；还原字节级一致 sha=`d8a0cc94fd13f6c2…`
→ **exit=0（绿）**。守卫有牙：YES。

## 回归

新 6 + `test_i18n_audit137` + `test_ui_wiring_contract110` + `test_ui_audit94` + `test_ui_audit97`
+ `test_legacy_selfcheck` = **119 例全绿**（新增 i18n 键未破坏键集对账）。

## 诚实边界

- **同类硬编码全角标点在其它 UI 模块亦存在**（全仓 AST 扫描命中）：`tab_history`（`｜` / `｜视频` /
  `｜PDF ·`）、`components`（`、` / `（` / `）`）、`tab_batch` / `tab_media`（`，`）等——本阶段**只修目标模块**，
  不逐一改动，避免一次性大范围波及。
- **建议的机械化收口**（登记为后续）：把共享 i18n 守卫的字符类从「CJK 汉字」扩到
  「CJK 汉字 + CJK 标点 + 半/全角形式」，让这一类**结构性可检出**，再统一 triage；此前它只能靠人肉发现。
- note/display_name 等**注册表数据**若为中文本不属本模块文案泄漏（数据 vs 文案须区分）。

## 产物

`docs/162-tab_models_i18n全角标点泄漏守卫.md`
