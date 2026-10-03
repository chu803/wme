# 阶段 149 · 真实基准真值完整性审计 — 模板化 GT 与 `labeled_by` 伪造

> **一句话结论**：`benchmark_real` 里那 60 条「角标」真值**不是量取出来的，是模板复制出来的**——
> 60 条标注只有 **2 个互不相同的 bbox**，且每个 bbox 在 **30 张完全不同的图**上逐字节一样，
> `labeled_by` 却写着 `manual:measured`。这意味着我们长期引用的「角标 recall ≥0.98」**建立在伪造真值上**，作废。
> 本阶段把「量取完整性」补成一道守卫，真实预检现已正确变红。

---

## 1. 起点（probe）

148 收尾后，P0「真实水印图覆盖」里用户只差「出图」，后半段（ingestion/labeling/scoring）由我接管。
盘点 `benchmark_real/` 时两个反常信号：

- `ground_truth.json` 的 60 条标注 `style` 全是 `corner_logo`；
- `manifest.json` 30 条 `watermark_style_hint` 全是 `mixed`（记忆里已记为「信息量为零」）。

初看像「角标覆盖齐了、只缺另 4 类」。但进一步解析发现比这严重得多。

## 2. 量化（quantify）

| 项 | 实测 |
|---|---|
| GT 条目 | 30（image_id 01~30，与 manifest 对齐） |
| 标注总数 | **60** |
| style 分布 | `corner_logo` ×60（100%） |
| **互不相同的 bbox** | **仅 2 个** |
| bbox `(25,28,49,37)` | 出现 **30 次** |
| bbox `(875,952,140,60)` | 出现 **30 次** |
| `labeled_by` | 全部 `manual:measured` |
| manifest `watermark_style_hint` | 全部 `mixed`（零信息） |

> 30 张内容完全不同的图（山湖日出 / 森林光影 / 沙漠日落…），在同一像素坐标有「量取」出的水印——
> 物理上不可能。这是模板填充的指纹。

## 3. 像素级佐证（可证伪）

取 4 张图，检查标注区域是否真有统一 logo：

| 图 | TL 区域均值 | 全局均值 | 区域内 std | 判读 |
|---|---|---|---|---|
| 01_山湖日出 | (198,189,181) | (165,136,105) | **84.4** | 高方差=满图细节，非平面 logo |
| 02_森林光影 | (164,167,147) | (75,79,49) | **82.4** | 偏绿（ foliage），非统一 logo |
| 15_锦鲤 | (156,161,145) | (86,86,51) | **78.8** | 偏绿 |
| 30_樱花野餐 | (194,180,179) | (181,156,127) | **83.8** | 偏粉 |

真实 AI 角标应是**统一外观**的标记；这里四张图同一角落的颜色天差地别（中性 / 绿 / 粉），
且区域内 std≈80（充满照片内容）。**标注区域不存在统一 logo** ⇒ bbox 是凭空套上去的占位符。

## 4. 分类（classify）：DEFECT（数据完整性），非 by-design

- 既有守卫 E（`test_real_dataset_annotations_are_all_manual`）只断言 `labeled_by` **前缀是 `manual:*`**，
  不查「量取是否真实」⇒ 一个把同一 bbox 复制到 30 张图、却标 `manual:measured` 的伪真值能 100% 通过。
- 这正是 `eval-blindspot-audit` 描述的盲区类型：**门禁全绿但指标建立在不可信真值上**。
- 属于「标签合规但内容伪造」——比「缺标注」更难发现，因为表面全绿。

## 5. 修复（最小改动）

`scripts/real_asset_preflight.py` 新增 `gt_integrity_issues(truth)`，并接入 `audit_truth`（其输出进 `main()` 的 `problems`，决定退出码）：

- **模板化信号**：某 bbox 在 ≥ `MIN_TEMPLATE_FREQ(3)` 张不同图上逐字节相同 ⇒ 报警；
- **信息量过低信号**：去重 bbox 数 / 总标注数 < `DEDUP_RATIO_FLOOR(0.5)` ⇒ 报警；
- **防误报**：两条信号都只在 `total ≥ MIN_SAMPLE(10)` 时触发（小数据集不判）；
- 接入后真实预检**正确地从绿变红**（EXIT=1），不再对伪造真值沉默。

## 6. 注入式反例 / 守卫测试

`tests/test_real_asset_preflight.py` 新增三组：

- `test_gt_integrity_flags_templated_current_real_data`：对**真实** `ground_truth.json` 断言守卫**检测成功**
  （断言出现「模板化」+「信息量过低」字样）——这条用例本身 PASS，证明守卫活着且抓到了现状；
- `test_gt_integrity_passes_distinct_bboxes`：30 张图各带**互不相同的** bbox ⇒ 守卫返回 `[]`（不误报）；
- `test_gt_integrity_small_sample_not_flagged`：5 张图 bbox 全同（< 样本下限）⇒ 守卫返回 `[]`（不误报）。

## 7. 验证

| 门禁 | 结果 |
|---|---|
| `test_real_asset_preflight.py`（全文件） | ✅ **20/20 绿** |
| `test_real_gate.py`（共享 `annotation_scope` 等） | ✅ **37/37 绿**（无回归） |
| 真实数据 `python scripts/real_asset_preflight.py` | ✅ **EXIT=1**，打印 2 条「真值完整性」告警 |

## 8. 影响（重要）

- 我们长期引用的 **「角标 recall ≥0.98」指标作废**——它评的是检测器对一组**模板占位 bbox** 的命中率，
  不反映真实去水印能力。任何「仅代表承诺类型（角标）」的横幅现在连角标自己都站不住。
- P0「真实图覆盖」升级为更严重的表述：**连唯一覆盖的类型（角标）真值都是假的**，
  真实基准当前**实质为零可信真值**。

## 9. 遗留与下一步（需你确认，未删任何文件）

`benchmark_real` 的 30 张图与 `ground_truth.json` 现状：
- 选项 A（推荐）：这 30 张是**无痕自然参考集**，本就不该带水印真值 ⇒ 把 `ground_truth.json` 里
  这 60 条 `corner_logo` 标注挪出/改标（从真值里移除），并停止把它们当「角标基准」引用；
- 选项 B：由你提供**真实带水印 AI 图**（每类 3~5 张，共 4 类：边缘小字 / 网格点阵 / 平铺文字 / 全局渐变）
  走 `inbox/` 流程重做真值（ingestion/labeling/scoring 我接管）。

两类清理/重标都涉及**改动或删除数据集文件**，按既定规则需你确认后才执行——本轮只新增守卫与本文档，**未改动原数据**。
