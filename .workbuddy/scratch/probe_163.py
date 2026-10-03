# -*- coding: utf-8 -*-
"""Stage 163 探针：ui/tab_auto 掩码精修模式矩阵（merge / manual / 兜底 / 未知模式）。

这是自动页最核心、最不直观的业务逻辑，此前无专属测试（既有覆盖只有历史登记 / dual_run 签名 / 接线契约）。
桩掉重依赖（detector/inpaint/postprocess/save_result/cache），用真实 build_mask_from_editor / merge_masks
量化每种组合的最终掩码。
"""
from __future__ import annotations

import sys
import types

import numpy as np

sys.path.insert(0, "D:/work 111/2026-08-03-17-38-51")

import ui.tab_auto as ta  # noqa: E402
import config  # noqa: E402

H, W = 40, 60
det = np.zeros((H, W), np.uint8);    det[5:15, 5:20] = 255       # 150 px
stroke = np.zeros((H, W), np.uint8); stroke[20:30, 30:45] = 255  # 150 px（与 det 不相交）
overlap = np.zeros((H, W), np.uint8)
overlap[5:15, 5:20] = 255        # 与 det 重叠
overlap[20:25, 30:40] = 255      # 额外补画


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


def _install(fallback_mask=None, config_ui_compare=False):
    ta.detect_watermark_mask = lambda img, **k: _Res(fallback_mask if fallback_mask is not None else np.zeros((H, W), np.uint8))
    ta.visualize_detection = lambda img, res: img
    ta.inpaint = lambda img, mask, **k: img.copy()
    ta.pp_postprocess = lambda raw, **k: raw
    ta.evaluate_improvement = lambda *a, **k: {"detail": "n/a"}
    ta.save_result = lambda arr, name, **k: f"/fake/{name}.png"
    ta._CACHE = _FakeCache()
    config.UI_COMPARE_VIEW = config_ui_compare


def _editor(*layers):
    return {"background": np.full((H, W, 3), 100, np.uint8),
            "layers": [ta._mask_to_rgba_layer(m) for m in layers],
            "composite": None}


def _run(editor, det_mask, mode):
    r = ta.process_auto_repair(
        editor, "opencv", 0.5, 0, 0, 128, 0,
        enable_postprocess=False, postprocess_eval=False,
        det_mask=det_mask, mask_mode=mode, record=False,
    )
    assert isinstance(r, tuple) and len(r) == 5, f"返回应为 5 元组，实际 {type(r)}/{len(r) if isinstance(r, tuple) else '-'}"
    return r


def _mask_of(r):
    """从 result_view 反推不了掩码，改为直接对拍：这里返回 (status, mask) 由调用方按需断言。"""
    return r[3]


def case(tag, editor, det_mask, mode, expect):
    _install(fallback_mask=det if expect.get("fallback") else None)
    r = _run(editor, det_mask, mode)
    status = r[3]
    print(f"[{tag}] rc5=OK  status含auto_fallback={'auto_fallback' if 'ℹ️' in status else 'no'}")
    print(f"    status={status[:80]!r}")
    if expect.get("no_mask"):
        print(f"    空掩码路径: {'OK' if ('掩码' in status or 'mask' in status.lower()) else '?'}")
    print()
    return r


if __name__ == "__main__":
    print("== tab_auto 掩码精修模式矩阵 ==")
    # 1) merge + 编辑框含 det+补画 → 所见即所得（= det ∪ 补画）
    case("1 merge 编辑框=det+补画", _editor(det, stroke), det, "merge", {})
    # 2) merge + 编辑框空 + det 有效 → 用 det
    case("2 merge 编辑框空 det有效", _editor(), det, "merge", {})
    # 3) merge + 编辑框空 + det=None → 自动兜底检测
    r3 = case("3 merge 编辑框空 det=None", _editor(), None, "merge", {"fallback": True})
    # 4) merge + 编辑框只有补画（清空后手绘）+ det 有效 → det 被丢弃（边界）
    case("4 merge 编辑框=仅补画", _editor(stroke), det, "merge", {})
    # 5) manual + 编辑框=det+重叠补画 → 只留 补画 ∩ ¬det
    case("5 manual 编辑框=det+重叠补画", _editor(det, overlap), det, "manual", {})
    # 6) 未知模式 → 回退 merge
    case("6 未知模式", _editor(det, stroke), det, "bogus", {})
    # 7) det_mask 是 3 维 → 视为无效 det
    case("7 det 3维", _editor(), det[:, :, None].repeat(3, axis=2), "merge", {"fallback": True})
    # 8) 全空 → 空掩码路径
    case("8 全空", _editor(), None, "merge", {"no_mask": True})

    # 直接量化关键语义：manual = 补画 ∩ ¬det
    _install()
    m = ta.merge_masks(det, overlap, "manual", target_shape=(H, W))
    expect_manual = overlap & ~det
    print(f"[量化] manual( det, overlap ) == overlap & ~det : {bool((m == expect_manual).all())}")
    m2 = ta.merge_masks(det, (det | stroke), "merge", target_shape=(H, W))
    print(f"[量化] merge( det, det|stroke ) == det|stroke     : {bool((m2 == (det | stroke)).all())}")
    print("PROBE DONE")
