"""
阶段 42：历史记录文件名搜索与「类型 + 关键词」联合筛选 的单元测试。

全部用例零 torch 依赖，聚焦三层实现：
  1. services.cache_service.CacheService.list_records —— kind + query 过滤。
  2. services.export_service.export_history_as_zip —— query 透传过滤。
  3. ui.tab_history.load_gallery / export_history_filtered_zip —— query 透传与兼容。
  4. utils.i18n —— 新增搜索键双语可解析。

注意：CacheService(enabled=False) 为隔离实例（不落盘、不污染单例）。
"""

import os as _os
import sys as _sys
import zipfile

import numpy as np
import pytest

# 项目根目录注入 sys.path，确保 ui/services 可被导入（测试运行目录可能为仓库根）。
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from services.cache_service import CacheService, HistoryRecord  # noqa: E402
from services.export_service import export_history_as_zip  # noqa: E402
import ui.tab_history as th  # noqa: E402


def _make_record(rid, name, kind="image", result_path=None, status="success"):
    return HistoryRecord(
        id=rid, ts=float(hash(rid)) % 1e9, original_name=name,
        kind=kind, result_path=result_path, status=status,
    )


# ---------------------------------------------------------------------------
# 1. list_records 过滤
# ---------------------------------------------------------------------------

def test_list_records_query_substring():
    cache = CacheService(enabled=False)
    cache._records = [
        _make_record("a", "cat_photo.png"),
        _make_record("b", "dog_photo.png"),
        _make_record("c", "CAT_scan.pdf"),
    ]
    # 不区分大小写子串匹配。
    out = cache.list_records(query="cat")
    names = {r.original_name for r in out}
    assert names == {"cat_photo.png", "CAT_scan.pdf"}
    # 空/None 关键词忽略。
    assert len(cache.list_records(query="")) == 3
    assert len(cache.list_records(query=None)) == 3


def test_list_records_kind_and_query_combined():
    cache = CacheService(enabled=False)
    cache._records = [
        _make_record("a", "cat.png", kind="image"),
        _make_record("b", "cat.mp4", kind="video"),
        _make_record("c", "dog.png", kind="image"),
    ]
    out = cache.list_records(kind="image", query="cat")
    assert [r.id for r in out] == ["a"]
    # 仅 kind 不过滤关键词。
    assert {r.id for r in cache.list_records(kind="image")} == {"a", "c"}


# ---------------------------------------------------------------------------
# 2. export_history_as_zip query 过滤
# ---------------------------------------------------------------------------

def test_export_history_as_zip_query_filter(tmp_path):
    img_png = str(tmp_path / "img.png")
    cv2 = __import__("cv2")
    cv2.imwrite(img_png, np.zeros((10, 10, 3), dtype=np.uint8) + 128)
    pdf_png = str(tmp_path / "doc.png")  # 用 png 占位，仅验证 query 过滤逻辑
    cv2.imwrite(pdf_png, np.zeros((10, 10, 3), dtype=np.uint8) + 64)

    class _FakeCache:
        def __init__(self, recs):
            self._recs = recs

        def list_records(self, kind=None, query=None):
            recs = self._recs
            if kind is not None:
                recs = [r for r in recs if getattr(r, "kind", "image") == kind]
            if query:
                q = query.strip().lower()
                if q:
                    recs = [r for r in recs if q in (r.original_name or "").lower()]
            return recs

    recs = [
        _make_record("a", "img.png", result_path=img_png),
        _make_record("b", "doc.png", result_path=pdf_png),
    ]
    out_zip = str(tmp_path / "out.zip")
    path = export_history_as_zip(_FakeCache(recs), query="doc", out_zip=out_zip)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert len(names) == 1 and "doc" in names[0]


# ---------------------------------------------------------------------------
# 3. ui.tab_history 透传与兼容
# ---------------------------------------------------------------------------

def test_load_gallery_query_passthrough(monkeypatch):
    captured = {}

    def fake_list(kind=None, query=None):
        captured["kind"] = kind
        captured["query"] = query
        return [_make_record("a", "cat.png")]

    monkeypatch.setattr(th._CACHE, "list_records", fake_list)

    # 无筛选无关键词 → 无参调用（兼容既有无参桩）。
    th.load_gallery(compare=False)
    assert captured["kind"] is None and captured["query"] is None

    # 有关键词 → 透传 query。
    th.load_gallery(compare=False, kind_filter="all", query="cat")
    assert captured["kind"] is None and captured["query"] == "cat"

    # 类型 + 关键词 → 两者同时透传。
    th.load_gallery(compare=True, kind_filter="image", query="dog")
    assert captured["kind"] == "image" and captured["query"] == "dog"


def test_export_history_filtered_zip_query_passthrough(monkeypatch):
    captured = {}

    def fake_export(cache, *, fmt=None, quality=None, out_zip=None,
                    kind=None, query=None):
        captured["kind"] = kind
        captured["query"] = query
        return "/tmp/x.zip"

    monkeypatch.setattr(th, "export_history_as_zip", fake_export)
    th.export_history_filtered_zip(kind_filter="pdf", query="report")
    assert captured["kind"] == "pdf" and captured["query"] == "report"
    th.export_history_filtered_zip(kind_filter="all", query="  ")
    assert captured["kind"] is None and captured["query"] is None


# ---------------------------------------------------------------------------
# 4. i18n 键存在
# ---------------------------------------------------------------------------

def test_i18n_search_keys():
    from utils.i18n import t, set_language
    for lang in ("zh-CN", "en-US"):
        set_language(lang)
        for k in ("history.search_label", "history.search_placeholder",
                  "history.search_info", "history.filter_active"):
            assert t(k) != k, f"{lang}: 缺失 {k}"
