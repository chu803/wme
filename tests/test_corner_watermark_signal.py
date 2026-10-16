# -*- coding: utf-8 -*-
"""
阶段 450 · `core/preset_recommender` 角标水印判据回归守卫。

背景：豆包类「AI 生成」水印固定在右下/中右，是半透明/低对比平台角标。早期「边缘密度
不对称性」启发式经全量阈值扫描证实不可靠（最优仅 6/75 命中、漏 69，且误命中自然图），
故改用**边缘图 NCC 模板匹配**（模板内嵌于 core/preset_recommender.py，自包含）。

真实图召回/精确由 scripts/verify_corner_reco.py（benchmark 88 图）实测（NCC 默认 0.40：
右下角标 ~24/50 → ai_logo、自然图 0 误命中）。本单测锁定**不依赖 benchmark 资产**的部分：

  · 内嵌模板可解码为 48×64 且缓存生效；
  · 纯净图 / 弥漫满图文字图 → 信号不命中（0.0），不回归 ai_text_heavy / general；
  · 决策分支：信号命中（monkeypatch）→ recommend 导向 ai_logo（edge=False，
    跳过 edge 策略的 OCR 空跑），triggered 含 corner_watermark；
  · fail-closed：NCC 阈值抬到 1.0 或总开关关闭 ⇒ 信号失效、不改判 ai_logo（可逆）。
"""

import numpy as np
import pytest

import config
import core.preset_recommender as pr

_H = _W = 400


def _cv2():
    import cv2
    return cv2


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    return _cv2().cvtColor(rgb, _cv2().COLOR_RGB2GRAY)


def _plain() -> np.ndarray:
    return np.full((_H, _W, 3), 240, np.uint8)


def _diffuse_text() -> np.ndarray:
    """满图随机短笔画（弥漫文字），四角密度相近 ⇒ 不应命中右下孤立角标。"""
    img = _plain()
    cv2 = _cv2()
    rng = np.random.default_rng(0)
    for _ in range(300):
        x = int(rng.integers(10, _W - 10))
        y = int(rng.integers(10, _H - 10))
        ang = float(rng.integers(0, 180))
        dx = int(18 * np.cos(np.deg2rad(ang)))
        dy = int(18 * np.sin(np.deg2rad(ang)))
        cv2.line(img, (x, y), (x + dx, y + dy), (60, 60, 60), 2)
    return img


# ------------------------------------------------------------------------------
# 模板本身
# ------------------------------------------------------------------------------
def test_template_decodes_to_expected_shape():
    tpl = pr._load_corner_watermark_template()
    assert tpl is not None, "内嵌角标模板应可解码"
    assert tpl.shape == (64, 48), f"模板形状应为 (64, 48)，实际 {tpl.shape}"
    assert pr._load_corner_watermark_template() is tpl, "二次调用应命中缓存"


# ------------------------------------------------------------------------------
# 信号不误命中（无 false positive）
# ------------------------------------------------------------------------------
def test_signal_silent_on_plain():
    assert pr._corner_watermark_signal(_to_gray(_plain())) == 0.0


def test_signal_silent_on_diffuse_text():
    """弥漫满图文字不是右下孤立角标 ⇒ 不命中（避免回归 ai_text_heavy）。"""
    assert pr._corner_watermark_signal(_to_gray(_diffuse_text())) == 0.0


def test_recommend_diffuse_text_stays_text_heavy():
    rec = pr.recommend_preset(_diffuse_text())
    assert rec.preset == "ai_text_heavy", (
        f"弥漫文字图应维持 ai_text_heavy，实际 {rec.preset}；scores={rec.scores}")


def test_recommend_plain_stays_general():
    assert pr.recommend_preset(_plain()).preset == config.RECOMMEND_DEFAULT_PRESET


# ------------------------------------------------------------------------------
# 决策分支：信号命中 → ai_logo（用 monkeypatch 隔离，不依赖合成匹配）
# ------------------------------------------------------------------------------
def test_recommend_routes_corner_watermark_to_ai_logo(monkeypatch):
    """信号命中 ⇒ 导向 ai_logo（edge=False），且 corner_watermark 计入触发特征。"""
    monkeypatch.setattr(pr, "_corner_watermark_signal", lambda g: 1.0)
    rec = pr.recommend_preset(_plain())
    assert rec.preset == "ai_logo", (
        f"角标命中应导向 ai_logo，实际 {rec.preset}；scores={rec.scores}")
    assert "corner_watermark" in rec.triggered
    assert rec.scores.get("corner_watermark") == 1.0


def test_corner_watermark_wins_over_grid(monkeypatch):
    """即便周期/网格信号也触发，corner_watermark 仍优先走 ai_logo（角标为真实类型）。"""
    monkeypatch.setattr(pr, "_corner_watermark_signal", lambda g: 1.0)
    # _grid_img 有强周期 ⇒ 会命中 f_periodic；此时角标应压过网格。
    cv2 = _cv2()
    img = np.full((_H, _W, 3), 240, np.uint8)
    step = 32
    for yy in range(0, _H, step):
        for xx in range(0, _W, step):
            cv2.circle(img, (xx, yy), 2, (180, 180, 180), -1)
    rec = pr.recommend_preset(img)
    assert rec.preset == "ai_logo", (
        f"角标+网格应走 ai_logo，实际 {rec.preset}；scores={rec.scores}")


# ------------------------------------------------------------------------------
# fail-closed：阈值 / 总开关可逆失效
# ------------------------------------------------------------------------------
def test_threshold_guard_blocks_low_confidence(monkeypatch):
    """NCC 阈值抬到 1.0（不可能达到）⇒ 纯净图仍不命中（阈值杠杆有效）。"""
    monkeypatch.setattr(config, "RECOMMEND_CORNER_WATERMARK_NCC_MIN", 1.0)
    assert pr._corner_watermark_signal(_to_gray(_plain())) == 0.0


def test_disabled_signal_does_not_route_to_ai_logo(monkeypatch):
    """总开关关闭 ⇒ 信号恒 0，recommend 不因角标改判 ai_logo。"""
    monkeypatch.setattr(config, "RECOMMEND_CORNER_WATERMARK_ENABLED", False)
    assert pr._corner_watermark_signal(_to_gray(_plain())) == 0.0
    # 即便把信号强制为 1.0，开关关闭时 recommend_preset 也不会采用它（源码层判 enabled）。
    monkeypatch.setattr(pr, "_corner_watermark_signal", lambda g: 1.0)
    rec = pr.recommend_preset(_plain())
    assert rec.preset != "ai_logo", "总开关关闭时不得因角标改判 ai_logo"
    assert rec.scores.get("corner_watermark") == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
