"""
方向 A · 真实 AI 图门禁数据集 · 目录约定
=========================================

为什么有这个目录
----------------
项目 80 个阶段的所有量化指标（误报 0.79%、精确率 51.3%、召回 82.3%、ΔPSNR +12.60 dB）
全部基于 640² 合成图。真实 AI 平台（Midjourney / Stable Diffusion / DALL-E / Adobe
Firefly / v0 / Lepton 等）输出图的水印风格（弧形、装饰、半透明叠加、凹凸纹理）
**合成图未覆盖**。本目录是真实图真值数据集的入口。

目录结构
--------
    benchmark_real/
    ├── README.md               # 本文件（说明文档）
    ├── manifest.json           # 已采集的图清单（入库）
    ├── ground_truth.json       # 真值标注（入库）
    ├── images/                 # 原始图（gitignored，体积大）
    │   └── <id>.<ext>
    └── results/                # 门禁输出（gitignored，可重新生成）

manifest.json / ground_truth.json 均入库以便复现；
images/ 与 results/ 不入库（前者是用户素材，后者可由脚本重生成）。

工作流
------
1. 把真实 AI 图放到 images/ 下（任意文件名）；用 `scripts/collect_real_assets.py scan`
   自动登记到 manifest.json（填写元数据：ai_source / style / original_size）。
2. 用 `scripts/build_real_ground_truth.py auto` 让检测器预填每个 watermark 的 bbox；
   用户用任何文本编辑器打开 ground_truth.json 微调（增删、修正、改 style）。
3. `scripts/run_real_benchmark.py` 跑门禁：每个预设 × 每张真值图 → recall/precision/IoU/FP，
   写入 results/benchmark_<timestamp>.json + 在 stdout 输出 Markdown 表格。
4. `scripts/real_benchmark_decision.py` 做引擎对决：每张图同时跑 OpenCV + LaMa，
   比较 PSNR（掩码区）/ NIQE；输出建议（双跑择优默认是否开启、推荐器是否调整）。

设计约束
--------
- **不依赖网络**：采集以本地 import 为主；公网下载作为可选 stub（依赖 urllib）。
- **不入库大图**：images/ 与 results/ 走 gitignore；真值/清单入库以便复现。
- **真值 JSON 可手工编辑**：bbox 用像素 [x, y, w, h]，风格枚举见 scripts/build_real_ground_truth.py。
- **可重入**：所有脚本允许重复运行而不破坏已有数据（manifest 与 ground_truth 是合并语义）。

风格枚举（与 core.detector.KIND_* 对齐）
----------------------------------------
  corner_logo      — 角标文字 / 平台签名（Midjourney 风）
  edge_text         — 边缘小字（多见于 SD WebUI 截图）
  grid_watermark   — 网格点阵（多见于 DALL-E）
  logo_watermark   — 实体 LOGO 轮廓
  gradient_watermark — 全局渐变叠加
  translucent      — 半透明区域水印
  overlay          — 整图半透明水印（Firefly 风）
  mixed            — 多种风格并存
  unknown          — 其他 / 暂不分类

ai_source 枚举
--------------
  midjourney / stable_diffusion / dalle / firefly / lepton / v0 / other

## 豆包出图采集清单（真实真值扩容 · P1 · 2026-10-18）

现状缺口：`ground_truth.json` 57 条 **全部是 corner_logo**，其余 KIND 零覆盖。
这正是 is_grid 真实误触发（docs/301）、半透明通路(296) 无法收敛的根因——
对「非角标」类水印**没有真实真值可量化**。本清单用于补齐各类风格真值。

### 1. 出图（你在豆包侧做）
对下面每种风格生成 **3–5 张**（首轮试点 ~20–30 张即可），要求：
- 基底内容多样化（风景 / 人像 / 产品 / 插画 / 抽象），别只用同一类；
- 分辨率尽量高（>=1024），水印本身清晰可辨；
- 文件名带风格前缀便于自查：`edge_text_01.png` / `grid_02.png` / `logo_01.png`
  / `gradient_01.png` / `translucent_01.png` / `overlay_01.png` / `mixed_01.png`
  （corner_logo 已有 57 张，不必再补，除非想增基底多样性）。

豆包提示词模板（{基底} 按风格替换）：
- edge_text：生成一张{写实风景/赛博城市夜景}，画面**底部边缘带一行小字版权水印**（如「AI生成 / 仅供展示」），字体小但要清晰可读
- grid_watermark：生成一张{插画/概念图}，叠加**细密点阵网格水印**（半透明、铺满全图，类似 DALL·E 风格）
- logo_watermark：生成一张{产品海报/封面}，中央或角落带一个**实体 LOGO 轮廓水印**（半透明）
- gradient_watermark：生成一张{抽象艺术图}，叠加**一层半透明全局渐变水印**（从一角到对角）
- translucent：生成一张{人像/风景}，整体叠加**半透明文字水印**（透明度约 25%–40%，覆盖画面大面积）
- overlay：生成一张{插画}，**整图叠加半透明水印条/水印图案**（Firefly 风格，轻量半透明）
- mixed：生成一张图，同时含**角标 + 边缘小字 + 半透明叠加**三种水印

### 2. 投递
把生成的图放进 `benchmark_real/images/`（任意文件名，前缀仅便于自查）。

### 3. 标注 + 评分（白露接管）
- 登记清单：`python scripts/collect_real_assets.py scan`
- 检测器预填：`python scripts/build_real_ground_truth.py auto`（仅供参考，多半只覆盖角标）
- 白露逐张 Read 图像，手工标注 `watermarks[].bbox[x,y,w,h] + style(kind) + note`，
  覆盖全部水印（含非角标），写回 `ground_truth.json`，`labeled_by` 置 `manual:visual`
- 结构校验：`python scripts/build_real_ground_truth.py validate`
- 逐类门禁：`python scripts/run_real_benchmark.py --presets general,ai_tiled,ai_signature,ai_corner,ai_text_heavy,ai_faint_tiled,ai_grid,ai_gradient,ai_logo,ai_multi`

### 4. 用途
新真值的目标是喂入 `real_gate` + `quality_baseline.json`：
- 加固 mask_recall 最薄红线（余量仅 ~24%）；
- 把 is_grid 真实误触发（JPEG 8px 块）、半透明边界从「数据缺口」变「可量化、可收敛」，
  使 296 / 301 能真正收口而非挂着。
- ⚠ 首轮实测发现「全图类不可直接并入」，见 §5。

### 5. 首轮入库结果与 bbox 局限（2026-10-18 实测）
- 已入库 21 张（7 类 × 3 版），真值落 `ground_truth_p1.json`（`labeled_by="manual:visual"`，
  21 条 / 26 个标注），**未并入主 `ground_truth.json`**（原因见下）。
- per-kind（general 预设，区域级 micro）：

  | 水印类 | 召回 | 精确率 |
  |---|---|---|
  | corner_logo | 48/55 = 0.87 | 5/7 = 0.71 |
  | edge_text | 3/5 = 0.60 | 1/49 = 0.02 |
  | gradient_watermark | 1/4 = 0.25 | — |
  | grid_watermark | 0/4 = 0.00 | — |
  | logo_watermark | 0/4 = 0.00 | — |
  | overlay | 0/3 = 0.00 | — |
  | translucent | 0/4 = 0.00 | 5/538 = 0.01 |

- **关键局限（方法论）**：benchmark 的 bbox-IoU（≥0.3）匹配是为**局部水印**（角标/边缘/LOGO/水印条）
  设计的；grid/gradient/translucent 是**全图覆盖**，用全图 bbox 作真值时任何局部预测的 IoU 都 < 0.3
  ⇒ 召回机械性为 0。
- **实测并轨影响**：把 21 条并入主 general 聚合 → mask_recall **0.5960 → 0.0376**（< 红线 0.45）、
  mask_iou 0.3217 → 0.2429（< 0.35）；recall 0.9057 → 0.6582、precision 0.0801 → 0.0615。
  ⇒ **不可直接并入**（会打穿已标定红线，属未经授权的红线松动）。
- 因此这批数据当前是**独立诊断集**，用于回答「检测器对各类是否开火 / 火在哪」；
  不作为承诺面红线基线。要让全图类可量化，需换口径（掩码级 / 覆盖率 / 全图召回）或
  修检测器使全图平铺水印输出覆盖整幅的掩码——属下一阶段的显式决策。
- 复现：`ground_truth_p1.json` + `manifest.json`（21 张 P1 图已入库 `images/`，与既有 88 张 tracked 惯例一致）。

### 6. 检测器修复：全幅平铺 → 全帧区域（2026-10-18，用户选「改检测器」）
- **实现**（`core/detector.py::_consolidate_fullframe` + config 四个杠杆）：
  同类区域（`grid_watermark` / `translucent`）已铺满全幅时（联合包围盒宽高**均** ≥
  `DETECT_FULLFRAME_SPREAD_RATIO` 且数量 ≥ `MIN_REGIONS`），追加一个 bbox=(0,0,W,H)、
  `full_frame=True` 的**全帧 region**。
- **关键取舍**：**只扩 region、不填 mask** —— 门禁的区域级匹配因此能认作「铺满全幅」，
  掩码仍只含真实水印像素，产品侧不会整图重绘毁图。需要 mask 也铺满时置
  `DETECT_FULLFRAME_FILL_MASK=True`（供 mask_recall 实验，代价是整图重绘）。
- **按预设限定**：仅 `ai_grid` / `ai_tiled` / `ai_faint_tiled` 开启；`general` 恒关（防误伤普通照片）。
- **A/B 实测**（21 张真值图，同一组检测参数，仅切 fullframe 开关）：
  | 水印类 | OFF | ON |
  |---|---|---|
  | grid_watermark | 0/4 = 0.00 | **3/4 = 0.75** |
  | translucent | 0/4 = 0.00 | **4/4 = 1.00** |
  | overlay | 0/3 = 0.00 | 1/3 = 0.33 |
  | gradient | 0/4 = 0.00 | 1/4 = 0.25 |
  （overlay/gradient 的部分匹配是**跨类全幅几何匹配**：全帧区域与任何全图真值 IoU=1.0，
  与 kind 无关——属已知口径 caveat，非本修复缺陷。）
- **零回归**：`general` 在 53 张真值图上逐位不变
  （0.5960 / 0.9057 / 0.3217 / 0.0129 / 0.6638 / 0.0801）；既有 90 例探测器/预设守卫全绿。

### 7. LOGO 缺口的决定性归因（2026-10-18，结论：**不可靠调参修复**）
P1 的 4 处 `logo_watermark` 真值（logo_A/B/C + mixed_b）为 0/4。逐路径探针（Canny→轮廓→
solidity/面积/宽高比）给出三个互不相同的真因：

1. **logo_A（圆环，占画面 33.3%）**：轮廓 `solidity=0.967`、`aspect=1.04`，判据全过，但
   **面积超过 `DETECT_LOGO_MAX_AREA_RATIO=0.15`**；且即便把上限抬到 0.50，其 34.2% 掩码会被
   **全局覆盖率闸（15%）**裁掉 ⇒ 区域仍为 0。该闸是「宁可漏检、不可滥杀」的**产品安全策略**，
   不是缺陷；放宽需显式授权。
2. **logo_C（六边形，占画面 44.6%）**：Canny 把细线打碎成 4957 条轮廓，最大轮廓
   `solidity=0.652 < 0.85` ⇒ 被实心率判据拒绝（且同样受 15% 闸约束）。
3. **logo_B（右下小方框，1.1%）**：细线低对比，Canny(50,150) 抓不到真值轮廓；检测器只在上方
   找到 63×50 的小伪区域（不在真值框内）⇒ 仍算漏检。

**反证（四组，均为实测）**：
- 网格扫描（solidity 0.35–0.85 × max_area 0.15–0.50）：降 solidity 救不了 B/C（恒 0/1），
  却让干净图误报爆炸（抽象流体 50–100%、咖啡杯 37–136%）⇒ 降 solidity 不可行。
- 断口桥接（膨胀 1–3 次 × 闭运算 5/7）：B/C 仍 0/1 ⇒ 不是「断口」而是**低对比**。
- 空洞比（`RETR_CCOMP`）：`hole_ratio` 不能区分「空心水印」与「实心角标」——既有 ai_logo_*
  里也大量出现 hole_ratio 0.91 的候选（字母封闭区）⇒ 不可用作放行判据。
- 抬 max_area 直通总入口：logo_A 区域仍为 0（被覆盖率闸裁剪）⇒ **调参修复被闸抵消**。

**决策**：**不提交「抬 max_area」的假修复**。若继续攻 LOGO，需二选一：
- **(a)** 显式放宽 15% 安全闸（需授权，风险 = 大图重绘毁图）；
- **(b)** 新增**低对比细线 LOGO 通路**（专用低阈 Canny + 直线/几何拟合 + 严格 FP 门禁），
  作为独立特性立项。
