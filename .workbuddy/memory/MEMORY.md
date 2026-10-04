# 项目长期记忆 · AI 智能水印擦除工作台
> SSOT：STAGES.md / LESSONS.md（至 221 条）/ 每日日志 `YYYY-MM-DD.md`。本文件只留索引、铁律、决策级边界、进度指针；细节查上述来源。

## 定位与合规
只处理用户自有权处理的 AI 生成图片上的可见水印；统一「去除本人生成的 AI 图片水印」表述（《著作权法》53 条）。

## 项目铁律（持久）
- 阶段完成主动告知「【XX阶段开发完成…】」；禁止「此处省略代码」；收到「终止项目」前持续推进。
- 分层 `ui→services→core→utils→config`，严禁反向依赖；`core/services/utils` 零 gradio；`config` 不依赖 `utils.logger`。
- 探针/basetemp 用非 Temp 路径；沙箱批量删 >50 文件 → `SystemExit(1)`（两段式受限 + 会话闩锁，归档 rename 优于删除）。
- 多段编辑须串行 + 重读校验；注入式反例读写 `newline=''`、sha 对原始字节（`'rb'`）计算。
- 网络：PyTorch/GitHub/HF/清华镜像不可达；PyPI 清华镜像可达；大文件走 `download_model.py`。

## 关键边界 · 真值审计
- 真实基准已重建（172/177）：`real_gate --strict-scope` EXIT=0；红线 recall≥0.64 / precision≥0.050 / mean_iou≥0.35 + 崩溃护栏 mask_iou≥0.15 / mask_coverage≥0.045。
- 检测器修复链：173 车牌长宽比 2.5→3.2；179 牌体补全先验（默认 OFF）；182 足迹重叠锚定 0.50（prior_fp_rate 0.40→0.00）；183 锚点一致性（6 处真实失败全修）。
- 能力声明：`WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。

## 门禁家族（持久契约）
- 成员：quality_gate + real_gate(30图) + e2e_real_probe(10图×4类) + mask_param_audit + clean_bench_probe + prompted_area_gate + perf_probe。退出码 0/1/2。
- 每个 `--update/--refresh` 须接 `baseline_guard`（可比性 + 向下刷新 + 继续判定）。
- 脚本 print 非 GBK 字符须 `force_utf8_stdio()`（170）。

## 经验铁律
全文见 **LESSONS.md（至 221 条）**。近阶段高频：183 有守卫≠有覆盖 / 200 性能须用真实素材 / 206 编辑回执≠落盘 / 211 性能基线须校验硬件指纹 / **221 环境假设可能是错的（本机即 RTX 5060）·从未执行的路径=未验证（GPU 首跑即 P0：fp16 致 LaMa 全 NaN）·`clamp` 拦不住 NaN·fp16 非通用开关**。

## 项目健康度
源码 ~33.2k + 测试 ~22.5k（111 文件）；实测 **2504 用例**（174 文件）；torch 2.9.1+cu128（本机 RTX 5060 Laptop 8151 MiB）+ big-lama.pt。

## 产品化与战略
技术内核强、商品外壳缺。路线 P1 数据质量 → P2 产品化性能(GPU+API+Docker) → P3 生态。不可替代：全本地离线 + 免手涂 8 预设 + 图片/PDF/视频全源 + 合规定位。

## 接口契约（101，持久）
`detect_watermark_mask(image: np.ndarray, *, preset=...) -> DetectionResult`：不吃路径；收 RGB/RGBA/灰度；`res.mask` uint8 0/255；`regions[].x/y/w/h` 为 `np.int32`（写 JSON 前 `int()`）；9 种格式、不递归、`MAX_IMAGE_PIXELS=80M` 超限抛异常；对外打分用掩码级指标，禁 bbox-IoU。

## i18n
4 子类全收口（全角标点/任务级消息/exceptions/报告交付物）。新增文案须双语，由覆盖契约守卫兜底。

## 仓库与交付
- git：`init -b main`，身份 `WME Dev <dev@wme.local>`；CI `ci.yml`→`run_all_checks.py`（184）。**推远端前须先跑 `python scripts/ci_env_probe.py`**（206 证明 15 用例会红，已修）。
- 历史重写（205，用户授权）：196MB `models/big-lama.pt` 已从全部历史移除，`.git` 326.6→143.8MB；**重写前历史不可恢复**（教训：备份须先 `git bundle`）。
- 仓库卫生（205）：追踪文件 5172→741；`.workbuddy/scratch/` 有意保留追踪。
- 验证入口（184）：`scripts/run_all_checks.py`（pytest CI 子集 / `--full`+`undef_scan` / `--gates`）。
- 性能门禁（186 A5）：`scripts/perf_probe.py` 真实基准；目标 P95<1.5s **当前不可达**（207 实测 GPU 默认 P95 19.07s、覆盖 30.27s，瓶颈在 CPU 后处理/检测，非 GPU）。
- 覆盖率（187/192）：TOTAL 75.23%（地板 67）；`main.py` 32.28%。
- E2 删除：`scripts/clean_artifacts.py`（dry-run/archive/delete 三模式，归档主路径）。

## 当前进度指针
✅ C1-C5 / A2 / A5 / B2 / D1（四阶段 + 真机验证 Stage 6）/ E1-E3 / 189-207 全收口；**208 server 入口函数缺陷审计**（audit_selector 全 45 模块闭环）。
⏳ 待用户交图：B1/B3/A1（真实水印图）、B4（真实 FP 率）；A4（ai_gradient 真渐变证伪骨架就绪，等你用豆包 v2 提示词出 3~5 张图闭环）。
⏳ D1 真机验证已做（207）：本机装 CUDA torch + `WME_DEVICE_MODE=auto` 启用 GPU；`API_MAX_CONCURRENCY` 默认 2→4（GPU/CPU 双标定）。
🔴 **生产入口缺陷已修（208）**：`server.start_server` 签名漏 `max_concurrency` 形参 → 任何调用（`main.py --api`）抛 `UnboundLocalError`，且 `API_ENABLED` 守卫成死代码；已补回参数 + 4 例回归（tests/test_server_start.py）。
