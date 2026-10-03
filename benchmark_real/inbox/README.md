# 真实图收件箱（交图口）＋ 操作手册

> 📋 **「要出什么图、提示词怎么给？」** → 见 `docs/P0-真实水印图采集清单与提示词.md`
> （含可直接粘进豆包/即梦的 10 条提示词 + 分辨率/格式/**保留角落水印**的硬性要求）。

把要补的**真实 AI 图**（你自己生成并下载、或平台导出的原图）拖进这个目录，
或直接给我任意路径。下面是从「交图」到「门禁转绿」的**实测可用**流程
（端到端已在隔离临时目录跑通：红→绿，`exit 1 → exit 0`）。

---

## 当前基准状态（阶段 150 之后）

| 项 | 值 |
|---|---|
| `benchmark_real/images/` | 30 张**无痕自然参考图**（不携带任何水印标注） |
| `manifest.json` | 30 条（hint 全部 `mixed`） |
| `ground_truth.json` | `entries: []`（**0 条标注**） |
| `real_asset_preflight.py` | **exit 1**，`committed_missing=['ai_logo']`（诚实红灯） |
| 承诺类型 | 仅 `ai_logo`（truth_styles = `corner_logo` + `logo_watermark`） |

> ⚠ **阶段 149/150 背景**：原 60 条 `corner_logo` 标注经核查是**模板复制**（仅 2 个互不相同的
> bbox，且逐字节重复在 30 张不同图上），像素佐证标注区并无统一 logo。阶段 150 已将其移除，
> 30 张图作为无痕参考集保留。`ai_logo` 仍承诺处理，但**当前 0 条真实真值** → 门禁正确报红灯。

**这就是唯一未消的 P0**：把 `ai_logo` 从「承诺但无证据」补成「承诺且有真实证据」。

---

## 收编一条真实图的实测步骤（以关闭 ai_logo 红灯为例）

### 1. 放图
把一张带角标 LOGO 的真实 AI 原图丢进 `benchmark_real/inbox/`（或任意路径）。
命名避开 `01_…~30_…`，建议带类名前缀，如 `logo_01_右下角.jpg`。

### 2. 登记进清单
```bash
python scripts/collect_real_assets.py add <path> --style unknown --copy
```
- `--copy` 会把图复制进 `benchmark_real/images/` 并写入 `manifest.json`。
- `--style unknown`：先以 catch-all 登记，**避免在标注前触发「hint↔真值矛盾」守卫**
  （如果你确定风格且会在同一步标注，也可直接写 `--style corner_logo`）。
- 记下打印出的 `image_id`（如 `logo_01_右下角`）。

### 3. 人工标注真值（关键：必须 `manual:*`）
编辑 `benchmark_real/ground_truth.json`，在 `entries` 里加一条：
```json
{
  "image_id": "<第 2 步拿到的 id>",
  "watermarks": [
    { "bbox": [x, y, w, h], "style": "corner_logo", "note": "右下角平台 LOGO" }
  ],
  "labeled_at": "2026-09-27T00:00:00Z",
  "labeled_by": "manual:measured"
}
```
要点：
- `bbox` = `[x, y, w, h]`，**4 个整数、w/h > 0、在图内**。
- `style` 必须是 `STYLE_ENUM` 之一（角标类用 `corner_logo` 或 `logo_watermark`）。
- `labeled_by` **必须是 `manual:*`**（如 `manual:measured`）。**绝不可用 `auto:*`**——
  `build_real_ground_truth.py auto` 能用检测器预填 bbox 候选，但那是检测器自己的输出
  （循环论证），写入的是 `auto:<preset>`，门禁会**硬失败**并拒绝。预填后请逐张核 bbox
  并改成 `manual:measured` 才算数。
- 想批量看覆盖情况：`python scripts/build_real_ground_truth.py list`；
  想结构校验：`python scripts/build_real_ground_truth.py validate`。

### 4. 跑预检 → 转绿
```bash
python scripts/real_asset_preflight.py
```
- **加第 1 条 `manual:measured` 角标标注起**，`committed_missing` 即变为 `[]`，
  `complete=True`，**exit 0 ✅**。
- 报告仍会提示「真值图数 < 下限 3」——这是**软建议**（推荐补到 3~5 张再宣布该类「已充分验证」），
  不是硬门禁；`real_gate` 只卡 `committed_missing`，≥1 张即绿。

### 5.（可选）刷新真实指标 / 宣布充分验证
当补到 ≥3 张且全为 `manual:*` 时：
```bash
python scripts/real_gate.py --update            # 刷新基线（会续跑红线/漂移判定）
python scripts/real_gate.py --strict-scope --presets general,ai_logo   # 取真实指标
```
随后同步 README「支持的水印类型」验证状态列与 `tests/test_watermark_scope95.py`
的承诺面守卫（它们故意钉死，逼你同进同退）。

---

## 收件箱机制说明

- 本目录是**暂存区**，不是数据集。收编后 `images/` 才是唯一数据源，本目录可随时清空
  （我不会自动删你的文件）。
- `real_asset_preflight.py` 会**扫描 `inbox/` 并查重**（与已收编 30 张比对 sha256、及内部自重复）。
  只丢图不登记 → 预检列出「待收编 N 个」；重复图 → 明确报错。
- 一次性批量收编 `images/` 下新文件：`python scripts/collect_real_assets.py scan --style unknown`。

---

## 收什么 / 不收什么

**收（每类建议 3~5 张，优先级从高到低）：**

| 目标类型 | 典型形态 |
|---|---|
| `ai_logo`（角标 LOGO） | 图角/边缘的平台签名、水印 logo（**当前承诺类，最优先补**） |
| `ai_text_heavy`（边缘小字） | 图底部/边缘说明文字、`AI生成` 字样 |
| `ai_grid`（网格点阵） | 全图规律排布的点阵 / 网格 logo |
| `ai_faint_tiled`（平铺文字） | 全图极淡、重复出现的文字/logo |
| `ai_gradient`（全局渐变） | 整图叠加的平滑渐变 / 署名层 |

**硬性要求**
- 格式 `.jpg` / `.png`（`.webp` 也可，收编时转存）；长边 **≥ 640**。
- **必须真实来源**：你自己生成并下载的原图，或平台导出的原图。
  ⚠ **不得用 AI 重新生成 / 放大的图充数**——那会让「真实水印」退化成合成水印。
- 命名避开 `01_…~30_…`（与现有 30 张冲突）。
- 能拿到「干净版」（去水印前的原图）最好：`bbox` 可从差值自动导出，比目视更准。

**不收**
- 非水印的普通图、截图、表情包；
- 二次压缩到水印不可辨的图（检测极限之下无可评之物）；
- 同一水印在多个平台反复转存的版本（算同一张，按 `sha256` 去重）。

---

## 相关文档

- 阶段 149：真实基准真值完整性审计（模板化 GT 与 `labeled_by` 伪造）→ `docs/149-*`
- 阶段 150：Option A 无痕参考集、移除伪造 GT → `docs/150-OptionA-无痕参考集移除伪造GT.md`
- 交图规格与承诺类型升级预检总说明 → `docs/108-真实图交图规格与承诺类型升级预检.md`
- 升级清单（逐项都有守卫）→ `scripts/real_asset_preflight.py` 的 `UPGRADE_CHECKLIST`
