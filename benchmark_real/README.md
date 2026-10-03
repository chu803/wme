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