# -*- coding: utf-8 -*-
"""
阶段 169 守卫：报告类交付物 i18n 泄漏收口。

背景（i18n 整类第 4 子类）：`services/report_service.py` 生成的 HTML / PDF 修复
报告全部是硬编码中文单语，英文界面下导出报告仍满是中文。本阶段把这些交付物文案
登记为 `utils/i18n` 的 `report.*` 双语键，经 `t()` 跟随全局语言取用。

本守卫钉住三条契约：
  1. 覆盖：`report_service.py` 中静态引用的每个 `t("report.*")` 键，必须在
     zh-CN 与 en-US 双侧都存在，且占位符同名（漏写一侧立即红）。
  2. 渲染：en-US 下生成的 HTML 报告、以及 `_card_html` 的全部质量分支，
     均**不含任何 CJK / 全角标点**（产品名等专有名词在测试中替换为 ASCII）。
  3. 回归：zh-CN 下报告仍含中文（确保没把原文清空）。
"""

import re

import numpy as np
import pytest
import cv2  # type: ignore

import config
import services.report_service as rs
from services.quality_service import QualityReport
from utils.i18n import set_language, t_in, validate_i18n_parity

# CJK 与全角标点范围（与阶段 168 守卫同口径；`·` U+00B7 / `—` U+2014 不在其中）。
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")

# `t("report.x")` / `t('report.x')` —— 静态引用的键（动态 f-string 键单独覆盖）。
_STATIC_KEY = re.compile(r"""\bt\(\s*['"]([^'"]+)['"]""")


def _has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def _referenced_report_keys() -> set:
    """从 report_service.py 源码提取所有静态 `t("report.*")` 引用键。"""
    import os

    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "services", "report_service.py"), encoding="utf-8").read()
    return {k for k in _STATIC_KEY.findall(src) if k.startswith("report.")}


def _write_png(path, val):
    cv2.imwrite(str(path), np.full((80, 120, 3), val, dtype=np.uint8))


# ------------------------------------------------------------------------------
# 契约 1：report.* 键齐备且占位符同名
# ------------------------------------------------------------------------------

def test_all_referenced_report_keys_defined_both_langs():
    keys = _referenced_report_keys()
    assert keys, "未从 report_service.py 提取到任何 report.* 键（正则或源码结构变了？）"
    for k in sorted(keys):
        assert t_in("zh-CN", k) != k, f"{k} 在 zh-CN 未定义（回退为键名）"
        assert t_in("en-US", k) != k, f"{k} 在 en-US 未定义（回退为键名）"


def test_all_referenced_report_keys_placeholder_parity():
    for k in sorted(_referenced_report_keys()):
        zh = t_in("zh-CN", k)
        en = t_in("en-US", k)
        pb = set(re.findall(r"\{([a-zA-Z_]\w*)\}", zh))
        pe = set(re.findall(r"\{([a-zA-Z_]\w*)\}", en))
        assert pb == pe, f"{k} 占位符不一致：zh={sorted(pb)} en={sorted(pe)}"


def test_dynamic_quality_level_keys_defined():
    """动态键 `report.quality_{level}` 的三个具体分支必须存在。"""
    for lvl in ("good", "warn", "review"):
        k = f"report.quality_{lvl}"
        assert t_in("en-US", k) != k and t_in("zh-CN", k) != k, f"{k} 缺失"


def test_i18n_parity_has_no_report_problems():
    problems = [p for p in validate_i18n_parity() if "report." in p]
    assert not problems, "语言包 report.* 结构不对称：\n" + "\n".join(problems)


# ------------------------------------------------------------------------------
# 契约 2：en-US 生成零 CJK
# ------------------------------------------------------------------------------

def _base_entry(quality):
    return {"name": "x.png", "engine": "opencv", "mode": "auto", "params": "s=1",
            "time_str": "2026-01-01 00:00:00", "img_b64": "", "img_w": 10, "img_h": 10,
            "img_is_compare": True, "result_path": "x.png", "recommendation": None,
            "quality": quality}


@pytest.mark.parametrize("level", ["good", "warn", "review"])
def test_en_card_html_quality_levels_no_cjk(level):
    set_language("en-US")
    e = _base_entry(QualityReport(ssim=0.9, changed_ratio=0.1, score=70, level=level, detail="d"))
    card = rs._card_html(e)
    assert not _has_cjk(card), f"en-US 质量卡（level={level}）仍含中文：{card!r}"


def test_en_card_html_unassessed_no_cjk():
    set_language("en-US")
    card = rs._card_html(_base_entry(None))
    assert not _has_cjk(card), f"en-US 未评估卡仍含中文：{card!r}"


def test_en_quality_summary_labels_english():
    set_language("en-US")
    qsum = rs._quality_summary({"total": 3, "good": 1, "warn": 1, "review": 1,
                                "assessed": 3, "unassessed": 0, "avg": 0.8})
    labels = [c[0] for c in qsum["counts"]]
    assert labels == ["Good", "Warning", "Needs review"], labels
    assert all(not _has_cjk(l) for l in labels)


def test_en_quality_summary_note_english():
    set_language("en-US")
    qsum = rs._quality_summary({"total": 3, "good": 0, "warn": 0, "review": 0,
                                "assessed": 0, "unassessed": 3, "avg": None})
    assert not _has_cjk(qsum["note"]), qsum["note"]


def test_en_html_report_no_cjk(tmp_path, monkeypatch):
    """en-US 下完整 HTML 报告零 CJK（产品名替换为 ASCII 以隔离专有名词）。"""
    set_language("en-US")
    monkeypatch.setattr(config, "APP_NAME", "WME", raising=False)
    o, r = tmp_path / "o.png", tmp_path / "r.png"
    _write_png(o, 235)
    _write_png(r, 60)
    src = rs.ReportSource(original_path=str(o), result_path=str(r), original_name="t.png",
                          engine="opencv", mode="auto", params="s=1", ts=1_700_000_000.0)
    out = tmp_path / "rep.html"
    rs.export_report([src], fmt="html", out_path=str(out))
    text = out.read_text(encoding="utf-8")
    assert not _has_cjk(text), f"en-US HTML 报告含中文：{sorted({c for c in text if _CJK.match(c)})[:8]}"


def test_en_empty_records_error_no_cjk(tmp_path):
    set_language("en-US")
    with pytest.raises(FileNotFoundError) as ei:
        rs.export_report([], fmt="html", out_path=str(tmp_path / "e.html"))
    assert not _has_cjk(str(ei.value)), str(ei.value)


# ------------------------------------------------------------------------------
# 契约 3：zh-CN 报告不被破坏
# ------------------------------------------------------------------------------

def test_zh_report_intact(tmp_path):
    set_language("zh-CN")
    o, r = tmp_path / "o.png", tmp_path / "r.png"
    _write_png(o, 235)
    _write_png(r, 60)
    src = rs.ReportSource(original_path=str(o), result_path=str(r), original_name="测试图.png",
                          engine="opencv", mode="auto", params="力度=50", ts=1_700_000_000.0)
    out = tmp_path / "rep_zh.html"
    rs.export_report([src], fmt="html", out_path=str(out))
    text = out.read_text(encoding="utf-8")
    # zh-CN 报告仍含中文标题与汇总（确保本地化没有清空原文）。
    assert "修复报告" in text
    assert "共" in text and "条" in text
