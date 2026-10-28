# -*- coding: utf-8 -*-
"""阶段 350 · 媒体服务层（视频 / PDF）面向用户的文案必须随语言切换。

背景：337 把「质量诊断」那条链路的文案收归 UI（服务层产键、UI 渲染），但**媒体服务层**
（`services/video_service.py` / `services/pdf_service.py`）仍在直接产中文句子
（进度提示、warnings、error）⇒ 英文界面里视频/PDF 链路全是中文。

本阶段把这两处的**用户可见文案**改成 i18n 键：
* 中文文案**逐字不变** ⇒ 中文用户与既有断言零变化；
* 英文界面拿到译文；
* 日志（`log.*`）**保持中文**（日志不是界面文案，不进 i18n）。

守卫：
* `media_svc.*` 的英文侧**零 CJK**（漏译一眼可见）；
* 行为级：切到 en-US 后，服务返回的 error **不含 CJK**；切回 zh-CN 仍是中文。
"""
from __future__ import annotations

import re

import pytest

from utils import i18n
from utils.i18n import get_language, set_language, t

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


@pytest.fixture
def restore_language():
    saved = get_language()
    yield
    set_language(saved)


def test_i18n_parity_still_clean():
    assert i18n.validate_i18n_parity() == []


def test_media_svc_english_has_no_cjk():
    """英文侧不能夹带中文（漏译/复制粘贴没改干净一眼可见）。"""
    bad = []
    for key in _keys():
        text = i18n.t_in("en-US", key)
        if _CJK.search(text):
            bad.append(f"{key}: {text}")
    assert not bad, f"英文文案里夹了中文：{bad}"


def _keys() -> list:
    from utils.i18n import TRANSLATIONS
    out = []
    for k, v in TRANSLATIONS["zh-CN"]["media_svc"].items():
        out.append(f"media_svc.{k}")
    return out


def test_video_error_follows_language(restore_language):
    import services.video_service as vs

    set_language("en-US")
    res = vs.process_video("D:/definitely/missing/file.mp4", out_path="D:/tmp/out.mp4")
    assert res.status == "failed"
    assert res.error and not _CJK.search(res.error), f"英文界面出现中文：{res.error}"

    set_language("zh-CN")
    res2 = vs.process_video("D:/definitely/missing/file.mp4", out_path="D:/tmp/out.mp4")
    assert res2.error and _CJK.search(res2.error), res2.error


def test_pdf_error_follows_language(restore_language):
    import services.pdf_service as ps

    set_language("en-US")
    res = ps.process_pdf("D:/definitely/missing/file.pdf", out_path="D:/tmp/out.pdf")
    assert res.status == "failed"
    assert res.error and not _CJK.search(res.error), f"英文界面出现中文：{res.error}"

    set_language("zh-CN")
    res2 = ps.process_pdf("D:/definitely/missing/file.pdf", out_path="D:/tmp/out.pdf")
    assert res2.error and _CJK.search(res2.error), res2.error


def test_media_svc_zh_text_is_unchanged():
    """中文侧逐字保持原样（中文用户零感知）。"""
    assert t("media_svc.video_opening") == "正在打开视频……"
    assert t("media_svc.pdf_done") == "PDF 处理完成"
    assert t("media_svc.video_no_frames") == "一帧都没能读出来，视频可能已损坏或编码不受支持。"
