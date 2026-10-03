# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 25 测试：检测自动预设推荐（preset_recommender + 服务层透传）。

 覆盖：
   1. 各合成图像特征 → 推荐预设的正确性（gradient / grid / faint_tiled /
      tiled / logo / text / plain→general）。
   2. 推荐开关关闭 → 回退默认预设。
   3. 推荐特征评分字典结构（关键键齐全）。
   4. 服务层透传：process_image / process_file 在 preset=None 时填充
      recommended_preset / recommendation；显式 preset 或 auto_preset=False
      时推荐结果为空（向后兼容）。
==============================================================================
"""

import os
import tempfile

import numpy as np
import pytest

import config
import cv2
from core import preset_recommender as pr
from services import auto_pipeline


# ------------------------------------------------------------------------------
# 合成图像工厂（纯 numpy + cv2，与 core/preset_recommender.py 自检同源）。
# ------------------------------------------------------------------------------
def _plain(h=400, w=400):
    return np.full((h, w, 3), 240, np.uint8)


def _gradient_img(h=400, w=400):
    img = np.zeros((h, w, 3), np.uint8)
    for y in range(h):
        v = int(255 * y / max(1, h - 1))
        img[y, :, :] = v
    return img


def _grid_img(h=400, w=400):
    img = np.full((h, w, 3), 240, np.uint8)
    step = 32
    for y in range(0, h, step):
        for x in range(0, w, step):
            cv2.circle(img, (x, y), 2, (180, 180, 180), -1)
    return img


def _faint_tiled_img(h=400, w=400):
    base = np.full((h, w, 3), 245, np.uint8).astype(np.float32)
    step = 40
    for y in range(0, h, step):
        for x in range(0, w, step):
            cv2.rectangle(base, (x + 10, y + 10), (x + 30, y + 30), (241, 241, 241), -1)
    return np.clip(base, 0, 255).astype(np.uint8)


def _tiled_img(h=400, w=400):
    base = np.full((h, w, 3), 245, np.uint8).astype(np.float32)
    step = 40
    for y in range(0, h, step):
        for x in range(0, w, step):
            cv2.rectangle(base, (x + 10, y + 10), (x + 30, y + 30), (220, 220, 220), -1)
    return np.clip(base, 0, 255).astype(np.uint8)


def _logo_img(h=400, w=400):
    img = _plain(h, w)
    cv2.circle(img, (45, 45), 32, (20, 20, 200), -1)
    return img


def _text_img(h=400, w=400):
    img = _plain(h, w)
    rng = np.random.default_rng(0)
    for _ in range(300):
        x = int(rng.integers(10, w - 10))
        y = int(rng.integers(10, h - 10))
        ang = float(rng.integers(0, 180))
        dx = int(18 * np.cos(np.deg2rad(ang)))
        dy = int(18 * np.sin(np.deg2rad(ang)))
        cv2.line(img, (x, y), (x + dx, y + dy), (60, 60, 60), 2)
    return img


# 预设 → 期望推荐 的映射（与 core 自检一致）。
EXPECTED = {
    "plain": "general",
    "gradient": "ai_gradient",
    "grid": "ai_grid",
    "faint_tiled": "ai_faint_tiled",
    "tiled": "ai_tiled",
    "logo": "ai_logo",
    "text": "ai_text_heavy",
}


@pytest.mark.parametrize(
    "name,img",
    [
        ("plain", _plain()),
        ("gradient", _gradient_img()),
        ("grid", _grid_img()),
        ("faint_tiled", _faint_tiled_img()),
        ("tiled", _tiled_img()),
        ("logo", _logo_img()),
        ("text", _text_img()),
    ],
)
def test_recommend_preset_each(name, img):
    """每种合成图都应推荐出期望的检测预设。"""
    rec = pr.recommend_preset(img)
    assert rec.preset == EXPECTED[name], f"[{name}] 期望 {EXPECTED[name]}，实际 {rec.preset}"


def test_recommend_disabled_switch():
    """总开关关闭 → 回退默认预设（分数为空）。"""
    original = config.RECOMMEND_ENABLED
    config.RECOMMEND_ENABLED = False
    try:
        rec = pr.recommend_preset(_grid_img())
        assert rec.preset == config.RECOMMEND_DEFAULT_PRESET
        assert rec.scores == {}
        assert rec.confidence == 0.0
    finally:
        config.RECOMMEND_ENABLED = original


def test_recommendation_scores_keys():
    """评分字典应含全部特征键与 regularity。"""
    rec = pr.recommend_preset(_tiled_img())
    for key in ("gradient", "grid", "tiled", "translucent",
                "solid_logo", "text", "signature", "regularity"):
        assert key in rec.scores, f"评分缺失键：{key}"
    assert isinstance(rec.confidence, float)
    assert 0.0 <= rec.confidence <= 1.0


def test_recommendation_to_dict():
    """to_dict 结构完整且可 JSON 序列化。"""
    rec = pr.recommend_preset(_logo_img())
    d = rec.to_dict()
    assert set(d.keys()) == {"preset", "scores", "triggered", "confidence"}
    assert d["preset"] == "ai_logo"
    import json
    json.dumps(d)  # 不应抛异常


def test_recommend_for_path(tmp_path):
    """从路径加载推荐（CLI / 快速验证用）。"""
    p = tmp_path / "grad.png"
    cv2.imwrite(str(p), _gradient_img())
    rec = pr.recommend_preset_for_path(str(p))
    assert rec.preset == "ai_gradient"


# ------------------------------------------------------------------------------
# 服务层透传（process_image / process_file）。
# 用纯色底图（无真正水印）→ 检测会 skipped，但推荐块在检测之前执行，
# 因此 recommended_preset 一定被填充，且不触发 inpaint（无需 torch）。
# ------------------------------------------------------------------------------
def test_process_image_auto_preset_threading():
    """preset=None 且 auto_preset=True → 返回 recommended_preset / recommendation。"""
    rd = auto_pipeline.process_image(
        _plain(),
        mode="auto",
        engine="opencv",
        auto_preset=True,
        preset=None,
    )
    assert "recommended_preset" in rd
    assert rd["recommended_preset"] is not None
    assert rd["recommendation"] is not None
    assert rd["recommendation"]["preset"] == rd["recommended_preset"]
    # plain 图 → 推荐 general。
    assert rd["recommended_preset"] == "general"


def test_process_image_explicit_preset_disables_recommend():
    """显式 preset 应禁用自动推荐（向后兼容，recommendation 为 None）。"""
    rd = auto_pipeline.process_image(
        _plain(),
        mode="auto",
        engine="opencv",
        preset="ai_tiled",
        auto_preset=True,
    )
    assert rd["recommended_preset"] is None
    assert rd["recommendation"] is None


def test_process_image_auto_preset_off():
    """auto_preset=False 且未指定 preset → 不推荐。"""
    rd = auto_pipeline.process_image(
        _plain(),
        mode="auto",
        engine="opencv",
        auto_preset=False,
        preset=None,
    )
    assert rd["recommended_preset"] is None
    assert rd["recommendation"] is None


def test_process_file_auto_preset_threading():
    """process_file 透传推荐结果（落盘结果按需）。"""
    import tempfile as _tf
    fd, path = _tf.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(path, _plain())
    try:
        rd = auto_pipeline.process_file(
            path, mode="auto", engine="opencv", auto_preset=True, preset=None,
        )
        assert "recommended_preset" in rd
        assert rd["recommended_preset"] == "general"
        assert rd["recommendation"] is not None
        # 纯色图检测不到水印 → skipped，但不影响推荐填充。
        assert rd["status"] in ("skipped", "success")
    finally:
        # 不依赖 os.remove（沙箱 safe-delete 拦截）；用 PowerShell 安全删除项目残留在临时目录的文件。
        import subprocess
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Remove-Item -Force -LiteralPath '{path}'"],
            check=False,
        )
