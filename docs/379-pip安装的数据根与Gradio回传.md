# 379 · pip 安装的数据根与 Gradio 回传（2026-11-05）

## 怎么发现的

1.0.4 发布物安装冒烟时，静态页面检查全绿：`v1.0.4`、隐私文案、对比色规则都在。
接着在真浏览器里跑黄金路径（试示例 → 自动检测 → 一键修复），**修复算完了、结果存了，
网页却报错**：

```
gradio.exceptions.InvalidPathError: Cannot move
D:\qoder\ci311venv\Lib\site-packages\outputs\auto_150447_cleaned_150447.png
to the gradio cache dir because it was not created by the application or it is not
located in either the current working directory or your system's temp directory.
```

排查安装目录，发现不止输出：`site-packages/outputs`、`cache`、`logs`、`models`
都被创建，`models` 里还有一次未完成的 `big-lama.pt`（4.4 MB）。

## 根因

`config.PROJECT_ROOT = config.py 所在目录`，而它被当成**可写数据根**：

- 源码运行：根目录是仓库，产物落在 `./outputs`，一切正常；
- PyInstaller：根目录是 EXE 旁边，符合预期；
- pip 安装：根目录变成 `site-packages`（只读、随升级被替换），
  输出 / 模型 / 缓存 / 日志全写进去；同时这些目录在 cwd 之外，
  Gradio 默认只服务 cwd 与系统临时目录，回传结果时拒绝搬运。

也就是说，这个缺陷只在「安装包 + 从仓库外启动 + 真有修复结果」三者同时成立时暴露，
静态页面检查与源码态测试都碰不到它。

## 修法

1. 拆出**运行时可写数据根** `DATA_ROOT`：
   - PyInstaller → EXE 所在目录（不变）
   - 源码树（以 `pyproject.toml` 判定）→ 仓库根（不变，开发时产物位置照旧）
   - pip 安装 → 平台用户数据目录：
     Windows `%LOCALAPPDATA%\wme-watermark-eraser`、
     macOS `~/Library/Application Support/wme-watermark-eraser`、
     Linux `${XDG_DATA_HOME:-~/.local/share}/wme-watermark-eraser`
   - 可用 `WME_DATA_DIR` 覆盖（推荐绝对路径）
2. `MODEL_DIR / OUTPUT_DIR / EXPORT_DIR / CACHE_DIR / LOG_DIR / TEMP_DIR` 默认全部改为
   落在 `DATA_ROOT` 下；既有的 `WME_*_DIR` 覆盖规则不变。
3. `ASSETS_DIR` 明确为**随包只读资源**，仍跟随 `PROJECT_ROOT`；不再进 `ensure_directories`
   （否则 pip 安装时会在 site-packages 里建 assets 目录）。
4. 插件相对目录改为以 `DATA_ROOT` 为基准，默认 `<数据目录>/plugins`。
5. `launch_app` 把 `OUTPUT_DIR / EXPORT_DIR / CACHE_DIR / TEMP_DIR` 传给
   Gradio 的 `allowed_paths`，结果文件才能被搬进缓存并回传。
6. 启动自检的非 ASCII 路径告警改查 `DATA_ROOT`：pip 安装时权重在用户目录，
   只查 site-packages 会漏报。

## 验证

新增守卫 `tests/test_data_root379.py`（6 条）：复制 `config.py` 到临时目录模拟
安装布局，分别断言「显式数据目录 / 平台默认目录 / 源码树 / frozen / ensure_directories
不碰安装目录 / 插件目录基准」；反例是同一个测试在修前全红（`DATA_ROOT` 不存在）。

`test_app_branches_audit.py` 新增 1 条：`launch_app` 必须把四个运行时目录放进
`allowed_paths`。`test_entry_encoding_and_path_guard.py` 的断言同步改为 `DATA_ROOT`。

真机复验（重新构建并安装到干净的 3.11 环境，从仓库外目录启动，
`WME_DATA_DIR=D:\qoder\watermark-workbench\.scratch\data379`）：

- 启动后 6 个运行时目录全部建在 `data379` 下；
- 试示例 → 自动检测（7 个疑似区域）→ 一键修复：状态栏显示
  `✅ 处理完成（引擎：AUTO）`，结果写入 `data379\outputs\auto_151510_cleaned_151510.png`；
- 网页成功拿到结果图（Gradio 缓存 URL），不再有 `InvalidPathError`；
- `site-packages\outputs` 里旧文件时间戳保持 15:04 未变，证明新进程没有再写安装目录；
- 全量入口 `rc=0`（3444 passed / 37 skipped / 6 deselected，458.83s）。

## 发布前闸门

只修一次不够：这个缺陷能上线，是因为发布链只核对「产物条目」、没跑「装出来的包」。
新增 `scripts/verify_installed_layout.py`：把待发布的 wheel 装进干净 venv，
从仓库外 cwd 导入 config，断言版本一致、`DATA_ROOT` 生效且不在包目录内、
运行时目录都落在数据根、`ensure_directories` 不在安装目录建目录。

- 对修复后的 wheel：`rc=0`，数据根为临时目录，包目录为 venv 的 site-packages；
- 对 PyPI 上已发布的 1.0.4 wheel：`rc=1`，明确报出「没有 DATA_ROOT」以及
  「安装目录被写入 logs/models」。
- `publish.yml` 在 `verify_dist` 之后、上传产物之前调用它（步骤 id `installed_layout`），
  由 `tests/test_verify_installed_layout379.py` 钉住判定反例与接线。

## 边界与后续

- 平台默认路径用子进程 + 伪造 `HOME/LOCALAPPDATA/XDG_DATA_HOME` 验证，
  未在真实 macOS / Linux 上跑安装包。
- 1.0.4 已写进 site-packages 的历史数据不做迁移；修复需要随下一版发布才到达用户。
- 临时安装的 `site-packages\outputs/cache/logs/models` 是本次测试自己产生的残留，
  清理动作未获授权，保留不动。
