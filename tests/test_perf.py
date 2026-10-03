# -*- coding: utf-8 -*-
"""
阶段 14 测试：性能与内存优化。

覆盖：
  · 进度回调节流（ThrottledProgress / make_scoped）
  · 大图分块推理（OpenCV 引擎也能分块，且分块与不分块行为一致）
  · 空显存释放节流（调用不崩溃）
  · 性能相关配置字段进入快照

全部离线、零 torch 依赖、不落盘（规避沙箱 safe-delete）。
"""
import numpy as np
import pytest

import config
from utils.progress import ThrottledProgress, make_scoped
from core.inpainter import inpaint
from core.device_manager import empty_cache


def test_progress_throttle_reduces_calls():
    """高频回调应被大幅节流，但首帧与末帧必须保留。"""
    calls: list[float] = []
    tp = ThrottledProgress(lambda p, m: calls.append(p), min_interval=0.0, min_step=0.05)
    for i in range(101):
        tp(i / 100.0, "x")
    tp(1.0, "done", force=True)
    assert len(calls) < 60, f"节流未生效，真实上报 {len(calls)} 次"
    assert calls[0] == 0.0, "首帧丢失"
    assert calls[-1] == 1.0, "末帧丢失"
    assert any(0.1 < c < 0.9 for c in calls), "中间关键进度点丢失"


def test_progress_throttle_time_based():
    """开启时间节流后，间隔内的密集回调应被合并（仅验证不崩溃 + 首末帧）。"""
    calls: list[float] = []
    # 使用真实时间节流（min_interval=0.05，min_step 很大使其不靠步进触发）。
    tp = ThrottledProgress(lambda p, m: calls.append(p), min_interval=0.05, min_step=1.0)
    tp(0.0, "s")
    # 瞬间密集上报，应全部被时间节流合并丢弃（除首帧外不新增）。
    for i in range(1, 100):
        tp(i / 100.0, "x")
    tp(1.0, "done", force=True)
    # 末帧强制完成，因此至少有首 + 末两帧；密集中间帧被合并。
    assert calls[0] == 0.0 and calls[-1] == 1.0
    assert len(calls) <= 3, f"时间节流未合并密集回调：{len(calls)}"


def test_make_scoped_maps_subrange():
    """make_scoped 应把子任务 0~1 线性映射到全局 [a, b]。"""
    seen: list[float] = []
    scoped = make_scoped(lambda p, m: seen.append(p), 0.2, 0.8)
    scoped(0.0, "a")
    scoped(0.5, "b")
    scoped(1.0, "c")
    assert seen == [0.2, 0.5, 0.8], seen


def test_large_image_tiling_fills_center():
    """超大图 OpenCV 修复（分块路径）应填掉中心水印；关闭分块行为一致。"""
    BIG = 2500
    img = np.full((BIG, BIG, 3), 200, np.uint8)
    c0, c1 = BIG // 2 - 30, BIG // 2 + 30
    img[c0:c1, c0:c1] = 50
    mask = np.zeros((BIG, BIG), np.uint8)
    mask[c0:c1, c0:c1] = 255

    out = inpaint(img, mask, engine="opencv", strength=1.0, denoise=0)
    assert int(out[BIG // 2, BIG // 2, 0]) != 50, "OpenCV 分块修复未填掉中心水印"

    # 关闭分块再跑一次，中心同样应被填（验证分块与不分块行为一致）。
    old = config.ENABLE_TILING
    config.ENABLE_TILING = False
    try:
        out2 = inpaint(img, mask, engine="opencv", strength=1.0, denoise=0)
    finally:
        config.ENABLE_TILING = old
    assert int(out2[BIG // 2, BIG // 2, 0]) != 50, "OpenCV 不分块修复未填掉中心水印"


def test_empty_cache_throttle_no_error():
    """无 torch 环境下 empty_cache 为 no-op；连续调用不应抛异常（节流逻辑不崩溃）。"""
    empty_cache()            # 首次
    empty_cache()            # 间隔极短，应被节流跳过
    empty_cache(force=True)  # 强制释放


def test_config_perf_fields():
    """性能相关配置进入快照，且取值合法。"""
    snap = config.get_config_snapshot()
    assert "进度节流间隔(秒)" in snap
    assert "空缓存节流间隔(秒)" in snap
    assert "设备复用" in snap
    assert config.PROGRESS_THROTTLE_SEC >= 0
    assert config.EMPTY_CACHE_MIN_INTERVAL_SEC >= 0
    assert isinstance(config.PERF_REUSE_DEVICE, bool)
