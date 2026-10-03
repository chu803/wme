# -*- coding: utf-8 -*-
"""
==============================================================================
 测试文件 : tests/test_image_history_record.py
 测试目标 : 阶段 45 —— 图片处理历史记录闭环（单图 / 自动页接入历史）。

 覆盖点：
   1. services.cache_service.add_success 新增 original_arr 参数：
        · 内存原图数组可落盘到 UPLOAD_DIR 并登记历史；
        · file_hash 未传时自动从落盘原图计算；
        · 缩略图由结果图生成并落地；
        · original_arr 优先于 original_path；
        · 关闭缓存时返回 None（零磁盘写入）；
        · 既有的 original_path 调用方式保持兼容。
   2. ui.tab_single.process_single / ui.tab_auto.process_auto_repair 在保存成功后
      调用 _CACHE.add_success(..., original_arr=image)，把内存原图登记进历史；
      record=False 时（历史页「重新处理」）不重复登记。

 约束：
   · 零 torch 依赖，可在无 GPU 沙箱内运行。
   · 全部落盘均指向临时目录，不污染项目真实缓存。
==============================================================================
"""

from __future__ import annotations

import numpy as np
import pytest

import services.cache_service as cs
import services.file_service as fs


# ------------------------------------------------------------------------------
# 服务层：add_success 的 original_arr 能力
# ------------------------------------------------------------------------------

def _patch_cache_dirs(monkeypatch, tmp_path):
    """把缓存相关的目录与索引路径重定向到临时目录，保证测试零污染。"""
    up = tmp_path / "uploads"
    th = tmp_path / "thumbs"
    hi = tmp_path / "history"
    for d in (up, th, hi):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cs, "UPLOAD_DIR", up)
    monkeypatch.setattr(cs, "THUMB_DIR", th)
    monkeypatch.setattr(cs, "HISTORY_DIR", hi)
    monkeypatch.setattr(cs, "INDEX_PATH", hi / "history_index.json")
    # save_thumbnail 内部写 file_service.THUMB_DIR，一并重定向。
    monkeypatch.setattr(fs, "THUMB_DIR", th)


def test_add_success_with_original_arr(tmp_path, monkeypatch):
    """内存原图数组应落盘到 UPLOAD_DIR 并登记为 image 记录。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=True)

    result = np.zeros((8, 8, 3), dtype=np.uint8)
    orig = np.full((8, 8, 3), 255, dtype=np.uint8)
    rec = cache.add_success(
        None, result,
        original_name="single_123456",
        mode="manual", engine="lama",
        params="引擎=lama",
        original_arr=orig,
    )

    assert rec is not None
    assert rec.kind == "image"
    assert rec.mode == "manual"
    assert rec.engine == "lama"
    # 原图已落盘且位于 UPLOAD_DIR。
    assert rec.original_path and (tmp_path / "uploads").__str__() in rec.original_path
    from pathlib import Path
    assert Path(rec.original_path).exists()
    # 缩略图已生成。
    assert rec.thumb_path and Path(rec.thumb_path).exists()
    # file_hash 自动计算。
    assert rec.file_hash


def test_add_success_original_arr_preferred(tmp_path, monkeypatch):
    """original_arr 与 original_path 同时给定时，应以数组落盘为准。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=True)

    # 伪造一个 original_path 指向项目外文件（不应被 copy）。
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not an image")
    orig = np.zeros((4, 4, 3), dtype=np.uint8)

    rec = cache.add_success(
        str(outside), np.zeros((4, 4, 3), np.uint8),
        original_name="pref", original_arr=orig,
    )
    assert rec is not None
    # 落盘文件来自数组（PNG，大小远大于占位字节串）。
    from pathlib import Path
    assert Path(rec.original_path).stat().st_size > len(b"not an image")


def test_add_success_file_hash_passthrough(tmp_path, monkeypatch):
    """显式传入 file_hash 时应原样保留，不再重新计算。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=True)
    rec = cache.add_success(
        None, np.zeros((4, 4, 3), np.uint8),
        original_name="h", original_arr=np.zeros((4, 4, 3), np.uint8),
        file_hash="deadbeef",
    )
    assert rec.file_hash == "deadbeef"


def test_add_success_disabled_returns_none(tmp_path, monkeypatch):
    """缓存关闭时 add_success 直接返回 None，不写磁盘。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=False)
    rec = cache.add_success(
        None, np.zeros((4, 4, 3), np.uint8),
        original_name="x", original_arr=np.zeros((4, 4, 3), np.uint8),
    )
    assert rec is None


def test_add_success_backward_compat_path(tmp_path, monkeypatch):
    """仅传 original_path（不传 original_arr）的既有调用方式仍可用。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=True)
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG placeholder")
    # 真实 PNG 头并非必需：save_upload 只做文件复制。
    rec = cache.add_success(
        str(src), np.zeros((4, 4, 3), np.uint8),
        original_name="src.png",
    )
    assert rec is not None
    from pathlib import Path
    assert Path(rec.original_path).exists()
    assert rec.kind == "image"


# ------------------------------------------------------------------------------
# 界面层：process_single / process_auto_repair 的历史登记接线
# ------------------------------------------------------------------------------

def _build_fake_cache():
    """记录 add_success / update_result_path 调用的假缓存。"""
    calls = {"success": [], "update": []}

    class FakeCache:
        def add_success(self, original_path, result_arr, *, original_name,
                        mode="auto", engine="auto", params="",
                        file_hash=None, original_arr=None):
            calls["success"].append({
                "original_path": original_path,
                "original_name": original_name,
                "mode": mode,
                "engine": engine,
                "original_arr": original_arr,
                "params": params,
            })
            rec = type("R", (), {"original_path": None,
                                 "result_path": None,
                                 "thumb_path": None,
                                 "original_name": original_name})()
            return rec

        def update_result_path(self, record, result_path):
            calls["update"].append((record, result_path))

    return FakeCache(), calls


def _patch_tab_processing(tab_mod, monkeypatch, tmp_path):
    """把修复链路上的外部依赖全部桩掉，让 process_* 走到历史登记分支。"""
    img = np.zeros((16, 16, 3), dtype=np.uint8)

    def fake_inpaint(image, mask, **kwargs):
        return np.zeros_like(image)

    def fake_build_mask(*a, **k):
        return np.ones((img.shape[0], img.shape[1]), dtype=np.uint8) * 255

    def fake_pp(*a, **k):
        # pp_postprocess 可能依赖 torch；此处直接回传，隔离实现细节。
        return a[0]

    def fake_save(*a, **k):
        p = tmp_path / "out.png"
        p.write_text("out")
        return str(p)

    monkeypatch.setattr(tab_mod, "inpaint", fake_inpaint)
    monkeypatch.setattr(tab_mod, "build_mask_from_editor", fake_build_mask)
    monkeypatch.setattr(tab_mod, "pp_postprocess", fake_pp)
    monkeypatch.setattr(tab_mod, "save_result", fake_save)
    return img


def test_process_single_records_history(tmp_path, monkeypatch):
    """单图处理成功后应调用 add_success 并传入 original_arr。"""
    import ui.tab_single as ts
    img = _patch_tab_processing(ts, monkeypatch, tmp_path)
    fake, calls = _build_fake_cache()
    monkeypatch.setattr(ts, "_CACHE", fake)

    editor_value = {"background": img}
    ts.process_single(
        editor_value, "lama", 1.0, 0, 0, 10, 0,
        record=True,
    )

    assert len(calls["success"]) == 1
    s = calls["success"][0]
    assert s["original_arr"] is not None
    assert s["mode"] == "manual"
    assert s["original_name"].startswith("single_")
    assert len(calls["update"]) == 1


def test_process_auto_repair_records_history(tmp_path, monkeypatch):
    """自动页处理成功后应调用 add_success 并传入 original_arr。"""
    import ui.tab_auto as ta
    img = _patch_tab_processing(ta, monkeypatch, tmp_path)
    fake, calls = _build_fake_cache()
    monkeypatch.setattr(ta, "_CACHE", fake)

    editor_value = {"background": img}
    ta.process_auto_repair(
        editor_value, "lama", 1.0, 0, 0, 10, 0,
        record=True,
    )

    assert len(calls["success"]) == 1
    s = calls["success"][0]
    assert s["original_arr"] is not None
    assert s["mode"] == "auto"
    assert s["original_name"].startswith("auto_")
    assert len(calls["update"]) == 1


def test_process_auto_repair_no_record_when_false(tmp_path, monkeypatch):
    """record=False（历史页重新处理）时不应新增历史记录。"""
    import ui.tab_auto as ta
    img = _patch_tab_processing(ta, monkeypatch, tmp_path)
    fake, calls = _build_fake_cache()
    monkeypatch.setattr(ta, "_CACHE", fake)

    editor_value = {"background": img}
    ta.process_auto_repair(
        editor_value, "lama", 1.0, 0, 0, 10, 0,
        record=False,
    )

    assert calls["success"] == []
    assert calls["update"] == []
