# -*- coding: utf-8 -*-
"""Stage 127 回归守卫：services/auto_pipeline 两处真缺陷（D1/D2）。

D1：process_file 的 manual 分支调用 load_image_with_alpha(mp)，但该名字未 import
    ⇒ manual 模式 NameError、完全不可用。守卫：manual（显式 mask_path 与同名掩码两条）
    必须成功。
D2：保存前 on_progress(0.92, "保存结果……") 连写两遍 ⇒ 进度事件重复。守卫：该进度
    事件在运行时恰好出现 1 次。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw

import services.auto_pipeline as ap


def _make_orig(path):
    img = Image.new("RGB", (120, 90), (240, 240, 240))
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([40, 30, 80, 60], fill=(150, 150, 150, 200))
    img.save(path)


def _make_mask(path):
    m = Image.new("RGB", (120, 90), (0, 0, 0))
    ImageDraw.Draw(m).rectangle([40, 30, 80, 60], fill=(255, 255, 255))
    m.save(path)


def _run(input_path, **kw):
    events = []
    rd = ap.process_file(
        str(input_path), engine="opencv",
        real_superres=False, superres=False,
        on_progress=lambda p, m: events.append((round(float(p), 3), m)),
        **kw,
    )
    return rd, events


def test_d1_manual_explicit_mask_path(tmp_path):
    """D1：manual + 显式 mask_path 必须成功（修复前 NameError: load_image_with_alpha）。"""
    orig = tmp_path / "orig.png"
    mask = tmp_path / "m.png"
    _make_orig(orig)
    _make_mask(mask)
    rd, _ = _run(orig, mode="manual", mask_path=str(mask), out_dir=str(tmp_path))
    assert rd["status"] == "success", rd
    assert rd.get("out_path") and os.path.exists(rd["out_path"])


def test_d1_manual_same_name_mask(tmp_path):
    """D1 兄弟路径：manual 自动发现同名 `.mask.png` 掩码后必须成功。"""
    orig = tmp_path / "orig.png"
    _make_orig(orig)
    _make_mask(tmp_path / "orig.mask.png")  # stem + ".mask.png" 约定
    rd, _ = _run(orig, mode="manual", out_dir=str(tmp_path))
    assert rd["status"] == "success", rd


def test_d2_progress_save_event_once(tmp_path):
    """D2：保存阶段的 (0.92, 保存结果……) 进度事件在运行时必须恰好 1 次。"""
    orig = tmp_path / "orig.png"
    mask = tmp_path / "m.png"
    _make_orig(orig)
    _make_mask(mask)
    rd, events = _run(orig, mode="manual", mask_path=str(mask), out_dir=str(tmp_path))
    assert rd["status"] == "success", rd
    n = sum(1 for (p, msg) in events if p == 0.92 and "保存结果" in msg)
    assert n == 1, f"保存进度事件应恰好 1 次，实际 {n}：{events}"


def test_import_exposes_mask_loader():
    """D1 静态邻位守卫：模块必须真的 import 了 load_image_with_alpha（防再次漏 import）。"""
    assert hasattr(ap, "load_image_with_alpha"), "auto_pipeline 未 import load_image_with_alpha"
