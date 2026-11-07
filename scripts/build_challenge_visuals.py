# -*- coding: utf-8 -*-
"""生成「目视裁定」的证据图：4 种几何的修复前后对比（默认 OpenCV 引擎）→ benchmark_real/challenge/visual/。

这些图是阶段 399 门禁里 `CORNER_GEOMETRY_ERASED_REF`（哪几种几何真的擦净了）的**依据**，
必须随仓库走，否则"目视裁定"无法被第三方复核。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from core.detector import detect_watermark_mask  # noqa: E402
from core.inpainter import inpaint  # noqa: E402
from core.preset_recommender import recommend_preset  # noqa: E402
from services.auto_pipeline import _apply_preset_dilate_floor  # noqa: E402

IMAGES = ROOT / "benchmark_real" / "challenge" / "images"
OUT = ROOT / "benchmark_real" / "challenge" / "visual"
OUT.mkdir(parents=True, exist_ok=True)


def read(p: pathlib.Path):
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    return img


def main() -> None:
    for name in ("B1.png", "B2.png", "B3.png", "B4.png"):
        img = read(IMAGES / name)
        rec = recommend_preset(img)
        det = detect_watermark_mask(img, preset=rec.preset, effective_preset=rec.preset)
        dil = _apply_preset_dilate_floor(rec.preset, 0)
        try:
            rep = inpaint(img, det.mask, engine="opencv", strength=1.0, dilate=dil,
                          feather=5, threshold=127, denoise=3)
        except Exception:  # noqa: BLE001
            rep = img.copy()
        sep = np.full((img.shape[0], 8, 3), 255, np.uint8)
        out = OUT / f"{name[:-4]}_before_after.jpg"
        cv2.imencode(".jpg", np.hstack([img, sep, rep]),
                     [int(cv2.IMWRITE_JPEG_QUALITY), 92])[1].tofile(str(out))
        print(f"{out}（预设 {rec.preset}）")


if __name__ == "__main__":
    main()
