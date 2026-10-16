# -*- coding: utf-8 -*-
"""
阶段 284 · `core/preset_recommender` 深度续审（5j 复审链延续，接 276–283）。

首轮审计（阶段 25 基础单测 + 阶段 109 真值审计 + 精度套件）已覆盖：
  · 各类合成图 → 预设映射（gradient/grid/faint_tiled/tiled/logo/text/plain→general）；
  · `RECOMMEND_ENABLED` 总开关关闭 → 回退默认预设；
  · 评分字典 8 键齐全、confidence ∈ [0,1]、`to_dict` 可 JSON 序列化、`recommend_preset_for_path`；
  · **阶段 109 P0**：`_periodicity` 由 AND 改 OR（与 detector 对齐）——一维条纹不再被
    推给 recall=0 的 `ai_text_heavy`；旧 AND 实现副本反证守卫非空；真实照片零副作用；
  · **死参数** `sensitivity` 自阶段 59 起不影响结果（行为 + 源码双向钉住 + docstring 一致）；
  · i18n 出口守卫：核心层 `__str__` 的中文不得被 UI/服务字符串化。

本轮按「契约输入类别枚举」钉死**首轮遗漏的成功/边界分支不变量**（探针实证全部正确，零真缺陷）：
  · `_ensure_rgb` 输入矩阵：None→ValueError；2D 灰度→堆叠 3 通道；RGBA 剥 alpha；
    (H,W,2)/1D/4D → ValueError；float32 → 转 uint8；
  · `_dominant_period` 五种退化输入（空/长度<8/全零/常量/正常条纹）⇒ 恒返回 period=-1；
  · `_marks_at` **兼容路径**（residual=None）与全 NaN / 常量 score（MAD=0）⇒ 全零掩码；
  · `_spectral_regularity` 全零掩码 → 0.0；
  · `_gradient_features` 均匀图（var_gray<1e-6）→ (0,0,0)；
  · `_corner_solid_count` / `_text_density` 极小图（1×1/4×4/16×16）⇒ 0，不崩；
  · 极小图走完整 `recommend_preset` ⇒ general 兜底，不崩；
  · **决策不变量**：confidence == max(scores)；scores 恒 8 键且全在 [0,1]；
    triggered ⊆ scores 键；
  · `Recommendation.to_dict` **深拷贝**（改返回值不污染原对象）；空 triggered 的 `__str__` 含「无」；
  · 兜底分支 `trans_act >= 0.3` ⇒ triggered=["translucent"]（用桩隔离其余特征族，确定性验证）。

零真缺陷（与 276–283 同构）：首轮遗漏的是「未钉边界」而非代码缺陷。
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
import core.preset_recommender as pr  # noqa: E402

H = W = 400


# --------------------------------------------------------------------------- #
# 1 · _ensure_rgb 输入矩阵
# --------------------------------------------------------------------------- #
def test_ensure_rgb_rejects_none():
    with pytest.raises(ValueError):
        pr._ensure_rgb(None)


def test_ensure_rgb_promotes_gray_to_3ch():
    out = pr._ensure_rgb(np.full((8, 8), 128, np.uint8))
    assert out.shape == (8, 8, 3) and out.dtype == np.uint8
    assert (out[:, :, 0] == out[:, :, 2]).all(), "灰度堆叠应三通道等值"


def test_ensure_rgb_strips_alpha():
    rgba = np.dstack([np.full((8, 8), 10, np.uint8)] * 3 + [np.full((8, 8), 255, np.uint8)])
    out = pr._ensure_rgb(rgba)
    assert out.shape == (8, 8, 3), "RGBA 应剥掉 alpha 通道"
    assert (out[:, :, 0] == 10).all(), "应保留 RGB 内容而非 alpha"


@pytest.mark.parametrize("shape", [(8, 8, 2), (8,), (4, 4, 4, 4)])
def test_ensure_rgb_rejects_bad_dims(shape):
    with pytest.raises(ValueError):
        pr._ensure_rgb(np.zeros(shape, np.uint8))


def test_ensure_rgb_casts_float_to_uint8():
    out = pr._ensure_rgb(np.zeros((8, 8, 3), np.float32))
    assert out.dtype == np.uint8, "float 输入须转 uint8（后续 cv2 依赖）"


# --------------------------------------------------------------------------- #
# 2 · _dominant_period 退化输入
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("proj_factory", [
    lambda: np.array([], np.float32),                 # 空
    lambda: np.ones(5, np.float32),                   # 长度 < 8
    lambda: np.zeros(20, np.float32),                 # sum < 1
    lambda: np.full(20, 5.0, np.float32),             # 常量 ⇒ std < eps
])
def test_dominant_period_degenerate_returns_minus_one(proj_factory):
    period, strength = pr._dominant_period(proj_factory())
    assert period == -1.0 and strength == 0.0, "退化输入须返回 (‑1, 0)"


def test_dominant_period_on_real_stripe():
    """正常条纹应检出周期（对照组，证明上面的退化断言不是因为恒返回 -1）。"""
    proj = np.zeros(40, np.float32)
    proj[::8] = 10.0
    period, strength = pr._dominant_period(proj)
    assert period > 0 and 0 < strength <= 1.0


# --------------------------------------------------------------------------- #
# 3 · _marks_at 兼容路径 / 全 NaN / 常量 score
# --------------------------------------------------------------------------- #
def test_marks_at_compat_path_residual_none():
    """residual=None 的兼容路径：只按分数门限，仍须产出 0/255 掩码不崩。"""
    score = np.random.default_rng(0).random((32, 32)).astype(np.float32)
    m = pr._marks_at(score, 0.5, None)
    assert m.shape == (32, 32) and m.dtype == np.uint8
    assert set(np.unique(m).tolist()) <= {0, 255}


@pytest.mark.parametrize("residual", [None, np.zeros((16, 16), np.float32)])
def test_marks_at_all_nan_returns_zero(residual):
    """全 NaN 分数 ⇒ 有限值集合为空 ⇒ 全零掩码（不抛错、不返回垃圾）。"""
    nan = np.full((16, 16), np.nan, np.float32)
    m = pr._marks_at(nan, 0.5, residual)
    assert int((m > 0).sum()) == 0


def test_marks_at_constant_score_zero_marks():
    """常量 score ⇒ MAD=0 ⇒ 阈值==中位数 ⇒ `score > thr` 全 False ⇒ 零标记。"""
    const = np.full((16, 16), 3.0, np.float32)
    m = pr._marks_at(const, 0.5, np.full((16, 16), 100.0, np.float32))
    assert int((m > 0).sum()) == 0, "常量分数不应产生任何标记（MAD=0）"


# --------------------------------------------------------------------------- #
# 4 · _spectral_regularity 退化
# --------------------------------------------------------------------------- #
def test_spectral_regularity_zero_mask():
    assert pr._spectral_regularity(np.zeros((32, 32), np.uint8)) == 0.0


# --------------------------------------------------------------------------- #
# 5 · _gradient_features 均匀图
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("val", [0, 240])
def test_gradient_features_uniform_returns_zeros(val):
    """均匀图 ⇒ var_gray < 1e-6 ⇒ 三元组全 0（否则 explained 会是 NaN）。"""
    e, pm, rs = pr._gradient_features(np.full((64, 64), val, np.uint8))
    assert (e, pm, rs) == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# 6 · 极小图不崩
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("hw", [(1, 1), (4, 4), (16, 16)])
def test_small_images_helpers_do_not_crash(hw):
    g = np.full((hw[0], hw[1]), 240, np.uint8)
    assert pr._corner_solid_count(g, hw[0], hw[1]) == 0
    overall, bottom = pr._text_density(g, hw[0], hw[1])
    assert 0.0 <= overall <= 1.0 and 0.0 <= bottom <= 1.0


@pytest.mark.parametrize("hw", [(1, 1), (8, 8), (16, 16), (32, 32)])
def test_small_images_full_pipeline_falls_back_to_general(hw):
    img = np.full((hw[0], hw[1], 3), 240, np.uint8)
    rec = pr.recommend_preset(img)
    assert rec.preset == config.RECOMMEND_DEFAULT_PRESET
    assert rec.confidence == 0.0


# --------------------------------------------------------------------------- #
# 7 · 决策不变量：confidence == max(scores)
# --------------------------------------------------------------------------- #
def _tiled_fixture():
    base = np.full((H, W, 3), 245, np.float32)
    for y in range(0, H, 40):
        for x in range(0, W, 40):
            base[y + 10:y + 30, x + 10:x + 30] = 220
    return np.clip(base, 0, 255).astype(np.uint8)


def test_confidence_equals_max_scores():
    """confidence 必须恒等于 max(scores.values())——多处消费方据此显示置信度。"""
    rec = pr.recommend_preset(_tiled_fixture())
    assert abs(rec.confidence - max(rec.scores.values())) < 1e-9


# 阶段 450：scores 键集契约（新增 corner_watermark 角标模板匹配信号，与
# utils/recommend_view._FEATURE_KEYS、utils/i18n 双语标签同名同序，见 test_utils_audit98 I3）。
_EXPECTED_SCORE_KEYS = {
    "gradient", "grid", "tiled", "translucent", "solid_logo",
    "corner_watermark", "text", "signature", "regularity",
}


def test_scores_shape_and_range():
    rec = pr.recommend_preset(_tiled_fixture())
    assert set(rec.scores) == _EXPECTED_SCORE_KEYS, (
        f"评分字典键集应为 9 键（阶段450 增 corner_watermark）：{set(rec.scores)}")
    assert all(0.0 <= v <= 1.0 for v in rec.scores.values()), "分值越界"
    assert set(rec.triggered) <= set(rec.scores), "triggered 必须是 scores 键的子集"


# --------------------------------------------------------------------------- #
# 8 · Recommendation 数据结构
# --------------------------------------------------------------------------- #
def test_to_dict_is_deep_copy():
    """to_dict 必须深拷贝：改返回值不得污染原对象（否则调用方会篡改内部状态）。"""
    r = pr.Recommendation(preset="ai_grid", scores={"grid": 0.9},
                          triggered=["grid"], confidence=0.9)
    d = r.to_dict()
    d["scores"]["grid"] = 999
    d["triggered"].append("injected")
    assert r.scores == {"grid": 0.9}, f"原 scores 被污染：{r.scores}"
    assert r.triggered == ["grid"], f"原 triggered 被污染：{r.triggered}"


def test_str_shows_none_when_triggered_empty():
    r = pr.Recommendation(preset="general")
    assert "无" in str(r), "空 triggered 的 __str__ 应显示「无」"
    assert r.confidence == 0.0


# --------------------------------------------------------------------------- #
# 9 · 兜底分支：trans_act >= 0.3 ⇒ triggered=["translucent"]
# --------------------------------------------------------------------------- #
def test_fallback_translucent_branch(monkeypatch):
    """无局部强信号但半透明覆盖够高 ⇒ 兜底 general 且 triggered=["translucent"]。

    用桩隔离其余特征族（logo/text/periodic/regularity 全部归零），只留
    trans_act 一个非零信号——确定性命中 `else` 分支的 `trans_act >= 0.3` 路径。
    """
    # 构造 marks 覆盖 ~8%（⇒ trans_act≈0.53 ≥0.3），其余特征一律清零。
    marks = np.zeros((H, W), np.uint8)
    marks[100:180, 100:180] = 255          # (80*80)/(400*400)=4%
    marks[200:280, 200:280] = 255          # 合计 8%
    residual = np.where(marks > 0, 100.0, 0.0).astype(np.float32)

    # 直接桩掉 _marks_at：否则全零 score 会被稳健门限压成零标记、coverage 归 0。
    monkeypatch.setattr(pr, "_marks_at", lambda score, sens, res=None: marks.copy())
    monkeypatch.setattr(pr, "_build_residual",
                        lambda rgb: (np.zeros((H, W), np.uint8), residual,
                                     np.zeros((H, W), np.float32), H, W))
    monkeypatch.setattr(pr, "_gradient_features", lambda g: (0.0, 0.0, 0.0))
    monkeypatch.setattr(pr, "_periodicity", lambda m, h, w: (False, 0.0))
    monkeypatch.setattr(pr, "_spectral_regularity", lambda m: 0.0)
    monkeypatch.setattr(pr, "_corner_solid_count", lambda g, h, w: 0)
    monkeypatch.setattr(pr, "_text_density", lambda g, h, w: (0.0, 0.0))

    rec = pr.recommend_preset(np.full((H, W, 3), 240, np.uint8))
    assert rec.preset == config.RECOMMEND_DEFAULT_PRESET, "无局部信号应落 general 兜底"
    assert rec.triggered == ["translucent"], \
        f"半透明覆盖达阈值时应记一笔 translucent，实际 {rec.triggered}"
    assert rec.scores["translucent"] >= 0.3