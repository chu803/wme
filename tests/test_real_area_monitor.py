"""阶段 107 · `real_gate` 的「未承诺预设掩码面积」监控层回归测试。

背景（缺口由阶段 104/106 两次登记）：`real_gate` 此前只跑 `general`，而 `general` 的
`enhance_tiled=None` / `enable_grid=None` ⇒ **平铺增强与网格两条通路在真实图上结构性失明**
（阶段 106 把网格通路假阳性砍掉 76%，真实图侧一点都看不见）。

本阶段补的判据**不需要真值**：`mask_coverage = 掩码像素 / 图面像素`，于是
「这条通路会不会在真实照片上涂满全图」第一次成为机器可判定的。

本文件锁定：① `_area_stats` 的纯函数语义；② 面积红线只守上界、且必须低于检测器内置面积闸
（否则恒不触发 = 哑巴）；③ 漂移只罚变胖、永不罚变瘦；④ 监控集合必须恰好等于
`config.WATERMARK_TYPE_SCOPE` 里 `committed=False` 的四类（**承诺面变了就必须有人来更新这里**）；
⑤ 报告模式（红线表为空）不得误报。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from scripts import real_gate as rg  # noqa: E402

_BASELINE = os.path.join(_ROOT, "benchmark_real", "quality_baseline.json")


def _rows(spec):
    """spec: {preset: [coverage, ...]} → run_all 形态的逐图结果（供 _area_stats 消费）。"""
    n = max(len(v) for v in spec.values())
    rows = []
    for i in range(n):
        results = {}
        for preset, covs in spec.items():
            if i < len(covs):
                results[preset] = {"mask_coverage": covs[i], "pred_count": 3}
        rows.append({"image_id": f"{i:02d}", "results": results})
    return rows


def _summary(area_only, per_preset=None, area_presets=None):
    return {
        "config": {"presets": ["general"], "area_presets": list(area_presets or area_only),
                   "n_images": 30, "n_expected": 30, "dataset_dir": "benchmark_real"},
        "aggregate": {"per_preset": per_preset or {"general": {
            # 阶段 183：由伪造真值期锚点（0.1552/1.0/0.7518/0.1705/0.0365/3.75）
            # 同步到真实真值实测（177 基线，杠杆 OFF）。
            "n": 30, "precision": 0.0690, "recall": 0.8, "mean_iou": 0.5347,
            "mask_iou": 0.2975, "mask_coverage": 0.0291, "coverage_ratio": 2.8,
        }}, "area_only": area_only, "n_images": 30},
    }


# ---------------------------------------------------------------------------
# 1. 纯函数：面积画像
# ---------------------------------------------------------------------------

def test_area_stats_semantics():
    """均值/P90/最大/有掩码图数/平均区域数，且 P90 取最近秩。"""
    rows = _rows({"ai_grid": [0.10, 0.20, 0.30, 0.40]})
    s = rg._area_stats(rows, "ai_grid")
    assert s["n"] == 4
    assert s["mask_coverage_mean"] == pytest.approx(0.25)
    assert s["mask_coverage_max"] == pytest.approx(0.40)
    assert s["mask_coverage_p90"] == pytest.approx(0.40)   # 最近秩：idx=3
    assert s["n_with_mask"] == 4
    assert s["pred_count_mean"] == pytest.approx(3.0)


def test_area_stats_skips_errors_and_empty():
    """检测失败的行被剔除（否则读图失败会把均值拉低 = 静默变绿）。"""
    rows = [{"results": {"ai_grid": {"mask_coverage": 0.2, "pred_count": 2}}},
            {"results": {"ai_grid": {"error": "boom"}}},
            {"results": {}}]
    s = rg._area_stats(rows, "ai_grid")
    assert s["n"] == 1 and s["mask_coverage_mean"] == pytest.approx(0.2)
    assert rg._area_stats([], "ai_grid") is None


def test_area_stats_counts_silent_images():
    """全零掩码必须是「0/30」而不是「没有数据」——静默本身是一条可判定的事实。"""
    s = rg._area_stats(_rows({"ai_gradient": [0.0, 0.0, 0.0]}), "ai_gradient")
    assert s["n"] == 3 and s["n_with_mask"] == 0
    assert s["mask_coverage_mean"] == 0.0


# ---------------------------------------------------------------------------
# 2. 契约一致性（「承诺面变了必须有人来更新」）
# ---------------------------------------------------------------------------

def test_monitored_set_equals_uncommitted_classes():
    """面积监控集合必须**恰好**等于 `WATERMARK_TYPE_SCOPE` 里 committed=False 的类。

    这条把「谁来补监控」变成强制动作：某天把某类改成 `committed=True`（或新增一类），
    本用例会失败，从而逼迫同步 `AREA_ONLY_PRESETS` 与红线表——而不是让它静默少监控一类。
    """
    uncommitted = {k for k, v in config.WATERMARK_TYPE_SCOPE.items()
                   if not v.get("committed")}
    assert set(rg.AREA_ONLY_PRESETS) == uncommitted, (
        f"面积监控集合 {sorted(rg.AREA_ONLY_PRESETS)} 与未承诺类型 {sorted(uncommitted)} 不一致")


def test_contract_only_references_monitored_presets():
    assert set(rg.REAL_AREA_CONTRACT) <= set(rg.AREA_ONLY_PRESETS)
    # `general`（有真值）与 `ai_logo`（承诺类型）都走红线层，不得混进面积层
    assert "general" not in rg.REAL_AREA_CONTRACT
    assert "ai_logo" not in rg.REAL_AREA_CONTRACT


def test_area_redlines_are_upper_bounds_below_internal_cap():
    """面积红线只守上界，且**必须低于检测器内置面积闸**。

    后者是本阶段的一条硬约束：`ai_grid` 实测最大值 14.97% 正贴着内置闸 15%
    ⇒ 均值被闸压住；若把面积红线设到 ≥0.15，它将**永远不会触发**（哑巴红线）。
    """
    cap = float(config.DETECT_MAX_COVERAGE_RATIO)
    for preset, contract in rg.REAL_AREA_CONTRACT.items():
        for key, (direction, threshold) in contract.items():
            assert direction == "max", f"{preset}/{key} 面积只守上界"
            if key == "mask_coverage_mean":
                assert 0 < threshold < cap, (
                    f"{preset}/{key} 红线 {threshold} 不在 (0, {cap}) 内——"
                    "高于内置面积闸则永远不触发")


def test_area_redlines_are_not_born_red():
    """红线必须严格宽于基线实测（否则一上线就红灯）。基线缺失则跳过。"""
    if not os.path.exists(_BASELINE):
        pytest.skip("真实图基线快照不存在")
    with open(_BASELINE, encoding="utf-8") as f:
        base_area = (json.load(f).get("aggregate") or {}).get("area_only") or {}
    if not base_area:
        pytest.skip("基线尚未包含 area_only（需跑一次 real_gate.py --update）")
    for preset, contract in rg.REAL_AREA_CONTRACT.items():
        m = base_area.get(preset)
        if not m:
            continue
        for key, (_d, threshold) in contract.items():
            measured = m.get(key)
            if measured is None:
                continue
            assert measured <= threshold, (
                f"{preset}/{key} 实测 {measured} > 红线 {threshold}——红线过紧，会一上线就红")


# ---------------------------------------------------------------------------
# 3. 判定：红线 / 漂移 / 口径
# ---------------------------------------------------------------------------

def test_redline_catches_oversize_area(monkeypatch):
    monkeypatch.setattr(rg, "REAL_AREA_CONTRACT",
                        {"ai_grid": {"mask_coverage_mean": ("max", 0.12)}})
    s = _summary({"ai_grid": {"n": 30, "mask_coverage_mean": 0.30, "mask_coverage_p90": 0.4,
                              "mask_coverage_max": 0.4, "n_with_mask": 30,
                              "pred_count_mean": 100.0}})
    ok, problems = rg.evaluate_real(s, None)
    assert not ok
    assert any("[面积]" in p and "ai_grid" in p for p in problems), problems


def test_silence_guard_catches_gradient_firing(monkeypatch):
    """`ai_gradient` 的静默守卫：一旦 30 张真实图上开始出现掩码 ⇒ 必须有人更新契约。"""
    monkeypatch.setattr(rg, "REAL_AREA_CONTRACT",
                        {"ai_gradient": {"n_with_mask": ("max", 0)}})
    quiet = _summary({"ai_gradient": {"n": 30, "mask_coverage_mean": 0.0,
                                      "mask_coverage_p90": 0.0, "mask_coverage_max": 0.0,
                                      "n_with_mask": 0, "pred_count_mean": 0.0}})
    assert rg.evaluate_real(quiet, None)[0] is True
    fired = _summary({"ai_gradient": {"n": 30, "mask_coverage_mean": 0.9,
                                      "mask_coverage_p90": 0.9, "mask_coverage_max": 0.9,
                                      "n_with_mask": 27, "pred_count_mean": 1.0}})
    ok, problems = rg.evaluate_real(fired, None)
    assert not ok and any("[面积]" in p for p in problems), problems


def test_drift_catches_growth_and_never_punishes_shrink():
    """面积漂移单向：变胖超容差 ⇒ 报；变瘦（改进）⇒ 永不报。"""
    base = _summary({"ai_faint_tiled": {"n": 30, "mask_coverage_mean": 0.06,
                                        "mask_coverage_p90": 0.11,
                                        "mask_coverage_max": 0.14,
                                        "n_with_mask": 30, "pred_count_mean": 38.0}})
    fat = _summary({"ai_faint_tiled": {"n": 30, "mask_coverage_mean": 0.09,
                                       "mask_coverage_p90": 0.11,
                                       "mask_coverage_max": 0.14,
                                       "n_with_mask": 30, "pred_count_mean": 38.0}})
    thin = _summary({"ai_faint_tiled": {"n": 30, "mask_coverage_mean": 0.02,
                                        "mask_coverage_p90": 0.04,
                                        "mask_coverage_max": 0.05,
                                        "n_with_mask": 30, "pred_count_mean": 38.0}})
    ok_fat, p_fat = rg.evaluate_real(fat, base)
    assert not ok_fat and any("[面积漂移]" in p for p in p_fat), p_fat
    ok_thin, p_thin = rg.evaluate_real(thin, base)
    assert ok_thin, p_thin


def test_missing_area_only_in_baseline_is_flagged():
    """旧版基线（无 `area_only`）必须提示刷新，而不是静默跳过整层。"""
    old = _summary({"ai_grid": {"n": 30, "mask_coverage_mean": 0.10,
                                "mask_coverage_p90": 0.1, "mask_coverage_max": 0.1,
                                "n_with_mask": 30, "pred_count_mean": 5.0}})
    old["aggregate"].pop("area_only")
    ok, problems = rg.evaluate_real(_summary({"ai_grid": {
        "n": 30, "mask_coverage_mean": 0.10, "mask_coverage_p90": 0.1,
        "mask_coverage_max": 0.1, "n_with_mask": 30, "pred_count_mean": 5.0}}), old)
    assert not ok and any("area_only" in p for p in problems), problems


def test_area_preset_set_mismatch_is_flagged():
    base = _summary({"ai_grid": {"n": 30, "mask_coverage_mean": 0.10,
                                 "mask_coverage_p90": 0.1, "mask_coverage_max": 0.1,
                                 "n_with_mask": 30, "pred_count_mean": 5.0}})
    now = _summary({"ai_faint_tiled": {"n": 30, "mask_coverage_mean": 0.06,
                                       "mask_coverage_p90": 0.1, "mask_coverage_max": 0.1,
                                       "n_with_mask": 30, "pred_count_mean": 5.0}})
    ok, problems = rg.evaluate_real(now, base)
    assert not ok and any("面积监控预设集合" in p for p in problems), problems


def test_report_only_mode_never_fails(monkeypatch):
    """红线表为空（未标定）= 只测量不判定 —— 反例自检，证明判定完全由数据驱动。

    若把 `REAL_AREA_CONTRACT` 清空后仍然报「面积」问题，说明判定与契约脱钩。
    """
    monkeypatch.setattr(rg, "REAL_AREA_CONTRACT", {})
    s = _summary({"ai_grid": {"n": 30, "mask_coverage_mean": 0.99,
                              "mask_coverage_p90": 0.99, "mask_coverage_max": 0.99,
                              "n_with_mask": 30, "pred_count_mean": 999.0}})
    ok, problems = rg.evaluate_real(s, None)
    assert ok, problems


def test_no_area_mode_is_explicit_not_silent():
    """`--no-area` 必须**显式**跳过（`area_skipped=True`），否则「少了四个预设」会判失败。

    两侧都钉住：
      · `area_skipped=False` + 面积集合与基线不一致 ⇒ **失败**（默认路径不允许静默少监控）；
      · `area_skipped=True` ⇒ 通过（由调用方负责打印警告——`main()` 里已加）。
    """
    base = _summary({"ai_grid": {"n": 30, "mask_coverage_mean": 0.10,
                                 "mask_coverage_p90": 0.1, "mask_coverage_max": 0.1,
                                 "n_with_mask": 30, "pred_count_mean": 5.0}})
    skipped = _summary({})                      # 面积层被跳过 ⇒ area_presets 为空
    skipped["config"]["area_presets"] = []
    ok, problems = rg.evaluate_real(skipped, base)
    assert not ok and any("面积监控预设集合" in p for p in problems), problems
    ok2, problems2 = rg.evaluate_real(skipped, base, area_skipped=True)
    assert ok2, problems2
