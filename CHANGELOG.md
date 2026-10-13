# 更新日志 / CHANGELOG

> 本文件按「里程碑」归纳项目演进（而非逐阶段流水账；逐阶段细节见 `docs/`，共 118 份）。
> 版本号遵循 `MAJOR.MINOR.PATCH`；当前版本 `1.0.0`。

## [Unreleased]

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
