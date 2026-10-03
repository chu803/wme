# 阶段 88 · services 层续审：报告汇总口径分裂与「单一真值来源」

> 审计对象：`services/report_service.py`（526 行）、`services/video_export.py`（183 行）
> 结论：**1 个确认缺陷家族（R1，含 2 个形态）** + **4 项「已验证为非缺陷/边界」**
> 守卫：`tests/test_services_audit88.py`（13 例，含注入式反例）

---

## 0. 一句话结论

同一份报告数据的「质量汇总」被 **HTML 出口**和 **PDF 出口**各拼了一遍。
于是当质量根本没被评估时，HTML 显示 **「良好 0 · 告警 0 · 待复核 0」**（而总条数是 3），
PDF 却显示「本报告未包含质量评估」——**同一份数据，两个互相矛盾的结论**。
修复方式不是「把 HTML 的字符串改成和 PDF 一样」，而是**抽出唯一的段列表来源**，
让任何出口都不可能再与另一侧分叉。

---

## 1. 选点：延续「覆盖度荒漠」指标

阶段 87 用 **模块行数 ÷ 测试引用次数** 定位盲区，首期 2 个模块就抓到 1 个 P0。
本阶段沿同一指标在 `services/` 继续推进（13 个模块只审了 2 个）：

| 模块 | 行数 | 测试引用 | 比值 | 判读 |
|---|---|---|---|---|
| **report_service** | 526 | 3 | **175** | ⚠️ 本阶段主战场 |
| **video_export** | 183 | 3 | **61** | ⚠️ 本阶段复核对象 |
| postprocess_service | 655 | 8 | 82 | 下一候选 |
| auto_pipeline | 575 | 10 | 58 | 下一候选 |

`report_service` 恰好是**一份数据、多个出口**的模块（HTML / PDF / CLI 三条路径都出自它或与它同源），
而这类模块天然容易长出「口径分裂」——**出口越多，越没人去核对出口之间是否一致**。

---

## 2. 缺陷 R1：报告质量汇总的口径分裂

### 2.1 代码位置与成因

`_summary_stats()`（第 198–213 行，修复前）只统计 `quality is not None` 的条目，
返回 `{total, good, warn, review, avg}` —— **没有「已评估/未评估」这两个维度**。
随后两个出口**各自**决定怎么渲染这一组数字：

- `generate_html`：`"共 N 条" + "· 良好 x · 告警 y · 待复核 z"`（仅当 entries 非空）+ `"· 平均分 A"`（仅当 avg 非 None）；
- `generate_pdf`：`if avg is not None: 质量概况… else: "本报告未包含质量评估（可设置中开启 REPORT_INCLUDE_QUALITY）。"`

两个出口用了**不同的判据**（HTML 看 `entries` 是否非空；PDF 看 `avg` 是否为 None），
这正是同一语义多入口的经典病害：**主路径（默认开质量评估）下两边恰好一致，
一旦走进非默认分支，两边立刻分叉。**

### 2.2 两条**真实**来路（不是构造出来的）

| 编号 | 触发条件 | 生产可达性 |
|---|---|---|
| T1 | `config.REPORT_INCLUDE_QUALITY=False` | **config 注释明确推荐**用于「纯交付」报告（不跑质量评估、只出对比图） |
| T2 | 逐条 `evaluate_quality` 抛错 | `build_entries` 里 `except Exception: log.warning("报告质量评估失败，跳过该条质量")` 后 `quality` 留 `None`，**同一批里部分条目失败** |

T2 尤其重要：它产出的是**混合批次**（部分有质量分、部分没有），
而不是「全有 / 全无」——这是单一 `avg is None` 判据**覆盖不到**的场景。

### 2.3 探针量化

探针 `%TEMP%/s88_probe/probe_r1.py` 用确定性构造的三个场景直接渲染 HTML + PDF 并抽取文本：

| 场景 | 构造 | HTML 汇总行（修复前） | PDF 汇总（修复前） | 判定 |
|---|---|---|---|---|
| **A 全部未评估** | 3 条 `quality=None` | `共 3 条 · 良好 0 · 告警 0 · 待复核 0` | `本报告未包含质量评估…` | ❌ 三项之和 0 ≠ 总数 3；两种结论 |
| **B 部分未评估** | 1 条 good + 2 条 `None` | `共 3 条 · 良好 1 · 告警 0 · 待复核 0 · 平均分 92.0` | `质量概况：良好 1 · 告警 0 · 待复核 0` | ❌ **两边都漏计 2 条**，与同文档逐条明细的「未评估质量」自相矛盾 |
| C 全部已评估 | good/warn/review 各 1 | `共 3 条 · 良好 1 · 告警 1 · 待复核 1 · 平均分 67.3` | 同口径 | ✅ 对照组 |

### 2.4 为什么这是缺陷，而不是「显示风格差异」

1. **数字自身不自洽**：场景 A 里三项之和 0，总条数 3。读者按字面理解只能得出
   「3 条全部待复核」——而报告里**根本没有这条信息**（一条都没评估）。
   这会把「没算」误导成「算出来最差」。
2. **同文档自相矛盾**：同一份 PDF 的汇总说「良好 1 · 告警 0 · 待复核 0」，
   而同一文档逐条明细里写着 2 次「未评估质量」。同一份文件里两处说法互斥。
3. **对账失败**：分项计数之和 ≠ 总条数，说明**存在被沉默丢弃的一类数据**——
   这类「失踪的数据」正是最容易让用户误判的地方。
4. **口述路径依赖**：用户拿到的结论取决于他点了「导出 HTML」还是「导出 PDF」，
   这本身就是缺陷。

> 与阶段 87 的关系：阶段 87 修的是「改了 `level` 没同步 `detail`」（**同一对象内的字段配对**），
> 本阶段是它的**跨出口扩展**——改的是「同一语义的多个渲染出口必须共用同一份口径」。
> 两者同属「状态与观测是**一对多**」家族（经验 26 → 经验 59）。

---

## 3. 修复：把「有哪些段」抽成唯一来源

### 3.1 设计原则

**渲染差异可以留在渲染层，但「显示哪些内容」必须只有一个决定点。**

- `_summary_stats` 增加两个维度：`assessed`（已评估条数）与 `unassessed`（`total - assessed`）；
- 新增 `_quality_summary(stats)`：把统计翻译成渲染层可直接消费的**段列表**，
  返回 `{mode, counts, note, avg}`，`mode ∈ {empty, none, partial, full}`；
- HTML 与 PDF **都只负责把段列表画出来**，不再各自判断「有没有质量评估」。

```python
def _quality_summary(stats: dict) -> dict:
    if stats["total"] == 0:
        return {"mode": "empty", "counts": [], "note": "", "avg": None}
    if stats["assessed"] == 0:                       # ← 缺数据 ≠ 零
        return {"mode": "none", "counts": [], "avg": None,
                "note": "本报告未包含质量评估（可在设置中开启 REPORT_INCLUDE_QUALITY）。"}
    counts = [("良好", stats["good"]), ("告警", stats["warn"]), ("待复核", stats["review"])]
    if stats["unassessed"]:                          # ← 保证分项之和 == 总数
        counts.append(("未评估", stats["unassessed"]))
    return {"mode": "partial" if stats["unassessed"] else "full",
            "counts": counts, "note": "", "avg": stats["avg"]}
```

### 3.2 修复后量化

| 场景 | HTML 汇总行 | PDF 汇总 | 一致性 |
|---|---|---|---|
| A | `共 3 条 · 本报告未包含质量评估（可在设置中开启 REPORT_INCLUDE_QUALITY）。` | 同文 | ✅ 逐字一致 |
| B | `共 3 条 · 良好 1 · 告警 0 · 待复核 0 · 未评估 2 · 平均分 92.0` | `质量概况：良好 1 · 告警 0 · 待复核 0 · 未评估 2` | ✅ 段列表一致，分项之和 = 3 |
| C | `共 3 条 · 良好 1 · 告警 1 · 待复核 1 · 平均分 67.3` | 同口径 | ✅ 行为不变（对照组） |

**分组语义落地为「有 段 就画 段」**：HTML 只需把 `counts` 加粗拼起来，
`note` 非空时补说明；PDF 只需把同样的 `counts` 用空格拼起来。
两侧共享的是**决策**，不是字符串模板——所以正文措辞仍可各自优化，不会再次分叉。

---

## 4. 已验证为非缺陷 / 登记为边界

阶段 87 的经验是：**审计的价值不只是「改了多少」，还包括「把多少未经证明的断言
变成有测试背书的结论」**。本阶段复核了 5 项候选，全部判定为「非缺陷」或「边界」，
并逐条写成测试钉住，防止后人以「优化」名义改坏。

| # | 候选 | 结论 | 依据 / 钉住方式 |
|---|---|---|---|
| V1 | `video_export._resolve_out_dir` 静默吞掉 `makedirs` 的 `OSError` | **非缺陷** | 目录创建失败 → 后续 `save_image` 必失败 → 被 `except` 捕获并 `log.warning` 后返回 `None`，**不会伪装成成功** |
| V2 | `export_frame_compare` 仅在形状不同时把结果缩回源尺寸 | **非缺陷** | 结果被真实超分放大时缩回源尺寸，符合「聚焦去水印而非放大」的设计意图；`test_export_frame_compare_resizes_upscaled_result_back` 用 2× 结果视频钉住 |
| V3 | `export_contact_sheet` 依赖 `extract_sampled_frames` 的返回契约 | **非缺陷** | 该函数返回 `list[dict{"idx","image"}]`，`f["image"]` 取值正确（阶段 37 起未变） |
| V4 | `_extract_one` 把越界帧号夹到 `[0, total-1]` | **边界登记** | 若结果视频被 `max_frames` 截断而调用方拿**源视频**帧号去抽结果帧，会得到结果视频的最后一帧（并非同一时刻）。UI 路径的帧号来自结果视频自身的抽样，故不可达；`test_extract_one_clamps_out_of_range_index` 钉住夹取行为 |
| V5 | CLI 汇总行「质量评估：需复核 x / 告警 y / 良好 z」在混有 PDF / 视频时计数小于「成功 N」 | **非缺陷（设计）** | `_print_result` 只对 `kind == "image"` 评估质量，代码注释写明「PDF 是多页、视频是多帧，拿整份文件算 SSIM 没有可比性」。`test_cli_quality_line_scope_is_images_only` 把这个范围钉住——若日后有人给 PDF/视频也算质量，测试会失败并要求重新评估该决定 |

> 同时确认：`ui/` 与 `utils/` 下**没有**第二处质量分级计数的拼装逻辑
> （全仓 grep 展示字面量「良好 / 待复核」只命中 `report_service` 与上述 CLI 一行）。

---

## 5. 注入式反例：证明守卫不是假绿

按阶段 83/87 的强制流程，**临时把 HTML 汇总行回退成修复前的内联写法**，
跑 `tests/test_services_audit88.py`，结果 **4 例失败**：

```
FAILED test_r1a_html_all_unassessed_does_not_look_like_all_review
FAILED test_r1b_partial_renders_unassessed_in_both_html_and_pdf
FAILED test_html_and_pdf_consume_same_quality_summary
FAILED test_old_inline_implementation_would_fail
```

失败信息正是期望的形态（示例）：

```
assert '良好 0 · 告警 0 · 待复核 0' not in '共 3 条 · 良好 0 · 告警 0 · 待复核 0'
```

脚本 `%TEMP%/s88_probe/inject_check.py` 用 `try/finally` 保证**无论成败都还原**，
并在退出前做「与原文件逐字节一致」校验（本次输出 `True`）。

### 5.1 哨兵守卫（本阶段新增手法）

仅断言输出字符串是不够的——新老实现**偶然**产出相同文本时测试会假绿。
因此核心守卫改用**哨兵替换共享函数**：

```python
sentinel = {"mode": "partial", "counts": [("良好", 7), ("未评估", 9)], "note": "", "avg": 42.5}
monkeypatch.setattr(rs, "_quality_summary", lambda stats: sentinel)
# HTML 与 PDF 都必须原样吐出 7 / 9 / 42.5
```

任何一侧若把计数**内联**回渲染层（也就是缺陷的成因），就拿不到哨兵值 → 立刻失败。
这比字符串断言强得多：**它守卫的是「单一真值来源」这个结构本身，而不是某次输出的文本。**

---

## 6. 回归与门禁

报告模块不在检测 / 修复链路上，但依项目铁律「改核心链路后两条门禁都要跑」，
本阶段仍完整执行：

| 门禁 | 结果 | 与阶段 86/87 基线对比 |
|---|---|---|
| 合成 `scripts/quality_gate.py` | ✅ 通过（exit 0） | 误报 **0.78%** · 精确率 **51.8%** · 召回 **81.9%** · IoU **49.7%** · ΔPSNR **+12.49 dB** · 交付链 **+11.31 dB** · 掩码区最差 **−0.01 dB** → **零回归** |
| 真实 `scripts/real_gate.py` | ✅ 通过（exit 0） | general **P 0.0385 / R 1.0000 / IoU 0.7573 / mask IoU 0.0635** → **零回归** |
| 全量 pytest | ✅ 无失败 | 新增 `tests/test_services_audit88.py`（13 例）；用例总数 1119 → **1132** |

> 过程记录：首次全量跑时 pytest 退出码为 1，但进度条**全是 `.`/`s`（无 `F`）**。
> 排查确认退出码来自沙箱的「批量删除确认」拦截——pytest 收尾会清理
> `pytest-of-HP/` 下除最近 3 个之外的历史临时目录，被 safe-delete 策略拦下。
> 改用**独立 `--basetemp`（指向 `%TEMP%` 而非项目内）** 后取得干净汇总。
> 这是一条值得记住的操作细节：**「退出码非 0」不等于「测试失败」，要先看进度条有没有 `F`。**

---

## 7. 新增经验

**经验 59 —— 多出口模块必须共用「决策」，而不是共用「模板」。**
一份数据若有 N 个出口（HTML / PDF / CLI / 日志），每个出口自己判断「该显示什么」，
那么在默认路径上它们会恰好一致，让人误以为没问题；一旦走进非默认分支就立刻分叉。
正确的切法是：**把「有哪些段、每段叫什么、值是几」抽成唯一来源**，
出口只负责把它画出来（加粗 / 换行 / 纯文本随出口自便）。
扫描手法：把展示用的字面量（`良好`、`待复核`）**全仓 grep**，
命中 ≥2 处且不在同一共享函数内 = 候选。

**经验 60 —— 「缺数据」必须与「零」在数据结构上可区分。**
`good=warn=review=0` 同时表示「全都不是 good」和「根本没评估」两种截然不同的事实。
任何统计结构只要可能遇到「未采集」，就必须**显式携带 `assessed / unassessed`（或等价标记）**，
并配一条**对账断言：分项之和 == 总数**。
对账失败本身就是「存在被沉默丢弃的一类数据」的信号——比任何单点断言都便宜有效。

---

## 8. 本阶段产出

| 类型 | 文件 | 说明 |
|---|---|---|
| 修复 | `services/report_service.py` | `_summary_stats` 增加 `assessed/unassessed`；新增 `_quality_summary` 段列表来源；HTML 与 PDF 改为消费段列表 |
| 测试 | `tests/test_services_audit88.py` | 13 例：统计维度、四种 mode、R1a/R1b 回归、对照组、空报告、**哨兵单源守卫**、内嵌旧实现反例、CLI 范围钉住、video_export 三项行为钉住 |
| 探针 | `%TEMP%/s88_probe/probe_r1.py`、`inject_check.py`、`run_all_checks.py` | 项目内零残留 |
| 文档 | 本文件 | — |

## 9. 登记的边界（本阶段不改）

1. **`report_service` 的 `_card_html` 单卡标注不受本阶段影响**：单卡本来就区分
   「质量分 x · level」与「未评估质量」，与汇总口径已对齐。
2. **`video_export` 的 V4 帧号夹取**（见 §4）：不改，理由已登记。
3. **`ui/` 层（6,796 行）仍未审计**：仍是项目最大的审计盲区，
   `services/` 亦仅审了 13 个模块中的 4 个。
