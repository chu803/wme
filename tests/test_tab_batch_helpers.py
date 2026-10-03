"""阶段 164 · `ui/tab_batch` 纯逻辑助手契约守卫。

背景
----
`ui/tab_batch` 是覆盖度荒漠榜次席（644 代码 / 40 引用测试）。既有覆盖集中在 `run_batch`
的队列接线 / 历史登记 / 结果导出 / 对比视图；而**批量页真正的输入规整与决策助手**
（`_normalize_file_value` / `_mask_path_for` / `_resolve_batch_preset` / `_preset_display` /
`_build_editor_value`）**此前零专属测试**——它们决定了「哪些文件被处理、手动模式的掩码从哪读、
每张图用哪个检测预设」，是批量页最易出静默错的地方。

本文件把上述纯助手的行为钉成可判定契约（探针量化 → 写死期望值）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import ui.tab_batch as tb  # noqa: E402
from utils.i18n import get_language, set_language  # noqa: E402

#: 阶段 164 新增的批量任务级 i18n 键（此前 `_process_one` 的状态/错误消息硬编码中文）
_TASK_KEYS = (
    "batch.task_reading_mask", "batch.task_cache_hit_msg", "batch.task_cache_hit_status",
    "batch.task_mask_read", "batch.task_saving", "batch.task_done", "batch.task_skip",
    "batch.task_read_fail", "batch.task_no_mask_file", "batch.task_no_region",
    "batch.task_no_result", "batch.task_detect_done", "batch.task_detect_done_cov",
    "batch.list_sep", "batch.progress_queue", "batch.progress_done", "batch.caption_quality",
)


def _suspect(s: str):
    """加强字符类：CJK 汉字 + CJK 标点 + 半/全角形式。"""
    for c in s:
        o = ord(c)
        if 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            return c
    return None


# --------------------------------------------------------------------------- #
# 1 · _normalize_file_value：Gradio File 返回值 → 纯路径字符串列表
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value, expect", [
    (None, []),
    ("a.png", ["a.png"]),
    ({"path": "a.png"}, ["a.png"]),
    ({"name": "b.png"}, ["b.png"]),
    ({}, []),
    (["a.png", "b.png", "a.png"], ["a.png", "b.png"]),          # 去重保序
    ([{"path": "x.png"}, None, "y.png", {"name": "z.png"}], ["x.png", "y.png", "z.png"]),
    ([123], ["123"]),                                           # 未知元素兜底 str
    (("p.png", "q.png"), ["p.png", "q.png"]),                   # tuple 也接受
])
def test_normalize_file_value(value, expect):
    assert tb._normalize_file_value(value) == expect


def test_normalize_file_value_unknown_scalar_returns_empty():
    """未识别的标量类型（既非 str/dict/list）⇒ 空列表（不静默塞垃圾路径）。"""
    assert tb._normalize_file_value(3.14) == []


# --------------------------------------------------------------------------- #
# 2 · _mask_path_for：按图片主名在同目录文件夹里找掩码
# --------------------------------------------------------------------------- #
def _touch(p: Path):
    p.write_bytes(b"x")
    return p


def test_mask_path_for_non_dir_returns_none(tmp_path):
    assert tb._mask_path_for(str(tmp_path / "nope"), str(tmp_path / "a.png")) is None
    assert tb._mask_path_for("", str(tmp_path / "a.png")) is None


def test_mask_path_for_matches_by_stem_across_ext(tmp_path):
    m = _touch(tmp_path / "photo.png")
    got = tb._mask_path_for(str(tmp_path), str(tmp_path / "photo.jpg"))
    assert got is not None and Path(got).name == "photo.png"


def test_mask_path_for_none_when_missing(tmp_path):
    assert tb._mask_path_for(str(tmp_path), str(tmp_path / "absent.jpg")) is None


def test_mask_path_for_deterministic_ext_priority(tmp_path):
    _touch(tmp_path / "p.jpg")
    _touch(tmp_path / "p.png")
    got = tb._mask_path_for(str(tmp_path), str(tmp_path / "p.bmp"))
    assert Path(got).suffix == ".png", "多候选时按 _MASK_EXTS 顺序取首个（确定性）"


# --------------------------------------------------------------------------- #
# 3 · _resolve_batch_preset：每张图用哪个检测预设
# --------------------------------------------------------------------------- #
def _rec(preset, confidence=0.9):
    return SimpleNamespace(preset=preset, confidence=confidence, triggered=["t"],
                           to_dict=lambda: {"preset": preset, "confidence": confidence})


def test_resolve_preset_auto_off_uses_fallback(monkeypatch):
    monkeypatch.setattr(tb, "recommend_preset",
                        lambda *a, **k: pytest.fail("auto_preset=False 不应调用推荐器"))
    got, rec, rp = tb._resolve_batch_preset(np.zeros((4, 4, 3), np.uint8),
                                            "n", 0.5, False, "ai_grid")
    assert (got, rec, rp) == ("ai_grid", None, None)


def test_resolve_preset_auto_on_specific_recommendation_wins(monkeypatch):
    monkeypatch.setattr(tb, "recommend_preset", lambda *a, **k: _rec("ai_grid"))
    got, rec, rp = tb._resolve_batch_preset(np.zeros((4, 4, 3), np.uint8),
                                            "n", 0.5, True, "general")
    assert got == "ai_grid" and rp == "ai_grid" and rec["preset"] == "ai_grid"


def test_resolve_preset_recommended_default_falls_back(monkeypatch):
    """推荐回默认预设 = 无强信号 ⇒ 回退下拉所选（不覆盖用户的整批共享预设）。"""
    monkeypatch.setattr(tb, "recommend_preset",
                        lambda *a, **k: _rec(config.RECOMMEND_DEFAULT_PRESET))
    got, rec, rp = tb._resolve_batch_preset(np.zeros((4, 4, 3), np.uint8),
                                            "n", 0.5, True, "ai_corner")
    assert got == "ai_corner", "推荐=默认 ⇒ 用 fallback"


def test_resolve_preset_recommender_failure_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rec fail")
    monkeypatch.setattr(tb, "recommend_preset", boom)
    got, rec, rp = tb._resolve_batch_preset(np.zeros((4, 4, 3), np.uint8),
                                            "n", 0.5, True, "ai_grid")
    assert (got, rec, rp) == ("ai_grid", None, None), "推荐异常不得拖垮批次"


def test_resolve_preset_empty_fallback_uses_config_default(monkeypatch):
    monkeypatch.setattr(tb, "recommend_preset", lambda *a, **k: _rec("ai_grid"))
    got, _, _ = tb._resolve_batch_preset(np.zeros((4, 4, 3), np.uint8),
                                         "n", 0.5, False, "")
    assert got == config.RECOMMEND_DEFAULT_PRESET


# --------------------------------------------------------------------------- #
# 4 · _preset_display / _build_editor_value
# --------------------------------------------------------------------------- #
def test_preset_display_known_and_unknown():
    assert tb._preset_display("definitely-not-a-preset") == "definitely-not-a-preset"
    # 已知预设名应映射到内置 display（非原样返回）
    any_name = next(iter(tb._PRESET_DISPLAY))
    assert tb._preset_display(any_name) == tb._PRESET_DISPLAY[any_name]


def test_build_editor_value_layers_alpha_equals_mask():
    img = np.full((6, 8, 3), 100, np.uint8)
    mask = np.zeros((6, 8), np.uint8)
    mask[1:3, 2:5] = 255
    ev = tb._build_editor_value(img, mask)
    assert set(ev) == {"background", "layers", "composite"}
    assert ev["background"] is img
    assert len(ev["layers"]) == 1
    layer = ev["layers"][0]
    assert layer.shape == (6, 8, 4)
    assert (layer[:, :, 3] == mask).all(), "图层 alpha 必须等于掩码"
    assert tuple(layer[0, 0, :3]) == (255, 59, 48), "待擦除标记色应为番茄红"


# --------------------------------------------------------------------------- #
# 5 · i18n：任务级消息必须双语（阶段 164 修复点）
# --------------------------------------------------------------------------- #
@pytest.fixture
def restore_lang():
    lang = get_language()
    yield
    set_language(lang)


@pytest.mark.parametrize("key", _TASK_KEYS)
def test_task_keys_are_bilingual(restore_lang, key):
    from utils.i18n import t
    set_language("zh-CN")
    zh = t(key)
    set_language("en-US")
    en = t(key)
    assert zh != key and en != key, f"{key} 缺翻译（回退成键名）"
    assert _suspect(zh) is not None, f"{key} 的 zh 值应含中文/全角：{zh!r}"
    assert _suspect(en) is None, f"{key} 的 en 值仍含中文/全角（泄漏）：{en!r}"


def test_no_userfacing_hardcoded_cjk_in_tab_batch():
    """源码级：tab_batch 不得有用户可见的硬编码 CJK/全角字面量。

    剔除 docstring、`t(...)` 实参（键名/模板）与 `log.*(...)` 实参（开发者日志）。
    """
    import ast
    src = (ROOT / "ui" / "tab_batch.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc, excl = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = getattr(n, "body", [])
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                doc.add(id(b[0].value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            is_t = isinstance(f, ast.Name) and f.id == "t"
            is_log = (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                      and f.value.id == "log")
            if is_t or is_log:
                for a in n.args:
                    for sub in ast.walk(a):
                        if isinstance(sub, ast.Constant):
                            excl.add(id(sub))
    bad = [(n.lineno, n.value) for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and id(n) not in doc and id(n) not in excl and _suspect(n.value)]
    assert not bad, f"tab_batch 仍有用户可见硬编码非 ASCII 文案：{bad}"


def test_process_one_error_wrapper_is_localized(restore_lang):
    """`_process_one` 抛出的错误会经 `batch.log_fail` 渲染进度日志 ⇒ **外层模板**必须随语言切换。

    注意：`{reason}` 由 `utils.exceptions` 的异常文案填充，那是**另一处独立泄漏**
    （阶段 164 登记为边界，见文档），故此处只钉 tab_batch 自己的外层模板。
    """
    from types import SimpleNamespace

    def boom(_p):
        raise tb.UnsupportedFormatError("unsupported ext")

    import ui.tab_batch as _tb
    orig = _tb.validate_image_file
    _tb.validate_image_file = boom

    def _fail():
        _tb._process_one(SimpleNamespace(path="x.png", name="x.png"),
                         lambda *a, **k: None, mode="auto", engine="opencv",
                         strength=0.5, dilate=0, feather=0, threshold=128,
                         denoise=0, sensitivity=0.5, mask_folder="")
    try:
        set_language("en-US")
        with pytest.raises(ValueError) as ei:
            _fail()
        assert str(ei.value).startswith("Skipped ("), f"en 外层模板未本地化：{ei.value!r}"

        set_language("zh-CN")
        with pytest.raises(ValueError) as ei2:
            _fail()
        assert str(ei2.value).startswith("跳过（"), f"zh 外层模板错误：{ei2.value!r}"
    finally:
        _tb.validate_image_file = orig

