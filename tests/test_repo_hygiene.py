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
                           capture_output=True, text=True, timeout=30)
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
