# -*- coding: utf-8 -*-
"""
阶段 170 元守卫：凡 `print`/`write` 非 GBK 字符的 `scripts/*.py` 必须做 UTF-8 加固。

背景（阶段 170 门禁盲区审计 S2 · 元缺陷）：阶段 146 已在 `real_gate.py` 里
**详细记录并修复**过一个失效模式——Windows 上 stdout 被重定向到管道/文件时，
Python 按 GBK 编码输出，而脚本会 `print` `✅/❌/⚠/⇒/✓/✗/−/²` 等
**GBK 不可编码**字符 ⇒ `UnicodeEncodeError`，**进程退出码 1**。
后果：**「输出内容」决定了「门禁退出码」**——已通过的门禁被读成未通过。

但那次修复只落在 3 个文件，另外 10 个同样会打印非 GBK 字符的脚本没同步
——**同一判据没有机械化地覆盖整族**。本守卫把它机械化：**不读「哪些脚本该加固」，
让机器枚举「所有 print 了非 GBK 字符的脚本」，再断言它们都已加固**，
于是未来新增脚本自动纳入。

⚠ 已知边界：只覆盖**静态可枚举**的 print/write 实参；若某脚本把非 GBK 字符
放在**被 return 出去的字符串**里再由调用方打印，本静态扫描看不到
（那属于动态构造，需运行时守卫，不在本静态元守卫的射程内）。
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
# 认可两种加固标记：共用件调用，或历史上就地 reconfigure。
_HARDEN_MARKERS = ("force_utf8_stdio", "reconfigure")
# 少于这个命中数说明扫描逻辑/目录结构变了，守卫可能空转。
_MIN_HITS = 10


def _call_name(node: ast.AST) -> str:
    f = getattr(node, "func", None)
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _printed_non_gbk_chars(src: str) -> set:
    """返回被 print(...) / *.write(...) 输出的、GBK 不可编码的字符集合。"""
    tree = ast.parse(src)
    chars: set = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _call_name(n) in ("print", "write"):
            for m in ast.walk(n):
                if isinstance(m, ast.Constant) and isinstance(m.value, str):
                    for c in m.value:
                        if ord(c) > 127:
                            try:
                                c.encode("gbk")
                            except UnicodeEncodeError:
                                chars.add(c)
    return chars


def _scripts_with_printed_non_gbk():
    out = []
    for p in sorted(SCRIPTS.glob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        if _printed_non_gbk_chars(src):
            out.append((p, src))
    return out


def test_all_emoji_printing_scripts_are_hardened():
    """守卫：每个会打印非 GBK 字符的脚本都必须做 UTF-8 加固。"""
    hits = _scripts_with_printed_non_gbk()
    assert len(hits) >= _MIN_HITS, (
        f"只扫到 {len(hits)} 个会打印非 GBK 字符的脚本（预期 ≥{_MIN_HITS}），"
        f"扫描逻辑或目录结构可能已失效"
    )
    offenders = [p.name for p, src in hits
                 if not any(m in src for m in _HARDEN_MARKERS)]
    assert not offenders, (
        "以下脚本会打印非 GBK 字符却没做编码加固——在 GBK 管道下会把「通过」"
        f"读成「退出码 1」：{offenders}"
    )


def test_console_encoding_helper_exists():
    """共用件必须存在且导出 force_utf8_stdio。"""
    import scripts.console_encoding as ce
    assert callable(ce.force_utf8_stdio)


def test_force_utf8_stdio_is_idempotent_and_never_raises():
    """加固函数幂等、对不支持 reconfigure 的流也绝不抛。"""
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    force_utf8_stdio()
