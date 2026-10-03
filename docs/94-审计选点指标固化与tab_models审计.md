# 阶段 94 · 审计选点指标固化 + `ui/tab_models` 审计

> 承接阶段 92/93。93 把**验证面**补到位（S2/S3/S4 闭环、S1 只缺真实图）；
> 本阶段回到**审计主线**，先把「选谁审」这件事本身变成可复现、有守卫的工具，
> 再用它审出断层第一的模块。

---

## §0 一句话结论

**「覆盖度荒漠」排名此前是不可复现的手算值，且藏着一条坏数据；把它固化成脚本后，
选出了真正的断层第一 `ui/tab_models`（比第二名 2.96 倍），审出 3 个真缺陷（含 1 个
在 `core/model_manager` 的根因）+ 3 项登记边界；修复后注入式反例 6/6 捕获，
两条门禁零回归，全量 1277 例零失败。**

---

## §1 起因：一张会骗人的排名表

阶段 93 复核时按「模块行数 ÷ 引用它的测试文件中的 `def test_` 个数」重算选点，
结果推翻记忆里的数：

| 模块 | 记忆里的值 | 实测重算 | 结论 |
|---|---|---|---|
| `ui/tab_models` | 356 / 4 = 89.0 | **262 / 4 = 65.5** | 仍是断层第一（口径差异在行数计数） |
| **`auto_pipeline`** | **575 / 10 = 57.5** | **389 / 50 = 7.8** | ❌ **记错**（分母差 5 倍），实为**中游** |
| `ui/tab_settings` | 674 / ? | 未进前 18（< 9.6） | ❌ **不是荒漠** |
| `services/postprocess_service` | 655 / 34 | 357 / 34 | ✅ 分母一致 ⇒ 证明新口径正确 |

**根因**：该排名历来靠**临时探针手算**，没有脚本、没有守卫 ⇒
它属于经验 5 说的「手工脚本 + 文档维持的结论 = 没有守卫」，
会**安静地**把决策引向错误目标（`auto_pipeline` 被当成第三优先，实际中游）。

### 1.1 第一版探针就掉进了已知陷阱

第一版探针只匹配 `from layer.name import`，于是把阶段 90/91 **已审过**的
`utils/logger`、`ui/theme` 算成 **0 引用（ratio = inf）**。这正是记忆里
明确警告过的「别名导入严重低估」——说明**靠记忆里的警告防不住，必须有机器断言**。

---

## §2 交付物 A：`scripts/audit_selector.py`

### 2.1 口径（两个陷阱都写死在代码里）

- **分子 = 代码行**：剔除空行、`#` 注释、三引号块内部与**文档串起始行**。
  实现刻意「粗糙但确定」：三引号只做奇偶配对，不解析嵌套。排序不需要精确到行，
  **需要的是同一个口径**。
- **分母 = 四写法导入匹配**（只用第一种就会重演 1.1 的低估）：
  ① `from layer.name import …`　② `import layer.name`
  ③ `from layer import …,name…`　④ **引号内**的字面量 `"layer.name…"`
  （覆盖 `monkeypatch.setattr` / `patch` 目标；**刻意要求带引号**，
  以免把「注释里提到模块名」算成引用）。
- 顶层模块（`config`/`server`/`api`/`cli`）无包前缀，按模块名匹配。

### 2.2 自检断言（防空转）

`KNOWN_AUDITED` 登记阶段 88–91 已审过的 6 个模块，**断言它们引用数 > 0**；
任一降为 0 即非零退出。这条断言不是装饰——1.1 的第一版探针正是把它写丢。

### 2.3 观察者效应（本阶段最有意思的发现）

补上 `tests/test_audit_selector.py`（其中断言「`ui/tab_models` 是第一荒漠」）后，
**该文件立刻被自己的匹配器算成对 `ui/tab_models` 的「引用」**：

```
审计前  ui/tab_models  hit_files=[test_ui.py]                        262/4  = 65.5
补量具后 ui/tab_models  hit_files=[test_audit_selector.py, test_ui.py] 262/23 = 11.4  ← 掉出前三
```

**量具污染了被测对象**。修法 = 显式登记 `META_TEST_EXCLUDES`（不做隐式名字猜测）。

> ⚠ 关键区分：`tests/test_ui_audit94.py` **真的在测** `ui/tab_models`，
> 它**应当**计入分母。只有「测量具本身」的文件才是污染。
> 因此本阶段审计完 `tab_models` 后它的比值**合法地**从 65.5 降到 ~13.8。

---

## §3 交付物 B：`ui/tab_models` 审计

选点结论：**`ui/tab_models` 262 代码行 / 仅 1 个引用文件（`test_ui.py`，4 例）/ 比值 65.5
= 第二名的 2.96 倍**。它是「模型管理」标签页：列模型槽、切当前模型、下载权重、
校验完整性、回滚备份、热重载、看详情 —— 七个 handler 全都**直接触碰文件系统与网络**。

### 3.1 探针量化：异常护栏覆盖率 3/9

把每个 handler 的依赖逐一替换成抛异常，观察是否优雅降级：

| 调用点 | 结果 | 备注 |
|---|---|---|
| `on_download` | ✅ GUARDED | 有 try/except + `format_exception_for_ui` |
| `_render_models_md` | ✅ GUARDED | 内部 try |
| `_render_status_md` | ✅ GUARDED | 内部 try |
| `on_refresh` / `on_set_active` / `on_verify` / `on_rollback` / `on_reload` / `on_detail` | ❌ **PROPAGATES** | 原始 traceback 直达 Gradio |

**3/9 优雅降级，6/9 把原始异常抛给用户** —— 违反阶段 81 建立的
「UI 唯一异常入口 `format_exception_for_ui`」契约。

### 3.2 真缺陷（3 项，含 1 项根因）

| 编号 | 位置 | 缺陷 | 为什么是缺陷 |
|---|---|---|---|
| **D1** | `core/model_manager._ensure_init` | `_INITED = True` 写在初始化**之前** ⇒ 中途抛异常后注册表**永久半初始化**且不再重试，异常却仍冒给调用方 | 同族：阶段 79「容错成为错误的藏身处」。**这是 D2/D3 的共同根因** |
| **D1b** | `core/model_manager._init_discover` | 「自动发现」的目录 I/O（`os.listdir`）失败会**让整个注册表初始化失败** | 「自动发现」是**可选增强**，其失败不该让主功能（内置槽 lama）不可用 |
| **D2** | `ui/tab_models.build_models_tab` | **构建期**裸调 `_mm.get_active_model()` | 构建期在应用启动路径上 ⇒ 失败形态是**整个应用起不来**（不是单页报错）；同文件 `_slot_choices()` 有回退、`_render_*` 有 try ⇒ **同一风险面三种处理方式** |
| **D3** | `ui/tab_models` 五个 handler | 无异常护栏，而 `on_download` 有 | 与阶段 81 契约不一致；用户看到未格式化堆栈而非 `models.*_fail` 友好文案 |

### 3.3 登记边界（刻意不改）

| 项 | 事实 | 为什么不改 |
|---|---|---|
| `gr.Markdown(label=…)` | `container=False`（**默认**）时 `label` **永不渲染**；显式 `show_label=True` 亦然（Gradio 6.22 实测会警告 `show_label has no effect when container is False`） | `tab_models` 两处 `label=t(...)` 与 `tab_history` 的 `reg_label` 都是**无害死元数据**。**不得**据此把 `reg_label` 扩散到更多 Markdown 上——那是把死代码扩散 |
| `on_download(progress: gr.Progress = gr.Progress())` | 默认值在导入期求值 | 实测可构造、可当无参回调调用（无请求上下文时空操作），**不是缺陷** |
| `_slot_choices()` 的 `["lama"]` 硬编码回退 | 注册表挂了时给下拉框一个可用项 | 它的存在意义**正是**这个场景；改成动态回退会与自身保护目的矛盾 |

> **本阶段最重要的一次"没动手"**：我曾据「`tab_history` 注册了 Markdown 的 label 而
> `tab_models` 没注册」判定 `tab_models` 有 i18n 缺陷，准备照着补 `reg_label`。
> 先核验 Gradio 语义后发现 label 根本不渲染 ⇒ **差点把一个死注册扩散到更多地方**。
> 经验：**"邻模块这么写"不构成契约；契约要看是否可达。**

### 3.4 i18n 补齐

新增 `models.op_fail`（zh/en 双语 + 自检清单），供 6 个 handler 共用：
`"⚠️ 操作失败：{exc}"` / `"⚠️ Operation failed: {exc}"`。
选它而不是复用 `models.read_fail`（后者文案是"读取模型列表失败"，语义只贴合刷新）。

---

## §4 修复

| 文件 | 改动 |
|---|---|
| `core/model_manager.py` | `_init_discover` 目录探测包 try/except OSError → 告警 + 跳过；`_ensure_init` 把 `_INITED = True` **移到全部初始化成功之后**（并注明重入性：四个初始化函数不回调公开 API） |
| `ui/tab_models.py` | 新增 `_safe_active_model()`（失败回退内置槽 + 告警）；`on_refresh` / `build_models_tab` / `on_download` 三处裸调改用它；`on_verify` / `on_rollback` / `on_reload` / `on_detail` / `on_set_active` 五个 handler 补 try/except → `t("models.op_fail", exc=format_exception_for_ui(exc))` |
| `utils/i18n.py` | 双语新增 `models.op_fail` + 自检清单登记 |

**刻意不做**：不给 Markdown 补 `reg_label`（§3.3）；不改 `_slot_choices` 回退；
不改 `verify_model` / `slot_detail` 内部的 TOCTOU 窗口（已在本阶段登记，见 §7）。

---

## §5 测试

新增 **42 例**（26 + 15，另有 1 例为参数化展开计数）：

### 5.1 `tests/test_audit_selector.py`（27 例）—— 量具的口径守卫

- `code_lines` 剔除规则（注释 / 空行 / 文档串起始行 / 行内 `x = """…"""` 计入）
- `match_refs` 四写法**各一条**用例 + **两条精度**用例（注释提及不算、跨层同名不互相污染）
- 分母口径（整文件用例数，不是 touch 数）
- 自检：0 引用必报错、模块缺失必报错、全绿时通过
- **观察者效应钉子**：量具自身不得计入任何模块；`META_TEST_EXCLUDES` 非空
- 结论编码：审计后 `tab_models` 比值应下降且 `test_ui_audit94.py` 在其命中文件里

### 5.2 `tests/test_ui_audit94.py`（15 例）—— 缺陷守卫 + 边界钉子

- D1 / D1b / D2 / D2b 各一条（含 `gr.Blocks()` 上下文这一 Gradio 用法约束）
- D3 参数化 5 条 + `on_download` 对照组 1 条
- 边界钉子 3 条（Markdown label 不渲染 / Progress 默认值安全 / 硬编码回退保留）
- i18n 完整性 2 条（双语键齐全、界面文案不硬编码——**开发者日志除外**）

> 顺带修正一条**我自己写错的测试**：最初把 `log.warning("UI 下载模型失败：%s")`
> 判为"硬编码中文文案"。本项目日志一贯中文（`core/` 同理），i18n 契约只管
> **用户可见**文案。该断言已收窄（剔除 `log.*(...)` 调用）。

---

## §6 注入式反例（6/6）

前置清场断言「原始状态必须全绿」→ 通过后逐条把修复改坏：

| 注入 | 改坏什么 | 结果 |
|---|---|---|
| I1 | `_INITED = True` 移回初始化之前 | ✅ 捕获（exit=1） |
| I2 | 去掉 `_init_discover` 的 I/O 护栏 | ✅ 捕获（exit=4） |
| I3 | 构建期改回裸调 `get_active_model()` | ✅ 捕获（exit=1） |
| I4 | 去掉 `on_verify` 的 try/except | ✅ 捕获（exit=1） |
| I5 | 清空 `META_TEST_EXCLUDES` | ✅ 捕获（exit=1） |
| I6 | `code_lines` 重新把文档串起始行算作代码 | ✅ 捕获（exit=1） |

**6/6 捕获，三个文件逐字节还原（与注入前记录的外部 sha256 比对）。**

### 6.1 第一次跑只有 4/6 —— 抓到两个**我自己**的问题

这是本阶段最有价值的过程记录：

- **I5 未捕获**：`test_meta_test_files_are_not_returned_by_test_files` 是
  `for meta in META_TEST_EXCLUDES: assert …`。集合被清空后**循环体一次都不执行**，
  断言空转 ⇒ 哑巴守卫。修法 = **先断言集合非空**再遍历。
  （「遍历式守卫必须先钉住集合非空」已登记为经验。）
- **I1 未捕获**：不是测试的问题，而是**我的注入写错了**——
  锚点把 `_INITED = True` 挪到了 `_init_config_slots()` **之后**，
  仍然在抛点之后才置位，**没有复现原缺陷**。改正锚点后立刻捕获。
  ⇒ 经验：**注入未捕获时，先怀疑注入本身，别急着改测试。**

---

## §7 登记不改（本阶段新登记）

1. `gr.Markdown` 的 `label` 语义（§3.3）——若 Gradio 升级后开始渲染，测试会失败并强制重新决策。
2. `ui/tab_settings.py:456` 的 `snapshot` Markdown 同样是「注册了 value、没注册 label」——
   按 §3.3 的结论**无需修改**（label 不渲染）。**登记为同类但不处理**。
3. `core/model_manager.verify_model` 第 429 行 `_os.path.getsize(p)` 存在
   **TOCTOU 窗口**（`exists()` 与 `getsize()` 之间文件消失即抛 OSError）；
   `slot_detail` 的 `_os.stat` 已包 try。本阶段**只登记不改**——它属于
   `core/` 续审范围，且发生的概率极低（文件在两步之间被删）。
4. 选点指标的**固有局限**：比值会因「刚被审计过」而下降，故它衡量的是
   **当前测试密度**，**不能**据此判断「该模块是否还有缺陷」。

---

## §8 回归

| 项 | 结果 |
|---|---|
| 全量测试 | **1277 例 / 86 文件，零失败**（7 例 env-skip） |
| 合成门禁 `quality_gate.py` | EXIT=0，逐项与基线一致（**零回归**） |
| 真实门禁 `real_gate.py` | EXIT=0（含阶段 93 的三条过检面积红线 + 覆盖面横幅） |
| 选点自检 `audit_selector.py --check` | EXIT=0 |
| 注入式反例 | **6/6 捕获 + 逐字节还原** |

---

## §9 下一阶段选点（本阶段产出）

审计完 `tab_models` 后重算，第一荒漠变为：

| 选点比值 | 模块 | 代码行 | 引用用例 |
|---:|---|---:|---:|
| 22.2 | **`ui/tab_auto`** | 709 | 32 |
| 19.5 | `ui/tab_batch` | 643 | 33 |
| 19.0 | `utils/recommend_view` | 152 | 8 |
| 18.9 | `services/queue_worker` | 264 | 14 |
| 17.1 | `core/model_manager` | 495 | 29 |

> 排序由 `python scripts/audit_selector.py` 随时重算，**不再是手算值**。

**剩余缺口（阶段 93 遗留）**：S1 仍只缺真实图 —— `edge_text` / `faint_tiled` /
`grid` / `gradient` 各 3–5 张（或确认「只有角标图」）。代码侧已就绪。
