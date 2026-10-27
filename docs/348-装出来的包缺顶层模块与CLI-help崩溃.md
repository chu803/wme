# 阶段 348 · 装出来的包缺 `job_manager`（三个命令一启动就崩）+ `wme-cli --help` 直接崩

> 日期 2026-10-26 · 性质：**分发缺陷修复**（在「把可离线副本装到 D 盘」这件事上暴露出来的）

## 一、怎么发现的：把包装进一个**全新 venv**

这是今天做「离线可用副本」时的副产品——**在一个干净环境里装自己的 wheel**，而不是在源码目录里跑：

```
D:\wme-1.0.3\venv\Scripts\wme-cli.exe --help
  ModuleNotFoundError: No module named 'job_manager'
```

## 二、缺陷一：wheel 里没有 `job_manager`

`pyproject.toml` 的 `py-modules` 声明了 6 个顶层模块：

```toml
py-modules = ["api", "cli", "config", "download_model", "main", "server"]
```

**漏了 `job_manager`** —— 而 `server.py:75` 有 `import job_manager as JM`。
⇒ wheel 里没有它 ⇒ **安装态**（任何不在源码目录里跑的场景）一 import 就炸：

* `wme` / `wme-cli` / `wme-api` **三个控制台命令全部启动即崩**（README 3.3 节推荐的就是这三条）；
* `import server` / `import api` 同样失败。

**为什么本地全绿**：在源码目录里跑时，`sys.path` 含仓库根 ⇒ `import job_manager` 找得到 ⇒ 所有自测、门禁、
本地 `python main.py` 都正常。**这个缺陷只在「装出来的包」里存在**，
而既有守卫只查了一个方向：

* `test_declared_py_modules_exist_on_disk`：**声明的**都在磁盘上 ✓（job_manager 没声明 ⇒ 不触发）
* `test_all_shipped_top_level_packages_are_declared`：顶层**包**（目录）都声明了 ✓（job_manager 是模块不是包）

⇒ **缺口正是「磁盘上的都在声明里」这个镜像方向**，且它只覆盖包、没覆盖模块。
⇒ 新增 `test_every_root_module_is_declared_or_exempted`（`tests/test_packaging_meta.py`），
与既有那条**配成一对**（`conftest.py` 显式豁免并写明理由）。

**影响面**：**已发布的 1.0.1 / 1.0.2 也有这个缺陷** ⇒ 从 PyPI 装包的用户，
`wme` 命令是起不来的（只有 `pip install .` 后在源码目录里跑、或用 Docker 才不受影响）。

## 三、缺陷二：`wme-cli --help` 直接崩（argparse 的 `%` 展开）

```
ValueError: unsupported format character '?' (0x2192) at index 44
```

`cli.py` 里 `--preserve-untouched` 的 help 文本含「实测误伤 50%→约 1%」，
而 **argparse 会对 help 文本做 `%` 风格展开** ⇒ 裸 `%` 后面跟 `→` 就炸。
⇒ **用户第一次试命令就撞上崩溃**（不是用起来才发现）。

修法：`50%%→约 1%%`（argparse 渲染回 `%`）。
守卫 `tests/test_cli_help348.py`：**真跑一次 `--help`**并要求 rc=0、输出含 usage，
且「50%」与「→」都还在（别为了不崩把信息删了）。

## 四、附带再确认一次阶段 331：父进程解码必须显式 UTF-8

写上面那条新守卫时，**我的测试自己又踩了 331 那个坑**：

```
UnicodeDecodeError: 'gbk' codec can't decode byte 0xae in position 72
⇒ proc.stdout 变成 None
```

子进程（`cli.py` 自带 `force_utf8_stdio`）输出 UTF-8，而父进程默认按 **locale（中文 Windows = GBK）** 解码
⇒ 读线程崩 ⇒ `stdout` 是 None。⇒ 新守卫显式 `encoding="utf-8", errors="replace"`。
**331 那条「跨进程编码要成对钉」在今天第三次被实证**（第一次是入口本身，第二次是媒体链路的 BGR，这次是测试探针）。

## 五、证据（安装态实测）

修复前：
```
D:\wme-1.0.3\venv\Scripts\wme-cli.exe --help
  → ModuleNotFoundError: No module named 'job_manager'
wheel 顶层条目: api.py cli.py config.py core download_model.py main.py server.py services ui utils   ← 无 job_manager
```

修复后：
```
wheel 顶层条目: api.py cli.py config.py core download_model.py job_manager.py main.py server.py services ui utils
$ wme-cli --help
usage: cli.py [-h] [-i INPUT] [-o OUTPUT] [--serve] ...
（安装态 import 全部模块：config/job_manager/server/cli/api/core.detector/ui.app/services.cache_service 均 ok）
```

## 六、连带：1.0.3 的 tag 需要再指一次

`v1.0.3` 的 tag 是在**本阶段修复之前**推的 ⇒ 若它这次发布成功，
PyPI 上的 1.0.3 仍然带着「命令启动即崩」与「--help 崩溃」两个缺陷 ⇒
必须把 tag 再指到含本修复的提交上重推（PyPI 上目前只有 ≤1.0.2，重推不会覆盖任何已上架版本）。
