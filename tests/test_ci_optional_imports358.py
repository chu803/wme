# -*- coding: utf-8 -*-
"""阶段 358 · CI 等价子集里不许有「模块顶层 import 可选依赖」的测试文件。

现场（今天真红的原因）：阶段 341 我写的 `tests/test_pdf_page_abort341.py` 在模块顶层
`import fitz` ⇒ CI 只装 `requirements-min/dev`（**不含 PyMuPDF**）⇒ **收集期 ImportError**
⇒ pytest 退出码 **2** ⇒ 阶段 330 之后「2 不再当跳过」⇒ 入口判失败 ⇒ **CI 红**
（而且是"收集即中止"那种快红：1 分多钟，和截图里的时长完全对得上）。

守卫判据：模块顶层（AST 的 `tree.body`）import 了 CI 环境没有的可选依赖 ⇒ 红。
* `cv2` / `gradio` **在** requirements-min.txt 里（opencv-python-headless / gradio）⇒ 不算 offender；
* 函数体内的 import 不算（那才是正确写法）。

修法：可选依赖一律 `pytest.importorskip("fitz")` ⇒ 缺了**跳过这条用例**，
而不是让整个子集收集失败。
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

#: CI 环境（requirements-min.txt + requirements-dev.txt）**没有**的可选依赖。
#: ⚠ cv2 / gradio 在 min 清单里 ⇒ 不在此列。
OPTIONAL = {"fitz", "pymupdf", "torch", "torchvision", "easyocr",
            "basicsr", "realesrgan"}


def _module_level_imports(path: pathlib.Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in tree.body:                       # 只看模块顶层
        if isinstance(node, ast.Import):
            out.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append(node.module.split(".")[0])
    return out


def test_no_test_module_imports_optional_deps_at_module_level():
    offenders = []
    for p in sorted(TESTS.glob("test_*.py")):
        src = p.read_text(encoding="utf-8")
        for name in _module_level_imports(p):
            if name in OPTIONAL and f'importorskip("{name}")' not in src \
                    and f"importorskip('{name}')" not in src:
                offenders.append(f"{p.name}: 模块顶层 import {name}")
    assert not offenders, (
        "这些测试在模块顶层 import 了 CI 环境没有的可选依赖 ⇒ CI 收集期就 ImportError，"
        f"整个子集跑不起来（pytest 退出码 2 = 失败）：{offenders}")
