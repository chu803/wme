# -*- coding: utf-8 -*-
"""阶段 337 · 质量诊断文案的**语言切换回归**：英文界面不许出现中文，中文界面不许出现 good/warn。

被修掉的泄漏（三处消费点，审计只报了前两处，第三处是我自己 grep 出来的）：

* `ui/tab_batch.py` 把服务层产的中文句子塞进翻译模板
  `t("batch.log_quality", level=q.level, detail=q.detail)` ⇒ 英文界面里模板是英文、内容是中文；
  同时 `level` 是英文原值 ⇒ **中文界面里冒出 "warn"**（反向泄漏）。
* `ui/tab_auto.py` 直接 `f"\n· 后处理提升：{imp['detail']}"`（UI 层硬编码中文）。
* `cli.py` 打 `q.level`（英文原值）与 `q.detail`（中文句子）。

修法：`services/quality_service` **不再产句子**，只回「i18n 键 + 数字」
（`detail_key` / `detail_base_key` / `review_floor`）；渲染收敛到 `detail_text()` / `level_text()`
两个唯一实现，批量页与 CLI 共用。

判据取自 LESSONS #72：**凡文案生成层，回归测试必须含一次语言切换 + CJK 码点扫描**——
「中文界面下的中文」永远测不出这类缺陷。
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from services.quality_service import (
    QualityReport,
    detail_text,
    evaluate_quality_arrays,
    level_text,
)
from utils.i18n import get_language, set_language, t

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


@pytest.fixture
def restore_language():
    saved = get_language()
    yield
    set_language(saved)


def _two_images(changed_ratio: float):
    """造一对图：原图纯色，结果图改动 `changed_ratio` 比例的区域。"""
    orig = np.full((64, 64, 3), 200, dtype=np.uint8)
    res = orig.copy()
    n = int(64 * 64 * changed_ratio)
    res.reshape(-1, 3)[:n] = 30
    return orig, res


def test_report_carries_no_sentence(restore_language):
    """服务层产物里不许再有中文句子——只允许键与数字。"""
    for ratio in (0.0, 0.02, 0.5, 0.99):
        rep = evaluate_quality_arrays(*_two_images(ratio))
        assert isinstance(rep, QualityReport)
        assert not hasattr(rep, "detail"), "detail 字段应当已被键取代（否则句子又会流回界面）"
        assert rep.detail_key.startswith("quality.detail_"), rep.detail_key
        for field in ("detail_key", "detail_base_key"):
            v = getattr(rep, field)
            assert not _CJK.search(v or ""), f"{field} 里出现中文：{v}"


@pytest.mark.parametrize("level", ["good", "warn", "review"])
def test_level_text_is_translated_both_ways(restore_language, level):
    set_language("en-US")
    en = level_text(level)
    set_language("zh-CN")
    zh = level_text(level)
    assert en and en != f"report.quality_{level}", "英文缺键会回退成键名"
    assert not _CJK.search(en), f"英文界面出现中文：{en}"
    assert _CJK.search(zh), f"中文界面必须给中文级别词，实得：{zh}"
    assert level not in zh, f"中文界面直接把英文原值 {level} 给了用户：{zh}"


def test_batch_log_line_has_no_cjk_under_en(restore_language):
    """**用户实际看到的那一行**：批量日志在 en-US 下必须零 CJK。"""
    orig, res = _two_images(0.5)
    rep = evaluate_quality_arrays(orig, res)
    set_language("en-US")
    line = t("batch.log_quality", name="pic.png", score=rep.score,
             level=level_text(rep.level), detail=detail_text(rep))
    assert not _CJK.search(line), f"英文界面漏中文：{line}"
    assert "quality.detail" not in line, f"键没被翻译：{line}"
    set_language("zh-CN")
    zh_line = t("batch.log_quality", name="pic.png", score=rep.score,
                level=level_text(rep.level), detail=detail_text(rep))
    assert _CJK.search(zh_line), f"中文界面必须是中文：{zh_line}"
    assert zh_line != line


def test_auto_postprocess_gain_is_translated(restore_language):
    """自动页那行「后处理提升」也不许再硬编码中文。"""
    set_language("en-US")
    en = t("auto.msg.postprocess_gain", before=61.0, after=73.5, delta="+12.5")
    assert not _CJK.search(en), en
    assert "61" in en and "73.5" in en and "+12.5" in en
    set_language("zh-CN")
    zh = t("auto.msg.postprocess_gain", before=61.0, after=73.5, delta="+12.5")
    assert _CJK.search(zh) and "后处理提升" in zh, zh


def test_forced_review_keeps_the_original_diagnosis(restore_language):
    """阶段 87 那条语义不能被 i18n 改造弄丢：强制复核时要**同时**说明「分数低于复核线」与
    「原诊断是什么」（否则用户按 review 去查水印没修掉，看到的却是「修过头」——方向相反）。"""
    rep = QualityReport(ssim=0.4, changed_ratio=0.9, score=40, level="review",
                        detail_key="quality.detail_review_low_score",
                        detail_base_key="quality.detail_warn_overshoot",
                        review_floor=60)
    set_language("zh-CN")
    zh = detail_text(rep)
    assert "40" in zh and "60" in zh, zh
    assert "修过头" in zh or "误伤" in zh, f"原诊断没被带进来：{zh}"
    set_language("en-US")
    en = detail_text(rep)
    assert not _CJK.search(en), en
    assert "overdone" in en or "damaging" in en, f"英文侧丢了原诊断：{en}"


def test_i18n_parity_still_clean():
    """新键必须中英对称、占位符同名（`validate_i18n_parity` 是既有构建期守卫）。"""
    from utils import i18n
    assert i18n.validate_i18n_parity() == []
