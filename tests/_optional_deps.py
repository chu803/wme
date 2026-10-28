# -*- coding: utf-8 -*-
"""可选依赖的**安全探测**（CI 最小依赖里没有的东西：PyMuPDF / torch / easyocr…）。

为什么不能直接用 `importlib.util.find_spec("fitz")`（阶段 358 的教训）：
`scripts/ci_env_probe.py` 正是**钩住 `find_spec`** 来模拟「CI 上没有这个模块」，
⇒ 在探针环境里 `find_spec("fitz")` 自己就抛 `ModuleNotFoundError` ⇒ 用它做探测的代码
**在收集期就炸**（我第一版就是这么写的，探针立刻把两个文件打成 ERROR）。

所以这里的做法是：**真的去 import**，捕获 `ImportError`
（`ModuleNotFoundError` 是它的子类 ⇒ 探针的模拟同样被捕获）。

另外：模块顶层的 `import fitz` 会被 `tests/test_ci_optional_imports358.py` 拦下
⇒ 这里的 import 放在**函数体内**，不属于顶层。
"""
from __future__ import annotations

from typing import Callable


def _probe(module: str) -> bool:
    try:
        __import__(module)
        return True
    except ImportError:
        return False


def has_fitz() -> bool:
    """PyMuPDF 是否可用（PDF 功能的前提）。"""
    return _probe("fitz")


def has_torch() -> bool:
    """torch 是否可用（LaMa / 真实超分的前提）。"""
    return _probe("torch")


def has_easyocr() -> bool:
    """EasyOCR 是否可用（OCR 文字水印的前提）。"""
    return _probe("easyocr")


AVAILABILITY: dict = {
    "fitz": has_fitz,
    "torch": has_torch,
    "easyocr": has_easyocr,
}


def check(name: str) -> bool:
    """按名字查可用性（未知名字一律当作「有」，避免误伤）。"""
    fn: Callable[[], bool] = AVAILABILITY.get(name, lambda: True)
    return bool(fn())
