# 项目长期记忆 · AI 智能水印擦除工作台
> SSOT：`STAGES.md` / `LESSONS.md` / 每日日志 `YYYY-MM-DD.md`。本文件只留铁律、决策边界、当前基线、交付指针。阶段流水账看 docs/ 与 STAGES。

## 定位与合规
只处理用户自有权处理的 AI 生成图可见水印；统一表述「去除本人生成的 AI 图片水印」（《著作权法》53 条）。

## 铁律（持久）
- 阶段完成主动告知「【XX阶段开发完成…】」；禁「此处省略代码」；收到「终止项目」前持续推进。
- 分层 `ui→services→core→utils→config`，禁反向依赖；`core/services/utils` 零 gradio；`config` 不依赖 `utils.logger`。
- 探针/basetemp 用非 Temp 路径；沙箱批量删 >50 文件 → `SystemExit(1)`；归档 rename 优于删除。
- 多段编辑串行 + 重读校验；注入式反例读写 `newline=''`、sha 对 `'rb'` 原始字节算。
- **bash 双引号内禁反引号**（会被命令替换吞内容，已踩两次）；写长命令/日志一律 Write 落脚本再执行。
- 文档铁律：新增根级文件须登 `tests/test_repo_hygiene._ALLOWED_ROOT_FILES`；新增 `docs/*.md` 须登 `docs/INDEX.md`（`test_docs_index` 机械守卫，漏登必红——295/301/302 已踩三次）。阶段收尾跑更广回归，非仅靶向文件。

## 环境与网络（实测）
- **canonical venv 沙箱可用**：`C:\Users\HP\.workbuddy\binaries\python\envs\default\Scripts\python.exe`，torch 2.9.1+cu128 + CUDA 12.8 + easyocr/gradio/fitz/pytest 全齐 ⇒ real_gate / 全量 pytest / GPU 实测我都能直接跑。
- real_gate 必须用该解释器；用系统 Python 会静默降级 OpenCV 兜底致五红线全红 ⇒ `_require_runtime_deps()` 守卫（缺 torch/easyocr ⇒ EXIT=3）。
- 「CI 已绿」须以 `scripts/ci_env_probe.py` 全量确认零真红；单文件/隔离子集 RC=0 不算。
- 网络：本机 **HTTPS github.com:443 被 reset**、系统代理关闭；**SSH 22/443 可达** ⇒ push 走 SSH 或**由用户本机执行**；沙箱 HTTPS 走代理 502。PyPI 清华镜像可达。**ghproxy 下大文件频繁断连**（每段 10–37MB），需 `curl -C - --retry 80 --retry-all-errors --max-time 0` 循环续传。
- Git Bash 显式 `export PATH` 须含 `PortableGit/.../usr/bin` + `/bin` + `System32` + venv Scripts，否则 `dirname/head/ls/grep` 全 127；跨盘脚本参数会被误映射成 `D:\c\...`，探针脚本放进项目 `scripts/` 用相对路径。

## 真值基线与红线（**2026-10-22 重锚 · 阶段 313**，53 实评 = 57 标注 − 4 缺口）
mask_recall **0.7700** / precision **0.0930** / recall 0.9245 / mean_iou 0.6853 / mask_iou 0.3325 / mask_coverage **0.0162** / coverage_ratio **2.83**。
红线：recall≥0.64、precision≥0.05、mean_iou≥0.35、**mask_recall≥0.45**；崩溃护栏 mask_iou≥0.20、mask_coverage≤0.020、coverage_ratio≤3.50。`real_gate --strict-scope` EXIT=0。
- mask_recall 余量已宽（0.7700 vs 红线 0.45 ≈ **+71%**），不再是薄项；薄项仍在 over-detection 侧但已转宽：mask_coverage 余量 **19%**（红线 max 0.020）、coverage_ratio 余量 **19.1%**（红线 max 3.50），靠 258 余量体检预警盯防。硬线 0.45 不动。
- **阶段 313 取舍**：P0-4 的救援曾按「逐框中心无证据即整框填满」实现，与 docstring 声称的「只补整图返空」不一致 ⇒ 文字密集图上一次填满 23 个框（9.1% 画面）、`prompted_area_gate` 文字类覆盖率 **+48%**。窄口径化后面积回基线、`prompted_area_gate` 与 `test_mask_param_calibration` 转绿，代价 committed 面 mask_recall 0.7809→0.7700。若将来发现「框内有部分证据但中心未覆盖」的真实静默漏检，应加**救援预算上限**（单图 ≤2% 画面），不要退回逐框整填。
- 图像级缺口：`WATERMARK_KNOWN_GAP_IMAGES={a1..a4}`（大宝石角标像素域不可分）；`test_known_gap_images243.py` 4 例绿。
- 能力声明：`WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。
- 重锚须 `--update --force-update` 双开（单独 `--force-update` 只判决不落盘）。**2026-10-19 已获授权双开执行**：baseline 由 0.5960→0.7700（P0-3）→**0.7809**（P0-4 救援式自播种，终值）落盘；不带 `--update` 复跑确认零漂移、全绿（RG_DRIFT_RC=0）。

## 门禁家族
`quality_gate` + `real_gate`(30图) + `e2e_real_probe`(10图×4类) + `mask_param_audit` + `clean_bench_probe` + `prompted_area_gate` + `perf_probe` + `docker_consistency_check` + `crop_quality_probe`。退出码 0/1/2（real_gate +3=环境错误）；`--update/--refresh` 须接 `baseline_guard`；脚本 print 非 GBK 须 `force_utf8_stdio()`。

## 性能归因（GPU=RTX 5060 Laptop sm_120 / 7.93GiB / torch 2.9.1+cu128）
- **GPU 真可用（305 更正）**：「沙箱/本机是 CPU torch」是**伪前提**——canonical venv 实为 torch 2.9.1+cu128、RTX 5060 Laptop sm_120 / 7.93GiB、EasyOCR 与 LaMa 均真在 cuda:0。判据用**真实 CUDA matmul 与 CPU 参考比对**（max|Δ|=7.2e-05），不信 `is_available()` 布尔自证。
- **GPU↔CPU 严格 A/B**（同 12 张 ≥2048px 图、仅 `WME_DEVICE_MODE=cpu` 切换）：detect P95 GPU 1.282s vs CPU 6.823s（**5.3×**）、full P95 10.45s vs 22.68s（2.2×）⇒ 1.5s 目标在 CPU 上结构性不可达。
- **权威口径取 `perf_probe`**：detect 阶段 GPU P95 **0.743s**（基线 0.679s×1.3=0.882s 护栏内、目标 1.5s 达标、噪声 0.041、无渐变）退出码 0；**full 阶段 P95 8.397s**（307 标定，`perf_baseline_gpu_default.json`，噪声 0.067、无渐变、超分 OFF）⇒ 1.5s 目标对 full 结构性不可达，须按 GPU 口径且对 full 另行放宽。两阶段写入**不同文件**互不覆盖（CI `run_all_checks` 默认跑 detect）。
- ⚠️ **三处口径不可混用**：自定义 `gpu_perf_probe`（≥2048px 最重子集+冷启动）1.282s ≠ 权威 `perf_probe`（固定 10 图、repeat=3 取 min）0.743s；full 的 8.397/8.891/10.45 三值跨口径不可比，结论一律取 `perf_probe` 的 8.397s。
- ⚠️ `use_fp16()` 返回 True **不代表本次推理用了 fp16**：LaMa 在 `_FP16_UNSAFE_MODELS` 内，model_manager 强制 float32（阶段 207，fp16 实测毁图）；`_run_lama_tile` 以模型 dtype 为唯一真值源。
- 瓶颈=LaMa 在大包围盒上的推理（recommend+detect 仅 ~1.85s）；full P95 结构地板≈4.4s。
- **裁剪修复 `INFER_CROP_TO_MASK`（默认开）**：2048² 7.3s→2.1s（3.6x）；只贴回 mask 区 ⇒ mask 外与 full 逐字节一致；mask 内为 LaMa 尺寸相关差异（非无损）。`crop_quality_probe` 58 张 ≥2048 图干净区 **58/58 通过**。fp16 禁（毁图）、resize 有损、固定网格分块 0.9x 更慢，均排除。
- `recommend_preset` 不调 detect、不调 OCR（纯 numpy/cv2）；real_gate 用显式 `--presets`，红线不受 recommend 影响。
- **角标判据 `_corner_watermark_signal`**：9 张真值边缘图模板库（base64 自包含）+ NCC（`RECOMMEND_CORNER_WATERMARK_NCC_MIN=0.30`）→ 右下/中右角标导向 `ai_logo`、跳过 OCR；召回 48%→76%（24/50→38/50），留出集 54%（31/57），自然图零误命中。坑：①模板与查询同灰度口径（cv2 RGB2GRAY；PIL mean 会让 NCC 0.7→0.45）；②必须走 `_require_cv2()`（NameError 被 except 吞成空库）；③合成实体复刻不了细粒度边缘 ⇒ 单测 monkeypatch 验分支、真实召回交 `scripts/verify_corner_reco.py`；④`verify_corner_reco`「非GT误命中=1」实为 c12.png（=c11 字节重复）。SSOT：`utils/recommend_view._FEATURE_KEYS` + `utils/i18n.py` 双语同序。守卫 `test_corner_watermark_signal.py`（9 例）。
- **API 同步路径并发实测（306，RTX 5060 真机）**：worker=1/2/4/8 吞吐 0.132/0.257/**0.331**/0.287 img/s，P95 12.6/**10.2**/14.9/40.1s，峰值显存（worker=8）6543MB≈**80.5%** 卡容。吞吐甜点=4、尾延迟甜点=2；默认 `API_MAX_CONCURRENCY=4`（HTTP 闸门）与 `API_ASYNC_MAX_WORKERS=2`（异步作业池）验证合理、**无需改动**。⚠️ 并发甜点/显存上限**负载相关**：257 在 1200×900 PDF 测得「k=4 仅 16% 卡容」，本实测在 2048² 整帧水印下 worker=8 达 80.5%（同并发数显存差 5×），不可外推；worker=8 在重负载下是脚枪，仅标注警告、不收缩 [1,8]。

## 已收口的关键判定
- **is_grid 误触发（docs/301，2026-10-18）**：doubao JPEG 图残差场 lag 8px 强自相关（0.19–0.57 vs 阈 0.09）= JPEG 8×8 DCT 块伪周期；修 `DETECT_GRID_MIN_PERIOD` 6→16（杠杆化）。效果：误触发 10/10→1/10；ai_grid 面积 mean 8.88%→2.43%、区域 747→42.8、余量 29%→80%；general 红线逐位不变；合成网格零漏检。残留 1 张 63px 为内容真周期（沙纹），属数据缺口。
- **EVIDENCE_AND 双刃剑**：合成 gradient 上是防洪闸（关掉洪水，精度 1.00→0.10、覆盖 1.2%→12.2%）；真实图侧会碎片化推高覆盖。⇒ **不删通路**；夹具用 gradient + 撤 xfail。ai_grid 未承诺 + 15% 闸 + 80% 余量，暂缓深挖。
- **半透明通路不可收（296）**：关掉 precision 0.079→0.26 但 mask_recall 0.511→0.4355 破线 ⇒ 提盒精度必蚀掩码召回，无免费午餐。
- **S2 过检收敛**：FP 主体 translucent 77.9%；`DETECT_TRANSLUCENT_SENSITIVITY` 是唯一有效杠杆（0.55→0.50）⇒ mask_recall −0.4%、precision +5.7%、coverage_ratio −6.9%。`merge_dilate`/`MIN_DELTA` 非杠杆。
- **颜色匹配 `INPAINT_COLOR_MATCH` 默认 False**：e2e 对比开着拖累去痕率（grid 72.6%→49.8%）；温和变体仍回归 ⇒ 「匹配到邻环」思路根本局限（邻环≠真背景），换思路才可开。
- 超分 2026-10-08 关闭；easyocr 降级 10-04 已还原（cu128 wheel 2862MB）。

## 接口契约（101）
`detect_watermark_mask(image: np.ndarray, *, preset=...) -> DetectionResult`：不吃路径；收 RGB/RGBA/灰度；`res.mask` uint8 0/255；`regions[].x/y/w/h` 为 `np.int32`（写 JSON 前 `int()`）；9 种格式、不递归、`MAX_IMAGE_PIXELS=80M` 超限抛异常。

## API 侧
只读端点 capabilities/models/models.active/models.verify 已补齐；推理端点绝不豁免（fail-closed）。异步作业 + worker 池 `API_ASYNC_MAX_WORKERS` 默认 2（实测 4 反降）。**仅 `POST /api/models/active` 不开放**。
- **安全层（299）**：`services/api_security.py` 纯标准库 token + 令牌桶；`API_AUTH_ENABLED`(false 默认，零行为变更)/`API_TOKEN`/`API_RATE_LIMIT_PER_MIN`(60)/`API_RATE_BURST`(10)；安全模式 POST 与作业下载需 token，只读 GET 豁免鉴权仍限流；`start_server` fail-closed。守卫 `test_api_security299.py` 12 例。

## 发布与版本（当前 v1.0.2）
- 远端 `github.com/chu803/wme`（`main` 已推）；CI `ci.yml` → `run_all_checks.py`；发布 `.github/workflows/publish.yml`（tag `v*` → 门禁 → build → PyPI，**当前走 `PYPI_API_TOKEN` Secret**，与 Publish #3/#5 同款已验证可发版）。**push 两条可行路**：①沙箱内 `git push` 必 502（走代理）；②`dangerouslyDisableSandbox`（escalated）放行后真实网络可推（实测 `0cb20e3` 一次过，`ef64fd7..0cb20e3 main->main`）；③用户本机终端亦可。⚠ **沙箱会丢弃 `.git/refs` 下「新建子目录」的写入**：`git update-ref refs/remotes/origin/main <sha>` 返回 0 但不落盘、`git fetch` 打印 `[new branch] main -> origin/main` 后 `refs/remotes` 仍空 ⇒ 表现为 `main...origin/main [gone]`。修法：用 Python `os.makedirs(.git/refs/remotes/origin)` + 写 `main` 文件（SHA 内容）再 `git branch --set-upstream-to=origin/main main`，即恢复 `## main...origin/main`。。OIDC/Trusted Publisher 因用户 PyPI 网页登录受阻（新设备确认邮件循环 20+ 分钟）**暂缓**，待其能正常登录 PyPI 再切（届时 publish.yml 改 `id-token:write` + 删 password，且需在 PyPI 项目页配 Trusted Publisher）。
- **PyPI `wme-watermark-eraser 1.0.0` 已于 2026-10-15 10:55 发布**（Publish #3 绿，源提交 `06b3d13`）；PyPI 不允许覆盖同版本。
- **1.0.1（2026-10-18，`c98dd32`）**：版本升 1.0.1（`config.APP_VERSION` 真值源 + pyproject + main.py + core/services/ui/utils `__version__`）；CHANGELOG 记 1.0.0 后的收口（裁剪修复默认开、is_grid 抗 JPEG 块、角标 NCC、#450 空 ROI 修复、MANIFEST.in）；`v1.0.1` tag。
- **溯源偏差（诚实记录）**：tag `v1.0.0` 指向 `0be2209`，PyPI 1.0.0 产物由 `06b3d13` 构建，两者不重合；不回指 tag（回指会再触发必失败的 Publish 运行）。需复现 1.0.0 就 checkout `06b3d13`。
- **1.0.2（2026-10-18，`ef64fd7`）**：元数据收口（`[project.urls]` + 分类器 7→13 条）+ 版本升 1.0.2；发布管线沿用 `PYPI_API_TOKEN`（OIDC 因账号登录受阻回退）。⚠️ **打 tag 必先同步升版本号**：v1.0.2 首次误指向 1.0.1 代码（`4d6acdb`），致 Publish #6 在 PyPI 重复上传失败；已 `git tag -f v1.0.2` 重指 `ef64fd7` 并 force-push。✅ **Publish #7 成功上架 PyPI**（2026-10-18T05:00Z，wheel 721663B + sdist 857147B），13 条分类器已生效、`info.version=1.0.2` 经 PyPI JSON 核实。
- 历史重写（205）：`models/big-lama.pt` 已移出全部历史 ⇒ 单点故障，重建 SOP 见 `docs/302-资产重建清单.md`（下载源/205,669,692B 校验/循环续传/备份建议）；`benchmark_real/images` ≈280MB 已授权入库（不走 LFS）。
- Docker（C5）：本机无 docker CLI，镜像从未真 build（仅静态契约 + 一致性门禁）；`Dockerfile.gpu` P0 已修 python/torch 错配。

## 健康度与战略
源码 ~33.2k + 测试 ~22.5k；**2504 用例 / 219 文件**（沙箱 14 FAILED 全为删除护栏 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 假红，隔离全过、父提交同败、非回归；正常机器预期全绿）；45 模块全审计（276–290 收官）。
技术内核强、商品外壳缺。P1 数据质量 → P2 产品化性能 → P3 生态。护城河=全本地离线 + 免手涂 8 预设 + 图/PDF/视频全源 + 合规定位。记分卡 `docs/quality/product_scorecard.json`：overall **B-** / eng **A** / core **B-** / prod **C+** / eco **D**。
- ⚠️ **cu121→cu128 声明对齐早已完成（305 复核证伪）**：此前「声明文件钉 cu121/torch 2.3.1、待处理」是**误记**。`requirements-gpu.txt` 与 `requirements.txt` 已同钉 `torch==2.9.1`，前者走 `--index-url .../cu128`；`Dockerfile.gpu` 基础镜像 `nvidia/cuda:12.8.1-runtime-ubuntu22.04`；`docker_consistency_check.py` 的 **E2（CUDA 12.8 ↔ cu128 匹配）+ W1（torch 2.9.1 一致）全绿** ⇒ 阶段 207 已收口，声明与运行环境一致，新人按声明装即 cu128。
- **剩余开放项**（截至 307，wme 侧自主可推进工作已彻底耗尽）：① ✅ full 阶段 perf 基线标定（307，RTX 5060 真机 P95 8.397s 落盘 `perf_baseline_gpu_default.json`，超分 OFF）；② ✅ API 侧并发实测（306，真机 RTX 5060 验证默认 `API_MAX_CONCURRENCY=4`/`API_ASYNC_MAX_WORKERS=2` 合理，无需改动）；③ C5 真 build（需 docker CLI，本机缺失）。注：D1 GPU、cu121 声明、API 并发三项此前系误判阻塞，305/306 已证伪收口。
- **316 实战演练新增待决项**（出处 `docs/316-实战演练-混合批量一个文件夹吞到底.md` §六；均**需产品拍板，不属可自主推进**）：
  A 后处理全图作用 ⇒ 真实原分辨率图上「水印外改动 46.9%–88.4%」（314b 同源，本次首次量化）；
  B 无参考质量分对「水印没动手」无感（a2/a3 bbox 差 1.06/0.74 仍得 100·good）；
  C 内容非 PDF 的 `.pdf` 被 MuPDF 静默按图片当 1 页处理，成功但无提示；
  D 预设置信度 0.01、触发特征「无」仍修复 12228px（阈值牵 real_gate 红线，动它需重锚授权）；
  E 输出命名不一致（图 `X_cleaned_HHMMSS.png` vs 媒体 `X_已去水印.pdf`）；
  F JPEG 输入默认输出 PNG，单图体积 ×4.65、整批 ×1.35。
  ✅ 同期缺陷「批量有失败仍退 0」已在 316 修复（`cli._batch_exit_code` 0/1/2 + 4 例守卫）。
- **317 修复侧真值基线**（`scripts/removal_truth_probe.py`，15 张真实无水印照片 × 4 种水印，像素级真值）：
  gain 中位 edge_text **0.939** / corner_logo 0.532 / faint_tiled **−0.117** / grid **−0.172**；
  误伤 33.8%–72.7% **与预设无关**；仅 16/60 gain>0.7。**第一版无红线，此数字即历史基线。**
  新开放项（比 A–F 更靠产品命门）：G `ai_grid` 通路在真实纹理上检回 0.001~0.204（11 例正确推荐仍全负）；
  H `ai_faint_tiled` 在真实照片上 **0/15 可达**；I 真值集 57 标注 style **100% corner_logo**，
  角标仅 5/15 被推成 `ai_logo`（主打场景推荐器未学会，现靠 text_heavy 误打误着）。
  G/H/I 均牵检测与红线，属"先出判决再定重锚"流程。
  **判决补充（10-23 实测）**：① 已**否证**「给 grid_on 加 regularity 门槛」——真照片 regularity 0.000–0.001，
  但合成点阵也只有 0.001–0.003（靠 grid 分 0.39–0.79 判别）⇒ 会打死合成通路且零区分力；
  ② 角标信号的根因是**风格覆盖**：NCC 模板库=9 张豆包图 + 白底板先验（V≥140/S≤80），
  深色圆角板+白字两条都不命中（corner 信号二值 0/1，5/15 命中，其余落到 text 分支误打误着）；
  ③ 冷启动消融：OCR 只被文字/角标通路需要（9/12 抽样 True），点阵通路 3/3 不需要。
  **三个待授权项（B/C/D 口径）**：B 冷启动延迟初始化（CUDA+OCR ≈4s/7.2s 白付；牵 detector/inpainter 时机）；
  C 角标样式覆盖（扩模板库/加深色板先验，牵检测红线）；D 掩码 +33%（主因图层抗锯齿被画笔阈值 10 收进，
  丢 alpha 本身 1.00×，缩放往返 1.07–1.11×；改阈值牵 314b 结论）。
- **318 已落地（默认关）**：`POSTPROCESS_PRESERVE_UNTOUCHED` + `BAND_PX=8` ⇒「掩码+带之外逐字节还原」。
  A/B（60 用例只翻开关）：误伤中位 **50% → 0.8–2.1%**、**0/60 gain 变差**、**60/60 ΔPSNR 改善 2.2–3.7 dB**；
  `faint_tiled`/`grid` 由净伤害转为 ~0（检回未变 ⇒ 是"停止帮倒忙"不是"擦掉了"）；
  同进程交替耗时 **0.99×**（跨会话绝对耗时不可比——今晨整机比昨晚慢 2.9×）。
  残余 0–16.5% 落在**检测误报区**，归检测侧。
  **下一步（待用户定）**：318b = CLI `--preserve-untouched` + 设置页 + REST，按 315 对称性守卫推进；
  默认值翻不翻属观感承诺，本阶段刻意未动。
- **318b 已完成（CLI/api/REST 三面；UI 未做）**：三态语义 `None`=跟随 config / 显式 True|False=逐请求覆盖；
  api 6 入口 + `get_capabilities` + REST 白名单 + CLI 三态全接；守卫 13 例用「替换下游+捕获实参」
  证明真的下传（含 PDF/视频真样本逐页逐帧）。
- **318c 已完成**：设置页 → 处理偏好新增全局默认复选框「只动水印区」，应用即生效（`postprocess()` 每次读 config），
  可存 `user_overrides`（**两份清单都要登记**，只加一份会被 `test_config_parse` 抓红）。
  **真开界面点过**：截图 → 勾选 → 应用 → 文案回「只动水印区：开启」（原文案漏报该项，已补）。
  **默认仍为关**；4 个页签的逐次开关未做（需 `RepairParamHandles` 扩展 + 位置参数接线，独立阶段）。
- **320 首次体验已做**：随包示例图（640² 真照片+角标，已挪 `ui/assets/` 并补 package-data，
  真打 wheel 验证在包内）+ 自动页「试一张示例」按钮；真机走通 示例→检测 6.3%→一键修复成功。
  实测：ImageEditor **不接受「background+空 layers」预填**（composite 填图也无效）⇒ 示例只填上传框。
  **新开放项**：掩码经浏览器往返会变胖——用户没画，编辑框却比检测多 **+33%**（8,454px），
  314b 同现象 2,949px@2048²；拆解后主因是**图层边缘抗锯齿**被画笔阈值 10 收进（丢 alpha 本身 1.00×、
  缩放往返仅 1.07–1.11×）；方向安全但文案「你补画」不诚实，需单独阶段改（牵 314b 结论）。
- **319 效率实测（同会话对照）**：首图冷启动 **7.23s**——import 仅 0.4s（torch 懒加载 2.0s），
  成本在 CUDA ≈2s + EasyOCR ≈2s，**纯 OpenCV 引擎也要付**；视频 `--frame-step 5` **1.82×**
  （37.3→20.5s，改动量 2.79 vs 2.77）；输出体积 PNG **4.65×** 输入 / JPG 1.13× / WebP **1.00×**。
  已落地两条**只提示不改默认**（`cli.size_hint` / `cli.frame_step_hint` + 9 例守卫含接线断言）。
  待拍板：A 输出格式跟随输入、B 视频步长做成页签选项、C 设备/OCR 延迟初始化
  （≈省半冷启动；需先消融「哪些预设必须 OCR」，牵 detector/inpainter 初始化时机）。
- **已否证的假设（勿重试）**：给 `grid_on` 加 `regularity` 门槛以挡真实照片误推 `ai_grid`。
  实测：15 张真照片 regularity 0.000–0.001，**合成点阵也只有 0.001–0.003**
  （grid 分 0.39–0.79 才是它们的判别依据）⇒ 加门槛会打死合成点阵通路，且对真照片零区分力。
  真缺口是**风格覆盖面**：角标模板来自 9 张豆包图 + 白底板先验（V≥140/S≤80），
  深色圆角板+白字两条都不命中，落到 `text` 分支误打误着（corner 信号二值 0/1，5/15 命中）。

## 复盘教训（非显然项）
- **语言是跨进程持久全局状态**（290 治本 / 304 投毒态实证收口）：i18n 落盘 `cache/i18n_state.json` 污染后续 pytest；`conftest.pytest_configure` 指向会话临时目录治本；304 用「投毒真实文件 en-US → 拉起全新 pytest 子进程 → 断言 `get_language()==zh-CN`」做投毒态重跑证伪，反例闭合确认毒确实写入；并加守卫 `tests/test_i18n_state_leak_guard.py` + 注入式反证 `scripts/_i18n_leak_inject_proof.py`（`WME_I18N_NO_REDIRECT=1` 复现修复前，守卫必 FAIL）。**已收口**，正常跑零副作用。
- **logging 单例污染**：`test_utils_audit90` rotation 用例受前序 handler/真实 `logs/app.log` 污染（既有顺序污染，父提交同样失败）；修复须 conftest 级重置 + 每用例 tmp 日志目录。
- 「mask 外 0 变化」≠ LaMa 恒等（两种模式都有固有扰动）⇒ crop 安全性以「crop 干净区扰动不差于 full」判，非 crop-vs-full=0。
- gradio `gr.update()` 返回普通 dict；`from-import` 后 monkeypatch 须打导入方命名空间；源码子串匹配会误命中注释，须 AST。
