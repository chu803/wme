"""阶段 191 · 测试超时契约守卫（`pytest-timeout` 内外层一致性）。

为什么需要（实测事故）
--------------------
`pytest.ini` 设了**全局** `--timeout=300`（作用于**测试函数本身**），而某些用例会给
它自己 spawn 的**子进程**传更长的 `timeout=600`。此时**外层比内层还小**：

    子进程允许 600s，但测试函数 300s 就被 pytest-timeout 掐断
    ⇒ 一旦机器有负载、或在覆盖率插桩下运行，pytest-timeout 会**中断整个 session**
      （实测：全库覆盖率跑在 71% 被杀、16 分钟的数据全丢）。

契约（本文件机械化）：
    凡测试体内出现 `timeout=N` 且 `N >= _INNER_THRESHOLD`，
    该测试**必须**带 `@pytest.mark.timeout(M)` 且 `M > N`（未带则回落到全局值比较）。

只扫 `tests/`，不碰产品代码；阈值取 300（= 全局默认），低于它的内层天然安全。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TESTS = _ROOT / "tests"

#: 内层超时达到该值就必须有更宽的外层（= 全局默认，低于它不会与全局冲突）。
_INNER_THRESHOLD = 300

_FUNC_RE = re.compile(r"^def (test_\w+)\s*\(")
_INNER_RE = re.compile(r"timeout=(\d+)")
_MARK_RE = re.compile(r"@pytest\.mark\.timeout\((\d+)\)")


def _global_timeout() -> int:
    text = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    m = re.search(r"--timeout=(\d+)", text)
    assert m, "pytest.ini 未设置全局 --timeout（本契约依赖它）"
    return int(m.group(1))


def _strip_comments(text: str) -> str:
    """去掉整行注释（含行内 `#` 之后的内容）。

    ⚠ 本函数的存在本身就是一条教训：首版扫描器**把注释里的 `timeout=600` 当成了代码**，
    于是「解释这条契约的注释」反而把守卫自己判红（同族：189 的顺序断言也曾被文档字符串干扰）。
    """
    out = []
    for ln in text.splitlines():
        out.append(ln.split("#", 1)[0])
    return "\n".join(out)


def _iter_test_funcs(path: Path):
    """产出 (函数名, 起始行号, 装饰器文本, 函数体文本) —— **注释已剔除**。"""
    raw = path.read_text(encoding="utf-8").splitlines()
    lines = [ln.split("#", 1)[0] for ln in raw]     # 行内注释一并剔除，行号保持不变
    for i, line in enumerate(lines):
        m = _FUNC_RE.match(line)
        if not m:
            continue
        # 收集紧邻其上的装饰器（含多行装饰器的续行），遇到空行即停
        deco, j = [], i - 1
        while j >= 0 and lines[j].strip() and not _FUNC_RE.match(lines[j]):
            deco.append(lines[j])
            if lines[j].startswith("@"):
                break
            j -= 1
        # 函数体：到下一个顶层 def / class 为止
        body = []
        for k in range(i + 1, len(lines)):
            if _FUNC_RE.match(lines[k]) or lines[k].startswith(("class ", "def ")):
                break
            body.append(lines[k])
        yield m.group(1), i + 1, "\n".join(reversed(deco)), "\n".join(body)


def test_global_timeout_is_configured():
    assert _global_timeout() > 0


def test_no_test_has_outer_timeout_smaller_than_inner():
    """核心契约：内层 `timeout=N`（N ≥ 300）必须被**更宽**的外层超时包住。"""
    global_to = _global_timeout()
    problems = []
    for f in sorted(_TESTS.glob("test_*.py")):
        for name, lineno, deco, body in _iter_test_funcs(f):
            inners = [int(x) for x in _INNER_RE.findall(body)]
            if not inners:
                continue
            inner = max(inners)
            if inner < _INNER_THRESHOLD:
                continue
            marks = [int(x) for x in _MARK_RE.findall(deco)]
            outer = max(marks) if marks else global_to
            if outer <= inner:
                problems.append(
                    f"{f.name}::{name} (L{lineno}) 内层 timeout={inner}s 但外层={outer}s"
                    f"（{'未设 @pytest.mark.timeout' if not marks else '标记过小'}）")
    assert not problems, (
        "外层超时必须严格大于内层，否则 pytest-timeout 会抢先掐断并中断整个 session：\n  "
        + "\n  ".join(problems)
    )


def test_prompted_area_gate_regression_case_is_marked():
    """实测事故的定点回归：该用例的子进程允许 600s，外层必须更宽（已设 900s）。"""
    f = _TESTS / "test_prompted_area_gate.py"
    found = False
    for name, _ln, deco, body in _iter_test_funcs(f):
        if name != "test_gate_end_to_end_exit0":
            continue
        found = True
        inner = max(int(x) for x in _INNER_RE.findall(body))
        marks = [int(x) for x in _MARK_RE.findall(deco)]
        assert marks, "该用例必须显式设 @pytest.mark.timeout"
        assert max(marks) > inner, f"外层 {marks} 必须大于内层 {inner}"
    assert found, "找不到 test_gate_end_to_end_exit0（用例被改名？请同步本守卫）"
