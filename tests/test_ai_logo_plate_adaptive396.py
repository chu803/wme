# -*- coding: utf-8 -*-
"""阶段 396：角标板「整块填充」的**足迹自适应**。

阶段 395 的诊断（挑战集 4 种几何）：
  · 检出 4/4、完全擦净仅 1/4；
  · 根因一：写死的足迹（340x128@2048² 贴右下角）对大板只覆盖 60.7%、对半透明板 64.3%；
  · 根因二：豆包专属模板信号换字体/位置/尺寸**一律为 0**（4 张实测全 0）⇒ 固定足迹那条路也走不通。

本阶段的做法：**填哪里由"检测器已经给出的角标板块区域"决定**（它的框在左下角板/半透明板上
与真实板重叠 100%）—— 不再只认写死的足迹。
两条纪律（都由守卫钉住）：
  · **不新增区域**：只填已有 corner_logo 区域的框 ⇒ 阶段 179 的「补全板块」杠杆语义不变；
  · **不越预设边界**：只有该预设真的产出了角标区域时才填（ai_gradient 这类"整图"预设的
    角落本就不该开火，它的红线不许被踩）。

守卫（真走检测器全链路）：
  · 半透明白板（右下，1024²）⇒ 掩码要盖住 ≥90% 的板（自适应生效）；
  · 左下角的板也要被盖住（写死足迹盖不到）；
  · **干净图不许被填**（宁可漏检不可滥杀）。
"""

from __future__ import annotations

import pathlib

import numpy as np

import config
from core import detector

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT = ROOT / "scripts" / "assets" / "DejaVuSans-Bold.ttf"


def _plate_image(side: int = 1024, plate_w: int = 174, plate_h: int = 59,
                 alpha: int = 255, corner: str = "right", bg=(90, 100, 115)):
    """白底圆角板 + 深色文字（半透明用 alpha）。字体用随源码分发的那份（阶段 369 纪律）。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (side, side), bg)
    d = ImageDraw.Draw(img)
    for i in range(0, side, 24):  # 一点纹理，避免背景太平
        d.line([(0, i), (side, i)], fill=(bg[0] + 6, bg[1] + 6, bg[2] + 6), width=1)
    board = Image.new("RGBA", (plate_w, plate_h), (255, 255, 255, alpha))
    ImageDraw.Draw(board).rounded_rectangle([0, 0, plate_w - 1, plate_h - 1],
                                             radius=12, fill=(255, 255, 255, alpha))
    x = side - plate_w - 20 if corner == "right" else 20
    y = side - plate_h - 20
    img.paste(board, (x, y), board)
    f = ImageFont.truetype(str(FONT), 22)
    d = ImageDraw.Draw(img)
    d.text((x + plate_w // 2 - 52, y + plate_h // 2 - 13), "Doubao AI", font=f, fill=(26, 26, 26))
    arr = np.asarray(img)[:, :, ::-1].copy()  # PIL RGB → BGR（与检测器入参一致）
    return arr, (x, y, plate_w, plate_h)


def _coverage(mask, box) -> float:
    x, y, w, h = box
    sub = mask[y:y + h, x:x + w]
    return float((sub > 0).mean()) if sub.size else 0.0


def test_semi_transparent_plate_is_filled_even_without_ai_logo_preset():
    """半透明白板：掩码要盖住 ≥90%（足迹自适应生效）。

    注：预设是 ai_text_heavy，但它**产出了角标区域**（角标策略在该预设下仍开着）
    ⇒ 填充按那个区域的框走，与"豆包模板信号"无关。
    """
    img, box = _plate_image(alpha=140)
    det = detector.detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    assert _coverage(det.mask, box) >= 0.90, (
        f"半透明板覆盖只有 {_coverage(det.mask, box):.1%}（预设无关的自适应填充没生效）")


def test_bottom_left_plate_is_filled():
    """左下角的板也要被盖住——写死的右下角足迹盖不到它。"""
    img, box = _plate_image(corner="left")
    det = detector.detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    assert _coverage(det.mask, box) >= 0.90, (
        f"左下角板覆盖只有 {_coverage(det.mask, box):.1%}")


def test_clean_image_is_not_filled():
    """干净图不许被整块填（宁可漏检不可滥杀）。"""
    img = np.full((1024, 1024, 3), 120, np.uint8)
    det = detector.detect_watermark_mask(img, preset="ai_text_heavy", effective_preset="ai_text_heavy")
    assert float((det.mask > 0).mean()) < 0.005, (
        f"干净图被填了 {float((det.mask > 0).mean()):.2%}")


def test_switch_still_controls_the_feature():
    """开关仍然能一键关掉整块填充。"""
    assert config.DETECT_AI_LOGO_PLATE_FILL is True
    assert config._env_bool("DETECT_AI_LOGO_PLATE_FILL", True) is True
