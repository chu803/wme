# -*- coding: utf-8 -*-
"""
阶段 285 · `core/image_utils` 深度续审（5j 复审链延续，接 276–284）。

首轮审计（阶段 89 真值审计 + 各处零散引用）已覆盖读写真值、ICC/EXIF 方向、
分块拼接往返、掩码膨胀等主干；但该模块 **1055 行 / 32 函数却无专属测试文件**
（全仓仅 17 处零散引用），纯 numpy 助手的**退化输入与参数下界**几乎无人看守。

本轮枚举契约输入类别后，探针实证发现 **3 处真缺陷**，均已修复；其余边界钉成守卫。

【真缺陷 A · 已修】`pad_to_multiple(multiple<=0)` → 取模除零 `ZeroDivisionError`
  `pad_h = (multiple - h % multiple) % multiple` 在 multiple=0 时直接除零，
  堆栈指向取模而非真正成因（调用方只传了 `MODEL_SIZE_ALIGN`）。
  与阶段 161（queue_worker `retry<0`）/ 166（`records_per_page<1`）同族：数量类
  参数缺下界即显式拒绝。修复：`multiple < 1` → `ValueError`。

【真缺陷 B · 已修】`split_into_tiles(overlap >= tile_size)` → 静默退化为逐像素切块
  步长是 `max(1, tile_size - overlap)`；当 overlap≥tile_size 时被钳到 1，
  分块**静默爆炸**——探针实测 600×600 / tile=256 / overlap=300 从应有 ~9 块
  变成 **119025 块**（内存与耗时静默爆炸，且**无任何报错**）。
  与 161/166 同族：步长钳到 1 反而掩盖了「overlap 相对 tile_size 过大」这一真正成因。
  修复：`tile_size<1` / `overlap<0` / `overlap>=tile_size` 三项均显式 `ValueError`。

【真缺陷 C · 已修】`tensor_to_mask` 缺阶段 207 的 NaN 净化
  `tensor_to_rgb` 在阶段 207 已加固（`nan_to_num` 必须先于 clamp，因 **clamp 拦不住 NaN**：
  `np.clip(nan,0,1)` 仍是 nan ⇒ `astype(uint8)` 触发 `invalid value encountered in cast`
  并把 NaN 静默变 0）。其**同族函数** `tensor_to_mask` 仍是旧写法，探针实测确实发出该警告。
  掩码侧后果同样严重：NaN 像素被判「无需修复」⇒ 区域**静默跳过修复**而调用方仍以为成功。
  修复：与 `tensor_to_rgb` 同口径补 `nan_to_num(nan=0, posinf=1, neginf=0)`。
  （本函数当前**无生产调用方**，属预防性加固；探针证据见上。）

本轮钉死的其余边界（探针实证全部正确）：
  · `dilate_mask`：`iterations<=0` 返回副本不改原图；`>0` 膨胀生效；
  · `feather_mask`：`ksize<=0` 转 float32 不羽化；偶数 ksize 自动 +1 取奇；
  · `threshold_mask`：thresh 取 0/127/255/300/-10 均行为正确、输出纯 0/255；
  · `pad_to_multiple`/`crop_padding` 往返：100×100 / 101×103 / 96×96 / 1×1 / 8×8 全部
    `array_equal` 为真；2D 掩码补边保持 ndim=2；`pad=(0,0,0,0)` 裁回为恒等；
  · `split_into_tiles`：600×600 / ts=256 / ov=64 ⇒ 9 块、y/x 起点 {0,192,384}、
    **覆盖无空洞**、首末块 first/last 标记正确；ts≥图尺寸 ⇒ 1 块；`skip` 标记随
    `TILE_SKIP_EMPTY`；overlap=0 退化为不重叠切分且覆盖完整；
  · `merge_tiles`：300×300 / ts=128 / ov=32 往返 `array_equal` 为真（无接缝误差）；
  · `_edge_window`：首块起点与末块终点恒 1；非首/非末侧做余弦斜坡；极小 length 时
    fade 受 `length//2` 限制不打架；
  · `tensor_to_rgb` NaN→0 / +inf→255 / -inf→0（阶段 207 回归）与越界 clamp；
  · `tensor_to_mask` 接受 (1,1,H,W) 与 (1,H,W)；`mask_to_tensor` 输出 (1,1,H,W) 且值域 {0,1}；
  · `rgb_to_tensor` 255→1.0 归一化。
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
import core.image_utils as iu  # noqa: E402

torch = pytest.importorskip("torch")

_MASK = np.zeros((20, 20), np.uint8)
_MASK[8:12, 8:12] = 255


# --------------------------------------------------------------------------- #
# 1 · 真缺陷 A 回归：pad_to_multiple 下界
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [0, -1, -8])
def test_pad_to_multiple_rejects_nonpositive(bad):
    """multiple<=0 必须显式 ValueError，而不是取模除零（阶段 161/166 同族）。"""
    with pytest.raises(ValueError, match="multiple"):
        iu.pad_to_multiple(np.zeros((10, 10, 3), np.uint8), multiple=bad)


# --------------------------------------------------------------------------- #
# 2 · 真缺陷 B 回归：split_into_tiles 参数下界 / 上界
# --------------------------------------------------------------------------- #
def test_split_tiles_rejects_overlap_ge_tile_size():
    """overlap>=tile_size 必须显式拒绝（否则步长钳到 1、逐像素切块静默爆炸）。"""
    img = np.zeros((600, 600, 3), np.uint8)
    msk = np.zeros((600, 600), np.uint8)
    with pytest.raises(ValueError, match="overlap"):
        iu.split_into_tiles(img, msk, tile_size=256, overlap=300)


@pytest.mark.parametrize("ts, ov", [(256, 256), (64, 128)])
def test_split_tiles_rejects_overlap_equal_or_over(ts, ov):
    img = np.zeros((300, 300, 3), np.uint8)
    msk = np.zeros((300, 300), np.uint8)
    with pytest.raises(ValueError, match="overlap"):
        iu.split_into_tiles(img, msk, tile_size=ts, overlap=ov)


def test_split_tiles_rejects_nonpositive_tile_size():
    img = np.zeros((50, 50, 3), np.uint8)
    msk = np.zeros((50, 50), np.uint8)
    with pytest.raises(ValueError, match="tile_size"):
        iu.split_into_tiles(img, msk, tile_size=0, overlap=0)


def test_split_tiles_rejects_negative_overlap():
    img = np.zeros((50, 50, 3), np.uint8)
    msk = np.zeros((50, 50), np.uint8)
    with pytest.raises(ValueError, match="overlap"):
        iu.split_into_tiles(img, msk, tile_size=64, overlap=-8)


def test_valid_split_does_not_blow_up():
    """对照组：合法参数下块数是**几十**量级（而非逐像素的十万量级）。"""
    img = np.zeros((600, 600, 3), np.uint8)
    msk = np.zeros((600, 600), np.uint8)
    tiles = iu.split_into_tiles(img, msk, tile_size=256, overlap=64)
    assert len(tiles) == 9, f"600×600 / ts=256 / ov=64 应为 9 块，实际 {len(tiles)}"


# --------------------------------------------------------------------------- #
# 3 · 真缺陷 C 回归：tensor_to_mask 的 NaN/inf 净化
# --------------------------------------------------------------------------- #
def test_tensor_to_mask_sanitizes_nan_and_inf():
    """NaN→0 / +inf→255 / -inf→0，且**不得**再发 invalid-cast 警告。"""
    t = torch.zeros((1, 1, 4, 4))
    t[0, 0, 0, 0] = float("nan")
    t[0, 0, 0, 1] = float("inf")
    t[0, 0, 0, 2] = float("-inf")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")     # 修复前会抛 invalid-cast 警告
        m = iu.tensor_to_mask(t)
    assert m[0, 0] == 0, "NaN 应净化为 0（无需修复）"
    assert m[0, 1] == 255, "+inf 应净化为 255（满掩码）"
    assert m[0, 2] == 0, "-inf 应净化为 0"


def test_tensor_to_mask_all_nan_yields_zero_mask():
    t = torch.full((1, 1, 4, 4), float("nan"))
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        m = iu.tensor_to_mask(t)
    assert int((m > 0).sum()) == 0


def test_tensor_to_rgb_keeps_stage207_nan_contract():
    """阶段 207 回归：tensor_to_rgb 仍须 NaN→0 / +inf→255 / -inf→0。"""
    t = torch.zeros((1, 3, 4, 4))
    t[0, 0, 0, 0] = float("nan")
    t[0, 1, 0, 0] = float("inf")
    t[0, 2, 0, 0] = float("-inf")
    arr = iu.tensor_to_rgb(t)
    assert arr[0, 0, 0] == 0 and arr[0, 0, 1] == 255 and arr[0, 0, 2] == 0


@pytest.mark.parametrize("fn", ["tensor_to_rgb", "tensor_to_mask"])
def test_tensor_converters_clamp_out_of_range(fn):
    """越界值须 clamp：5.0→255、-3.0→0。"""
    t = torch.full((1, 1, 2, 2), 5.0)
    t[0, 0, 0, 0] = -3.0
    out = getattr(iu, fn)(t)
    assert out[0, 1] == 255 and out[0, 0] == 0


# --------------------------------------------------------------------------- #
# 4 · 掩码后处理边界
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("it", [0, -1])
def test_dilate_mask_nonpositive_returns_copy(it):
    out = iu.dilate_mask(_MASK, iterations=it)
    assert out is not _MASK, "须返回副本"
    assert np.array_equal(out, _MASK), "iterations<=0 不应改变掩码"
    assert int(_MASK.sum()) == 4080, "原掩码不得被就地修改"


def test_dilate_mask_positive_expands():
    out = iu.dilate_mask(_MASK, iterations=5)
    assert int(out.sum()) > int(_MASK.sum()), "iterations>0 应膨胀"


@pytest.mark.parametrize("ks", [0, -3])
def test_feather_mask_nonpositive_is_noop_float(ks):
    out = iu.feather_mask(_MASK, ks)
    assert out.dtype == np.float32
    assert np.array_equal(out, _MASK.astype(np.float32))


@pytest.mark.parametrize("ks", [2, 3, 5])
def test_feather_mask_even_ksize_is_oddified(ks):
    """偶数 ksize 自动 +1 取奇（cv2.GaussianBlur 要求奇数核）。"""
    import cv2
    out = iu.feather_mask(_MASK, ks)
    expected_ks = ks + 1 if ks % 2 == 0 else ks
    ref = cv2.GaussianBlur(_MASK.astype(np.float32), (expected_ks, expected_ks), 0)
    assert np.allclose(out, ref), f"ksize={ks} 未按「偶数+1」处理"


@pytest.mark.parametrize("thresh, expect_count", [
    (0, 16),      # 255 块 > 0 保留；0 背景 0>0 为假剔除
    (127, 16),    # 255 > 127 保留
    (254, 16),    # 255 > 254 保留
    (255, 0),     # ⚠ THRESH_BINARY 取**严格大于**：255 > 255 为假 ⇒ 全零
    (300, 0),     # 阈值高于最大值 ⇒ 全零
    (-10, 400),   # 0 > -10 ⇒ 背景也保留 ⇒ 全图
])
def test_threshold_mask_boundaries(thresh, expect_count):
    """OpenCV `THRESH_BINARY` 语义：像素值**严格大于** thresh 才置 255。

    钉死 `thresh == 255` 这一反直觉点：掩码值恰为 255 时会被**整块丢弃**
    （255 > 255 为假）。调用方不应把 255 当作「保留」的下界。
    """
    out = iu.threshold_mask(_MASK, thresh)
    assert set(np.unique(out).tolist()) <= {0, 255}, "输出须是纯 0/255"
    assert int((out > 0).sum()) == expect_count, \
        f"thresh={thresh} 前景像素数应为 {expect_count}，实际 {int((out > 0).sum())}"


# --------------------------------------------------------------------------- #
# 5 · pad/crop 往返
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("h, w", [(100, 100), (101, 103), (96, 96), (1, 1), (8, 8)])
def test_pad_crop_roundtrip_is_lossless(h, w):
    arr = np.full((h, w, 3), 200, np.uint8)
    padded, pad = iu.pad_to_multiple(arr, multiple=8)
    back = iu.crop_padding(padded, pad)
    assert back.shape == arr.shape
    assert np.array_equal(back, arr), f"{h}x{w} 往返不一致"


def test_pad_to_multiple_keeps_ndim_for_2d_mask():
    padded, _pad = iu.pad_to_multiple(np.full((100, 100), 255, np.uint8), multiple=8)
    assert padded.ndim == 2, "2D 掩码补边后不应变成 3D"


def test_crop_padding_zero_pad_is_identity():
    a = np.arange(16, dtype=np.uint8).reshape(4, 4)
    assert np.array_equal(iu.crop_padding(a, (0, 0, 0, 0)), a)


# --------------------------------------------------------------------------- #
# 6 · 分块几何
# --------------------------------------------------------------------------- #
def test_split_tiles_geometry_and_full_coverage():
    img = np.zeros((600, 600, 3), np.uint8)
    msk = np.zeros((600, 600), np.uint8)
    msk[250:350, 250:350] = 255
    tiles = iu.split_into_tiles(img, msk, tile_size=256, overlap=64)
    assert sorted({t["y"] for t in tiles}) == [0, 192, 384]
    assert sorted({t["x"] for t in tiles}) == [0, 192, 384]
    covered = np.zeros((600, 600), bool)
    for t in tiles:
        covered[t["y"]:t["y2"], t["x"]:t["x2"]] = True
    assert covered.all(), "分块必须无空洞"
    assert tiles[0]["first_y"] is True and tiles[-1]["last_y"] is True
    assert tiles[0]["first_x"] is True and tiles[-1]["last_x"] is True


def test_split_tiles_single_tile_when_tile_exceeds_image():
    img = np.zeros((50, 50, 3), np.uint8)
    tiles = iu.split_into_tiles(img, np.zeros((50, 50), np.uint8),
                               tile_size=1024, overlap=64)
    assert len(tiles) == 1, "块大于图时应只有 1 块"


def test_split_tiles_zero_overlap_still_covers():
    img = np.zeros((600, 600, 3), np.uint8)
    tiles = iu.split_into_tiles(img, np.zeros((600, 600), np.uint8),
                               tile_size=64, overlap=0)
    covered = np.zeros((600, 600), bool)
    for t in tiles:
        covered[t["y"]:t["y2"], t["x"]:t["x2"]] = True
    assert covered.all(), "overlap=0 仍须完整覆盖"


def test_split_tiles_skip_flag_follows_config():
    img = np.zeros((600, 600, 3), np.uint8)
    msk = np.zeros((600, 600), np.uint8)
    msk[250:350, 250:350] = 255
    tiles = iu.split_into_tiles(img, msk, tile_size=256, overlap=64)
    if config.TILE_SKIP_EMPTY:
        assert any(t["skip"] for t in tiles), "含水印图应至少有一块可跳过"
        assert not all(t["skip"] for t in tiles), "含水印块不应被标记跳过"


# --------------------------------------------------------------------------- #
# 7 · merge_tiles 往返
# --------------------------------------------------------------------------- #
def test_merge_tiles_roundtrip_is_lossless():
    """无修改的分块拼接必须逐像素还原原图（接缝权重互补的正确性）。"""
    img = np.zeros((300, 300, 3), np.uint8)
    img[:, :] = (30, 120, 200)
    tiles = iu.split_into_tiles(img, np.zeros((300, 300), np.uint8),
                               tile_size=128, overlap=32)
    for t in tiles:
        t["result"] = t["img"]
    merged = iu.merge_tiles(tiles, 300, 300, overlap=32)
    assert np.array_equal(merged, img), "拼接出现误差（权重窗不互补？）"


# --------------------------------------------------------------------------- #
# 8 · _edge_window 边界
# --------------------------------------------------------------------------- #
def test_edge_window_first_and_last_edges_are_one():
    """首块起点与末块终点恒为 1（避免图像边缘被压暗）。"""
    w = iu._edge_window(8, 4, is_first=True, is_last=True)
    assert np.allclose(w, 1.0)


@pytest.mark.parametrize("first, last, at_start, at_end", [
    (False, True, 0.0, 1.0),
    (True, False, 1.0, 0.0),
    (False, False, 0.0, 0.0),
])
def test_edge_window_fades_only_on_non_boundary_sides(first, last, at_start, at_end):
    w = iu._edge_window(8, 4, is_first=first, is_last=last)
    assert abs(float(w[0]) - at_start) < 1e-6, "起点侧衰减判定错"
    assert abs(float(w[-1]) - at_end) < 1e-6, "终点侧衰减判定错"
    assert w.min() >= 0.0 and w.max() <= 1.0, "权重须在 [0,1]"


# --------------------------------------------------------------------------- #
# 9 · 张量转换基本契约
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("shape", [(1, 1, 4, 4), (1, 4, 4)])
def test_tensor_to_mask_accepts_both_shapes(shape):
    out = iu.tensor_to_mask(torch.zeros(shape))
    assert out.shape == (4, 4), f"{shape}  squeeze 后应为 (4,4)"


def test_mask_to_tensor_shape_and_range():
    t = iu.mask_to_tensor(_MASK)
    assert tuple(t.shape) == (1, 1, 20, 20)
    assert t.dtype == torch.float32
    assert set(t.flatten().tolist()) <= {0.0, 1.0}


def test_rgb_to_tensor_normalizes_to_unit_range():
    t = iu.rgb_to_tensor(np.full((4, 5, 3), 255, np.uint8))
    assert tuple(t.shape) == (1, 3, 4, 5)
    assert abs(float(t.max()) - 1.0) < 1e-6