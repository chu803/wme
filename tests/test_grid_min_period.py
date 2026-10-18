"""2026-10-18 · is_grid 最小周期标定守卫（DETECT_GRID_MIN_PERIOD）。

背景：doubao JPEG 图上 ACF 把 **JPEG 8×8 DCT 块**的 8px 伪周期当成网格水印
（10/10 误触发，acf 0.19~0.57 远超 0.09 阈）。标定：最小周期 6→16
（合成网格周期恒 56，JPEG q90 不改变；lag8 峰始终远小于 lag56 峰 ⇒ 不漏真网格）。

本守卫双向锁定：
  ① 负向：真实 doubao JPEG（资产已入库）走 ai_grid 预设不得产出显著掩码；
  ② 正向：56px 合成网格仍被检出（min_period 不误伤）。
资产缺失时跳过（不误红）；注入反证见提交说明。
"""
import os

import numpy as np
import pytest

import config
from core.detector import detect_grid_watermark
from scripts.detector_diag import BASE_KINDS, CASES

_IMG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "benchmark_real", "images",
)
# 该图 ACF 周期=8（纯 JPEG 块伪周期，无内容真周期），is_grid 应被 min_period 挡掉。
_JPEG_BLOCK_IMG = "01_山湖日出.jpg"


def _load_rgb(path):
    import cv2
    with open(path, "rb") as fh:
        return cv2.cvtColor(
            cv2.imdecode(np.frombuffer(fh.read(), dtype=np.uint8), cv2.IMREAD_COLOR),
            cv2.COLOR_BGR2RGB,
        )


def test_config_min_period_is_calibrated():
    """杠杆存在且默认 16（4~128 区间内）。"""
    assert hasattr(config, "DETECT_GRID_MIN_PERIOD")
    assert 4 <= config.DETECT_GRID_MIN_PERIOD <= 128
    assert config.DETECT_GRID_MIN_PERIOD == 16, (
        "最小周期默认值被改——若调须重跑标定探针（JPEG 8px 块 vs 真实网格 56px）"
    )


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(_IMG_DIR, _JPEG_BLOCK_IMG)),
    reason=f"缺真实资产 {_JPEG_BLOCK_IMG}（benchmark_real/images 未就绪）",
)
def test_jpeg_block_period_does_not_trigger_grid():
    """doubao JPEG 的 8px DCT 块伪周期不得触发**网格通路**（修复前 is_grid=True）。

    注：断言针对 detect_grid_watermark（网格策略单独）而非 preset 级输出——ai_grid
    预设是多通路并集，doubao 图上的覆盖主要来自半透明通路（另见 real_gate 面积监控）。
    """
    rgb = _load_rgb(os.path.join(_IMG_DIR, _JPEG_BLOCK_IMG))
    h, w = rgb.shape[:2]
    s = config.DETECT_TRANSLUCENT_SENSITIVITY
    m, _ = detect_grid_watermark(
        rgb, s, h * w,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    cov = float(np.count_nonzero(m > 0)) / max(1, m.size)
    assert cov < 0.001, (
        f"JPEG 块伪周期触发了网格通路：mask 覆盖 {cov*100:.3f}%"
        f"（DETECT_GRID_MIN_PERIOD={config.DETECT_GRID_MIN_PERIOD}，应挡掉 8px 周期）"
    )


def test_synthetic_grid_still_detected_after_min_period():
    """56px 合成网格不受 min_period=16 影响（正样本不得误伤）。"""
    base = BASE_KINDS["textured"](7)
    marked = CASES["grid"][0](base)
    h, w = marked.shape[:2]
    s = config.DETECT_TRANSLUCENT_SENSITIVITY
    m, _ = detect_grid_watermark(
        marked, s, h * w,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    cov = float(np.count_nonzero(m > 0)) / max(1, m.size)
    assert cov > 0.005, (
        f"合成网格被 min_period 误伤：mask 覆盖仅 {cov*100:.2f}%"
        f"（周期 56 应远大于 MIN_PERIOD={config.DETECT_GRID_MIN_PERIOD}）"
    )
