# -*- coding: utf-8 -*-
"""阶段 343 · 导出按钮必须说得出「成没成」。

缺陷：`ui/tab_media.py` 的 `export_frame_file` / `export_sheet_file` 所有失败路径都是
`return None`，而两个按钮的 `outputs` 只有一个 `gr.File` ⇒
「点了没反应」与「成功」在界面上**完全不可区分**（用户只会反复点，或者以为软件坏了）。

最能说明问题的一条事实：`media.video.export_no_video` 与 `media.video.export_done`
**两条文案早就中英双语写好在 i18n 里**，但全仓 `grep` 下来没有任何代码用过它们
⇒ 反馈这件事不是没想到，是**停在半路**（文案先就位、接线没人补）。
本阶段就是把它接上：`(文件或 None, 写进视频日志的一句真话)`。
"""
from __future__ import annotations

import os

import ui.tab_media as tm
from utils.i18n import t


def _files(tmp_path, monkeypatch, *, have_out=True):
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.write_bytes(b"x")
    if have_out:
        out.write_bytes(b"x")
    monkeypatch.setattr(tm, "_normalize_file_value", lambda v: str(v) if v else "")
    return str(src), (str(out) if have_out else "")


def test_missing_result_video_says_so(tmp_path, monkeypatch):
    _src, out = _files(tmp_path, monkeypatch, have_out=False)
    got, msg = tm.export_frame_file("whatever", out, 0, "concat")
    assert got is None
    assert msg == t("media.video.export_no_video"), msg
    assert msg and "media.video" not in msg, "t() 回退成键名 ⇒ 用户会看到裸键"


def test_success_reports_the_written_path(tmp_path, monkeypatch):
    src, out = _files(tmp_path, monkeypatch)
    monkeypatch.setattr(tm, "export_frame_compare",
                        lambda s, o, i: f"/tmp/帧_{i}.png")
    got, msg = tm.export_frame_file(src, out, 3, "concat")
    assert got == "/tmp/帧_3.png", got
    assert msg == t("media.video.export_done", path=got), msg
    assert got in msg, "反馈里必须带上产物路径，否则用户不知道导出了什么"


def test_service_returning_none_is_not_silence(tmp_path, monkeypatch):
    """服务层「没能写出」返回 None ⇒ 界面不能跟着沉默。"""
    src, out = _files(tmp_path, monkeypatch)
    monkeypatch.setattr(tm, "export_frame_compare", lambda s, o, i: None)
    got, msg = tm.export_frame_file(src, out, 1, "concat")
    assert got is None
    assert msg == t("media.video.export_fail", exc=t("media.video.compare_no_frame")), msg


def test_service_exception_is_surfaced_not_swallowed(tmp_path, monkeypatch):
    src, out = _files(tmp_path, monkeypatch)

    def boom(s, o, i):
        raise RuntimeError("编码器不兼容当前分辨率")

    monkeypatch.setattr(tm, "export_frame_compare", boom)
    got, msg = tm.export_frame_file(src, out, 1, "concat")
    assert got is None
    assert msg.startswith("❌") and "编码器不兼容当前分辨率" in msg, msg


def test_sheet_export_has_the_same_two_channels(tmp_path, monkeypatch):
    """帧拼图按钮同一条契约（以前也只会 `return None`）。"""
    _src, out = _files(tmp_path, monkeypatch)
    monkeypatch.setattr(tm, "export_contact_sheet", lambda o: os.path.join("/tmp", "sheet.png"))
    got, msg = tm.export_sheet_file(out)
    assert got == os.path.join("/tmp", "sheet.png")
    assert "sheet.png" in msg

    monkeypatch.setattr(tm, "export_contact_sheet", lambda o: None)
    got2, msg2 = tm.export_sheet_file(out)
    assert got2 is None and msg2.startswith("❌"), (got2, msg2)


def test_export_feedback_keys_are_now_referenced():
    """防退化：那两个双语键以前是「写了没人用」，本阶段起必须真的被引用。"""
    src = "".join(open(tm.__file__, encoding="utf-8").readlines())
    for key in ("media.video.export_no_video", "media.video.export_done",
                "media.video.export_fail"):
        assert f't("{key}"' in src, f"{key} 又变回「有文案没接线」的孤儿键"
