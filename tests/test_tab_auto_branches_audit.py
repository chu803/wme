# -*- coding: utf-8 -*-
"""阶段 276 · `ui/tab_auto` 续审：钉死首轮（96/163）遗漏的回调分支。

背景
----
阶段 96 守了 `on_enhance` 渲染护栏 + `build_app` 注册表清零；
阶段 163 守了「掩码精修模式矩阵」（merge/manual/bogus 共 8 例）。
这两轮都聚焦「核心逻辑」，但**自动页其余回调的错误路由 + 工具按钮 + 历史登记
+ 真超分放大后的对比拼接**从未被专属测试钉死。

本文件用注入式桩（mirror 163 的 env fixture）量化 22 个分支，确认全部 by-design
正确（零真缺陷），并把它们**显式钉死**，防止后人改回调时静默破坏返回元组不变量。

量化来源：`.scratch/probe_tab_auto_reaudit.py`（22 项全绿，0 真缺陷）。
分类结论（legacy-module-audit 1c/5j）：全部为「by-design 正确、首轮未覆盖」的边界，
故此处只钉住行为、不引入任何修复。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import ui.tab_auto as ta  # noqa: E402
from utils.exceptions import EmptyMaskError, ImageTooLargeError  # noqa: E402

H, W = 40, 60
IMG = np.full((H, W, 3), 120, np.uint8)
DET = np.zeros((H, W), np.uint8); DET[5:15, 5:20] = 255
STROKE = np.zeros((H, W), np.uint8); STROKE[20:30, 30:45] = 255


class _FakeCache:
    """只记录历史登记调用，绝不真落盘。"""

    def __init__(self):
        self.calls = []

    def add_success(self, *a, **k):
        self.calls.append(("add_success", k))
        return object()

    def update_result_path(self, *a, **k):
        self.calls.append(("update_result_path", k))


class _DetResult:
    def __init__(self, mask, used_ocr=False):
        self.mask = mask
        self.regions = []
        self.stats = None
        self.used_ocr = used_ocr


def _editor(*layers):
    return {
        "background": IMG,
        "layers": [ta._mask_to_rgba_layer(m) for m in layers],
        "composite": None,
    }


# --------------------------------------------------------------------------- #
# 桩容器 fixture：可针对单用例覆盖具体依赖
# --------------------------------------------------------------------------- #
@pytest.fixture
def stub(monkeypatch):
    """桩掉所有重型依赖；逐项可通过 monkeypatch 再次覆盖。"""
    holder = {
        "boom_inpaint": None, "boom_detect": None, "boom_vis": None,
        "boom_validate": None, "boom_recommend": None, "boom_build_mask": None,
        "upscale": False,
    }

    def fake_inpaint(img, mask, **k):
        if holder["boom_inpaint"] is not None:
            raise holder["boom_inpaint"]
        return img.copy()

    def fake_detect(img, **k):
        if holder["boom_detect"] is not None:
            raise holder["boom_detect"]
        return _DetResult(DET)

    def fake_vis(img, res):
        if holder["boom_vis"] is not None:
            raise holder["boom_vis"]
        return img

    def fake_validate(img):
        if holder["boom_validate"] is not None:
            raise holder["boom_validate"]

    def fake_recommend(img, **k):
        if holder["boom_recommend"] is not None:
            raise holder["boom_recommend"]

        class _R:
            preset = "general"

            def __str__(self):
                return "reason"

        return _R()

    def fake_build_mask(ev, **k):
        if holder["boom_build_mask"] is not None:
            raise holder["boom_build_mask"]
        return STROKE

    def fake_postprocess(raw, **k):
        if holder["upscale"]:
            return np.repeat(np.repeat(raw, 2, axis=0), 2, axis=1)
        return raw

    monkeypatch.setattr(ta, "detect_watermark_mask", fake_detect)
    monkeypatch.setattr(ta, "visualize_detection", fake_vis)
    monkeypatch.setattr(ta, "inpaint", fake_inpaint)
    monkeypatch.setattr(ta, "pp_postprocess", fake_postprocess)
    monkeypatch.setattr(ta, "evaluate_improvement", lambda *a, **k: {"detail": "n/a"})
    monkeypatch.setattr(ta, "build_mask_from_editor", fake_build_mask)
    monkeypatch.setattr(ta, "validate_numpy_image", fake_validate)
    monkeypatch.setattr(ta, "recommend_preset", fake_recommend)
    monkeypatch.setattr(ta, "save_result", lambda arr, name, **k: f"/fake/{name}.png")
    monkeypatch.setattr(ta, "_CACHE", _FakeCache())
    return holder


# =========================================================================== #
# 1. process_auto_repair 错误路由（修复引擎异常 → 5 元组不变量）
# =========================================================================== #
@pytest.mark.parametrize("boom", [
    EmptyMaskError("e"),
    FileNotFoundError("model"),
    RuntimeError("boom"),
])
def test_repair_inpaint_error_routes_to_5tuple(stub, monkeypatch, boom):
    stub["boom_inpaint"] = boom
    r = ta.process_auto_repair(_editor(DET), "opencv", 0.5, 0, 0, 128, 0, record=False)
    assert isinstance(r, tuple) and len(r) == 5, "异常路径必须仍返回 5 元组"
    assert r[0] is not None and r[1] is None and r[4] is None, "异常 ⇒ 原图保留/结果空/无下载"


def test_repair_build_mask_error_routes_to_5tuple(stub):
    stub["boom_build_mask"] = RuntimeError("maskboom")
    r = ta.process_auto_repair(_editor(DET), "opencv", 0.5, 0, 0, 128, 0, record=False)
    assert len(r) == 5 and r[1] is None


# =========================================================================== #
# 2. record=True 历史登记路径
# =========================================================================== #
def test_repair_record_true_registers_history(stub, monkeypatch):
    cache = ta._CACHE
    r = ta.process_auto_repair(_editor(DET), "opencv", 0.5, 0, 0, 128, 0, record=True)
    assert len(r) == 5
    kinds = [c[0] for c in cache.calls]
    assert "add_success" in kinds, "record=True 必须登记历史"
    assert "update_result_path" in kinds, "登记后必须回填结果路径"


# =========================================================================== #
# 3. 真超分放大结果 + UI_COMPARE_VIEW 开启 → 对比拼接不得崩溃
# =========================================================================== #
def test_repair_upscaled_result_compare_ok(stub, monkeypatch):
    """结果比原图大时（真超分放大），对比拼接必须成功且状态栏提示分辨率提升。"""
    stub["upscale"] = True
    monkeypatch.setattr(config, "UI_COMPARE_VIEW", True)
    r = ta.process_auto_repair(_editor(DET), "opencv", 0.5, 0, 0, 128, 0, record=False)
    assert len(r) == 5
    compare = r[2]
    assert isinstance(compare, np.ndarray), "放大结果 + 对比视图不得崩溃"
    assert "分辨率" in r[3] or "提升" in r[3], "状态栏应提示分辨率已提升"


# =========================================================================== #
# 4. process_auto_detect 错误路由
# =========================================================================== #
def test_detect_too_large_routes_to_5tuple(stub):
    stub["boom_validate"] = ImageTooLargeError("too big")
    r = ta.process_auto_detect(IMG, 0.5, True, True, True, "general")
    assert len(r) == 5 and r[0] is not None and r[1] is None and r[4] is None


def test_detect_bad_data_routes_to_5tuple(stub):
    stub["boom_validate"] = ValueError("bad")
    r = ta.process_auto_detect(IMG, 0.5, True, True, True, "general")
    assert len(r) == 5 and r[1] is None


def test_detect_core_exception_routes_to_5tuple(stub):
    stub["boom_detect"] = RuntimeError("detectboom")
    r = ta.process_auto_detect(IMG, 0.5, True, True, True, "general")
    assert len(r) == 5 and r[0] is not None and r[1] is None


def test_detect_visualize_failure_falls_back_to_upload(stub):
    stub["boom_vis"] = RuntimeError("visboom")
    r = ta.process_auto_detect(IMG, 0.5, True, True, True, "general")
    assert len(r) == 5 and r[0] is IMG, "可视化失败必须退回原图"


def test_detect_no_watermark_prefills_empty_editor(stub, monkeypatch):
    """未检测到水印 → 编辑框预填空图层 + 本地化提示 + State 存零掩码。"""
    monkeypatch.setattr(ta, "detect_watermark_mask",
                        lambda img, **k: _DetResult(np.zeros((H, W), np.uint8)))
    r = ta.process_auto_detect(IMG, 0.5, True, True, True, "general")
    assert len(r) == 5
    assert r[1]["layers"] == [], "未检测 ⇒ 预填空图层（引导用户手涂）"
    assert r[4] is not None and (r[4] == 0).all(), "State 必须存零掩码"
    assert "未检测" in r[2] or "涂抹" in r[2], "状态栏应给本地化引导文案"


# =========================================================================== #
# 5. process_recommend_preset 错误路由 + 正常路径
# =========================================================================== #
def test_recommend_none_returns_default(stub):
    preset, md = ta.process_recommend_preset(None)
    assert preset == config.DETECT_PRESET_DEFAULT


def test_recommend_bad_image_returns_default(stub):
    stub["boom_validate"] = ValueError("badimg")
    preset, md = ta.process_recommend_preset(IMG)
    assert preset == config.DETECT_PRESET_DEFAULT


def test_recommend_exception_returns_default(stub):
    stub["boom_recommend"] = RuntimeError("recboom")
    preset, md = ta.process_recommend_preset(IMG)
    assert preset == config.DETECT_PRESET_DEFAULT


def test_recommend_normal_returns_preset_and_markdown(stub):
    preset, md = ta.process_recommend_preset(IMG)
    assert isinstance(preset, str) and isinstance(md, str)


# =========================================================================== #
# 6. 工具按钮回调（_reload_detection_mask / _clear_editor）
# =========================================================================== #
def test_reload_bg_none():
    assert ta._reload_detection_mask(DET, None) is None


def test_reload_det_not_ndarray():
    assert ta._reload_detection_mask("x", IMG) is None


def test_reload_shape_mismatch():
    assert ta._reload_detection_mask(DET, np.zeros((H * 2, W * 2, 3), np.uint8)) is None


def test_reload_det_empty_returns_blank_canvas():
    out = ta._reload_detection_mask(np.zeros((H, W), np.uint8), IMG)
    assert out == {"background": IMG, "layers": [], "composite": None}


def test_reload_normal_prefills_red_layer():
    out = ta._reload_detection_mask(DET, IMG)
    assert out is not None and out["layers"]
    alpha = out["layers"][0][:, :, 3]
    assert (alpha == DET).all(), "重载图层的 alpha 必须等于检测掩码"


def test_clear_bg_none():
    assert ta._clear_editor(None) is None


def test_clear_normal_returns_blank_canvas():
    out = ta._clear_editor(IMG)
    assert out == {"background": IMG, "layers": [], "composite": None}
