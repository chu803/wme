# -*- coding: utf-8 -*-
"""阶段 326 · 对外数字的防腐守卫（`docs/quality/product_scorecard.json`）。

背景（为什么必须有）：325 之后做一次「对外口径据实核一遍」，实测发现三处早就与现实脱节却
没有任何东西在管：

| 声明处 | 写着 | 实测（2026-10-25） |
|---|---|---|
| `CHANGELOG.md` 第 3 行 | 逐阶段文档「共 118 份」 | `docs/*.md` **284** 个（含 INDEX） |
| 记分卡 `test_cases` | **2504** | `pytest --collect-only -q` 求和 = **3359** |
| 记分卡 `test_files` / `docs` | 219 / 246 | **242** / **284** |

**为什么这些数字重要**：记分卡是「机器可读的对外承诺」，`purpose` 字段自己写着
「供每个阶段后刷新」。数字烂掉等于对外宣称一个不存在的项目规模——比虚报小的更糟的是
**读的人无法判断哪个是真的**。

**为什么不写死等号**：文档数每加一篇就会动；等号会让每个阶段都红一次，然后被人调高容差
或删除守卫。这里用**方向 + 量级**双断言：声明值不得**高于**实测（不能吹），
也不得落后实测太多（不能烂）。阈值取 25%，注释里写清复测命令。
"""
from __future__ import annotations

import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SCORE_PATH = os.path.join(_ROOT, "docs", "quality", "product_scorecard.json")
#: 声明值允许落后实测值的比例（超过即判「对外数字已腐烂」）
STALE_TOLERANCE = 0.25


def _scorecard() -> dict:
    assert os.path.isfile(SCORE_PATH), "记分卡文件不存在"
    return json.load(open(SCORE_PATH, encoding="utf-8"))


def _engineering() -> dict:
    """取 engineering 条目（记分卡里它位于顶层 `dimensions` 列表）。"""
    score = _scorecard()
    items = score.get("dimensions") or score.get("categories") or []
    for item in items:
        if isinstance(item, dict) and item.get("id") == "engineering":
            return item
    raise AssertionError("记分卡里找不到 dimensions 中 id=engineering 的条目")


def _metrics(score: dict) -> dict:
    """工程条目里的 metrics（找不到就返回空 dict，由断言去报缺失）。"""
    return _engineering().get("metrics") or {}


def test_the_two_copies_of_the_same_numbers_agree():
    """`scale` 与 `dimensions[engineering].metrics` 抄了同一组数字 ⇒ 先保证两份不互相打脸。

    这是 326 实测到的真实状态：本次更新前 `scale.docs=246` 而 engineering `docs=280`，
    同一个事实的两个副本已经不一致。数字必须有唯一真值来源；短期内两处都要写，
    那就至少钉住「不许各说各话」。
    """
    score = _scorecard()
    scale, m = score.get("scale") or {}, _metrics(score)
    for key in ("test_cases", "test_files", "docs"):
        assert key in scale and key in m, f"{key} 在两处之一缺失"
        assert scale[key] == m[key], f"{key} 两份副本不一致：scale={scale[key]} vs engineering={m[key]}"


def test_scorecard_is_machine_readable_and_has_engineering_metrics():
    """前提断言：结构没变（结构变了后面的数字比较就没意义）。"""
    score = _scorecard()
    assert _engineering(), "engineering 条目缺失"
    m = _metrics(score)
    for key in ("test_cases", "test_files", "docs"):
        assert key in m, f"记分卡 engineering.metrics 缺 {key}"


def test_docs_count_is_not_stale():
    """`docs` 声明值 ≤ 实测（不许吹），且不得落后超过 STALE_TOLERANCE。"""
    actual = len([n for n in os.listdir(os.path.join(_ROOT, "docs")) if n.endswith(".md")])
    declared = _metrics(_scorecard())["docs"]
    assert declared <= actual + 1, f"记分卡 docs={declared} 高于实测 {actual}（复测：ls docs/*.md | wc -l）"
    assert declared >= round(actual * (1 - STALE_TOLERANCE)), (
        f"记分卡 docs={declared} 已落后实测 {actual} 超过 {STALE_TOLERANCE:.0%}"
        "⇒ 刷新 docs/quality/product_scorecard.json（复测命令同上）")


def test_test_file_count_is_not_stale():
    actual = len([n for n in os.listdir(os.path.join(_ROOT, "tests"))
                  if n.startswith("test_") and n.endswith(".py")])
    declared = _metrics(_scorecard())["test_files"]
    assert declared <= actual, f"记分卡 test_files={declared} 高于实测 {actual}"
    assert declared >= round(actual * (1 - STALE_TOLERANCE)), (
        f"记分卡 test_files={declared} 已落后实测 {actual}"
        "（复测：ls tests/test_*.py | wc -l）")


def test_test_case_count_is_between_def_and_parametrized_ceiling():
    """`test_cases` 夹在「`def test_` 条数」与「条数 ×2」之间。

    为什么不用真实收集数：`pytest --collect-only -q` 要 1~2 分钟，不该塞进单测；
    `def test_` 数是**下界**（参数化只增不减），×2 是按本项目参数化密度取的上界。
    实测（2026-10-25）：`def test_` **2565** 条 / 真实收集 **3359** 条（比值 1.31）
    ⇒ 3359 落在 [2565, 5130] 内；而过期的声明值 **2504** 低于下界 2565，被这条抓住
    （余量只有 61，说明**下界是紧的**——真值一旦回落到 def 数附近就该重查参数化是否被删）。
    """
    def_count = 0
    for n in sorted(os.listdir(os.path.join(_ROOT, "tests"))):
        if not (n.startswith("test_") and n.endswith(".py")):
            continue
        txt = open(os.path.join(_ROOT, "tests", n), encoding="utf-8", errors="ignore").read()
        def_count += len(re.findall(r"^\s*def test_", txt, re.M))
    declared = _metrics(_scorecard())["test_cases"]
    assert declared >= def_count, (
        f"记分卡 test_cases={declared} 低于 `def test_` 条数 {def_count}"
        "⇒ 数字已过期（参数化只会让真实收集数更大）")
    assert declared <= def_count * 2, (
        f"记分卡 test_cases={declared} 高于 `def test_` 条数 ×2（{def_count * 2}）"
        "⇒ 数字虚高或写法异常")


def test_changelog_doc_count_is_not_stale():
    """`CHANGELOG.md` 头部那句「共 N 份」也必须跟实测同数量级。

    这条是 326 的直接起因：它写着 118，而实测 284——差了 2.4 倍，且没有任何守卫。
    """
    head = "\n".join(open(os.path.join(_ROOT, "CHANGELOG.md"), encoding="utf-8").read().splitlines()[:6])
    m = re.search(r"共\s*(\d+)\s*份", head)
    assert m, f"CHANGELOG 头部没找到「共 N 份」这句：{head[:120]}"
    actual = len([n for n in os.listdir(os.path.join(_ROOT, "docs")) if n.endswith(".md")])
    declared = int(m.group(1))
    assert abs(actual - declared) <= max(2, round(actual * STALE_TOLERANCE)), (
        f"CHANGELOG 说 docs 共 {declared} 份，实测 {actual} 份"
        "（复测：ls docs/*.md | wc -l）")
