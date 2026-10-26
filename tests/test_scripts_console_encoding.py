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


# ---------------------------------------------------------------------------
# 阶段 331 补 / 333 收口：把上面那条静态守卫自陈的**盲区**机械化
#   旧判据只看 `print(...)` 字面量里的非 GBK 字符 ⇒ 「字符串由函数构造后再打印」看不见。
#   当天就被咬：`docker_consistency_check.py` 打印 `audit()` 造出的 `⇒`，GBK 管道下
#   UnicodeEncodeError ⇒ rc=1，而它是统一入口的**默认第 3 步**（实测见 docs/331）。
#   331 第一版用的判据是「源码里出现任何非 ASCII」——太粗：注释与文档字符串也会触发，
#   会把人推向「为过守卫而乱改文案」。333 换成**精确但仍无盲区**的那一条：
#   **AST 里的字符串常量**（剔 docstring）含 GBK 编不出的字符 ⇒ 必须加固。
# ---------------------------------------------------------------------------

#: 不依赖第三方 `cp936` 表：直接试编码，失败即判定为「GBK 编不出」。
def _gbk_unencodable(text: str) -> set:
    bad = set()
    for ch in text:
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            bad.add(ch)
    return bad


def _docstring_nodes(tree) -> set:
    """返回「是 docstring 的 Constant 节点 id」，用于剔除——文案注释不该逼出加固调用。"""
    out = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if not isinstance(body, list) or not body:      # Lambda 等节点的 body 是表达式，不是语句列表
            continue
        if isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


#: `tests/` 不在本判据射程内：测试由 pytest 的捕获层负责，且**没有任何地方把测试文件当脚本 spawn**
#: （实测 `grep` 全仓无引用）。把测试也算进来只会逼人往测试文件里塞无用的加固调用。
_ENCODING_SCAN_SKIP_DIRS = {"htmlcov", "temp", "build", "dist", ".git", "__pycache__",
                            "scratch", ".pytest_cache", ".workbuddy", "tests"}


def _iter_py(skip_dirs: set):
    """**有界**遍历仓库代码：根目录 `*.py` + 每个顶层目录**下一层**的 `*.py`。

    为什么不用 `rglob("*.py")`：本仓有上万文件的资源目录（`benchmark_real/`、`cache/`、
    `htmlcov/`…），深走一次要**几分钟**——一条静态守卫不该那么贵（实测：判据一字未动，
    整文件耗时 62.66s → 秒级）。代码包都在 `core/services/ui/utils/scripts/tests` 这一层，
    一层已足够；若将来出现两层深的包，由 `test_legacy_selfcheck` 的子进程实跑兜底。
    """
    for p in list(ROOT.glob("*.py")) + list(ROOT.glob("*/*.py")):
        if set(p.relative_to(ROOT).parts) & skip_dirs:
            continue
        yield p


def test_every_entrypoint_with_unencodable_string_is_hardened():
    """**全仓**：有 `__main__` 且字符串常量里含 GBK 编不出的字符 ⇒ 必须做 UTF-8 加固。

    为什么按「常量出现在哪里都不管」而不是只看 `print` 实参：会被打印的字符串经常**不在**
    print 的实参里（`audit()` 的返回值、i18n 文案表、异常消息），只看 print 就是 331 那个盲区。
    为什么仍然有边界：若某字符是用 `chr(0x21D2)` 这类**运行期拼装**出来的，静态扫描看不见——
    那种形态在本仓不存在（实测），出现时由「子进程实跑自检」那条守卫（`test_legacy_selfcheck`）兜。
    """
    offenders, checked = [], []
    for p in sorted(_iter_py(_ENCODING_SCAN_SKIP_DIRS)):
        rel = p.relative_to(ROOT)
        src = p.read_text(encoding="utf-8", errors="replace")
        if '__name__ == "__main__"' not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        ds = _docstring_nodes(tree)
        bad: set = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in ds:
                bad |= _gbk_unencodable(n.value)
        if not bad:
            continue
        checked.append(rel.as_posix())
        if not any(m in src for m in _HARDEN_MARKERS):
            offenders.append(f"{rel.as_posix()}（含 {''.join(sorted(bad))[:12]}）")
    assert len(checked) >= 20, f"只扫到 {len(checked)} 个入口，判据或目录结构已失效 ⇒ 守卫在空转"
    assert not offenders, (
        "以下入口的字符串常量里有 GBK 编不出的字符却没加固 ⇒ 中文 Windows 控制台下"
        f"「自检通过」会被读成 rc=1（或静默少打一段）：{offenders}"
    )


def test_text_mode_subprocess_calls_declare_encoding():
    """**读侧**同族判据：`tests/` 与 `scripts/` 里所有 text/capture_output 的子进程调用都要写死编码。

    为什么读侧也要守卫：写侧加固后子进程发 UTF-8，而父进程默认按 locale(GBK) 解码 ⇒
    读线程抛 UnicodeDecodeError ⇒ `CompletedProcess.stdout is None` ⇒ 断言炸在
    `NoneType + str` 这种**与真因毫无关系**的地方（当天实测 19 条红全是这一族）。
    ⚠ 判据是**全仓**枚举而不是「只扫 tests/」：331 第一版就漏了根目录 `conftest.py` 的 `cli_run`
    fixture —— 它是**七个** `test_cli.py` 用例的公共出口，漏一处就是七条红。
    """
    import ast

    # 读侧判据**要**扫 tests/（调用点大多在测试里），所以只排除资源目录。
    files = sorted(_iter_py(_ENCODING_SCAN_SKIP_DIRS - {"tests"}))

    def _fname(node):
        f = getattr(node, "func", None)
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
        return ""

    offenders, checked = [], 0
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and _fname(n) in ("run", "check_output", "Popen", "call")):
                continue
            kw = {k.arg for k in n.keywords if k.arg}
            if not (({"text", "universal_newlines"} & kw) or "capture_output" in kw):
                continue
            if "encoding" in kw:
                checked += 1
                continue
            if not ({"text", "universal_newlines"} & kw):
                continue          # capture_output 但没有 text= ⇒ bytes 模式，本来就不会解码
            checked += 1
            offenders.append(f"{p.relative_to(ROOT).as_posix()}:{n.lineno}")
    assert checked >= 20, f"只扫到 {checked} 处文本模式子进程调用，枚举可能已失效"
    assert not offenders, (
        f"以下调用点用 locale 默认编码解码子进程输出（中文 Windows=GBK）⇒ 会读出 stdout=None：{offenders}"
    )


def test_force_utf8_stdio_is_idempotent_and_never_raises():
    """加固函数幂等、对不支持 reconfigure 的流也绝不抛。"""
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    force_utf8_stdio()
