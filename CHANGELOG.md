# 更新日志 / CHANGELOG

> 本文件按「里程碑」归纳项目演进（而非逐阶段流水账；逐阶段细节见 `docs/`，共 118 份）。
> 版本号遵循 `MAJOR.MINOR.PATCH`；当前版本 `1.0.0`。

## [Unreleased]

### 新增（阶段 116 · 产品化基座）
- 新增 GitHub Actions CI（`.github/workflows/ci.yml`）：落地 `pytest.ini` 早已声明的
  `torch_required` / `network` 离线 CI 约定（此前项目**无任何 CI**）。
- 新增 `LICENSE`（专有许可 + 合规使用条款，与源码头部「All Rights Reserved」一致）。
- 新增机器可读自我质检记分卡 `docs/quality/product_scorecard.json`。
- 新增 `.gitignore` 条目：`.pytest_tmp*` / `*.out` / `pytest*.xml` / `NUL` /
  `config.user_overrides.json`（运行期用户配置，严禁入库）。
- 仓库根目录保洁：把 7 个临时产物（`iso.out` / `search.out` / `pytest4*.*`）移入
  `temp/_root_cleanup_2026-09-25/`（可逆，未删除）。
- 新增 `docs/116-项目自我质检与竞品对标-龙头路线图.md`。

### 修复（阶段 115 · core/superres 真值审计）
- **无权重 esrgan 后端输出噪声**：此前「缺权重」分支保留**随机初始化**卷积网络，
  输出与干净双三次 `mean_abs_diff≈102`、画面均值 138→1.45（近黑），却被标注为
  「双三次放大的真实推理」；而 `postprocess_service` 非 None 即采用 ⇒ 开真·超分即得噪声图。
  改为无权重时**纯双三次上采样**。
- **模型缓存未按 scale 键控**：同一实例先 2× 再 4× 返回旧尺寸；改 `_built_scale` 联合键控。

### 修复（阶段 114 · cli 真值审计）
- `--superres` / `--superres-amount` 此前**只透传图片**，PDF 路径漏传（`process_pdf` 支持）
  ⇒ PDF 上该标志静默失效；已补齐透传。
- `_run_one` 未自保证展示键 ⇒ 打印空文件名（潜伏）；已用 `setdefault` 自保证。

### 修复（阶段 113 · services/video_service 真值审计）
- `extract_sampled_frames(return_indices=False)` 是**空操作**（重建恒等 dict，参数永不生效）；
  改为真正剥离 `idx`。

---

## [1.0.0] —— 核心能力与质量体系成型

### 核心能力
- **多输入源**：图片 / PDF（多页拆分-逐页处理-回填）/ 视频（拆帧-逐帧-拼回，支持掩码复用 + 漂移护栏）。
- **智能检测**：8 类水印预设（general / ai_tiled / ai_signature / ai_corner / ai_text_heavy /
  ai_faint_tiled / ai_grid / ai_gradient / ai_logo / ai_multi）+ 自动预设推荐。
- **修复引擎**：LaMa（TorchScript）+ OpenCV（TELEA/NS）+ 插件引擎；双跑择优（opt-in）。
- **后处理深化**：Poisson 融合 / 纹理重建 / 伪影抑制 / 轻量超分 / 真·超分（ESRGAN / Real-ESRGAN）。
- **五入口**：Gradio 网页界面 / REST API / CLI / 插件系统 / 打包 EXE（PyInstaller）。

### 质量体系（88 起）
- 三条并行门禁：合成 `quality_gate.py` + 真实 `real_gate.py` + 修复侧 `e2e_real_probe.py --gate`；
  掩码标定审计 `mask_param_audit.py`。
- 真值审计体系：`scripts/audit_selector.py`（覆盖度荒漠选点）+ `AUDITED_SINCE` 登记，
  已连续审计 19 个模块（88 → 115）。
- 注入式反例：每处修复均验证「守卫真会失败」。
- 经验库 `LESSONS.md`（125 条）；能力声明 SSOT `config.WATERMARK_TYPE_SCOPE`。

### 工程化
- 分层架构 `ui → services → core → utils → config`（严禁反向依赖，core/services 零 gradio）。
- 107 个测试文件、约 1516 用例；118 份设计/审计文档。
- 依赖三档：`requirements.txt`（完整）/ `-min`（无 torch）/ `-gpu`（CUDA）。
- Dockerfile（CPU，含 GPU recipe）+ PyInstaller `build.spec`。

---

## 合规声明

本工具**仅限**用于去除使用者本人拥有著作权的 AI 生成内容上的可见水印。详见 `LICENSE`。
