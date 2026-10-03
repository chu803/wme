# 次级档：提示词渲染水印素材（prompt-rendered）

> **阶段 120 新建。** 本目录**不是** `benchmark_real`，两者**口径不同、严禁混用**。

## 1. 这是什么

20 张图（4 类 × 5）：底图由豆包生成，水印由豆包**按提示词渲染**进像素
（见 `docs/117` 的提示词包）。用于**类型覆盖冒烟**——回答"检测器对这四类有没有反应、
掩码面积是否落在既有红线内"，**不**用于出具对外承诺的真实指标。

## 2. 为什么与 `benchmark_real` 分开

| | `benchmark_real`（严格档） | **本档 `benchmark_prompted`** |
|---|---|---|
| 底图 | 用户自己的 AI 生成图 | 豆包生成图 |
| 水印 | **平台原生导出水印**（AI 角标 + AI生成框） | **提示词渲染水印**（模型按指令画出） |
| 口径 | 「真实水印 × 真实底图」 | 「AI 渲染水印 × AI 底图」 |
| 用途 | 承诺面 / 红线 / 对外指标 | **仅类型覆盖冒烟** |
| `committed` | `ai_logo` ✅ | **不参与承诺面**（全部 `committed=False`） |

`benchmark_real/README` 明确要求"真实来源、不得用 AI 重新生成充数"；
本批是**新生成**、水印由生成器渲染（非脚本合成），但仍**不等于平台原生水印**，
故**另立一档**，避免稀释严格口径（`docs/118` §4 方案 B）。

## 3. 结构（与 `benchmark_real` 同 schema）

```
benchmark_prompted/
├── images/                 # 20 张（已 gitignore，不入库）
├── manifest.json           # {id, filename, path, ai_source, watermark_style_hint, original_size, sha256, added_at, source, provenance}
├── ground_truth.json       # {image_id, watermarks:[{bbox, style, note}], labeled_at, labeled_by}
└── results_area.json       # 本次「掩码面积」度量结果 + 对照红线
```

## 4. 已知边界（诚实登记）

1. **`ai_grid` / `ai_faint_tiled` / `ai_gradient` 是「全图型」水印** ⇒ 真值 `bbox` = 整图，
   **bbox 类指标（recall/IoU）对其无意义**（`scripts/real_gate.py:631` 同判）。
   本档对这三类**只用「掩码面积」**度量。
2. **`ai_text_heavy` 的精确 `bbox` 待人工确认**：自动量取（底部亮像素连通域）会把
   水面反光 / 亮书页误当文字 ⇒ 现暂记「底部小字带」近似框，`labeled_by="manual:assistant(approximate)"`。
   **未据此出 recall**。
3. 12 张首批文件**扩展名 `.png` 实为 JPEG 编码**（`grad_06~08` 是首次真 PNG）；
   入库时保留原字节，未做转码。
4. `grad_01~03` **未收编**：那三张是"大号 LOGO"而非渐变（`docs/118` §1）。

## 5. 来源

- 提示词：`docs/117-交图作战包-豆包生成真实水印图提示词.md`
- 验收与度量：`docs/118`、`docs/119`、`docs/120`
- 探测：`core.detector.detect_watermark_mask`（按类型映射 preset）
