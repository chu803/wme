# 项目长期记忆 · AI 智能水印擦除工作台
> SSOT：STAGES.md（逐阶段推导）/ LESSONS.md（经验铁律全文）/ 每日日志 `YYYY-MM-DD.md`（近期一手记录）。本文件只留**索引、铁律、决策级边界、当前进度指针**；细节查上述来源。

## 定位与合规
只处理**用户自己有权处理的 AI 生成图片**上的可见水印；全站统一「去除本人生成的 AI 图片水印」表述（《著作权法》53 条）。

## 项目铁律（持久）
- 阶段完成主动告知「【XX阶段开发完成…】」；禁止「此处省略代码」；收到「终止项目」前持续推进。
- 分层 `ui→services→core→utils→config`，严禁反向依赖；`core/services/utils` 零 gradio；`config` 不依赖 `utils.logger`。
- 探针脚本 / basetemp 用**非 Temp 路径**（`%TEMP%` 会被清理器中途删）；沙箱批量删 >50 文件 → `SystemExit(1)`（**两段式受限：单次 >50 拒 + 会话闩锁**，归档 rename 优于删除）。
- 同一文件多处编辑须串行 + 重读校验；注入式反例读写 `newline=''`、sha 对原始字节（`'rb'`）计算。
- 网络：pytorch/GitHub/HF/清华镜像不可达；**PyPI 清华镜像可达**（pip 默认走 tuna）；阿里云 pytorch-wheels 可达；大文件走 `download_model.py`。

## 关键边界 · 真值审计当前状态
- **真实基准真值已重建（172/172-A/177）**：150 清空伪造（149 模板假 GT）；172 收编 10 张豆包真实图（右下角「豆包AI」角标，逐图量真值 `manual:measured`）；177 `quality_baseline.json` 重建为真实数据（n=10/10、`committed_missing=[]`）。
- **红线已按真实水平重标（177）**：mask_recall ≥0.45（头条）/ recall ≥0.64 / precision ≥0.050 / mean_iou ≥0.35（实测×0.8 回归地板）+ 崩溃护栏 mask_iou **0.15**（183 由 0.10 抬高，见下）/ mask_coverage 0.045 / ratio 4.35 ⇒ `real_gate --strict-scope` EXIT=0。
- **检测器修复链**：173 `DETECT_PLATE_MAX_ASPECT` 2.5→3.2 + 条件化闭运算（仅底图不亮时）；179 生成器感知牌体补全先验 `plate_footprint_prior`（信号锚定 + 右下角不变式 + 实心掩码直写，可逆杠杆 `DETECT_PLATE_COMPLETE_FROM_SIGNAL` **默认 OFF**）；182 加足迹重叠锚定 `DETECT_PLATE_PRIOR_ANCHOR_OVERLAP`=0.50（合成集 prior_fp_rate 0.40→0.00、召回零回退）。
- **180/181 结论**：179 杠杆保持默认 OFF（合成集确定性误发 0.40，被 182 几何锚定消除；真实照片 FP 仍未测 ⇒ 决定更稳）；181 澄清 180 误判——基线在干净图上稳态不误发（翻转是检测器冷启动伪影），加 `_warmup()` 加固。
- **183 锚点一致性审计**：172/177 交付后一批测试停在旧世界 → 全库首跑 6 处真实失败全修；附带 `mask_iou` 崩溃护栏 0.10→**0.15**（实测已抬到 0.2975，0.10 松于实测一半违反防哑巴不变式）。根治理念：所有锚点从单一真值来源派生。
- **能力声明 SSOT**：`WATERMARK_TYPE_COMMITTED={ai_logo}` + `KNOWN_SYNTHETIC_GAP={ai_gradient}`。

## 门禁家族（持久契约）
- 成员：quality_gate + real_gate(30 图) + e2e_real_probe --gate(10图×4类) + mask_param_audit + clean_bench_probe + prompted_area_gate + perf_probe(186)。退出码 **0 通过 / 1 未通过 / 2 跳过**（171）。
- **146 基线刷新护栏**：每个门禁的 `--update/--refresh` 必须接 `baseline_guard`（可比性护栏 + 向下刷新护栏 + 刷新后继续判定）；perf_probe 在 192 补齐（此前直接 `return 0` 静默通道）。
- **170 编码加固**：脚本 print 非 GBK 字符在 Windows 重定向下抛 `UnicodeEncodeError` ⇒ 退出码被读反；统一 `scripts/console_encoding.py::force_utf8_stdio()`。real_gate 空真值是**合法状态**（170，跳过评估交 `evaluate_real` 判 `committed_missing`）。

## 经验铁律索引
全文见 **LESSONS.md**（至 **213** 条）。近阶段重点：182 守卫拒绝≠无副作用 / 183 有守卫≠有覆盖 / 184 校验同源 / 185 判据不对称即 bug / 186 打印契约≠强制契约 / 187 macro≠micro·头条须 micro / 188 量测≠决策 / 189 写入口就地校验 / 190 下载亦写入口·skip/reject 语义区分 / 191 验证装置须可证伪 / 192 人眼复核取景须覆盖修复区 / 193 or 兜底吞显式0 / 194 i18n 须含全角标点 / 195 数学正确≠交互语义 / 196 还原判据 sha·newline='' / 197 本地化只解外层 / 198 兜底不回退他人默认值 / 199 数量参数缺下界→判据恒真 / 200 性能须用真实素材 / 201 统计量须高于噪声地板 / 202 受限环境删除两段受限·归档优于删 / 203 改视野须重审清单·守卫整文件跑 / 204 pytest-timeout 内外层须外层>内层·扫描源须剔注释 / 205 改既有函数前须跑其全量既有测试·新组件登记家族守卫免费获不变式 / 206 编辑回执≠落盘·多段编辑须逐项 Grep 复核 / 207 沙箱 basetemp 不复用项目内目录 / 208 新脚本三类守卫+全量回归 / 209 基础设施文件契约守卫·WME_ 前缀否定断言 / 210 动手前先 grep 既有实现·勿重复造轮子 / 211 性能基线可比性须校验硬件指纹（device/gpu_name） / 212 沙箱内 git stash 会损坏 .git·忽略规则须先提交才生效（用 info/exclude）·内联 shell 吞反引号 / **213 改全局单例必须自包含·测试污染 ⇒ 假红（报错值与源码矛盾 / 时红时绿 是特征）**。

## 项目健康度
~33.2k 源码 + ~22.5k 测试（111 文件）；~1546 用例；torch 2.9.1+cpu + big-lama.pt。自检 116 总评 B（工程A/核心B-/产品化D/生态F）。

## 产品化与战略
技术内核强、商品外壳缺。不可替代：全本地离线 + 免手涂 8 预设 + PDF/视频/图片全源 + 合规定位。路线 P1 数据质量 → P2 产品化性能(GPU+API+Docker, P95<1.5s) → P3 生态。

## 接口契约（101，持久）
`detect_watermark_mask(image: np.ndarray, *, preset=...) -> DetectionResult`：不吃路径；收 RGB/RGBA/灰度；`res.mask` uint8 0/255；`regions[].x/y/w/h` 为 `np.int32`（写 JSON 前 `int()`）；9 种格式、不递归、`MAX_IMAGE_PIXELS=80M` 超限抛异常；对外打分用掩码级指标，禁 bbox-IoU。

## i18n 整类收口 ✅
4 子类全收口：全角标点(162) / 任务级消息(164) / utils.exceptions(168) / 报告交付物(169)。新增文案须双语，由覆盖契约守卫兜底。

## 仓库与交付
- **git（185 C1）**：已 `init -b main`，**13+ 提交**；仓库级身份占位 `WME Dev <dev@wme.local>`（用户未设全局身份）。CI `ci.yml` → `run_all_checks.py`（184 固化同源；**待推远端即生效**，需用户决策）。
- **验证入口（184）**：`scripts/run_all_checks.py`（pytest CI 子集 / `--full`+`undef_scan` / `--gates` 追加门禁；退出码 0/1/2；强制项目内 basetemp）。
- **性能门禁（186 A5）**：`scripts/perf_probe.py` 真实基准（CPU·检测 P50 1.427s / P95 3.209s，对照路线图 1.5s 为 2.1×，默认只守回归、目标仅报告）。
- **覆盖率（187/192）**：TOTAL **75.23%**（187:75.29%）；地板 `COVERAGE_FLOOR=67`；`.coveragerc` 必须 `parallel=False`（否则收尾删并行数据撞删除护栏⇒报告丢失）。分层：`core/detector` 87.3% / `inpainter` 93.1% / `config` 91.8% / `ui/components` 98.01% / `tab_single` 81.25% / `tab_media` 63.17% / `tab_batch` 53.87%（搬家效应）/ **`main.py` 32.28%**（187:0.00%，189 成果）。
- **E2 删除（已交付脚本）**：`scripts/clean_artifacts.py` 把「盘点→隔离→回收」固化——默认 `--dry-run` 只列；`--archive` 用 rename 隔离到 `.scratch/cleaned/<ts>/`（**主路径，可逆可找回**）；`--delete --i-am-sure` 才真删（≤50 小批量，沙箱外用）。小目标 `build/` `*.egg-info/` `_dbg_bench/` `.verify_tmp/` `.pytest_iso/` 与大目录 `.pytest_*`/`logs`/`temp`/`cache`/`exports`/`outputs` 现可由该脚本在沙箱外承接，无需再手敲 `rm`（均 gitignored）。

## 当前进度指针（详见 `docs/<编号>-*.md` 与每日日志）
162 tab_models i18n · 164 tab_batch 助手+i18n · 165 model_manager · 166 report_service · 167 detector 网格通路契约 · 168 exceptions i18n · 169 report i18n · 170 门禁盲区审计 · 171 退出码契约 · 172 真实图收编 · 173 检测器修复 · 177 基线重标 · 178 lift 三否闭合 · 179 牌体补全先验 · 180 干净图基准门禁 · 181 基线非确定性再审计(纠 180) · 182 牌内证据锚定 · 183 锚点一致性审计 · 184 验证入口固化(CI/本地同源) · **185 全产品优化清单**(盘点) · **C1 git init** · **A2 precision 自我纠正**(本就有红线) · **A5 perf_probe** · **187 覆盖率基线** · **188 C2 打包元数据 + C3 依赖钉版 + E1/E2 清单** · **E1 归档 ✅**(根目录点文件 142→5) · **189 启动入口编排守卫(main 0→32%)** · **190 单图耗时显示 + 产物目录退出审计视野** · **191 UI 两弱页接缝补测 + gr.File 归一化收敛 + pytest-timeout 内外层错配修复** · **192 入口收口 C4 + 全库覆盖率复测 + perf_probe 刷新护栏 + normalize 契约回归修复** · **193 D2 批量/媒体页耗时展示（收口，共用 format_elapsed 接缝）** · **194 A4 真渐变证伪骨架（待你出图闭环）** · **195 E2 仓库产物清理脚本（dry-run/archive/delete 三模式，sandbox 安全）** · **196 E3 文档索引（docs/INDEX.md + 防腐烂守卫，覆盖 198 篇）** · **197 C5 Docker 容器化（多阶段 + HEALTHCHECK + 非 root，修 WME_ 前缀静默缺陷）**。

### 已完成 / 剩余清单
- ✅ C1 / C2 / C3 / C4 / C5 / A5 / B2 / E1 / 189 / 190 / 191 / 192 / 193 / 194 / 195 / 196 / 197 / 198（D1 全四阶段：GPU 契约+REST 接入+Docker GPU 配方+细粒度端点）；A2 已纠正。
- ✅ D1 产品化 GPU/API 接入完成（D1-Stage 1 GPU 设备契约 + 推理张量跟随设备 / Stage 2 REST 接入 main.py --api + Docker EXPOSE 7861 / Stage 3 Dockerfile.gpu + 9 例契约 / Stage 4 REST 细粒度端点 `/api/detect`+`/api/inpaint` + 5 例契约，全绿）。
- ⏳ 待用户交图：B1/B3/A1（真实水印图 P0 闭合 inbox）、B4（真实 FP 率测量）；D1 真机验证待你决策（① 本机装 CUDA torch + `WME_DEVICE_MODE=auto` 启用 GPU ② P95<1.5s 须在 RTX 5060 以 `perf_probe` 实测，当前 CPU 基线 P95 3.209s）。
- ⏳ A4（ai_gradient 真渐变证伪）：**骨架已就绪**（v2 提示词 + `scripts/a4_gradient_proof.py` + 守卫测试），阻塞于你用豆包 v2 提示词出 3~5 张真渐变图。
- ✅ 193 D2 批量/媒体页耗时展示（收口：单图 190 + 批量/媒体 193 共用 `format_elapsed` 接缝）。
- ✅ 194 A4 真渐变证伪骨架（v2 提示词就绪 + 证伪脚本 + 3 例守卫测试，待你出图闭环）。
- ✅ 195 E2 仓库产物清理脚本（`scripts/clean_artifacts.py`：dry-run/archive/delete 三模式 + 7 例守卫测试，真实仓库 79 条目/16078 文件/68.2MB 一键盘点；`--archive` 为安全主路径）。
- ✅ 196 E3 文档索引（`docs/INDEX.md`：上编优化清单执行索引 + 下编全量目录；`tests/test_docs_index.py` 防腐烂守卫以磁盘枚举为准；现覆盖 199 篇）。
- ✅ 197 C5 Docker 容器化（`Dockerfile` 改多阶段 builder→runtime + HEALTHCHECK 轮询 Gradio 首页 + 非 root uid 10001；**修旧文件 `SERVER_HOST` 无 `WME_` 前缀的静默缺陷**；`tests/test_dockerfile_contract.py` 8 例契约守卫）。
- ✅ 198 D1 产品化 GPU/API 接入（Stage 1 GPU 设备契约 + 张量跟随设备 / Stage 2 `main.py --api` 接入 + Docker EXPOSE 7861 / Stage 3 `Dockerfile.gpu` + 9 例契约 / Stage 4 REST 细粒度端点 `/api/detect`+`/api/inpaint` + 5 例契约 / Stage 5 perf_probe `--stage full` 全链路计时 + 真机验证口径 / **Stage 6 性能基线硬件一致性守卫（`device_fingerprint` + `baseline_comparable` 校验 device/gpu_name）**；LESSONS 210/211）。
- ✅ 199 REST 并发闸门（对应 185 清单 **D1 并发策略**，与 198 的「D1 产品化」同名不同物）：探针实测 `SERVER_MAX_THREADS` **只喂 Gradio**、REST 侧 `ThreadingHTTPServer` 无上限（并发争用会冲垮 P95 门禁）⇒ 新增 `config.API_MAX_CONCURRENCY`（默认 2 / 0=关）+ `_BoundedThreadingHTTPServer`（工作线程内取闸门·accept 不阻塞·**排队而非拒绝**·health 与 `/` 豁免并回报 `{max_concurrency,active,peak,gated}`）；`tests/test_server_concurrency.py` 7 例（含注入式反例，反例有效性已验证）。**185 清单纯代码项至此全部收口**；LESSONS 212（含 git 仓库重建事故留档：残损 `.git` 已 rename 归档为 `.git_broken_20261003_154623/`，基线提交 `402e26f`，`fsck` rc=0）。
- ✅ 200 config 审计守卫「假红」根因（收口 199 遗留的 4 条红灯）：报错值与 `config.py` 实际值矛盾（`INPAINT_STRENGTH_MIN(1.0)≥MAX(0.0)`，实际 `0.0/1.0`）且逐轮变、同用例时红时绿 ⇒ **测试污染全局单例**（`test_config_audit145` 靠模块级 autouse fixture 兜底，但**同一测试内的循环**传播脏值：`test_N3_default_out_of_range_is_caught` 遍历 5 组三元组每组末尾把 `DEFAULT` 留在区间外）。修法 = `_set()` 返回还原函数 + 全部 17 处改 `try/finally` 自包含 + MIN/MAX 干净值提前取；新增 **N8 三条守卫**（源码层否定式扫描 / 还原函数可证伪 / **关掉 fixture 兜底跑完仍零残留**），反例有效性已验证（一处裸 `_set` ⇒ 3 条同时变红）。**33/33 通过**（修复前 4 失败）；波及面扫描 27 处疑似逐个核实，仅此 1 处真有问题。LESSONS 213。
