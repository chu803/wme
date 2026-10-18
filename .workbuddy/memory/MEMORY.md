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

## 真值基线与红线（2026-10-17 重锚，53 实评 = 57 标注 − 4 缺口）
mask_recall **0.5960** / precision 0.0801 / recall 0.9057 / mean_iou 0.6638 / mask_iou 0.3217 / mask_coverage 0.0129 / coverage_ratio 2.30。
红线：recall≥0.64、precision≥0.05、mean_iou≥0.35、**mask_recall≥0.45**；崩溃护栏 mask_iou≥0.20、mask_coverage≤0.020、coverage_ratio≤3.50。`real_gate --strict-scope` EXIT=0。
- mask_recall 仍最薄（余量 ~24.8%），靠 259 advisory 盯防；硬线 0.45 不动。
- 图像级缺口：`WATERMARK_KNOWN_GAP_IMAGES={a1..a4}`（大宝石角标像素域不可分）；`test_known_gap_images243.py` 4 例绿。
- 能力声明：`WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。
- 重锚须 `--update --force-update` 双开（单独 `--force-update` 只判决不落盘）。

## 门禁家族
`quality_gate` + `real_gate`(30图) + `e2e_real_probe`(10图×4类) + `mask_param_audit` + `clean_bench_probe` + `prompted_area_gate` + `perf_probe` + `docker_consistency_check` + `crop_quality_probe`。退出码 0/1/2（real_gate +3=环境错误）；`--update/--refresh` 须接 `baseline_guard`；脚本 print 非 GBK 须 `force_utf8_stdio()`。

## 性能归因
- detect P95 **0.776s**（<1.5s 达标）；full P95 **8.585s**（superres=off，结构地板≈4.4s ⇒ 1.5s 对 full 不可达）。
- 瓶颈=LaMa fp32 @2048² ≈7–8s（recommend+detect 仅 ~1.85s）。
- **裁剪修复 `INFER_CROP_TO_MASK`（默认开）**：2048² 7.3s→2.1s（3.6x）；只贴回 mask 区 ⇒ mask 外与 full 逐字节一致；mask 内为 LaMa 尺寸相关差异（非无损）。`crop_quality_probe` 58 张 ≥2048 图干净区 **58/58 通过**。fp16 禁（毁图）、resize 有损、固定网格分块 0.9x 更慢，均排除。
- `recommend_preset` 不调 detect、不调 OCR（纯 numpy/cv2）；real_gate 用显式 `--presets`，红线不受 recommend 影响。
- **角标判据 `_corner_watermark_signal`**：9 张真值边缘图模板库（base64 自包含）+ NCC（`RECOMMEND_CORNER_WATERMARK_NCC_MIN=0.30`）→ 右下/中右角标导向 `ai_logo`、跳过 OCR；召回 48%→76%（24/50→38/50），留出集 54%（31/57），自然图零误命中。坑：①模板与查询同灰度口径（cv2 RGB2GRAY；PIL mean 会让 NCC 0.7→0.45）；②必须走 `_require_cv2()`（NameError 被 except 吞成空库）；③合成实体复刻不了细粒度边缘 ⇒ 单测 monkeypatch 验分支、真实召回交 `scripts/verify_corner_reco.py`；④`verify_corner_reco`「非GT误命中=1」实为 c12.png（=c11 字节重复）。SSOT：`utils/recommend_view._FEATURE_KEYS` + `utils/i18n.py` 双语同序。守卫 `test_corner_watermark_signal.py`（9 例）。

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

## 复盘教训（非显然项）
- **语言是跨进程持久全局状态**（290）：i18n 落盘 `cache/i18n_state.json` 污染后续 pytest；`conftest.pytest_configure` 指向会话临时目录治本，须用「投毒态」重跑证伪。
- **logging 单例污染**：`test_utils_audit90` rotation 用例受前序 handler/真实 `logs/app.log` 污染（既有顺序污染，父提交同样失败）；修复须 conftest 级重置 + 每用例 tmp 日志目录。
- 「mask 外 0 变化」≠ LaMa 恒等（两种模式都有固有扰动）⇒ crop 安全性以「crop 干净区扰动不差于 full」判，非 crop-vs-full=0。
- gradio `gr.update()` 返回普通 dict；`from-import` 后 monkeypatch 须打导入方命名空间；源码子串匹配会误命中注释，须 AST。
