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
新真值直接喂入 `real_gate` + `quality_baseline.json`：
- 加固 mask_recall 最薄红线（余量仅 ~24%）；
- 把 is_grid 真实误触发（JPEG 8px 块）、半透明边界从「数据缺口」变「可量化、可收敛」，
  使 296 / 301 能真正收口而非挂着。
