# -*- coding: utf-8 -*-
"""Stage 132 回归守卫：services/cache_service 两处真缺陷（D1/D2）。

D1：`CacheService.delete()` 先在锁内移除记录、**之后**才 `self.get(record_id)` 取
    thumb_path 删缩略图 ⇒ get 必然返回 None ⇒ 缩略图清理是死代码（历史页每删一条
    就漏一个缩略图，进而堆积成孤儿）。修复：移除前先快照 thumb_path。
D2：`__main__` 自检曾直接用真实 CACHE_DIR 并 `clear()` ⇒ 清空用户真实历史索引与
    缩略图（破坏性自检）。修复：把 file_service / 本模块目录与 INDEX_PATH 重定向到
    临时目录。守卫：源级断言 `__main__` 含 temp 重定向（防回退）。
    子进程实跑守卫见 test_legacy_selfcheck.py 的 MODULES。
"""
from __future__ import annotations

import inspect
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import services.cache_service as cs
from services.cache_service import CacheService, HistoryRecord


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """把索引重定向到临时文件，避免触碰真实历史索引。"""
    monkeypatch.setattr(cs, "INDEX_PATH", tmp_path / "history.json")
    c = CacheService(enabled=True)
    c._records = []
    return c


def _rec(rid: str, thumb) -> HistoryRecord:
    return HistoryRecord(id=rid, ts=time.time(), original_name=f"{rid}.png",
                         thumb_path=str(thumb), status="success")


# ==============================================================================
# D1 · delete 必须清理缩略图
# ==============================================================================
def test_d1_delete_removes_thumbnail(iso, tmp_path):
    thumb = tmp_path / "t1.png"
    thumb.write_bytes(b"\x00" * 8)
    iso._records = [_rec("r1", thumb)]
    assert iso.delete("r1") is True
    assert not thumb.exists(), "delete 应清理被删记录的缩略图"


def test_d1_delete_missing_record_returns_false(iso):
    assert iso.delete("__no_such__") is False


def test_d1_sibling_delete_by_filter_still_removes_thumbnail(iso, tmp_path):
    """回归：兄弟方法 delete_by_filter 的缩略图清理不得被破坏。"""
    thumb = tmp_path / "t2.png"
    thumb.write_bytes(b"\x00" * 8)
    iso._records = [_rec("r2", thumb)]
    assert iso.delete_by_filter() == 1
    assert not thumb.exists()


def test_d1_thumb_kept_when_concurrent_record_has_no_thumb(iso, tmp_path):
    """边界：记录无 thumb_path 时 delete 不得报错。"""
    iso._records = [HistoryRecord(id="r3", ts=time.time(), original_name="x.png",
                                  thumb_path=None, status="success")]
    assert iso.delete("r3") is True


# ==============================================================================
# D2 · 自检必须隔离（源级守卫）
# ==============================================================================
def test_d2_selfcheck_main_is_isolated():
    """__main__ 必须把目录/索引重定向到临时目录（防回退成破坏性自检）。"""
    src = inspect.getsource(cs)
    assert 'if __name__ == "__main__":' in src
    main = src.split('if __name__ == "__main__":', 1)[1]
    assert "mkdtemp(" in main and "cache_selftest_" in main, "自检未使用临时目录"
    assert "INDEX_PATH =" in main, "自检未重定向 INDEX_PATH"
    assert "UPLOAD_DIR" in main and "THUMB_DIR" in main, "自检未重定向上传/缩略图目录"
