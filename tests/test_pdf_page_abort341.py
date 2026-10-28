# -*- coding: utf-8 -*-
"""阶段 341 · 一页出错不许毁掉整份 PDF：中止必须是**专用**异常类型。

被修掉的自相矛盾（`services/pdf_service.py`）：

    if not config.PDF_KEEP_FAILED_PAGE:
        raise RuntimeError(f"第 {pr.index} 页处理失败：…")
    except RuntimeError:
        raise        # 注释写着「由 PDF_KEEP_FAILED_PAGE=False 主动抛出的中断」

但那层 `try` 包住的是**整段逐页处理**，而这段里会抛 `RuntimeError` 的不止中止这一处：
`_encode_page_image` 自己就抛 `RuntimeError("页面图像编码彻底失败…")`（`:316`），
PyMuPDF 的渲染/插入错误同样可能是 `RuntimeError` ⇒ 一页坏就把整份文档中止，
既违反同一函数里那句「单页异常不该毁掉整份文档」，也无视默认值 `PDF_KEEP_FAILED_PAGE=True`。

判据：**「我要主动中止」不能和「事情出错了」共用一个异常类型**（#319 宽口子同族）。
"""
from __future__ import annotations

import pytest

# 阶段 358：⚠ **不许在模块顶层 import 可选依赖**。
#   CI 只装 requirements-min/dev（**不含 PyMuPDF**）⇒ 顶层 `import fitz` 会让
#   **整个 CI 子集在收集期 ImportError** ⇒ pytest 退出码 2 ⇒ 阶段 330 之后（2 不再当"跳过"）
#   入口判失败 ⇒ CI 红，而且红得飞快（收集即中止）。
#   ⇒ 可选依赖一律用 importorskip：缺了就**跳过这条用例**，而不是让整个子集收集失败。
fitz = pytest.importorskip("fitz")

import config
import services.pdf_service as ps


def _make_pdf(tmp_path, pages: int = 3) -> str:
    path = str(tmp_path / "in.pdf")
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=80, height=60)
        page.insert_text((10, 30), f"page {i}")
    doc.save(path)
    doc.close()
    return path


def _fake_process_image(fail_call: int | None):
    """按调用次序模拟「第 N 页炸了」，其余页原样返回。

    ⚠ `status` 必须用管线**真实的词汇**（`success / skipped / failed`，见
    `auto_pipeline:192`）；第一版我写了个不存在的 `"no_watermark"`，结果那两页被
    当成真失败计入 `failed_pages`，差点把「测试自己错」当成产品缺陷报出去
    （339/340 同条教训：桩要按被测契约的真实取值域造）。
    """
    state = {"n": 0}

    def _f(rgb, **kw):
        i = state["n"]
        state["n"] += 1
        if fail_call is not None and i == fail_call:
            raise RuntimeError("模拟：该页渲染/编码失败")
        return {"status": "skipped", "result_rgb": rgb, "mask": None,
                "stats": None, "recommended_preset": None, "recommendation": None,
                "error": ""}

    return _f


def _run(tmp_path, monkeypatch, *, fail_call, keep):
    src = _make_pdf(tmp_path)
    monkeypatch.setattr(config, "PDF_KEEP_FAILED_PAGE", keep)
    monkeypatch.setattr(ps, "process_image", _fake_process_image(fail_call))
    res = ps.process_pdf(src, str(tmp_path / "out.pdf"), dpi=72, mode="auto")
    return src, res


def test_one_page_runtime_error_does_not_abort_the_document(tmp_path, monkeypatch):
    """**这条就是被修掉的**：默认 keep=True 时，坏页标失败、其余页照常处理、页数不塌。"""
    _src, res = _run(tmp_path, monkeypatch, fail_call=1, keep=True)

    assert res.status == "success", f"一页 RuntimeError 就中止了整份：{res.error}"
    assert res.failed_pages == 1, res.summary()
    assert res.skipped_pages == 2, res.summary()
    assert res.error in (None, ""), res.error
    doc = fitz.open(res.out_path)
    try:
        assert doc.page_count == 3, "输出页数必须等于源页数（坏页保留原样，不能整份丢）"
    finally:
        doc.close()
    bad = [p for p in res.pages if p.status == "failed"]
    assert bad and "模拟" in bad[0].error, [p.error for p in res.pages]


def test_keep_false_still_aborts_and_says_why(tmp_path, monkeypatch):
    """反向锚：用户明确设了 keep=False 时**必须**中止，而且说清是哪一页（别把修复改成静默放行）。"""
    _src, res = _run(tmp_path, monkeypatch, fail_call=1, keep=False)

    assert res.status == "failed", res.summary()
    assert "PdfAbortOnFailedPage" in (res.error or ""), (
        f"中止原因没交代清楚（应能看到专用异常类型）：{res.error}")
    assert "第" in (res.error or ""), f"没说是哪一页导致的中止：{res.error}"


def test_healthy_document_is_unaffected(tmp_path, monkeypatch):
    """正向对照：没有任何页出错时，行为与修复前一致（3 页全部走完、0 失败）。"""
    _src, res = _run(tmp_path, monkeypatch, fail_call=None, keep=True)
    assert res.status == "success", res.error
    assert res.failed_pages == 0 and len(res.pages) == 3, res.summary()


def test_abort_class_is_a_runtime_error_subclass():
    """`PdfAbortOnFailedPage` 必须是 `RuntimeError` 子类：上游按 RuntimeError 兜底的代码不许被改坏。"""
    assert issubclass(ps.PdfAbortOnFailedPage, RuntimeError)
