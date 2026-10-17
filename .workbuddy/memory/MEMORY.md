# 项目长期记忆 · AI 智能水印擦除工作台
> SSOT：STAGES.md / LESSONS.md / 每日日志 `YYYY-MM-DD.md`。本文件只留索引、铁律、决策级边界、进度指针；阶段流水账见 docs/291-300 与 STAGES.md / LESSONS.md。

## 定位与合规
只处理用户自有权处理的 AI 生成图片可见水印；统一「去除本人生成的 AI 图片水印」表述（《著作权法》53 条）。

## 项目铁律（持久）
- 阶段完成主动告知「【XX阶段开发完成…】」；禁止「此处省略代码」；收到「终止项目」前持续推进。
- 分层 `ui→services→core→utils→config`，严禁反向依赖；`core/services/utils` 零 gradio；`config` 不依赖 `utils.logger`。
- 探针/basetemp 用非 Temp 路径；沙箱批量删 >50 文件 → `SystemExit(1)`（两段式受限 + 会话闩锁，归档 rename 优于删除）。
- 多段编辑须串行 + 重读校验；注入式反例读写 `newline=''`、sha 对原始字节（`'rb'`）计算。
- 网络（2026-10-17 实测修正，原记「TCP:443 可达」有误）：本机 **HTTPS github.com:443 被 reset/不可达**、系统代理关闭（ProxyEnable=0，无 ProxyServer/PAC）；但 **SSH 通路可达**（github.com:22 与 ssh.github.com:443 实测 OK）⇒ **push 走 SSH**：远端改 `git@github.com:chu803/wme.git`，需本机 ssh key 已加 GitHub；本机原无 `~/.ssh`。沙箱侧 HTTPS（代理 502）与直连均不通、`reg.exe` 被安全策略禁。PyPI 清华镜像可达。

## 沙箱能力更正（2026-10-16 关键）
- **canonical venv 在沙箱内可用**，绝对路径 `C:\Users\HP\.workbuddy\binaries\python\envs\default\Scripts\python.exe`：torch 2.9.1+cu128 + **CUDA 12.8 可用** + easyocr/gradio/fitz/pytest 全齐。之前错判沙箱无此 venv（实际是命令里写成了项目内相对路径）。
- 推论：**real_gate / run_real_benchmark / 全量 pytest / S2 过检收敛我都能在沙箱直接跑**（带 GPU），不再受"需你本机"限制。仅 `git push` 远端因网络策略须你本机。
- real_gate 必须跑 canonical venv；用错解释器（如系统 Python 3.11）会静默降级 OpenCV 兜底致五红线全红 ⇒ `_require_runtime_deps()` 守卫（缺 torch/easyocr ⇒ EXIT=3）。
- 「CI 已绿」必须以 `ci_env_probe.py` 全量、确认零真红为准；单文件/隔离子集 RC=0 不能断言 CI 绿。

## 关键边界 · 真值审计
- 真实基准（53 实评 = 57 标注 − 4 缺口，**S2 收紧后已重锚 2026-10-17**）：mask_recall **0.5960** / precision 0.0801 / recall 0.9057 / mean_iou 0.6638 / mask_iou 0.3217 / mask_coverage 0.0129 / coverage_ratio 2.30。红线 recall≥0.64 / precision≥0.05 / mean_iou≥0.35 / **mask_recall≥0.45** + 崩溃护栏 mask_iou≥**0.20** / mask_coverage≤0.020 / coverage_ratio≤3.50。`real_gate --strict-scope` EXIT=0（S2 基线同口径：0.5984→0.5960 仅因 `DETECT_TRANSLUCENT_SENSITIVITY` 0.55→0.50）。
- 图像级缺口（243）：`WATERMARK_KNOWN_GAP_IMAGES={a1,a2,a3,a4}`（大颗宝石角标）像素域不可分 ⇒ 收窄承诺。守卫 `test_known_gap_images243.py` 4 例全绿。
- 能力声明：`WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。
- **制裁尺度（242）**：mask_recall 硬线 0.45 不动，靠 259 advisory 余量监控（~24.8% 余量）盯防。

## 门禁家族（持久契约）
quality_gate + real_gate(30图) + e2e_real_probe(10图×4类) + mask_param_audit + clean_bench_probe + prompted_area_gate + perf_probe + docker_consistency_check + **crop_quality_probe（2048² 裁剪判决）**。退出码 0/1/2（+ real_gate 退码3=环境错误）。每个 `--update/--refresh` 须接 `baseline_guard`。脚本 print 非 GBK 须 `force_utf8_stdio()`。

## 性能归因链（含裁剪优化结论）
- detect 真实 P95=**0.776s**（<1.5s 达标）；full P95=**8.585s**（superres=off，结构性地板≈4.4s ⇒ 1.5s 对 full 不可达）。torch 2.9.1+cu128 + big-lama.pt。
- **瓶颈=LaMa fp32 在 2048² 上 ~7-8s**（recommend+detect 仅 ~1.85s，非瓶颈）。
- **裁剪到水印包围盒修复（INFER_CROP_TO_MASK，默认开）**：2048² 上 7.3s→2.1s（**3.6x**）；只贴回 mask 区 ⇒ mask 外与 full **逐字节一致**；mask 内为 LaMa 尺寸相关行为差异（非无损权衡）。`crop_quality_probe` 全量 58 张 ≥2048 图：**干净区安全 58/58 通过**、速度中位 1.51x / 2048² ~3.2x。fp16 仍禁（207 毁图）、resize 有损、固定网格分块实测 0.9x（更慢）已排除。
- **detect/recommend 成本拆分（#450）**：`recommend_preset` **不调用** `detect_watermark_mask`、不调 OCR（仅 numpy+cv2 轻量特征）；`real_gate` 用显式 `--presets` 故红线基线**不受** recommend 影响。recommend 分布 text_heavy×30 / multi×11 / grid×5 / general×2 / ai_logo×0。
- **#450 修复（用户授权「改 recommender 判据」）**：右下/中右角标图导向 `ai_logo`（edge=False）跳过高危 OCR 空跑；**改用边缘图 NCC 模板匹配** `core/preset_recommender._corner_watermark_signal`，**9 张真值图 ROI 边缘图构成模板库**（base64 PNG 内嵌自包含）。标定 `RECOMMEND_CORNER_WATERMARK_NCC_MIN=0.30`：精确率 100%（自然图 0 误命中）、右下角标召回 48%→76%（24/50→38/50）、留出集召回 54%（31/57）。fail-closed 可逆杠杆：`RECOMMEND_CORNER_WATERMARK_ENABLED`(True)/`_NCC_MIN`(0.30)/`_MIN_EDGE`(5.0)。**关键坑**：①模板与查询须同灰度口径（cv2 `COLOR_RGB2GRAY`，PIL `mean` 建模板会让 NCC 从 ~0.7 掉到 ~0.45）；②`_load_corner_watermark_templates` 必须走 `_require_cv2()`（NameError 被 except 吞成空库 ⇒ 信号恒 0）；③合成实体无法复刻模板细粒度边缘做 NCC ⇒ 单测用 monkeypatch 隔离验决策分支、真实召回交 `scripts/verify_corner_reco.py`；④`verify_corner_reco`「非GT误命中=1」= c12.png（=c11 字节重复图）id 映射漏标，非真自然图误命中。**I3 SSOT 同步**：scores 键 `corner_watermark` ⇒ `utils/recommend_view._FEATURE_KEYS` + `utils/i18n.py` 双语同序补齐。守卫 `tests/test_corner_watermark_signal.py`（9 例）；探针 `probe_recommend_dist.py`/`verify_corner_reco.py` 登记 `NOT_REGISTERED_BY_DESIGN`。

## 项目健康度
源码 ~33.2k + 测试 ~22.5k；实测 **2504 用例 / 219 文件**（2026-10-16 全量 canonical venv：**14 FAILED**，余 passed / ~10 env-skip；**14 失败 100% 为沙箱安全删除护栏 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 拦截 `os.remove` 所致的环境假红（含 12 文件删除类 + 2 个 audit90 rotation 用例），隔离单独跑全过、父提交同败 ⇒ 早于 #450、非回归**；正常机器预期全绿）；**45 模块全审计**（5j 续审链 276-290 收官）；torch 2.9.1+cu128（RTX 5060 Laptop 8151MiB）+ big-lama.pt。

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
只读端点 `/api/capabilities`/`/api/models`/`/api/models/active`/`/api/models/verify` 已补齐（251）；推理端点绝不豁免（199 fail-closed）。**255 异步作业收口**（11 例全绿）。**257 D1 worker 池** `config.API_ASYNC_MAX_WORKERS` 默认 2（[1,8]）；RTX 5060 实测 worker=4 吞吐反降+尾延迟翻倍 ⇒ 默认 2 为甜点。**仅 `POST /api/models/active` 仍不开放**。
- **API 安全层（299）**：`services/api_security.py` 纯标准库 token 鉴权 + 令牌桶限流；`API_AUTH_ENABLED`(默认false，零行为变更)/`API_TOKEN`/`API_RATE_LIMIT_PER_MIN`(60)/`API_RATE_BURST`(10)；安全模式 POST + 作业文件下载需 token（401），只读 GET 豁免鉴权仍受限流；start_server fail-closed。守卫 `tests/test_api_security299.py` 12 例全绿。

## 决策级边界（已收口）
- A4（2026-10-04）：a1-a4 归 corner_logo 非全图渐变；`ai_gradient` 仍为真·无真实图缺口。
- ① 超分 2026-10-08 关闭（`user_overrides` → `ENABLED=false/SCALE=4/BACKEND=auto`）。
- 环境事故（2026-10-04）：easyocr 降级已还原（cu128 wheel 2862MB）。

## 发布工程（296-298，指针）
- **296 决定性否证**：半透明通路在 25 门禁图产 190 个 FP 盒；关掉 precision 0.079→0.26(3.3×) 但 mask_recall 0.511→0.4355 **破 0.45 红线** ⇒ 提盒精度必蚀掩码召回，无免费午餐；当前 (0.65,0.50) 为校准平衡。
- **297-298 发布管线就绪**：pyproject `[project.scripts]` 三件套（wme/cli/api）+ AST 守卫 + 真 wheel 构建验证；`.github/workflows/publish.yml`（tag v* → 门禁 → build → PyPI，Secret PYPI_API_TOKEN）+ `docs/297-runbook` + `docs/298-评估`。**待用户 push + 配 token + 打 tag v1.0.0 即上 PyPI**。
- **2026-10-17 预检结论（S2 收官后）**：管线三件套结构正确、`docker_consistency_check` 全绿、pyproject 三件套齐备。跑与发布同款门禁 `run_all_checks.py`（无 --gates）初见 **6 failed**；其中 **3 个是我方责任已修**：①S2 重锚的镜像常量 `MSER_OFF`(test_detector_audit100) + `CONVERGED`(test_overdetection99) 同步到新基线（文件注释 line 88 授权的「合法连带损伤」）；②`test_repo_hygiene` 被我自己的 `tee` 探针残留 `.pytest_runall_publish_preflight.log` 触发，已删。剩余 2 个预存、非 S2 回归：`test_utils_audit90` 2 例 rotation（沙箱 logging 单例顺序污染，隔离跑即绿、干净 CI 必绿）。**⇒ 原判「唯一真阻塞=网格 EVIDENCE_AND 陈旧测试」经探针复核是误诊，已纠正（见下条）。**
- **⚠ 2026-10-17 EVIDENCE_AND 误诊纠正 + 真实图反转（双刃剑，勿简单删/留）**：`test_grid_evidence_and_lowers_false_coverage` 早期用 textured(seed=7) 只见 on==off 精确率均 100%，误判「冗余可删」并 xfail。① **合成夹具侧**：探针扫 3 底图×8 seed——textured/photo 上仅略降召回；**gradient 上是洪水闸**，关后折叠模板伪周期铺满全图（seed=3：精确率 1.00→0.10、覆盖 1.2%→12.2%）。据此夹具改 gradient(seed=3)、撤 xfail（6 passed；注入摘闸→on==off==10% FAIL，detector.py 净 diff 空）。② **真实图侧（同日再探针，方向相反）**：10 张 doubao 真实图上 EVIDENCE_AND **开着反而覆盖率翻倍**（mean 11.29% vs 关 5.60%）、区域数 658 vs 42——交集把周期模板打碎成大量小碎片，覆盖率裁剪「保留小区域」又将碎片全留+膨胀填充 → 反被推高（多张顶到检测器 15% 裁剪闸）。⇒ **EVIDENCE_AND 是把双刃剑**：合成 gradient 上防洪（删会洪水）；真实非网格图上增碎片化覆盖（开着推高 real_gate 的 ai_grid mean 8.88%，关掉约 4–5%）。**上一轮「删会击穿 ai_grid 红线、正是它压回 1.2%」只对合成 gradient 成立，对真实被门禁图方向相反——已更正**。根因是 ai_grid 在非网格 doubao 图上误触发 is_grid；更深收敛缺真实网格水印真值（数据缺口）。未改代码（ai_grid 未承诺+已监控+15% 闸）。守卫 `tests/test_grid_precision.py`（合成防洪侧仍有效）。
- **is_grid 误触发根因（同日定位）**：doubao 图 10/10 强触发，周期几乎都=8px——正是 **JPEG 8×8 DCT 块**在残差场上的周期性（acf 0.19–0.57 vs 阈 0.09，强周期非贴线）；`_ok` 最小周期仅 6 放过它，真实网格周期~38。修法=抬高最小周期/拒 JPEG 块周期（需真实网格真值，未改码）。
- **多平台调研结论（2026-10-17）**：项目架构（LaMa+FFC、掩码膨胀+羽化、crop-to-mask+mask 外逐字节一致、lama/opencv 双引擎选路、SSIM/changed_ratio 质量门禁）与业界最佳实践**高度一致**（seki.dev 六模型横评 LaMa 质量/秒+可靠性胜出）；**唯一明确缺口=直方图/色彩匹配后处理**（IOPaint `sd_match_histograms` 有、项目无；消修复区与周围亮度/色跳变，低风险高价值，**已实现(2026-10-17)**：core/inpainter._match_region_color + config.INPAINT_COLOR_MATCH(**默认False**：e2e前后对比显示开着拖累去痕率 grid 72.6%→49.8%/collateral↑/ΔPSNR↓，属 tradeoff，须更温和变体才能默认开；按需 WME_INPAINT_COLOR_MATCH=true 开启)；crop_quality_probe 干净区安全 58/58、守卫 tests/test_inpainter_color_match.py）。LaMa 弱点=复杂/独特场景臆造 + 不确定（质量门禁兜臆造）。

## 数据方向 · 28 张真实豆包图入基准（300，指针）
- 用户供图 `D:\照片存储` → 29 PNG 入 `benchmark_real/images/`；c12 与 c11 字节相同（sha256 去重）⇒ 实为 **28 张唯一图**（均 2048×2048）；全带同一「豆包 AI 生成」固定平台水印（右下 ≈[1683,1936,319,71]）；**无干净图** ⇒ 296 干净图校准 precision 未达。
- **门禁结果（53 实评）**：mask_recall **0.5111→0.5984**、precision 0.0789→0.0758、区域召回 0.84→0.9057、mean_iou 0.5876→0.6673、过检倍率 2.21→2.47 ⇒ 全部红线通过，`real_gate --strict-scope` EXIT=0，已 `--update --force-update` 重锚。
- A/B 泛化：新 28 张 box_recall 0.9643 > 原 25 张 0.8400 ⇒ 泛化良好；唯一漏检 c15（密集小字母艺术字+低对比度，属真漏检）。

## 过检收敛 S2（指针，2026-10-17 收官）
- 目标：在不破 mask_recall≥0.45 红线下降 coverage_ratio。基线→分解→杠杆扫描→收敛改→real_gate 验红线，全在 canonical venv 完成。
- 分解（`overdetection_probe`）：FP-by-kind **translucent 77.9%**（绝对主导）/ ocr_text 12.1% / edge_text 10.0% / corner_logo 0.0%（角标通路准确）。
- 杠杆扫描反证：`merge_dilate` 与 `MIN_DELTA` 均**非**过检收敛杠杆（仅 recall/过检权衡，且 q5_2 洪泛在所有 MIN_DELTA 下不变 ⇒ 洪泛由相对判据 k_sigma·σ 驱动）；**`DETECT_TRANSLUCENT_SENSITIVITY` 为唯一有效杠杆**（k_sigma=max(0.5, DETECT_SIGMA_K*(1.5−s))，s↑→k↓→更灵敏）。
- 收敛改（保守可逆）：`DETECT_TRANSLUCENT_SENSITIVITY` 0.55→0.50（仅默认值，env 可覆盖）。结果 mask_recall 0.5984→0.5960(−0.4%)、precision 0.0758→0.0801(+5.7%)、coverage_ratio 2.47→2.30(−6.9%)，全红线余量>30%、无漂移告警；已 `--strict-scope --update --force-update` 重锚 `benchmark_real/quality_baseline.json`。
- 归因定论：translucent 既是过检主体又是 ai_logo 召回唯一来源 ⇒ 收紧必蚀召回；0.55→0.50 已是「召回-精度」权衡墙上最保守有效点（与 296「提盒精度必蚀掩码召回，无免费午餐」一致）。更深收紧无免费收益，故收口。

## 文档铁律（294 沉淀）
① **新增根级文件必须同步登记 `tests/test_repo_hygiene._ALLOWED_ROOT_FILES`**；② **新增 `docs/*.md` 必须同步登记 `docs/INDEX.md`**（`tests/test_docs_index` 机械守卫，漏登必红）。阶段收尾须跑**更广回归**（非仅靶向文件）。

## 本链沉淀的可复用经验（完整见 LESSONS.md）
- **语言是跨进程持久化共享全局状态**（290 治本）：i18n 落盘 `cache/i18n_state.json` 污染后续 pytest；`conftest.py` 的 `pytest_configure` 早于 collection 指向会话临时目录已治本；须用「投毒态」重跑证伪修复。
- gradio 6.22 `gr.update()` 返回普通 dict 非 `Update`；`from-import` 后 monkeypatch 须打**导入方**命名空间；断言「补丁生效」与「结果合法」须同时写；源码子串匹配会误命中注释须用 AST。
- **「mask 外 0 变化」≠ LaMa 恒等**：LaMa 在 mask 外固有轻微扰动（两种模式都有），crop 安全性须以「crop 干净区扰动不差于 full」为判据，而非 crop-vs-full=0。
- **全局 logging 单例污染（测试隔离坑，2026-10-16 确认）**：`logging.getLogger` 是进程内全局注册表，前序用例泄漏 handler / 真实 `logs/app.log` 轮转状态会跨用例搞红 `test_utils_audit90` 的 rotation 用例——这是**项目既有顺序污染**（父提交 `71b58a0` 全量同样失败且更惨），非 #450 回归。修复须 conftest 级 handler 重置 + 每用例 `logs/` 指向 tmp，不能只修单个用例（阶段 245/246 已踩过）。
- **Git Bash 沙箱 PATH / 跨盘脚本参数陷阱（S2 沉淀）**：① 显式 `export PATH` 须含 `PortableGit/.../usr/bin`（提供 ls/grep/head/dirname）+ `/bin`（bash 内部）+ `System32` + venv Scripts；仅写 `/bin` 会 `dirname/head/ls/grep: command not found`(Exit127)。② 跨盘脚本参数：`$CANON /c/Users/.../script.py` 的脚本路径会被 bash 误映射成 `D:\c\Users\...`（`can't open file 'D:\c\...'`）；探针脚本必须放进项目内 `scripts/` 用相对路径调用。③ canonical venv：`/c/Users/HP/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（torch 2.9.1+cu128 + CUDA 12.8 可用）。
