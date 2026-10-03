# -*- coding: utf-8 -*-
"""
阶段 41 测试：历史记录分类筛选与按类型批量导出。

覆盖纯逻辑与轻量集成（全部零 torch）：
  · CacheService.list_records 的 kind 过滤。
  · export_service.export_history_as_zip 的 kind 过滤（仅打包指定类型）。
  · tab_history.load_gallery 的 kind_filter 文案（图片/视频/PDF 徽章）。
  · tab_history.export_history_filtered_zip 的 kind 透传与空结果提示。
  · i18n 关键键存在（中英双语）。

全部使用临时目录，不污染项目缓存。
"""

import os as _os
import sys as _sys
import zipfile as _zipfile

import numpy as np
import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from services.cache_service import CacheService, HistoryRecord  # noqa: E402
from services.export_service import export_history_as_zip  # noqa: E402
from utils.i18n import set_language, t  # noqa: E402

set_language("zh-CN")


def _png(tmp_path, name="a.png"):
    p = str(tmp_path / name)
    cv2 = __import__("cv2")
    cv2.imwrite(p, np.zeros((10, 10, 3), dtype=np.uint8) + 128)
    return p


def _rec(rid, kind, name="x.png", thumb=None, result=None):
    return HistoryRecord(
        id=rid, ts=1.0, original_name=name, kind=kind,
        thumb_path=thumb, result_path=result,
        status="success",
    )


# ------------------------------------------------------------------------------
# CacheService.list_records kind 过滤
# ------------------------------------------------------------------------------
def test_cache_list_records_kind_filter():
    cs = CacheService(enabled=False)
    cs._records = [
        _rec("i1", "image"), _rec("v1", "video"),
        _rec("p1", "pdf"), _rec("i2", "image"),
    ]
    assert len(cs.list_records()) == 4
    pdfs = cs.list_records(kind="pdf")
    assert [r.id for r in pdfs] == ["p1"]
    imgs = cs.list_records(kind="image")
    assert {r.id for r in imgs} == {"i1", "i2"}
    # 缺省（None）返回全部，不受旧数据无 kind 影响。
    assert len(cs.list_records(kind=None)) == 4


# ------------------------------------------------------------------------------
# export_history_as_zip kind 过滤
# ------------------------------------------------------------------------------
class _FakeZipCache:
    """不碰磁盘的缓存桩：list_records 按 kind 过滤并返回含真实文件的记录。"""

    def __init__(self, recs):
        self._recs = recs

    def list_records(self, kind=None, query=None):
        if kind is None:
            return list(self._recs)
        return [r for r in self._recs if getattr(r, "kind", "image") == kind]


def test_export_history_as_zip_kind_filter(tmp_path):
    img_png = _png(tmp_path, "img.png")
    # PDF 结果文件：OpenCV 无法写 .pdf，且 export_history_as_zip 仅按字节打包，
    # 故用普通文件写入构造一个存在的结果文件即可。
    pdf_png = str(tmp_path / "doc.pdf")
    with open(pdf_png, "wb") as fh:
        fh.write(b"%PDF-1.4 fake result bytes")
    recs = [
        HistoryRecord(id="i1", ts=1.0, original_name="img.png", kind="image",
                      result_path=img_png, status="success"),
        HistoryRecord(id="p1", ts=2.0, original_name="doc.pdf", kind="pdf",
                      result_path=pdf_png, status="success"),
    ]
    fake = _FakeZipCache(recs)
    out = str(tmp_path / "out.zip")
    # 仅导出 PDF。
    export_history_as_zip(fake, kind="pdf", out_zip=out)
    with _zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert len(names) == 1
    assert "pdf" in names[0].lower() and "doc" in names[0]

    # 全部导出（无 kind）。
    out2 = str(tmp_path / "out_all.zip")
    export_history_as_zip(fake, out_zip=out2)
    with _zipfile.ZipFile(out2) as zf:
        assert len(zf.namelist()) == 2


# ------------------------------------------------------------------------------
# tab_history.load_gallery kind_filter 文案
# ------------------------------------------------------------------------------
def test_tab_history_load_gallery_kind_filter(monkeypatch, tmp_path):
    import ui.tab_history as th

    img_t = _png(tmp_path, "img_t.png")
    vid_t = _png(tmp_path, "vid_t.png")
    pdf_t = _png(tmp_path, "pdf_t.png")
    recs = [
        _rec("i1", "image", "图.png", thumb=img_t, result=img_t),
        _rec("v1", "video", "片.mp4", thumb=vid_t, result=vid_t),
        _rec("p1", "pdf", "报告.pdf", thumb=pdf_t, result=pdf_t),
    ]

    class _FakeCache:
        def list_records(self, kind=None, query=None):
            if kind is None:
                return list(recs)
            return [r for r in recs if getattr(r, "kind", "image") == kind]

        def get(self, rid):
            return next((r for r in recs if r.id == rid), None)

    monkeypatch.setattr(th, "_CACHE", _FakeCache())

    all_g, _ = th.load_gallery(kind_filter="all")
    assert len(all_g) == 3

    pdf_g, _ = th.load_gallery(kind_filter="pdf")
    assert len(pdf_g) == 1
    assert "📄" in pdf_g[0][1] and "PDF" in pdf_g[0][1]

    vid_g, _ = th.load_gallery(kind_filter="video")
    assert len(vid_g) == 1
    assert "🎬" in vid_g[0][1] and "视频" in vid_g[0][1]

    img_g, _ = th.load_gallery(kind_filter="image")
    assert len(img_g) == 1


# ------------------------------------------------------------------------------
# tab_history.export_history_filtered_zip kind 透传与空结果
# ------------------------------------------------------------------------------
def test_tab_history_export_filtered_zip_passthrough(monkeypatch):
    import ui.tab_history as th

    captured = {}

    def fake_export(cache, *, fmt=None, quality=None, out_zip=None, kind=None, query=None):
        captured["kind"] = kind
        return "/tmp/results.zip"

    monkeypatch.setattr(th, "export_history_as_zip", fake_export)

    path, msg = th.export_history_filtered_zip("pdf")
    assert captured["kind"] == "pdf"
    assert path == "/tmp/results.zip"
    assert "打包" in msg

    path2, _ = th.export_history_filtered_zip("all")
    assert captured["kind"] is None  # "all" 映射为不过滤


def test_tab_history_export_filtered_zip_empty(monkeypatch):
    import ui.tab_history as th

    def fake_export(cache, **kw):
        raise FileNotFoundError("历史记录中没有可导出的成功结果。")

    monkeypatch.setattr(th, "export_history_as_zip", fake_export)
    path, msg = th.export_history_filtered_zip("video")
    assert path is None
    assert "没有" in msg


# ------------------------------------------------------------------------------
# i18n 键存在性
# ------------------------------------------------------------------------------
def test_i18n_history_filter_keys():
    for lang in ("zh-CN", "en-US"):
        set_language(lang)
        for k in ("history.filter_label", "history.filter_info",
                  "history.filter_all", "history.filter_image",
                  "history.filter_video", "history.filter_pdf",
                  "history.export_filtered_btn", "history.export_filtered_file_label",
                  "history.export_filtered_ok", "history.export_filtered_empty"):
            assert t(k) != k, f"{lang}: 缺失 {k}"
