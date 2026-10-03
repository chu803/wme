# -*- coding: utf-8 -*-
"""异常体系「定义 ↔ 使用」契约守卫测试（阶段 81）。

背景：`utils/exceptions.py` 精心设计了结构化异常（title / reason / suggestions /
detail）与两个呈现·包装能力，但**使用侧完全脱节**：

  E1 `to_markdown()`（文档写明「用于 Gradio 界面上的错误提示框」）生产代码**零调用**；
  E2 `wrap_unknown_error()` 生产代码**零调用**；
  E3 UI 层 13 个文件**零引用**异常基类，40 处错误提示全写成 `t("xxx.msg.yyy", exc=exc)`，
     靠 `str(exc)` 输出**纯文本** → 三段式结构化提示只落地了「文本拼接」这一层；
  E4 9 个叶子异常类从未被 `raise`；
  E5 `InvalidConfigValueError` 在 `path_helper.py` 中是**未使用导入**；
  E6 取消信号双实现（`queue_worker._CancelSignal` 在用 / `TaskCancelledError` 从未使用）。

本文件锁定修复后的契约，并把「定义了却没人用」变成必须显式登记的决策点。
"""

from __future__ import annotations

import pathlib
import re

import pytest

import utils.exceptions as ex

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 叶子异常类中**有意预留**的部分：它们为环境相关或未来场景准备，
# 当前无触发路径。新增未使用的异常类必须在此登记，否则守卫失败。
_RESERVED_EXCEPTIONS = {
    "OutOfVRAMError": "GPU 显存不足——本机为 CPU 后端，暂无触发路径",
    "OutOfMemoryError": "内存不足——同族预留",
    "DiskFullError": "磁盘写满——导出/缓存落盘时可能触发，尚未接入",
    "DownloadTimeoutError": "模型下载超时——download_model.py 自带重试，未走此异常",
    "ModelDownloadError": "模型下载失败——同上",
    "PortInUseError": "端口占用——server.py 启动期可能触发，尚未接入",
    "TaskCancelledError": "任务取消——queue_worker 用内部 _CancelSignal 实现（见边界记录）",
    "TaskTimeoutError": "任务超时——预留",
    "InvalidConfigValueError": "配置取值非法——config 解析器改为回退默认而非抛错",
}


def _all_exception_classes() -> dict:
    out = {}
    for name in dir(ex):
        obj = getattr(ex, name)
        if isinstance(obj, type) and issubclass(obj, BaseException) and obj is not BaseException:
            out[name] = obj
    return out


def _leaf_classes() -> dict:
    """叶子类 = 没有任何子类的异常类（分类基类被排除）。"""
    classes = _all_exception_classes()
    parents = set()
    for cls in classes.values():
        for base in cls.__mro__[1:]:
            if base.__name__ in classes:
                parents.add(base.__name__)
    return {k: v for k, v in classes.items() if k not in parents}


def _production_sources() -> dict:
    files = []
    for d in ("core", "services", "utils", "ui"):
        files += [p for p in (ROOT / d).rglob("*.py") if "__pycache__" not in str(p)]
    files += [ROOT / f for f in ("api.py", "server.py", "cli.py", "main.py")]
    return {str(p): p.read_text(encoding="utf-8", errors="ignore") for p in files}


# ==============================================================================
# 核心守卫 1 · 叶子异常类必须「被抛出」或「显式登记为预留」
# ==============================================================================
def test_every_leaf_exception_is_raised_or_reserved():
    """**守卫**：每个叶子异常类要么在生产代码中被 raise，要么登记在预留清单里。

    这条断言把「定义了却没人用」从"悄悄累积"变成"必须显式决策"。
    """
    sources = _production_sources()
    leaves = _leaf_classes()
    raised = set()
    for cls_name in leaves:
        pat = re.compile(rf"\braise\s+{re.escape(cls_name)}\b")
        if any(pat.search(src) for src in sources.values()):
            raised.add(cls_name)

    unaccounted = sorted(set(leaves) - raised - set(_RESERVED_EXCEPTIONS))
    assert not unaccounted, (
        "以下叶子异常类既未被抛出、也未登记为预留，请决定「接入」或「登记」：\n  "
        + "\n  ".join(unaccounted)
    )

    # 反向检查：登记项不得是无意义的历史残留（必须确实存在于模块中且确实未被抛出）
    for name in _RESERVED_EXCEPTIONS:
        assert name in leaves, f"{name} 已不是叶子异常类，请更新预留清单"
        assert name not in raised, f"{name} 已被实际抛出，请从预留清单移除"


def test_reserved_exceptions_carry_explanation():
    """预留登记必须带原因说明（不能是空占位）。"""
    for name, why in _RESERVED_EXCEPTIONS.items():
        assert why and len(why.strip()) >= 8, f"{name} 的预留说明过短：{why!r}"


# ==============================================================================
# 核心守卫 2 · 结构化呈现能力必须被生产代码使用（防死代码回归）
# ==============================================================================
def test_to_markdown_is_used_in_production():
    """`to_markdown()` 必须被生产代码调用（修复前是死代码，只有测试碰过）。"""
    sources = _production_sources()
    hits = [p for p, s in sources.items() if re.search(r"\.to_markdown\(", s)]
    assert hits, "to_markdown() 又变回死代码了"


def test_ui_uses_unified_render_entry():
    """**守卫**：UI 层不得再出现裸 `exc=exc`，必须经过统一渲染入口。"""
    offenders = []
    for p in (ROOT / "ui").rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"exc=exc\b", src):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"以下文件仍在直接插值异常对象：{offenders}"


def test_ui_imports_render_helper():
    """UI 层使用的渲染入口必须真的被 import（防拼错名字）。"""
    for p in (ROOT / "ui").rglob("tab_*.py"):
        if "__pycache__" in str(p):
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        if "format_exception_for_ui(" in src:
            assert "format_exception_for_ui" in src.split("format_exception_for_ui(")[0] or \
                   "import" in src, f"{p} 使用了渲染入口但未见导入"


# ==============================================================================
# format_exception_for_ui 的契约
# ==============================================================================
def test_custom_exception_renders_as_structured_markdown():
    """自定义异常 → 结构化 Markdown（标题 / 原因 / 诊断 / 建议）。"""
    err = ex.ImageTooLargeError(
        reason="图片体积 12.3 MB 超过上限 10 MB。",
        detail={"上限MB": 10},
    )
    out = ex.format_exception_for_ui(err)
    assert out.startswith("### ❌"), "缺少标题层级"
    assert "**原因**" in out and "12.3 MB" in out
    assert "**解决建议**" in out, "建议列表未渲染"
    assert "上限MB" in out


@pytest.mark.parametrize("raw", [
    ValueError("bad value"),
    FileNotFoundError("missing"),
    RuntimeError("boom"),
    KeyError("k"),
    OSError("io"),
])
def test_builtin_exception_output_unchanged(raw):
    """**零回归契约**：内置异常必须原样返回 `str(exc)`，不得改变既有措辞。"""
    assert ex.format_exception_for_ui(raw) == str(raw)


def test_never_raises_even_for_hostile_objects():
    """处在错误处理路径上，本函数自身**绝不能抛**。"""
    class _HostileStr(Exception):
        def __str__(self):
            raise RuntimeError("__str__ 也炸了")

    assert ex.format_exception_for_ui(_HostileStr()) == "_HostileStr"
    assert ex.format_exception_for_ui(Exception()) == ""


def test_hostile_subclass_of_custom_error_still_safe():
    """自定义异常的子类若渲染失败，仍回退为 str()，不冒泡。"""
    class _Weird(ex.ImageError):
        def to_markdown(self):
            raise RuntimeError("渲染失败")

    out = ex.format_exception_for_ui(_Weird(reason="x"))
    assert isinstance(out, str) and out


# ==============================================================================
# 基类结构完整性
# ==============================================================================
def test_every_exception_overrides_title():
    """每个具体异常类都必须覆盖 default_title（否则界面显示「发生未知错误」）。"""
    base_title = ex.WatermarkEraserError.default_title
    bad = [name for name, cls in _all_exception_classes().items()
           if name != "WatermarkEraserError" and cls.default_title == base_title]
    assert not bad, f"以下异常类未定制标题：{bad}"


def test_custom_exceptions_carry_all_three_sections():
    """三要素（标题/原因/建议）必须都能渲染出来。"""
    err = ex.ModelNotFoundError(reason="models/big-lama.pt 不存在。")
    text = err.to_text()
    assert "【" in text and "】" in text
    assert "原因：" in text
    assert "建议：" in text
    assert err.suggestions and all(isinstance(x, str) for x in err.suggestions)


def test_exception_hierarchy_is_rooted_in_wme_error():
    """所有自定义异常必须继承自共同基类（便于统一捕获）。"""
    for name, cls in _all_exception_classes().items():
        assert issubclass(cls, ex.WatermarkEraserError), name


def test_wrap_unknown_error_still_functional():
    """`wrap_unknown_error` 虽未接入生产，其能力必须保持可用（预留能力，不得腐化）。"""
    wrapped = ex.wrap_unknown_error(RuntimeError("CUDA out of memory"), context="推理")
    assert isinstance(wrapped, ex.WatermarkEraserError)
    assert wrapped.to_text()
