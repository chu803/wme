# -*- coding: utf-8 -*-
"""阶段 355 · 「做了一半」的事必须在界面上说出来（第三轮审计最后两条）。

⑤ 对比图导出丢失败项不报告：`except → continue` 只 log，消息只报成功数
   ⇒ ZIP 比历史页少几张却没人知道。
⑥ 「保存为默认」写盘失败不可见：`config.save_user_overrides` 返回 False 只 log.warning，
   而界面文案承诺「保存为默认…下次启动自动生效」、仍显示「✅ 已应用」⇒ 用户以为存上了。
"""
from __future__ import annotations

import pytest

import ui.tab_settings as ts


# ── ⑥ 「保存为默认」写盘失败 ────────────────────────────────────────────────
def test_save_default_failure_shows_in_the_info(monkeypatch):
    monkeypatch.setattr(ts.config, "save_user_overrides", lambda *a, **k: False)
    out = ts.apply_superres_config(True, 2, "auto", True)
    info = out[-1]
    assert "save_default_failed" not in info, info          # 应是**译文**，不是键名
    assert ts.t("settings.snap.msg.save_default_failed") in info, (
        f"写盘失败没有出现在界面信息里：{info!r}")


def test_save_default_success_has_no_warning(monkeypatch):
    """正向对照：写盘成功时不该多出警告（别把每次都变成告警）。"""
    monkeypatch.setattr(ts.config, "save_user_overrides", lambda *a, **k: True)
    out = ts.apply_superres_config(True, 2, "auto", True)
    info = out[-1]
    assert ts.t("settings.snap.msg.save_default_failed") not in info, info


# ── ⑤ 对比图导出丢失败项 ───────────────────────────────────────────────────
class _Rec:
    def __init__(self, rid, name, orig=None, res=None):
        self.id = rid
        self.original_name = name
        self.original_path = orig
        self.result_path = res
        self.status = "success"


def test_compare_export_reports_failed_items(monkeypatch, tmp_path):
    """一条成功、一条失败 ⇒ 消息里必须同时出现成功数与失败数。"""
    # 处理器要求「原图与结果都还在」⇒ 造两个真实文件
    ok_o = tmp_path / "ok_o.png"
    ok_r = tmp_path / "ok_r.png"
    bad_o = tmp_path / "bad_o.png"
    bad_r = tmp_path / "bad_r.png"
    for p in (ok_o, ok_r, bad_o, bad_r):
        p.write_bytes(b"x")
    recs = [_Rec("r1", "ok.png", str(ok_o), str(ok_r)),
            _Rec("r2", "bad.png", str(bad_o), str(bad_r))]

    class _StubCache:
        def list_records(self, *a, **kw):
            return recs

    monkeypatch.setattr(ts, "_CACHE", _StubCache())

    def fake_export_compare(original, result, **kw):
        if "bad" in str(original):
            raise RuntimeError("模拟失败")
        return "/tmp/cmp_ok.png"

    monkeypatch.setattr(ts, "export_compare", fake_export_compare)
    monkeypatch.setattr(ts, "export_paths_as_zip",
                        lambda paths, names=None, **kw: "/tmp/out.zip")

    msg, zip_path = ts.on_export_compares(gap=8)
    assert zip_path == "/tmp/out.zip", msg
    assert ts.t("settings.snap.msg.compare_zip_partial", n=1, failed=1,
                names="bad.png", path="/tmp/out.zip") in msg, (
        f"导出失败项没有出现在消息里：{msg!r}")


def test_compare_export_all_ok_has_no_partial_warning(monkeypatch, tmp_path):
    """正向对照：全部成功时不出现 partial 文案。"""
    o = tmp_path / "ok_o.png"
    r = tmp_path / "ok_r.png"
    for p in (o, r):
        p.write_bytes(b"x")
    recs = [_Rec("r1", "ok.png", str(o), str(r))]

    class _StubCache:
        def list_records(self, *a, **kw):
            return recs

    monkeypatch.setattr(ts, "_CACHE", _StubCache())
    monkeypatch.setattr(ts, "export_compare",
                        lambda original, result, **kw: "/tmp/cmp_ok.png")
    monkeypatch.setattr(ts, "export_paths_as_zip",
                        lambda paths, names=None, **kw: "/tmp/out.zip")

    msg, _zip = ts.on_export_compares(gap=8)
    assert ts.t("settings.snap.msg.compare_zip_ok", n=1, path="/tmp/out.zip") in msg, msg
    assert ts.t("settings.snap.msg.compare_zip_partial", n=1, failed=0,
                names="", path="/tmp/out.zip") not in msg, msg
