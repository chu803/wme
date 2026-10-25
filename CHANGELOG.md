# 更新日志 / CHANGELOG

> 本文件按「里程碑」归纳项目演进（而非逐阶段流水账；逐阶段细节见 `docs/`，共 118 份）。
> 版本号遵循 `MAJOR.MINOR.PATCH`；当前版本 `1.0.2`。

## [1.0.2] - 2026-10-18

> 1.0.1 已于同日上架 PyPI；本版为**元数据与发布口径**收口，代码逻辑与 1.0.1 一致（无功能变更）。

### 打包 / 元数据（PyPI 包页可发现性）
- `pyproject.toml` 新增 `[project.urls]`（Homepage / Repository / Documentation / Changelog → `github.com/chu803/wme`）。
- 分类器 7 → 13 条：补 `Development Status :: 5 - Production/Stable`、`Intended Audience :: End Users/Desktop`、`Intended Audience :: Developers`、`License :: Other/Proprietary License`、`Natural Language :: Chinese (Simplified)`、`Topic :: Utilities`。
- 版本号同步升至 `1.0.2`（`config.APP_VERSION` 真值源 + `pyproject.toml` + `main.py` + `core/services/ui/utils/tests` 的 `__version__`），`tests/test_packaging_meta.py` 断言一致。

### 发布管线
- 尝试迁移 PyPI Trusted Publisher（OIDC）后因账号网页登录受阻回退，**沿用已验证的 `PYPI_API_TOKEN`**（Publish #3/#5 同款）；OIDC 待可正常登录 PyPI 后再切。

## [1.0.1] - 2026-10-18

> PyPI `1.0.0` 已于 2026-10-15 发布（Publish #3 全绿）且 **PyPI 不允许覆盖同版本**，故 1.0.0 之后的
> 修复与性能收口统一走本版，不再重发 1.0.0。
> 溯源（诚实边界）：PyPI 上的 `1.0.0` 产物由提交 `06b3d13` 构建，而 tag `v1.0.0` 指向 `0be2209`
> （其后的 25 个提交，含本版全部修复）——**两者不重合**；需复现 PyPI 1.0.0 请 `git checkout 06b3d13`。
> 不把 tag 回指、也不删远端 tag：回指会再触发一次必然失败（重复上传同版本）的 Publish 运行，属纯噪声。
> 1.0.1 起 tag 与产物一致。

### 性能（裁剪到水印包围盒修复：默认开启，2048² 约 3.6x）
- `INFER_CROP_TO_MASK`（默认 `True`）：推理裁剪到水印外接框（含 padding）修复后回贴；2048² 实测 **7.3s→2.1s**。
- **安全边界（实测）**：mask 外像素与全图推理**逐字节一致**；`crop_quality_probe` 覆盖 58 张 ≥2048² 图，干净区 **58/58 通过**，速度中位 1.51x。
- **诚实边界**：mask 内存在 LaMa 的尺寸相关行为差异，属**非无损**权衡；`fp16`（毁图）、`resize`（有损）、固定网格分块（实测 0.9x 更慢）三条替代路径均已排除。

### 修复（`is_grid` 误触发：JPEG 8×8 DCT 块伪周期）
- 根因：真实 JPEG 图残差场在 lag 8px 出现强自相关（0.19~0.57 vs 阈 0.09），`_ok` 最小周期硬编码 6 放过了它（真实网格周期=56）。
- 修：新增 `config.DETECT_GRID_MIN_PERIOD`（默认 16，范围 4~128，`WME_` 前缀可覆盖），`_ok` 改读该杠杆。
- 效果：真实 doubao 图 is_grid 误触发 **10/10 → 1/10**；`real_gate --strict-scope` 下 `ai_grid` 覆盖面积 mean **8.88%→2.43%**、区域数 **747→42.8**、红线余量 **29%→80%**；`general` 七项红线**逐位不变**；合成网格 seed=7 零漏检。守卫 `tests/test_grid_min_period.py`（含注入反证）。

### 优化（预设推荐：右下角标 NCC 模板匹配）
- `core/preset_recommender._corner_watermark_signal`：以 9 张真值图 ROI 边缘图构成模板库（base64 内嵌、自包含），边缘图 NCC 匹配（`RECOMMEND_CORNER_WATERMARK_NCC_MIN=0.30`）把右下/中右角标图导向 `ai_logo`，避免其进入高危 OCR 空跑。
- 实测：右下角标召回 **48%→76%**（24/50→38/50），留出集 54%（31/57）；自然图零误命中（精确率 100%）。fail-closed 可逆：`RECOMMEND_CORNER_WATERMARK_ENABLED` / `_NCC_MIN` / `_MIN_EDGE`。

### 修复（#450 深度复审抓到的真缺陷）
- 极小图空 ROI 崩溃：3 处空 ROI / gallery 索引路径加守卫（消除 `TypeError`）。
- 预设打分区 8 键契约过时（新增 `corner_watermark`）：同步 `utils/recommend_view._FEATURE_KEYS` 与 `utils/i18n.py` 双语键序。

### 打包
- 新增 `MANIFEST.in`：sdist 口径与 wheel 对齐（prune `tests/` `scripts/` `benchmark*/` 等），并由 `tests/test_repo_hygiene.py` 静态守卫锁定。

### 记录
- 纠正 `EVIDENCE_AND` 的过度断言：真实图探针显示它反使 `ai_grid` 覆盖率翻倍（双刃剑——合成 gradient 防洪、真实非网格图增碎片），故**不删通路**；grid 测试夹具改 gradient 并撤销 xfail。
- `INPAINT_COLOR_MATCH` 新增但**默认关闭**：e2e 前后对比显示开启拖累去痕率（保守回退，保留开关供实验）。
- 新增 `docs/302-资产重建清单.md`：`models/big-lama.pt` 不入库（阶段 205 瘦身），固化下载源/大小校验/断点续传/备份建议。

## [Unreleased]

### 新增（阶段 323 · R2c 单图最坏净伤害红线 + 余量必须可见）
- **问题**：322a 的 R2b（`faint_tiled`/`grid` 擦除度中位 ≥ 0）余量只有 **0.010 / 0.002**，
  且中位数有个结构性缺陷——**15 例里 6 例被擦坏，中位照样可以是正的**
  （`faint_tiled` 中位 +0.010 的同时最差一张是 **−0.191**）。
- **三条候选路，两条被实测否掉**（写进 `docs/323`，别再重试）：
  ① 调预设参数收窄伤害 ⇒ 被**预设共享**堵住：`faint_tiled` 6 张负功里 5 张走 `ai_text_heavy`，
     而该预设同时服务 9/15 角标与 14/15 边字 = 直接动承诺面（`ai_grid` 只沾 1 角标 + 1 边字，
     且管着 grid 6 张负功里的 3 张 ⇒ 记为 324 的第一实验）；
  ② 「看不见就不动手」闸门 ⇒ 唯一自然分离的量是 `det_precision`（负功 0.075–0.146 vs 非负
     0.141–0.308），**但它需要真值、运行时不存在**；可观察的 recall/coverage/strength 三量分布全重叠，
     硬造阈值就是假改进 ⇒ 不做；
  ③ 真提升这两类的检测 = 把「不承诺」变承诺，是产品决策 + 检测工程，不在本轮。
- **落地**：新红线 **R2c**——任一类型的**单图最小擦除度 ≥ `NET_HARM_WORST = −0.25`**（作用于全部四类；
  现值最差 −0.191 ⇒ 余量 0.059）。`_gate_medians` 同时产出 `worst_gain`，基线文件重新生成带上该字段，
  `test_committed_baseline_is_self_consistent` 断言基线自身不踩 R2c 且字段必须存在。
  **门禁通过时也打印四类余量**（擦除度/最坏单图/误伤），薄余量不写出来就会被读成「绿=有富余」。
  `GAIN_ABS_FLOOR` 保持 0.0 不动，由测试钉住。
- **补测后第二条候选路也死了**（docs/323 §六）：想给 `ai_grid` 加「规则性不足就不动手」的闸门，
  用项目现成的 `_spectral_regularity` 逐例量化 30 用例的修复掩码 ⇒ 负功组 0.0017–0.0057 vs
  非负组 0.0009–0.0045，**完全重叠**；且生产把规则性算在残差标记图上（`grid` 触发在 11/15 上本就命中），
  真正的病是「找到了格子、没盖住墨迹」（`grid` 用例掩码精确率仅 **0.005–0.04**）。
  ⇒ 三条候选路只剩 **③ 提升平铺墨迹召回**，而那是检测工程 + 是否扩大承诺面的产品决策，不是调参加闸门。
- 最终验证：`run_all_checks --full --gates` **10 步全部 rc=0**（pytest 614.8s、removal_truth 352.7s、
  corner_signal 8.1s）；`--gates` 步骤说明同步为「四条红线」。
- **顺路的机制澄清**：四类 ΔPSNR 中位全负**不是隐藏缺陷**。逐像素分解（`clean_03`）显示
  修得好时 ΔPSNR 是 **+10.99**（edge_text 真值区 MSE 19475→15.5）；负值来自①为擦 0.08% 真值区
  要重画 ~6% 画面的掩码过度覆盖（#296 的结构性耦合）、②`grid`/`faint_tiled` 水印本身铺满 95% 画面，
  「擦掉满屏水印」在 MSE 上不可能中性 ⇒ ΔPSNR 对这两类没有解释力，门禁红线仍只用
  区内 `removal_gain` + 区外 `collateral`。
- 验证：`tests/test_removal_truth_gate.py` 21 → **27 例**；基线重跑三类中位数与 322a 逐位一致
  （第三次复现）；进程内把 `NET_HARM_WORST` 抬到 0.05、关掉 R2b/R3 ⇒ `R2C_EXIT=1` 且只有 R2c 报出
  （证明这条线单独有牙齿）。

### 变更（阶段 322c · 视频步长提示据实化）
- UI 抽帧步长的说明文案从「设 3~5 可提速数倍」（未测量的口头条数）改为**实测口径**：
  「固定台标设 3~5：实测约 1.8× 提速（24 帧样本，step=5，画质指标不变）；移动/滚动水印请保持 1」，
  与 `cli.frame_step_hint()` 的 319 实测数一致；英文同步。

### 新增（阶段 322b · 角标模板库：负结果 + 把已有标定升成门禁）
- **原计划（扩模板库 9→17 提角标检回）被自己的 A/B 否掉**：同一批真实图、5 个阈值实测，
  17 模板的命中**每个阈值都与 9 模板相同**，误命中却从 0 变 1（0.26 档变 4）；逐模板溯源定位到
  新增的「纯字线」模板最高 NCC 落在一张边缘文字图上（0.368）——只买误命中不买召回。
  ⇒ **不入库**，`scripts/make_corner_templates.py` 保留为取证工具（`--stats/--emit/--ab`，S 类豁免）。
- 病根诊断：把**两层指标混成一层**——探针的「角标检回 0.267」是检测器掩码质量，模板库管的是
  推荐器路由。真正有效的杠杆在检测器侧（321 深色底牌先验已证明 0.307→0.726）。
- **新增第 7 道门禁 `scripts/corner_signal_gate.py`**：走生产函数 `_corner_watermark_signal`，
  在 108 张图上钉 R1 角标命中 ≥36（现值 **38/49=0.7755**，与 450 当年 38 命中独立复现一致）、
  R2 非角标误命中 **必须为 0**（精确率承诺，不留余量），外加 R0 覆盖闭合三条
  （读不回的图 / GT 未匹配 / 分类不闭合）。耗时 15.9s。
- 升门禁过程中实测翻掉的两个数据坑：**中文文件名 `cv2.imread` 返回 None**（30 张，改用
  `np.fromfile + imdecode`）；**GT id 匹配口径**（47 个短 id + 10 个 24 字符截断 id）——
  裸前缀会让 `1` 认领 `10_东京街头.jpg`，把角标真值从 49 张虚涨到 75 张、命中率假跌到 0.51。
  `q1_2…q5_2` 5 条确认从未入库 ⇒ 写进 `KNOWN_MISSING_GT_IDS` 显式豁免，并**反向断言**
  「清单里的 id 一旦又有图就报错要求更新」，防止豁免变免死金牌。
- 守卫 `tests/test_corner_signal_gate.py`（21 例）：四条红线各自触发/持平不触发、空角标集绝不绿、
  分类不闭合要报、匹配口径正反例（短 id 不认领长 stem、孤儿 id 不被兄弟认领）、中文名图可读、
  缺资产 ⇒ 2、接入 `--gates` 且不在默认清单。

### 新增（阶段 322a · 修复侧门禁化：「擦得干净」从有数字升级为有守卫）
- `scripts/removal_truth_probe.py` 加 `--gate` / `--write-baseline`，四条红线按**类型中位数 × 绝对容差**：
  R1 检回 ≥ 基线−0.05、R2 擦除度 ≥ 基线−0.05、**R2b `faint_tiled`/`grid` 擦除度 ≥ 0**（擦完不得比不擦
  更差，绝对红线）、R3 误伤 ≤ 5%（320f「只动水印区」承诺）。退出码 0/1/2 沿用门禁族契约。
- 基线 `benchmark_real/removal_truth_baseline.json`（15 张真照片 × 4 类 = 60 用例，逐类型中位数）。
  预演证明价值：拿 317 首轮（后处理收窄**之前**）的数据喂判据 ⇒ R2/R2b/R3 三条全红
  （误伤中位 49%~51%、`faint_tiled` −0.117、`grid` −0.172）——318 那类回归今天会被直接拦下。
- 接入 `run_all_checks.py --gates`（第 6 道，默认步骤不跑，成本 ≈6.3 分钟）。
- **预演抓到的三个门禁自身 bug（都修了并有守卫）**：① 解释器没装 `easyocr` 时数字照样「自洽」
  （`edge_text` 检回 0.992→0.0、`faint_tiled` 擦除度 +0.010→−0.030）⇒ OCR 不在位改判**退出码 2**；
  ② `--write-baseline` 要求 rc==0 而 rc==0 要求基线存在 ⇒ 首份基线永远写不出来（死锁）；
  ③ 空掩码 `skipped` 被当无效用例排除 ⇒ 检测器集体失明只会让样本数变小、中位数照样漂亮
  （哑巴口）⇒ 现在按真实空操作计入（检回 0 / 擦除度 0 / 误伤 0）。
- 守卫 `tests/test_removal_truth_gate.py`：四条红线各自触发 + 容差边界不触发 + 空结果绝不绿 +
  **committed 基线与自身比对必须 0 问题且不踩绝对红线**（秒级预演，防门禁「出生就红」）+
  门禁口径不可被 `--limit/--cases/--engine` 绕松 + 基线只能由 `--gate` 写。

### 变更（阶段 320f · 产品决策：默认值翻转）
- **「只动水印区」默认开**：60 用例 A/B（误伤 50%→0.8–2.1%、0/60 擦除度变差、ΔPSNR 全改善、
  耗时 0.99×）+ 产品定位（去水印 ≠ 顺手美化整张图）。旧观感可在设置页关闭。
- **「输出格式跟随输入」默认开**：PNG 4.65× → JPG 1.13× / WebP 1.00×；PNG 进仍出 PNG。
- **视频步长保持 1**：移动水印会静默掉质量，不拿质量换速度。
- 四个质量门禁（real/prompted_area/quality/clean_bench）在翻转后全部通过。
- 附带诚实修正：精修统计文案「你补画 N 像素」→「编辑框比检测多 N 像素（含预填层边缘过渡）」
  （用户没画也会 +33%，主因是预填层抗锯齿被画笔阈值收进）。

### 新增（阶段 321 · 深色底牌先验）
- 317 探针发现：角标换一种板样式（**深色圆角板+白字**）检回只有 0.267——原两条候选来源
  （白牌判据 V≥140/S≤80、局部提亮）都假设「牌比底亮」。新增镜像来源：
  暗（V≤90）+ 低饱和（S≤140，比白牌宽，实测半透明深板 S 中位可达 116）+ 内部有**亮**墨迹
  （占空比 ≥5%），各跑各的过滤、只并框；**跳过闭运算**（白牌用它是桥接被深字切开的亮块，
  深色牌会被并进周围暗部——实测 clean_08 的 177×68 牌被 41×41 核并成跨区大块而整块丢弃）。
- 修完 clean_08 打出与真值几乎逐像素重合的框 `(1854,1963,177,68)` vs `(1854,1963,176,67)`。
- 逐例对比（60 用例，与 317 基线）：`clean_08 + corner_logo` 检回 **0.307→0.726**、
  clean_15 0.276→0.326；均值 0.371→**0.402**、擦除度 0.503→**0.518**，**无一例变差**；
  中位数不动（只有 2–3 例真正受益）。红线三连绿：real_gate / prompted_area_gate / clean_bench_probe。
- 守卫 `tests/test_dark_plate_prior321.py`（5 例）：深牌命中、**极性对照双向**（白牌判据不收深牌、
  深牌判据不收白牌）、无亮墨迹拒绝、总开关生效。

### 优化（阶段 320e · 设备选择惰性化）
- `core/inpainter.py` 原本无条件 `select_device()`（实测 **1.66s** + `import torch`）；
  而内置 OpenCV 引擎的 `inpaint` 根本不看 device、LaMa/插件引擎自行 `device or select_device()`、
  且结果有缓存。改为 `engine != "opencv"` 才选，**输出逐字节不变**。
- 实测（真跑 CLI）：默认 OCR 开 7.64s（设备在检测阶段已选，缓存命中 ⇒ 本改动省不到）；
  `WME_DETECT_USE_OCR=false` 时 **7.64s → 3.27s（−4.4s，−57%）**，日志不再出现「自动选择设备」。
- 守卫 3 例：OpenCV 不选 / 模型引擎仍选 / 显式 device 不再选。
- 诚实边界：默认配置下几乎无收益；插件引擎一律照旧选设备。见 `docs/320e`。

### 新增（阶段 320d · 设置页开关「输出格式跟随输入」）
- 320c 的能力只有环境变量可达 ⇒ 按 318c 同款接入「设置页 → 处理偏好」：复选框 + 应用即生效
  （`process_file` 每次读 config）+ 可保存为默认；`user_overrides` 两份清单一并登记；
  确认文案补「输出格式跟随输入：{fmt}」。
- **真开界面点过**：勾选 → 应用 → 文案回「输出格式跟随输入：开启」；未勾保存为默认，未改用户配置。
- 默认仍为**关**（一律 PNG）：翻默认会让所有用户拿到的文件从 PNG 变成 JPG/WebP，需产品拍板。

### 新增（阶段 320c · 输出格式跟随输入，默认关）
- 319 实测 PNG 输出是输入的 **4.65×**（7.68MB vs 1.65MB），JPG 1.13×、WebP **1.00×**。
  默认格式属「无损承诺」，故先做能力不改默认：`OUTPUT_FMT_FOLLOW_INPUT=False`；
  优先级「显式 `-f` > 跟随输入（开关）> None=照旧（`save_image` 的既有默认）」——
  None 的语义是**照旧**，不是"随便选"。映射 `png/jpg/webp/tif/avif` 同名，
  `bmp→PNG`（无对应输出格式），未知扩展名→None。
  能力经 `get_capabilities()["output_fmt_follow_input"]` 暴露。
- 守卫 13 例：映射 10 例 + **关闭时任意扩展名都返回 None**（防偷偷改默认）+ 显式优先 +
  端到端真跑（同一 JPEG：关→`*.png`、开→`*.jpg`）。
- 诚实边界：JPEG/WebP 输出会再经历一次有损编码（无代际叠加）；只覆盖图片路径；
  **默认仍为 PNG**——翻转默认是可见的产品行为变化，需拍板。见 `docs/320c`。
- ⚠ **真跑复验抓到的缺陷（已修）**：`cli.py` 的 `-f` 默认值是 `config.DEFAULT_OUTPUT_FORMAT`，
  被 `_resolve_out_fmt` 当成「显式指定」⇒ **跟随输入在 CLI 上永远不生效**（输出仍 PNG），
  而单测因直接调 `process_file(out_fmt=None)` 而全绿。修法：`-f` 默认改 None（不传=没意见），
  头部显示「格式=跟随输入（默认 PNG）」。新增两条守卫：`args.fmt is None` + 真跑 CLI 断言 `.jpg`
  且不再弹体积提示。实测：开关开 → `.jpg` 且无提示；开关关 → `.png` 且有提示。

### 新增（阶段 320 · 首次体验：一键载入示例图）
- 新用户「打开 → 没有带水印的图 → 卡住」。随包发布 `docs/assets/sample_watermarked.png`
  （640² 真照片 + 右下角「StudioAI」角标，553KB），自动页加「🖼 没有图？试一张示例」→ 填上传框
  → 状态栏指引「② 自动检测 → ④ 一键修复」；资源缺失时降级为提示，不让首屏崩。
- **真机走完整链路**：示例 → 检测 6.3%（25,818px / 9 区域）→ 一键修复成功并落盘。
- 两处实测发现：① ImageEditor **不接受「background + 空 layers」的预填**（把 `composite` 填成原图
  也无效，浏览器回传后当没图 ⇒ 修复报「请先上传」）⇒ 只填上传框，编辑框交给已验证的检测回调；
  ② **掩码经浏览器往返会变胖**（先前存在）：用户一笔没画，编辑框却比检测多 **+33%**（8,454px），
  314b 同现象在 2048² 残留 2,949px；方向是「多擦一点」（安全侧），但文案写「你补画」不诚实，未改。
- 守卫 `tests/test_sample_image320.py`（5 例）：资源存在与尺寸 / 三值对齐 / 资源缺失降级 / 双语键。
  **打包已修**：示例图从 `docs/assets/` 挪到 `ui/assets/`（`packages.find` 含 `ui*`），并补
  `[tool.setuptools.package-data] ui = ["assets/*.png"]`；真打 wheel 验证该文件在包内（566,420 字节）。
  守卫 `test_sample_asset_is_declared_for_packaging` 防「挪了但没声明」。
  诚实边界：示例是**合成水印**，只能体验流程，数字不能当能力证据（能力见 docs/317 与 real_gate）。

### 新增（阶段 318c · 设置页开关「只动水印区」）
- 318 有引擎、318b 有 CLI/api/REST，但网页用户用不到（#298 的第三种形态：门面接了、界面没接）。
  在**设置页 → 处理偏好**加一个全局默认复选框：点「应用」立即写
  `config.POSTPROCESS_PRESERVE_UNTOUCHED`（`postprocess()` 每次读 config ⇒ 立即生效），
  可勾「保存为默认」写入 `config.user_overrides.json`；`user_overrides` 的**两份清单**
  （`_USER_OVERRIDABLE_KEYS` + `_USER_OVERRIDE_VALIDATORS`）同时登记，缺一份会被
  `test_config_parse::test_override_lists_are_consistent` 当场抓红。
- 确认文案补「只动水印区：{preserve}」——实测原文案只报四项，用户点完看不出是否生效。
- 验证是**真开界面点的**：截图确认渲染 → 勾选 → 应用 → 文案回「只动水印区：开启」；
  全程未勾「保存为默认」，未改用户配置文件。单测 +3 例（白名单往返 / `None` 不动这项的三态 / 双语键）。
- **默认仍为关**：翻默认会改变所有用户成片观感，留给产品决策。见 `docs/318c`。

### 优化（阶段 319 · 效率与交付面：三项实测 + 两条提示）
- 实测三项：① 首图冷启动 **7.23s**——import 仅 0.4s（torch 懒加载 2.0s），成本在 CUDA ≈2s +
  EasyOCR ≈2s，**纯 OpenCV 引擎也要付**；② 24 帧静止水印视频 `--frame-step 5` **1.82×** 提速
  （37.3s→20.5s），逐帧改动量几乎不变（2.79 vs 2.77）；③ 同图输出体积 PNG **4.65×** 输入、
  JPG 1.13×、WebP **1.00×**（7.68MB / 1.86MB / 1.65MB）。
- 落地两条**只提示、不改默认**：`cli.size_hint`（输出 >1.5× 输入时提示 `-f WEBP/JPG` 的实测体积）、
  `cli.frame_step_hint`（视频默认步长 + 掩码复用 + 片段≥20 帧时提示静止水印可 1.8× 提速，滚动水印例外写明）。
- 守卫 `tests/test_cli_delivery_hints319.py`（9 例）：阈值/缺失值/替代格式命名/视频三前提 +
  `_print_result` **接线断言**（函数对但没被调用等于没有）+ PDF 不提示的例外。
- 未动的三个默认值（输出格式跟随输入、视频默认步长、opencv 通路跳过设备/OCR 初始化≈省 3–4s）
  已列代价与建议，待拍板。见 `docs/319`。

### 新增（阶段 318 ·「只动水印区」后处理作用域开关）
- 314b/316/317 三条实测指向同一凶手：误伤（水印**外**改动 46.9%–88.4%，且与预设无关）来自**后处理**，
  不是擦除。根因非 bug——阶段 60 裁决「掩码内不锐化、掩码外照常锐化」。
- 新增 `POSTPROCESS_PRESERVE_UNTOUCHED`（默认 **False**）+ `POSTPROCESS_PRESERVE_BAND_PX`（默认 8）：
  开启后后处理最后一步把「掩码 + 外扩带之外」**逐字节还原为原图**。缺原图/掩码/形状不符 ⇒ 静默跳过。
  纯 config 驱动 ⇒ UI/CLI/api/REST 四条链同时生效（不新增门面参数，规避 315 的漏接线坑）。
- A/B（60 用例，只翻这一个开关）：误伤中位 **50% → 0.8–2.1%**、**0/60 gain 变差**、
  **60/60 ΔPSNR 改善 2.2–3.7 dB**；`faint_tiled`/`grid` 由**净伤害**（−0.117/−0.172）转为 0 附近
  ——不是"擦掉了"（检回未变），是"停止帮倒忙"。残余 0–16.5% 落在**检测误报区**，归检测侧。
- 同进程 A/B/B/A 交替对照耗时 **0.99×**（跨会话 304s→680s 是机器状态，不是开关开销；已写入文档防误读）。
- 守卫 `tests/test_preserve_untouched318.py`（11 例，纯 numpy）：关时不变 / **开时确有差异**（先证反例构造成功）/
  差异只在作用域（含带宽方向反写检测）；另钉 `cv2.dilate` 默认边界值 DBL_MAX 陷阱与 OpenCV 5 不接受 bool 输入。
- **默认值未翻**（用户定「先看判决再定」）：建议 318b 做 CLI/设置页/REST 暴露后再看真实反馈。见 `docs/318`。

### 新增（阶段 318b ·「只动水印区」的门面暴露）
- 318 的能力此前只经环境变量可达（= LESSONS #298「能力存在但对外不可达」）。现接到四个门面，默认值仍不动：
  `postprocess(..., preserve_untouched=None)` 三态（None 跟随 config / 显式 True|False 逐请求覆盖）→
  `process_image`/`process_file` → `process_pdf`/`process_video`（逐页/逐帧）→ `api` 6 个入口
  （file/bytes/array/batch/pdf/video）+ `get_capabilities` 暴露 → REST `_collect_params` 白名单与
  `"true"/"1"/"yes"/"on"` 归一 → CLI `--preserve-untouched/--no-preserve-untouched`（三态）。
- 守卫 `tests/test_preserve_untouched_facades318b.py`（13 例）：**签名存在不算接上**——每例都替换下游
  并捕获实参；PDF/视频两例真造 1 页 PDF 与 3 帧 MP4，断言逐页/逐帧 `process_image` 收到开关；
  端到端那例先断言「关闭态带外确实变化」再断言「开启态逐字节不变」（LESSONS #304）。
- 未做：UI 设置页（318c，需同时进 `user_overrides` 白名单）；视频逐帧还原的帧间稳定性基准。见 `docs/318b`。

### 度量（阶段 317 · 修复侧真值探针——「擦干净了吗」第一次可算）
- 补齐项目最大的证据空洞：既有指标全在**检测侧**，ΔPSNR 用例底图是 640² **程序生成图**，
  真实照片侧从未有「未加水印的原图」⇒ 交付质量无数字可说。
- 新 `scripts/removal_truth_probe.py`：用 `benchmark_real/clean/` 的 **15 张真实无水印照片**作底图，
  复用自带水印合成器叠已知样式，`truth = marked − clean` 逐像素相减 ⇒ **像素级真值由构造保证**
  （比人工 bbox 严，半透明压到的背景也算）；全程走 `auto_preset=True` 的用户路径。
  两轴独立：`removal_gain`（擦干净了吗）与 `collateral_pct`（有没有乱动）。
- 60 用例结果（中位）：edge_text gain **0.939**、corner_logo 0.532、faint_tiled **−0.117**、grid **−0.172**；
  **faint_tiled 13/15、grid 15/15 为负收益**（处理后更偏离原图）；误伤 33.8%–72.7% 与预设无关。
  根因定位：`grid` 被正确推荐为 `ai_grid` 的 11 例全部负收益 ⇒ 病在**检测器看不见真实纹理上的点阵**；
  `ai_faint_tiled` 在真实照片上 **0/15 可达**；真值集 57 标注 style **100% 是角标**而角标仅 5/15
  被推荐成 `ai_logo`（推荐器对主打场景没真学会）。第一版**不设红线**，本数字即历史基线。
- 守卫顺手钉掉两个度量自身的 bug：`np.abs(uint8−uint8)` 回绕（残留虚高到 254）、
  `cv2.dilate` 默认边界值 DBL_MAX（沿四边凭空生出 24px 外扩带，collateral 漏计一整圈）。
- ⚠ 保留一条自我更正：冒烟单例曾推断「角标被误推成 ai_grid」，全量实测仅 3/60 ——
  单例证据不足以支撑跨用例结论（LESSONS #248④ 的现场复发）。见 `docs/317`。

### 修复（阶段 313 – 316 · 环境重建后接手审计，2026-10-22）
- **检测（313）**：OCR 文字框救援由「逐框中心无证据即整框填满」收窄为「**整图将返空才救援**」——
  原实现与 docstring 声称的设计意图不一致，实测单图一次填满 23 个文字框（9.1% 画面），
  `prompted_area_gate` 报 `ai_text_heavy` 覆盖率 **+48%**、`test_mask_param_calibration` 红。
  代价 committed 面 mask_recall 0.7809→**0.7700**（余量仍 +71%），换得 mask_coverage 余量 19%→**27%**、
  coverage_ratio 余量 13%→**24%** 且两道门禁转绿。基线经授权双开重锚。见 `docs/313`。
- **入口健壮性（314）**：`main.py`/`cli.py`/`server.py` 会 print `✅/❌/⚠️`，GBK 重定向下**任务完成后**
  崩溃、退出码 1（阶段 170 的元守卫只扫 `scripts/`，产品入口一直在射程外）。实现迁至
  `utils/console_encoding.py`（分发包不含 `scripts/`）并在三入口**业务模块导入之前**加固。
  新增非 ASCII 安装路径告警（中文路径下 libtorch 打不开权重，报 WinError 127「文件不存在」，
  实测一次拖垮 46 个用例）。见 `docs/314`。
- **UI 严重缺陷（314b）**：Gradio 6.x 往返会把编辑框图层**压成 RGB、丢弃 alpha**，而预填实现是
  「全图刷红 + 靠 alpha 表达掩码」⇒ 整幅画布被判为涂抹区，**「一键修复」重绘整张图**
  （实测 82.11% 像素被改、状态栏「你补画 413 万像素」而用户一笔没画）。服务端 2500+ 用例全绿
  也拦不住（测试直接喂 RGBA，从未模拟往返形态）。修复后编辑框 4,194,304→**63,106** px。
  另修 0 字节图片/损坏 PDF 的不可读报错。见 `docs/314b`。
- **验证缺口（315）**：新增 `scripts/media_gate.py` —— 项目宣称「图/PDF/视频全源」，但三个质量门禁
  **全部只测静态图**、`benchmark_real` 无任何媒体样本、PDF/视频仅有服务层 mock 单测 ⇒ 端到端零验证。
  现用真值集图像现场合成 PDF/视频，红线守「页数保持·水印区必须变化·输出可解码·帧数不塌」。
  实跑第一次即撞出 `api.remove_watermark_video` 漏 `preset`/`auto_preset`（服务层支持、PDF 门面也有）
  ⇒ 门面不对称，已补齐并加对称性守卫。见 `docs/315`。
- **文档纪律断层（315）**：`docs/` 缺 308–312；`STAGES.md` 自称逐阶段 SSOT 却止于阶段 171；
  本 CHANGELOG 止于阶段 300。已登记断层并补登有证据的 310–312 摘要（308/309 查无痕迹，不做推测补写）。
  见 `docs/308-312-阶段文档断层与Docker攻坚补登.md`。
- **实战演练（316）**：门禁全绿 ≠ 用户真能用。一个目录混装 **12 张原分辨率真图 + 4 页 PDF + 24 帧视频 +
  3 个坏文件**（0 字节 / 截断 PNG / PNG 改名 `.pdf`），一条 `cli.py --include-media -e` 吞下：
  141.6s、输入 17→输出 15、**自报计数与实际落盘一致**、PDF 不丢页、视频不丢帧、REST 独立往返成功；
  单张大图 5.0–7.1s（`auto` 实选 OpenCV 通路）。见 `docs/316`。
- **批量退出码（316）**：演练即撞出——`成功 15 / 失败 2` 却 **`EXIT=0`**，脚本与 CI 判不了成败、
  只能 grep 中文文案。新增 `cli._batch_exit_code`（0=全成功，**跳过不算失败** / 1=有失败 / 2=参数或输入缺失，
  与门禁族口径一致），顶层文档补契约段；守卫 4 例**成对**覆盖正反方向。
- **演练暴露、只登记未改的 6 项**（需产品拍板，出处 `docs/316` §六）：水印**外**改动实测 **46.9%–88.4%**
  （后处理全图作用，314b 同源）；水印没动的 a2/a3 仍得「质量分 100 · good」（无参考分分不清「没做」与「做好」）；
  非 PDF 内容被 MuPDF 静默按 1 页处理；预设置信度 0.01、触发特征「无」仍修复 12228px；
  输出命名不一致（`X_cleaned_HHMMSS.png` vs `X_已去水印.pdf`）；JPEG→PNG 单图体积 ×4.65。
- ⚠ **301–312 的逐条细节本段不代笔**：那批阶段由他人完成且无一手记录，逐阶段事实见 `docs/`
  与 `.workbuddy/memory/YYYY-MM-DD.md`。

### 数据（阶段 300 · 28 张真实豆包图入基准 + 重锚，mask_recall 余量 12%→24.8%）
- 用户供图 `D:\照片存储` 收编 **28 张**（c1–c11/c13–c15/s1–s14，2048×2048）入 `benchmark_real/`：真值标注 **29→57 条**，承诺面实评 **25→53 图**。**c12 与 c11 字节完全相同**（sha256 `06863b7c64d4…`），`collect_real_assets.py scan` 已按 hash 去重故不入基准（避免同图双计污染聚合）。
- **独立量取法**（不触 `tests/test_real_asset_preflight.py` 的 `auto:` 循环论证硬守卫）：跨 28 图**平均高通响应**——固定平台覆盖保留、自然纹理相消——干净浮出「豆包 AI 生成」文字足迹 `[1683,1936,319,71]`；再逐图在足迹内按自身响应取阈值掩码外接框（19 个不同 bbox，符合既有 `ai_logo_*` 逐图实测纪律，也过 preflight 的模板化/信息量双守卫）。
- **再锚后实测（53 图 · general）**：mask_recall **0.5111→0.5984**、recall 0.8400→0.9057、mean_iou 0.5876→0.6673、precision 0.0789→0.0758、mask_iou 0.313→0.3124、mask_coverage 0.0120→0.0137、coverage_ratio 2.21→2.47。**红线一条未动**，夹逼不变式全部仍满足；`real_gate --strict-scope` **EXIT=0**。
- **mask_recall 余量 ~12%→~24.8%**（仍是最薄一条，259 advisory 盯防）——「红线未随数据集重锚」的担忧实质性缓解；漂移告警线与硬线之间从 0.010 恢复到 0.089 的真实缓冲。
- **A/B 泛化诊断**（口径自证：原 25 图 box_recall 0.8400 与门禁基线逐位相同）：新 28 张 **0.9643**（27/28）> 原 25 张 0.8400（21/25）⇒ 检测器对新真实图泛化更优。唯一漏检 `c15`（密集小字母艺术字 + 水印低对比度，OCR 被装饰字母占满），经放大目视确认标注无误，属**真漏检**。
- **新增开放项**：两条未承诺通路面积漂移略超 15% 容差——`ai_faint_tiled` 2.38%→2.80%（+17.6%）、`ai_grid` 7.58%→8.88%（+17.2%），均远低于硬红线 7.5%/12.5%；成因为新图自然纹理更丰富的**构成效应**，非检测器回退。见 `docs/258` §六。
- 连带同步（经验 81）：`--update --force-update` 重锚基线后，同步 `tests/test_detector_audit100.py`(MSER_OFF) / `tests/test_overdetection99.py`(CONVERGED) / `tests/test_watermark_scope.py`(ai_logo 29→57) / `tests/test_redline_margin_health259.py`(余量带)；`MSER_ON` 等注入反例锚点保持不变。README / `docs/258` / `docs/298` / `docs/quality/product_scorecard.json` 的口径与指标一并刷新。

### 修复（阶段 300-b · `run_all_checks.py --help` 崩溃）
- `--cov` 的 help 字符串含字面 `%`，argparse 的 `_expand_help` 将其当格式符解析 ⇒ `--help` 直接以 `ValueError: unsupported format character` 崩溃（全角逗号紧邻百分号）。修：转义为 `%%`。验证：修复后 `--help` EXIT=0 且正常输出用法。

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

## [1.0.0] —— 核心能力与质量体系成型（2026-10-15 发布）

### 核心能力
- **多输入源**：图片 / PDF（多页拆分-逐页处理-回填）/ 视频（拆帧-逐帧-拼回，支持掩码复用 + 漂移护栏）。
- **智能检测**：8 类水印预设（general / ai_tiled / ai_signature / ai_corner / ai_text_heavy /
  ai_faint_tiled / ai_grid / ai_gradient / ai_logo / ai_multi）+ 自动预设推荐。
- **修复引擎**：LaMa（TorchScript）+ OpenCV（TELEA/NS）+ 插件引擎；双跑择优（opt-in）。
- **后处理深化**：Poisson 融合 / 纹理重建 / 伪影抑制 / 轻量超分 / 真·超分（ESRGAN / Real-ESRGAN）。
- **五入口**：Gradio 网页界面 / REST API / CLI / 插件系统 / 打包 EXE（PyInstaller）。

### 质量体系（88 起）
- **门禁家族 8 成员**：`quality_gate`（合成）+ `real_gate`（真实，五红线）+ `e2e_real_probe`（修复侧）+
  `mask_param_audit` + `clean_bench_probe` + `prompted_area_gate` + `perf_probe` + `docker_consistency_check`。
- 真值审计体系：`scripts/audit_selector.py`（覆盖度荒漠选点）+ `AUDITED_SINCE` 登记，
  已审 **45 个模块**（5j 深度续审链 276–290 收官，`--unaudited` 返回空表）。
- 注入式反例：每处修复均验证「守卫真会失败」（revert → 守卫 RC=1）。
- 经验库 `.workbuddy/memory/LESSONS.md`（281 条）；能力声明 SSOT `config.WATERMARK_TYPE_SCOPE`。

### 工程化
- 分层架构 `ui → services → core → utils → config`（严禁反向依赖，core/services 零 gradio）。
- **219 个测试文件、2500+ 用例；249+ 篇设计/审计文档**（规模 SSOT 见 README §4，`tests/test_readme_scale.py` 守护下限）。
- 依赖三档：`requirements.txt`（完整）/ `-min`（无 torch）/ `-gpu`（CUDA）。
- Dockerfile（CPU，含 GPU recipe）+ PyInstaller `build.spec`。

### 发布与真实数据验证（297–300）
- **可安装分发**：`pyproject.toml` `[project.scripts]` 三件套 `wme`/`wme-cli`/`wme-api`；wheel **0.66MB** + sdist **1.4MB**（纯 Python，不含基准大图）；`.github/workflows/publish.yml` 打 tag `v*` → 门禁 → 构建 → 发 PyPI（Secret `PYPI_API_TOKEN`）。
- **API 安全层**（299）：`services/api_security.py` 纯标准库 token 鉴权 + 进程内令牌桶限流，默认关闭 ⇒ 零行为变更。
- **真实数据基准**（300）：28 张豆包真实图入 `benchmark_real/`，承诺面实评 25→53 图；mask_recall 0.5111→**0.5984**（余量 ~24.8%），五红线全过、`real_gate --strict-scope` EXIT=0。
- 仓库已发布：`github.com/chu803/wme`。

---

## 合规声明

本工具**仅限**用于去除使用者本人拥有著作权的 AI 生成内容上的可见水印。详见 `LICENSE`。
