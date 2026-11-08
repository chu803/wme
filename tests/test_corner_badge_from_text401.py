# -*- coding: utf-8 -*-
"""阶段 401：角落角标「板」可由**文字框反推**（覆盖大板 B2）。

阶段 400 的遗留：B2（358x67 大板）擦不干净，因为白底板块判据拒收它——
它的亮区与照片里相邻的亮背景**连成一片**（实测连通域 371x144、实心度 0.53、
触碰搜索区边界）⇒ 任何"亮块"阈值法都会把板和背景一起收/一起丢。

换一条路：**角标本来就是"字 + 板"** ⇒ 用角落里的 OCR 文字框当种子，
按一个可配的放大系数反推板的范围（文字在板上居中、四周留白）。

守卫（真走检测器全链路，用挑战集里真实的 B2）：
  · B2 的板必须被掩码覆盖 ≥80%（板被认出来了）；
  · 板必须**亮**（白底）——深底纯文字（B4 那种）不适用放大反推，否则会误伤周围；
  · 干净图不受影响（不新增误伤）。
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHAL = ROOT / "benchmark_real" / "challenge" / "images"
pytest.importorskip("easyocr", reason="需要 EasyOCR：角标文字框相关行为")

#: 挑战集里 B2（大板）与 B4（深底纯文字）的真实足迹（来自 scripts/build_challenge_set.py 的几何参数）。
B2_BOX = (646, 937, 358, 67)
B4_BOX = (880, 976, 124, 34)


def _ensure_challenge_set() -> None:
    if not (CHAL / "B2.png").exists():
        import sys

        sys.path.insert(0, str(ROOT / "scripts"))
        import build_challenge_set

        build_challenge_set.main()


def _read(name: str):
    import cv2

    p = CHAL / name
    return cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)


def test_big_plate_is_covered_by_mask():
    """B2（大板）：板必须被掩码盖住 ≥80%。"""
    from core.detector import detect_watermark_mask

    _ensure_challenge_set()
    img = _read("B2.png")
    det = detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    x, y, w, h = B2_BOX
    sub = det.mask[y:y + h, x:x + w]
    assert float((sub > 0).mean()) >= 0.80, (
        f"大板只被盖了 {float((sub > 0).mean()):.1%}（板没被认出来）")


def test_badge_expansion_only_applies_to_bright_boards():
    """深底纯文字（B4）不适用"板反推"：那里没有白板，不该把周围一起擦。"""
    from core.detector import detect_watermark_mask

    _ensure_challenge_set()
    img = _read("B4.png")
    det = detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    x, y, w, h = B4_BOX
    sub = det.mask[y:y + h, x:x + w]
    # 只允许文字本身的覆盖（+小膨胀），不允许"板"那种整块放大
    assert float((sub > 0).mean()) < 0.95, (
        f"深底文字被当成板、放大覆盖了 {float((sub > 0).mean()):.1%}")
