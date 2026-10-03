"""阶段 163 · `ui/tab_auto` 掩码精修模式矩阵守卫。

背景
----
`ui/tab_auto` 是覆盖度荒漠榜第 3 席（720 代码 / 39 引用测试）。既有覆盖只钉了
「历史登记接线」「dual_run 签名」「回调接线契约」；**本文件所守的「掩码精修模式矩阵」
—— 自动页最核心、最不直观的业务逻辑 —— 此前零专属测试**。

矩阵（探针 `.workbuddy/scratch/probe_163.py` 量化，全部 by design 正确）：

| # | mode | 编辑框图层 | det(State) | 交给修复引擎的掩码 |
|---|------|-----------|-----------|------------------|
| 1 | merge | det+补画 | det | **编辑框所见即所得**（= det ∪ 补画） |
| 2 | merge | 空 | det | **det** |
| 3 | merge | 空 | None | **自动兜底检测**（状态栏带 ℹ️ 提示） |
| 4 | merge | 仅补画 | det | **仅补画**（det 被丢弃 —— 边界：清空编辑框即「从头开始」） |
| 5 | manual | det+重叠补画 | det | **补画 ∩ ¬det**（剥离预填检测层） |
| 6 | bogus | det+补画 | det | 回退 merge（同 #1）+ 告警日志 |
| 7 | merge | 空 | 3 维数组 | 视为无效 det → 自动兜底 |
| 8 | merge | 空 | None（兜底也空） | 空掩码 → 不调引擎，状态栏提示先标记 |

#4 的语义辨析：mode_info 承诺「补漏+**擦除**都生效」⇒ 编辑框必须**所见即所得**
（否则橡皮擦擦掉误检后，`det ∪ 编辑框` 会把误检加回来）；det 只在编辑框**为空**时兜底。
故 #4 不是缺陷，而是「擦除可用」的前提——本文件把它**显式钉死**，防止后人以
「修复 merge 语义」为名改成严格并集、静默弄坏橡皮擦。
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

H, W = 40, 60
det = np.zeros((H, W), np.uint8);    det[5:15, 5:20] = 255       # 检测命中 150px
stroke = np.zeros((H, W), np.uint8); stroke[20:30, 30:45] = 255  # 补画 150px（与 det 不相交）
overlap = np.zeros((H, W), np.uint8)
overlap[5:15, 5:20] = 255      # 与 det 重叠（用户重涂）
overlap[20:25, 30:40] = 255    # 额外补画
fb = np.zeros((H, W), np.uint8);     fb[0:4, 0:4] = 255          # 兜底检测结果


class _Res:
    def __init__(self, mask):
        self.mask = mask
        self.regions = []
        self.stats = None
        self.used_ocr = False


class _FakeCache:
    def add_success(self, *a, **k):
        return object()

    def update_result_path(self, *a, **k):
        pass


CAPTURED: dict = {}


@pytest.fixture
def env(monkeypatch):
    """桩掉重依赖；`detect_watermark_mask` 可经 `set_fallback` 指定兜底掩码。"""
    holder = {"fallback": fb}

    def fake_detect(img, **k):
        return _Res(holder["fallback"])

    def fake_inpaint(img, mask, **k):
        CAPTURED["mask"] = np.array(mask, copy=True)
        return img.copy()

    monkeypatch.setattr(ta, "detect_watermark_mask", fake_detect)
    monkeypatch.setattr(ta, "visualize_detection", lambda img, res: img)
    monkeypatch.setattr(ta, "inpaint", fake_inpaint)
    monkeypatch.setattr(ta, "pp_postprocess", lambda raw, **k: raw)
    monkeypatch.setattr(ta, "evaluate_improvement", lambda *a, **k: {"detail": "n/a"})
    monkeypatch.setattr(ta, "save_result", lambda arr, name, **k: f"/fake/{name}.png")
    monkeypatch.setattr(ta, "_CACHE", _FakeCache())
    monkeypatch.setattr(config, "UI_COMPARE_VIEW", False)
    CAPTURED.clear()
    return holder


def _editor(*layers):
    return {"background": np.full((H, W, 3), 100, np.uint8),
            "layers": [ta._mask_to_rgba_layer(m) for m in layers],
            "composite": None}


def _repair(env, editor, det_mask, mode):
    return ta.process_auto_repair(
        editor, "opencv", 0.5, 0, 0, 128, 0,
        enable_postprocess=False, postprocess_eval=False,
        det_mask=det_mask, mask_mode=mode, record=False,
    )


def _captured():
    return CAPTURED.get("mask")


# --------------------------------------------------------------------------- #
# 矩阵
# --------------------------------------------------------------------------- #
def test_merge_editor_wysiwyg(env):
    r = _repair(env, _editor(det, stroke), det, "merge")
    assert len(r) == 5
    m = _captured()
    assert m is not None and (m == (det | stroke)).all(), "merge+编辑框非空 ⇒ 所见即所得"


def test_merge_empty_editor_uses_det(env):
    _repair(env, _editor(), det, "merge")
    m = _captured()
    assert m is not None and (m == det).all(), "merge+编辑框空 ⇒ 用检测掩码"


def test_merge_empty_editor_no_det_autofallback(env):
    env["fallback"] = fb
    r = _repair(env, _editor(), None, "merge")
    m = _captured()
    assert m is not None and (m == fb).all(), "merge+编辑框空+det=None ⇒ 自动兜底检测"
    assert "ℹ️" in r[3], "兜底必须在状态栏给出 ℹ️ 提示"


def test_merge_strokes_only_drops_det(env):
    """边界钉死：清空编辑框后只补画 ⇒ det 被丢弃（所见即所得，橡皮擦可用的前提）。"""
    _repair(env, _editor(stroke), det, "merge")
    m = _captured()
    assert m is not None and (m == stroke).all(), (
        "merge+仅补画 ⇒ 只修补画（若变成 det∪补画，橡皮擦擦除误检将失效）")


def test_manual_strips_det(env):
    _repair(env, _editor(det, overlap), det, "manual")
    m = _captured()
    assert m is not None and (m == (overlap & ~det)).all(), "manual ⇒ 补画 ∩ ¬det"


def test_unknown_mode_falls_back_to_merge(env):
    _repair(env, _editor(det, stroke), det, "bogus")
    m = _captured()
    assert m is not None and (m == (det | stroke)).all(), "未知模式 ⇒ 回退 merge"


def test_non2d_det_is_ignored(env):
    env["fallback"] = fb
    _repair(env, _editor(), det[:, :, None].repeat(3, axis=2), "merge")
    m = _captured()
    assert m is not None and (m == fb).all(), "非 2 维 det 视为无效 ⇒ 自动兜底"


def test_all_empty_never_calls_engine(env):
    env["fallback"] = np.zeros((H, W), np.uint8)   # 兜底也空
    r = _repair(env, _editor(), None, "merge")
    assert "mask" not in CAPTURED, "空掩码不得调用修复引擎"
    assert r[1] is None and r[4] is None and "标记" in r[3]


# --------------------------------------------------------------------------- #
# 返回形状 + 检测回调
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("case", [
    ("merge", "full"), ("merge", "empty"), ("merge", "fallback"),
    ("manual", "full"), ("bogus", "full"),
])
def test_repair_always_returns_5_tuple(env, case):
    mode, kind = case
    if kind == "full":
        ed, dm = _editor(det, stroke), det
    elif kind == "empty":
        ed, dm = _editor(), det
    else:
        ed, dm = _editor(), None
    r = _repair(env, ed, dm, mode)
    assert isinstance(r, tuple) and len(r) == 5


def test_detect_no_upload(env):
    r = ta.process_auto_detect(None, 0.5, True, True, True, "general")
    assert len(r) == 5 and r[0] is None and r[1] is None and r[4] is None


def test_detect_returns_state_mask(env):
    upload = np.full((H, W, 3), 100, np.uint8)
    env["fallback"] = det
    preview, editor_value, status, stats_md, mask = ta.process_auto_detect(
        upload, 0.5, True, True, True, "general")
    assert len((preview, editor_value, status, stats_md, mask)) == 5
    assert (mask == det).all(), "检测掩码必须原样存入 State（供修复回调合并/统计）"
    assert editor_value["layers"], "检测到水印 ⇒ 编辑框应预填红色图层"
    alpha = editor_value["layers"][0][:, :, 3]
    assert (alpha == det).all(), "预填图层的 alpha 必须等于检测掩码"
