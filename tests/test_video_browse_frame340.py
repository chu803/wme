# -*- coding: utf-8 -*-
"""阶段 340 · 画廊「点第 N 帧」必须真的加载第 N 帧（位置 ≠ 帧号）。

缺陷（读码即确认，本机无需播放视频）：

* `_build_video_browse_gallery` 用 `extract_sampled_frames(..., return_indices=True)` 抽帧，
  标题写的是**真实帧号** `第 {f['idx']} 帧`；
* 而点选处理器 `on_frame_gallery_select` 直接把 `evt.index`——`gr.Gallery` 里的**位置**
  （0..`VIDEO_COMPARE_FRAMES-1`）——写进滑块并交给 `run_video_compare(frame_idx)`，
  后者的 docstring 明写「抽帧序号（0 = 首帧）」。

⇒ 6 格画廊点第 5 格（标题「第 240 帧」）会把滑块设成 4 并渲染第 4 帧。
而 `media.video.browse_info` 承诺「点击任一帧即可加载到下方对比面板」⇒
**它看起来是成功的，只是给了另一帧**——和 339/336 同族：界面说的和做的不一致。
"""
from __future__ import annotations

import ui.tab_media as tm


class _Evt:
    def __init__(self, index):
        self.index = index


# ------------------------------------------------------------------------------
# ① 纯映射
# ------------------------------------------------------------------------------

def test_position_maps_to_real_frame_number():
    indices = [0, 60, 120, 180, 240, 300]
    assert tm.gallery_position_to_frame(indices, 4) == 240
    assert tm.gallery_position_to_frame(indices, 0) == 0
    assert tm.gallery_position_to_frame(indices, 5) == 300


def test_position_out_of_range_or_missing_returns_none():
    indices = [0, 60, 120]
    for bad in (3, 99, None, -1, "x", ()):
        assert tm.gallery_position_to_frame(indices, bad) is None, bad
    assert tm.gallery_position_to_frame([], 0) is None, "没有映射表时不许猜一个位置当帧号"


# ------------------------------------------------------------------------------
# ② 接线：处理器必须用映射后的帧号，而不是 evt.index
# ------------------------------------------------------------------------------

def _prepare(monkeypatch, tmp_path, indices):
    """把「文件存在性」与「解码」都接管掉，只留下位置→帧号这段真逻辑。"""
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    for p in (src, out):
        p.write_bytes(b"x")            # 只需存在，真实解码由桩接管
    # tab_media 的 `_normalize_file_value` 返回**字符串**（与 tab_batch 的列表版不同源，
    # 见 339 的教训：替身要按被测函数的真实契约造，否则拿到的是与主题无关的 TypeError）。
    monkeypatch.setattr(tm, "_normalize_file_value", lambda v: str(v) if v else "")
    monkeypatch.setattr(tm, "_VIDEO_BROWSE_INDICES", {str(out): list(indices)})
    seen = {}

    def fake_compare(src_value, out_value, frame_idx, mode):
        seen["frame_idx"] = frame_idx
        return "<div>对比</div>"

    monkeypatch.setattr(tm, "run_video_compare", fake_compare)
    return str(src), str(out), seen


def test_select_handler_uses_mapped_frame_index(monkeypatch, tmp_path):
    src, out, seen = _prepare(monkeypatch, tmp_path, [0, 60, 120, 180, 240, 300])
    update, html = tm.on_frame_gallery_select(_Evt(4), src, out, "concat")
    assert seen["frame_idx"] == 240, f"渲染了第 {seen['frame_idx']} 帧，用户点的是「第 240 帧」"
    assert html == "<div>对比</div>"
    assert update["value"] == 240, f"滑块应停在真实帧号 240，实得 {update}"


def test_select_handler_refuses_to_show_a_wrong_frame(monkeypatch, tmp_path):
    """拿不到映射时：明说「没选中」，不许随便渲染另一帧冒充用户点的那一帧。"""
    src, out, seen = _prepare(monkeypatch, tmp_path, [0, 60, 120])
    monkeypatch.setattr(tm, "_browse_frame_indices", lambda p: [])
    update, html = tm.on_frame_gallery_select(_Evt(1), src, out, "concat")
    assert "frame_idx" not in seen, "没有映射表却仍然渲染了帧"
    assert html == tm.t("media.video.compare_no_frame"), html
    assert update.get("value") is None, update


def test_select_handler_out_of_range_position_is_safe(monkeypatch, tmp_path):
    src, out, seen = _prepare(monkeypatch, tmp_path, [0, 60, 120])
    update, html = tm.on_frame_gallery_select(_Evt(7), src, out, "concat")
    assert "frame_idx" not in seen
    assert html == tm.t("media.video.compare_no_frame"), html
