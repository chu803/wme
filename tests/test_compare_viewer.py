# -*- coding: utf-8 -*-
"""
阶段 38 测试：图片前后对比查看器。

覆盖三层：
  · services.compare_service 纯函数（模式规整 / 配对加载与对齐 / 三模式渲染 /
    差异摘要 / 落盘导出 / 缩放与对齐工具）。
  · ui.compare_view 回调（未选中 / 缺文件 / 三模式+摘要 / 导出 / 摘要含超分提示）。
  · 页面级 select 回调：
      - ui.tab_batch.on_gallery_select / on_gallery_select_with_mode
        （越界复位、合法索引载入配对）。
      - ui.tab_history.on_select 六元组（图片记录 / 越界 / 视频记录引导）。
      - ui.tab_history.reprocess_and_refresh 五元组（重处理失败/视频复位 + 成功刷新）。

全部使用临时目录，不污染项目缓存；语言锁定 zh-CN 以断言中文提示文案。
"""

import os as _os
import sys as _sys

import numpy as np
import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from utils.i18n import set_language  # noqa: E402

@pytest.fixture(autouse=True)
def _lang_zh():
    """阶段 249：语言设置从**模块级**移入夹具。

    模块级 set_language() 发生在**收集期**（先于一切夹具），conftest 的
    `_isolate_i18n_language` 只能保存已污染的值再还原，挡不住它；移入夹具后
    收集期零副作用，每个用例仍拿到 zh-CN（本夹具 setup 时设置、conftest 的
    teardown 还原）。
    """
    set_language("zh-CN")


# ------------------------------------------------------------------------------
# 共享工具
# ------------------------------------------------------------------------------
def _make_upscaled_pair(tmp_path, size=(80, 100), big=(160, 200)):
    """造一对「原图小、结果大」的图，用于触发 upscaled 判定。"""
    from core.image_utils import save_image

    base_dir = str(tmp_path)
    src = _os.path.join(base_dir, "原图.png")
    res = _os.path.join(base_dir, "结果.png")
    small = np.full((size[0], size[1], 3), 120, dtype=np.uint8)
    big = np.full((big[0], big[1], 3), 120, dtype=np.uint8)
    save_image(src, small, fmt="PNG")
    save_image(res, big, fmt="PNG")
    return src, res


# ==============================================================================
# 第一组：services.compare_service 纯函数
# ==============================================================================
def test_normalize_mode():
    from services import compare_service as cs

    # 合法值（含大小写混合）原样返回。
    assert cs.normalize_mode("SLIDER") == "slider"
    assert cs.normalize_mode("HeatMap") == "heatmap"
    assert cs.normalize_mode("concat") == "concat"
    # 非法 / 空值回退默认模式（config.UI_COMPARE_DEFAULT_MODE="concat"）。
    assert cs.normalize_mode("不存在的模式") == "concat"
    assert cs.normalize_mode(None) == "concat"
    assert cs.normalize_mode("") == "concat"
    # 回退值本身非法时最终落到 concat。
    import config as _cfg
    _old = _cfg.UI_COMPARE_DEFAULT_MODE
    try:
        _cfg.UI_COMPARE_DEFAULT_MODE = "坏模式"
        assert cs.normalize_mode("更坏") == "concat"
    finally:
        _cfg.UI_COMPARE_DEFAULT_MODE = _old


def test_load_pair_success_and_missing():
    from services import compare_service as cs

    import tempfile
    tmp = tempfile.mkdtemp()
    s, r = _make_pair_local(tmp)
    pair = cs.load_pair(s, r)
    assert pair is not None
    assert pair[0].shape[:2] == pair[1].shape[:2]
    # 缺图返回 None。
    assert cs.load_pair(s, _os.path.join(tmp, "缺失.png")) is None
    assert cs.load_pair(None, r) is None


def _make_pair_local(tmp):
    from core.image_utils import save_image
    src = _os.path.join(tmp, "原图.png")
    res = _os.path.join(tmp, "结果.png")
    base = np.full((120, 160, 3), 90, dtype=np.uint8)
    marked = base.copy()
    marked[20:40, 30:90] = 240
    save_image(src, marked, fmt="PNG")
    save_image(res, base, fmt="PNG")
    return src, res


def test_load_pair_align_toggle():
    from services import compare_service as cs

    import tempfile
    tmp = tempfile.mkdtemp()
    src, res = _make_upscaled_pair(tmp)
    # align=True：结果缩回原图尺寸，两图尺寸一致。
    a, b = cs.load_pair(src, res, align=True)
    assert a.shape[:2] == b.shape[:2] == (80, 100)
    # align=False：保留各自原始尺寸，结果更大。
    a2, b2 = cs.load_pair(src, res, align=False)
    assert a2.shape[:2] == (80, 100)
    assert b2.shape[:2] == (160, 200)


def test_render_pair_html_three_modes():
    from services import compare_service as cs

    import tempfile
    tmp = tempfile.mkdtemp()
    src, res = _make_pair_local(tmp)
    for m in ("concat", "slider", "heatmap"):
        html = cs.render_pair_html(src, res, m, heatmap_caption="差异热图")
        assert html and "<" in html, f"{m} 模式渲染为空"
    # 非法模式回退仍出图。
    assert cs.render_pair_html(src, res, "鬼模式")
    # 缺图返回空串。
    assert cs.render_pair_html(src, _os.path.join(tmp, "x.png")) == ""


def test_summarize_pair_metrics():
    from services import compare_service as cs

    import tempfile
    tmp = tempfile.mkdtemp()
    src, res = _make_pair_local(tmp)
    # 白斑约占 20×60 / 120×160 = 6.25%。
    s = cs.summarize_pair(src, res)
    assert s is not None
    assert 0.05 < s["changed_ratio"] < 0.08, s
    assert s["upscaled"] is False
    assert s["width"] == 160 and s["height"] == 120
    assert s["max_diff"] >= 100
    assert 0 <= s["mean_diff"] <= 255
    # 缺图返回 None。
    assert cs.summarize_pair(src, _os.path.join(tmp, "x.png")) is None


def test_summarize_pair_upscaled():
    from services import compare_service as cs

    import tempfile
    tmp = tempfile.mkdtemp()
    src, res = _make_upscaled_pair(tmp)
    s = cs.summarize_pair(src, res)
    assert s is not None
    assert s["upscaled"] is True
    assert s["result_width"] == 200 and s["result_height"] == 160
    assert s["width"] == 100 and s["height"] == 80


def test_export_pair_compare(tmp_path):
    from services import compare_service as cs

    src, res = _make_pair_local(str(tmp_path))
    out = cs.export_pair_compare(src, res)
    assert out and _os.path.isfile(out), "对比图导出失败"
    assert out.endswith(".png")
    # 缺图导出失败返回 None。
    assert cs.export_pair_compare(src, str(tmp_path / "x.png")) is None


def test_shrink_for_view_and_align_pair():
    from services import compare_service as cs

    import tempfile
    tmp = tempfile.mkdtemp()
    src, res = _make_upscaled_pair(tmp)
    from core.image_utils import load_image
    a = load_image(src)
    b = load_image(res)
    # align_pair 以原图为基准。
    aa, bb = cs.align_pair(a, b)
    assert aa.shape[:2] == bb.shape[:2] == (80, 100)
    # shrink_for_view：超限缩放，不超限原样返回（不拷贝语义，仅验证尺寸）。
    big = np.zeros((2000, 3000, 3), dtype=np.uint8)
    small = cs.shrink_for_view(big, max_side=300)
    assert max(small.shape[:2]) <= 300
    same = cs.shrink_for_view(np.zeros((50, 50, 3), dtype=np.uint8), max_side=300)
    assert same.shape[:2] == (50, 50)


# ==============================================================================
# 第二组：ui.compare_view 回调
# ==============================================================================
def test_compare_view_render_pair_no_select():
    from ui.compare_view import render_pair

    h, s = render_pair(None, "concat")
    assert h == "" and "尚未选择" in s


def test_compare_view_render_pair_missing_file(tmp_path):
    from ui.compare_view import render_pair

    h, s = render_pair((str(tmp_path / "a.png"), str(tmp_path / "b.png"), "x"), "concat")
    assert h == "" and "缺少" in s


def test_compare_view_render_pair_three_modes_and_summary(tmp_path):
    from ui.compare_view import render_pair

    src, res = _make_pair_local(str(tmp_path))
    name = "测试图.png"
    for m in ("concat", "slider", "heatmap"):
        h, s = render_pair((src, res, name), m)
        assert h and "<" in h, f"{m} 渲染为空"
        assert name in s, "摘要缺少条目名"
        assert "改动像素" in s, "摘要缺少差异指标"


def test_compare_view_export_pair(tmp_path):
    from ui.compare_view import export_pair

    src, res = _make_pair_local(str(tmp_path))
    f, msg = export_pair((src, res, "测试图.png"))
    assert f and _os.path.isfile(f)
    assert "已导出" in msg
    # 未选中导出失败提示。
    f2, msg2 = export_pair(None)
    assert f2 is None and "尚未选择" in msg2


def test_compare_view_format_summary_upscaled(tmp_path):
    from ui.compare_view import format_summary

    src, res = _make_upscaled_pair(str(tmp_path))
    text = format_summary("大图.png", src, res)
    assert "大图.png" in text
    assert "放大" in text, "超分结果应提示被放大"


# ==============================================================================
# 第三组：页面级 select 回调
# ==============================================================================
def test_tab_batch_on_gallery_select(tmp_path):
    import ui.tab_batch as tb

    src, res = _make_pair_local(str(tmp_path))
    # 注入配对表（与画廊同序）。
    tb._batch_pairs = [(src, res, "a.png"), (src, res, "b.png")]

    class _Evt:
        index = 1

    pair, h, s = tb.on_gallery_select(_Evt())
    assert pair == (src, res, "b.png")
    assert h and "<" in h
    assert "b.png" in s

    # 越界 index → 复位为未选择。
    class _EvtBad:
        index = 99

    p2, h2, s2 = tb.on_gallery_select(_EvtBad())
    assert p2 is None and h2 == "" and "尚未选择" in s2

    # (row, col) 元组索引规整：取第一个分量。
    class _EvtTuple:
        index = (0, 2)

    pair_t, _, _ = tb.on_gallery_select(_EvtTuple())
    assert pair_t == (src, res, "a.png")

    # with_mode 沿用当前模式且输出一致。
    pb, hb, sb = tb.on_gallery_select_with_mode(_Evt(), "heatmap")
    assert pb == (src, res, "b.png") and hb and "<" in hb


def test_tab_history_on_select_image_and_oob(tmp_path):
    import ui.tab_history as th
    from services.cache_service import HistoryRecord

    src, res = _make_pair_local(str(tmp_path))
    rec = HistoryRecord(
        id="i1", ts=1.0, original_name="a.png", kind="image",
        original_path=src, result_path=res,
    )
    th._HISTORY_ID_MAP = [(rec.id, rec.result_path)]
    th._CACHE.get = lambda rid: rec  # type: ignore[assignment]

    class _Evt:
        index = 0

    detail, dl, video, pair, html, summary = th.on_select(_Evt())
    assert video is None, "图片记录不应返回视频路径"
    assert pair == (src, res, "a.png")
    assert html and "<" in html
    assert "a.png" in summary
    assert rec.result_path == dl

    # 越界 → 复位。
    class _EvtBad:
        index = 7

    d2, _, v2, p2, h2, s2 = th.on_select(_EvtBad())
    assert p2 is None and h2 == "" and "尚未选择" in s2


def test_tab_history_on_select_video_guides_to_media(tmp_path):
    import ui.tab_history as th
    from services.cache_service import HistoryRecord

    rec = HistoryRecord(
        id="v1", ts=1.0, original_name="v.mp4", kind="video",
        video_path=str(tmp_path / "v.mp4"), result_path=str(tmp_path / "v.mp4"),
    )
    th._HISTORY_ID_MAP = [(rec.id, rec.result_path)]
    th._CACHE.get = lambda rid: rec  # type: ignore[assignment]

    class _Evt:
        index = 0

    detail, dl, video, pair, html, summary = th.on_select(_Evt())
    assert video == str(tmp_path / "v.mp4")
    assert pair is None and html == ""
    assert summary and ("媒体" in summary or "抽帧" in summary or "video" in summary.lower())


def test_tab_history_reprocess_and_refresh(tmp_path, monkeypatch):
    import ui.tab_history as th
    from services.cache_service import HistoryRecord

    src, res = _make_pair_local(str(tmp_path))
    rec = HistoryRecord(
        id="i1", ts=1.0, original_name="a.png", kind="image",
        original_path=src, result_path=res,
    )

    # 场景 A：重处理成功 → 新结果刷进查看器。
    # 注意：必须用 monkeypatch，否则直接赋值会污染模块全局，影响后续用例。
    monkeypatch.setattr(th, "reprocess",
                        lambda *a, **k: ("重处理完成", [src]))
    monkeypatch.setattr(th._CACHE, "get", lambda rid: rec)
    msg, gallery, pair, html, summary = th.reprocess_and_refresh(
        "i1", "lama", 1.0, 3, False, "concat",
    )
    assert msg == "重处理完成"
    assert pair == (src, res, "a.png")
    assert html and "<" in html
    assert "a.png" in summary

    # 场景 B：记录为视频 → 查看器复位。
    vrec = HistoryRecord(id="v1", ts=1.0, original_name="v.mp4", kind="video",
                         video_path=str(tmp_path / "v.mp4"),
                         result_path=str(tmp_path / "v.mp4"))
    monkeypatch.setattr(th._CACHE, "get", lambda rid: vrec)
    m2, g2, p2, h2, s2 = th.reprocess_and_refresh("v1", "lama", 1.0, 3, False, "concat")
    assert p2 is None and h2 == "" and "尚未选择" in s2

    # 场景 C：原图缺失 → 复位为未选择。
    brec = HistoryRecord(id="b1", ts=1.0, original_name="b.png", kind="image",
                         original_path=str(tmp_path / "no.png"), result_path=res)
    monkeypatch.setattr(th._CACHE, "get", lambda rid: brec)
    m3, g3, p3, h3, s3 = th.reprocess_and_refresh("b1", "lama", 1.0, 3, False, "concat")
    assert p3 is None and h3 == "" and "尚未选择" in s3
