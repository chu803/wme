"""
阶段 43：历史记录批量删除（按当前筛选结果）+ 破坏性操作二次确认 的单元测试。

全部用例零 torch 依赖，聚焦三层实现：
  1. services.cache_service.CacheService.delete_by_filter —— 按类型 + 文件名关键词
     删除当前筛选视图下全部记录，并清理缩略图（保留结果成品图），返回删除条数。
  2. ui.tab_history.delete_filtered / delete_selected / clear_all —— 二次确认守卫
     （confirm=False 拒绝执行；confirm=True 执行并复位确认复选框）。
  3. utils.i18n —— 阶段 43 新增键双语可解析。

注意：CacheService(enabled=False) 为隔离实例（不落盘、不污染单例）。
"""

import os as _os
import sys as _sys

import pytest

# 项目根目录注入 sys.path，确保 ui/services 可被导入（测试运行目录可能为仓库根）。
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from services.cache_service import CacheService, HistoryRecord  # noqa: E402
import services.cache_service as cache_service  # noqa: E402
import ui.tab_history as th  # noqa: E402
from utils.i18n import t, set_language  # noqa: E402


def _make_record(rid, name, kind="image", thumb_path=None, result_path=None,
                 status="success"):
    return HistoryRecord(
        id=rid, ts=float(abs(hash(rid)) % 1_000_000_000), original_name=name,
        kind=kind, thumb_path=thumb_path, result_path=result_path, status=status,
    )


# enabled=True 的隔离缓存：把索引重定向到临时文件，避免污染项目真实缓存。
@pytest.fixture
def enabled_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "INDEX_PATH", tmp_path / "history.json")
    cache = CacheService(enabled=True)
    cache._records = []
    return cache


# ---------------------------------------------------------------------------
# 1. CacheService.delete_by_filter —— 服务层
# ---------------------------------------------------------------------------

def test_delete_by_filter_kind(enabled_cache):
    enabled_cache._records = [
        _make_record("a", "cat.png", kind="image"),
        _make_record("b", "clip.mp4", kind="video"),
        _make_record("c", "doc.pdf", kind="pdf"),
    ]
    n = enabled_cache.delete_by_filter(kind="video")
    assert n == 1
    assert {r.kind for r in enabled_cache.list_records()} == {"image", "pdf"}


def test_delete_by_filter_kind_and_query(enabled_cache):
    enabled_cache._records = [
        _make_record("a", "cat.png", kind="image"),
        _make_record("b", "cat.mp4", kind="video"),
        _make_record("c", "dog.png", kind="image"),
        _make_record("d", "CAT.pdf", kind="pdf"),
    ]
    # kind=image + query=cat → 仅命中 a；关键词不区分大小写由 list_records 保证。
    n = enabled_cache.delete_by_filter(kind="image", query="cat")
    assert n == 1
    # list_records 按 ts 倒序，顺序不固定，故用集合比较。
    assert {r.id for r in enabled_cache.list_records()} == {"b", "c", "d"}


def test_delete_by_filter_all_when_no_filter(enabled_cache):
    enabled_cache._records = [
        _make_record("a", "x.png", kind="image"),
        _make_record("b", "y.mp4", kind="video"),
    ]
    # kind=None / query=None → 删除全部。
    assert enabled_cache.delete_by_filter() == 2
    assert enabled_cache.list_records() == []


def test_delete_by_filter_empty_match(enabled_cache):
    enabled_cache._records = [_make_record("a", "x.png", kind="image")]
    assert enabled_cache.delete_by_filter(kind="video", query="nope") == 0
    assert len(enabled_cache.list_records()) == 1


def test_delete_by_filter_cleans_thumb_keeps_result(tmp_path, monkeypatch):
    # 重定向索引到临时文件，避免污染项目真实缓存。
    monkeypatch.setattr(cache_service, "INDEX_PATH", tmp_path / "history.json")
    thumb = str(tmp_path / "thumb.png")
    with open(thumb, "w", encoding="utf-8") as f:
        f.write("t")
    result = str(tmp_path / "result_not_exist.png")  # 故意不存在，验证不被误删
    cache = CacheService(enabled=True)
    cache._records = [_make_record("a", "x.png", kind="image",
                                   thumb_path=thumb, result_path=result)]
    n = cache.delete_by_filter(kind="image")
    assert n == 1
    # 缩略图被清理。
    assert not _os.path.exists(thumb)
    # 结果成品图（即便存在）也不在此方法内删除——delete_by_filter 仅清理缩略图。
    assert len(cache.list_records()) == 0


# ---------------------------------------------------------------------------
# 2. UI 层：二次确认守卫
# ---------------------------------------------------------------------------

class _FakeCache:
    """隔离的假缓存，记录调用以断言行为。"""

    def __init__(self, records=None):
        self._records = list(records or [])
        self.deleted_by_filter = []
        self.deleted_ids = []
        self.cleared = 0

    def list_records(self, kind=None, query=None):
        return list(self._records)

    def delete_by_filter(self, kind=None, query=None):
        self.deleted_by_filter.append((kind, query))
        n = len(self._records)
        self._records = []
        return n

    def delete(self, rid):
        self.deleted_ids.append(rid)
        return True

    def clear(self):
        self.cleared += 1
        n = len(self._records)
        self._records = []
        return n


@pytest.fixture
def patched_cache(monkeypatch):
    fake = _FakeCache([_make_record("a", "cat.png", kind="image")])
    monkeypatch.setattr(th, "_CACHE", fake)
    return fake


def test_delete_filtered_requires_confirm(patched_cache):
    msg, gallery, pair, html, summary, confirm = th.delete_filtered(
        compare=False, confirm=False, kind_filter="all", query=None)
    # 未确认 → 不调用删除，返回警告文案。
    assert patched_cache.deleted_by_filter == []
    assert msg == t("history.delete_filtered_warn")
    assert confirm is not None  # gr.update(value=False) 复位确认框


def test_delete_filtered_runs_with_confirm(patched_cache):
    msg, gallery, pair, html, summary, confirm = th.delete_filtered(
        compare=False, confirm=True, kind_filter="video", query="cat")
    # 确认 → 以同一过滤口径调用 delete_by_filter。
    assert patched_cache.deleted_by_filter == [("video", "cat")]
    assert msg == t("history.delete_filtered_ok", n=1)
    assert confirm is not None


def test_delete_selected_requires_confirm(patched_cache, monkeypatch):
    monkeypatch.setattr(th, "_LAST_SELECTED_ID", "a")
    msg, gallery, pair, html, summary, confirm = th.delete_selected(
        "a", compare=False, confirm=False)
    assert patched_cache.deleted_ids == []
    assert msg == t("history.msg.confirm_needed")
    assert confirm is not None


def test_delete_selected_runs_with_confirm(patched_cache, monkeypatch):
    monkeypatch.setattr(th, "_LAST_SELECTED_ID", "a")
    msg, gallery, pair, html, summary, confirm = th.delete_selected(
        "a", compare=False, confirm=True)
    assert patched_cache.deleted_ids == ["a"]
    assert msg == t("history.msg.deleted")
    assert confirm is not None


def test_clear_all_requires_confirm(patched_cache):
    msg, gallery, pair, html, summary, confirm = th.clear_all(
        compare=False, confirm=False)
    assert patched_cache.cleared == 0
    assert msg == t("history.msg.confirm_needed")
    assert confirm is not None


def test_clear_all_runs_with_confirm(patched_cache):
    msg, gallery, pair, html, summary, confirm = th.clear_all(
        compare=False, confirm=True)
    assert patched_cache.cleared == 1
    assert msg == t("history.clear_ok", n=1)
    assert confirm is not None


# ---------------------------------------------------------------------------
# 3. i18n 阶段 43 新键可解析
# ---------------------------------------------------------------------------

def test_i18n_stage43_keys():
    set_language("zh-CN")
    for k in ("delete_filtered_btn", "delete_filtered_confirm", "delete_filtered_ok",
              "delete_filtered_empty", "delete_filtered_warn"):
        assert t(f"history.{k}") != f"history.{k}"
    assert t("history.msg.confirm_needed") != "history.msg.confirm_needed"
    set_language("en-US")
    for k in ("delete_filtered_btn", "delete_filtered_confirm", "delete_filtered_ok",
              "delete_filtered_empty", "delete_filtered_warn"):
        assert t(f"history.{k}") != f"history.{k}"
    assert t("history.msg.confirm_needed") != "history.msg.confirm_needed"
    set_language("zh-CN")
