"""阶段 162 · `ui/tab_models` i18n 契约守卫（含「全角标点泄漏」这一类）。

背景
----
`ui/tab_models` 是覆盖度荒漠榜次席（281 代码 / 15 测试）。探针确认其**接线契约**
（`tests/test_ui_wiring_contract110.py` 已覆盖）与**行为健壮性**（stub `model_manager`
后 9 种底层异常 + 边界输入全部兜住、元组长度正确）**均 by design 正确**。

但抓到一处 i18n 泄漏：`_render_status_md` 把分隔符**硬编码**成全角 `｜`：
    f"- {dev_line} ｜ {dev_label}"
项目自身的约定是「分隔符放在**本地化模板**里」（`slot_line` 的 zh 用 `｜`、en 用 `|`），
这一处却游离在模板之外 ⇒ **en-US 下仍显示全角分隔符**。

更深一层：项目既有的 i18n 守卫（`_first_cjk` 等）**只扫 CJK 汉字（U+4E00–U+9FFF）**，
因此「全角/CJK 标点类泄漏」在结构上**对守卫不可见**。本守卫用**加强字符类**
（CJK 汉字 + CJK 标点 U+3000–303F + 半/全角形式 U+FF00–FFEF）把这一类钉住。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui.tab_models as tm  # noqa: E402
from utils.i18n import get_language, set_language  # noqa: E402


def _suspect(s: str):
    """加强字符类：CJK 汉字 + CJK 标点 + 半/全角形式（→ 全角分隔符/括号也会被抓）。"""
    for c in s:
        o = ord(c)
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            return c
    return None


class _FakeMM:
    def list_models(self):
        # note 是注册表数据（非本模块字面量）——用 ASCII 以免把「数据」误判为「文案泄漏」
        return [{"name": "lama", "display_name": "LaMa", "size_mb": 200.5,
                 "loaded": True, "note": "built-in", "expected_mb": None}]

    def get_active_model(self):
        return "lama"

    def is_model_available(self, name):
        return name == "lama"

    def get_model_info(self):
        return {"device": "cpu", "device_name": "CPU", "device_is_gpu": False,
                "fp16": False, "cache_count": 1, "loaded": True, "path": "/m/lama.pt"}


@pytest.fixture
def mm(monkeypatch):
    monkeypatch.setattr(tm, "_mm", _FakeMM())
    lang = get_language()
    yield
    set_language(lang)          # 不污染其它用例（i18n 是进程级全局）


# --------------------------------------------------------------------------- #
# 1 · 渲染契约：en-US 不得含任何全角/CJK 字符
# --------------------------------------------------------------------------- #
def test_en_status_has_no_fullwidth_or_cjk(mm):
    set_language("en-US")
    out = tm._render_status_md()
    bad = _suspect(out)
    assert bad is None, f"en-US 状态面板含全角/CJK 字符 {bad!r}：{out!r}"


def test_en_models_md_has_no_fullwidth_or_cjk(mm):
    set_language("en-US")
    out = tm._render_models_md()
    bad = _suspect(out)
    assert bad is None, f"en-US 模型清单含全角/CJK 字符 {bad!r}：{out!r}"


def test_zh_status_still_has_cjk(mm):
    """反向守卫：zh-CN 下必须仍有中文/全角（防「把中文全改成英文常量」式假修复）。"""
    set_language("zh-CN")
    assert _suspect(tm._render_status_md()) is not None
    assert _suspect(tm._render_models_md()) is not None


def test_status_separator_is_localized(mm):
    """阶段 162 修复点：分隔符必须随语言切换（en=ASCII '|'，zh=全角 '｜'）。"""
    set_language("en-US")
    en = tm._render_status_md()
    set_language("zh-CN")
    zh = tm._render_status_md()
    assert " | " in en and "｜" not in en, f"en-US 分隔符未本地化：{en!r}"
    assert "｜" in zh, f"zh-CN 分隔符应为全角：{zh!r}"


# --------------------------------------------------------------------------- #
# 2 · 源码级：不得有用户可见的硬编码全角/CJK 字面量
#    （剔除 docstring、t(...) 实参、log.*(...) 实参——后两者是键名/开发者日志）
# --------------------------------------------------------------------------- #
def test_no_userfacing_hardcoded_nonascii_literals():
    src = (ROOT / "ui" / "tab_models.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    doc, excl = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = getattr(n, "body", [])
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                doc.add(id(b[0].value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            is_t = isinstance(f, ast.Name) and f.id == "t"
            is_log = (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                      and f.value.id == "log")
            if is_t or is_log:
                for a in n.args:
                    for sub in ast.walk(a):
                        if isinstance(sub, ast.Constant):
                            excl.add(id(sub))

    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if id(n) in doc or id(n) in excl:
                continue
            c = _suspect(n.value)
            if c:
                bad.append((n.lineno, n.value, c))
    assert not bad, f"tab_models 仍有用户可见硬编码非 ASCII 文案：{bad}"
