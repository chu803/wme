# -*- coding: utf-8 -*-
"""
==============================================================================
 测试文件 : tests/test_batch_recommend.py
 文件说明 : 阶段 26（批量智能推荐）单元测试。

            验证：
              1. _resolve_batch_preset 对「平铺 / 满图文字 / 常规」不同图能各自
                 推荐出正确的检测预设（而非整批共用一个）。
              2. auto_preset=False 时回退到共享 preset，且 recommendation 为 None。
              3. 推荐器异常时安全回退到共享 preset（不影响批次）。
              4. 多图聚合出正确的「预设分布」计数（与 run_batch 的累计逻辑一致）。

            纯算法验证，不加载模型、不触发实际修复，速度快、零外部依赖。
==============================================================================
"""

import sys
import os as _os

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pytest

import config
from ui import tab_batch as tb


# ------------------------------------------------------------------------------
# 合成测试图（与 core/preset_recommender.py 自检用例同构，保证预期预设可复现）。
# ------------------------------------------------------------------------------
def _cv2():
    import cv2
    return cv2


def _plain_img():
    return np.full((400, 400, 3), 240, np.uint8)


def _tiled_img():
    cv2 = _cv2()
    base = np.full((400, 400, 3), 245, np.uint8).astype(np.float32)
    step = 40
    for y in range(0, 400, step):
        for x in range(0, 400, step):
            cv2.rectangle(base, (x + 10, y + 10), (x + 30, y + 30),
                          (220, 220, 220), -1)
    return np.clip(base, 0, 255).astype(np.uint8)


def _text_img():
    cv2 = _cv2()
    img = _plain_img()
    rng = np.random.default_rng(0)
    for _ in range(300):
        x = int(rng.integers(10, 390))
        y = int(rng.integers(10, 390))
        ang = float(rng.integers(0, 180))
        dx = int(18 * np.cos(np.deg2rad(ang)))
        dy = int(18 * np.sin(np.deg2rad(ang)))
        cv2.line(img, (x, y), (x + dx, y + dy), (60, 60, 60), 2)
    return img


# ------------------------------------------------------------------------------
# 1) 每张图独立推荐出正确预设
# ------------------------------------------------------------------------------
@pytest.mark.parametrize("img,expected", [
    (_tiled_img, "ai_tiled"),
    (_text_img, "ai_text_heavy"),
    (_plain_img, "general"),
])
def test_resolve_per_image_preset(img, expected):
    image = img()
    preset_to_use, recommendation, recommended = tb._resolve_batch_preset(
        image, "test.png", 0.5, auto_preset=True, fallback_preset="general",
    )
    assert recommended == expected, (
        f"应为 {expected}，实际 {recommended}（{recommendation}）"
    )
    if expected == "general":
        # 推荐回默认：实际应用共享 preset（general）。
        assert preset_to_use == "general"
    else:
        assert preset_to_use == expected
    # 推荐成功时 recommendation 为可 JSON 序列化的 dict。
    assert recommendation is not None
    import json
    json.dumps(recommendation)


# ------------------------------------------------------------------------------
# 2) 关闭智能推荐 → 整批共用共享 preset，且 recommendation 为 None
# ------------------------------------------------------------------------------
def test_auto_preset_off_uses_fallback():
    image = _tiled_img()  # 即便图是平铺，关闭后也应尊重共享预设。
    preset_to_use, recommendation, recommended = tb._resolve_batch_preset(
        image, "test.png", 0.5, auto_preset=False, fallback_preset="ai_grid",
    )
    assert preset_to_use == "ai_grid"
    assert recommendation is None
    assert recommended is None


# ------------------------------------------------------------------------------
# 3) 推荐器异常 → 安全回退共享 preset（不影响批次）
# ------------------------------------------------------------------------------
def test_recommend_failure_falls_back(monkeypatch):
    def _boom(rgb, sensitivity=None):
        raise RuntimeError("模拟推荐器崩溃")

    monkeypatch.setattr(tb, "recommend_preset", _boom)
    preset_to_use, recommendation, recommended = tb._resolve_batch_preset(
        _tiled_img(), "test.png", 0.5, auto_preset=True, fallback_preset="general",
    )
    assert preset_to_use == "general"
    assert recommendation is None
    assert recommended is None


# ------------------------------------------------------------------------------
# 4) 多图聚合 → 预设分布计数（与 run_batch 累计逻辑一致）
# ------------------------------------------------------------------------------
def test_preset_distribution_across_batch():
    images = [_tiled_img(), _text_img(), _plain_img(), _tiled_img()]
    counts = {}
    for i, im in enumerate(images):
        _, _, recommended = tb._resolve_batch_preset(
            im, f"img{i}.png", 0.5, auto_preset=True, fallback_preset="general",
        )
        if recommended:  # run_batch 仅对非空 recommended_preset 计数。
            counts[recommended] = counts.get(recommended, 0) + 1
    assert counts.get("ai_tiled") == 2
    assert counts.get("ai_text_heavy") == 1
    assert counts.get("general") == 1  # 常规图也计入（实际应用 general）。

    # 分布文案格式（与 run_batch 末尾一致）。
    dist = "，".join(f"{tb._preset_display(k)}×{v}" for k, v in counts.items())
    assert "AI 平铺水印×2" in dist
    assert "AI 文字密集×1" in dist
    assert "通用检测×1" in dist


# ------------------------------------------------------------------------------
# 5) _preset_display 对已知/未知预设的映射
# ------------------------------------------------------------------------------
def test_preset_display_mapping():
    disp = tb._preset_display("ai_tiled")
    assert disp and disp != "ai_tiled"  # 已知预设应映射到可读标签。
    assert tb._preset_display("no_such_preset") == "no_such_preset"  # 未知原样返回。


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
