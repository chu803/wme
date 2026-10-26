"""阶段 191 · UI 两个最弱标签页的纯函数接缝守卫（承接 187：`ui/tab_batch` 55.5% / `ui/tab_media` 56.4%）。

覆盖两类目标：
  ① **共享归一化收敛**（`ui.components.normalize_file_paths`）：此前 `tab_batch` 与
     `tab_media` 各写一份、对「带 `.name` 的对象」处理不一致；两份实现**都会**把
     `pathlib.Path` 当成「有 `.name` 的对象」而**丢掉目录**（潜在缺陷）⇒ 收敛为唯一实现；
  ② **纯函数接缝**：两个标签页里所有「无 I/O、无 Gradio 服务」的小函数
     （预设标签映射 / ImageEditor 组装 / 掩码查找 / 批内预设解析 / 取消 / 画廊点选 /
     人话时长 / 导出前置校验），逐条锁住行为。

设计取舍：只测**纯函数与可注入依赖**，不启动 Gradio 服务；需要 I/O 的用 `tmp_path`。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config  # noqa: E402
import ui.tab_batch as tb  # noqa: E402
import ui.tab_media as tm  # noqa: E402
from ui.components import normalize_file_paths  # noqa: E402


# ---------------------------------------------------------------------------
# ① 共享归一化：形状与优先级
# ---------------------------------------------------------------------------

def _p(*parts) -> str:
    """平台归一化的路径字符串（Windows 上 `Path("/tmp/a")` 会变成 `\\tmp\\a`）。

    教训：路径类断言**不能**写死 POSIX 分隔符，否则同一份测试在 Windows 上必红。
    """
    return os.fspath(Path(*parts))


@pytest.mark.parametrize("value,expected", [
    (None, []),
    ("a.png", ["a.png"]),
    ("", [""]),
    (Path("/tmp/a.png"), [_p("/tmp/a.png")]),      # ← 关键：不得被截成 "a.png"
    ({}, []),
    ({"path": "/tmp/b.png"}, ["/tmp/b.png"]),
    ({"name": "/tmp/c.png"}, ["/tmp/c.png"]),
    ([], []),
    (["a.png", "b.png"], ["a.png", "b.png"]),
    (["a.png", "a.png"], ["a.png"]),               # 去重保序
    (["a.png", None, {"path": "b.png"}], ["a.png", "b.png"]),
    ([[["deep.png"]]], ["deep.png"]),              # 递归
])
def test_normalize_file_paths_shapes(value, expected):
    assert normalize_file_paths(value) == expected


def test_pathlike_takes_priority_over_dot_name():
    """`Path.name` 只是末段文件名 ⇒ `os.PathLike` 必须**先**判断（否则丢目录）。"""
    assert normalize_file_paths(Path("/tmp/dir/a.png")) == [_p("/tmp/dir/a.png")]
    assert Path("/tmp/dir/a.png").name == "a.png"      # 说明为何要这样排序


def test_object_with_name_attribute_is_used():
    class _Fake:
        def __init__(self, name):
            self.name = name

    assert normalize_file_paths(_Fake("/tmp/tmpfile.png")) == ["/tmp/tmpfile.png"]
    assert normalize_file_paths(_Fake(Path("/tmp/x.png"))) == [_p("/tmp/x.png")]
    # 无可用 name 的对象（顶层）→ 丢弃，不塞垃圾路径（既有契约，见
    # tests/test_tab_batch_helpers.py::test_normalize_file_value_unknown_scalar_returns_empty）
    assert normalize_file_paths(_Fake("")) == []


def test_list_items_keep_the_legacy_str_fallback():
    """**列表内**的未知元素保留 `str(item)` 兜底——与顶层「丢弃」的不对称是既有契约。"""
    assert normalize_file_paths([123]) == ["123"]
    assert normalize_file_paths(3.14) == []          # 顶层：丢弃
    assert normalize_file_paths([3.14, "a.png"]) == ["3.14", "a.png"]


def test_batch_and_media_contracts_are_thin_layers_over_the_shared_helper():
    """两个页面各自只保留「形状约定」，实现必须来自同一处。"""
    v = ["one.png", {"path": "two.png"}, "one.png"]
    assert tb._normalize_file_value(v) == normalize_file_paths(v) == ["one.png", "two.png"]
    # 媒体页的约定 = 取第一个（或空串）
    assert tm._normalize_file_value(v) == "one.png"
    assert tm._normalize_file_value(None) == ""
    assert tm._normalize_file_value([]) == ""
    # 媒体页也受益于 Path 修复（旧实现会返回 "a.png"）
    assert tm._normalize_file_value(Path("/tmp/dir/a.png")) == _p("/tmp/dir/a.png")


# ---------------------------------------------------------------------------
# ② tab_batch 的纯函数接缝
# ---------------------------------------------------------------------------

def test_preset_display_maps_and_passes_through():
    known = next(iter(tb._PRESET_DISPLAY))
    assert tb._preset_display(known) == tb._PRESET_DISPLAY[known]
    assert tb._preset_display("不存在的预设") == "不存在的预设"


def test_build_editor_value_structure_and_alpha():
    img = np.zeros((8, 6, 3), dtype=np.uint8)
    mask = np.zeros((8, 6), dtype=np.uint8)
    mask[2:5, 1:4] = 255
    val = tb._build_editor_value(img, mask)
    assert set(val) == {"background", "layers", "composite"}
    assert val["background"] is img and val["composite"] is None
    layer = val["layers"][0]
    assert layer.shape == (8, 6, 4) and layer.dtype == np.uint8
    assert tuple(layer[0, 0, :3]) == (255, 59, 48)          # 固定涂抹色
    assert np.array_equal(layer[:, :, 3], mask)             # alpha 就是掩码


def test_cancel_batch_three_branches(monkeypatch):
    """取消按钮的三条分支（阶段 336 从两条变三条）。

    旧夹具只给 `cancel()`，因为旧实现只判「句柄在不在」⇒ 批跑结束后按取消也会谎称
    「已发送取消信号」。新契约要看队列真实状态，所以夹具必须提供 `summary()`；
    这是**改行为连带打破写死旧行为的夹具**（LESSONS #81）：同步夹具，不是放宽断言。
    """
    monkeypatch.setattr(tb, "_CURRENT_QUEUE", None)
    assert tb.cancel_batch()  # ① 无队列：给出「无可取消」文案（非空即通过语义）

    class _Q:
        def __init__(self, pending=1, running=0):
            self.called = False
            self._s = {"pending": pending, "running": running}

        def cancel(self):
            self.called = True

        def summary(self):
            return dict(self._s)

    # ② 有活干：真的取消，并如实回报。
    q = _Q(pending=2)
    monkeypatch.setattr(tb, "_CURRENT_QUEUE", q)
    msg = tb.cancel_batch()
    assert q.called and msg

    # ③ 队列已排空（跑完了）：不许声称发了信号，也不该调 cancel()。
    drained = _Q(pending=0, running=0)
    monkeypatch.setattr(tb, "_CURRENT_QUEUE", drained)
    idle_msg = tb.cancel_batch()
    assert not drained.called, "空队列不该调 cancel()"
    assert idle_msg and idle_msg != msg, "空队列必须换一句真话，不能复用「已发送取消信号」"


@pytest.mark.parametrize("index,expect_pair", [
    (None, False), (0, False), (5, False),   # 无产出 / 越界 → 不选中
])
def test_gallery_select_without_pairs_returns_hint(monkeypatch, index, expect_pair):
    monkeypatch.setattr(tb, "_batch_pairs", [])
    evt = type("E", (), {"index": index})()
    pair, html, summary = tb.on_gallery_select(evt)
    assert pair is None and html == "" and summary
    pair2, _, _ = tb.on_gallery_select_with_mode(evt, config.UI_COMPARE_DEFAULT_MODE)
    assert pair2 is None


def test_gallery_select_uses_pair_and_mode(monkeypatch):
    """在范围内应把对应配对 + **用户所选模式**交给 render_pair（而不是默认模式）。"""
    monkeypatch.setattr(tb, "_batch_pairs", [{"id": 1}, {"id": 2}])
    seen = {}

    def fake_render(pair, mode):
        seen["pair"], seen["mode"] = pair, mode
        return "<html>", "summary"

    monkeypatch.setattr(tb, "render_pair", fake_render)
    evt = type("E", (), {"index": (1, 0)})()      # Gradio 部分版本给 (row, col)
    pair, html, summary = tb.on_gallery_select_with_mode(evt, "slider")
    assert pair == {"id": 2} and (html, summary) == ("<html>", "summary")
    assert seen == {"pair": {"id": 2}, "mode": "slider"}


def test_mask_path_for_finds_same_stem(tmp_path):
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"x")
    mask = tmp_path / ("pic" + tb._MASK_EXTS[0])
    mask.write_bytes(b"m")
    got = tb._mask_path_for(str(tmp_path), str(img))
    assert got is not None and os.path.basename(got) == "pic" + tb._MASK_EXTS[0]
    # 找不到 / 目录不存在 → None
    assert tb._mask_path_for(str(tmp_path), str(tmp_path / "other.jpg")) is None
    assert tb._mask_path_for(str(tmp_path / "nope"), str(img)) is None
    assert tb._mask_path_for("", str(img)) is None


def test_resolve_batch_preset_four_branches(monkeypatch):
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    # ① 关闭智能推荐 → 直接用 fallback，不调用推荐器
    called = []
    monkeypatch.setattr(tb, "recommend_preset",
                        lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError))
    assert tb._resolve_batch_preset(img, "n", 0.5, False, "ai_grid") == ("ai_grid", None, None)
    assert not called
    # ② 推荐到具体预设 → 覆盖 fallback
    class _Rec:
        preset = "ai_text_heavy"
        confidence = 0.9
        triggered = ["edge_text"]

        def to_dict(self):
            return {"preset": self.preset}

    monkeypatch.setattr(tb, "recommend_preset", lambda *a, **k: _Rec())
    preset, rec, recommended = tb._resolve_batch_preset(img, "n", 0.5, True, "general")
    assert (preset, recommended) == ("ai_text_heavy", "ai_text_heavy")
    assert rec == {"preset": "ai_text_heavy"}
    # ③ 推荐回「默认预设」视为无强信号 → 回退 fallback
    class _Rec2(_Rec):
        preset = config.RECOMMEND_DEFAULT_PRESET

    monkeypatch.setattr(tb, "recommend_preset", lambda *a, **k: _Rec2())
    preset2, _, recommended2 = tb._resolve_batch_preset(img, "n", 0.5, True, "ai_grid")
    assert preset2 == "ai_grid" and recommended2 == config.RECOMMEND_DEFAULT_PRESET
    # ④ 推荐抛异常 → 回退 fallback，不冒泡
    monkeypatch.setattr(tb, "recommend_preset",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tb._resolve_batch_preset(img, "n", 0.5, True, "ai_grid") == ("ai_grid", None, None)


def test_resolve_batch_preset_fallback_defaults_to_config(monkeypatch):
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    assert tb._resolve_batch_preset(img, "n", 0.5, False, "")[0] == config.RECOMMEND_DEFAULT_PRESET


# ---------------------------------------------------------------------------
# ③ tab_media 的纯函数接缝
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sec,expected", [
    (0, "0.0 秒"),
    (30.5, "30.5 秒"),
    (60, "1 分 0 秒"),
    (90, "1 分 30 秒"),
    (3600, "60 分 0 秒"),
    (-5, "0.0 秒"),        # 负数被夹到 0（不出现「-5.0 秒」）
])
def test_human_seconds(sec, expected):
    assert tm._human_seconds(sec) == expected


def test_preset_choice_label_falls_back_when_key_missing():
    """有 i18n 键则用译文；缺键时用传入的 display，**绝不返回裸键名**。"""
    from core.detection_presets import list_presets

    names = list_presets()
    assert names, "至少应存在一个检测预设"
    label = tm._preset_choice_label(names[0], "兜底显示名")
    assert label and not label.startswith("detect.preset.")
    assert tm._preset_choice_label("绝不存在_191", "兜底显示名") == "兜底显示名"


def test_media_export_helpers_reject_missing_files(tmp_path):
    """导出前置校验：路径缺失/不存在时**不抛异常**，并且要说得出为什么没导出（阶段 343）。

    这条以前断言的是 `is None`（单值返回）⇒ 343 把契约改成 `(文件, 日志)` 二元组后
    它反向钉住了「失败只 return None、界面零反馈」的旧形态（同 #318/#81：
    改行为要同步写死旧行为的夹具，而不是给实现加兼容分支）。
    """
    for path in (None, str(tmp_path / "nope.mp4")):
        got, msg = tm.export_sheet_file(path)
        assert got is None, path
        assert msg, f"缺文件时也必须给一句真话（{path!r}）"

    for pair in ((None, None), (str(tmp_path / "nope.mp4"), str(tmp_path / "nope2.mp4"))):
        got, msg = tm.export_frame_file(pair[0], pair[1], 0, "concat")
        assert got is None, pair
        assert msg, f"缺文件时也必须给一句真话（{pair!r}）"


def test_on_frame_gallery_select_without_index_or_video(monkeypatch, tmp_path):
    evt = type("E", (), {"index": None})()
    _upd, msg = tm.on_frame_gallery_select(evt, None, None, "concat")
    assert msg
    evt2 = type("E", (), {"index": 2})()
    _upd2, msg2 = tm.on_frame_gallery_select(evt2, "a.mp4", str(tmp_path / "missing.mp4"), "concat")
    assert msg2
