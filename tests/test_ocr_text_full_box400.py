# -*- coding: utf-8 -*-
"""阶段 400：OCR 文字框必须**整框**进掩码（否则字形边缘残留 ⇒ "糊字仍可读"）。

根因（实测定位，三步排除法）：
  1. 掩码可视化显示：绿色只盖住文字**左半**，右边「AI」露在掩码外；
  2. 膨胀 2/3/5px 无效（缺的不只是抗锯齿边缘）；
  3. 手工把 OCR 框整块填进掩码 ⇒ 文字彻底消失。
⇒ 真正原因：碎片聚合重建掩码时只保留「原有候选像素」，而 OCR 文字框的候选像素
是**字形本身** ⇒ 框没被完整覆盖。

判定指标（阶段 399 的教训：自动指标必须与目视一致）——用**锐边残留**
（框内 |∇| 的 99 分位，修复后/修复前）：文字可读 ⇒ 1.000；只剩模糊块 ⇒ 0.281 ⇒ 阈值 0.5。

夹具用挑战集里真实的 B4（右下角无底板纯文字角标，由已入库的确定性生成器产出）——
与门禁量的是同一张图，避免"夹具没复现 ⇒ 假绿"。

守卫（真走检测器 + 修复全链路）：
  · B4 的锐边残留必须 < 0.5（文字被擦掉）；
  · 框外不许被改（误伤护栏）。
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

#: 本用例验的是「OCR 文字框进掩码」⇒ 需要 EasyOCR（可选依赖）。
#: CI 等价环境（ci311venv）不装 ⇒ 跳过；完整环境（gatevenv）真跑。
pytest.importorskip("easyocr", reason="需要 EasyOCR：OCR 文字框相关行为")

ROOT = pathlib.Path(__file__).resolve().parent.parent
B4 = ROOT / "benchmark_real" / "challenge" / "images" / "B4.png"
#: B4 的文字足迹（与 scripts/build_challenge_set.py 的几何参数一致）。
TEXT_BOX = (842, 980, 166, 30)


def _ensure_b4() -> None:
    if not B4.exists():
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        import build_challenge_set

        build_challenge_set.main()


def _read(path: pathlib.Path):
    import cv2

    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _sharp_ratio(before: np.ndarray, after: np.ndarray, box=TEXT_BOX) -> float:
    """框内**锐边**残留比值（修复后 / 修复前）：文字还在 ⇒ 高；只剩模糊块 ⇒ 低。"""
    import cv2

    x, y, w, h = box

    def _p99(img: np.ndarray) -> float:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[y:y + h, x:x + w].astype(np.float32)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        return float(np.percentile(np.sqrt(gx * gx + gy * gy), 99.0))

    b = _p99(before)
    return _p99(after) / b if b > 1e-6 else 0.0


def test_b4_text_only_badge_is_actually_removed():
    """B4（深底深字、无底板）：修复后文字的锐边必须基本消失。"""
    from core.detector import detect_watermark_mask
    from core.inpainter import inpaint

    _ensure_b4()
    img = _read(B4)
    det = detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    out = inpaint(img, det.mask, engine="opencv", strength=1.0, dilate=0, feather=5,
                  threshold=127, denoise=3)
    ratio = _sharp_ratio(img, out)
    assert ratio < 0.5, f"文字还在（锐边残留 {ratio:.3f}，应 <0.5）"


def test_b4_mask_covers_the_whole_ocr_text_box():
    """机制守卫：OCR 文字框必须**整框**在掩码里（本次修复的直接对象）。"""
    from core.detector import detect_watermark_mask

    _ensure_b4()
    img = _read(B4)
    det = detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    boxes = [r for r in det.regions if r.kind == "ocr_text"]
    assert boxes, "夹具没检出 OCR 文字框（EasyOCR 未命中？）"
    for r in boxes:
        sub = det.mask[r.y:r.y + r.h, r.x:r.x + r.w]
        assert (sub > 0).mean() >= 0.99, (
            f"OCR 框 [{r.x},{r.y},{r.w},{r.h}] 只被掩码盖了 "
            f"{float((sub > 0).mean()):.1%}（必须整框）")


def test_b4_repair_does_not_touch_far_outside_the_text():
    """误伤护栏：文字框外不许被改。"""
    from core.detector import detect_watermark_mask
    from core.inpainter import inpaint

    _ensure_b4()
    img = _read(B4)
    det = detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    out = inpaint(img, det.mask, engine="opencv", strength=1.0, dilate=0, feather=5,
                  threshold=127, denoise=3)
    diff = np.abs(img.astype(int) - out.astype(int)).max(axis=2) > 10
    x, y, w, h = TEXT_BOX
    outside = diff.copy()
    outside[y:y + h, x:x + w] = False
    assert float(outside.mean()) < 0.005, (
        f"文字框外被改了 {float(outside.mean()):.3%}（误伤）")
