# -*- coding: utf-8 -*-
"""阶段 342 · 「导出 ZIP」不许在 `exports/` 里留下一地中间产物。

缺陷（两条同类路径）：

* `services/export_service.export_history_as_zip(fmt="WEBP")` 里逐条记录调
  `reencode_image(..., out_path=None)` ⇒ 落到默认目录 `config.EXPORT_DIR`（唯一化命名、永不覆盖），
  打完 ZIP 就没人管它们；
* `ui/tab_settings.on_export_compares` 逐条调 `export_compare(...)`（同样不传 `out_path`）⇒ 同样一地散图。

而 `CacheService.scan_orphans` 的文档**明确不扫 `exports/`**（那是用户成品目录，
`services/cache_service.py:855`）⇒ 设置页的「清理孤儿文件」也收不回它们。
⇒ 用户点一次「导出 ZIP（转 WEBP）」，`exports/` 就多 N 张全尺寸图，且只会一直涨。

阶段 147 的自检注释早就知道这件事（当时的做法是把 `config.EXPORT_DIR` 临时指向临时目录），
但那**只让自检不污染**，真实用户路径一直在污染 —— 兜底路径绕过已经守住的那层（经验 169 同族）。
"""
from __future__ import annotations

import os
import zipfile

import numpy as np
import pytest

import config
import services.export_service as es
from services.cache_service import HistoryRecord


def _exports(tmp_path, monkeypatch):
    d = tmp_path / "exports"
    d.mkdir()
    monkeypatch.setattr(config, "EXPORT_DIR", d)
    return d


def _records(tmp_path, n=3):
    """造 n 条真实存在的结果图记录（reencode 要真读得出来）。"""
    recs = []
    for i in range(n):
        p = tmp_path / f"res_{i}.png"
        # 走本仓自己的保存通道，避免又踩「cv2.imwrite 不吃中文路径」
        from core.image_utils import save_image
        save_image(str(p), np.full((24, 32, 3), 60 + i, dtype=np.uint8), fmt="PNG")
        recs.append(HistoryRecord(
            id=f"r{i}", ts=1.0, original_name=f"pic{i}.png",
            result_path=str(p), original_path=str(p), status="success",
        ))
    return recs


class _StubCache:
    def __init__(self, recs):
        self._recs = recs

    def list_records(self, kind=None, query=None, **kw):
        return list(self._recs)


def test_history_zip_leaves_only_the_archive(tmp_path, monkeypatch):
    exports = _exports(tmp_path, monkeypatch)
    recs = _records(tmp_path)

    zip_path = es.export_history_as_zip(_StubCache(recs), fmt="WEBP")

    assert zip_path and os.path.isfile(zip_path)
    leftovers = sorted(os.listdir(exports))
    assert leftovers == [os.path.basename(zip_path)], (
        f"exports/ 里应当只剩 ZIP 一个文件，实得：{leftovers}")

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    assert len(names) == len(recs), names
    assert all(n.endswith(".webp") for n in names), names


def test_history_zip_without_reformat_still_works(tmp_path, monkeypatch):
    """控制组：不转格式的那条分支不许被改坏（产物仍是 N 个文件的 ZIP）。"""
    _exports(tmp_path, monkeypatch)
    recs = _records(tmp_path)
    zip_path = es.export_history_as_zip(_StubCache(recs))
    with zipfile.ZipFile(zip_path) as z:
        assert len(z.namelist()) == len(recs)
    assert sorted(os.listdir(config.EXPORT_DIR)) == [os.path.basename(zip_path)]


def test_compare_zip_leaves_only_the_archive(tmp_path, monkeypatch):
    """`on_export_compares` 同一条病：对比图是中间产物，不该留在 exports/。"""
    import ui.tab_settings as ts

    exports = _exports(tmp_path, monkeypatch)
    recs = _records(tmp_path, n=2)
    monkeypatch.setattr(ts, "_CACHE", _StubCache(recs))

    msg, zip_path = ts.on_export_compares(gap=8)

    assert zip_path and os.path.isfile(zip_path), msg
    leftovers = sorted(os.listdir(exports))
    assert leftovers == [os.path.basename(zip_path)], (
        f"点一次「导出对比图 ZIP」不该在 exports/ 留下散图，实得：{leftovers}")
    with zipfile.ZipFile(zip_path) as z:
        assert len(z.namelist()) == 2
