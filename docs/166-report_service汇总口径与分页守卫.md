# 阶段 166 · `services/report_service` 汇总口径 + 分页守卫

## 动机

覆盖度荒漠榜次席 = `services/report_service`（349 代码 / 28 引用测试 / 比值 12.5）。它把一批处理结果
汇总成**可交付的 HTML/PDF 报告**。阶段 88 曾修过「HTML 与 PDF 各自拼汇总串 ⇒ 同一份数据两种结论」，
但此后**没有机器守卫**；`build_entries` 的过滤/退化、PDF 分页参数也无守卫。

## 量化（守卫即探针，15 例）

**全部 by design 正确**——阶段 88 的统一口径经实测确认成立：

| 轴 | 结论 |
|---|---|
| `_summary_stats` / `_quality_summary` 四态 | empty / **none（一条未评估 ⇒ 显式说明，不渲染成「良好 0 · 告警 0 · 待复核 0」）** / partial（补「未评估 N」）/ full；**各态计数之和 == 总条数** |
| `build_entries` 过滤 | 只收「status=success **且** 结果文件存在」；原图缺失 ⇒ 退化为单图（`img_is_compare=False`）且不评估质量 |
| 质量失败不拖垮条目 | `evaluate_quality` 抛错 ⇒ 该条 `quality=None` 但**条目保留** |
| `include_quality=False` | 完全不调用 `evaluate_quality` |
| `_maybe_downscale` | 保持宽高比（100×200 → 25×50）；`max_side=0` ⇒ 不限 |
| **HTML / PDF 汇总一致** | 同一 entries 生成两端，**总条数与逐项计数在 HTML 与 PDF 中完全相同**（阶段 88 回归护栏） |
| 空报告 | 无成功记录 ⇒ `FileNotFoundError`（不产出空报告） |

## 抓到 1 处

**（P2 · 数量参数缺下界 ⇒ 判据恒真、行为静默退化）** `generate_pdf(records_per_page=...)`：
`per_page <= 0` 时，分页判据 `placed_on_page >= per_page` **恒真** ⇒ 每处理一条就换页，
静默退化成「一图一页」（负值同样）——既非崩溃、也非空转，而是**悄悄换了策略**。

修复：`if per_page < 1: raise ValueError(...)`（与阶段 161 `queue_worker` 的 `retry` 下界校验同族）。

## 守卫

`tests/test_report_service_contract.py`（**15 例**）：四态汇总 + 计数对账 / 过滤与退化 / 质量失败保留 /
关闭评估不调用 / 拼接图 base64 与尺寸 / 下采样宽高比 / **`records_per_page <= 0` 显式拒绝** /
**HTML 与 PDF 汇总一致** / 空报告报错。

## 注入式反例（守卫有牙）

`inject_166.py`（#188 模式：`newline=''` + 原始字节 sha + 行尾感知锚点，本文件 LF）：
移除下界校验 → pytest **exit=1（红，`test_generate_pdf_rejects_nonpositive_records_per_page` 命中）**；
还原**原始字节 sha** `b35421d47c9ace9a…` 一致 → **exit=0（绿）**。

## 回归

新 15 + `test_report_service` + `test_export_compare_view` + `test_legacy_selfcheck` = **108 例全绿**。

## 诚实边界

- **报告类交付物为中文单语**：HTML 固定 `lang="zh-CN"`、汇总/明细标签（共 N 条 / 质量概况 / 名称 / 模式·引擎 /
  未评估质量 / 本报告未包含质量评估…）均为硬编码中文——这是继「全角标点 / 任务级消息 / `utils/exceptions`
  异常文案」之后的**第 4 个 i18n 子类：报告类交付物**。是否本地化属产品决策，本轮**登记不改**。
- `export_report` 对未知 `fmt` **静默回退 HTML**（仅 "pdf" 走 PDF）——登记为边界（UI 只传 html/pdf，当前不可达）。
- `build_entries` 只收「结果文件存在」的记录：被保留策略清理过的历史记录会被**静默略去**，
  `total` 只反映实际可渲染条数（登记为边界：报告是「可交付物」，略去无法内嵌的文件是设计取舍）。

## 产物

`docs/166-report_service汇总口径与分页守卫.md`
