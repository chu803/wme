"""阶段 55：修复引擎 `auto` 选择的证据驱动收敛。

背景（阶段 51 benchmark，合成数据，同一份检测掩码喂两引擎）：
    OpenCV 在 8/8 用例占优，LaMa 在多数用例起负作用（ΔPSNR 低至 −25.19 dB，
    因大面积/渐变区域产生幻觉纹理）。但 `core/inpainter.py` 的 `auto` 分支旧逻辑是
    「只要有模型就无脑选 LaMa」——而 `config.INPAINT_ENGINE` 默认正是 "auto"，
    导致本机（已装 LaMa 权重）**每一次默认修复都在用更差的引擎**，静默劣化结果。

阶段 55 修复：抽出纯函数 `_resolve_auto_engine`，默认偏好 opencv
（见 `config.INPAINT_AUTO_PREFER`，默认 "opencv"）；LaMa 仅在显式偏好且模型就绪时启用，
模型缺失一律回退 opencv。这样既消除静默劣化，又保留 LaMa 作为显式 opt-in（也方便 P0 真实图验证）。

本文件锁定：
    1) 纯函数解析逻辑（4 种组合）；
    2) 集成：auto 在本机（模型就绪）实际走 opencv 且修复成功；
    3) 集成：偏好 lama 且模型就绪时，auto 经 LaMa 路径修复成功（skip 若模型缺失）；
    4) 回退：偏好 lama 但模型缺失时，auto 回退 opencv 且修复成功。
"""
import numpy as np
import pytest

import config
from core.inpainter import _resolve_auto_engine, inpaint
from core.model_loader import is_model_available


def test_resolve_auto_engine_logic():
    """纯函数：默认偏好 opencv；lama 仅在偏好且模型就绪时启用；缺失回退 opencv。"""
    # 默认偏好（opencv）+ 模型就绪 → opencv（关键修复：旧逻辑此处选 lama）
    assert _resolve_auto_engine("opencv", True) == "opencv"
    # 默认偏好 + 模型缺失 → opencv（始终可用）
    assert _resolve_auto_engine("opencv", False) == "opencv"
    # 偏好 lama + 模型就绪 → lama（保留显式 opt-in）
    assert _resolve_auto_engine("lama", True) == "lama"
    # 偏好 lama + 模型缺失 → 回退 opencv（保证总能修）
    assert _resolve_auto_engine("lama", False) == "opencv"
    # 未知偏好 → 回退 opencv
    assert _resolve_auto_engine("bogus", True) == "opencv"


def _synthetic():
    """造一张带中央水印块的图 + 掩码。"""
    img = np.full((64, 64, 3), 210, dtype=np.uint8)
    img[20:44, 20:44] = 40
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[20:44, 20:44] = 255
    return img, mask


def test_auto_uses_opencv_when_model_present():
    """集成：本机（LaMa 权重就绪）上 auto 默认走 opencv 且修复成功（无静默劣化）。"""
    img, mask = _synthetic()
    out = inpaint(img, mask, engine="auto", strength=1.0)
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    # 水印区应被改写（不再是原水印值 40）
    assert not np.array_equal(img[20:44, 20:44], out[20:44, 20:44])


@pytest.mark.skipif(not is_model_available(), reason="需要 LaMa 权重（P0 环境已就绪）")
def test_auto_honors_lama_preference_when_model_present():
    """集成：偏好 lama 且模型就绪时，auto 经 LaMa 路径修复成功（验证接线正确）。"""
    prev = config.INPAINT_AUTO_PREFER
    config.INPAINT_AUTO_PREFER = "lama"
    try:
        img, mask = _synthetic()
        out = inpaint(img, mask, engine="auto", strength=1.0)
    finally:
        config.INPAINT_AUTO_PREFER = prev
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(img[20:44, 20:44], out[20:44, 20:44])


def test_auto_falls_back_when_lama_preferred_but_model_missing(monkeypatch):
    """回退：偏好 lama 但模型缺失时，auto 回退 opencv 且修复成功。"""
    import core.inpainter as _inp
    monkeypatch.setattr(_inp, "is_model_available", lambda: False)
    prev = config.INPAINT_AUTO_PREFER
    config.INPAINT_AUTO_PREFER = "lama"
    try:
        img, mask = _synthetic()
        out = inpaint(img, mask, engine="auto", strength=1.0)
    finally:
        config.INPAINT_AUTO_PREFER = prev
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(img[20:44, 20:44], out[20:44, 20:44])
