# -*- coding: utf-8 -*-
"""阶段 393：OCR 提示词要说事实（LESSONS #360 文案要跟行为）。

实测（端到端跑界面同一条处理链）：干净图那次提示写"未启用 OCR"，
但 EasyOCR **是装着的、也确实开着**，只是这张图没命中 OCR 文字框
⇒ `used_ocr`（"命中过 OCR 框"）被当成了"OCR 是否启用"。
用户会因此以为没装 EasyOCR、被误导去装第二个。

守卫：
  · OCR 可用但本次没命中 ⇒ 提示必须说"已启用"（并说明没命中）；
  · OCR 不可用 ⇒ 提示才说"未启用"；
  · 中英文两套键都在。
"""

from __future__ import annotations

from utils.i18n import t_in


def test_ocr_tip_says_enabled_when_available_but_no_hit():
    from ui.tab_auto import _ocr_tip

    tip = _ocr_tip(used_ocr=False, ocr_available=True)
    assert "已启用" in tip, "OCR 可用却提示未启用 = 事实错误"
    assert "未启用 OCR" not in tip


def test_ocr_tip_says_disabled_only_when_really_unavailable():
    from ui.tab_auto import _ocr_tip

    tip = _ocr_tip(used_ocr=False, ocr_available=False)
    assert "未启用" in tip, "OCR 真不可用时必须说未启用"


def test_ocr_tip_hit_case_mentions_enabled():
    from ui.tab_auto import _ocr_tip

    tip = _ocr_tip(used_ocr=True, ocr_available=True)
    assert "已启用" in tip


def test_new_i18n_keys_exist_in_both_languages():
    for lang in ("zh-CN", "en-US"):
        assert t_in(lang, "auto.msg.ocr_on_no_hit"), f"{lang} 缺 auto.msg.ocr_on_no_hit"
        assert t_in(lang, "detect.stats.ocr_on_no_hit"), f"{lang} 缺 detect.stats.ocr_on_no_hit"


def test_detector_exposes_public_ocr_availability():
    """检测器要能对外说"OCR 可用"（不能只有私有的 _ocr_available）。"""
    from core import detector

    assert callable(getattr(detector, "is_ocr_available", None))
    assert detector.is_ocr_available() in (True, False)