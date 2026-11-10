# -*- coding: utf-8 -*-
"""阶段 405：检测区域勾选排除（替代有缺陷的「预填图层 + 橡皮」通路）。

外部验收（2026-11-10）F5 实测：在预填红色掩码层上用橡皮涂抹误报区，
改动比例 1.1%→2.4%、误报区差异 8.76%→22.91%（越擦越多）。受控探针确认
Gradio 6.31 的 ImageEditor 编辑预填图层时会按视口坐标重排内容 ⇒ 语料层修复
不可行。产品改为：编辑框只收用户笔迹；检测误报用「检测区域」取消勾选。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import ui.tab_auto as ta  # noqa: E402

H, W = 40, 60
det = np.zeros((H, W), np.uint8)
det[5:15, 5:20] = 255      # 区域 A
det[20:30, 35:50] = 255    # 区域 B
stroke = np.zeros((H, W), np.uint8)
stroke[32:38, 2:10] = 255

ITEMS = [
    {"label": "A", "x": 5, "y": 5, "w": 15, "h": 10, "kind": "corner_logo"},
    {"label": "B", "x": 35, "y": 20, "w": 15, "h": 10, "kind": "translucent"},
]

CAPTURED: dict = {}


class _FakeCache:
    def add_success(self, *a, **k):
        return object()

    def update_result_path(self, *a, **k):
        pass


class _Res:
    def __init__(self, mask):
        self.mask = mask
        self.regions = []
        self.stats = None
        self.used_ocr = False


@pytest.fixture
def env(monkeypatch):
    def fake_inpaint(img, mask, **k):
        CAPTURED["mask"] = np.array(mask, copy=True)
        CAPTURED["img"] = np.array(img, copy=True)
        return img.copy()

    monkeypatch.setattr(ta, "inpaint", fake_inpaint)
    monkeypatch.setattr(ta, "pp_postprocess", lambda raw, **k: raw)
    monkeypatch.setattr(ta, "evaluate_improvement", lambda *a, **k: {"detail": "n/a"})
    monkeypatch.setattr(ta, "save_result", lambda arr, name, **k: f"/fake/{name}.png")
    monkeypatch.setattr(ta, "_CACHE", _FakeCache())
    monkeypatch.setattr(config, "UI_COMPARE_VIEW", False)
    CAPTURED.clear()


def _editor(*layers):
    return {"background": np.full((H, W, 3), 100, np.uint8),
            "layers": [ta._mask_to_rgba_layer(m) for m in layers],
            "composite": None}


def _repair(editor, det_mask, selected, regions, upload=None):
    return ta.process_auto_repair(
        editor, "opencv", 0.5, 0, 0, 128, 0,
        enable_postprocess=False, postprocess_eval=False,
        det_mask=det_mask, mask_mode="merge",
        region_select=selected, det_regions=regions,
        upload=upload,
        record=False,
    )


# --------------------------------------------------------------------------- #
# 1. 掩码裁剪单元
# --------------------------------------------------------------------------- #
def test_exclude_only_unselected_boxes():
    out = ta.exclude_regions_from_mask(det, ITEMS, ["A"])
    assert (out[5:15, 5:20] == 255).all(), "勾选的区域必须保留"
    assert (out[20:30, 35:50] == 0).all(), "取消勾选的区域必须清掉"
    assert (det[20:30, 35:50] == 255).all(), "不得修改入参掩码"


def test_exclude_pad_expands_box():
    m = np.zeros((H, W), np.uint8)
    m[4:16, 4:21] = 255          # 比 bbox 外扩 1px 的像素
    out = ta.exclude_regions_from_mask(m, [ITEMS[0]], [], pad=4)
    assert out.sum() == 0, "取消勾选时 bbox 外扩 pad 内的检测像素也要清掉"


def test_exclude_none_selected_means_keep_all():
    out = ta.exclude_regions_from_mask(det, ITEMS, None)
    assert (out == det).all(), "None 视为全选（防御未知输入）"


# --------------------------------------------------------------------------- #
# 2. 修复入口：裁剪后并集 / 全取消
# --------------------------------------------------------------------------- #
def test_merge_repair_uses_checked_regions_only(env):
    _repair(_editor(), det, ["A"], ITEMS)
    m = CAPTURED.get("mask")
    assert m is not None
    assert (m[5:15, 5:20] == 255).all(), "选中的区域 A 要修"
    assert (m[20:30, 35:50] == 0).all(), "未选中的区域 B 不修"


def test_merge_repair_keeps_user_strokes_with_det(env):
    _repair(_editor(stroke), det, ["A"], ITEMS)
    m = CAPTURED.get("mask")
    assert (m[32:38, 2:10] == 255).all(), "画笔补涂必须保留"
    assert (m[5:15, 5:20] == 255).all(), "选中的检测区域必须保留"
    assert (m[20:30, 35:50] == 0).all(), "取消勾选的区域必须清掉"


def test_merge_repair_all_excluded_reports_and_skips_engine(env):
    r = _repair(_editor(), det, [], ITEMS)
    assert "mask" not in CAPTURED, "全部取消 ⇒ 不调用修复引擎"
    assert r[1] is None and r[4] is None
    assert "取消" in r[3] and "区域" in r[3]


def test_repair_prefers_original_upload_over_editor_roundtrip(env):
    """编辑框背景会被浏览器整图重编码 ⇒ 修复底图必须优先用上传原图。"""
    editor = _editor()
    editor["background"] = np.full((H, W, 3), 100, np.uint8)
    original = np.full((H, W, 3), 120, np.uint8)
    _repair(editor, det, None, [], upload=original)
    assert (CAPTURED["img"] == 120).all(), "inpaint 必须拿到上传原图，而不是编辑框背景"


def test_repair_accepts_upload_when_editor_background_missing(env):
    """编辑框没回传背景（空画布）时，上传原图仍应可修。"""
    r = _repair({"background": None, "layers": [], "composite": None},
                det, None, [], upload=np.full((H, W, 3), 120, np.uint8))
    assert len(r) == 5 and r[1] is not None
    assert (CAPTURED["img"] == 120).all()


# --------------------------------------------------------------------------- #
# 3. 检测回调：不再预填图层 + 返回区域状态
# --------------------------------------------------------------------------- #
def test_detect_returns_empty_editor_and_regions(monkeypatch):
    region = type("Region", (), {"kind": "corner_logo", "x": 5, "y": 5, "w": 15, "h": 10})()

    class _R:
        def __init__(self):
            self.mask = det.copy()
            self.stats = None
            self.used_ocr = False
            self.regions = [region]

    monkeypatch.setattr(ta, "detect_watermark_mask", lambda img, **k: _R())
    monkeypatch.setattr(ta, "visualize_detection", lambda img, res: img)
    monkeypatch.setattr(ta, "is_ocr_available", lambda: False)

    upload = np.full((H, W, 3), 100, np.uint8)
    preview, editor, status, stats, mask, regions, update = ta.process_auto_detect(
        upload, 0.5, True, True, True, "general")
    assert editor["layers"] == [], "阶段 405：编辑框不得预填掩码图层"
    assert editor["background"] is upload
    assert len(regions) == 1 and regions[0]["label"].startswith("区域 1")
    assert mask is not None
    assert update is not None
