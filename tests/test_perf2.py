# -*- coding: utf-8 -*-
"""
阶段 16 性能与内存再优化 · 单元测试。

覆盖：
  · 图片解码内存缓存：命中 / 清空 / 文件覆盖失效 / 关闭开关。
  · 质量评估降采样：超大图收敛到 QUALITY_MAX_EVAL_SIDE 且不改变分级结论。
  · config 快照含阶段 16 配置项。
"""
import os
import tempfile

import numpy as np
import cv2
import config

from core.image_utils import load_image_with_alpha, clear_image_cache, _IMAGE_CACHE
import core.image_utils as _iu
from services.quality_service import evaluate_quality


def _save(arr: np.ndarray) -> str:
    """把 RGB 数组写成临时 PNG，返回路径。"""
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(f.name, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    f.close()
    return f.name


def test_decode_cache_hit_and_clear():
    clear_image_cache()
    p = _save(np.full((300, 300, 3), 200, np.uint8))
    try:
        a, _ = load_image_with_alpha(p)
        b, _ = load_image_with_alpha(p)
        assert np.array_equal(a, b)
        assert len(_IMAGE_CACHE) >= 1
    finally:
        clear_image_cache()
        assert len(_IMAGE_CACHE) == 0
        os.remove(p)


def test_decode_cache_invalidation_on_overwrite():
    clear_image_cache()
    p = _save(np.full((100, 100, 3), 10, np.uint8))
    try:
        a, _ = load_image_with_alpha(p)
        # 用不同内容覆盖同一路径（mtime 变化 → 缓存键失效）。
        cv2.imwrite(p, cv2.cvtColor(np.full((100, 100, 3), 200, np.uint8), cv2.COLOR_RGB2BGR))
        # 文件尺寸相同、写入可能在同一 mtime 精度内完成（沙箱/NTFS 偶发），
        # 显式把 mtime 推后，确保缓存键（abspath, mtime_ns, size）必然失效。
        # 注意：不能用 os.utime(p, None)（=当前时间）——FAT/NTFS 低精度下
        # 可能与首次写入落在同一时间粒度，2026-08-30 实测偶发导致缓存不失效。
        t = os.stat(p).st_mtime + 10
        os.utime(p, (t, t))
        b, _ = load_image_with_alpha(p)
        assert not np.array_equal(a, b)
    finally:
        clear_image_cache()
        os.remove(p)


def test_decode_cache_disabled():
    saved = _iu._IMAGE_CACHE_ENABLED
    _iu._IMAGE_CACHE_ENABLED = False
    clear_image_cache()
    p = _save(np.full((100, 100, 3), 50, np.uint8))
    try:
        a, _ = load_image_with_alpha(p)
        b, _ = load_image_with_alpha(p)
        assert np.array_equal(a, b)
        assert len(_IMAGE_CACHE) == 0  # 关闭时不写缓存
    finally:
        _iu._IMAGE_CACHE_ENABLED = saved
        clear_image_cache()
        os.remove(p)


def test_quality_downscale_classification_preserved():
    """大图（800×800）下，激进降采样仍应保持 good / review 的正确分级。"""
    h, w = 800, 800
    base = np.full((h, w, 3), 235, np.uint8)
    orig = base.copy()
    cv2.rectangle(orig, (200, 300), (600, 500), (200, 200, 200), -1)  # 灰块水印
    p_orig = _save(orig)
    p_fixed = _save(base)     # 修得好：结果接近底图
    p_not = _save(orig)       # 没修掉：结果=原图
    try:
        # 全分辨率基线。
        assert evaluate_quality(p_orig, p_fixed).level == "good"
        assert evaluate_quality(p_orig, p_not).level == "review"

        # 激进降采样（64px）下结论不变。
        saved = config.QUALITY_MAX_EVAL_SIDE
        config.QUALITY_MAX_EVAL_SIDE = 64
        try:
            assert evaluate_quality(p_orig, p_fixed).level == "good"
            assert evaluate_quality(p_orig, p_not).level == "review"
        finally:
            config.QUALITY_MAX_EVAL_SIDE = saved
    finally:
        clear_image_cache()
        for p in (p_orig, p_fixed, p_not):
            os.remove(p)


def test_config_snapshot_has_perf2_keys():
    snap = config.get_config_snapshot()
    for k in ("解码缓存启用", "质量评估降采样边长"):
        assert k in snap
