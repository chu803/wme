# -*- coding: utf-8 -*-
"""
阶段 283 · `ui/tab_single` 深度续审（5j 复审链延续，接 276–282）。

首轮审计（阶段 110 接线契约 + 阶段 190 耗时口径）已覆盖：
  · **接线契约**：`len(inputs) == len(形参) − 内部尾部形参`，`record` 必须是后缀；
    `dual_run` 控件值真的到达 `inpaint`；默认路径登记历史（110 的 P0 回归守卫）；
  · `_SINGLE_WIDGET_PARAMS` 与形参表前缀一致；五元组返回与 docstring 声明一致（组 D）；
  · **耗时口径**：`single.msg.elapsed` 双语同形、起止点顺序断言、`_compose_status` 三段拼接；
  · `on_enhance` 的三种退化输入（None/尺寸不等/不存在模式）优雅降级。

首轮聚焦「接线 + 耗时」，但**遗漏了 `process_single` 九条返回分支的返回值不变量**
（每条分支回传什么图 / download 是否为空 / status 是否对应文案）与 `_extract_image`
的输入形态矩阵。本轮按「契约输入类别枚举」补齐。

本轮钉死的不变量（探针实证全部正确，零真缺陷）：
  · `_extract_image` 十一类输入形态：None / dict(有无 background) / tuple / list / ndarray /
    非法标量（str·int）；**空 tuple·空 list ⇒ None**（不 IndexError）；
  · `process_single` 九条返回分支**恒为五元组**，且
    「失败分支 ⇒ result=None 且 download=None，但原图仍回传（除 no_image）」；
  · `no_image` 分支额外连原图也不回传（全 None）；
  · **save_fail 成功分支**：修复成功仅落盘失败 ⇒ 仍回传结果图（download 为 None）；
  · `UI_COMPARE_VIEW` 开 ⇒ compare 第三元组非 None；关 ⇒ None；
  · `record=False` 不调 `add_success`；`record=True` 调一次；
  · 缓存 `add_success` 返回 None 或抛错 ⇒ **主流程不受影响**（仍回传结果与 download）；
  · 超分放大（后处理改变 shape）⇒ 状态文案追加 `auto.msg.upscaled` 且 factor 正确；
  · `on_enhance` 四类模式均返回非空 HTML；None 原图/结果 ⇒ `compare.no_result`。

【附带修复·声明漂移】`_SINGLE_WIDGET_PARAMS` 的 docstring 引用了已被合并掉的
  `tests/test_tab_single_audit110.py`；阶段 283 更正为现行 `test_ui_wiring_contract110.py`
  （组 A + 组 D），并点明组 D 的前缀守卫，避免后人去改一个不存在的文件。

零真缺陷（与 276–282 同构）：首轮遗漏的是「未钉边界」而非代码缺陷。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui.tab_single as ts  # noqa: E402
from utils.exceptions import (  # noqa: E402
    EmptyMaskError,
    ImageTooLargeError,
    format_exception_for_ui,
)
from utils.i18n import t as _t  # noqa: E402

IMG = np.zeros((8, 8, 3), np.uint8)
EV = {"background": IMG}


class _Stubs:
    """替换 `process_single` 的重依赖（validate/build_mask/inpaint/postprocess/save/_CACHE/config）。"""

    def __init__(self):
        self.orig = {}

    def set(self, name, val):
        self.orig[name] = getattr(ts, name)
        setattr(ts, name, val)

    def restore(self):
        for k, v in self.orig.items():
            setattr(ts, k, v)
        self.orig = {}


def _raise(exc):
    """返回一个「调用即抛 exc」的替身。"""
    def _f(*a, **k):
        raise exc
    return _f


@pytest.fixture
def stubs():
    """成功路径的默认桩：掩码**非空** ⇒ 不会误触 no_mask 早退。"""
    s = _Stubs()
    s.set("validate_numpy_image", lambda img: None)
    s.set("build_mask_from_editor", lambda ev, **kw: np.full((8, 8), 255, np.uint8))
    s.set("count_mask_pixels", lambda m: int((m > 0).sum()))
    s.set("inpaint", lambda *a, **k: np.zeros((8, 8, 3), np.uint8))
    s.set("pp_postprocess", lambda r, **k: r)
    s.set("save_result", lambda *a, **k: "/tmp/out.png")
    s.set("config", type("C", (), {
        "UI_COMPARE_VIEW": False,
        "DEFAULT_OUTPUT_FORMAT": "png",
        "DEFAULT_OUTPUT_QUALITY": 95,
    })())
    s.set("_CACHE", type("C", (), {
        "add_success": lambda slf, *a, **k: "rec1",
        "update_result_path": lambda slf, *a, **k: None,
    })())
    try:
        yield s
    finally:
        s.restore()


def _call(**kw):
    base = dict(editor_value=EV, engine="lama", strength=1.0, dilate=0, feather=0,
                threshold=128, denoise=0)
    base.update(kw)
    return ts.process_single(**base)


# --------------------------------------------------------------------------- #
# 1 · _extract_image 输入形态矩阵
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value, expect_none", [
    (None, True),
    ({"background": IMG}, False),
    ({"layers": []}, True),        # dict 但无 background
    ({}, True),
    ((IMG,), False),               # Gradio 4.x 元组
    ((), True),                    # 空元组 ⇒ None（不得 IndexError）
    ([IMG], False),
    ([], True),                    # 空列表 ⇒ None
    (IMG, False),                  # 裸 ndarray
])
def test_extract_image_shapes(value, expect_none):
    got = ts._extract_image(value)
    assert (got is None) is expect_none, f"{type(value).__name__} 形态判定错：{got!r}"


@pytest.mark.parametrize("junk", ["a-string", 123, 4.5, object()])
def test_extract_image_rejects_unknown_scalars(junk):
    """无法识别的标量类型 ⇒ None（不得把垃圾当图片传出）。"""
    assert ts._extract_image(junk) is None


# --------------------------------------------------------------------------- #
# 2 · process_single 九条返回分支：恒五元组 + 语义对账
# --------------------------------------------------------------------------- #
def test_no_image_returns_all_none(stubs):
    """no_image 分支：连原图都不回传（全 None 五元组）。"""
    out = _call(editor_value=None)
    assert len(out) == 5
    assert out == (None, None, None, _t("single.msg.no_image"), None)


@pytest.mark.parametrize("patch, expect_status", [
    # (打桩方式, 期望 status 全文)——直接给出期望文案，避免反向拼串造成脆弱断言。
    ("too_large", _t("single.msg.too_large",
                     exc=format_exception_for_ui(ImageTooLargeError("too big")))),
    ("bad_data", _t("single.msg.bad_data",
                    exc=format_exception_for_ui(ValueError("bad")))),
    ("mask_fail", _t("single.msg.mask_fail",
                     exc=format_exception_for_ui(RuntimeError("mask boom")))),
    ("no_mask", _t("single.msg.no_mask")),
    ("empty_mask_error", _t("single.msg.no_mask")),
    ("no_model", _t("single.msg.no_model",
                    exc=format_exception_for_ui(FileNotFoundError("no lama")))),
    ("generic_fail", _t("single.msg.fail",
                        exc=format_exception_for_ui(RuntimeError("boom")))),
])
def test_failure_branches_return_orig_but_no_result(stubs, patch, expect_status):
    """失败分支：原图仍回传（用户看得见自己上传的图），但结果与 download 为空。"""
    if patch == "too_large":
        ts.validate_numpy_image = _raise(ImageTooLargeError("too big"))
    elif patch == "bad_data":
        ts.validate_numpy_image = _raise(ValueError("bad"))
    elif patch == "mask_fail":
        ts.build_mask_from_editor = _raise(RuntimeError("mask boom"))
    elif patch == "no_mask":
        ts.count_mask_pixels = lambda m: 0
    elif patch == "empty_mask_error":
        ts.inpaint = _raise(EmptyMaskError("empty"))
    elif patch == "no_model":
        ts.inpaint = _raise(FileNotFoundError("no lama"))
    else:
        ts.inpaint = _raise(RuntimeError("boom"))

    out = _call()
    assert len(out) == 5, f"{patch} 分支必须回传五元组"
    assert out[0] is IMG, f"{patch} 分支应保留原图"
    assert out[1] is None, f"{patch} 分支不得回传结果图"
    assert out[4] is None, f"{patch} 分支不得给出下载文件"
    assert out[3] == expect_status, f"{patch} 分支 status 文案不符：{out[3]!r}"


def test_save_failure_still_returns_result_image(stubs):
    """**成功分支**：修复成功但落盘失败 ⇒ 仍回传结果图（仅 download 为空）。

    这是「修复本身成功、只是保存失败」的用户可恢复场景，不得把结果图也吞掉。
    """
    ts.save_result = _raise(OSError("disk full"))
    out = _call()
    assert len(out) == 5
    assert isinstance(out[1], np.ndarray), "落盘失败时结果图仍须回传（用户可手动另存）"
    assert out[4] is None, "未落盘 ⇒ 不得给出下载文件"
    assert "disk full" in out[3]


def test_success_path_returns_all_five(stubs):
    out = _call()
    assert len(out) == 5
    assert out[0] is IMG
    assert isinstance(out[1], np.ndarray)
    assert out[2] is None          # UI_COMPARE_VIEW 默认关
    assert out[4] == "/tmp/out.png"


# --------------------------------------------------------------------------- #
# 3 · UI_COMPARE_VIEW 开关 → compare 第三元组
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag, expect_none", [(True, False), (False, True)])
def test_compare_view_toggle_controls_third_item(stubs, flag, expect_none):
    """对比视图开关直接决定第三元组是否为 None（首轮未钉）。"""
    ts.config.UI_COMPARE_VIEW = flag
    out = _call()
    assert (out[2] is None) is expect_none, f"UI_COMPARE_VIEW={flag} 时第三元组判定错"


# --------------------------------------------------------------------------- #
# 4 · record 开关与缓存容错
# --------------------------------------------------------------------------- #
def test_record_false_skips_cache(stubs):
    calls = []
    ts._CACHE = type("C", (), {
        "add_success": lambda slf, *a, **k: calls.append(1) or "r",
        "update_result_path": lambda slf, *a, **k: None,
    })()
    _call(record=False)
    assert calls == [], "record=False 不得登记历史"


def test_record_true_registers_once(stubs):
    calls = []
    ts._CACHE = type("C", (), {
        "add_success": lambda slf, *a, **k: calls.append(1) or "r",
        "update_result_path": lambda slf, *a, **k: None,
    })()
    _call(record=True)
    assert len(calls) == 1, f"record=True 应登记恰好一次，实际 {len(calls)}"


@pytest.mark.parametrize("label, cache", [
    ("returns_none", type("C", (), {
        "add_success": lambda slf, *a, **k: None,
        "update_result_path": lambda slf, *a, **k: None})()),
    ("raises", type("C", (), {
        "add_success": lambda slf, *a, **k: _raise(RuntimeError("cache boom"))(slf),
        "update_result_path": lambda slf, *a, **k: None})()),
])
def test_cache_failure_does_not_break_main_flow(stubs, label, cache):
    """历史登记失败（返回 None 或抛错）不得影响主流程的结果与下载。"""
    ts._CACHE = cache
    out = _call()
    assert len(out) == 5
    assert isinstance(out[1], np.ndarray), f"cache {label} 吞掉了结果图"
    assert out[4] == "/tmp/out.png", f"cache {label} 吞掉了下载路径"


# --------------------------------------------------------------------------- #
# 5 · 超分放大提示
# --------------------------------------------------------------------------- #
def test_upscaled_note_appended_when_shape_changes(stubs):
    """后处理放大输出（真·超分）⇒ 状态文案追加 upscaled 提示且 factor 正确。"""
    ts.pp_postprocess = lambda r, **k: np.zeros((16, 16, 3), np.uint8)   # 8 -> 16（2×）
    out = _call()
    assert _t("auto.msg.upscaled", factor=2) in out[3], f"缺少 2× 放大提示：{out[3]!r}"


def test_no_upscaled_note_when_shape_unchanged(stubs):
    """形状未变 ⇒ 不得追加放大提示（避免误报）。"""
    out = _call()
    assert _t("auto.msg.upscaled", factor=2) not in out[3]


# --------------------------------------------------------------------------- #
# 6 · on_enhance 成功路径
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["concat", "slider", "heatmap", "不存在的模式"])
def test_on_enhance_returns_non_empty_html(mode):
    a = np.zeros((8, 8, 3), np.uint8)
    b = np.full((8, 8, 3), 255, np.uint8)
    html = ts.on_enhance(a, b, mode)
    assert isinstance(html, str) and html, f"mode={mode} 未返回 HTML"


@pytest.mark.parametrize("a, b", [(None, np.zeros((4, 4, 3), np.uint8)),
                                  (np.zeros((4, 4, 3), np.uint8), None),
                                  (None, None)])
def test_on_enhance_none_inputs_return_no_result(a, b):
    assert ts.on_enhance(a, b, "concat") == _t("compare.no_result")


# --------------------------------------------------------------------------- #
# 7 · 声明漂移守卫（本轮附带修复点）
# --------------------------------------------------------------------------- #
def test_docstring_references_existing_test_file():
    """`_SINGLE_WIDGET_PARAMS` 的 docstring 不得引用已不存在的测试文件。

    阶段 283 修复：原引用 `tests/test_tab_single_audit110.py` 已被合并进
    `tests/test_ui_wiring_contract110.py`。本守卫防同类漂移复发。
    """
    import re
    src = (ROOT / "ui" / "tab_single.py").read_text(encoding="utf-8")
    # 正则捕获的是 `tests/` **之后**的相对路径，故需补回前缀再判存在性。
    refs = {"tests/" + m for m in re.findall(r"tests/(test_[\w/]+\.py)", src)}
    missing = sorted(r for r in refs if not (ROOT / r).exists())
    assert not missing, f"docstring 引用了不存在的测试文件：{missing}"
    assert "tests/test_ui_wiring_contract110.py" in refs, (
        "应引用现行的 tests/test_ui_wiring_contract110.py（阶段 283 更正）")