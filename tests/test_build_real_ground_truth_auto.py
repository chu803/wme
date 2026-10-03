# -*- coding: utf-8 -*-
"""
Stage 157 守卫：`scripts/build_real_ground_truth.cmd_auto` 的「写真值契约」。

`cmd_auto` 是唯一会**写入** `benchmark_real/ground_truth.json` 的入口（其余两个子命令
`list`/`validate` 只读或复用 `structural_problems`）。它把检测器输出直接落成真值候选，
但此前不校验这些候选是否经得起 `audit_truth`（`real_asset_preflight`）的校验 ⇒ 两类
**静默真值损坏**：

  ① 检测器返回 0 区域时仍写 `watermarks: []` 空条目——`audit_truth` 报「空标注会被读成
     『没有水印』」错误，且空条目污染 `have_ids` 阻断后续自动重标、`cmd_list` 还把它算成
     「已标注」掩盖真实未标注。
  ② 越界 / 非正 bbox 原样写入——`audit_truth` 的「bbox 非负 / 在图内」判据会判错，但
     `auto` 打印「预填完成」让用户误以为成功。

本文件钉死修复后的契约：
  · 任何候选 bbox 越界（x<0 / y<0 / x+w>W / y+h>H）或非正（w<=0 / h<=0）→ 跳过，不写入；
  · 一张图若没有任何有效候选 → **不写空条目**（直接跳过），不污染 `have_ids`；
  · 已有真值（image_id 在 have_ids）→ 跳过；缺 id / 文件缺失 → 跳过或记 failed。

测试不依赖真实检测器：用 `unittest.mock.patch` 替换 `core.detector.detect_watermark_mask`，
并用独立 tmp 目录隔离 `BENCH_DIR`/`MANIFEST_PATH`/`TRUTH_PATH`/`IMAGES_DIR`，绝不碰真实数据集。
"""
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import numpy as np  # noqa: F401  (cmd_auto 内部会用；保留以便真实 pytest 环境一致)
from PIL import Image

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import build_real_ground_truth as m  # noqa: E402


class FakeRegion:
    """仿 `core.detector.DetectionRegion` 的最小子集（cmd_auto 只用这五个字段）。"""
    def __init__(self, x, y, w, h, kind="corner_logo"):
        self.x, self.y, self.w, self.h, self.kind = x, y, w, h, kind


class FakeResult:
    def __init__(self, regions):
        self.regions = regions


def _args(preset="general"):
    return SimpleNamespace(preset=preset)


def _make_image(path, w=64, h=48):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (w, h), (120, 120, 120)).save(path)


def _setup(tmp, images):
    """把模块级路径常量重定向到 tmp，并写出 manifest + 空 truth + 图片文件。"""
    m.BENCH_DIR = tmp
    m.IMAGES_DIR = os.path.join(tmp, "images")
    m.MANIFEST_PATH = os.path.join(tmp, "manifest.json")
    m.TRUTH_PATH = os.path.join(tmp, "ground_truth.json")
    os.makedirs(m.IMAGES_DIR, exist_ok=True)
    manifest = {"version": 1, "images": images}
    with open(m.MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(m.TRUTH_PATH, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "entries": []}, f, ensure_ascii=False)
    for im in images:
        if im.get("id") and not im.get("_nodisk"):
            # cmd_auto 的 _abs_image_path 把 manifest.path 相对 BENCH_DIR 解析，
            # 故真实图存放于 BENCH_DIR/images/<name>、manifest.path 应为 "images/<name>"。
            # 带 "_nodisk": True 的条目不落盘，用于模拟「清单声明但文件缺失」。
            _make_image(os.path.join(m.BENCH_DIR, im["path"]))
    return tmp


def _run(images, fake_result, preset="general"):
    tmp = tempfile.mkdtemp()
    try:
        _setup(tmp, images)
        with mock.patch("core.detector.detect_watermark_mask", return_value=fake_result):
            rc = m.cmd_auto(_args(preset))
        with open(m.TRUTH_PATH, encoding="utf-8") as f:
            truth = json.load(f)
        return rc, truth
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_auto_writes_valid_bbox():
    rc, t = _run([{"id": "img1", "path": "img1.png", "filename": "img1.png"}],
                 FakeResult([FakeRegion(10, 10, 20, 20, "corner_logo")]))
    assert rc == 0
    assert len(t["entries"]) == 1, t
    e = t["entries"][0]
    assert e["image_id"] == "img1"
    assert e["watermarks"][0]["bbox"] == [10, 10, 20, 20], e["watermarks"]
    assert e["watermarks"][0]["style"] == "corner_logo"
    assert e["labeled_by"] == "auto:general"


def test_auto_skips_already_labeled():
    tmp = tempfile.mkdtemp()
    try:
        _setup(tmp, [{"id": "img1", "path": "img1.png", "filename": "img1.png"}])
        # 预先手动标注 img1（模拟已存在真值）
        with open(m.TRUTH_PATH, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "entries": [{
                "image_id": "img1",
                "watermarks": [{"bbox": [1, 1, 2, 2], "style": "unknown", "note": ""}],
                "labeled_at": "x", "labeled_by": "manual:test"}]}, f)
        with mock.patch("core.detector.detect_watermark_mask",
                        return_value=FakeResult([FakeRegion(10, 10, 20, 20)])):
            rc = m.cmd_auto(_args())
        with open(m.TRUTH_PATH, encoding="utf-8") as f:
            t = json.load(f)
        assert rc == 0
        assert len(t["entries"]) == 1, t
        assert t["entries"][0]["labeled_by"] == "manual:test"  # 未被 auto 覆盖
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_auto_empty_regions_no_entry():
    """缺陷修复：检测器返回 0 区域时**不得**写空 watermarks 条目。"""
    rc, t = _run([{"id": "img1", "path": "img1.png", "filename": "img1.png"}],
                 FakeResult([]))
    assert rc == 0
    assert len(t["entries"]) == 0, t


def test_auto_skips_out_of_bounds_bbox():
    # 图片 64x48；region x+w=70 > 64 ⇒ 越界，应跳过（不写条目）
    rc, t = _run([{"id": "img1", "path": "img1.png", "filename": "img1.png"}],
                 FakeResult([FakeRegion(50, 10, 20, 20)]))
    assert rc == 0
    assert len(t["entries"]) == 0, t


def test_auto_skips_nonpositive_bbox():
    for bad in (FakeRegion(10, 10, 0, 20),    # w=0
                FakeRegion(10, 10, 20, 0),    # h=0
                FakeRegion(-5, 10, 20, 20),   # x<0
                FakeRegion(10, -3, 20, 20)):  # y<0
        rc, t = _run([{"id": "img1", "path": "img1.png", "filename": "img1.png"}],
                     FakeResult([bad]))
        assert rc == 0
        assert len(t["entries"]) == 0, (bad, t)


def test_auto_keeps_valid_when_mixed():
    # 一个越界 + 一个有效 ⇒ 只保留有效的那条
    rc, t = _run([{"id": "img1", "path": "img1.png", "filename": "img1.png"}],
                 FakeResult([FakeRegion(50, 10, 20, 20),        # 越界
                             FakeRegion(5, 5, 10, 10, "edge_text")]))  # 有效
    assert rc == 0
    assert len(t["entries"]) == 1, t
    wm = t["entries"][0]["watermarks"]
    assert len(wm) == 1, wm
    assert wm[0]["bbox"] == [5, 5, 10, 10], wm
    assert wm[0]["style"] == "edge_text"


def test_auto_handles_missing_file():
    # manifest 声明的图片文件不存在 ⇒ failed，不写条目，返回 0
    tmp = tempfile.mkdtemp()
    try:
        _setup(tmp, [{"id": "img1", "path": "images/missing.png",
                      "filename": "missing.png", "_nodisk": True}])
        with mock.patch("core.detector.detect_watermark_mask",
                        return_value=FakeResult([FakeRegion(10, 10, 20, 20)])):
            rc = m.cmd_auto(_args())
        with open(m.TRUTH_PATH, encoding="utf-8") as f:
            t = json.load(f)
        assert rc == 0
        assert len(t["entries"]) == 0, t
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_auto_skips_missing_id():
    # manifest 条目缺 id ⇒ 跳过，不写条目
    rc, t = _run([{"path": "img1.png", "filename": "img1.png"}],
                 FakeResult([FakeRegion(10, 10, 20, 20)]))
    assert rc == 0
    assert len(t["entries"]) == 0, t
