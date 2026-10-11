# 项目长期记忆 · AI 智能水印擦除工作台
> SSOT：STAGES.md / LESSONS.md / 每日日志 `YYYY-MM-DD.md`。本文件只留索引、铁律、决策级边界、进度指针。

## 定位与合规
只处理用户自有权处理的 AI 生成图片可见水印；统一「去除本人生成的 AI 图片水印」表述（《著作权法》53 条）。

## 项目铁律（持久）
- 阶段完成主动告知「【XX阶段开发完成…】」；禁止「此处省略代码」；收到「终止项目」前持续推进。
- 分层 `ui→services→core→utils→config`，严禁反向依赖；`core/services/utils` 零 gradio；`config` 不依赖 `utils.logger`。
- 探针/basetemp 用非 Temp 路径；沙箱批量删 >50 文件 → `SystemExit(1)`（两段式受限 + 会话闩锁，归档 rename 优于删除）。
- 多段编辑须串行 + 重读校验；注入式反例读写 `newline=''`、sha 对原始字节（`'rb'`）计算。
- 网络：PyTorch/GitHub/HF/清华镜像不可达；PyPI 清华镜像可达；大文件走 `download_model.py`。

## 关键边界 · 真值审计
- 真实基准重建（29 图口径）：`general` 实测 mask_recall **0.5111** / precision 0.0789 / recall 0.8400 / mean_iou 0.5876 / mask_iou 0.3130 / mask_coverage 0.0107 / coverage_ratio 2.21。红线 recall≥0.64 / precision≥0.050 / mean_iou≥0.35 / **mask_recall≥0.45** + 崩溃护栏 mask_iou≥**0.20** / mask_coverage≤0.020 / coverage_ratio≤3.50。`real_gate --strict-scope` EXIT=0。
- 图像级已知检测缺口（243）：`config.WATERMARK_KNOWN_GAP_IMAGES={a1,a2,a3,a4}`（大颗多面体宝石角标），像素域不可分 ⇒ 收窄承诺。守卫 `tests/test_known_gap_images243.py` 4 例全绿。
- 检测器修复链：173/179/182/183。能力声明 `WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。

## 门禁家族（持久契约）
成员：quality_gate + real_gate(30图) + e2e_real_probe(10图×4类) + mask_param_audit + clean_bench_probe + prompted_area_gate + perf_probe。退出码 0/1/2。每个 `--update/--refresh` 须接 `baseline_guard`。脚本 print 非 GBK 字符须 `force_utf8_stdio()`。

## 性能归因链（211→230→263）
- detect 真实 P95=**0.776s**（较 v3 基线 0.679 高 +14%，30% 护栏内，基线维持 0.679）。
- full 基线已建立：`benchmark_real/perf_baseline_full.json`，P95=**8.585s**，superres=off 干净口径。
- 结构性地板 ≈4.4s（LaMa+denoise+detect）⇒ 1.5s 对 full 不可达。torch 2.9.1+cu128 + big-lama.pt。

## 项目健康度
源码 ~33.2k + 测试 ~22.5k（111 文件）；实测 **2504 用例**（174 文件）；torch 2.9.1+cu128（RTX 5060 Laptop 8151 MiB）+ big-lama.pt。

## 产品化与战略
技术内核强、商品外壳缺。P1 数据质量 → P2 产品化性能 → P3 生态。不可替代：全本地离线 + 免手涂 8 预设 + 图片/PDF/视频全源 + 合规定位。

## 接口契约（101，持久）
`detect_watermark_mask(image: np.ndarray, *, preset=...) -> DetectionResult`：不吃路径；收 RGB/RGBA/灰度；`res.mask` uint8 0/255；`regions[].x/y/w/h` 为 `np.int32`（写 JSON 前 `int()`）；9 种格式、不递归、`MAX_IMAGE_PIXELS=80M` 超限抛异常；对外打分用掩码级指标。

## 仓库与交付
- git：`init -b main`，身份 `WME Dev <dev@wme.local>`；CI `ci.yml`→`run_all_checks.py`。推远端前跑 `python scripts/ci_env_probe.py`。
- 历史重写（205）：`models/big-lama.pt` 已从全部历史移除，`.git` 326.6→143.8MB。
- 验证入口：`scripts/run_all_checks.py`（CI 子集 / `--full` / `--gates`）。E2 删除：`scripts/clean_artifacts.py`（archive 优先）。
- Docker（C5，210 硬化）：`.dockerignore` 排除后上下文 4.4MB；⚠ 本机无 docker CLI，镜像从未真 `docker build`。250 静态审计：Dockerfile.gpu P0（python3.11 映射）+ 依赖统一 torch 2.9.1（删 torchvision）。

## API 侧（250/251/255/257）
只读端点补齐：`/api/capabilities`、`/api/models`、`/api/models/active`、`/api/models/verify?name=`（251）；`/api/health` 保持兼容。推理端点绝不豁免（199 fail-closed）。**255 异步作业模型收口**：`job_manager.py` + `POST /api/jobs` / `GET /api/jobs/{id}` / `GET /api/jobs/{id}/file`；守卫 `tests/test_api_async_jobs255.py` 11 例全绿。**257 D1 worker 池并发标定**：`config.API_ASYNC_MAX_WORKERS`（默认 2，范围 [1,8]）；RTX 5060 甜点 k=2。仅剩 `POST /api/models/active` 按既有决策仍不开放。

## 决策级边界（全部待决项已收口）
- A4（2026-10-04）：a1-a4 归 corner_logo，非全图渐变；`ai_gradient` 仍为真·无真实图缺口。
- ① 超分 2026-10-08 拍板 A 关闭并落地（`config.user_overrides.json` → `ENABLED=false/SCALE=4/BACKEND=auto`）。
- ② 分块阈值 260 收口（INFER_MAX_SIDE 边界语义）。
- ③ full 基线口径修复已落地且已建立（263：P95=8.585s，superres=off）。
- ④ `test_real_asset_preflight` 数据漂移已确认收口（239 占图比口径 + `>=10` 下限容忍）。
- **242 红线决策（2026-10-08）：A 维持**——mask_recall 硬线 0.45 不动，靠 259 余量健康监控（advisory 层，只报不失败）盯防；余量最薄（~12%）由监控暴露而非放松红线。零契约变更。
- 环境事故收口（2026-10-04）：easyocr 降级已还原（cu128 wheel 2862MB）。

## 5j 深度续审链（276-290，**已收官**；荒漠比值驱动；已审模块首轮遗漏分支钉死）
| 阶段 | 模块 | 真缺陷 | 守卫 | 备注 |
|---|---|---:|---:|---|
| 276 | ui/tab_auto | 0 | 22 | 边界不变量 |
| 277 | ui/tab_models | 0 | 33 | 成功/边界返回值不变量 |
| 278 | services/queue_worker | 0 | 14 | 成功路径+字段级不变量 |
| 279 | services/report_service | 0 | 18 | 成功路径+边界分支 |
| 280 | utils/recommend_view | 0 | 16 | 边界钳制+空态分支 |
| 281 | ui/tab_batch | **1** | 24 | 空序列索引 TypeError（3 处同根因，已修） |
| 282 | core/mask_editor | 0 | 37 | 边界+形态分支 |
| 283 | ui/tab_single | 0 | 39 | 九条返回分支+声明漂移修复 |
| 284 | core/preset_recommender | 0 | 31 | 退化输入+决策不变量 |
| 285 | core/image_utils | **3** | 48 | pad 除零/分块爆炸/NaN 净化（均修） |
| 286 | core/model_manager | 0 | 18 | sha/体积/active/persist 分支 |
| 287 | core/device_manager | 0 | 16 | 缓存复用/探测退化/节流分支 |
| 288 | ui/theme | 0 | 9 | build_app_theme 双层兜底·builtin_theme_names 返回()·resolve 边界回退·CSS 占位符 |
| 289 | utils/logger | 0 | 7 | _ColorFormatter 不着色两分支·print_banner·_ensure 幂等/复用·get_logger 默认名·_resolve_level 非字符串 |
| 290 | ui/app | 0 | 36 | **收官站**：_find_free_port 五语义·launch_app 全返回码·288「soft」兜底跨模块契约·**超分注册表不累积（U3 同源兄弟，此前零守卫）**·overlay CSS 不破坏 JS 字面量·config 开关翻转 |

> C1-C5 / A2 / A5 / B2 / D1 / E1-E3 / 189-263 全收口。
> ⏳ 待用户侧：B1/B3/A1 真实水印图、B4 真实 FP 率、Docker 真构建验收（本机无 docker）。
> ✅ **续审链已收官**：`audit_selector.py --unaudited` 返回空表（45 模块全部已审）。再启动续审必须先重跑该脚本取候选，不得凭记忆。

## 本链沉淀的可复用经验
- **⚠ 语言是「跨进程持久化」的共享全局状态**（290 实测踩到）：`ui.i18n_rt.apply_language()` 内部调 `set_language()` **并 `save_language()`**，同时改进程全局 `_LANG` 与**磁盘** `cache/i18n_state.json`；而 `load_language()` 优先级为 环境变量 > 持久化文件 > config > 默认 ⇒ 持久化文件里的 `en-US` 会在**之后每个全新 pytest 进程**生效并压过 `config.UI_LANGUAGE=zh-CN`（实测 14 例「期望中文」全红，改回 zh-CN 后 144 例全绿）。**任何调用 `_on_lang()` / `apply_language()` 的测试必须用 autouse 夹具还原 `_LANG` 与状态文件**。守卫见 `tests/test_app_branches_audit.py::_isolate_language_state`。
- **断言「本轮无连带损伤」必须先跑排除本轮文件的对照**：290 宽回归首跑 14 例红，先 `--ignore` 新守卫（仍 14 红）+ 完全隔离那 5 个文件（仍 14 红）才证明与本轮无关，再查根因才发现是跨进程污染。省略前两步会把泄漏源误判成 flaky。
- **gradio 6.22 的 `gr.update()` 返回普通 dict** `{'value':…,'__type__':'update'}`，**不是 `Update` 对象**——`isinstance(x, gr.update)` 会直接 TypeError。断言更新产物须兼容两种形态。
- **from-import 的真实调用点在导入方命名空间**：`from ui.theme import build_app_theme` 后，monkeypatch `theme_mod.build_app_theme` **不生效**，须打 `app_mod.build_app_theme`（经验 264 的又一实例）。
- **断言「补丁确实生效」与「结果合法」要同时写**：只断言结果合法时，补丁打错位置会假绿（290 的 G5 双条件）。
- **源码子串匹配会误命中注释**：「launch_app 不含 config.SERVER_THEME」曾因注释里写着该串而假失败 → 须按 AST 属性访问判定（经验 250）。
