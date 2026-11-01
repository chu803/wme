# -*- coding: utf-8 -*-
"""阶段 364 · 发布前用**真产物**核对分发口径（静态守卫不等于「真的进包」）。

## 为什么需要

仓库里已有 `tests/test_packaging_meta.py`，但它读的是 `pyproject.toml` 的**声明**。
阶段 348 的真实教训是：声明与产物可以各说各话——`py-modules` 漏了 `job_manager`，
wheel 里就没有它，而**源码目录里一切正常**（`sys.path` 含仓库根），
于是 `wme` / `wme-cli` / `wme-api` 三个命令在**装出来的包**里启动即崩，本地全绿一路放过。
LESSONS 里那条判据写得很清楚：**改了打包配置要真打一次并查条目**。
本脚本把"查条目"变成一条命令，供发布前（和 `publish.yml` 上传前）反复执行。

## 查什么

wheel（用户 `pip install` 真正拿到的东西）：
  · 顶层模块齐全（`config/main/cli/server/api/job_manager`）
  · `[console_scripts]` 三个入口都在，且指向真实存在的模块
  · 示例图 `ui/assets/*.png` 在包内（阶段 320c 的 package-data）
  · **不含**模型权重（`*.pt` 只该由 `download_model.py` 下载）
  · **不含** `tests/` `scripts/` `docs/`（与「测试套件不随分发发布」一致）

sdist（从源码安装 / `--no-binary` 的人拿到的东西）：
  · `pyproject.toml` + `README.md`
  · `requirements-*.txt`（阶段 364 补：清单是 pyproject **刻意不声明依赖**后唯一的依赖真值来源，
    被 README §3.3 与 docs/297 runbook 第 5 步指着用，却不在 sdist 里 ⇒ 那条文档路径对安装态用户是空的）

## 退出码（与门禁家族一致）

    0 = 全部符合
    1 = 有条目不符合（**发布前必须修**）
    2 = 没找到产物（先 `python -m build`）或指定版本没有对应文件

## 用法

    python -m build                       # 先出产物
    python scripts/verify_dist_artifacts.py
    python scripts/verify_dist_artifacts.py --version 1.0.3 --dist dist
"""
from __future__ import annotations

import argparse
import os
import sys
import tarfile
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: wheel 里必须存在的顶层模块（与 `pyproject.toml` 的 `py-modules` 同口径，但这里验**产物**）。
_REQUIRED_TOP_MODULES = ("config.py", "main.py", "cli.py", "server.py", "api.py", "job_manager.py")

#: console_scripts 的入口名（三件套，阶段 296 补齐、348 实测过安装态）。
_REQUIRED_CONSOLE_SCRIPTS = ("wme", "wme-cli", "wme-api")

#: sdist 里必须存在的依赖清单（README §3.3 / runbook 第 5 步指着它们）。
_REQUIRED_SDIST_REQUIREMENTS = ("requirements.txt", "requirements-min.txt", "requirements-dev.txt")

#: 任何产物都不该出现的路径前缀（体积与意图双重原因）。
_FORBIDDEN_PREFIXES = ("tests/", "scripts/", "docs/", ".github/")


def _wheel_names(path: str) -> list:
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def _sdist_names(path: str) -> list:
    out = []
    with tarfile.open(path, "r:gz") as t:
        for m in t.getnames():
            parts = m.split("/", 1)
            out.append(parts[1] if len(parts) > 1 else parts[0])
    return out


def _console_scripts(z, dist_info: str) -> dict:
    """读 wheel 里的 `entry_points.txt`，返回 {命令名: "模块:属性"}。"""
    name = f"{dist_info}entry_points.txt"
    if name not in z.namelist():
        return {}
    text = z.read(name).decode("utf-8")
    out, in_section = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_section = (s == "[console_scripts]")
            continue
        if in_section and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def check_wheel(path: str) -> list:
    """返回 wheel 的问题清单（空 = 合格）。"""
    problems = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        dist_info = next((n.split("/")[0] + "/" for n in names if n.endswith(".dist-info/METADATA")), "")
        for mod in _REQUIRED_TOP_MODULES:
            if mod not in names:
                problems.append(f"wheel 缺顶层模块 {mod}（装出来 import 就崩，阶段 348 的同款缺陷）")
        scripts = _console_scripts(z, dist_info)
        for cmd in _REQUIRED_CONSOLE_SCRIPTS:
            target = scripts.get(cmd)
            if not target:
                problems.append(f"wheel 的 console_scripts 缺 {cmd}（实际：{sorted(scripts)}）")
                continue
            module = target.split(":")[0].strip()
            if f"{module}.py" not in names:
                problems.append(f"{cmd} 指向 {target}，但 wheel 里没有 {module}.py ⇒ 命令启动即 ImportError")
        if not any(n.startswith("ui/assets/") and n.lower().endswith(".png") for n in names):
            problems.append("wheel 缺 ui/assets/*.png（示例图与「输出格式跟随输入」的资产，阶段 320c）")
        for n in names:
            if n.endswith(".pt"):
                problems.append(f"wheel 里出现了模型权重 {n}（权重只该由 download_model.py 下载）")
            if n.startswith(_FORBIDDEN_PREFIXES):
                problems.append(f"wheel 里出现了不该随分发发布的 {n}")
    return problems


def check_sdist(path: str) -> list:
    """返回 sdist 的问题清单（空 = 合格）。"""
    problems = []
    names = _sdist_names(path)
    for need in ("pyproject.toml", "README.md"):
        if need not in names:
            problems.append(f"sdist 缺 {need}")
    for req in _REQUIRED_SDIST_REQUIREMENTS:
        if req not in names:
            problems.append(
                f"sdist 缺 {req}（pyproject 刻意不声明 dependencies ⇒ 这份清单是唯一的依赖真值来源，"
                "README §3.3 与 docs/297 runbook 第 5 步都指着它）"
            )
    for n in names:
        if n.startswith(_FORBIDDEN_PREFIXES):
            problems.append(f"sdist 里出现了不该随分发发布的 {n}")
    return problems


def match_artifacts(files, version: str) -> tuple:
    """从一组文件名里挑出该版本的 (wheel, sdist)（纯函数，便于断言）。

    ⚠ 命名规则与直觉不同，这里就是第一版踩的坑：wheel 是
    `wme_watermark_eraser-1.0.3-py3-none-any.whl`（版本用**点**、前后都是 `-`），
    而包名里的 `-` 会被规范化成 `_`；sdist 是 `wme_watermark_eraser-1.0.3.tar.gz`。
    首版按 `_{version带下划线}-` 找 wheel ⇒ 永远找不到，把合格产物报成不合格。
    """
    wheel = sdist = None
    for f in files:
        if f.endswith(".whl") and f"-{version}-" in f:
            wheel = f
        elif f.endswith(f"-{version}.tar.gz"):
            sdist = f
    return wheel, sdist


def locate(dist_dir: str, version: str) -> tuple:
    """在 `dist_dir` 里找到该版本的 wheel 与 sdist（返回完整路径，找不到为 None）。"""
    if not os.path.isdir(dist_dir):
        return None, None
    wheel, sdist = match_artifacts(sorted(os.listdir(dist_dir)), version)
    return (os.path.join(dist_dir, wheel) if wheel else None,
            os.path.join(dist_dir, sdist) if sdist else None)


def main(argv=None) -> int:
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="发布前用真产物核对分发口径")
    ap.add_argument("--dist", default=os.path.join(_ROOT, "dist"), help="产物目录（默认 ./dist）")
    ap.add_argument("--version", default=None, help="版本号（默认取 config.APP_VERSION）")
    ns = ap.parse_args(argv)

    version = ns.version
    if not version:
        import config
        version = config.APP_VERSION

    wheel, sdist = locate(ns.dist, version)
    print("=" * 70)
    print(f"分发产物核对 · wme-watermark-eraser {version}")
    print(f"  产物目录 : {ns.dist}")
    print(f"  wheel    : {wheel or '(未找到)'}")
    print(f"  sdist    : {sdist or '(未找到)'}")
    print("=" * 70)
    if not wheel and not sdist:
        print("结论：目录里没有任何该版本的产物 ⇒ 跳过（退出码 2）。")
        print("      先跑一次 `python -m build`（构建隔离需要能取到 setuptools/wheel）。")
        return 2

    problems = []
    if wheel:
        w = check_wheel(wheel)
        print(f"  wheel 条目核对：{'合格' if not w else f'{len(w)} 项问题'}")
        problems += [f"[wheel] {p}" for p in w]
    else:
        problems.append("[wheel] 没找到 wheel 产物（只有 sdist ⇒ 用户 pip install 走的是源码构建）")
    if sdist:
        s = check_sdist(sdist)
        print(f"  sdist 条目核对：{'合格' if not s else f'{len(s)} 项问题'}")
        problems += [f"[sdist] {p}" for p in s]
    else:
        problems.append("[sdist] 没找到 sdist 产物")

    if problems:
        print("-" * 70)
        for p in problems:
            print(f"  ❌ {p}")
        print("-" * 70)
        print(f"结论：{len(problems)} 项不合格 ⇒ 发布前必须修（退出码 1）。")
        return 1
    print("结论：wheel 与 sdist 的条目都符合分发口径（退出码 0）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
