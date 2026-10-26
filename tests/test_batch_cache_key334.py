# -*- coding: utf-8 -*-
"""阶段 334 · 批量「命中缓存」必须认参数，不认参数就是返回旧图。

钉三件事（都是读代码确认过的真实缺陷，不是假想）：

1. `services/cache_service.find_by_hash` 此前**只比文件哈希** ⇒ 同一张图换引擎 / 改灵敏度 /
   换掩码都会命中上一次的成品，还在日志里记成「✅ 命中缓存，已复用历史结果」。
   现在必须带 `cache_sig`；旧记录（签名空）**不命中**。
2. `ui/tab_batch._process_one` 的签名由**自己的入参快照**生成 ⇒ 新增一个会影响输出的参数
   自动进入键，不需要有人记得维护清单。
3. `ui/tab_batch` 的两处形态缺陷：输入白名单误用 `_MASK_EXTS`（`.tif/.tiff/.jpe/.jfif` 被静默丢），
   以及把 `gr.File(file_count="directory")` 返回的**文件列表**当目录用（手动掩码批量每张必失败）。
"""
from __future__ import annotations

import os

import numpy as np

import services.cache_service as cs
import services.file_service as fs
import ui.tab_batch as tb
from services.queue_worker import Task, TaskStatus


# ------------------------------------------------------------------------------
# 公共夹具：把缓存目录重定向到临时目录（零污染）
# ------------------------------------------------------------------------------

def _patch_cache_dirs(monkeypatch, tmp_path):
    up, th, hi = tmp_path / "uploads", tmp_path / "thumbs", tmp_path / "history"
    for d in (up, th, hi):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cs, "UPLOAD_DIR", up)
    monkeypatch.setattr(cs, "THUMB_DIR", th)
    monkeypatch.setattr(cs, "HISTORY_DIR", hi)
    monkeypatch.setattr(cs, "INDEX_PATH", hi / "history_index.json")
    monkeypatch.setattr(fs, "THUMB_DIR", th)


def _make_record(cache, tmp_path, *, sig="", engine="opencv"):
    """写一条**有 result_path** 的成功记录（`add_success` 只登记，结果路径由上层回填）。"""
    src = tmp_path / f"src_{engine}.png"
    src.write_bytes(b"\x89PNG-fixed-bytes")
    res = np.zeros((8, 8, 3), dtype=np.uint8)
    rec = cache.add_success(str(src), res, original_name="a.png",
                            mode="auto", engine=engine, params=f"引擎={engine}",
                            cache_sig=sig)
    assert rec is not None
    out = tmp_path / f"out_{engine}.png"
    out.write_bytes(b"x")
    cache.update_result_path(rec, str(out))
    return rec, src, out


# ------------------------------------------------------------------------------
# ① 服务层：命中键
# ------------------------------------------------------------------------------

def test_find_by_hash_requires_matching_signature(tmp_path, monkeypatch):
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=True)
    rec, src, out = _make_record(cache, tmp_path, sig="SIG-A")

    assert cache.find_by_hash(rec.file_hash, "SIG-A") is not None
    assert cache.find_by_hash(rec.file_hash, "SIG-B") is None, "签名不同必须重算"
    assert cache.find_by_hash(rec.file_hash) is not None, "不传签名保持旧行为"


def test_record_without_signature_never_hits(tmp_path, monkeypatch):
    """旧数据（`cache_sig=""`）不参与命中：宁可重算一次，也不能把旧图当新结果。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=True)
    rec, _src, _out = _make_record(cache, tmp_path, sig="")
    assert rec.cache_sig == ""
    assert cache.find_by_hash(rec.file_hash, "SIG-NEW") is None


def test_signature_roundtrips_through_index(tmp_path, monkeypatch):
    """签名必须能持久化再读回（否则重启后全部记录退化成「不命中」以外的错判）。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    cache = cs.CacheService(enabled=True)
    rec, _src, _out = _make_record(cache, tmp_path, sig="SIG-RT")
    again = cs.CacheService(enabled=True)
    hit = again.find_by_hash(rec.file_hash, "SIG-RT")
    assert hit is not None and hit.cache_sig == "SIG-RT"


# ------------------------------------------------------------------------------
# ② 批量页：签名来自入参快照
# ------------------------------------------------------------------------------

def test_cache_signature_changes_with_any_output_param():
    base = dict(mode="auto", engine="opencv", sensitivity=0.5, strength=3,
                denoise=0, preset="general")
    s0 = tb.cache_signature_for(base)
    assert tb.cache_signature_for(dict(base)) == s0, "同样的参数必须给同样的键（否则缓存永失效）"
    for key, val in (("engine", "lama"), ("sensitivity", 0.9), ("strength", 8),
                     ("preset", "ai_logo"), ("mode", "manual")):
        assert tb.cache_signature_for({**base, key: val}) != s0, f"改 {key} 必须换键"


def _stub_pipeline(monkeypatch, tmp_path, calls):
    """把 `_process_one` 的重活全换成桩，只留下「缓存判定」这一段是真的。"""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    # 掩码走的是「灰度 + Otsu 二值化」：常数图会被 Otsu 判成空掩码 ⇒ 给一张真有分界的图，
    # 否则测不到「命中缓存」这条路（会在更早的 `mask.sum()==0` 处抛错）。
    mask_like = np.zeros((8, 8), dtype=np.uint8)
    mask_like[:4] = 255

    def fake_load(path: str):
        if "masks" in path:
            return np.dstack([mask_like, mask_like, mask_like])
        return img

    monkeypatch.setattr(tb, "validate_image_file", lambda p: object())
    monkeypatch.setattr(tb, "load_image", fake_load)

    class _R:
        def __init__(self):
            self.mask = np.full((8, 8), 255, dtype=np.uint8)
            self.regions = [1]
            self.stats = None

    monkeypatch.setattr(tb, "detect_watermark_mask",
                        lambda image, sensitivity=None, use_ocr=None, preset=None: _R())

    def fake_repair(editor_value, **kw):
        calls.append(kw.get("engine"))
        out = tmp_path / f"result_{len(calls)}.png"
        out.write_bytes(b"r")
        return None, img, "已修复", None, str(out)

    monkeypatch.setattr(tb, "process_auto_repair", fake_repair)


def _run_process_one(tb_mod, monkeypatch, tmp_path, calls, **over):
    _stub_pipeline(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(tb_mod, "_CACHE", cs.CacheService(enabled=True))
    src = tmp_path / "pic.png"
    src.write_bytes(b"same-bytes")
    task = Task("t1", name="pic.png", path=str(src))
    kw = dict(mode="auto", engine="opencv", strength=3, dilate=1, feather=1,
              threshold=128, denoise=0, sensitivity=0.5, mask_folder="", preset="general")
    kw.update(over)
    return tb_mod._process_one(task, lambda *a: None, **kw)


def test_batch_rerun_with_same_params_hits_cache(tmp_path, monkeypatch):
    _patch_cache_dirs(monkeypatch, tmp_path)
    calls: list = []
    first = _run_process_one(tb, monkeypatch, tmp_path, calls)
    assert first["skipped"] is False and len(calls) == 1
    second = _run_process_one(tb, monkeypatch, tmp_path, calls)
    assert second["skipped"] is True, "同参数重跑应当复用成品（缓存的正向价值）"
    assert len(calls) == 1, "命中时不该再修一次"


def test_batch_changing_engine_does_not_reuse_old_result(tmp_path, monkeypatch):
    """**这就是被修掉的那条**：旧实现只比文件哈希 ⇒ 换引擎直接返回上一次的旧图。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    calls: list = []
    first = _run_process_one(tb, monkeypatch, tmp_path, calls, engine="opencv")
    assert first["skipped"] is False
    again = _run_process_one(tb, monkeypatch, tmp_path, calls, engine="lama")
    assert again["skipped"] is False, "换了引擎还复用旧成品 = 把错图交给用户并记成成功"
    assert calls == ["opencv", "lama"]


def test_batch_changing_sensitivity_does_not_reuse_old_result(tmp_path, monkeypatch):
    _patch_cache_dirs(monkeypatch, tmp_path)
    calls: list = []
    _run_process_one(tb, monkeypatch, tmp_path, calls, sensitivity=0.5)
    again = _run_process_one(tb, monkeypatch, tmp_path, calls, sensitivity=0.9)
    assert again["skipped"] is False, "检测灵敏度改变掩码 ⇒ 旧成品不成立"


def test_batch_manual_mask_content_is_part_of_the_key(tmp_path, monkeypatch):
    """手动模式：同一张图配**不同掩码**必须重算（掩码内容进键）。"""
    _patch_cache_dirs(monkeypatch, tmp_path)
    mask_dir = tmp_path / "masks"
    mask_dir.mkdir()
    m1 = mask_dir / "pic.png"
    m1.write_bytes(b"mask-one")
    calls: list = []

    def run(mask_bytes: str):
        m1.write_bytes(mask_bytes)
        idx = tb.mask_candidates(None, [str(m1)])
        return _run_process_one(tb, monkeypatch, tmp_path, calls,
                                mode="manual", mask_folder="", mask_index=idx)

    first = run(b"mask-one")
    assert first["skipped"] is False
    same = run(b"mask-one")
    assert same["skipped"] is True, "掩码没变、参数没变 ⇒ 该命中"
    other = run(b"mask-two")
    assert other["skipped"] is False, "换了掩码还复用旧成品 = 用 A 的涂抹结果交 B 的图"


# ------------------------------------------------------------------------------
# ③ 两处形态缺陷
# ------------------------------------------------------------------------------

def test_batch_input_accepts_tiff_jpe_jfif():
    """输入白名单必须回到 `config.SUPPORTED_INPUT_FORMATS`（此前误用 `_MASK_EXTS`）。"""
    paths = ["a.tif", "b.tiff", "c.jpe", "d.jfif", "e.png", "notes.txt", "f.psd"]
    kept, dropped = tb.select_batch_inputs(paths)
    assert kept == ["a.tif", "b.tiff", "c.jpe", "d.jfif", "e.png"], kept
    assert dropped == ["notes.txt", "f.psd"], dropped


def test_mask_candidates_accepts_gradio_directory_shape(tmp_path):
    """`gr.File(file_count="directory")` 给的是**目录里的文件**，旧代码当目录用 ⇒ 恒失败。"""
    d = tmp_path / "masks"
    d.mkdir()
    (d / "pic1.png").write_bytes(b"1")
    (d / "pic2.PNG").write_bytes(b"2")
    (d / "readme.txt").write_bytes(b"x")
    file_list = [str(d / "pic1.png"), str(d / "pic2.PNG"), str(d / "readme.txt")]

    by_files = tb.mask_candidates(None, file_list)
    assert set(by_files) == {"pic1", "pic2"}, by_files
    assert tb._mask_path_for_index(by_files, "x/pic1.jpg") == str(d / "pic1.png")
    assert tb._mask_path_for_index(by_files, "x/pic3.jpg") is None

    by_dir = tb.mask_candidates(None, [str(d)]), 
    assert set(by_dir[0]) == {"pic1", "pic2"}, "给目录的旧形态必须继续可用"


def test_process_one_signature_snapshot_covers_new_params():
    """签名来自 `_process_one` 自己的 `locals()` 快照 ⇒ 新增入参自动进键（防「记得登记」）。"""
    src = open(tb.__file__, encoding="utf-8").read()
    i = src.index("def _process_one(")
    body = src[i:src.index("def ", i + 10)]
    assert "locals()" in body, "签名不再从入参快照派生 ⇒ 参数清单会退化成手写副本"
    assert "mask_folder" in body and "cache_signature_for" in body
