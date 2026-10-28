# -*- coding: utf-8 -*-
"""阶段 354 · 两处「收了参数却没用」/「索引与展示不同源」的修复守卫。

1. `/api/detect`：用法页与调用点注释都写着「preset/sensitivity 可选传递」，
   但调用点恒传 `None` ⇒ 客户端钉了预设却拿到默认检测，**没有任何提示**。
2. 历史画廊：索引表 `_HISTORY_ID_MAP` 原本按**全部**记录建，而画廊会跳过
   「缩略图与结果都不存在」的记录 ⇒ **格子与索引错位** ⇒ 点第 N 格选中的是另一条记录，
   「删除所选」删的也是那一条。
"""
from __future__ import annotations

import base64
import inspect
import pathlib

import server
import ui.tab_history as th


# ── ① /api/detect ───────────────────────────────────────────────────────────
def test_detect_helper_forwards_preset_and_sensitivity(monkeypatch):
    seen = {}

    def fake(raw, preset=None, sensitivity=None, **kw):
        seen["preset"] = preset
        seen["sensitivity"] = sensitivity
        return b"mask-bytes", {"regions": []}

    monkeypatch.setattr(server.api, "detect_watermark_bytes", fake)
    server._detect_one_b64(base64.b64encode(b"x").decode(), "ai_logo", 0.8)

    assert seen["preset"] == "ai_logo", seen
    assert seen["sensitivity"] == 0.8, seen


def test_detect_handler_reads_body_params():
    """处理器必须把 body 里的 preset/sensitivity 传下去（源码级，防退回 None）。"""
    src = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    assert 'body.get("preset")' in src, "/api/detect 处理器没有读取 body 里的 preset"
    assert 'body.get("sensitivity")' in src, "/api/detect 处理器没有读取 body 里的 sensitivity"


# ── ② 历史画廊索引 ──────────────────────────────────────────────────────────
class _Rec:
    def __init__(self, rid, thumb, result, name="pic.png"):
        self.id = rid
        self.thumb_path = thumb
        self.result_path = result
        self.original_name = name
        self.original_path = None
        self.mode = "auto"
        self.engine = "opencv"
        self.kind = "image"


def test_history_index_map_has_the_same_length_as_the_gallery(monkeypatch, tmp_path):
    good = tmp_path / "good.png"
    good.write_bytes(b"x")

    rec_ok = _Rec("r1", str(good), str(good))
    rec_gone = _Rec("r2", str(tmp_path / "missing.png"), str(tmp_path / "missing2.png"))

    class _StubCache:
        def list_records(self, *a, **kw):
            return [rec_ok, rec_gone]

    monkeypatch.setattr(th, "_CACHE", _StubCache())
    gallery, _info = th.load_gallery()

    assert len(gallery) == 1, gallery
    assert len(th._HISTORY_ID_MAP) == len(gallery), (
        f"索引表 {len(th._HISTORY_ID_MAP)} 条 ≠ 画廊 {len(gallery)} 格 ⇒ 点第 N 格会选中另一条记录")
    assert th._HISTORY_ID_MAP[0][0] == "r1", th._HISTORY_ID_MAP
