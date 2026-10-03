# -*- coding: utf-8 -*-
"""阶段 85：无参考质量分的「区域内梯度」特征回归守卫。

背景（缺陷）
-----------
`core/quality._features` 原本这样取「区域内部梯度」：

    in_gx = (bin_mask[:, :-1] & bin_mask[:, 1:])   # bin_mask 是 uint8
    g = np.concatenate([gx[in_gx].ravel(), gy[in_gy].ravel()])

`bin_mask` 在真实调用链上是 **uint8**（`threshold_mask` 返回 0/255，检测器掩码同），
于是 `in_gx` 是 uint8 而非布尔数组 → numpy 按**整数花式索引**解释：
`gx[in_gx]` 变成「取第 0 / 第 255 行」，结果从 (H, W−1) 膨胀成 (H, W−1, W)。

后果有二，且都很隐蔽：
  1. **语义错**——梯度特征与掩码无关（恒定取顶部两行 / 第 255 行），
     「梯度只统计区域内部」这一设计**从未生效**；
  2. **资源爆**——640² 图 → 523,059,840 个 float64 → **3.9 GiB**，
     空闲时侥幸能过、内存紧张时 `MemoryError`（全量套件里就是这么暴露的，
     `tests/test_api_layer_sync.py` 的端到端用例）；1024² → ~17 GiB，必然 OOM。

旧单测全部用 64×64（爆炸后仅 51 万元素）所以从未抓到——**规模本身是判据的一部分**。
本文件的守卫因此都带「真实尺寸」维度。
"""
from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")  # noqa: F841

from core.quality import (  # noqa: E402
    _features,
    _interior_gradients,
    no_reference_inpaint_score,
)


# ---------------------------------------------------------------------------
# 1. 样本数：结构守卫（直接锁死「膨胀」这一根因）
# ---------------------------------------------------------------------------
def test_full_mask_sample_count_is_linear_not_quadratic():
    """全掩码 640²：样本数必须 = H·(W−1) + (H−1)·W（≈2HW，线性）。

    若有人把 `inside` 退回 uint8（整数索引），结果会是
    640·639·639 + 639·640·640 = 523,059,840 → 本断言立即失败。
    """
    h = w = 640
    mask = np.full((h, w), 255, np.uint8)
    ch = np.zeros((h, w), np.float64)
    g = _interior_gradients(ch, mask)
    expected = h * (w - 1) + (h - 1) * w
    assert g.size == expected
    # 防止有人用「恒定比例」凑数：期望值本身就是 O(H·W)。
    assert g.size < 2 * h * w


def test_inset_region_sample_count_excludes_boundary():
    """内嵌矩形区域：只统计「邻居都在区域内」的梯度，边界不计入。"""
    h = w = 300
    mask = np.zeros((h, w), np.uint8)
    mask[100:200, 100:200] = 255          # 区域 100×100
    ch = np.zeros((h, w), np.float64)
    g = _interior_gradients(ch, mask)
    rh = rw = 100
    expected = rh * (rw - 1) + (rh - 1) * rw
    assert g.size == expected


def test_single_pixel_region_has_no_interior_gradient():
    """单像素区域没有任何「内部梯度」——不应报错、也不应给出样本。"""
    mask = np.zeros((32, 32), np.uint8)
    mask[10, 10] = 255
    g = _interior_gradients(np.zeros((32, 32), np.float64), mask)
    assert g.size == 0


# ---------------------------------------------------------------------------
# 2. 掩码 dtype：uint8(0/255) 与 bool 必须完全一致
# ---------------------------------------------------------------------------
def test_mask_dtype_uint8_and_bool_agree():
    """uint8 0/255、uint8 0/1、bool 三种写法必须给出同一结果。

    旧实现下 uint8 会走整数索引，三者结果天差地别——这正是缺陷本体。
    """
    rng = np.random.default_rng(20260914)
    ch = rng.random((128, 128)).astype(np.float64)
    mask = np.zeros((128, 128), np.uint8)
    mask[20:100, 30:90] = 255

    a = _interior_gradients(ch, mask)                       # uint8 0/255
    b = _interior_gradients(ch, (mask > 0).astype(np.uint8))  # uint8 0/1
    c = _interior_gradients(ch, mask > 0)                   # bool
    assert a.size == b.size == c.size
    assert np.allclose(a, b) and np.allclose(a, c)


# ---------------------------------------------------------------------------
# 3. 语义：梯度特征只看区域内部，与区域外内容无关
# ---------------------------------------------------------------------------
def test_features_ignore_gradients_outside_region():
    """区域外布满巨梯度、区域内完全平坦 → 梯度特征必须为 0。

    构造要点（决定这条测试是否真能抓到缺陷）：旧实现把 uint8 掩码当**整数索引**用，
    取的是 `gx` 的**第 0 / 第 255 行**与 `gy` 的第 0 / 第 255 列。因此必须把巨梯度
    放在**这些位置上**——放别处（如普通边缘）旧实现照样给出 0，测试就会假绿。
    这里让第 0 行 / 第 0 列呈交替条纹（`gx` 第 0 行、`gy` 第 0 列强梯度），
    区域内部保持全平。
    """
    arr = np.zeros((300, 300), np.uint8)
    stripe = np.tile([0, 255], 150).astype(np.uint8)
    arr[0, :] = stripe        # gx 第 0 行 → 强梯度（旧实现必取的索引之一）
    arr[:, 0] = stripe        # gy 第 0 列 → 强梯度
    mask = np.zeros((300, 300), np.uint8)
    mask[100:200, 100:200] = 255   # 区域内部完全平坦

    mu, sd, gm, gs = _features(arr, mask)
    assert mu == pytest.approx(0.0)
    assert sd == pytest.approx(0.0)
    assert gm == pytest.approx(0.0)
    assert gs == pytest.approx(0.0)


def test_features_change_when_region_interior_changes():
    """区域内部引入梯度 → 梯度特征必须响应（证明不是恒 0 的死特征）。"""
    mask = np.zeros((64, 64), np.uint8)
    mask[16:48, 16:48] = 255

    flat = np.zeros((64, 64), np.uint8)
    textured = flat.copy()
    textured[16:48, 16:48] = np.tile([0, 255], (32, 16)).astype(np.uint8)  # 区域内有条纹

    gm_flat = _features(flat, mask)[2]
    gm_tex = _features(textured, mask)[2]
    assert gm_flat == pytest.approx(0.0)
    assert gm_tex > 0.0


def test_features_uint8_bool_mask_equivalence_end_to_end():
    """`_features` 对 uint8 / bool 掩码结果一致（含 mean/std 分量）。"""
    rng = np.random.default_rng(7)
    arr = (rng.random((96, 96, 3)) * 255).astype(np.uint8)
    mask = np.zeros((96, 96), np.uint8)
    mask[30:70, 30:70] = 255
    assert np.allclose(_features(arr, mask), _features(arr, mask > 0))


def test_features_empty_region_returns_zeros():
    """空区域 → 全 0（调用方据此识别「无区域」）。"""
    arr = np.zeros((32, 32, 3), np.uint8)
    f = _features(arr, np.zeros((32, 32), np.uint8))
    assert f.size == 12
    assert np.allclose(f, 0.0)


def test_features_supports_grayscale_2d():
    """灰度 2-D 输入：特征长度 4（单通道）。"""
    arr = np.full((40, 40), 100, np.uint8)
    mask = np.zeros((40, 40), np.uint8)
    mask[10:30, 10:30] = 255
    f = _features(arr, mask)
    assert f.size == 4
    assert f[0] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 4. 资源守卫：真实尺寸下的峰值分配必须有界（这才是暴露缺陷的那条）
# ---------------------------------------------------------------------------
def test_memory_bounded_at_real_size():
    """640² 全掩码：峰值分配必须远小于 400 MB。

    旧实现此处需要 ~4.2 GiB（`(640,639,639)` + `(639,640,640)` 两个 float64
    中间量再拼接），空闲时侥幸通过、并发/内存紧张时抛 MemoryError。
    numpy 的数组分配可被 tracemalloc 捕获（已在 numpy 2.5 验证）。
    """
    h = w = 640
    arr = np.full((h, w, 3), 128, np.uint8)
    mask = np.full((h, w), 255, np.uint8)

    tracemalloc.start()
    try:
        f = _features(arr, mask)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert f.size == 12
    assert peak < 400 * 1024 * 1024, f"640² 特征提取峰值 {peak / 1e6:.1f} MB 过高（疑似回退）"


def test_score_runs_fast_at_real_size():
    """端到端（含 cv2 膨胀背景环）在 640² 上必须是亚秒级。

    旧实现每次调用要分配 ~4 GiB，单次明显更慢；这条与上一条互为交叉验证。
    """
    import time

    clean = np.full((640, 640, 3), 128, np.uint8)
    marked = clean.copy()
    marked[200:440, 200:440] = 200
    mask = np.zeros((640, 640), np.uint8)
    mask[200:440, 200:440] = 255

    t0 = time.perf_counter()
    no_reference_inpaint_score(clean, marked, mask)
    no_reference_inpaint_score(marked, marked, mask)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"640² 两次打分耗时 {elapsed:.2f}s（疑似回退）"


# ---------------------------------------------------------------------------
# 5. 端到端语义在真实尺寸上仍成立
# ---------------------------------------------------------------------------
def test_score_semantics_hold_at_real_size():
    """640²：完美修复（还原底色）必须优于「留着水印」。"""
    clean = np.full((640, 640, 3), 128, np.uint8)
    marked = clean.copy()
    marked[200:440, 200:440] = 200
    mask = np.zeros((640, 640), np.uint8)
    mask[200:440, 200:440] = 255

    s_good = no_reference_inpaint_score(clean, marked, mask)
    s_bad = no_reference_inpaint_score(marked, marked, mask)
    assert s_good > s_bad


def test_score_is_deterministic():
    """同一输入重复调用结果逐位一致（打分不得含随机性）。"""
    clean = np.full((128, 128, 3), 100, np.uint8)
    marked = clean.copy()
    marked[40:88, 40:88] = 220
    mask = np.zeros((128, 128), np.uint8)
    mask[40:88, 40:88] = 255
    a = no_reference_inpaint_score(clean, marked, mask)
    b = no_reference_inpaint_score(clean, marked, mask)
    assert a == b
