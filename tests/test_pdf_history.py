# -*- coding: utf-8 -*-
"""
阶段 40 测试：PDF 结果历史记录与管理。

覆盖纯逻辑与轻量集成（全部零 torch，不依赖真实超分权重）：
  · render_pdf_first_page 的契约（缺文件/非 PDF/缺 fitz 回退 None；fitz 可用时正常渲染）
  · HistoryRecord 的 PDF 字段（page_count/file_size）序列化往返
  · CacheService.add_pdf_record 登记（首页缩略图；渲染失败回退占位；字段正确）
  · tab_media._store_pdf_record 调用缓存登记（monkeypatch，不落盘）
  · tab_history.on_select 对 PDF 记录返回结果路径 + 引导提示（不走图片对比）
  · tab_history.reprocess 对 PDF 记录给出提示并复位
  · load_gallery 对 PDF 记录渲染「📄 名称｜PDF · N页」徽章
  · i18n 关键键存在（history.detail.pdf / pdf_hint / compare.viewer.pdf_hint）
全部使用临时目录，不污染项目缓存。
"""

import os as _os
import sys as _sys

import numpy as np
import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config  # noqa: E402
from services import pdf_service as ps  # noqa: E402
from services.cache_service import CacheService, HistoryRecord  # noqa: E402
from utils.i18n import t, set_language  # noqa: E402

@pytest.fixture(autouse=True)
def _lang_zh():
    """阶段 249：语言设置从**模块级**移入夹具。

    模块级 set_language() 发生在**收集期**（先于一切夹具），conftest 的
    `_isolate_i18n_language` 只能保存已污染的值再还原，挡不住它；移入夹具后
    收集期零副作用，每个用例仍拿到 zh-CN（本夹具 setup 时设置、conftest 的
    teardown 还原）。
    """
    set_language("zh-CN")


# ------------------------------------------------------------------------------
# render_pdf_first_page 契约
# ------------------------------------------------------------------------------
def test_render_pdf_first_page_missing_file():
    assert ps.render_pdf_first_page("__not_exist__.pdf") is None


def test_render_pdf_first_page_non_pdf(tmp_path):
    png = str(tmp_path / "a.png")
    cv2 = __import__("cv2")
    cv2.imwrite(png, np.zeros((10, 10, 3), dtype=np.uint8) + 128)
    assert ps.render_pdf_first_page(png) is None


def test_render_pdf_first_page_no_fitz(monkeypatch):
    def _boom():
        raise RuntimeError("no fitz")
    monkeypatch.setattr(ps, "_import_fitz", _boom)
    # 文件都不存在也无所谓，_import_fitz 先抛，应被兜成 None。
    assert ps.render_pdf_first_page("anything.pdf") is None


@pytest.mark.skipif(not ps.is_pdf_available(), reason="PyMuPDF 未安装")
def test_render_pdf_first_page_happy(tmp_path):
    fitz = ps._import_fitz()
    src = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    doc.new_page(width=200, height=300)
    doc.save(src)
    doc.close()
    arr = ps.render_pdf_first_page(src)
    assert arr is not None
    assert arr.ndim == 3 and arr.shape[2] == 3
    assert arr.dtype == np.uint8


# ------------------------------------------------------------------------------
# HistoryRecord PDF 字段往返
# ------------------------------------------------------------------------------
def test_history_record_pdf_roundtrip():
    rec = HistoryRecord(
        id="pdf_1", ts=999.0, original_name="d.pdf", kind="pdf",
        result_path="/x/out.pdf", page_count=12, file_size=1024 * 57,
    )
    d = rec.to_dict()
    assert d["kind"] == "pdf"
    assert d["page_count"] == 12
    assert d["file_size"] == 1024 * 57
    rec2 = HistoryRecord.from_dict(d)
    assert rec2.kind == "pdf"
    assert rec2.page_count == 12
    assert rec2.file_size == 1024 * 57
    # 旧数据（无 pdf 字段）回溯为默认值，不崩。
    rec3 = HistoryRecord.from_dict({"id": "old", "kind": "image"})
    assert rec3.page_count == 0 and rec3.file_size == 0


# ------------------------------------------------------------------------------
# CacheService.add_pdf_record（不落盘真实缓存）
# ------------------------------------------------------------------------------
def _fake_pdf_cache(monkeypatch, tmp_path):
    """构造一个不碰真实缓存目录的 CacheService 测试替身。"""
    monkeypatch.setattr("services.cache_service.INDEX_PATH",
                        tmp_path / "history_index.json")
    monkeypatch.setattr("services.cache_service.ensure_dirs", lambda: None)
    monkeypatch.setattr("services.cache_service.make_thumbnail",
                        lambda arr, size: arr)
    monkeypatch.setattr("services.cache_service.save_thumbnail",
                        lambda arr, name: str(tmp_path / "thumb.png"))

    thumb_png = str(tmp_path / "firstpage.png")
    cv2 = __import__("cv2")
    cv2.imwrite(thumb_png, np.zeros((20, 20, 3), dtype=np.uint8) + 200)

    def _fake_render(path):
        return cv2.imread(thumb_png)
    monkeypatch.setattr(ps, "render_pdf_first_page", _fake_render)
    return CacheService(enabled=True)


def test_add_pdf_record_registers(monkeypatch, tmp_path):
    cache = _fake_pdf_cache(monkeypatch, tmp_path)
    rec = cache.add_pdf_record(
        str(tmp_path / "src.pdf"), str(tmp_path / "out.pdf"),
        original_name="src.pdf", params="共 3 页",
        page_count=3, file_size=2048, upscaled=False,
    )
    assert rec is not None
    assert rec.kind == "pdf"
    assert rec.page_count == 3
    assert rec.file_size == 2048
    assert rec.result_path == str(tmp_path / "out.pdf")
    assert rec.thumb_path is not None
    assert len(cache.list_records()) == 1


def test_add_pdf_record_render_fallback_placeholder(monkeypatch, tmp_path):
    """render_pdf_first_page 返回 None 时应回退占位缩略图，记录照常登记。"""
    cache = _fake_pdf_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(ps, "render_pdf_first_page", lambda path: None)
    rec = cache.add_pdf_record(
        str(tmp_path / "src.pdf"), str(tmp_path / "out.pdf"),
        original_name="src.pdf", page_count=1, file_size=10,
    )
    assert rec is not None
    assert rec.kind == "pdf"
    assert rec.thumb_path is not None


# ------------------------------------------------------------------------------
# tab_media._store_pdf_record（monkeypatch 缓存登记）
# ------------------------------------------------------------------------------
def test_tab_media_stores_pdf_record(monkeypatch, tmp_path):
    import ui.tab_media as tm

    captured = {}
    out_pdf = str(tmp_path / "fake_out.png")
    cv2 = __import__("cv2")
    cv2.imwrite(out_pdf, np.zeros((10, 10, 3), dtype=np.uint8) + 128)
    src_pdf = str(tmp_path / "src.pdf")

    class _FakeRes:
        out_path = out_pdf
        total_pages = 7

        def summary(self):
            return "共 7 页"

    def fake_add(src, out, **kw):
        captured["src"] = src
        captured["out"] = out
        captured["kw"] = kw
        return None

    monkeypatch.setattr(tm._CACHE, "add_pdf_record", fake_add)
    tm._store_pdf_record(_FakeRes(), src_pdf)
    assert captured["src"] == src_pdf
    assert captured["out"] == out_pdf
    assert captured["kw"]["page_count"] == 7
    assert captured["kw"]["file_size"] == _os.path.getsize(out_pdf)
    assert captured["kw"]["upscaled"] is False


def test_tab_media_stores_pdf_record_skips_invalid(monkeypatch):
    import ui.tab_media as tm
    called = {"n": 0}

    def fake_add(src, out, **kw):
        called["n"] += 1
        return None

    monkeypatch.setattr(tm._CACHE, "add_pdf_record", fake_add)

    class _FakeBad:
        out_path = None  # 无效结果路径
        total_pages = 0

        def summary(self):
            return ""
    tm._store_pdf_record(_FakeBad(), "/tmp/src.pdf")
    assert called["n"] == 0


# ------------------------------------------------------------------------------
# tab_history PDF 分支
# ------------------------------------------------------------------------------
def test_tab_history_on_select_pdf(monkeypatch):
    import ui.tab_history as th

    rec = HistoryRecord(
        id="p1", ts=1.0, original_name="d.pdf", kind="pdf",
        result_path="/tmp/d.pdf", page_count=5, file_size=4096,
    )
    monkeypatch.setattr(th, "_HISTORY_ID_MAP", [(rec.id, rec.result_path)])
    monkeypatch.setattr(th._CACHE, "get", lambda rid: rec)

    class _Evt:
        index = 0

    detail, dl, video, pair, html, summary = th.on_select(_Evt())
    assert dl == "/tmp/d.pdf"
    assert video is None          # PDF 记录无视频预览路径
    assert pair is None and html == "" and summary
    # 详情含页数信息、给出 PDF 引导提示。
    assert "5" in detail
    assert "pdf" in summary.lower() or "PDF" in summary


def test_tab_history_reprocess_pdf_hint(monkeypatch):
    import ui.tab_history as th

    rec = HistoryRecord(id="p2", ts=1.0, original_name="d.pdf", kind="pdf",
                        result_path="/tmp/d.pdf")
    monkeypatch.setattr(th._CACHE, "get", lambda rid: rec)
    msg, gallery = th.reprocess("p2", "lama", 1.0, 0)
    assert "PDF" in msg


def test_tab_history_load_gallery_pdf_badge(monkeypatch, tmp_path):
    import ui.tab_history as th

    thumb = str(tmp_path / "thumb.png")
    cv2 = __import__("cv2")
    cv2.imwrite(thumb, np.zeros((16, 16, 3), dtype=np.uint8) + 200)

    rec = HistoryRecord(id="p3", ts=1.0, original_name="报告.pdf", kind="pdf",
                        result_path="/tmp/d.pdf", thumb_path=thumb, page_count=9)
    monkeypatch.setattr(th._CACHE, "list_records", lambda: [rec])

    gallery, info = th.load_gallery(compare=False)
    assert len(gallery) == 1
    caption = gallery[0][1]
    assert "📄" in caption and "PDF" in caption and "9" in caption


def test_tab_history_record_detail_pdf():
    import ui.tab_history as th
    rec = HistoryRecord(id="p4", ts=1.0, original_name="d.pdf", kind="pdf",
                        result_path="/tmp/d.pdf", page_count=3, file_size=2 * 1024 * 1024)
    detail = th._record_detail(rec)
    assert "3" in detail and "2" in detail  # 页数 + 体积(MB)
    assert "pdf" in detail.lower()


# ------------------------------------------------------------------------------
# i18n 键存在性
# ------------------------------------------------------------------------------
def test_i18n_pdf_history_keys():
    for lang in ("zh-CN", "en-US"):
        set_language(lang)
        assert t("history.detail.pdf", pages=3, size_mb=1.5) != "history.detail.pdf"
        assert t("history.detail.pdf_hint") != "history.detail.pdf_hint"
        assert t("compare.viewer.pdf_hint") != "compare.viewer.pdf_hint"
