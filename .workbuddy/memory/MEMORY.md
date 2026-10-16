# 项目长期记忆 · AI 智能水印擦除工作台
> SSOT：STAGES.md / LESSONS.md / 每日日志 `YYYY-MM-DD.md`。本文件只留索引、铁律、决策级边界、进度指针；阶段流水账见 docs/291-300 与 STAGES.md / LESSONS.md。

## 定位与合规
只处理用户自有权处理的 AI 生成图片可见水印；统一「去除本人生成的 AI 图片水印」表述（《著作权法》53 条）。

## 项目铁律（持久）
- 阶段完成主动告知「【XX阶段开发完成…】」；禁止「此处省略代码」；收到「终止项目」前持续推进。
- 分层 `ui→services→core→utils→config`，严禁反向依赖；`core/services/utils` 零 gradio；`config` 不依赖 `utils.logger`。
- 探针/basetemp 用非 Temp 路径；沙箱批量删 >50 文件 → `SystemExit(1)`（两段式受限 + 会话闩锁，归档 rename 优于删除）。
- 多段编辑须串行 + 重读校验；注入式反例读写 `newline=''`、sha 对原始字节（`'rb'`）计算。
- 网络：GitHub TCP:443 可达但沙箱无凭证 ⇒ **push 须用户本机执行**；PyPI 清华镜像可达。

## 关键边界 · 真值审计
- 真实基准（53 实评 = 57 标注 − 4 缺口）：mask_recall **0.5984** / precision 0.0758 / recall 0.9057 / mean_iou 0.6673 / mask_iou 0.3124 / mask_coverage 0.0137 / coverage_ratio 2.47。红线 recall≥0.64 / precision≥0.05 / mean_iou≥0.35 / **mask_recall≥0.45** + 崩溃护栏 mask_iou≥**0.20** / mask_coverage≤0.020 / coverage_ratio≤3.50。`real_gate --strict-scope` EXIT=0。
- 图像级缺口（243）：`WATERMARK_KNOWN_GAP_IMAGES={a1,a2,a3,a4}`（大颗宝石角标）像素域不可分 ⇒ 收窄承诺。守卫 `test_known_gap_images243.py` 4 例全绿。
- 能力声明：`WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。
- **制裁尺度（242）**：mask_recall 硬线 0.45 不动，靠 259 advisory 余量监控（~24.8% 余量）盯防。

## 门禁家族（持久契约）
quality_gate + real_gate(30图) + e2e_real_probe(10图×4类) + mask_param_audit + clean_bench_probe + prompted_area_gate + perf_probe + docker_consistency_check + **crop_quality_probe（2048² 裁剪判决）**。退出码 0/1/2（+ real_gate 退码3=环境错误）。每个 `--update/--refresh` 须接 `baseline_guard`。脚本 print 非 GBK 须 `force_utf8_stdio()`。
- **real_gate / run_real_benchmark 必须跑在 canonical venv**（`python/envs/default/Scripts/python.exe`，torch 2.9.1+cu128 + easyocr + numpy + cv2 + PIL 全齐）。用错解释器会静默降级 OpenCV 兜底致五红线全红 ⇒ 已加 `_require_runtime_deps()` 守卫（缺 torch/easyocr ⇒ EXIT=3）。
- **「CI 已绿」必须以 `ci_env_probe.py` 全量、确认零真红为准**；单文件/隔离子集 RC=0 不能断言 CI 绿。

## 性能归因链（含裁剪优化结论）
- detect 真实 P95=**0.776s**（<1.5s 达标）；full P95=**8.585s**（superres=off，结构性地板≈4.4s ⇒ 1.5s 对 full 不可达）。torch 2.9.1+cu128 + big-lama.pt。
- **瓶颈=LaMa fp32 在 2048² 上 ~7-8s**（recommend+detect 仅 ~1.85s，非瓶颈）。
- **裁剪到水印包围盒修复（INFER_CROP_TO_MASK，默认开）**：2048² 上 7.3s→2.1s（**3.6x**）；只贴回 mask 区 ⇒ mask 外与 full **逐字节一致**（非分块图验证 crop-vs-full 外=0）；mask 内为 LaMa 尺寸相关行为差异（in>10 最高 ~63%，非无损权衡）。`crop_quality_probe` 全量 58 张 ≥2048 图判决：**干净区安全 58/58 通过**（crop 干净区扰动不差于 full，LaMa 在 mask 外固有轻微扰动两种模式同量级）、速度中位 1.51x / 2048² ~3.2x、质量软门禁未全过（水印填充不同，无干净真值不可判优劣）⇒ 默认开、可设 `INFER_CROP_TO_MASK=0` 回退 full。fp16 仍禁（207 毁图）、resize 有损、固定网格分块实测 0.9x（更慢）已排除。
- **detect/recommend 成本拆分（#450）**：`recommend_preset` **不调用** `detect_watermark_mask`、不调 OCR（仅 numpy+cv2 轻量特征分析：`_build_residual`/`_corner_solid_count`/`_text_density`/`_spectral_regularity`/`_periodicity`/`_gradient_features`）；`real_gate` 用显式 `--presets` 故红线基线**不受** recommend 影响。`detect_cost_probe.py` 全量 48 张豆包基准结论：recommend 分布 text_heavy×30 / multi×11 / grid×5 / general×2、**ai_logo×0**——根因=水印是「豆包 AI 生成」**文字**、`_corner_solid_count`（实心度）恒为 0，自然图笔画密度又让 `text=1.0` 误触发 ⇒ 推 text_heavy/grid/multi 均 `edge=True` ⇒ 右下角水印图上 **edge 策略 + OCR 空跑**=真冗余点（非「detect 被重跑」）。
- **#450 修复（用户授权「改 recommender 判据」，已实现+门禁）**：把右下/中右角标图导向 `ai_logo`（edge=False）跳过高危 OCR 空跑。**边缘密度不对称性启发式被全量阈值扫描实证否决**（最优仅 6/75 命中、漏 69，且底部纹理自然图误命中 40+/88——「上静下动」把 BR/TL 比抬高；灰度白度/方差亦不可分）。**改用边缘图 NCC 模板匹配** `core/preset_recommender._corner_watermark_signal`。**极致化（模板库）**：单平均模板会把不同外观水印糊掉，改 **9 张真值图右下 ROI 边缘图构成模板库**（base64 PNG 纵向堆叠内嵌、自包含），对每 ROI 取库内最大 NCC（±roi 双极性，BR + 中右两 ROI）。**全量 88 图 + 留出集（模板源不计召回、防自匹配膨胀）标定**：NCC 默认 `RECOMMEND_CORNER_WATERMARK_NCC_MIN=0.30` → **精确率 100%（自然图 0 误命中）、右下角标召回 48%→76%（24/50→38/50）、留出集召回 54%（31/57）**；同等零误命中下严格优于单平均模板（avg@0.30 仅 45%、avg@0.40 仅 48%），且救回已知漏检 c15；0.35 更稳（召回 48%）、0.25 召回 55% 但 FP=1。fail-closed 可逆杠杆：`RECOMMEND_CORNER_WATERMARK_ENABLED`(True)/`_NCC_MIN`(0.30)/`_MIN_EDGE`(5.0)。决策分支置于 grid 之前（角标压周期）、corner_watermark 不进 families 组合（免误入 ai_multi）。**关键坑**：①模板与查询须同灰度口径（cv2 `COLOR_RGB2GRAY`，PIL `mean` 建模板会让 NCC 从 ~0.7 掉到 ~0.45，害我重嵌）；②`_load_corner_watermark_templates` 必须走 `_require_cv2()`（模块不顶层 import cv2，NameError 被 except 吞成空库 ⇒ 信号恒 0）；③合成实体无法复刻模板细粒度边缘做 NCC（会误走 solid_logo），故单测用 monkeypatch 隔离验决策分支、真实召回交 `scripts/verify_corner_reco.py`；④`verify_corner_reco` 报的「非GT误命中=1」是 **c12.png（=c11 字节重复图）被 id 映射漏标**，非真自然图误命中。**I3 SSOT 同步**：scores 键 `corner_watermark` ⇒ `utils/recommend_view._FEATURE_KEYS` + `utils/i18n.py` 双语 `detect.recommend.feature.corner_watermark` 同序补齐（否则 `test_utils_audit98` I3 红——prior session 加 scores 键漏同步已埋红，本次连带修复）。守卫 `tests/test_corner_watermark_signal.py`（9 例）；探针 `probe_recommend_dist.py`/`verify_corner_reco.py` 登记 `NOT_REGISTERED_BY_DESIGN`。

## 项目健康度
源码 ~33.2k + 测试 ~22.5k；实测 **2504 用例 / 219 文件**；**45 模块全审计**（5j 续审链 276-290 收官）；torch 2.9.1+cu128（RTX 5060 Laptop 8151MiB）+ big-lama.pt。

## 接口契约（101，持久）
`detect_watermark_mask(image: np.ndarray, *, preset=...) -> DetectionResult`：不吃路径；收 RGB/RGBA/灰度；`res.mask` uint8 0/255；`regions[].x/y/w/h` 为 `np.int32`（写 JSON 前 `int()`）；9 种格式、不递归、`MAX_IMAGE_PIXELS=80M` 超限抛异常；对外打分用掩码级指标。

## 产品化与战略
技术内核强、商品外壳缺。P1 数据质量 → P2 产品化性能 → P3 生态。护城河=全本地离线 + 免手涂 8 预设 + 图/PDF/视频全源 + 合规定位。记分卡 `docs/quality/product_scorecard.json`：overall **B-** / eng **A** / core **B-** / prod **C+** / eco **D**。

## 仓库与交付
- git：`init -b main`，身份 `WME Dev <dev@wme.local>`；CI `ci.yml`→`run_all_checks.py`；推远端前跑 `scripts/ci_env_probe.py`。**已发布（2026-10-15）**：远端 `github.com/chu803/wme`，`main` 已推、上传 422MB；⚠ **推送须用户本机终端执行**（沙箱代理写被网络策略阻断）。
- 历史重写（205）：`models/big-lama.pt` 已从全部历史移除；`benchmark_real/images` ≈280MB 二值图已授权入库（用户授权、不移 LFS）。
- 验证入口 `scripts/run_all_checks.py`（CI 子集 / `--full` / `--gates`）；E2 删除 `scripts/clean_artifacts.py`（archive 优先）。
- Docker（C5）：`.dockerignore` 4.4MB；⚠ 本机无 docker CLI，镜像从未真 build（仅静态契约 + 一致性门禁）。`Dockerfile.gpu` P0（python3.11 错配修复 + torch 2.9.1+cu128 统一）。

## API 侧
只读端点 `/api/capabilities`/`/api/models`/`/api/models/active`/`/api/models/verify` 已补齐（251）；推理端点绝不豁免（199 fail-closed）。**255 异步作业收口**（11 例全绿）。**257 D1 worker 池** `config.API_ASYNC_MAX_WORKERS` 默认 2（[1,8]）；RTX 5060 实测 worker=4 吞吐反降+尾延迟翻倍 ⇒ 默认 2 为甜点（脚手架 `scripts/d1_concurrency_probe.py`）。**仅 `POST /api/models/active` 仍不开放**。
- **D1 并发实测（2026-10-13，superres=off）**：worker 1→2 吞吐 0.104→0.168 img/s（+62%）；worker=4 吞吐 0.139、P50 24.7s、显存 3523→6623MB。
- **API 安全层（299）**：`services/api_security.py` 纯标准库 token 鉴权 + 令牌桶限流；`API_AUTH_ENABLED`（默认 false，零行为变更）/ `API_TOKEN` / `API_RATE_LIMIT_PER_MIN`(60) / `API_RATE_BURST`(10)；安全模式 POST + 作业文件下载需 token（401），只读 GET 豁免鉴权仍受限流；start_server fail-closed。守卫 `tests/test_api_security299.py` 12 例全绿。

## 决策级边界（已收口）
- A4（2026-10-04）：a1-a4 归 corner_logo 非全图渐变；`ai_gradient` 仍为真·无真实图缺口。
- ① 超分 2026-10-08 关闭（`user_overrides` → `ENABLED=false/SCALE=4/BACKEND=auto`）。
- 环境事故（2026-10-04）：easyocr 降级已还原（cu128 wheel 2862MB）。

## 发布工程（296-298，指针）
- **296 决定性否证**：半透明通路在 25 门禁图产 190 个 FP 盒（贡献 ~0.076 mask_recall）；关掉 precision 0.079→0.26(3.3×) 但 mask_recall 0.511→0.4355 **破 0.45 红线** ⇒ 提盒精度必蚀掩码召回，无免费午餐；当前 (0.65,0.50) 为校准平衡。
- **297-298 发布管线就绪**：pyproject `[project.scripts]` 三件套（wme/cli/api）+ AST 守卫 + 真 wheel 构建验证；`.github/workflows/publish.yml`（tag v* → 门禁 → build → PyPI，Secret PYPI_API_TOKEN）+ `docs/297-runbook` + `docs/298-评估`。**待用户 push + 配 token + 打 tag v1.0.0 即上 PyPI**。

## 数据方向 · 28 张真实豆包图入基准（300，指针）
- 用户供图 `D:\照片存储` → 29 PNG 入 `benchmark_real/images/`；c12 与 c11 字节相同（sha256 去重）⇒ 实为 **28 张唯一图**（均 2048×2048）；全带同一「豆包 AI 生成」固定平台水印（右下 ≈[1683,1936,319,71]）；**无干净图** ⇒ 296 干净图校准 precision 未达。
- **门禁结果（53 实评）**：mask_recall **0.5111→0.5984**、precision 0.0789→0.0758、区域召回 0.84→0.9057、mean_iou 0.5876→0.6673、过检倍率 2.21→2.47 ⇒ 全部红线通过，`real_gate --strict-scope` EXIT=0，已 `--update --force-update` 重锚。
- A/B 泛化：新 28 张 box_recall 0.9643 > 原 25 张 0.8400 ⇒ 泛化良好；唯一漏检 c15（密集小字母艺术字+低对比度，属真漏检）。
- 顺修（300-b）：`d1_concurrency_probe.py` 漏同步两守卫 ⇒ 登记 `NOT_REGISTERED_BY_DESIGN` + main() 补 `force_utf8_stdio()`；`run_all_checks.py --help` 崩溃（`%` 未转义 `%%`）。

## 文档铁律（294 沉淀）
① **新增根级文件必须同步登记 `tests/test_repo_hygiene._ALLOWED_ROOT_FILES`**；② **新增 `docs/*.md` 必须同步登记 `docs/INDEX.md`**（`tests/test_docs_index` 机械守卫，漏登必红）。298 自建 297-runbook 漏登 INDEX 已补。阶段收尾须跑**更广回归**（非仅靶向文件）。

## 本链沉淀的可复用经验（节选；完整见 LESSONS.md）
- **语言是跨进程持久化共享全局状态**（290 治本）：i18n 落盘 `cache/i18n_state.json` 污染后续 pytest；`conftest.py` 的 `pytest_configure` 早于 collection 指向会话临时目录已治本；须用「投毒态」重跑证伪修复。
- gradio 6.22 `gr.update()` 返回普通 dict 非 `Update`；`from-import` 后 monkeypatch 须打**导入方**命名空间；断言「补丁生效」与「结果合法」须同时写；源码子串匹配会误命中注释须用 AST。
- **「mask 外 0 变化」≠ LaMa 恒等**：LaMa 在 mask 外固有轻微扰动（两种模式都有），crop 安全性须以「crop 干净区扰动不差于 full」为判据，而非 crop-vs-full=0。
