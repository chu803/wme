# -*- coding: utf-8 -*-
"""Stage 160 探针：make_real_compare_crops 的「取景 vs 掩码」契约。

假设：mask 用 --pad 外扩、crop 用 --view 外扩；若 view < pad，则 crop 比被修复区域窄
（极端时整幅落在 mask 内）⇒ 人眼看到的「对比图」根本不含水印与上下文，验证通道被静默误导。
用桩 inpaint（掩码处涂绿/蓝）量化 crop 内「属于 mask」的列比例。
隔离 tmp，不改源码。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types

import numpy as np

ROOT = "D:/work 111/2026-08-03-17-38-51"
sys.path.insert(0, ROOT)

from scripts import make_real_compare_crops as mrc  # noqa: E402

SAVED: dict = {}
MASK_SEEN: dict = {}


def _install_stubs():
    core = types.ModuleType("core")
    core.__path__ = []
    iu = types.ModuleType("core.image_utils")

    def load_image(p):
        from PIL import Image
        return np.array(Image.open(p).convert("RGB"))

    def save_image(p, arr):
        from PIL import Image
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Image.fromarray(arr.astype(np.uint8)).save(p)
        SAVED[os.path.basename(p)] = arr.copy()

    iu.load_image = load_image
    iu.save_image = save_image

    ip = types.ModuleType("core.inpainter")

    def inpaint(img, mask, engine="lama"):
        MASK_SEEN[engine] = mask.copy()
        out = img.copy()
        out[mask > 0] = (0, 255, 0) if engine == "opencv" else (0, 0, 255)
        return out

    ip.inpaint = inpaint
    sys.modules["core"] = core
    sys.modules["core.image_utils"] = iu
    sys.modules["core.inpainter"] = ip


def _setup(tmp):
    bdir = os.path.join(tmp, "benchmark_real")
    os.makedirs(os.path.join(bdir, "images"), exist_ok=True)
    img = np.full((120, 160, 3), 100, np.uint8)
    img[20:45, 40:70] = (255, 0, 0)
    from PIL import Image
    Image.fromarray(img).save(os.path.join(bdir, "images", "t1.png"))
    json.dump({"version": 1, "images": [
        {"id": "t1", "filename": "t1.png", "path": "images/t1.png", "ai_source": "other",
         "watermark_style_hint": "corner_logo", "original_size": [160, 120],
         "sha256": "x", "added_at": "t", "source": "local"}]},
        open(os.path.join(bdir, "manifest.json"), "w", encoding="utf-8"))
    json.dump({"version": 1, "entries": [
        {"image_id": "t1", "watermarks": [{"bbox": [40, 20, 30, 25], "style": "corner_logo"}],
         "labeled_at": "t", "labeled_by": "manual:test"}]},
        open(os.path.join(bdir, "ground_truth.json"), "w", encoding="utf-8"))
    return bdir


def _run(bdir, argv):
    mrc.BENCH_DIR = bdir
    mrc.MANIFEST_PATH = os.path.join(bdir, "manifest.json")
    mrc.TRUTH_PATH = os.path.join(bdir, "ground_truth.json")
    outdir = os.path.join(bdir, "results", "visual")
    old = sys.argv
    sys.argv = ["prog", "--out-dir", outdir, *argv]
    try:
        rc = mrc.main()
    finally:
        sys.argv = old
    return rc


def _report(tag, pad, view):
    SAVED.clear(); MASK_SEEN.clear()
    tmp = tempfile.mkdtemp()
    bdir = _setup(tmp)
    rc = _run(bdir, ["--pad", str(pad), "--view", str(view)])
    comp = SAVED.get("t1_0_corner_logo.png")
    mask = MASK_SEEN.get("opencv")
    # crop 窗口（同一逻辑）
    x, y, bw, bh = 40, 20, 30, 25
    H, W = 120, 160
    cx0, cx1 = max(0, x - view), min(W, x + bw + view)
    mx0, mx1 = max(0, x - pad), min(W, x + bw + pad)
    # crop 内每一列是否落在 mask 的 x 范围内
    in_mask = sum(1 for c in range(cx0, cx1) if mx0 <= c < mx1)
    frac = in_mask / (cx1 - cx0) if cx1 > cx0 else 0.0
    print(f"[{tag}] pad={pad} view={view} rc={rc}")
    print(f"    mask x[{mx0},{mx1})  crop x[{cx0},{cx1})  宽={cx1-cx0}")
    print(f"    crop 内落在 mask 的列比例 = {frac:.2f}（1.00 = 整幅都在被修复区内 ⇒ 看不到原图与上下文）")
    if comp is not None:
        print(f"    composite shape={comp.shape}")
    print()


if __name__ == "__main__":
    _install_stubs()
    print("== make_real_compare_crops 取景契约 ==")
    _report("默认(合理)", 24, 70)
    _report("view<pad", 40, 10)
    _report("view 远小于 pad", 60, 0)
    print("PROBE DONE")
