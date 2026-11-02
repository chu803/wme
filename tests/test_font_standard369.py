# -*- coding: utf-8 -*-
"""阶段 369 · 合成基准的**字体标准**必须固定在仓库里，不能随本机字体库存漂移。

起因（实测，同一 seed=7、同几何）：
  `scripts/make_demo_assets.py::_font()` 原先按「本机装了哪个字体」挑
  （Windows 取 arialbd、Linux 取系统字体目录里的 DejaVuSans-Bold、都没有→PIL 内置位图字体），
  于是**两台机器量的不是同一个水印**：

  | 用例（默认路径应完全检不出）        | arialbd | DejaVu(Linux) |
  |-------------------------------------|---------|---------------|
  | 256 png textured corner_logo        | 0 px    | 2813 px       |
  | 320 jpeg75 textured corner_logo     | 0 px    | 0 px          |

  edge_text 的 ΔPSNR 12.43→17.12、coverage_pct 1.16→1.49（覆盖率类阈值全在挪）。
  在 CI 等价环境里强制 DejaVu 跑整套 ⇒ 恰好翻红 1 条
  （`test_lowres_rescue::test_default_path_keeps_blackout[256-png-textured-corner_logo]`），
  其余 3398 条绿 ⇒ 这是**用例级的环境假设**，与 359/360/362 同族（第四类：字体）。

判据（不是「希望它固定」，而是「不固定就红」）：
  L1 随源码分发的字体文件在位，且 SHA-256 等于钉住的值（换了字体=换了量具）。
  L2 `_font(size)` 解析到的路径**就是**仓库内那份，且在仓库内 —— 不受本机字体影响。
  L3 全仓 .py（跳过以点开头的目录与构建产物）**任何字符串常量**都不许指向 OS 字体目录。
     用 AST，不用文本匹配：注释/文档里提一句「以前用本机字体目录里的 arialbd」不算引用
     （359/362 两次被文本判据误报，同族教训 #316/#337/#339/#341）。
  L4 反例双向：注入一条 OS 字体路径必须被抓；写成注释/普通句子不许被抓。
  L5 字体缺失时**明确报错**并说清成因与下一步 —— 不许静默回退到本机字体（那等于换个量具还自称同一把尺）。

跑法：PYTHONUTF8=1 python -m pytest tests/test_font_standard369.py -q
"""
from __future__ import annotations

import ast
import hashlib
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import scripts.make_demo_assets as mda  # noqa: E402

#: 钉死的字体指纹：matplotlib 3.11.2 随包分发的未修改 DejaVuSans-Bold.ttf。
#: 换字体=换量具 ⇒ 必须连同基准数字一起重锚，不允许只改这一行。
_FONT_SHA256 = "b184b89e3c1075f22f6b71575b6fc20d4972b3cfd3b23322ca6fd596dcaef167"

#: 各 OS 的字体目录前缀（源码**字符串常量**里出现它 = 把度量标准交给了本机）。
#: 这里刻意用片段拼接：本守卫不该被自己照红。已知可绕法=产品代码也用拼接写路径，
#: 静态判据抓不到；补位的是 L1/L2（`_font()` 必须解析到仓库内那份、指纹必须对），绕不过。
_B = chr(92)
_OS_FONT_DIRS = (
    "C:" + _B + "Win" + "dows" + _B + "Fonts",
    "/usr" + "/share" + "/fonts",
    "/usr/local" + "/share" + "/fonts",
    "/System" + "/Library" + "/Fonts",
    "/Library" + "/Fonts",
    "~/" + ".fonts",
    "~/.local" + "/share" + "/fonts",
)

_ARTIFACT_DIRS = {"__pycache__", "build", "dist", "node_modules",
                  "htmlcov", "benchmark_real", "logs", "cache"}


def _scan_os_font_refs(source: str):
    """返回**字符串常量**里指向 OS 字体目录的行号（AST，不看注释）。"""
    hits = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            for d in _OS_FONT_DIRS:
                if d in v:
                    hits.append(node.lineno)
                    break
    return hits


def _repo_py_files():
    """全仓 .py（含 tests/ —— 测试也不许把量具交给本机，360 的教训是「盲区比假绿贵」）。

    跳过：点开头的目录（.git / .scratch / .workbuddy / **`.pytest_runall_*`**——
    实测第一次跑 62.9 秒就因为它在 8 个 basetemp 里上万条目录项中翻 .py）、
    以及构建与产物目录。
    """
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in _ARTIFACT_DIRS
                       and not d.startswith("venv") and d != ".venv"]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


# ---------------------------------------------------------------------------
# L1 字体文件本体
# ---------------------------------------------------------------------------

def test_bundled_font_file_is_present():
    assert os.path.isfile(mda.BUNDLED_FONT), (
        "仓库内的基准字体缺失：%s ⇒ 合成水印无法跨机器复现" % mda.BUNDLED_FONT)
    assert os.path.isfile(os.path.join(_ROOT, "scripts", "assets", "LICENSE-DejaVu.txt")), (
        "字体许可文本必须与字体同目录分发（Bitstream Vera 许可要求副本带上许可原文）")


def test_bundled_font_sha_is_the_pinned_one():
    with open(mda.BUNDLED_FONT, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    assert got == _FONT_SHA256, (
        "基准字体被换掉了 ⇒ 所有以合成图为分母的数字（覆盖率/召回/ΔPSNR）都要重锚。\n"
        "期望 %s\n实际 %s" % (_FONT_SHA256, got[:16] + "..."))


# ---------------------------------------------------------------------------
# L2 _font() 只认仓库里那份
# ---------------------------------------------------------------------------

def test_font_helper_resolves_to_the_repo_file():
    f = mda._font(20)
    path = getattr(f, "path", None)
    assert path is not None, "_font 返回的不是 TrueType 字体（%r）⇒ 可能回退到内置位图字体" % type(f).__name__
    real = os.path.realpath(path)
    assert real == os.path.realpath(mda.BUNDLED_FONT), (
        "_font 用的不是仓库里那份字体：%s" % path)
    assert os.path.realpath(_ROOT).lower() in real.lower(), (
        "字体解析到了仓库之外（本机字体库存）：%s" % path)


def test_font_missing_fails_loudly_not_silently(tmp_path, monkeypatch):
    """字体缺失必须是**说清成因的报错**，不许静默换成别的量具。"""
    monkeypatch.setattr(mda, "BUNDLED_FONT", str(tmp_path / "nope" / "font.ttf"))
    with pytest.raises(Exception) as ei:
        mda._font(20)
    msg = str(ei.value)
    assert "font.ttf" in msg.replace(chr(92), "/"), "报错要点名缺的是哪个文件：%s" % msg[:200]
    assert ("git" in msg) or ("检出" in msg) or ("sdist" in msg), (
        "报错必须给出下一步（为什么缺、怎么补）：%s" % msg[:200])


# ---------------------------------------------------------------------------
# L3 全仓不引用 OS 字体目录
# ---------------------------------------------------------------------------

def test_no_source_file_references_os_font_directories():
    offenders = []
    for p in _repo_py_files():
        rel = os.path.relpath(p, _ROOT).replace("\\", "/")
        src = io.open(p, encoding="utf-8", errors="replace").read()
        for line in _scan_os_font_refs(src):
            offenders.append("%s:%d" % (rel, line))
    assert not offenders, (
        "这些位置把字体交给了本机字体库存（同一 seed 在不同 OS 上会渲染出不同像素）：\n  "
        + "\n  ".join(offenders)
        + "\n判据与替代方案见 docs/369；基准字体应随源码分发到 scripts/assets/。")


# ---------------------------------------------------------------------------
# L4 反例双向（否则 L3 是恒绿的装饰）
# ---------------------------------------------------------------------------

def test_scanner_catches_an_injected_os_font_path():
    """注入一条 Windows 字体路径 ⇒ 必须被抓（否则 L3 是恒绿装饰）。"""
    bs = chr(92)
    win_path = "C:" + bs + "Windows" + bs + "Fonts" + bs + "arialbd.ttf"   # 单反斜杠的真值
    bad = "import os\nP = %r\n" % win_path                                  # 写成字面量后 ast 解析回来仍是它
    assert _scan_os_font_refs(bad) == [2], "注入的 OS 字体路径没被抓到 ⇒ L3 不含信息"


def test_scanner_catches_posix_font_dir():
    bad = "P = %r\n" % (("/usr/" + "share/fonts") + "/truetype/x.ttf")
    assert _scan_os_font_refs(bad) == [1], "POSIX 字体目录没被抓到"


def test_scanner_does_not_flag_comments():
    """注释里提一句旧做法**不算**把量具交给本机（359/362 的文本判据就栽在这上面）。"""
    bs = chr(92)
    harmless = ("# 以前这里是 C:" + bs + "Windows" + bs + "Fonts" + bs
                + "arialbd.ttf，现在改成随仓库分发\nX = 1\n")
    assert _scan_os_font_refs(harmless) == [], "注释里的路径被当成引用 ⇒ 文本判据复发"


def test_scanner_flags_docstrings_and_that_is_an_agreed_tradeoff():
    """已知取舍：docstring 也是字符串常量 ⇒ 会命中。源码里别写 OS 字体路径的原文，
    要提就写成「arialbd 那类本机字体」这种不含目录的形式（本文件的模块文档就避开了）。"""
    dir_ = "/usr/" + "share/fonts"          # 运行时拼出来，别在本文件里写成整条字面量
    doc = '"""说明：Linux 上 %s 不一定存在。"""\nX = 1\n' % dir_
    assert _scan_os_font_refs(doc) == [1], "docstring 命中是刻意的，别把它当成假阳性去掉"


