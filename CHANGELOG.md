# 更新日志 / CHANGELOG

> 本文件按「里程碑」归纳项目演进（而非逐阶段流水账；逐阶段细节见 `docs/`，共 286 份（2026-10-25 实测刷新，口径 `ls docs/*.md | wc -l`，含 INDEX；守卫见 326）。
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

### 修复（阶段 353 · api 字节入口的通道顺序 + job 的「假成功」）
- 第三轮审计报 7 条；**其中一条实测推翻了它的方向**：审计说「`_encode_bytes` 只对 TIFF/AVIF 红蓝互换」，
  但它的隔离实验喂的是 **BGR 数组**，而管线交出来的是 **RGB** ⇒ 实测（纯红 RGB → 编码 → 解码）
  **PNG/JPG 才是互换的那条（还是主路径）**，TIFF/AVIF 反而正确。⇒ 照单执行会去修本来正确的分支。
- 修三条：① 编码侧 `cv2.imencode` 前显式 RGB2BGR；② api 三个字节入口解码后补 BGR2RGB
  （**= 阶段 338 那个病在 api 层的同一份**：REST 入口与文件路径给的**不是同一个结果**）；
  ③ `job_manager` 先查结果对象的 status：PDF/视频服务失败**不抛异常**，worker 不看 status
  ⇒ 坏 PDF 作业被标 success、客户端随后取文件 404。
- 守卫 `tests/test_api_channel_and_job_status353.py`（4 例）：纯红经 PNG/JPG 编码后解码**仍是红**；
  **`api.detect_watermark_bytes` 与「读文件 → detect_watermark_mask」必须给出同一个掩码**；
  坏 PDF 作业必须是 `failed` 且 `out_path` 为空。反例（修复前 HEAD）实测：坏 PDF 作业被判 `'success'`。
- 另 4 条登记为开放项（`/api/detect` 忽略 preset/sensitivity、对比图导出丢失败项不报告、
  「保存为默认」写盘失败不可见、历史画廊索引可能指错记录）——后三条是条件性。
### 改进（阶段 352 · 「跳过 / 有 WARN」也要说出结论）
- 门禁家族退出码 **2** 表示「跳过」或「有 WARN 但不阻断」，而统一入口行首只印一个 `⏭`
  ⇒ 容易被读成「这一步什么都没检查」⇒「有 2 个 WARN 待决策」被吞进绿（阶段 330 记录的 #24）。
- 修法**只改显示、不改退出码契约**：`run_steps()` 在 `rc == 2` 时把子进程**最后一条非空输出**
  接在该行后面（`⏭ docker_static rc=2 … └─ 结论：无 ERROR，2 个 WARN（不阻断构建，但需知悉/决策）`）。
- **为什么不动退出码语义**：把「有 WARN」判成红属**对外口径翻转**，按纪律先给判决表
  （A/B 方案与代价已列在 `docs/330` §六）⇒ 先把证据摆到眼前，语义等拍板。
- 守卫 `tests/test_run_all_checks.py::test_skip_code_surfaces_the_child_conclusion`；
  rc!=0 的失败路径与 rc==0 的成功路径格式不变 ⇒ 既有断言不受影响。

### 修复（阶段 351 · CI 红 8 天的第二个原因：系统依赖写死了镜像代际的包名）
- 修完依赖区间（347）后 CI 仍红（#27/#28/#29，1m14s～1m32s）⇒ 还有别的原因。
- 查证：**Ubuntu 24.04（noble）起 glib 运行时包改名** `libglib2.0-0` → **`libglib2.0-0t64`**，
  且**过渡包 `libglib2.0-0` 被删**（launchpad #2063221）⇒ 写死旧包名的 apt 步在新镜像上
  `Unable to locate package` ⇒ **整 job 一分钟红**，门禁都跑不到。
  这是**镜像侧**变化（不需要仓库改动）⇒ 解释了「#14（10-18）绿、#15 起全红」的时间线。
- **两个原因症状完全一样**（都发生在"装依赖"那十几秒里）⇒ 只看时长区分不出来 ⇒ 必须分别验证。
- 修法：系统依赖**按镜像实际有什么来挑**（`GLIB=$(apt-cache show libglib2.0-0t64 >/dev/null 2>&1 &&
  echo libglib2.0-0t64 || echo libglib2.0-0)`），`ci.yml` / `publish.yml` 同改。
- 守卫 `tests/test_workflow_system_deps351.py`（4 例）：必须探测 t64 变体、安装命令必须用变量、
  不得写死旧包名、YAML 可解析且**每步都有 id**（阶段 332 的诊断依赖它）。
- 教训 #331：同一个 fast-red 症状可以有多条独立成因；修一条不绿要**并列假设**再验，
  「上次绿这次红」的第一嫌疑是**环境**（镜像/依赖），不是代码。

### 改进（阶段 350 · 媒体服务层（视频 / PDF）的用户可见文案归入 i18n）
- 337 把「质量诊断」链路收归 UI 后，媒体服务层仍在直接产中文句子（进度提示、`result.warnings`、
  `result.error`）⇒ **英文界面里视频与 PDF 链路全是中文**。
- 新增 `media_svc.*` 键段（中英各 26 条），**中文文案逐字不变** ⇒ 中文用户零感知、既有断言零变化；
  服务层改成 `t("media_svc.*", 参数…)`；**日志（`log.*`）保持中文**（日志不是界面文案）。
- 参数化处用占位符（`{src}{formats}{w}{h}{fps}{step}{codecs}{i}{plan}{iou}{threshold}`
  `{written}{total}{pages}{n}{msg}`），两侧同名以满足 `validate_i18n_parity`。
- 共改 26 处（视频 17 + PDF 9），全部用**精确文本替换脚本**（每处必须恰好命中一次，否则脚本报错退出）。
- 守卫 `tests/test_media_service_i18n350.py`（6 例）：英文侧零 CJK、行为级 en-US 返回值零 CJK /
  zh-CN 仍是中文、中文侧逐字回归、i18n 对称性仍空。
- **未做**：CLI 整体无 i18n 层（不 import `t`、无 `--lang`）——属产品决策，未做。

### 新增（阶段 349 · 真实平铺/点阵样本采集包：成对性机器判定 + 召回测量）
- #21（提升 grid 点阵检测召回）唯一阻塞是**真实样本**，样本能不能用取决于**成对性**
  （同一张文件的两种导出、像素逐位对齐、只多一层水印叠印）。`docs/P0` §六 写清了要求，
  但那是**给人读的** ⇒ 历史上两次成对图被当成真值用，判据是「人眼看像不像」⇒ 两次都判**不可用**。
- 采集包：`D:\wme-1.0.3\samples_grid\`（`README.txt` 要求与成对规则、`prompts.txt` 10 条点阵/网格水印提示词、
  `pairs\` 放样本处）。
- 工具 `scripts/grid_sample_check.py`：一条命令给出 ① **成对性逐对判定**（尺寸不一致 / 两张一样 /
  差异像素占比 >30% / 长边 <1024 ⇒ 不可用并打印原因）；② **真值像素数与差异占比**；
  ③ `--measure` 量出**当前检测召回**（真值区被检出的比例中位）＝ **#21 要提升的那个数**。
  退出码 `0` 够量且全可用 / `1` 有不可用项或不足 20 对 / `2` 目录问题（不能是 0）。
- 守卫 `tests/test_grid_sample_check349.py`（6 例）：两种目录形态都能发现、四种不可用情形、
  正常成对⇒可用且真值像素数算得出、空目录⇒退出码 2。
- 工具**只读**样本目录；样本入库后仍走「据实重锚基线」的流程（对外口径翻转先给判决表）。

### 修复（阶段 348 · 装出来的包缺顶层模块（命令启动即崩）+ `wme-cli --help` 崩溃）
- 把 wheel 装进**全新 venv** 后跑 `wme-cli --help` ⇒ `ModuleNotFoundError: No module named 'job_manager'`。
  根因：`pyproject.toml` 的 `py-modules` 漏了 `job_manager`，而 `server.py:75` 有 `import job_manager as JM`
  ⇒ wheel 里没有它 ⇒ **安装态**一 import 就炸，`wme`/`wme-cli`/`wme-api` **三个命令启动即崩**。
  **本地全绿的原因**：源码目录里 `sys.path` 含仓库根 ⇒ import 得到 ⇒ 这个缺陷**只在装出来的包里存在**。
  既有守卫只查「声明的都在磁盘上」；另一头「磁盘上的都在声明里」只覆盖顶层**包**、没覆盖顶层**模块**
  ⇒ 新增 `test_every_root_module_is_declared_or_exempted`（与既有那条配成一对，`conftest.py` 显式豁免）。
  ⚠ **已发布的 1.0.1/1.0.2 也有此缺陷** ⇒ 从 PyPI 装包的用户 `wme` 命令起不来。
- `cli.py --help` 崩：`ValueError: unsupported format character '?' (0x2192)` ——
  help 文本里的「实测误伤 50%→约 1%」被 argparse 的 `%` 展开当成格式串 ⇒ 改成 `50%%→`。
  守卫 `tests/test_cli_help348.py` **真跑一次 `--help`**（rc=0、含 usage、且「50%」「→」都还在）。
- **顺带第三次实证阶段 331**：写新守卫时测试自己又踩「父进程按 GBK 解码子进程 UTF-8 ⇒ `stdout=None`」
  ⇒ 新守卫显式 `encoding="utf-8", errors="replace"`。
- 证据（安装态）：修复前 wheel 顶层缺 `job_manager.py` 且命令崩；修复后 wheel 顶层齐、`wme-cli --help` 打出 usage、
  安装态 import 全部模块均 ok。**连带**：`v1.0.3` 的 tag 推在本修复之前 ⇒ 需再指一次。

### 修复（阶段 347 · CI 红了 8 天的真因：清单钉了「只有本机装得到」的版本）
- **从 Actions 列表截图倒推**：#14 绿（**5m23s**，跑完入口）→ #15 起全红（**26s～1m32s**，跑不完入口步）
  ⇒ 失败在**装依赖**；而 `git log ef64fd7..HEAD -- requirements-*.txt` 只有**一个**提交碰过清单：
  `0cb20e3`「对齐依赖清单到已验证 venv 版本」——**正是第一次红的 #15**。
- 那次把 6 个核心包钉成**本机 venv 的精确版本**（opencv-python-headless 5.0.0.93 / numpy 2.5.1 /
  Pillow 12.3.0 / scikit-image 0.26.0 / scipy 1.18.1 / pydantic 2.13.4），
  **这些版本在 PyPI 上都有**，但实测索引页的**轮子 Python 标签**显示：`numpy 2.5.1` 只有 cp312/313/314、
  `scipy 1.18.1` 只有 cp312~315 ⇒ **都没有 cp311 轮子**，而 CI 的矩阵是 **Python 3.11**
  ⇒ `pip install` 必然 `No matching distribution found`。
  （opencv 5.0.0.93 是 cp37-abi3 轮子 ⇒ 向前兼容；Pillow / scikit-image 有 cp311 ⇒ 无碍。
  本机是 **Python 3.13**，那两个版本有 cp313 轮子 ⇒ 本机一路绿灯、**只有 CI 红**——
  「本地装得上」不等于「CI 装得上」，这正是它能潜伏 8 天的原因。）
- **决定性推理（不需要联网）**：清单里其余精确 pin 在那次改动里**一字未动**，而 CI #14 用它们**装成功过**
  ⇒ 可解析；嫌疑精确落在那 6 个上。
- **不止影响 CI**：`requirements-min.txt` 同时是 README 让用户照着装的清单 ⇒ 这 8 天里照着装的用户**也装不上**。
- **修法**：把「装得到」与「验证过」拆成两份 —— `requirements-min.txt` 那 6 个改**区间**
  （**下界 = 上一版 CI 装成功过的版本**，**上界 = 本机验证过的版本**），
  新增 `requirements-verified.txt` 留档阶段 297 的本机验证组合（**不用于安装**）⇒ 那次决策的信息没丢。
- **守卫** `tests/test_requirements_ci_installable.py`（4 例）：精确 pin 只允许出现在「CI 装成功过」的集合里、
  区间下界不得高于已知可用版本、本机验证组合必须被清单上界覆盖、dev 清单同规则。
  **反例**：把守卫放到 `0cb20e3` 的清单上跑 ⇒ **逐个点名**那 6 个装不到的 pin
  ⇒ 这条守卫本可以在 10-18 就拦下 CI #15。
- **连带影响**：`v1.0.3` 的 tag 推在依赖修复**之前** ⇒ publish.yml 会在同一个 pip install 步骤失败
  ⇒ **PyPI 上没有 1.0.3**（发布链自守生效，但「推了 tag ≠ 发版成功」）⇒ 需把 tag 重指到修复后的提交再推。

## [1.0.3] - 2026-10-26

> 这一版的主题是「**把假绿和假成功都拆掉**」：门禁/退出码层的 6 处「说绿其实没跑」，
> 产品层的 6 处「说成功其实没做成」，以及 1 处「跨入口喂错颜色导致检测结果不对」。
> 全部改动都有可复现的反例（worktree 跑修复前代码）与守卫用例；统一入口 `--full --gates` 10 步全绿。

### 用户能直接感知的修复
- **批量去重不再返回旧图**：命中键从「文件哈希」升级为「哈希 + 全部决定输出的入参 + 掩码内容」——
  换引擎、改灵敏度、换涂抹掩码后重跑同一文件夹，现在会重新处理，而不是把上一次成品当本次结果交出去。
- **PDF / 视频的检测结果与单图页一致**：此前这两个入口把 BGR 喂给按 RGB 写的管线，
  同一张图检测到的像素差 **2.4 倍**（区域 6→7）；而颜色看起来正常，是因为一进一出两次错位互相抵消。
- **视频读到一半不再报「✅ 处理完成」**：解码提前停止时，现在会置「截断」并把少掉的帧数说出来。
- **一页坏不再毁掉整份 PDF**：此前任何 `RuntimeError`（含编码失败、渲染失败）都会中止整份文档，
  无视默认 `PDF_KEEP_FAILED_PAGE=True`；现在只有用户显式设了 keep=False 才中止。
- **「导出 ZIP」不再在 `exports/` 留一地全尺寸散图**；**导出按钮失败会有话说**（此前只 return None）。
- **「取消」不再对着空队列谎称已发送取消信号**；**视频页两个互斥开关的初始显示与实际生效状态对齐**
  （此前「逐帧智能推荐」显示勾选、实际被强制关闭）。
- **不再把「没写出来的文件」报成成功**：AVIF 编码器不可用时的降级路径不看写入结果就返回路径。
- **装出来的包能真的启动**：`wme` / `wme-cli` / `wme-api` 三个命令此前在**安装态**必然崩
  （wheel 漏了 `job_manager` 顶层模块，而源码目录里跑是好的 ⇒ 本地自测全绿也发现不了）；
  `wme-cli --help` 也会崩（help 文本里的「50%→」被 argparse 的 `%` 展开当成格式串）。
  ⚠ **已发布的 1.0.1 / 1.0.2 仍带这两个缺陷** ⇒ 从 PyPI 装包的用户 `wme` 命令起不来。
- **英文界面里视频 / PDF 链路不再是中文**：媒体服务层的进度、告警、错误都改走 i18n
  （中文文案逐字不变 ⇒ 中文用户零感知）。

### 质量体系
- 退出码按**步骤各自的约定**归一（pytest 的 2 是「收集期报错」，不是「跳过」）——
  此前一次 CI 侧的收集期 ImportError 会被判成「全部通过」。
- `ci.yml` / `publish.yml` 显式 `shell: bash` + `set -eo pipefail`：此前 `| tee` 把门禁退出码吞成 0，
  「不过门禁不发布」从未真正成立。
- 跨进程编码在边界钉一次（写侧 `PYTHONIOENCODING` + 读侧 `encoding=`，成对）——
  中文 Windows 上「一条用例没跑」与「19 条假红」同时存在过。
- `media_gate` 的「改动占比」加容差：此前「非水印区改动 94.9%」是**重编码噪声**（实测 |差|>8 的只有 3.0%）。
- 诊断步骤现在会先说「**哪一步红了**」，不再只留一句 `(no gate_output.txt)`。
- **依赖清单不再钉「本机装得到、CI 装不到」的版本**：清单改成区间（下界 = 上一版 CI 装成功过的版本，
  上界 = 本机验证过的版本），本机验证组合另存 `requirements-verified.txt` 留档。
  实测：`numpy 2.5.1` / `scipy 1.18.1` **没有 cp311 轮子**，而 CI 矩阵是 Python 3.11 ⇒ 装不上。
- **CI 的系统依赖按镜像实际有什么来装**：Ubuntu 24.04 起 glib 运行时包改名
  `libglib2.0-0` → `libglib2.0-0t64`（过渡包被删）⇒ 写死旧包名会让整个 job 一分钟红。
- 入口在「跳过 / 有 WARN」时会把子进程自己的结论接出来（`⏭ … └─ 结论：无 ERROR，2 个 WARN…`），
  免得「有 WARN 待决策」被读成「什么都没检查」。

### 详见
逐阶段细节见 `docs/`（阶段 330–346）与 `docs/INDEX.md`；门禁口径与基线见 `docs/quality/product_scorecard.json`。

### 修复（阶段 346 · 门禁那 94.9% 是重编码噪声，「改动占比」加容差）
- 完整门禁里 `media_gate` 打出「视频 非水印区改动 **94.9%** | 全图改动 97.5%」。用门禁自己的构件复现
  并打出非水印区 |差| 直方图：**48.8% 只差 1~3 级、45.6% 差 4~8 级、≥17 级只有 0.3%**，
  均值差 **3.83/255** ⇒ 视频/PDF 必须重新编码，压缩让几乎每个像素动 ±1~3；而判据是「差 > 0 即算改动」
  ⇒ 该数**必然接近 100%**。**那是指标失真，不是损伤。**
- 顺带修名不符实：`outside_diff` 原是「整幅图把 bbox 区置零后的均值」（被稀释），不是「非水印区均值」
  ⇒ 改为用掩码取真正的非水印区像素统计。
- 改法：**红线一条没动**（R1 页数 / R2 水印区必变 / R3 可解码 / R4 帧数不塌），只修诊断可信度：
  新增 `DIFF_TOLERANCE = 8`，改动占比按 `> 8` 判定；docstring 补容差与实测依据。
  同场景：视频非水印区改动 **94.9% → 3.0%**、全图 97.5% → 3.7%；PDF 第 2 页 61.8% → 1.5%。
  **把失真指标当真去改产品会走偏**（#308 的教训：先量上界再决定动什么）。
- 守卫 `tests/test_media_gate_metrics346.py`（4 例）：相同帧 ⇒ 0；**整体 ±2 噪声 ⇒ 0**；
  **bbox 内 ±30 仍被算成改动**（容差不许掩盖真损伤）；容差常量在范围内且 `diff_stats` 真的用它、无 `>0` 残留。
  `media_gate` 修后 rc=0。
- 写测试两坑（已写进注释）：桩返回 int16 被 `cv2.absdiff` 拒；画布 64px 时 `BBOX_DILATE=48` 把整幅图
  罩进 bbox 区 ⇒「非水印区」零像素、指标恒 0 ⇒ **测区域类指标前先确认区域真的有像素**。

### 修复（阶段 345 · 视频页互斥开关：显示勾选、实际被服务层强制关闭）
- **判决先行**：服务层 `detect_auto = bool(auto_preset) and not reuse`（`video_service.py:493`）
  的语义是对的（阶段 27：复用掩码 = 固定水印位置，整段共用一个预设更合理）⇒ 不改它、不改 `config` 默认值；
  **错的只是 UI 初始状态**：`v_reuse` 初始 `VIDEO_REUSE_MASK`=True（复用开着），
  而「逐帧智能推荐」复选框初值写死 `True` ⇒ **显示勾选、实际被 `and not reuse` 强制关闭**；
  检测模式下拉照常可点但选了被 `preset=None` 忽略；它自己的 info 写着「勾选『复用水印位置』时自动关闭」
  ——**文案与实际相反**。唯一能纠正它的 `v_reuse.change` 只在用户动手时触发，构建期不跑。
- 修法：纯函数 `video_recommend_defaults(reuse=None)` 按同一套语义推出
  `{reuse, auto_preset, auto_preset_interactive}`，UI 构造处用它（复选框值与可点性、
  检测模式下拉 `interactive=not auto_preset`）——**不新增控件、不改判定、不改默认值、
  不改用户手动勾选后的行为**，只是把「显示」对齐到「实际」。
- 守卫 `tests/test_video_recommend_defaults345.py`（4 例）：含**核心不变量**（把显示的初值代入服务层公式，
  结果必须与显示一致，且「复用开着 ⇒ 推荐关」成立）、按本机真实默认值验、以及**接线守卫**
  （初值必须来自 helper，不许再写死 `value=True`——写死就是本缺陷的形状）。HEAD 上 4 条全红。
- 第二轮审计 7 条**全部处理完**：F1(338)/F2(340)/F3(339)/F4(345)/F5(343)/F6(342)/F7(341)。

### 测试（阶段 344 · 入口里唯一反复变红的用例：就绪判据从 TCP 提到真实 HTTP 往返）
- `test_api_security299::test_post_without_token_401` 今天两次在完整入口变红、隔离跑两次全绿
  （记录在案的「全量偶发红＝夹具竞态」，不放宽断言）。旧 `_wait_ready` 只做一次 TCP 连接就返回，
  而 `listen` backlog 会替应用接受连接 ⇒ 端口通 ≠ `serve_forever()` 已在 accept；
  3400 条用例并跑时随后那个真 POST 就撞上 `ConnectionError`。
- **第一版修错了**：一律改成发一个真请求探之后**隔离跑反而更红**（`实际 429`）——
  限流夹具 `rl_server` 是 1/min·burst 1，建服务时也走同一个 `_wait_ready`
  ⇒ **探针把唯一那个令牌用掉了**（探针不是只读的：它会碰限流、计数、配额、缓存、日志、幂等键）。
- 最终形态：探针**开关化**，只在限流宽松的 `secure_server`(100000/min) 与 `open_server`(默认) 开启，
  `rl_server` 保持 TCP-only。验证：隔离 + 6 个 CPU 竞争者下 `test_api_security299 + test_api` 均 rc=0。
  **产品代码一行未动**；教训落 LESSONS #324（含流程判据：这类"更严谨"的改动必须先在真实约束下跑一遍再提交）。

### 修复（阶段 343 · 导出按钮「点了没反应」＝反馈停在半路）
- `ui/tab_media.py` 的导出对比帧图 / 帧拼图所有失败路径都 `return None`，而 `outputs` 只接一个
  `gr.File` ⇒「没反应」与「成功」完全不可区分；`run_video` 还会往同一组件写 `None` 清掉上次导出。
- 关键证据：`media.video.export_no_video` / `export_done` **中英双语早就在 i18n 里**，
  全仓 grep 无任何代码引用 ⇒ 不是没想到，是**文案先就位、接线没人补**；「有文案」还会让后来人
  误以为功能已实现。
- 修法：两个处理器统一返回 `(文件或 None, 一句真话)`，接线 `outputs=[v_export_file, video_log]`
  ——复用本页已有的结果日志框（`:929`），**不新增常驻组件**；四条出口各有措辞
  （缺视频 / 成功带路径 / 服务层返回 None / 服务层抛异常被捕获 + `log.exception`）；
  新增键只有 `media.video.export_fail`（双语对称，`validate_i18n_parity()` 实测 `[]`）。
- 既有守卫反向钉住旧契约：`test_tab_media_audit112` 接线表写着 `(4,1)/(1,1)` ⇒ 把「没有反馈」永久化；
  按 #81 同步到 `(4,2)/(1,2)` 并写明理由，另补更硬的判据：这两个函数**每条 return 都必须是 2 元组**
  ⇒ 以后「失败就 return None」的分支会被直接抓住。
- 守卫 `tests/test_export_feedback343.py` 6 例（含「三个键必须真的被引用」防回到原状）；
  HEAD 上 **6 条全红**，修复后连同 audit112 + i18n 对称守卫全绿。
- 第二轮审计到此：F1(338)/F2(340)/F3(339)/F5(343)/F6(342)/F7(341) **六条已修**；
  F4（逐帧智能推荐在默认设置下恒不生效、与自己的 tooltip 矛盾）属默认值/交互语义判断，
  按纪律先出判决再动，登记为待核。

### 修复（阶段 342 · 「导出 ZIP」把中间产物留在 exports/，谁都收不回）
- 两条同形路径：`export_history_as_zip(fmt=...)` 逐条 `reencode_image(..., out_path=None)`、
  `ui/tab_settings.on_export_compares` 逐条 `export_compare(...)` 不传 `out_path`
  ⇒ 全尺寸散图落到默认目录 `config.EXPORT_DIR`（唯一化命名、永不覆盖），打完 ZIP 没人删
  ⇒ 用户点一次导出就在 `exports/` 留下 N 个**永久**文件。
- 清理功能结构上救不了它：`scan_orphans` 文档明写不扫 `exports/`（用户成品目录，`cache_service.py:855`），
  那条排除本身是对的 ⇒ 唯一能修的位置是产生者自己。
- 为什么藏这么久：`export_service.py:484`（阶段 147）注释精确知道「中间产物走默认目录」，
  但当年的解法是把 `config.EXPORT_DIR` 临时改指向临时目录 —— **绕开的是自检，用户路径一直在污染**；
  且**注释写着"临时产物"不等于有人负责删**（经验 169 同族）。
- 修法：谁产生谁清理、写在函数内部 —— `with tempfile.TemporaryDirectory()` + 逐条显式 `out_path`，
  **ZIP 必须在 with 块内写**（块一退出目录就没了，写在外面就是空包）；交付物仍只有 ZIP 落在 `exports/`。
- 守卫 `tests/test_export_zip_orphans342.py`（3 例：转 WEBP 后只剩 ZIP / 不转格式控制组 /
  对比图 ZIP 后只剩 ZIP），并同时断言 ZIP 条目数与后缀正确。HEAD 反例 **2 红 / 1 绿**。
- 另一半 F5（导出帧按钮失败零反馈、`run_video` 写 None 清掉上次导出）**未修**：需要新增状态文案 +
  输出组件，属 UI 契约变更，留在任务 #31 下半场。

### 修复（阶段 341 · 一页渲染失败就把整份 PDF 中止）
- 逐页循环里 `if not PDF_KEEP_FAILED_PAGE: raise RuntimeError(...)` 与外层 `except RuntimeError: raise`
  配对，但那层 `try` 包住**整段逐页处理**：`_encode_page_image` 自己抛的
  `RuntimeError("页面图像编码彻底失败…")`（`:316`）与 PyMuPDF 的渲染/插入错误同为 `RuntimeError`
  ⇒ **一页坏就中止整份文档**；默认值是 `PDF_KEEP_FAILED_PAGE=True`（`config.py:2055`），
  同一函数还写着「单页异常不该毁掉整份文档」⇒ 代码与注释、配置承诺三者互斥。
  用户表现：20 页里第 3 页编码失败 ⇒ 整份「处理失败」，而不是 19 页正常 + 1 页保留原样。
- 修法：新增专用异常 `PdfAbortOnFailedPage(RuntimeError)`（仍是 `RuntimeError` 子类，上游兜底不变），
  捕获点改为 `except PdfAbortOnFailedPage: raise` ⇒ 其它 `RuntimeError` 落回单页韧性。
  判据：**「我要主动中止」不能和「事情出错了」共用一个异常类型**（#319 宽口子同族）。
- 顺手统一一个语义分叉（非审计所报）：`keep=False` 原本只在「管线返回 failed」时中止，
  「本页抛异常」那条路径根本不读这个开关 ⇒ 同一开关两种解释；现在两条路径同一判断
  （配置项含义必须与失败**方式**无关，只与「这一页没做成」有关）。
- 反例（HEAD 实测）：三页文档、第 2 页抛 `RuntimeError`、`keep=True` ⇒ HEAD `status="failed"`（整份中止）；
  修复后 `"success"`、`failed_pages=1`、`skipped_pages=2`、输出 `page_count==3`（坏页原样保留、页数不塌）。
- 守卫 `tests/test_pdf_page_abort341.py`（4 例，含「专用异常仍是 RuntimeError 子类」的防退化锚）；
  既有 `test_pdf_service_audit131` / `test_pdf_history` 修后仍绿。
  桩再次踩同一条（339/340）：第一版给 `status` 填了不存在的 `"no_watermark"` ⇒ 正常页被计入
  `failed_pages`，差点把「测试自己错」报成产品缺陷；真实取值域见 `auto_pipeline:192`。

### 修复（阶段 340 · 画廊点「第 240 帧」，对比面板渲染的是第 4 帧）
- 两个 `index` 混用：画廊标题是 `extract_sampled_frames(..., return_indices=True)` 的**真实帧号**，
  而 `gr.Gallery` 的 `evt.index` 是**位置**（0..5）。处理器把位置直接写进滑块
  （滑块范围却是 `config.VIDEO_MAX_FRAMES`）并交给 `run_video_compare`（docstring 明写「抽帧序号」）
  ⇒ 点「第 240 帧」渲染第 4 帧，而 `media.video.browse_info` 承诺「点击任一帧即可加载到下方对比面板」
  ⇒ **看起来成功、实际给的是另一帧**。
- HEAD 行为取证（桩捕获入参直接调旧处理器）：`点第 5 格（标题 240）实际渲染 frame_idx = 4`。
  新用例在 HEAD 上 5 条全红，但部分是「新函数尚不存在」所致 ⇒ **以行为取证为反例主体**，不拿 ImportError 冒充证据。
- 修法：`_VIDEO_BROWSE_INDICES` 按结果视频缓存「位置→帧号」（画廊组装时顺手记下）；
  `_browse_frame_indices` 未命中时用**同一个** `extract_sampled_frames` 重抽（不另写抽样公式，避免第二套真值来源）；
  纯函数 `gallery_position_to_frame`（越界/无表/非整数 ⇒ None）；处理器用映射后的帧号，
  **拿不到映射就明说「没选中」**，不许渲染另一帧冒充。缓存的理由：抽 6 帧需解码 6 张图，点选是即时交互。
- 守卫 `tests/test_video_browse_frame340.py`（5 例：纯映射 + 处理器用映射后帧号 + 滑块停在真实帧号 +
  无映射不渲染 + 越界安全）。既有 `test_tab_media_audit112` 只钉 arity（3 入 2 出）修后仍绿
  ⇒ 抓不到这条是因为**形状对了但语义错了**（#74/#167）。
- 又记一笔替身纪律：`tab_media._normalize_file_value` 返回**字符串**（`:105`，与 `tab_batch` 的列表版不同源），
  第一版打桩成列表 ⇒ `TypeError: _path_isfile ... not list`（339 同条教训）。

### 修复（阶段 339 · 视频解码提前停止被报成「✅ 处理完成」）
- `video_service` 逐帧循环 `if not ok: break` 把「读完了」与「读到坏帧」当成同一件事，
  而 `truncated` 只在「达到帧数上限」那一支置位 ⇒ 解码中途停止时（VBR mp4/mkv 常见，
  `CAP_PROP_FRAME_COUNT` 常虚高）写出 210/900 帧也会 `status="success"`、`truncated=False`、
  `warnings` 空、`on_progress(1.0, "视频处理完成")` ⇒ 用户以为整段修好，实际后 3/4 画面没进结果视频。
- 修法不改判失败（那 210 帧有效，判 `failed` 会误导），而是**把少掉的部分说出来**：
  `not truncated and total > 0 and written < total` ⇒ 置 `truncated` 并追加一条 warning；
  `ui/tab_media.py:522` 逐条显示 warnings ⇒ 提醒真能被看见。前置条件写全：
  `total > 0`（有些容器读不到帧数，不能拿 0 去比）、`not truncated`（上限截断已报过，不重复追加）。
- 守卫 `tests/test_video_truncated339.py`（3 例，`_FakeCap` 伪造「元数据 5 帧、实际交付 2 帧」）：
  提前停止必须说 `2/5` / 帧数齐平不许凭空冒出告警 / 上限截断仍只报一次。
  HEAD 反例 **1 红 / 2 绿**（红的是要修的，绿的两条证明没把正向路径改成新噪声、原有语义没退化）。
  踩坑：`process_video` 第一步 `isfile(src)` ⇒ 光伪造 `VideoCapture` 不够，必须真有 src ——
  **替身要按被测函数的真实前置条件造**。
- 登记同源未修项：`video_service` 多处**服务层硬编码中文**（`on_progress(1.0, "视频处理完成")`、
  `f"处理第 {idx+1}/{plan} 帧……"`、各 `warnings.append`）⇒ 英文界面的视频进度与告警仍是中文，
  属 337「文案归 UI」尚未推到的层，本条把视频服务层明确加进该开放项。

### 修复（阶段 338 · PDF 与视频把 BGR 喂给了 RGB 管线：同一张图检测像素差 2.4 倍）
- 契约在代码里：`load_image` 返回 `rgb`（单图/自动/批量都经它）、`core/detector` 按
  `cvtColor(rgb, RGB2GRAY)` 取灰、同文件 `video_service.extract_sampled_frames:210` 早就做了 `BGR2RGB`
  ⇒ 约定公认，**只有逐帧循环与 PDF 没遵守**：`_page_to_array` 把 PyMuPDF 的 RGB 主动翻成 BGR、
  `process_video` 把 `cap.read()` 的 BGR 原样喂进管线。
- 实测同一张 `ui/assets/sample_watermarked.png`：RGB ⇒ mask 4292 px / 6 区域 / 覆盖 1.05%；
  BGR ⇒ **10328 px / 7 区域 / 2.52%** ⇒ 同一个水印单图页擦得干净、PDF/视频页残留或擦过头；
  颜色类先验（白底板 V/S、角标模板 NCC）也在错通道上算。
- **为什么肉眼看不出来**：两处错互相抵消——进管线把 BGR 当 RGB（检测错），出管线又按 BGR 交给
  `cv2.imencode`/`VideoWriter`（那确实是 BGR 约定）⇒ 成品颜色对、只有检测质量被悄悄拖走。
  根因还有一条：`auto_pipeline.process_image` 的 docstring **自己就矛盾**（摘要行「RGB 数组」、
  参数行「BGR，OpenCV 默认排布」）⇒ 已订正为「以代码为准 + 调用方负责转 RGB」。
- 修法：管线内外**各只翻一次**。`_page_to_array` 返回 RGB（灰度页 `GRAY2RGB`）；
  `_encode_page_image` 入参改 `rgb` 并在函数开头做唯一一次 `RGB2BGR`；`process_video` 读到帧即转
  `frame_rgb`、循环内 `out_frame` 全程 RGB（原来是 BGR 的 `frame` 与 RGB 的 `result_rgb` **混排**）、
  `writer.write` 前唯一一次翻回。附带修好 `render_pdf_first_page` 首页缩略图（以前 BGR 当 RGB 交给
  Gradio ⇒ R/B 反色）。
- 守卫 `tests/test_channel_order338.py`（3 例）：现造纯红 PDF 页断言 R 高 B 低；编码往返断言「恰好翻一次」；
  造 3 帧纯红小视频 + monkeypatch 捕获 `process_image` 入参断言收到 RGB，并读回输出帧断言颜色没错位。
  HEAD 上 worktree 反例 ⇒ **2 红 / 1 绿**，绿的正是「编码往返」（它只量颜色，而颜色是两处错抵消的结果），
  这个分布把机理钉死。踩坑：`cv2.imread` 读 MJPG-AVI 首帧返回 None ⇒ 得用 `VideoCapture.read()`。
- 既有 `test_pdf_service_audit131` / `test_video_service_audit113` / `test_pdf_history` /
  `test_video_reuse_drift_guard` 修后全绿 ⇒ 过去**没有任何断言覆盖通道顺序**，这解释了它为什么能活这么久。

### 修复（阶段 337 · 质量诊断文案所有权：服务层产键、UI 产句子；泄漏是双向的）
- **修复前实测那行**：`en-US ⇒ 🔎 Quality: pic.png score 75 (warn) — 改动区域过大…可能修复过头（误伤原图）。`
  ⇒ 英文界面拿到中文诊断；`zh-CN ⇒ …分75（warn）— …` ⇒ 中文界面拿到英文原值 `warn`。
  **同一行两种泄漏并存**；审计只报了前一半，反向泄漏是读消费点时自己看出来的
  （`level` 从未被翻译，而 `report_service` 早有正确写法 `t(f"report.quality_{level}")` ⇒ 同一件事三种做法）。
- **消费点靠 grep 数出来**：审计报了 `ui/tab_batch.py`、`ui/tab_auto.py`，漏了 **`cli.py:539/769`**
  （`f"[质量分 {q.score} · {q.level}]"` 与 `f"… {q.level} —— {q.detail}"`）。
- **修法**：`QualityReport` 删 `detail`（硬编码中文句子），换成 `detail_key/detail_base_key/review_floor`；
  `detail_base_key` 保住**阶段 87** 的语义（跌破复核线时要同时说「低于复核线」与「原诊断」，
  否则 `level=review` 配「修过头」= 方向相反的报告）。渲染收敛为 `level_text()`/`detail_text()`
  两个唯一实现，批量页与 CLI 共用；`ui/tab_auto` 改用新键 `auto.msg.postprocess_gain`，
  数据取服务层**已返回**的结构化字段；i18n 新增 `quality.detail_*` 5 键（中英对称，`validate_i18n_parity` 守卫）。
- **CLI 边界如实说明**：`cli.py` 完全没有 i18n 层（不 import `t`、无 `--lang`），其自身中文标签本轮**没动**
  （属「CLI 要不要国际化」的产品决策）；本次只让它不再把英文原值与中文句子混着打。
- **守卫**：`tests/test_quality_i18n337.py`（8 例，判据取自 LESSONS #72：**语言切换 + CJK 码点扫描**）——
  报告对象里不许再有中文字符串且 `detail` 字段必须已不存在、en 侧零 CJK 且不回退成键名、
  **zh 侧不许出现英文原值**、断言对象是用户实际看到的那一行、复核叠加时中英都带原诊断、新键中英对称。
- 反例闭合：新测试在修复前 HEAD 上**收集期即 ImportError**（新 API 不存在），故另用一次性探针在 worktree
  取「修复前」实测输出（用完即删）；修复后 en-US 零 CJK、zh-CN 显示「告警」。定向回归 rc=0。
- **没做整族 AST 守卫**（如实说明）：`services/`、`ui/` 里有大量日志中文，静态判据要么误报一片、
  要么要排除 `log.*`（排除规则本身又是手抄清单）⇒ 行为级判据更准也更难绕。

### 修复（阶段 336 · 批跑结束后按「取消」谎称「已发送取消信号」）
- `ui/tab_batch._CURRENT_QUEUE` 批跑开始时赋值、**从不置回 None**，取消处理器（含快捷键 `Shift+C`）只判
  `is not None` ⇒ 批跑结束后再按取消仍回「⏹ 已发送取消信号，未开始的任务将不再处理…」，
  对着一个空队列声称做了事。与 330–335 同族：说不清的输出，这次说的是「我做了什么」。
- 修法两处一起收（缺一处仍退化）：① 生成器跑队列那段包 `try/finally`，`finally` 清空句柄
  （正常结束 / 异常 / 前端提前关闭三种都覆盖）；② `cancel_batch()` 改看队列真实状态
  （`pending + running == 0` ⇒ 用既有 `batch.msg.cancel_none` 说真话），**不新增 i18n 键**。
- 守卫 `tests/test_batch_cancel336.py`（5 例）：跑前取消；**排空后取消不得声称发信号、且不得调 `cancel()`**；
  有 pending 必须真取消（断言 `is_cancelled()`）且跑完再按必须**改口**；生成器源码必须含
  `finally: _CURRENT_QUEUE = None`（防退化）。反例闭合：worktree 跑修复前 ⇒ **3 红 / 2 绿**。
- 写测试时自己踩两坑（对 `queue_worker` 语义的假设错，非产品错）：`run()` 是**生成器**，不消费等于没跑；
  `cancel()` 只置队列级标志，任务状态要等 `run()` 消费时才变 CANCELLED ⇒ 断言应看 `is_cancelled()`
  而不是 `summary()["cancelled"]`。**断言要按被测对象的真实语义写。**

### 修复（阶段 335 · 产品写图还在用 `cv2.imwrite`，中文路径下静默写空却返回路径）
- 本机实测：`cv2.imwrite(<含中文目录>/结果.png, arr)` **返回 False 且一字节都不写**（不抛错）。
  仓内早已知道（`save_image` 用 `imencode + open()` 绕开、`compare_service.py:389` 写成显式规则），
  但规则**没覆盖整族**，漏两处产品路径。
- ① `core/image_utils._save_avif()` 第三级回退 `cv2.imwrite(...)` 后**无条件** `return png_path`
  ⇒ 上层 `file_service.save_result` 记「处理结果已保存」、调用方拿到 `status="success"` 与不存在的路径。
  ⚠ 可达性如实说：本机 `cv2.imencode(".avif")` 抛错，但 **Pillow 12.3 原生支持 AVIF ⇒ 分支②成功**，
  审计那句「没装 pillow-avif-plugin ⇒ 必走分支③」**不成立** ⇒ 属**条件性缺陷**（旧 Pillow / 无 libavif 构建才暴露），
  测试用 monkeypatch 关掉分支①②逼出分支③。
- ② `services/cache_service._materialize_original()` 同样不看返回值 ⇒ **只要缓存目录含中文就每天发生**：
  原图没落盘而历史登记成功 ⇒「重新处理」要读的原图不存在。
- 修法：两处统一走已有 `save_image(path, arr, fmt="PNG")`（不另写一份落盘逻辑）；`cache_service` 延迟导入
  避开 core ↔ services 导入环，并删掉已无用的 `import cv2` 依赖检查。契约边界写清：AVIF 不可用**照样降级不崩**，
  新抛的是编码/写盘真失败（那种情况返回路径就是撒谎）。
- 守卫 `tests/test_avif_fallback335.py`（6 例）：ASCII/中文目录都要写出**能读回来**的图、编码失败抛 IOError、
  缓存原图在中文目录必须落盘、`core/services/ui/utils` 产品代码禁 `cv2.imwrite`（`__main__` 自检除外，目录机械枚举）。
- **两次自我打脸（值得记）**：判据写错——`" == " in ast.dump(node.test)` 认不出 `if __name__ == "__main__":`
  （dump 里是 `ast.Eq`，没有 `" == "`）⇒ `__main__` 子树一个都没排除，把 4 处纯自检误报成产品缺陷，
  差一点为过自己的守卫去改 4 个不该改的文件；验证通道用错——读回用了 `cv2.imread`，它自己就因中文路径
  返回 None ⇒ 红在产品之外，改回用被测系统自己的 `load_image`。**写守卫时要连守卫自己一起验；探针要走在用户实际走的通道上。**
- 反例闭合：worktree 跑修复前 ⇒ **4 红 / 2 绿**（绿的两条是 ASCII 正常行为，留着当锚）；修复后 6 全绿、
  定向回归 77 passed。

### 修复（阶段 334 · 批量去重键只认文件哈希 ⇒ 换引擎/换掩码会把旧图当新结果交出去）
- **缺陷**：`services/cache_service.find_by_hash` 只比 `status/file_hash/result_path`，
  记录里已存的 `mode/engine/params` 一个都不看；`ui/tab_batch._process_one` 又把这段排在**掩码生成之前**
  ⇒ 换引擎、调灵敏度、换完全不同的涂抹掩码重跑同一文件夹，每张直接复用上一次成品，
  日志记「✅ 命中缓存，已复用历史结果」。**是把错图交给用户并声称成功**，不是省时间。
- **修法**：`HistoryRecord` 新增 `cache_sig`（去重命中键）；`find_by_hash(file_hash, cache_sig)` 只命中签名一致者，
  记录签名为空（旧数据 / 其他页签）⇒ **不命中**。签名由 `_process_one` **自己的 `locals()` 入参快照**派生
  （26 个参数不手抄清单；新增影响输出的参数自动进键），手动模式改为**先定位掩码再算键**，
  掩码文件哈希进入键。代价如实写明：修完所有历史缓存退化为「第一次要重算」，换掉的是错命中这条路。
- **同轮另修两条形态缺陷**：
  ① `gr.File(file_count="directory")` 返回的是目录里的**每个文件**，旧代码取 `[0]` 当目录 ⇒ `isdir()` 恒假
  ⇒ **手动掩码批量整条功能死路**（新增 `mask_candidates()` 建 `{主干: 路径}` 索引，目录形态继续兼容）；
  ② 批量输入白名单误用 `_MASK_EXTS` ⇒ `.tif/.tiff/.jpe/.jfif` 被**静默丢弃**（挑 20 张 TIFF 报「未选择图片」）
  ⇒ 改从 `config.SUPPORTED_INPUT_FORMATS` 派生（单一真值来源），`select_batch_inputs()` 返回 `(kept, dropped)`
  并把 dropped 写进 `log.warning`：**不能让用户的文件凭空消失**。
- **守卫**：新增 `tests/test_batch_cache_key334.py`（11 例）——命中键语义（签名不符不命中 / 不传保持旧行为 /
  持久化读回）、签名随任一输出参数变化、掩码内容进键、同参数仍该命中、TIFF 白名单、Gradio 目录形态、
  以及「签名必须仍从 `locals()` 派生」的反退化锚。
- **反例闭合**：`git worktree add --detach temp/wt334 HEAD` 在**修复前**代码上跑同一套用例 ⇒ **10 红 / 1 绿**；
  唯一绿的那条是「同参数重跑该命中缓存」，正是它该有的表现，兼作「别把缓存正向价值改坏」的锚。
  修复后 11 例全绿；定向回归（`test_tab_batch_helpers` + `test_image_history_record` + `test_cache_service_audit132`）
  65 例全绿。
- **测试层根因**：②③ 长期存在是因为既有单测都在函数层面喂理想形态（目录字符串、干净后缀），
  没有一条走 Gradio 的真实返回值 ⇒ 夹具形态与真实形态不同源（#167 手抄清单的同族变体）。
- **本轮没修**（如实列）：AVIF 降级不看 `cv2.imwrite` 返回值却回 `success`；`tab_auto`/后处理明细文案绕过 `t()`；
  批跑完成后按「取消」谎称已发信号（`_CURRENT_QUEUE` 从不置回 None）；`tab_single`/`tab_auto` 只写不命中。

### 修复 + 订正（阶段 333 · 331 那条「约 50 个未加固」是虚高，精确判据下是 8 个、真会崩的只有 2 个）
- **订正 #1（数字）**：331 的 50 来自粗判据「源码出现任何非 ASCII」，**注释与 docstring 也算** ⇒ 虚高。
  换成 AST 判据「**字符串常量**（剔 docstring）含 GBK 编不出的字符」：69 未加固 → 46 命中 / 10 未加固
  → 排除 `tests/`（pytest 自管编码；实测全仓无人把测试文件当脚本 spawn）⇒ **8 个产品文件**。
- **订正 #2（性质）**：用 `git show HEAD:<file>` 落到 `temp/`（保 `_ROOT` 计算不变）在
  `PYTHONIOENCODING=gbk` 下实跑 ⇒ 只有 `services/video_service.py`（**rc=1、4 次崩溃**）与
  `services/postprocess_service.py`（rc=0 但**1 次崩溃被静默吞掉**）是**缺陷**；
  其余 6 个（`utils/exceptions.py`、`utils/i18n.py`、`utils/recommend_view.py`、`ui/tab_history.py`、
  `ui/components.py`、`ui/app.py`）今天不崩 ⇒ 属**预防性统一约定**，不当成 bug 报。
  `ui/app.py` 的 `__main__` 会启动界面 ⇒ 未实跑，与 170 已加固的 `main.py`/`cli.py` 同列用户可直跑入口。
- **判据换血**：删 331 的粗判据（其代价不是误报，而是**逼人为过守卫去改文案**），换成
  `test_every_entrypoint_with_unencodable_string_is_hardened`（全仓一层目录；只看「是不是字符串常量」
  不看所在函数 ⇒ 仍是 331 那个盲区的解）。写清仍看不见的两种形态：运行期 `chr()` 拼装（实测本仓不存在）、
  两层深目录（由 `test_legacy_selfcheck` 的子进程实跑兜）。
- **守卫自己太贵也是缺陷**：331 的读侧判据用 `ROOT.rglob("*.py")` 深走全仓 ⇒ **62.66s**（本仓有上万文件的
  资源目录）。抽成有界遍历 `_iter_py()`（根 `*.py` + 每个顶层目录下一层）⇒ 判据一字未动，**→ 1.04s**。
  一条要一分钟的守卫，下一个赶时间的人一定把它 skip 掉。
- **反例闭合**：把修复前的 `video_service.py` 喂给同一套判据函数 ⇒ 抓到 `['⚠','✅']` 且未加固；
  修复后 7 个可安全实跑的文件在 `gbk` 下全部 **rc=0、零编码崩溃**。

### 新增（阶段 332 · CI 自己说出「哪一步红了」）
- **动机**：328/329/330/331 连修四层「说不清的绿」后，第一次真红（CI #17，45 秒）本机读不到现场
  （无 `act`、pip 连不上 PyPI 复刻不了 min 环境、会话内查 GitHub API 被安全策略拦）⇒ 只能等人截图。
  **「只有人能看懂的诊断等于没有诊断」。**
- **旧诊断的覆盖面**：`ci.yml`/`publish.yml` 的 `if: failure()` 步骤只读 `gate_output.txt`，
  而那文件**只有 `entry` 步真跑过才存在** ⇒ 失败发生在 `checkout/python/system/deps` 时
  只会写一句没用的 `(no gate_output.txt)`。（#17 只用 45 秒，走不到 `entry` 之后 ⇒ 假设红在 `entry` 之前，
  是推断不是结论，等下一次红自己说。）
- **改法**：诊断输出**以「哪一步红了」状态表开头**（`${{ toJSON(steps) }}` ⇒ 逐行 `id: outcome`），
  FAILED 清单跟在后面；两文件同形改，不留第二套口径。配套给**每个 step 补 `id`**——
  `steps` 上下文只包含有 id 的步骤，漏一个就在那张表里**静默消失**，比没有表更糟（表会骗人）。
- **守卫**：`test_every_workflow_step_has_an_id`（参数化＝**机械枚举** `workflows/*.yml`；断言每步有 id、
  文件仍含 `toJSON(steps)` 与「哪一步红了」、切出步骤数 `>=5` 才非空转；**刻意不用 PyYAML**——
  CI 只装 `requirements-min.txt`，守卫不能建在可选依赖上）、`test_workflow_step_ids_are_unique_per_file`。
  反例可构造：内存里删 `id: checkout` ⇒ 抓到 `- name: 拉取代码`（守卫有牙齿）。
- **本机真验**：两文件 `yaml.safe_load` 通过（`ci.yml` 6 步、`publish.yml` 10 步全带 id）；
  把诊断 JS 抽出来用桩替 `core/github` 交 `node` 跑三种 payload ⇒
  `checkout: success / entry: failure`、`deps: failure`（pip 失败那一类也能指认）、
  空表时老实写「(步骤状态不可用：请确认每个 step 都有 id)」。

### 修复（阶段 331 · 中文 Windows 上「一条用例没跑」与「19 条假红」同时存在）
- **现场**：真跑统一入口（不设任何环境变量＝用户双击 `test.bat` 的那个环境）⇒
  `pytest rc=1`（19 failed / 3339 passed）＋ `docker_static rc=1`（**0.1 秒**就红，什么都没检查完）。
  19 条红的签名高度一致：`TypeError: unsupported operand type(s) for +: 'NoneType' and 'NoneType'`
  ⇒ 测试拿到的是 `CompletedProcess.stdout is None`，**不是产品回归**。
- **根因**：跨进程编码没有唯一固定点，靠「每个脚本记得加固 + 每个调用点记得声明编码」两套分布式约定。
  两个方向本机都在真的发生：子进程按 GBK 写（GBK 编不出 `⇒ ✅ ❌ ² −` ⇒ `UnicodeEncodeError` ⇒ rc=1），
  父进程 `text=True` 按 locale(GBK) 读已加固子进程的 UTF-8（⇒ 读线程崩 ⇒ `stdout=None`）。
  机制一行证实：`PYTHONIOENCODING=gbk python -c "print('✅ x')"` ⇒ rc=1。
- **为什么阶段 170 的守卫没拦住**：它只扫 `print(...)` **字面量**里的非 GBK 字符，而
  `docker_consistency_check.py` 的 `⇒` 由 `audit()` **构造**后打印 ⇒ 正撞该守卫文档里自陈的盲区，
  且撞在统一入口的**默认第 3 步**。静态枚举欠账：`scripts/` 里 13 个未加固入口、
  `tests/`+`scripts/` 里 14 处文本模式调用未声明编码；`download_model.py`（README:2170 让用户直跑）
  打印「✅ 模型已就绪」未加固 ⇒ **下载成功也会 rc=1**（已修，hostile env 实测 rc=0 且 ✅ 正常打印）。
- **⚠ 那份枚举自己也是分布式的**：第一版没扫仓库根的 `conftest.py`，漏掉 `cli_run` fixture
  （`conftest.py:139`）——它正是 7 条 `test_cli.py` 用例的公共出口。守卫已改成**全仓枚举**
  （排除 `htmlcov/temp/.workbuddy` 等），实测全仓 23 处文本模式调用、0 处未声明编码。
- **修法（在边界钉死一次）**：① `conftest.py` 一处 `os.environ.setdefault("PYTHONIOENCODING","utf-8")`
  覆盖所有被测试 spawn 的子进程；② `run_all_checks.run_steps()` 对**每一步**同时传
  `env=…PYTHONIOENCODING=utf-8`（写侧）与 `encoding="utf-8", errors="replace"`（读侧）——产品路径；
  ③ 15 处调用点显式声明编码；④ 15 个文件补就地加固（人直跑路径）。
  `errors="replace"` 刻意保留：**判定层不该被显示层绑架**（阶段 146 的「输出内容决定门禁退出码」是缺陷）。
  `setdefault` 不削弱对抗守卫：`test_cli_survives_gbk_redirect` 用**显式赋值** `= "gbk"`，hostile 环境照旧真测。
- **同一条入口跑 5 次的实测序列**：A 修复前 `19 failed / 3339 passed` + `docker_static rc=1`；
  B 边界修完（`cli_run` 还没修）`8 failed / 3353 passed`，`undef_scan/docker_static/corner_signal_gate` 三步全 ✅；
  C 与 B 同一份代码但整个入口在 `PYTHONIOENCODING=gbk` 下启动 ⇒ `7 failed / 3354 passed`（**hostile 环境不再改变判定**）；
  D 补上 `cli_run` 后单跑 `test_cli + 三条新守卫 + test_run_all_checks + test_api_security299` ⇒ **rc=0**；
  E 最终完整入口（默认环境）⇒ **4/4 步 ✅、总退出码 0**（pytest 步 661.8s）⇒ 本地双击 `test.bat` 不再假红。
  B 里那条 `test_api_security299::test_post_without_token_401`（`ConnectionError`）**与编码无关**：
  C 未复现、D 单跑全绿 ⇒ 判为并发抢占抖动（跑 B 时同机并发了 13 个脚本的 `--help` 实跑）；
  该 fixture 已有 `_wait_ready(port)`，不是「未就绪就发请求」那类缺陷 ⇒ **不动它，只登记**。
- **顺带第二条（同一族：说不清的绿）**：E 想留「多少条用例通过」的证据时才发现 `run_steps()` **只在失败时**
  附打末尾输出 ⇒ CI 日志里成功那行是孤零零的 `✅ pytest rc=0`，**没人能看出它跑了多少用例**。
  现在 `rc == 0` 也附打子进程最后一行（截断 160 字符，**判定完全不变**），守卫
  `test_success_still_prints_a_line_of_evidence`；实跑两静态步骤确认 `└─ 全部通过…` 证据行真的出现。
- **守卫**：`test_every_script_entrypoint_with_non_ascii_is_hardened`（判据不看字符出现在哪里，
  误报方向安全，带 `checked≥20` 反空转）、`test_text_mode_subprocess_calls_declare_encoding`
  （bytes 模式正确排除）、`test_run_steps_pins_the_cross_process_encoding`（spy runner 逐步断言三个参数）。
- **诚实边界**：`core/services/utils/ui` 仍有约 50 个模块自检未就地加固（打印中文，GBK 编得下 ⇒ 不崩）；
  经入口/测试 spawn 的已被固定点覆盖，剩下只有「人手工直跑库模块」这条 dev 路径。
  守卫范围与加固范围**一起扩**，否则守卫一上线就红 50 条（那是为了守卫改产品，方向反了）。

### 修复（阶段 330 · 「2 = 跳过」被当成全局约定，pytest 的收集期报错能洗成绿）
- 统一入口只有一张全局码表（`0/1/2/3`），各步骤原始码直接进 `aggregate()`。门禁家族的约定是我们自己定的、
  没错；但第 1 步是 pytest，它的 **2 = Interrupted（收集期报错、`-k` 选空）**、3 = Internal error，
  入口却按「⏭ 跳过」解释 ⇒ `[2,0,0,0]` 既无 1 也无 3、也不是全 2 ⇒ **返回 0 并印「✅ 全部通过」**。
  **一条用例都没跑，CI 也可以是绿的**（CI 装 `requirements-min.txt`，缺包最常见的形态正是导入期炸 ⇒ 2）。
- 本机实测码表（第一次取 `$?` 取到的是 `tail` 的 0——329 同一个坑，改用文件重定向）：
  收集期 ImportError ⇒ **2**；`-k zzz_nothing` 选空 ⇒ **2**；路径不存在 ⇒ **4**；未知参数 ⇒ **4**。
- 修法：约定挂到步骤上。`Step` 新增 `skip_codes=(2,)` / `env_codes=(3,)`（默认值即门禁家族约定 ⇒
  **七道门禁一行没改**），pytest 步骤显式声明两档都没有；新增纯函数 `normalize_rc()`，
  `run_steps()` 逐步归一后才喂 `aggregate()`。原始码仍印在日志（`rc=2→判1`）并留在报告 `rc` 字段，
  另加 `verdict` —— **判定改了口径要让读日志的人看得出是谁改的**。
- 守卫 3 条（`tests/test_run_all_checks.py`，先红：`pytest 返回 2 时入口应判失败，实得 2`）：
  pytest 2/3/4/5 ⇒ 总码 1 且那行含 ❌ 与原始码；**反向**钉 `corner_signal_gate` 的 2/3 仍是跳过/环境错误
  （防修复过头把「缺资产」误判成产品退化）；`test_run_steps_all_skipped_returns_two` 排除 pytest 并写明理由。
- CI #17 的红**仍未诊断**：本机 pip 连不上 PyPI 无法复刻 min 环境，缺的是 Actions 日志里「哪一步带 ❌」。

### 修复（阶段 329 · CI/发布链的门禁退出码被管道吞掉）
- `ci.yml` 与 `publish.yml` 的门禁步骤都写成 `python scripts/run_all_checks.py 2>&1 | tee gate_output.txt`，
  而两个文件**都没声明 `shell:`** ⇒ GitHub 默认 `sh -e`（**不带 pipefail**）⇒ 管道退出码取 `tee` 的 0。
  本机用真实命令复现：`sh -e` 下本该返回 2 的 `--list` 被读成 **0**；`bash -eo pipefail` 下整步以 2 失败。
- 后果：`publish.yml` 里那句「**不过门禁不发布**」在此配置下**不成立**；CI 步骤永远绿；
  两步的失败诊断（`if: failure()` 写 Summary + 建 issue）**永远不会触发**。
  上游佐证：actions/runner#1212、github/docs#18933、codeinthehole TIL。
- 修法（两处同形，双保险）：`shell: bash` + 脚本体内 `set -eo pipefail`。
- 守卫 `test_piped_gate_step_cannot_swallow_the_exit_code`（参数化两个文件）：含 `| tee` 的 `run:` 行
  必须在 ±6 行内同时出现 `shell: bash` 与 `pipefail`。**先红**（精确指到 `ci.yml:65`/`publish.yml:60`）**后绿**；
  `yaml.safe_load` 两文件解析通过。未验证：本机无 `act`，GH runner 上的实际行为要 push 后看一次 Actions。

### 修复（阶段 328 · 统一入口把「环境错误」聚合成通过）
- `real_gate` 约定退出码 **3 = 环境错误**（区别于 1 质量不合格 / 2 跳过），而 `aggregate()` 只把
  `rc==1` 当失败 ⇒ 3 落到 `return 0`，出现「那一步印 🚧/❌、总结句印 ✅ 全部通过」的自相矛盾输出。
- 语义补成四档并**默认拒绝未知码**：优先级 `1 > 3 > 约定外(算1) > 2 > 0`；
  127/崩溃一类现在按失败处理（说不清的结果不是通过）。措辞抽成 `VERDICTS`/`STEP_MARKS` 唯一真值来源——
  顺手修掉一个潜伏崩溃：总结句原先用 `{0,1,2}[total]` 取值，任何一步返回 3 都会让入口 **KeyError**。
- TDD：先加 6 条聚合断言 ⇒ **5 条红**（旧实现分别返回 0）＝缺陷活证据；再加端到端反例
  （假 runner 驱动真实 `run_steps`：`real_gate→3` ⇒ 总码 3 且输出不得含「全部通过」；`undef_scan→127` ⇒ 1）
  与结构守卫（两张措辞表的键必须恰为 `{0,1,2,3}`）。`tests/test_run_all_checks.py` 37 → **41 例**。
- 消费者全核过：两个 workflow、`test.sh`（`if …; then`）、`test.bat` 都按「非零即失败」解释 ⇒ 语义变化正是想要的；没有任何代码解析那句总结文本。


### 变更（阶段 327 · 文档卫生：已决事项不再读起来像未决，零代码改动）
- `docs/P0-真实水印图采集清单与提示词.md` 的**目的句**还写着「解除 `real_gate` 的 `ai_logo` 红灯」，
  而那件事**阶段 172 就完成了**（现 57 条 GT 全是 `corner_logo`）⇒ 读者会以为还在等第一批角标图。
  头部补状态块（原使命已完成，§一~§五 降级为「当时怎么采的口径记录」），并新增 **§六**：
  当前真正需要采的是 **`grid` 的真实成对样本**，依据是 324 的 oracle 数（`grid` 0.749 / `faint_tiled` 0.026）；
  硬性条件写死为「**同一文件的带水印导出 + 无水印导出**（像素对齐）」——重新生成的相似图不算，
  317 两次接入都因此判不可用；`faint_tiled` 明确「不必为它采」。
- `docs/INDEX.md` 上编状态表：**A1 / B1 / B3 由 ⛔ 待你出图 改为 ✅**（172 已闭合），
  并新增一行 **A1b ⛔ 阻塞：平铺/点阵真实成对样本**指向 P0 §六 —— 那才是现在真正的阻塞项。
- 可复用判据：凡「状态 / 目的 / 优先级」句式的文档，都要问「它描述的还是现在吗」；
  这类句子不会自己变红，只会让下一个人重复劳动（与 326 的记忆修复同类）。


### 新增（阶段 326 · 对外数字的防腐守卫 + 据实刷新）
- 一查就是三处脱节且无人管：`CHANGELOG` 头部「共 118 份」实测 **284**；记分卡
  `test_cases` **2504** vs 真实收集 **3359**；`test_files` 219→**242**、`docs` 246→**284**、
  `src_loc` 33200→**36382**、`test_loc_approx` 26000→**48257**、`scripts` 32→**49**。
  同一个事实还存了两份副本并已互相打脸（`scale.docs=246` vs `engineering.docs=280`）。
- 全部据实刷新，并把**复现口径**写进 `scale.scale_note`（哪些文件算进 LOC、用什么命令数）。
  `audited_modules=45` 有意不动——没有可机械复测的定义，编一个数比留着更糟（docs/326 §四）。
- 新守卫 `tests/test_scorecard_fresh326.py`（6 例）：两处副本必须一致；声明值不得高于实测、
  落后不得超 25%；`test_cases` 夹在 `def test_` 条数（下界）与其 ×2（上界）之间——
  真实收集要 1~2 分钟，不塞进单测。**刻意不写死等号**：等号会让每个阶段无谓变红，
  最后守卫被人删掉。


### 证据（阶段 325 · 门禁在 CI/发布链的覆盖缺口，核实 + 待授权建议）
- 核实：`ci.yml` 与 `publish.yml` 都只跑 `run_all_checks.py`（**无 `--gates`**）⇒ 七道效果/真值门禁
  在任何自动链路上都不跑，`publish.yml` 注释里的「tag → 门禁」实际等于 CI 等价子集。不是新缺陷，
  但 322a/322b 新加两道后「门禁」一词与自动链路的差距变大，显式记一笔防止误读。
- 实测哪道能进 CI（用 `find_spec` 打桩伪装 min 依赖、无 torch/easyocr 的环境）：新增的 42 例守卫全绿；
  **`corner_signal_gate` rc=0（≈10s，38/49、误命中 0）** ⇒ 今天就能加；
  `removal_truth_probe --gate` **rc=2** + 人话理由 ⇒ 正确跳过、不会假绿（322a 守卫的实战验证）。
  数据集确认随仓库分发（`git ls-files benchmark_real` = 321 文件，含 images/ 与 clean/）。
- 其余六道**不该**顺手加：红线基线是在「OCR 在位」口径下标定的，无 OCR 的 runner 上数字会变但那不是
  产品回归 ⇒ 只会造成 CI 常红或逼人重锚。**已实施**：`corner_signal_gate` 提进 `run_all_checks` **默认步骤**（第 4 步，+≈10 秒），`ci.yml`/`publish.yml`/`test.bat`/`test.sh` 一行都不用改即自动获得这道守卫。
  原建议「往 ci.yml 另加一条命令」被自己否掉——那正是阶段 192 打掉的**入口副本**（`test_entry_surfaces_call_the_same_unified_entry` 防它）。风险面：`aggregate()` 只把 rc=1 判失败 ⇒ 缺数据集的机器是 ⏭ 跳过、不是假绿；`benchmark_real/images` 109 个受控文件（无 LFS） ⇒ push 后 CI 就有图。另实测把 torch/torchvision/easyocr **全部屏蔽**后仍 rc=0、10.2s。
- 自我更正：本轮曾把 320f 已按「不动阈值 + 诚实文案」处置完的「掩码编辑器往返 +33%」当成未决缺陷上报；
  先 grep 项目记忆清单才发现（LESSONS #307 那条纪律的正向用例），并已修掉记忆里那条过期措辞。

### 证据（阶段 324 · 平铺类修复上界 spike，取证不改代码）
- 用 oracle 实验量「检测之外的天花板」：把构造真值掩码喂给同一条管线（`mode="manual"`），
  与自动路径同轮对照，15 真照片 × 2 类 = 30 用例，判读规则**开跑前写死**。
- **`grid` = 检测瓶颈**：oracle 中位 gain **0.749**（15/15 ≥0.571）vs 自动 0.002
  ⇒ 掩码一给对，同一套修复就能擦干净。这条线值得做，但**阻塞在数据**：
  真实图集 57 条 GT **100% 是角标**，平铺无真实真值，现在开工只能拿合成点自证（322b 的教训）。
- **`faint_tiled` = 修复瓶颈且结构性**：oracle 中位仅 **0.026**、6/15 张被擦得**更差**（最差 −0.691）。
  α≈8.6% 的平铺文字自身误差 10~14，而修复要换掉 6% 画面，重绘差异与之同量级 ⇒
  「擦掉它」在误差上不可能净赚。**划进「别再试」清单**（不要用检测工程去救它）。
- 诚实边界：oracle 走 manual 交付默认参数、与自动预设不同，故 0.749 读作「本管线内上界」而非理论上限；
  但 324 §3.2 的**符号**（残留 > 强度）与预设无关，是替换面积与误差量级的关系。
  探针脚本一次性、**未入库**（它依赖「把构造真值当掩码」这个只在合成水印上成立的作弊条件）。
- **顺带落地一句界面文案**（本阶段唯一的代码改动）：`scope.note` 从只说「效果不保证」升级为
  说清**为什么不承诺**——「不承诺有两种：一种我们实测过上限（定位全对也几乎没有净收益）；
  一种只是缺真实标注样本，尚未定论」。三个约束写进 docs/324 §七：文案必须留在 `scope.note`
  **同一个键**里（新开键等于绕过「模板不得手抄类型名」守卫）、类型名仍由 `config` 注入、
  理由句里**不写数量**（会过期的事实不写死）。新守卫 `test_scope_note_explains_why_not_committed`
  双语各钉两个理由；中文横幅实测 150 → 226 字（读回 `render_scope_note()`）。

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
- **连带损伤与「不许静默降级」**（同属 337）：完整入口抓到 4 条红，其中三种消费者需要处理——
  `test_ui_tab_seams` 替身缺 `summary()`（336 新依赖，同步夹具）、`test_services_audit` 两条写死已删除的
  `rep.detail`（改为断言「键 + 渲染句子」，**87 那条语义一个字没放宽**）、
  以及最坏的一种：`test_tab_batch_branches_audit` 的替身仍带旧字段 ⇒ `detail_text()` 抛 AttributeError，
  却被调用点外的 `except Exception: log.warning("质量评估失败（不影响结果）")` **吃掉**
  ⇒ 用例全绿、批次照跑，只有界面悄悄少一行诊断（HEAD 上取到该 warning 原文）。
  修法不是取消降级，而是**把降级范围收窄到数据/环境类**：`except (AttributeError, NameError, TypeError): raise`
  置于宽口子之前，并留 `test_coding_error_in_quality_render_is_not_swallowed`（旧形状替身必须炸；
  HEAD 必红、修复后绿）。判据落 LESSONS #319。
