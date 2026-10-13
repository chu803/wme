# 项目长期记忆 · AI 智能水印擦除工作台
> SSOT：STAGES.md / LESSONS.md / 每日日志 `YYYY-MM-DD.md`。本文件只留索引、铁律、决策级边界、进度指针；阶段流水账见 docs/291-298 与 STAGES.md。

## 定位与合规
只处理用户自有权处理的 AI 生成图片可见水印；统一「去除本人生成的 AI 图片水印」表述（《著作权法》53 条）。

## 项目铁律（持久）
- 阶段完成主动告知「【XX阶段开发完成…】」；禁止「此处省略代码」；收到「终止项目」前持续推进。
- 分层 `ui→services→core→utils→config`，严禁反向依赖；`core/services/utils` 零 gradio；`config` 不依赖 `utils.logger`。
- 探针/basetemp 用非 Temp 路径；沙箱批量删 >50 文件 → `SystemExit(1)`（两段式受限 + 会话闩锁，归档 rename 优于删除）。
- 多段编辑须串行 + 重读校验；注入式反例读写 `newline=''`、sha 对原始字节（`'rb'`）计算。
- 网络：PyTorch/GitHub/HF/清华镜像不可达；PyPI 清华镜像可达；大文件走 `download_model.py`。

## 关键边界 · 真值审计
- 真实基准（29 图 / general）：mask_recall **0.5111** / precision 0.0789 / recall 0.84 / mean_iou 0.5876 / mask_iou 0.313 / mask_coverage 0.0107 / coverage_ratio 2.21。红线 recall≥0.64 / precision≥0.05 / mean_iou≥0.35 / **mask_recall≥0.45** + 崩溃护栏 mask_iou≥**0.20** / mask_coverage≤0.020 / coverage_ratio≤3.50。`real_gate --strict-scope` EXIT=0。
- 图像级缺口（243）：`WATERMARK_KNOWN_GAP_IMAGES={a1,a2,a3,a4}`（大颗宝石角标）像素域不可分 ⇒ 收窄承诺。守卫 `test_known_gap_images243.py` 4 例全绿。
- 能力声明：`WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。

## 门禁家族（持久契约）
quality_gate + real_gate(30图) + e2e_real_probe(10图×4类) + mask_param_audit + clean_bench_probe + prompted_area_gate + perf_probe + docker_consistency_check。退出码 0/1/2。每个 `--update/--refresh` 须接 `baseline_guard`。脚本 print 非 GBK 须 `force_utf8_stdio()`。

## 性能归因链
detect 真实 P95=**0.776s**（<1.5s 达标）；full P95=**8.585s**（superres=off，结构性地板≈4.4s ⇒ 1.5s 对 full 不可达）。torch 2.9.1+cu128 + big-lama.pt。

## 项目健康度
源码 ~33.2k + 测试 ~22.5k；实测 **2504 用例 / 219 文件**；**45 模块全审计**（5j 续审链 276-290 收官，`audit_selector.py --unaudited` 返回空）；torch 2.9.1+cu128（RTX 5060 Laptop 8151MiB）+ big-lama.pt。

## 接口契约（101，持久）
`detect_watermark_mask(image: np.ndarray, *, preset=...) -> DetectionResult`：不吃路径；收 RGB/RGBA/灰度；`res.mask` uint8 0/255；`regions[].x/y/w/h` 为 `np.int32`（写 JSON 前 `int()`）；9 种格式、不递归、`MAX_IMAGE_PIXELS=80M` 超限抛异常；对外打分用掩码级指标。

## 产品化与战略
技术内核强、商品外壳缺。P1 数据质量 → P2 产品化性能 → P3 生态。护城河=全本地离线 + 免手涂 8 预设 + 图/PDF/视频全源 + 合规定位。记分卡 `docs/quality/product_scorecard.json`（291 刷新 / 296 续修）：overall **B-** / eng **A** / core **B-** / prod **C+** / eco **D**。

## 仓库与交付
- git：`init -b main`，身份 `WME Dev <dev@wme.local>`；CI `ci.yml`→`run_all_checks.py`；推远端前跑 `scripts/ci_env_probe.py`。
- 历史重写（205）：`models/big-lama.pt` 已从全部历史移除（`.git` 326.6→143.8MB）；曾致 `.gitignore` 回归 + `config.user_overrides.json` 误入库，291 已 `git rm --cached` + 加固。
- 验证入口 `scripts/run_all_checks.py`（CI 子集 / `--full` / `--gates`）；E2 删除 `scripts/clean_artifacts.py`（archive 优先）。
- Docker（C5 收口）：`.dockerignore` 上下文 4.4MB；⚠ 本机无 docker CLI，镜像从未真 build（仅静态契约 + 一致性脚本门禁）。`Dockerfile.gpu` P0（python3.11 错配修复 + torch 2.9.1+cu128 统一、删 torchvision）；`docker-compose.yml` cpu/gpu profile。

## API 侧
只读端点 `/api/capabilities`/`/api/models`/`/api/models/active`/`/api/models/verify` 已补齐（251）；推理端点绝不豁免（199 fail-closed）。**255 异步作业收口**（`job_manager.py` + `POST/GET /api/jobs[/id][/file]`，11 例全绿）。**257 D1 worker 池** `config.API_ASYNC_MAX_WORKERS` 默认 2（[1,8]）；仅 `POST /api/models/active` 仍不开放。
- **D1 并发实测（RTX 5060 Laptop 8GB，2026-10-13，superres=off）**：worker 1→2 吞吐 0.104→0.168 img/s（+62%）、P50 9.5→9.9s（平）；worker=4 吞吐反降 0.139、P50 飙 24.7s、显存 3523→6623MB ⇒ **默认 2 已验证为甜点，提至 4/8 因显存争用致吞吐回退+尾延迟翻倍**。脚手架 `scripts/d1_concurrency_probe.py` 可复测。

## 决策级边界（已收口）
- A4（2026-10-04）：a1-a4 归 corner_logo 非全图渐变；`ai_gradient` 仍为真·无真实图缺口。
- ① 超分 2026-10-08 关闭（`user_overrides` → `ENABLED=false/SCALE=4/BACKEND=auto`）。
- **242 红线决策（2026-10-08）A 维持**：mask_recall 硬线 0.45 不动，靠 259 advisory 余量监控（~12% 最薄）盯防，不放松红线。
- 环境事故（2026-10-04）：easyocr 降级已还原（cu128 wheel 2862MB）。

## 发布工程（296-298，本会话）
- **296 precision↔mask_recall 结构性耦合（决定性否证）**：半透明通路在 25 门禁图产 190 个 FP 盒（translucent），盒级是 FP 但像素覆盖真值水印、贡献 ~0.076 mask_recall；关掉该通路 precision 0.079→0.26(3.3×) 但 mask_recall 0.511→0.4355 **破 0.45 红线** ⇒ 提盒精度必蚀掩码召回，无免费午餐。当前 (0.65,0.50) 为校准平衡；干净解需半透明通路**精确盒化**（深层改进，research 阻塞）。precision 低但安全（余量 37%），mask_recall 余量 12% 是更紧约束。
- **297-298 发布管线就绪**：pyproject 补 `[project.scripts]` 三件套（`wme`=main / `wme-cli`=cli / `wme-api`=server），AST 守卫 + 真 wheel 构建验证（entry_points 真实写入、临时 venv 真装生成正确启动器）。新增 `.github/workflows/publish.yml`（tag `v*` → 门禁 → build → 发 PyPI，Secret `PYPI_API_TOKEN`）+ `docs/297-发布v1.0-runbook.md` + `docs/298-项目当前水平评估.md`。**待用户 push + 配 token + 打 tag v1.0.0 即上 PyPI**。

## 文档铁律（294 沉淀，本会话再犯一次 ⇒ 必须双登记）
① **新增根级文件必须同步登记 `tests/test_repo_hygiene._ALLOWED_ROOT_FILES`**；② **新增 `docs/*.md` 必须同步登记 `docs/INDEX.md`**（`tests/test_docs_index` 机械守卫，漏登必红）。295 因漏登 INDEX 抓回归；**298 自己建 297-runbook 时又漏登 INDEX，补登 297+298 后 test_docs_index 5 例全绿** ⇒ 此律两度应验。阶段收尾须跑**更广回归**（非仅靶向文件）。

## 本链沉淀的可复用经验（节选；完整见 LESSONS.md）
- **语言是跨进程持久化共享全局状态**（290 治本）：i18n 状态落盘 `cache/i18n_state.json`，污染后续 pytest 进程；`conftest.py` 的 `pytest_configure` 早于 collection 把状态文件指向会话临时目录已治本。须用「投毒态」重跑证伪修复（否则假绿）。
- gradio 6.22 `gr.update()` 返回普通 dict 非 `Update` 对象；`from-import` 后 monkeypatch 须打**导入方**命名空间；断言「补丁生效」与「结果合法」须同时写；源码子串匹配会误命中注释须用 AST。
