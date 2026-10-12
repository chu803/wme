# -*- coding: utf-8 -*-
"""阶段 294 · README「项目规模」刷新 + 规模下限守卫。

## 为什么需要
README §4 项目规模表曾**严重 stale**：写「测试 1346 例 / 92 文件」「设计文档 103 份」
「质量门禁 两条」，而实测 2500+ 用例 / 219 文件 / 249 篇 / 门禁家族 8 成员；且「已知局限」
一节仍称「services/ui 两层未完成审计」——而 45 模块早已全审、5j 续审链收官。
主文档**低报项目成熟度**，对「对标龙头 / 准备发布」有实质影响。本阶段据实刷新 README，
并把可廉价核验的规模固化为**下限守卫**。

## 设计取舍：为什么是「下限」而非「精确相等」
docs / 测试文件数是**随阶段持续增长**的量：精确相等守卫会在每次新增时假红（脆弱、
终被忽略）。故用 **≥ 下限**：
  · 抗回归——大规模删除测试/文档会跌破下限 ⇒ 变红；
  · 对增长免疫——新增不触发；
  · README 侧相应改为「N+ 篇 / 219 文件 / 2500+ 用例」的「+」框架，增长无需改 README。

**精确用例数（2500+）刻意不做廉价自动守卫**：它需 `pytest --collect-only` 全量收集
（导入全部测试模块，秒级~数十秒），对卫生守卫过重；它作为**周期快照**维护（同记分卡），
README 已引导「以 python -m pytest --co -q 为准」。
"""
from __future__ import annotations

import glob
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: README §4 声明的规模**下限**（增长安全、回归报警）。上调 README 数字时同步上调此处。
_DOCS_FLOOR = 249      # README「设计文档 249+ 篇」
_TEST_FILES_FLOOR = 219  # README「测试 219 文件」


def test_docs_count_meets_readme_floor():
    """docs/ 下 .md 篇数不得低于 README §4 声明的下限（防大规模删除回归）。"""
    n = len(glob.glob(os.path.join(_ROOT, "docs", "*.md")))
    assert n >= _DOCS_FLOOR, (
        f"docs/*.md 仅 {n} 篇 < README §4 声明下限 {_DOCS_FLOOR} ⇒ 疑有大规模删除；"
        f"若确有删减，请同步下调 README §4 与 _DOCS_FLOOR"
    )


def test_test_files_count_meets_readme_floor():
    """tests/ 下 .py 文件数不得低于 README §4 声明的下限（防测试套件缩水）。"""
    n = len(glob.glob(os.path.join(_ROOT, "tests", "*.py")))
    assert n >= _TEST_FILES_FLOOR, (
        f"tests/*.py 仅 {n} 个 < README §4 声明下限 {_TEST_FILES_FLOOR} ⇒ 疑有测试被删；"
        f"若确有删减，请同步下调 README §4 与 _TEST_FILES_FLOOR"
    )
