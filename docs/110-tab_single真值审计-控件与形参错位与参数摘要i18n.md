# 阶段 110 · `ui/tab_single` 真值审计：控件与形参错位 —— 「双跑择优」失效 + 默认路径丢历史

> **选点由脚本给**：`audit_selector.py --unaudited` 榜首 **`ui/tab_single` ratio 13.7**
> （437 代码行 / 32 引用用例 / 3 文件）。审计后该模块进入 `AUDITED_SINCE`（阶段 110）。

---

## 1. 结论先行

| 编号 | 判定 | 内容 | 处置 |
|---|---|---|---|
| **F1** | **真缺陷（P0，两个功能同时错位）** | `run_btn.click(inputs=[…21 项…])` 而 `process_single` 有 **22 个形参**，且内部开关 `record` 排在最后一个 UI 控件 `dual_run` **之前** ⇒ Gradio **按位置**传参 ⇒ **「双跑择优」复选框绑到 `record`**、`dual_run` 恒吃默认值 | **已修**（形参重排 + 契约常量）+ 全 ui 层通解守卫 |
| **F2** | **真缺陷（同族，第二处）** | `ui/tab_auto.process_auto_repair`（25 形参 / 24 控件）**同一处错位**——由「全 ui 层接线扫描」一并发现 | **已修** |
| **F3** | **真缺陷（i18n 泄漏，四处同语义不同源）** | 「修复参数摘要」在 `tab_single` / `tab_batch`(×2) / `tab_auto` / `tab_history` **各写一份且硬编码中文**，而该文案会被 `report_service` 渲染进 HTML/PDF（`参数：…`）⇒ 切到 en-US 报告里仍是中文；且**改一处漏三处**（我第一轮只改了 1 处，通解守卫又抓出 3 处） | **已修**（抽 `ui.components.repair_params_text*` 两个构造器 + 2 个 i18n 键）+ AST 通解守卫 |
| **F4** | 看似缺陷、实为正确（**钉住**） | ① `on_enhance` 无 try/except，但三种恶意输入全部**优雅降级**；② `scores`…（不适用）| 写成不变量测试 |
| **F5** | 声明≠实现（文档） | `process_single` docstring 写"**四**元组 (orig_view, result_view, status_text, download_file)"，实际返回**五**元组（含 `compare_view`，与 `outputs` 五项对应） | **已改正** + 「所有分支都是 5 元组」的对账守卫 |

---

## 2. F1 归因（P0）

### 2.1 取证（`%TEMP%/s110_probe.py` / `s110_add.py`）

| # | 控件（inputs） | 绑到的形参 | 说明 |
|---|---|---|---|
| 1–20 | `editor`…`pp_real_backend` | `editor_value`…`real_backend` | 语义一一对应（仅形参命名不同），**前 20 项没错** |
| **21** | **`dual_run`** | **`record`** | ⚠ **错位点**；形参 `dual_run` **无人绑定** |

行为实测（按位置传 21 个实参，末位模拟复选框）：

| 复选框 | `inpaint(dual_run=…)` | `_CACHE.add_success` 次数 | 判定 |
|---|---|---|---|
| **False（界面默认）** | False | **0** | **默认路径根本不登记处理历史** |
| True | False | 1 | 勾选后历史回来了，但双跑依然不开 |

⇒ **两个功能被整体错位**：
1. **「双跑择优」在界面上完全无效**（`dual_run` 恒 False）；
2. **默认路径丢失历史记录**（`record` 取了复选框的默认 False）。

### 2.2 为什么能长期存活

- docstring 只写"四元组"，编辑接线时没有长度契约可依；
- 21 个控件与 22 个形参**只差 1**，肉眼扫过去"看起来是对齐的"；
- 两个功能都**不报错**：一个静默不生效、一个静默不记录——正是本项目反复出现的「静默缺陷」形态
  （经验 85/87/90/102 同族）。

### 2.3 修法

形参重排：`dual_run`（UI 控件）在前、`record`（内部开关）在后；
并新增模块级契约常量 `_SINGLE_WIDGET_PARAMS`（控件形参前缀），供测试逐位对账。

---

## 3. 全 ui 层接线扫描（把个案推广成契约）

`%TEMP%/s110_wiring.py` 扫全部 `ui/*.py` 的 `fn=<回调>` ↔ `inputs=[...]`：

| 文件 | 回调 | 形参 | inputs | 差 | 尾部未绑定 | 判定 |
|---|---|---|---|---|---|---|
| `tab_single.py` | `process_single` | 22 | 21 | +1 | `record` | ⚠ **真错位**（F1） |
| `tab_auto.py` | `process_auto_repair` | 25 | 24 | +1 | `dual_run` | ⚠ **真错位**（F2） |
| `tab_batch.py` | `run_batch` | 28 | 27 | +1 | `progress` | ✅ 合法（Gradio 注入） |
| `tab_media.py` | `run_pdf` / `run_video` | 13/17 | 12/16 | +1 | `progress` | ✅ 合法 |
| `tab_models.py` | `on_download` | 2 | 1 | +1 | `progress` | ✅ 合法 |
| 其余 39 处 | — | — | — | 0 | — | ✅ |

**关键区分**：`progress` 是 Gradio **注入**的进度对象（不在 inputs 里），
`record` 是**内部开关却被排在了控件之前** ⇒ 只有后两处是真缺陷。

⇒ 契约（写进测试）：
`len(inputs) == len(形参) − len(内部尾部形参)`，且**内部尾部必须是形参表的后缀**；
全部例外**显式登记** `_INTERNAL_TAIL`（不做名字猜测，技能 5b）。

---

## 4. F3 归因（i18n 泄漏 × 四处同语义不同源）

**出口链**：`ui/*` 构造 `params` → `_CACHE.add_success(params=…)` / `ReportSource(params=…)`
→ `services/report_service` 渲染进 **HTML**（`参数：{params}`）与 **PDF**（`参数：{…[:80]}`）。

**四处硬编码**（`%TEMP%/s110_wiring.py` 的 AST 通解守卫 + 人工复核）：

| 位置 | 原文 | 差异 |
|---|---|---|
| `tab_single.py` | `f"引擎={engine} 力度=… 阈值={threshold}"` | 6 参数 |
| `tab_auto.py` | 同上 | 6 参数 |
| `tab_batch.py`（两处） | 6 参数版 / **3 参数版** | **同一页面两种格式** |
| `tab_history.py` | `f"引擎=… 降噪={denoise}（重新处理）"` | 3 参数 + 来源标注 |

⇒ 缺陷有两层：**① 硬编码中文**（切 en-US 后报告仍中文）；
**② 同一语义四处各自拼装**（改一处漏三处——我第一轮确实只改了 1 处，
是 AST 通解守卫把剩下 3 处抓出来的）。

**修法**：抽到 `ui/components.py` 的**唯一真值来源**（该文件的存在理由正是
"避免各写一份、日后改一处要同步四处"）：

```python
repair_params_text(engine, strength, dilate, feather, threshold, denoise)   # 6 参数
repair_params_text_short(engine, strength, denoise)                         # 3 参数（重新处理）
```

**为什么不合并成一个函数**：`tab_history.reprocess(rec_id, engine, strength, denoise)`
的入口**只有 3 个参数在作用域内**（膨胀/平滑/阈值不在该页控件里）⇒ 强行复用 6 参数模板
只能补 `None`，摘要会出现 "None"。**判据是参数可得性，不是"看起来像同一种文案"**
（技能第四步：单真值来源要按**语义**取，不能按字面合并）。

i18n 键：`params.hist_summary` / `params.hist_summary_short`（zh-CN / en-US 双表均补齐；
`tests/test_i18n_coverage.py` 通过）。

---

## 5. 验证

| 检查 | 结果 |
|---|---|
| **全 ui 层接线契约** | 扫描 45 处接线；例外全部显式登记；`_INTERNAL_TAIL` 不得有陈旧条目 |
| P0 直接守卫 | `dual_run` 必须是这两个回调的**最后一个控件形参**；`record` 必须是**后缀** |
| 行为 | 复选框值 True/False 都**原样到达** `inpaint(dual_run=…)`；默认路径**必须登记历史**（1 次） |
| 反例自检 | **内嵌「修复前形参顺序」副本**，断言它确实把 `dual_run` 丢掉（守卫不是哑巴，技能 5b） |
| i18n | zh 含中文 / en **不含中文**，且参数已代入；四个标签页都必须调用共享构造器 |
| 通解守卫 | AST 扫 ui 层常量，禁止再出现 `引擎=`/`力度=`/`降噪=` 等摘要字面量（对**尚未写下的**第 N 处同样有效） |
| 契约对账 | `process_single` **所有** `return` 都是 5 元组；docstring 声明"五元组" |
| 护栏不变量 | `on_enhance` 三种恶意输入必须优雅降级 |
| 受影响面定向跑 | **181 例全绿**（ui 接线 + i18n + i18n 覆盖率 + export/history + ui_audit91/94/96/97 + batch/media 推荐 + recommend_view） |
| 新增守卫 | `tests/test_ui_wiring_contract110.py` **14 例** |
| **全量跑抓到的回归（已修）** | `tests/test_audit_selector.py::test_unaudited_filters_registered_modules` 因我在 109/110 把 `core/preset_recommender`/`ui/tab_single` 注册进 `AUDITED_SINCE` 而写死样本失效 ⇒ 改为**动态取真实未审模块**做幸存者（不再与具体模块名耦合）。复跑该文件 + 接线守卫 **44 例全绿** |
| 全量跑余下红灯（环境 artifact，与本轮无关） | 仅 `tests/test_utils_audit90.py` 2 例（日志轮转），签名 = 沙箱单轮批量删除护栏 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`（BULK_GUARD_HITS=2，正好对应 2 例）；阶段 108/109 同形态，非本轮改动所致 |
| 探针 | `%TEMP%/s110_{probe,add,wiring}.py`（项目内零残留） |

---

## 6. 诚实边界与登记

1. **`tab_batch.py` 的第二处（3 参数版）已统一为 6 参数版** —— 属**内容变更**
   （报告里的该行会多出「膨胀/平滑/阈值」）。理由：同一页面同一语义出现两种格式本身就是缺陷；
   该字段是**展示串**，无解析依赖（`report_service` 只做 `escape`/截断）。
2. **`progress` 的四处"数量差"是合法的**，已显式登记（不做名字猜测；若哪天 Gradio 改变注入
   方式，测试里的登记会变成陈旧条目并失败）。
3. **UI 手工验收仍未做**：本阶段用「AST 接线契约 + 行为探针（monkeypatch 依赖）」证明修复，
   **没有**在真实浏览器里点过那两个控件（无头环境）。登记为边界：若你要眼见为实，
   跑 `python main.py` 勾选/取消「双跑择优」各处理一张图，看日志里是否出现双跑记录。
4. 门禁：`scripts/` 与 `ui/` 无交集（三条门禁显式传 preset、不经过任何标签页）⇒ 无需重跑。
