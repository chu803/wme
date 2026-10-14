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
quality_gate + real_gate(30图) + e2e_real_probe(10图×4类) + mask_param_audit + clean_bench_probe + prompted_area_gate + perf_probe + docker_consistency_check。退出码 0/1/2（+ real_gate 退码3=环境错误）。每个 `--update/--refresh` 须接 `baseline_guard`。脚本 print 非 GBK 须 `force_utf8_stdio()`。
- **real_gate / run_real_benchmark 必须跑在 canonical venv**：解释器 `python/envs/default/Scripts/python.exe`（torch 2.9.1+cu128 + easyocr + numpy + cv2 + PIL 全齐）。用错解释器（如系统 Python3.11.4 无 torch/easyocr）会**静默降级 OpenCV 兜底**，致五条红线全红、相对基线全漂移的 silently-wrong 误判——已加 `_require_runtime_deps()` 守卫（缺失 torch/easyocr ⇒ EXIT=3 环境错误，信息指明 canonical venv 路径），避免假红误导发布。
- **「CI 已绿」必须以 `ci_env_probe.py` 全量、且确认零真红为准**：`real_gate` 的 `_require_runtime_deps()` 守卫在 CI 等价（屏蔽 torch/easyocr）下返回 EXIT=3；若 CLI 测试未用 `monkeypatch.setattr(rg,"_require_runtime_deps",lambda:None)` 屏蔽，断言会被 3 击穿。**仅凭单文件/隔离子集 RC=0 不能断言 CI 绿**——阶段 300 收口时上一轮只在真实 venv（torch 在、守卫天然不触发）下跑过隔离集，误判「CI 已绿」，全量 CI 探针才暴露 5 个 `test_real_gate` 真红（`cb43e84` 修复：补齐 5 处 monkeypatch）。**真红 vs 沙箱假红区分**：失败若 `SystemExit:1` 且行号指向删除/日志轮转（`sitecustomize._check_bulk_delete_guard` 会话级批量删闩锁），属本沙箱 artifact、真实 GitHub CI 无此闩锁，不计为真红、不修。

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

## API 安全层（299 · 你决定：API 鉴权/限流）
- `services/api_security.py`：纯标准库 token 鉴权 + 进程内令牌桶限流（零新依赖，沿用零依赖 REST）。
- `API_AUTH_ENABLED`（默认 false，总开关）/ `API_TOKEN` / `API_RATE_LIMIT_PER_MIN`(60) / `API_RATE_BURST`(10)。**默认关 ⇒ 零行为变更**。
- 契约：安全模式下 POST 端点 + 作业文件下载需 token（401）；只读 GET 豁免鉴权仍受限流（呼应 251 闸门豁免）；start_server fail-closed（开安全模式却空 token ⇒ 拒启）。
- 守卫 `tests/test_api_security299.py` 12 例全绿；API+打包族 70 例无回归。

## 决策级边界（已收口）
- A4（2026-10-04）：a1-a4 归 corner_logo 非全图渐变；`ai_gradient` 仍为真·无真实图缺口。
- ① 超分 2026-10-08 关闭（`user_overrides` → `ENABLED=false/SCALE=4/BACKEND=auto`）。
- **242 红线决策（2026-10-08）A 维持**：mask_recall 硬线 0.45 不动，靠 259 advisory 余量监控（~12% 最薄）盯防，不放松红线。
- 环境事故（2026-10-04）：easyocr 降级已还原（cu128 wheel 2862MB）。

## 发布工程（296-298，本会话）
- **296 precision↔mask_recall 结构性耦合（决定性否证）**：半透明通路在 25 门禁图产 190 个 FP 盒（translucent），盒级是 FP 但像素覆盖真值水印、贡献 ~0.076 mask_recall；关掉该通路 precision 0.079→0.26(3.3×) 但 mask_recall 0.511→0.4355 **破 0.45 红线** ⇒ 提盒精度必蚀掩码召回，无免费午餐。当前 (0.65,0.50) 为校准平衡；干净解需半透明通路**精确盒化**（深层改进，research 阻塞）。precision 低但安全（余量 37%），mask_recall 余量 12% 是更紧约束。
- **297-298 发布管线就绪**：pyproject 补 `[project.scripts]` 三件套（`wme`=main / `wme-cli`=cli / `wme-api`=server），AST 守卫 + 真 wheel 构建验证（entry_points 真实写入、临时 venv 真装生成正确启动器）。新增 `.github/workflows/publish.yml`（tag `v*` → 门禁 → build → 发 PyPI，Secret `PYPI_API_TOKEN`）+ `docs/297-发布v1.0-runbook.md` + `docs/298-项目当前水平评估.md`。**待用户 push + 配 token + 打 tag v1.0.0 即上 PyPI**。

## 数据方向 · 28 张真实豆包图入基准（300 · 已提交 release + 重锚 · 57d9c5d）
- 用户供图 `D:\照片存储` → 29 PNG 入 `benchmark_real/images/`；**c12 与 c11 字节完全相同**（sha256 `06863b7c64d4…`，collect 按 hash 去重）⇒ 实为 **28 张唯一图**（c1-c11/c13-c15/s1-s14，均 2048×2048）。
- 28 张全带同一「豆包 AI 生成」**固定平台水印**（右下 ≈ `[1683,1936,319,71]`）；**无干净图** ⇒ 296 作战包「干净图校准 precision」目标未达成。
- **独立量取法**（非 detector ⇒ 不触 `AUTO_PREFIX` 循环论证守卫）：跨 28 图**平均高通响应**（固定覆盖保留、自然纹理相消）定足迹，再逐图在足迹内按自身响应取阈值掩码外接框（同既有 ai_logo_* 逐图实测纪律）。v1「矩形窗口」版会吃背景噪声致框外扩（虚高 recall），已弃用。
- `real_asset_preflight` 的**模板化/信息量守卫**（单 bbox 占比≥50%、去重占比<0.5）会否掉「统一 bbox」——该守卫假设位置各异，对固定平台覆盖是**已知误报面**（既有 ai_logo_* 靠逐图微差通过）。
- **已提升（用户授权 A）**：28 条写入 release `ground_truth.json`（29→57 条）；承诺面实评 25→**53 图**。连带同步 `test_watermark_scope.py`（ai_logo 29→57）、`test_detector_audit100.py`(MSER_OFF)、`test_overdetection99.py`(CONVERGED)、`test_redline_margin_health259.py`(余量带)。**`MSER_ON`/`STAGE*_LEVEL` 注入反例锚点保持不变**。红线一条未动（夹逼不变式仍满足）。
- **门禁结果（53 实评 = 57 标注 − 4 缺口）**：mask_recall **0.5111→0.5984**（≥0.45，余量 ~12%→**~24.8%**）/ precision 0.0789→0.0758（≥0.05）/ 区域召回 0.84→0.9057 / mean_iou 0.5876→0.6673 / mask_iou 0.313→0.3124 / mask_coverage 0.0120→0.0137 / 过检倍率 2.21→2.47 ⇒ **全部红线通过，`real_gate --strict-scope` EXIT=0**，已 `--update --force-update` 重锚基线。
- **A/B 泛化诊断**（口径自证：orig 组 box_recall 0.8400 与门禁基线逐位相同）：新 28 张 **0.9643**（27/28 命中）> 原 25 张 0.8400（21/25）⇒ 检测器对新真实图**泛化良好且更优**。唯一漏检 c15（密集小字母艺术字 + 水印低对比度，OCR 被装饰字母占满）；放大目视确认标注无误，属**真漏检**。
- **新增开放项**：未承诺通路面积漂移 ai_faint_tiled 2.38%→2.80%(+17.6%)、ai_grid 7.58%→8.88%(+17.2%)，均略超 15% 容差但远低于硬红线 7.5%/12.5%；成因为新图自然纹理更丰富的**构成效应**，非检测器回退。
- **顺修两个既存红灯（阶段 300-b）**：`scripts/d1_concurrency_probe.py`（299 新建）漏同步两个守卫 ⇒ `test_selfcheck_coverage_is_accounted_for` 与 `test_scripts_console_encoding` 常红。修：登记进 `NOT_REGISTERED_BY_DESIGN` + main() 补 `force_utf8_stdio()`。
- **另修 `run_all_checks.py --help` 崩溃**：`--cov` help 字符串字面 `%` 被 argparse 当格式符 ⇒ ValueError；转义 `%%` 后 EXIT=0。
- **已知 flaky（非本轮引入）**：`test_utils_audit90.py` 两个 rotation 用例在全量下顺序污染（共用物理 `logs/app.log`，`[8]` 参数最敏感）；单跑/文件级全绿，该文件注释自述「长期 flaky 真根因」，专门守卫 `test_logging_global_leak245.py`/`246` 均绿。
- 文档同步：README 验证状态列、`docs/258` §六（新增再锚小节）、`docs/298`、`docs/quality/product_scorecard.json`、CHANGELOG。探针产物归档 `.scratch/data_direction_20261013/`。

## 文档铁律（294 沉淀，本会话再犯一次 ⇒ 必须双登记）
① **新增根级文件必须同步登记 `tests/test_repo_hygiene._ALLOWED_ROOT_FILES`**；② **新增 `docs/*.md` 必须同步登记 `docs/INDEX.md`**（`tests/test_docs_index` 机械守卫，漏登必红）。295 因漏登 INDEX 抓回归；**298 自己建 297-runbook 时又漏登 INDEX，补登 297+298 后 test_docs_index 5 例全绿** ⇒ 此律两度应验。阶段收尾须跑**更广回归**（非仅靶向文件）。

## 本链沉淀的可复用经验（节选；完整见 LESSONS.md）
- **语言是跨进程持久化共享全局状态**（290 治本）：i18n 状态落盘 `cache/i18n_state.json`，污染后续 pytest 进程；`conftest.py` 的 `pytest_configure` 早于 collection 把状态文件指向会话临时目录已治本。须用「投毒态」重跑证伪修复（否则假绿）。
- gradio 6.22 `gr.update()` 返回普通 dict 非 `Update` 对象；`from-import` 后 monkeypatch 须打**导入方**命名空间；断言「补丁生效」与「结果合法」须同时写；源码子串匹配会误命中注释须用 AST。
