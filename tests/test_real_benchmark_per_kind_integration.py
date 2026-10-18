# -*- coding: utf-8 -*-
"""P1 per-kind 管线集成守卫（合成夹具，绝不污染 benchmark_real）。

阶段 P1：真实图门禁新增 per-kind 召回/精确率视角（commit cd88a53）。本文件用合成夹具
证明**整条链路**（manifest → ground_truth style → evaluate_one → _aggregate → per_kind）
在多类水印数据集上正确拆分，且 _match_regions 的几何匹配语义（按 bbox IoU，与 kind 无关）
被正确传导到 recall_by_kind / precision_by_kind。

设计要点
--------
- 全部落在 pytest tmp_path，绝不触碰真实 benchmark_real/ground_truth.json；
- monkeypatch `core.detector.detect_watermark_mask` 为受控桩（完美检测 / 漏检 / 错类 FP），
  使断言确定性、不依赖真实检测器对合成图的行为；
- 因此本测试是**管线集成 + per-kind 聚合正确性**守卫，而非检测器能力测试。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

import scripts.run_real_benchmark as rrb


class _FakeRegion:
    """仿 core.detector 的 region：evaluate_one 只读 x/y/w/h/kind。"""

    def __init__(self, x: int, y: int, w: int, h: int, kind: str) -> None:
        self.x, self.y, self.w, self.h, self.kind = x, y, w, h, kind


class _FakeResult:
    def __init__(self, regions, shape):
        self.regions = regions
        self.mask = np.zeros(shape[:2], dtype=np.uint8)


def _make_image(path, color=(128, 128, 128)):
    from PIL import Image

    Image.new("RGB", (64, 48), color).save(path)


def test_per_kind_end_to_end_multiclass(tmp_path, monkeypatch):
    # 5 张图，覆盖多种类 + 三种检测情形：完美 / 漏检 / 错类(FP)。
    #   img_corner      corner_logo      完美检测
    #   img_grid        grid_watermark   完美检测
    #   img_grad        gradient_watermark 完美检测
    #   img_miss        grid_watermark   检测器返回空（漏检）
    #   img_wrongkind   translucent      检测器吐一个不重叠的 unknown 区域（FP + 该 truth 漏检）
    ids = ["img_corner", "img_grid", "img_grad", "img_miss", "img_wrongkind"]
    style_of = {
        "img_corner": "corner_logo",
        "img_grid": "grid_watermark",
        "img_grad": "gradient_watermark",
        "img_miss": "grid_watermark",
        "img_wrongkind": "translucent",
    }
    images_dir = tmp_path / "images"
    images_dir.mkdir()

    truth_entries = []
    fake_results = []  # 与 run_all 调用顺序一致（无 gap 图，按 manifest 顺序）
    for iid in ids:
        img = images_dir / f"{iid}.png"
        _make_image(img)
        style = style_of[iid]
        truth_entries.append({
            "image_id": iid,
            "watermarks": [{"bbox": [4, 4, 20, 10], "style": style}],
            "labeled_at": "t", "labeled_by": "manual",
        })
        if iid == "img_miss":
            fake_results.append(_FakeResult([], (48, 64)))  # 漏检
        elif iid == "img_wrongkind":
            # 不重叠的 unknown 区域：既不与 translucent truth 匹配（漏检），自身也是 FP
            fake_results.append(_FakeResult(
                [_FakeRegion(40, 30, 20, 10, "unknown")], (48, 64)))
        else:
            fake_results.append(_FakeResult(
                [_FakeRegion(4, 4, 20, 10, style)], (48, 64)))  # 完美检测（与 truth bbox 同）

    manifest = {"version": 1, "images": [
        {"id": iid, "path": str(images_dir / f"{iid}.png"), "ai_source": "other"}
        for iid in ids
    ]}
    truth = {"version": 1, "entries": truth_entries}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")

    # evaluate_one 内部 `from core.detector import detect_watermark_mask`，
    # 故桩须打在 core.detector 上才能被命中。
    import core.detector as cd

    calls = {"i": 0}

    def fake_detect(image_rgb, preset=None, use_ocr=False):
        r = fake_results[calls["i"]]
        calls["i"] += 1
        return r

    monkeypatch.setattr(cd, "detect_watermark_mask", fake_detect)

    out = rrb.run_all(["general"], manifest, truth)
    pk = out["per_kind"]["general"]

    # n_images = 5（无 gap 图）
    assert pk["n_images"] == 5

    # recall_by_kind（按 truth style，几何匹配语义）
    assert pk["recall_by_kind"]["corner_logo"]["recall_micro"] == 1.0      # 1/1
    assert pk["recall_by_kind"]["grid_watermark"]["recall_micro"] == 0.5   # 1/2（img_miss 漏检）
    assert pk["recall_by_kind"]["gradient_watermark"]["recall_micro"] == 1.0  # 1/1
    assert pk["recall_by_kind"]["translucent"]["recall_micro"] == 0.0      # 错类区不重叠→漏检

    # precision_by_kind（按预测 kind；unknown 区是 FP，精确率 0）
    assert pk["precision_by_kind"]["corner_logo"]["precision_micro"] == 1.0
    assert pk["precision_by_kind"]["grid_watermark"]["precision_micro"] == 1.0
    assert pk["precision_by_kind"]["gradient_watermark"]["precision_micro"] == 1.0
    assert pk["precision_by_kind"]["unknown"]["precision_micro"] == 0.0

    # 内部一致性：matched <= total
    for tbl in (pk["recall_by_kind"], pk["precision_by_kind"]):
        for v in tbl.values():
            assert v["matched"] <= v["total"]


def test_per_kind_skips_gap_images(tmp_path, monkeypatch):
    # 已知缺口图（config.WATERMARK_KNOWN_GAP_IMAGES 口径）须**不参与 per_kind 聚合**。
    import config as cfg

    gap_id = "gap_a1"
    ids = ["img_ok", gap_id]
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    for iid in ids:
        _make_image(images_dir / f"{iid}.png")

    truth_entries = [
        {"image_id": "img_ok", "watermarks": [
            {"bbox": [4, 4, 20, 10], "style": "corner_logo"}], "labeled_at": "t", "labeled_by": "manual"},
        {"image_id": gap_id, "watermarks": [
            {"bbox": [4, 4, 20, 10], "style": "corner_logo"}], "labeled_at": "t", "labeled_by": "manual"},
    ]
    manifest = {"version": 1, "images": [
        {"id": iid, "path": str(images_dir / f"{iid}.png"), "ai_source": "other"}
        for iid in ids
    ]}
    truth = {"version": 1, "entries": truth_entries}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")

    import core.detector as cd

    def fake_detect(image_rgb, preset=None, use_ocr=False):
        # 两张图都「完美检测」
        return _FakeResult([_FakeRegion(4, 4, 20, 10, "corner_logo")], (48, 64))

    monkeypatch.setattr(cd, "detect_watermark_mask", fake_detect)

    orig_gap = getattr(cfg, "WATERMARK_KNOWN_GAP_IMAGES", ())
    monkeypatch.setattr(cfg, "WATERMARK_KNOWN_GAP_IMAGES", (gap_id,))

    out = rrb.run_all(["general"], manifest, truth)
    pk = out["per_kind"]["general"]
    # 缺口图被排除：只有 img_ok 参与 → corner_logo 召回 1/1（不是 2/2）
    assert pk["n_images"] == 1
    assert pk["recall_by_kind"]["corner_logo"]["total"] == 1
    assert pk["recall_by_kind"]["corner_logo"]["recall_micro"] == 1.0
    # 缺口图仍在 per_image 里（独立成段），只是不参与聚合
    assert any(r["image_id"] == gap_id for r in out["per_image"])
    monkeypatch.setattr(cfg, "WATERMARK_KNOWN_GAP_IMAGES", orig_gap)
