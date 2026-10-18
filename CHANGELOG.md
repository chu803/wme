# 更新日志 / CHANGELOG

> 本文件按「里程碑」归纳项目演进（而非逐阶段流水账；逐阶段细节见 `docs/`，共 118 份）。
> 版本号遵循 `MAJOR.MINOR.PATCH`；当前版本 `1.0.2`。

## [1.0.2] - 2026-10-18

> 1.0.1 已于同日上架 PyPI；本版为**元数据与发布口径**收口，代码逻辑与 1.0.1 一致（无功能变更）。

### 打包 / 元数据（PyPI 包页可发现性）
- `pyproject.toml` 新增 `[project.urls]`（Homepage / Repository / Documentation / Changelog → `github.com/chu803/wme`）。
- 分类器 7 → 13 条：补 `Development Status :: 5 - Production/Stable`、`Intended Audience :: End Users/Desktop`、`Intended Audience :: Developers`、`License :: Other/Proprietary License`、`Natural Language :: Chinese (Simplified)`、`Topic :: Utilities`。
- 版本号同步升至 `1.0.2`（`config.APP_VERSION` 真值源 + `pyproject.toml` + `main.py` + `core/services/ui/utils/tests` 的 `__version__`），`tests/test_packaging_meta.py` 断言一致。

### 发布管线
- 尝试迁移 PyPI Trusted Publisher（OIDC）后因账号网页登录受阻回退，**沿用已验证的 `PYPI_API_TOKEN`**（Publish #3/#5 同款）；OIDC 待可正常登录 PyPI 后再切。

## [1.0.1] - 2026-10-18

> PyPI `1.0.0` 已于 2026-10-15 发布（Publish #3 全绿）且 **PyPI 不允许覆盖同版本**，故 1.0.0 之后的
> 修复与性能收口统一走本版，不再重发 1.0.0。
> 溯源（诚实边界）：PyPI 上的 `1.0.0` 产物由提交 `06b3d13` 构建，而 tag `v1.0.0` 指向 `0be2209`
> （其后的 25 个提交，含本版全部修复）——**两者不重合**；需复现 PyPI 1.0.0 请 `git checkout 06b3d13`。
> 不把 tag 回指、也不删远端 tag：回指会再触发一次必然失败（重复上传同版本）的 Publish 运行，属纯噪声。
> 1.0.1 起 tag 与产物一致。

### 性能（裁剪到水印包围盒修复：默认开启，2048² 约 3.6x）
- `INFER_CROP_TO_MASK`（默认 `True`）：推理裁剪到水印外接框（含 padding）修复后回贴；2048² 实测 **7.3s→2.1s**。
- **安全边界（实测）**：mask 外像素与全图推理**逐字节一致**；`crop_quality_probe` 覆盖 58 张 ≥2048² 图，干净区 **58/58 通过**，速度中位 1.51x。
- **诚实边界**：mask 内存在 LaMa 的尺寸相关行为差异，属**非无损**权衡；`fp16`（毁图）、`resize`（有损）、固定网格分块（实测 0.9x 更慢）三条替代路径均已排除。

### 修复（`is_grid` 误触发：JPEG 8×8 DCT 块伪周期）
- 根因：真实 JPEG 图残差场在 lag 8px 出现强自相关（0.19~0.57 vs 阈 0.09），`_ok` 最小周期硬编码 6 放过了它（真实网格周期=56）。
- 修：新增 `config.DETECT_GRID_MIN_PERIOD`（默认 16，范围 4~128，`WME_` 前缀可覆盖），`_ok` 改读该杠杆。
- 效果：真实 doubao 图 is_grid 误触发 **10/10 → 1/10**；`real_gate --strict-scope` 下 `ai_grid` 覆盖面积 mean **8.88%→2.43%**、区域数 **747→42.8**、红线余量 **29%→80%**；`general` 七项红线**逐位不变**；合成网格 seed=7 零漏检。守卫 `tests/test_grid_min_period.py`（含注入反证）。

### 优化（预设推荐：右下角标 NCC 模板匹配）
- `core/preset_recommender._corner_watermark_signal`：以 9 张真值图 ROI 边缘图构成模板库（base64 内嵌、自包含），边缘图 NCC 匹配（`RECOMMEND_CORNER_WATERMARK_NCC_MIN=0.30`）把右下/中右角标图导向 `ai_logo`，避免其进入高危 OCR 空跑。
- 实测：右下角标召回 **48%→76%**（24/50→38/50），留出集 54%（31/57）；自然图零误命中（精确率 100%）。fail-closed 可逆：`RECOMMEND_CORNER_WATERMARK_ENABLED` / `_NCC_MIN` / `_MIN_EDGE`。

### 修复（#450 深度复审抓到的真缺陷）
- 极小图空 ROI 崩溃：3 处空 ROI / gallery 索引路径加守卫（消除 `TypeError`）。
- 预设打分区 8 键契约过时（新增 `corner_watermark`）：同步 `utils/recommend_view._FEATURE_KEYS` 与 `utils/i18n.py` 双语键序。

### 打包
- 新增 `MANIFEST.in`：sdist 口径与 wheel 对齐（prune `tests/` `scripts/` `benchmark*/` 等），并由 `tests/test_repo_hygiene.py` 静态守卫锁定。

### 记录
- 纠正 `EVIDENCE_AND` 的过度断言：真实图探针显示它反使 `ai_grid` 覆盖率翻倍（双刃剑——合成 gradient 防洪、真实非网格图增碎片），故**不删通路**；grid 测试夹具改 gradient 并撤销 xfail。
- `INPAINT_COLOR_MATCH` 新增但**默认关闭**：e2e 前后对比显示开启拖累去痕率（保守回退，保留开关供实验）。
- 新增 `docs/302-资产重建清单.md`：`models/big-lama.pt` 不入库（阶段 205 瘦身），固化下载源/大小校验/断点续传/备份建议。

## [Unreleased]

### 数据（阶段 300 · 28 张真实豆包图入基准 + 重锚，mask_recall 余量 12%→24.8%）
- 用户供图 `D:\照片存储` 收编 **28 张**（c1–c11/c13–c15/s1–s14，2048×2048）入 `benchmark_real/`：真值标注 **29→57 条**，承诺面实评 **25→53 图**。**c12 与 c11 字节完全相同**（sha256 `06863b7c64d4…`），`collect_real_assets.py scan` 已按 hash 去重故不入基准（避免同图双计污染聚合）。
- **独立量取法**（不触 `tests/test_real_asset_preflight.py` 的 `auto:` 循环论证硬守卫）：跨 28 图**平均高通响应**——固定平台覆盖保留、自然纹理相消——干净浮出「豆包 AI 生成」文字足迹 `[1683,1936,319,71]`；再逐图在足迹内按自身响应取阈值掩码外接框（19 个不同 bbox，符合既有 `ai_logo_*` 逐图实测纪律，也过 preflight 的模板化/信息量双守卫）。
- **再锚后实测（53 图 · general）**：mask_recall **0.5111→0.5984**、recall 0.8400→0.9057、mean_iou 0.5876→0.6673、precision 0.0789→0.0758、mask_iou 0.313→0.3124、mask_coverage 0.0120→0.0137、coverage_ratio 2.21→2.47。**红线一条未动**，夹逼不变式全部仍满足；`real_gate --strict-scope` **EXIT=0**。
- **mask_recall 余量 ~12%→~24.8%**（仍是最薄一条，259 advisory 盯防）——「红线未随数据集重锚」的担忧实质性缓解；漂移告警线与硬线之间从 0.010 恢复到 0.089 的真实缓冲。
- **A/B 泛化诊断**（口径自证：原 25 图 box_recall 0.8400 与门禁基线逐位相同）：新 28 张 **0.9643**（27/28）> 原 25 张 0.8400（21/25）⇒ 检测器对新真实图泛化更优。唯一漏检 `c15`（密集小字母艺术字 + 水印低对比度，OCR 被装饰字母占满），经放大目视确认标注无误，属**真漏检**。
- **新增开放项**：两条未承诺通路面积漂移略超 15% 容差——`ai_faint_tiled` 2.38%→2.80%（+17.6%）、`ai_grid` 7.58%→8.88%（+17.2%），均远低于硬红线 7.5%/12.5%；成因为新图自然纹理更丰富的**构成效应**，非检测器回退。见 `docs/258` §六。
- 连带同步（经验 81）：`--update --force-update` 重锚基线后，同步 `tests/test_detector_audit100.py`(MSER_OFF) / `tests/test_overdetection99.py`(CONVERGED) / `tests/test_watermark_scope.py`(ai_logo 29→57) / `tests/test_redline_margin_health259.py`(余量带)；`MSER_ON` 等注入反例锚点保持不变。README / `docs/258` / `docs/298` / `docs/quality/product_scorecard.json` 的口径与指标一并刷新。

### 修复（阶段 300-b · `run_all_checks.py --help` 崩溃）
- `--cov` 的 help 字符串含字面 `%`，argparse 的 `_expand_help` 将其当格式符解析 ⇒ `--help` 直接以 `ValueError: unsupported format character` 崩溃（全角逗号紧邻百分号）。修：转义为 `%%`。验证：修复后 `--help` EXIT=0 且正常输出用法。

### 新增（阶段 297 · 发布 v1.0 基建：[project.scripts] 控制台入口三件套）
- `pyproject.toml` 新增 `[project.scripts]`：`wme`（网页 UI）= `main:main` / `wme-cli`（批量命令行）= `cli:main` / `wme-api`（REST 服务）= `server:start_server`；命名属发布范围决策，改名改表即可。三入口函数均经 AST 核验无必需参数，`pip install` 后可直接无参调用。
- `tests/test_packaging_meta.py` 新增 `test_console_scripts_declared_and_resolvable`：双向锁定「三命令都声明 + module:func 指向磁盘真实顶层可调用」。
- README §3.3 启动节补充 `wme`/`wme-cli`/`wme-api` 命令（源码直启仍保留）。
- **实跑验证**：`pip wheel` 成功产出 wheel，解包 `entry_points.txt` 已真实写入三条 console_scripts；装入临时 venv 后 pip 生成的三条 `.exe` 启动器内容正确（`from cli import main; sys.exit(main())` 等），`wme-cli --help` 仅在缺 numpy 等运行期依赖时报错（环境限制，非 wheel 缺陷）。

### 新增（阶段 297-c · 发布管线 + runbook，选项 3「你决定」落地）
- 新增 `.github/workflows/publish.yml`：打 tag `v*` 或手动触发 → 跑与 CI 同一套门禁（`run_all_checks.py`）→ `python -m build` 构建 wheel+sdist → `pypa/gh-action-pypi-publish` 发 PyPI（凭据走 Secret `PYPI_API_TOKEN`，并注明可改 OIDC Trusted Publisher）。
- 新增 `docs/297-发布v1.0-runbook.md`：固化 4 项发布范围决策处置（命令名已定 / **发 PyPI** / 文字水印暂缓 / API 鉴权留待发布后）、发布步骤、诚实边界（本机未真跑全量 `pip install .`、权重不入包、PyPI 项目名沿用 `wme-watermark-eraser`）。

### 新增（阶段 298 · 项目当前水平评估快照）
- 新增 `docs/298-项目当前水平评估.md`：把记分卡（291 刷新 / 296 续修）落成人读战略快照——六维指标级水平、真实检测硬指标、优先级缺口、三阶段路线图、未受控竞品横评、诚实边界。
- 补登记：297-c 建 `docs/297-发布v1.0-runbook.md` 时漏登 `docs/INDEX.md`（会让 `tests/test_docs_index.py` 变红，正是 295 修过的坑）；本阶段在 INDEX 同时补登 297 与 298，`test_docs_index` 5 例全绿 ⇒ 回归已修。

### 优化（发布链路加固 · `python -m build` 本机不可复现的应对）
- `publish.yml` 在 `python -m build` 后新增 `actions/upload-artifact@v4` 步骤：将 wheel+sdist 上传为 Actions artifact，**即使未配 `PYPI_API_TOKEN` 也能在 Actions 页面下载产物本地试装**，给发布加安全网。
- runbook 诚实边界补记：本机清华镜像缺 `build` 包与 `setuptools>=77`（`from versions: none`），`python -m build` 本机无法复现；已用等价的 `pip wheel --no-build-isolation` 验证 wheel 构建/entry_points/临时 venv 启动器；`python -m build` 在 GitHub 官方 PyPI 环境可正常取构建依赖。

### 新增（阶段 299 · API 安全层：token 鉴权 + 限流，零新依赖）
- 新增 `services/api_security.py`：纯标准库实现——`security_gate()` 统一闸门（限流 + 鉴权），
  进程内令牌桶限流（`_rate_limit_allow`，按 token 或客户端 IP 计），`extract_token` 从
  `Authorization: Bearer` 或 `?token=` 取密钥，`hmac.compare_digest` 恒定时间比对。
- `server.py` 在 `do_GET`/`do_POST` 顶部接入闸门：**安全模式（`API_AUTH_ENABLED=true`）下，
  所有 POST 端点（remove/detect/inpaint/batch/jobs 提交）与作业结果文件下载需有效 token
  （否则 401）；只读 GET（health/capabilities/models/jobs 状态）豁免鉴权但仍受限流**；限流对所有请求生效。
- `config.py` 新增 `API_AUTH_ENABLED`（默认 false）/ `API_TOKEN` / `API_RATE_LIMIT_PER_MIN`（默认 60）/
  `API_RATE_BURST`（默认 10）；`start_server` 加 **fail-closed 启动守卫**：安全模式开但 token 空 ⇒ 拒绝启动。
- **默认关闭 ⇒ 零行为变更**：既有调用方/测试不受影响；`tests/test_api_security299.py` 12 例全绿，
  并复跑 API+打包族 70 例（251/255/256/ concurrency-d1 / auto-erase / packaging）全绿，无回归。

### 修复（阶段 295 · 全量回归验证：修复 5 处真实缺陷）
- 自 290-b 后首次全量回归（218 文件 / 5 批），覆盖 291–294。**修复 5 处真实缺陷**：① docs/292/293/294 未登记 `docs/INDEX.md`；②③④ `tests/test_run_all_checks.py` 3 处断言未同步 C5 的 `docker_static` 默认步骤；⑤ **`scripts/cpu_load.py` 的 `process_busy_pct` 调 `GetProcessTimes` 少传第 4 个 `FILETIME` 出参 ⇒ 偶发栈损坏崩溃（0xC0000409）拖垮整个测试批**（阶段 224 潜伏 ctypes 缺陷）——修：传齐 4 出参 + 求和改 kernel+user + 加 argtypes 防复发。
- 16 个 full-batch 失败经**逐个单跑 RC=0 全证为沙箱删除护栏 artifact**（全是 delete/cleanup/rotation 测试、零触及删除逻辑、同轮累积删除即 SIGTERM；不影响 CI）。见 `docs/295`。

### 修复（阶段 294 · README 规模刷新 + 根文件白名单回归修复）
- **README §4 项目规模据实刷新**：曾 stale（测试 1346→**2500+ 用例 / 219 文件**、设计文档 103→**249+ 篇**、质量门禁 2→**门禁家族 8 成员**、代码 32k→36k）；「services/ui 未完成审计」声明更新为「45 模块全审、5j 续审链收官」。
- 新增 `tests/test_readme_scale.py`：docs/测试文件数**下限守卫**（增长量用 ≥ 下限，抗回归删除、对增长免疫）。
- **修复 291/C5 引入的回归**：新增 `CONTRIBUTING.md`/`SECURITY.md`/`docker-compose.yml` 未登记 `test_repo_hygiene._ALLOWED_ROOT_FILES` ⇒ 该守卫报红；已补登白名单（合法文件 + 理由）。

### 新增/修复（阶段 292–293 · 评测盲区审计 + 打包构建验证）
- **评测盲区审计**（`docs/292`）：对门禁/基准自身跑 eval-blindspot-audit 八探针，确认无「无红线覆盖的变坏类型」结构性盲区（活守卫 50 例全绿为证）；残余缺口（full 管线耗时、真实覆盖/精度）为已披露+blocked，按「不可执行→只能告警」处理。
- **打包构建验证 + PEP 639 许可迁移**（`docs/293`）：`pyproject.toml` 可构建干净完整纯 Python wheel + sdist（装包 smoke import OK）。修复 `project.license` 弃用——旧 TOML table 形式自 setuptools 77 起弃用、2027-02-18 后构建失效，迁到 `license-files`，build-system setuptools 下限 68→77；`tests/test_packaging_meta.py` 新增禁回退弃用 table 的前向兼容守卫。

### 刷新（阶段 291 · 商品外壳体检 + 205 回归修复）
- 刷新自我质检记分卡 `docs/quality/product_scorecard.json`（116 旧值归档于 previous_assessment 块）：overall **B-**（engineering A / core_quality B- / productization C+ / ecosystem D）；新增竞品能力矩阵（显式标注**未受控横评**）与三阶段可验收路线图。文档 `docs/291-商品外壳体检刷新与205回归修复.md`。
- **修复 205 历史重写导致的外壳回归**：`.gitignore` 重建时丢失 116 的加固项；`5edb575`（超分决策落地）又把运行期配置 `config.user_overrides.json` 误提交。已 `git rm --cached` 解除追踪（本地文件保留；config 默认值与决策一致 ⇒ 行为不变）+ `.gitignore` 重新加固（`config.user_overrides.json` / `*.out` / `*.log` / `*.tmp`）。
- 新增 `CONTRIBUTING.md`（内部开发规约：唯一验证入口 / 分层铁律 / 门禁家族 / 改动纪律）与 `SECURITY.md`（本地离线 posture / 依赖单一真值 / 合规边界 / 报告途径）。
- 诚实披露：CHANGELOG [Unreleased] 此前冻结在 116，117–290 未逐条回填；本版据实刷新记分卡，逐阶段细节仍见 `docs/`。

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

## [1.0.0] —— 核心能力与质量体系成型（2026-10-15 发布）

### 核心能力
- **多输入源**：图片 / PDF（多页拆分-逐页处理-回填）/ 视频（拆帧-逐帧-拼回，支持掩码复用 + 漂移护栏）。
- **智能检测**：8 类水印预设（general / ai_tiled / ai_signature / ai_corner / ai_text_heavy /
  ai_faint_tiled / ai_grid / ai_gradient / ai_logo / ai_multi）+ 自动预设推荐。
- **修复引擎**：LaMa（TorchScript）+ OpenCV（TELEA/NS）+ 插件引擎；双跑择优（opt-in）。
- **后处理深化**：Poisson 融合 / 纹理重建 / 伪影抑制 / 轻量超分 / 真·超分（ESRGAN / Real-ESRGAN）。
- **五入口**：Gradio 网页界面 / REST API / CLI / 插件系统 / 打包 EXE（PyInstaller）。

### 质量体系（88 起）
- **门禁家族 8 成员**：`quality_gate`（合成）+ `real_gate`（真实，五红线）+ `e2e_real_probe`（修复侧）+
  `mask_param_audit` + `clean_bench_probe` + `prompted_area_gate` + `perf_probe` + `docker_consistency_check`。
- 真值审计体系：`scripts/audit_selector.py`（覆盖度荒漠选点）+ `AUDITED_SINCE` 登记，
  已审 **45 个模块**（5j 深度续审链 276–290 收官，`--unaudited` 返回空表）。
- 注入式反例：每处修复均验证「守卫真会失败」（revert → 守卫 RC=1）。
- 经验库 `.workbuddy/memory/LESSONS.md`（281 条）；能力声明 SSOT `config.WATERMARK_TYPE_SCOPE`。

### 工程化
- 分层架构 `ui → services → core → utils → config`（严禁反向依赖，core/services 零 gradio）。
- **219 个测试文件、2500+ 用例；249+ 篇设计/审计文档**（规模 SSOT 见 README §4，`tests/test_readme_scale.py` 守护下限）。
- 依赖三档：`requirements.txt`（完整）/ `-min`（无 torch）/ `-gpu`（CUDA）。
- Dockerfile（CPU，含 GPU recipe）+ PyInstaller `build.spec`。

### 发布与真实数据验证（297–300）
- **可安装分发**：`pyproject.toml` `[project.scripts]` 三件套 `wme`/`wme-cli`/`wme-api`；wheel **0.66MB** + sdist **1.4MB**（纯 Python，不含基准大图）；`.github/workflows/publish.yml` 打 tag `v*` → 门禁 → 构建 → 发 PyPI（Secret `PYPI_API_TOKEN`）。
- **API 安全层**（299）：`services/api_security.py` 纯标准库 token 鉴权 + 进程内令牌桶限流，默认关闭 ⇒ 零行为变更。
- **真实数据基准**（300）：28 张豆包真实图入 `benchmark_real/`，承诺面实评 25→53 图；mask_recall 0.5111→**0.5984**（余量 ~24.8%），五红线全过、`real_gate --strict-scope` EXIT=0。
- 仓库已发布：`github.com/chu803/wme`。

---

## 合规声明

本工具**仅限**用于去除使用者本人拥有著作权的 AI 生成内容上的可见水印。详见 `LICENSE`。
