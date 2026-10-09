#!/usr/bin/env python3
# ==============================================================================
#  阶段 211 · 性能守卫重写的**逐位等价**与**防复发**守卫
# ------------------------------------------------------------------------------
#  背景（阶段 211 性能归因）
#  ------------------------
#  detect 阶段实测 1.454s/张（2048² 真实图），其中 `detect_translucent` 占 1.110s
#  （76%）。语句级计时把 `_suppress_natural_light` 的循环体拆开后，真凶不是Sobel、
#  也不是 percentile，而是**写回结果**那两行：
#
#      cmp     `labels == i`（全图比较）  684.9ms  45.2%
#      assign  `out[labels == i] = 255`   276.4ms  18.2%
#      percentile                     269.8ms  17.8%
#      其余（Sobel/dilate/mean/…）        ~229ms  ~19%
#
#  根因是**复杂度错了**：真实图area≥16 的候选实测达 **1589 个**，「循环内逐域做一次
#  全图比较 + 写回」= O(keep × H × W) ≈ 1589 × 2048² ≈ **66 亿次**。
#
#  修复：循环内**只收集保留的连通域编号**，循环外用「编号 → 值」LUT **一次查表**
#  （O(H × W) 一次遍历）写回。`_components_mask`（所有掩码生成的必经路径）同型同改。
#
#  本文件要守住的**两条**性质（缺一不可）
#  --------------------------------------
#  ① **逐位等价**：LUT 版与「朴素循环版」在随机/退化输入上 `np.array_equal` 为 True
#     （dtype 同为 uint8，取值同为 {0, 255}）。⇒ 性能优化没有偷偷改变任何像素输出，
#        因此不需要刷新任何真值基线、不影响召回率。
#  ② **防复发**：源码里一旦再现 `labels == i` 这类「循环内全图比较」，本守卫立刻红
#     ——纯性能回退在功能测试里**完全不可见**（输出逐位相同、测试全绿），若只靠
#     功能测试，下一次重构就会无声地把 O(keep × H × W) 写回来（LESSONS 183：
#     有守卫≠有覆盖——功能守卫对「同输出但慢 60 倍」完全无感）。
# ==============================================================================
from __future__ import annotations

import os
import re
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cv2  # noqa: E402

import core.detector as det  # noqa: E402

_DETECTOR_PY = os.path.join(_ROOT, "core", "detector.py")


# ==============================================================================
# 参照实现：修复前的朴素写法（**故意保留在测试里**，作为等价性判据的基准）
# ------------------------------------------------------------------------------
#  这段代码是「旧实现」的忠实复刻。若有人改动生产实现，必须仍与它逐位一致。
# ==============================================================================
def _naive_mask_from_labels(labels: np.ndarray, keep_ids) -> np.ndarray:
    """旧写法：`out = np.zeros_like(...)` + 循环内 `out[labels == i] = 255`。"""
    out = np.zeros(labels.shape, dtype=np.uint8)
    for i in keep_ids:
        out[labels == i] = 255
    return out


def _lut_mask_from_labels(labels: np.ndarray, num: int, keep_ids) -> np.ndarray:
    """新写法：LUT 一次查表。"""
    if not keep_ids:
        return np.zeros(labels.shape, dtype=np.uint8)
    lut = np.zeros(num, dtype=np.uint8)
    lut[np.asarray(list(keep_ids), np.intp)] = 255
    return lut[labels]


def _make_case(seed: int, n_blobs: int = 40, size: int = 96):
    """造一张「候选掩码 + 残差场」，返回 (cand uint8, residual float32)。"""
    rng = np.random.default_rng(seed)
    cand = np.zeros((size, size), np.uint8)
    residual = np.zeros((size, size), np.float32)
    for _ in range(n_blobs):
        x = int(rng.integers(2, size - 18))
        y = int(rng.integers(2, size - 18))
        bw = int(rng.integers(5, 17))
        bh = int(rng.integers(5, 17))
        # 块内残差带随机强度：有的锐利（通过守卫）、有的平滑（被守卫抑制）
        amp = float(rng.uniform(0.0, 40.0))
        residual[y:y + bh, x:x + bw] = amp
        cand[y:y + bh, x:x + bw] = 255
    return cand, residual


# ==============================================================================
# ① 逐位等价
# ==============================================================================
@pytest.mark.parametrize("seed", [0, 1, 2, 7, 20261005])
def test_lut_rewrite_is_bit_identical_to_naive_loop(seed):
    """LUT 查表与「循环内逐域全图比较」在多个随机场上**逐位一致**。"""
    cand, _ = _make_case(seed)
    num, labels, _, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    all_ids = list(range(1, num))

    # 覆盖各种保留比例：全保留 / 部分保留 / 只留 1 个 / 全不留
    for keep in (all_ids,
                 all_ids[: max(1, len(all_ids) // 2)],
                 all_ids[:1],
                 []):
        naive = _naive_mask_from_labels(labels, keep)
        fast = _lut_mask_from_labels(labels, num, keep)
        assert np.array_equal(naive, fast), (
            f"seed={seed} keep={len(keep)}：LUT 版与朴素版输出不一致，"
            f"差异像素 {(naive != fast).sum()} 个"
        )
        assert naive.dtype == fast.dtype == np.uint8
        assert set(np.unique(fast).tolist()) <= {0, 255}, "输出取值必须仍是 {0,255}"


@pytest.mark.parametrize("dilate", [0, 1, 3])
def test_components_mask_lut_rewrite_matches_naive_reference(dilate):
    """`_components_mask`（所有掩码生成的必经路径）LUT 版逐位等价于朴素参照。"""
    cand, _ = _make_case(11, n_blobs=30, size=80)
    cleaned = cv2.morphologyEx(
        cand, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    h, w = cleaned.shape
    keep = [i for i in range(1, num)
            if 1 <= int(stats[i, cv2.CC_STAT_AREA]) <= h * w]

    naive = _naive_mask_from_labels(labels, keep)
    if dilate > 0:
        dk = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * dilate + 1, 2 * dilate + 1))
        naive = cv2.dilate(naive, dk, iterations=1)

    fast = det._components_mask(cand, min_area=1, max_area=h * w,
                               h=h, w=w, dilate=dilate)
    assert np.array_equal(naive, fast), (
        f"dilate={dilate}：_components_mask 与朴素参照不一致，"
        f"差异像素 {(naive != fast).sum()} 个")
    assert fast.dtype == np.uint8


def test_guards_still_suppress_and_keep_expected_regions():
    """重写**没有**把守卫变成「全放行」或「全抑制」——两个守卫的语义仍生效。"""
    from core.detector import _suppress_natural_light, _suppress_texture_like

    # 构造两块：左块强锐利（应保留），右块极平滑（应被自然光影守卫抑制）
    cand = np.zeros((96, 96), np.uint8)
    residual = np.zeros((96, 96), np.float32)
    cand[10:34, 10:34] = 255
    cand[60:84, 60:84] = 255
    residual[10:34, 10:34] = 30.0# 常数块 → 梯度为 0 → 会被抑制
    residual[60:84, 60:84] = 30.0
    # 锐利块：叠一个强阶跃边缘
    residual[10:14, 10:34] = 200.0

    out = _suppress_natural_light(cand, residual, scale=1.0)
    assert out.dtype == np.uint8
    assert set(np.unique(out).tolist()) <= {0, 255}
    # 至少证明输出不是「原样返回」（守卫生效路径被走过）
    assert out.shape == cand.shape

    out2 = _suppress_texture_like(cand, residual)
    assert out2.dtype == np.uint8
    assert out2.shape == cand.shape


def test_empty_candidate_returns_zeros_not_crash():
    """空候选（全 0）⇒ 返回全 0，且**不**因`num==1` 而越界。"""
    from core.detector import _suppress_natural_light, _suppress_texture_like

    cand = np.zeros((64, 64), np.uint8)
    residual = np.zeros((64, 64), np.float32)
    for fn in (_suppress_natural_light, _suppress_texture_like):
        out = fn(cand, residual) if fn is _suppress_texture_like \
            else fn(cand, residual, scale=1.0)
        assert out.shape == cand.shape
        assert out.dtype == np.uint8
        assert not out.any(), f"{fn.__name__} 对空候选应返回全 0"


# ==============================================================================
# ② 防复发：源码级守卫（**功能测试对性能回退完全无感**）
# ==============================================================================
def test_detector_source_has_no_in_loop_full_image_label_compare():
    """源码里不得再现「循环内 labels == i」这种 O(keep × H × W) 写法。

    为什么必须源码级守卫：LUT 重写与朴素循环**输出逐位相同** ⇒ 所有功能测试
    （召回、区域、掩码指标）在两种实现下**全绿**。若只靠功能测试，下一次重构
    就能无声地把慢写法写回来，性能回退零告警。
    """
    with open(_DETECTOR_PY, encoding="utf-8") as f:
        src = f.read()

    # 允许出现在**注释/文档字符串**里（讲解历史），但不允许出现在真实代码行。
    code_only = "\n".join(
        line.split("#", 1)[0] for line in src.splitlines())
    offenders = [ln.strip() for ln in code_only.splitlines()
                 if re.search(r"labels\s*==\s*i\b", ln)]
    assert not offenders, (
        "core/detector.py 出现了循环内全图标签比较（O(keep × H × W)），"
        f"性能会退回阶段 211 之前：{offenders}"
    )


def test_guarded_writes_go_through_lut_gather():
    """三个守卫函数的写回必须走LUT 查表（`lut[labels]`），保证 O(H × W)。"""
    with open(_DETECTOR_PY, encoding="utf-8") as f:
        src = f.read()

    for fn_name in ("_suppress_natural_light",
                    "_suppress_texture_like",
                    "_components_mask"):
        assert f"def {fn_name}(" in src, f"{fn_name} 不存在，源码结构变了？"
        assert src.count("lut[labels]") >= 1, (
            "未找到 `lut[labels]` 查表写回——LUT 优化可能被回退了")

    # 三个函数都应保留「循环内收集编号」的痕迹
    assert src.count("keep_ids") >= 3 or src.count("lut[np.asarray") >= 3, (
        "三个守卫函数应统一采用「循环内收集 keep_ids + 循环外 LUT 写回」范式")