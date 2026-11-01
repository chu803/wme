# 阶段 364 · 本机第一次真跑通 `python -m build`（含构建隔离），并把「查产物」钉进发布链

> 日期 2026-11-01 · 性质：**发布基建收口**（docs/297 那条"本机无法复现"的诚实边界可以撤销了）

## 一、为什么这件事值得单开一阶段

docs/297 的「诚实边界」里挂着两条一直没做到的事：
① 没在本机真跑 `pip install .` / `python -m build`（当时归因于清华镜像取不到 `build` 与 `setuptools>=77`）；
② 冒烟（发布后从 PyPI 装、在全新 venv 里跑三个命令）只能靠人手工做，本机没法复现。

阶段 348 的另一条教训也一直没工具化：**声明对了不等于进包对了**——
`py-modules` 漏 `job_manager` 时源码目录一切正常、装出来的包启动即崩，本地全量测试一路绿。
LESSONS 里写的是"改了打包配置要真打一次并查 zip 条目"，但每次都得手敲、没人查。

换镜像之后（阿里云可用，见 347/360 的记录）这两条一起做掉了。

## 二、实测结果

### 构建（含隔离）在本机通了

```
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ PIP_TRUSTED_HOST=mirrors.aliyun.com \
  python -m build
→ Successfully built wme_watermark_eraser-1.0.3.tar.gz and wme_watermark_eraser-1.0.3-py3-none-any.whl   BUILD_RC=0
```
⇒ **docs/297 里"本机无法复现 `python -m build`"那句作废**，改成"换镜像后可复现，需带 `PIP_INDEX_URL`"。

### 但产物核对抓到两个缺口

1. **sdist 里没有 `requirements-*.txt`**。这不是洁癖：`pyproject` 刻意**不声明** `dependencies`
   （真值来源是 requirements，避免两处漂移），于是 README §3.3 与 runbook 第 5 步都写着
   「装完包还要照 `requirements-min.txt` 自己装依赖」——
   而**从 sdist 安装的人（`--no-binary :all:`）拿不到那份被文档指着的文件**，文档路径对他是空的。
   ⇒ `MANIFEST.in` 补 `include requirements*.txt`（不是把清单复制进 pyproject，那正是当初不声明的理由）。
2. wheel 侧核对全部合格：顶层 6 个模块（含 `job_manager`）、三个 `console_scripts` 都指向包内真实模块、
   `ui/assets/sample_watermarked.png` 在包内（320c）、**不含权重**、**不含 tests/scripts/docs**。

### 安装态冒烟（全新 venv，3.11 + 只装 wheel 与 min 清单）

```
UP_RC=0  WHEEL_RC=0  DEPS_RC=0
wme --help     rc=0  42 行（argparse 用法）
wme-cli --help rc=0  113 行
安装态 import 失败: 无      # config/main/cli/server/api/job_manager/core…/services…/ui.tab_batch
```
⚠ 第一次冒烟跑出 `DEPS_RC=1` + `WinError 32`（文件被占用）：原因是**我自己同时开了两个后台任务
往同一个 venv 装**（不是产品问题）。重开一个干净 venv 复测即全 0。

## 三、`wme-api --help` 的真相（顺带发现，留下一阶段）

冒烟里 `wme-api --help` 打的**不是用法**，而是服务启动横幅（`按 Ctrl+C 停止服务`）并阻塞——
我的两轮循环就是被这个"阻塞"卡住的。查源码确认：`server.py` 里**没有 argparse、也不读 `sys.argv`**，
`start_server()` 只吃关键字参数、其余取 `config`（可用环境变量覆盖）。

⇒ 严格说不算 bug（文档写的是 `wme-api  # REST 服务`），但对用户有三个实际损失：
`--help` 无法自描述、写错的参数被**静默忽略**、而忽略的代价是直接占用一个端口。
README §3.3 那句「参数有默认值」也容易读成"有命令行参数"。
⇒ 记为任务 #37：给 `wme-api` 补一层薄 CLI（`--host/--port/--max-concurrency/--help`）并加守卫
（"每个 console_scripts 入口的 `--help` 都必须打用法、必须能退出"）。**本阶段不动入口语义。**

## 四、工具与守卫

- `scripts/verify_dist_artifacts.py`（新）：核对 wheel + sdist 的条目，退出码沿用家族口径
  （`0` 合格 / `1` 有条目不合格 / `2` 没有产物 ⇒ 提示先 `python -m build`）。
  ⚠ 它第一次跑就把**我自己**的 bug 报了出来：找 wheel 时按 `_{版本带下划线}-` 匹配
  （wheel 的真名是 `wme_watermark_eraser-1.0.3-py3-none-any.whl`，版本是**点分**）
  ⇒ 把合格产物报成"没找到 wheel"。已把匹配逻辑抽成纯函数 `match_artifacts()` 并单独钉住。
- `.github/workflows/publish.yml`：在 `build` 与 `publish` 之间新增步骤 `id: verify_dist`
  （`shell: bash` + `set -eo pipefail` + `tee logs/verify_dist.log`，守 329/356 的既有纪律）
  ⇒ **上传前**用真产物核对，产物不合格就不进 PyPI。
- 守卫 `tests/test_dist_artifacts364.py`（7 例）：命名匹配、四类 wheel 缺陷各自点名、
  sdist 缺任一份 requirements 就点名、`MANIFEST.in` 必须显式 include、无产物⇒退出码 2、
  `publish.yml` 这一步存在且**夹在 build 与 publish 之间**、以及**真构建一次**再核对必须为 0。

## 五、诚实边界

- `--no-isolation` 用于测试内的真构建（隔离要去 PyPI 取 setuptools/wheel，
  本机默认镜像取不到 ⇒ 会让守卫变成"网络配置决定红绿"）；**带镜像的隔离构建**由 runbook 手工执行并留档。
- 本机没有 Linux/Docker ⇒ 安装态冒烟只在 Windows + Python 3.11 上验过。
- wheel 里仍然没有依赖清单（刻意不在包内塞；wheel 用户的清单入口是 PyPI 项目页 / 仓库 README）。
  如果希望 `pip install wme-watermark-eraser` **一步到位**，那等于要在 pyproject 声明 `dependencies`——
  属基线翻转（当初刻意不做的决定），需用户拍板（任务 #38）。
