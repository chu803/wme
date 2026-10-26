# -*- coding: utf-8 -*-
"""阶段 205 · 仓库卫生守卫：追踪文件不得超限 / 文档承诺的忽略规则必须生效。

## 为什么需要

仓库里曾真实存在一个 **205 MB 的 `models/big-lama.pt` 被 git 追踪**，
而 `models/.gitkeep` 明确承诺「权重文件体积大，**已被 .gitignore 忽略，不会上传到
代码仓库**」——**文档与实现互相矛盾**（`.gitignore` 里根本没有 `models/` 规则）。

后果不是"洁癖问题"：GitHub 对**单文件 > 100 MB 硬性拒收** ⇒ `git push` 会失败。
即"推远端"这条既定下一步会被这个缺陷**直接阻断**，而没有任何守卫能提前发现。

## 本守卫锁定的不变式

1. `test_no_tracked_file_exceeds_size_limit`
   —— 任何被 git 追踪的文件不得超过 `_MAX_TRACKED_BYTES`。
   （阈值取 GitHub 的 **警告线 50 MB**，比硬性拒绝线 100 MB 更早报警。）
2. `test_documented_ignore_patterns_are_effective`
   —— 被文档承诺"已忽略"的路径，必须**真的**匹配 `.gitignore` 规则。
   （防止"承诺写了、规则没写"这类**文档与实现矛盾**再次发生，同 LESSONS 218「承诺面必须实测」。）

## 设计取舍

* 用 `git ls-files` 判定"追踪"——这是**唯一真值来源**，
  自己实现"哪些文件会被 git 追踪"等于重写一份 `.gitignore` 语义（必错）。
* git 不可用时 **skip**（而不是 fail）：本守卫针对"仓库纳管"场景，
  未纳入版本控制时不适用（同 170「空真值是合法状态」的处理）。
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 追踪文件的体积上限。GitHub 对单文件 > 100 MB **硬性拒绝**、> 50 MB 给出警告；
#: 取 50 MB 作阈值 ⇒ 比硬性拒绝线更早发现问题（且本项目的合法大资产仅 1~2 MB 图片）。
_MAX_TRACKED_BYTES = 50 * 1024 * 1024


def _git_tracked_files() -> list:
    """返回全部被追踪文件的仓库相对路径（NUL 分隔，避免路径被引号转义）。"""
    try:
        r = subprocess.run(["git", "-C", _ROOT, "ls-files", "-z"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):      # pragma: no cover
        pytest.skip("git 不可用")
    if r.returncode != 0:
        pytest.skip(f"非 git 工作树（rc={r.returncode}）——本守卫不适用")
    return [f for f in (r.stdout or "").split("\0") if f]


def test_no_tracked_file_exceeds_size_limit():
    """任何被追踪的文件都不得超过 `_MAX_TRACKED_BYTES`（默认 50 MB）。

    实测背景：`models/big-lama.pt` 曾以 **205 MB** 被追踪 ⇒ 会让 `git push`
    被 GitHub 的 100 MB 单文件硬上限**直接拒绝**，而当时没有任何守卫能发现。
    模型权重应经 `python download_model.py` 获取，不入库。
    """
    files = _git_tracked_files()
    assert files, "未取到任何追踪文件（判定路径可能失效，需更新）"

    oversized = []
    for rel in files:
        abs_path = os.path.join(_ROOT, rel.replace("/", os.sep))
        try:
            size = os.path.getsize(abs_path)
        except OSError:
            continue                                    # 工作区缺失（如 sparse checkout）
        if size > _MAX_TRACKED_BYTES:
            oversized.append(f"{rel} = {size / 1024 / 1024:.1f} MB")
    assert not oversized, (
        "以下追踪文件超过体积上限 %d MB（会让 git push 被远端拒绝）：\n  %s\n"
        "处置：① 大二进制/权重 → 加入 .gitignore 并 `git rm --cached` 停止追踪"
        "（勿删磁盘文件）；② 确需入库 → 改用 Git LFS。"
        % (_MAX_TRACKED_BYTES // 1024 // 1024, "\n  ".join(oversized))
    )


#: 文档**承诺已被忽略**的路径模式 → 该承诺的出处（供人核对，不是装饰）。
#: 新增"文档说忽略、但规则可能漏写"的路径时，往这里加一行。
_DOCUMENTED_IGNORES = {
    "models/big-lama.pt": "models/.gitkeep",
}


def test_documented_ignore_patterns_are_effective():
    """文档承诺「已被 .gitignore 忽略」的路径，必须**真的**被忽略。

    为什么单独守（LESSONS 218「承诺面必须实测」）：`models/.gitkeep` 白纸黑字写着
    「权重文件…已被 .gitignore 忽略，不会上传到代码仓库」，但 `.gitignore` 里
    **压根没有 `models/` 规则** ⇒ 205 MB 的权重被真实追踪。
    **"写了承诺"与"承诺成立"是两件事**，本条把前者与后者对账。
    """
    gitignore = os.path.join(_ROOT, ".gitignore")
    assert os.path.isfile(gitignore), ".gitignore 不存在"
    patterns = [
        ln.strip() for ln in open(gitignore, encoding="utf-8").read().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]

    def _ignored(rel: str) -> bool:
        # 用 git 自身判定（唯一真值来源），而非自己实现 glob 语义。
        r = subprocess.run(["git", "-C", _ROOT, "check-ignore", "-q", "--", rel],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        return r.returncode == 0

    unmet = []
    for rel, src in _DOCUMENTED_IGNORES.items():
        if _ignored(rel):
            continue
        # 若未被忽略，退一步：至少要求 .gitignore 里有能覆盖它的显式规则文本，
        # 否则说明"承诺有、规则没有"（本缺陷正是如此）。
        base = os.path.basename(rel)
        if not any(base in p or rel in p or p.rstrip("/") == os.path.dirname(rel)
                   for p in patterns):
            unmet.append(f"{rel}（承诺出处：{src}）")
    assert not unmet, (
        "以下路径被文档声明为「已被 .gitignore 忽略」，但规则并未覆盖：\n  "
        + "\n  ".join(unmet)
        + "\n处置：补 .gitignore 规则（文档与实现必须一致，见 LESSONS 218）。"
    )


# ---------------------------------------------------------------------------
# 阶段 261 · 根目录散落文件守卫（新增维度）
# ---------------------------------------------------------------------------
# 背景（docs/261）：阶段 188 出了仓库卫生清单并完成了 E1 归档，但**清单开出后
#   又在根目录留下了新垃圾**，且此前没有任何守卫能发现——实测抓到 7 个散落文件：
#     · covdata_parallel_quarantine.bin（188 §E2 已登记「孤儿，删除」，但一直没收尾）
#     · _b1.txt / _b1416.txt / _cfgdiff.json（探针临时残留）
#     · 3 个 0KB 畸形文件名，其中一个含 Word 私有区字符 U+F02A、把一句中文
#       当成了文件名（典型的「shell 重定向写错」事故）
#   它们全部**未被 git 跟踪**（git ls-files 为空）⇒ 磁盘清理零风险，但**一旦有人
#   `git add .` 就会污染仓库**——而没有任何自动判定会失败。
# 本节把「根目录只允许出现白名单文件」变成机器可判定的不变式。

#: 根目录**允许**出现的文件（源码 / 打包元数据 / 配置 / 入口 / 说明文档）。
#: 逐项显式登记而非「白名单 + 通配」，避免新增源码时误报（新增须显式登记＝
#: 让「根目录多了个文件」成为一个需要解释的决定，而非默默发生）。
_ALLOWED_ROOT_FILES = {
    # 入口与源码
    "api.py", "cli.py", "config.py", "conftest.py", "download_model.py",
    "job_manager.py", "main.py", "server.py",
    # 打包元数据
    "pyproject.toml", "build.spec", "MANIFEST.in",  # MANIFEST.in: sdist 口径对齐 wheel（2026-10-17）
    # 依赖清单
    "requirements.txt", "requirements-dev.txt", "requirements-gpu.txt",
    "requirements-min.txt",
    # 测试入口
    "pytest.ini", "test.bat", "test.sh",
    # 合规与说明
    "LICENSE", "README.md", "CHANGELOG.md", "TODO.md",
    "CONTRIBUTING.md", "SECURITY.md",  # 阶段 291 商品外壳体检补齐
    # 容器交付物（阶段 210 硬化；已 git 追踪，见 docs/250 静态审计）
    "Dockerfile", "Dockerfile.gpu", "docker-compose.yml",  # compose 见阶段 210/C5
    # 本地用户配置覆盖（gitignored，属正常产物）
    "config.user_overrides.json",
}

#: 根目录**点文件**白名单（版本控制与覆盖率配置；体积守卫另行负责）。
_ALLOWED_ROOT_DOTFILES = {
    ".gitignore", ".dockerignore", ".gitattributes", ".coveragerc",
    # 覆盖率数据（可再生、gitignored；docs/188 §E1 判定为合法保留）
    ".coverage",
}


def _is_tracked(rel: str) -> bool:
    """git 是否追踪该路径（唯一真值来源 = git 自身）。"""
    r = subprocess.run(["git", "-C", _ROOT, "ls-files", "--error-unmatch", "--", rel],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return r.returncode == 0


def test_root_dir_has_no_unexpected_files():
    """根目录不得出现白名单外的散落文件（探针残留 / 临时产物 / 畸形文件名）。

    实测背景（docs/261）：7 个垃圾文件曾静默躺在根目录，其中一个是含 U+F02A
    私有区字符的 0KB「把中文句子当文件名」的产物——**没有任何守卫会失败**，
    而任何人 `git add .` 都会把它们带进仓库。
    """
    allowed = _ALLOWED_ROOT_FILES | _ALLOWED_ROOT_DOTFILES
    unexpected = []
    for name in sorted(os.listdir(_ROOT)):
        abs_p = os.path.join(_ROOT, name)
        if not os.path.isfile(abs_p):
            continue                      # 目录不算（assets/ docs/ tests/ …）
        if name in allowed:
            continue
        unexpected.append(name)

    assert not unexpected, (
        "根目录出现白名单外的散落文件（会污染仓库，`git add .` 即入库）：\n  "
        + "\n  ".join(f"{n!r} ({os.path.getsize(os.path.join(_ROOT, n))} B)"
                     for n in unexpected)
        + "\n处置：① 若是临时产物/探针残留 → 归档到 .scratch/（已 gitignored）"
          "或删除；② 若是合法源码/配置 → 登记进 _ALLOWED_ROOT_FILES 并说明理由；"
          "③ 若是误操作产生的畸形文件名 → 直接删除（git 未跟踪，无风险）。"
          "详见 docs/188 §3 卫生清单与 docs/261。"
    )


def test_root_junk_is_not_git_tracked():
    """根目录若存在白名单外的散落文件，它们**必须未被 git 跟踪**。

    这条是上一条的**安全网**：即使守卫被绕过（白名单被放宽/垃圾被重新生成），
    也保证垃圾不会悄悄进版本库。判定用 `git ls-files`（唯一真值来源）。
    """
    allowed = _ALLOWED_ROOT_FILES | _ALLOWED_ROOT_DOTFILES
    tracked_junk = []
    for name in sorted(os.listdir(_ROOT)):
        abs_p = os.path.join(_ROOT, name)
        if not os.path.isfile(abs_p) or name in allowed:
            continue
        if _is_tracked(name):
            tracked_junk.append(name)
    assert not tracked_junk, (
        "以下白名单外的文件已被 git 追踪（必须 `git rm --cached` 并加 .gitignore）：\n  "
        + "\n  ".join(tracked_junk)
        + "\n处置：`git rm --cached <文件>` 停止追踪（保留磁盘文件）+ 补 .gitignore 规则。"
    )
