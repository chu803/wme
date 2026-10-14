"""阶段 93 · 验证面缺口收敛的回归测试。

覆盖三条缺口（见 `docs/92`）：
  · **S2 过检面积无红线** → `mask_coverage` / `coverage_ratio` / `mask_iou` 三条红线，
    以及「漂移方向从红线方向派生」这一修正（`mask_coverage` 是越高越坏）。
  · **S1 未标注被读成没漏检** → `annotation_scope()` 把「没标」与「没有」区分开，
    默认只告警（不制造假红）、`--strict-scope` 可升级为失败。
  · **S4 文档承诺超出证据** → `config.WATERMARK_TYPE_SCOPE` 作为单一真值来源，
    README 的类型清单必须 ⊆ 该表键集合。

设计要点（对齐项目既有守卫风格）：
  · 判定逻辑用**纯函数 + 确定数据**锁死（不读真实图、不依赖 LaMa）；
  · 阈值的**标定区间**必须被断言（禁止把红线放宽到变哑巴）；
  · 「崩溃护栏」必须显式登记（新增一条红线的决策点）。
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import real_gate as rg          # noqa: E402
import config                                # noqa: E402

README_PATH = os.path.join(_ROOT, "README.md")
BASELINE_PATH = os.path.join(_ROOT, "benchmark_real", "quality_baseline.json")
TRUTH_PATH = os.path.join(_ROOT, "benchmark_real", "ground_truth.json")


# ---------------------------------------------------------------------------
# 构造确定数据
# ---------------------------------------------------------------------------

def _m(n=30, precision=0.0761, recall=0.8000, mean_iou=0.5347, mask_iou=0.2714,
       mask_coverage=0.0107, coverage_ratio=1.92):
    """一组「恰好等于当前基线」的指标（各字段可覆盖以构造越线场景）。

    演进：阶段 86 基线（coverage 0.1618 / ratio 16.62 / mask_iou 0.0635）
    → 阶段 99 把 `DETECT_MAX_COVERAGE_RATIO` 0.30→0.15（0.1266 / 13.00 / 0.0699）
    → **阶段 100 把角标 MSER 降级通路默认关闭**（0.0745 / 7.65 / 0.1163，
    precision 0.0392→0.1382，mean_iou 0.7573→0.7518）
    → **阶段 106 框内证据几何收敛**（pad 2→1、k_scale 0.5→0.65）：
    **0.0365 / 3.75 / 0.1705**、precision **0.1552**，recall 与 mean_iou 逐位不变。
    → ⚠ **阶段 183 重锚**：阶段 106 的全部数值**标定于伪造真值期**（阶段 149 判定
    原 60 条 corner_logo GT 系模板复制；172 收编 10 张真实豆包图 + 逐图实测真值后，
    177 全面重算）。故本节锚点更新为**真实真值实测**（177 基线，杠杆 OFF，已由
    183 单预设门禁复现逐位一致）：    **0.0291 / 2.80 / 0.2975**、
    precision **0.0690**、recall **0.8000**、mean_iou **0.5347**。
    → ⚠ **阶段 240（Option A）再锚**：2026-10-05 导入 q 系列 11 张（数据集 10→29 图，
    豆包角标更小更靠角）使过检天然更低；当前 29 图实测 precision 0.0761 /
    mask_coverage 0.0107 / coverage_ratio 1.92 / mask_iou 0.2714（与基线文件逐位一致），
    故本节锚点同步到该值。红线随之从 0.045 / 4.35× 下移到 0.020 / 3.50×
    （mask_iou 0.15 不变，三者均满足「红线夹逼实测」不变式）。
    本函数是「红线夹逼」断言的实测锚点，**每阶段必须同步更新**，
    否则断言会比对旧世界（经验 37：新增阶段同步更新标定表）。
    """
    return {"n": n, "precision": precision, "recall": recall, "mean_iou": mean_iou,
            "mask_iou": mask_iou, "mask_coverage": mask_coverage,
            "coverage_ratio": coverage_ratio}


def _summary(per_preset=None, n_images=30, n_expected=30, presets=("general",), scope=None):
    s = {
        "config": {"presets": list(presets), "n_images": n_images,
                   "n_expected": n_expected, "dataset_dir": "benchmark_real"},
        "aggregate": {"per_preset": per_preset if per_preset is not None
                      else {"general": _m()}, "n_images": n_images},
    }
    if scope is not None:
        s["scope"] = scope
    return s


def _baseline(per_preset=None, presets=("general",)):
    # 阶段 107：基线结构新增 `aggregate.area_only` / `config.area_presets`
    # （未承诺预设的面积画像）。夹具必须带上这两个键，否则会被
    # 「旧版基线，请 --update 刷新」这条**刻意的硬失败**拦下 —— 同步夹具，不放宽判定。
    return {
        "config": {"presets": list(presets), "area_presets": [],
                   "n_images": 30, "n_expected": 30},
        "aggregate": {"per_preset": per_preset if per_preset is not None
                      else {"general": _m()}, "area_only": {}, "n_images": 30},
    }


def _incomplete_scope(**over):
    base = {"labeled": ["corner_logo"], "n_labeled_styles": 1, "declared_images": 0,
            "unspecified_images": 30, "contradictory": [], "unknown_styles": [],
            "complete": False}
    base.update(over)
    return base


def _manifest(hints):
    return {"images": [{"id": f"{i:02d}", "watermark_style_hint": h}
                       for i, h in enumerate(hints, 1)]}


def _truth(styles):
    return {"entries": [{"image_id": f"{i:02d}",
                         "watermarks": [{"bbox": [0, 0, 10, 10], "style": s} for s in ss]}
                        for i, ss in enumerate(styles, 1)]}


# ===========================================================================
# S2 · 过检面积红线
# ===========================================================================

def test_pass_at_measured_baseline():
    """基线值必须通过——红线不能一上线就红灯。"""
    ok, problems = rg.evaluate_real(_summary(), None)
    assert ok, problems


def test_coverage_red_line_fails_when_mask_explodes():
    ok, problems = rg.evaluate_real(_summary({"general": _m(mask_coverage=0.40)}), None)
    assert not ok
    assert any("mask_coverage" in p for p in problems)


def test_coverage_ratio_red_line_fails():
    ok, problems = rg.evaluate_real(_summary({"general": _m(coverage_ratio=99.0)}), None)
    assert not ok
    assert any("coverage_ratio" in p for p in problems)


def test_mask_iou_red_line_fails():
    ok, problems = rg.evaluate_real(_summary({"general": _m(mask_iou=0.01)}), None)
    assert not ok
    assert any("mask_iou" in p for p in problems)


def test_coverage_direction_is_upward():
    """`mask_coverage` 越高越坏——这是本次修正的核心，缩水的旧实现查不出来。"""
    assert rg.REAL_PRESET_CONTRACT["general"]["mask_coverage"][0] == "max"
    assert rg.REAL_PRESET_CONTRACT["general"]["coverage_ratio"][0] == "max"
    assert rg.REAL_PRESET_CONTRACT["general"]["mask_iou"][0] == "min"


def test_drift_detects_coverage_rise():
    """掩码从 0.0107 涨到 0.016（+49%）必须报漂移，且措辞是「上升」而非「下滑」。

    取值刻意落在 **(漂移线 0.0107×1.15=0.0123, 红线 0.020)** 之间：
    阶段 106 把红线从 0.11 压到 0.045；阶段 240（Option A）因 q 系列扩图使过检天然更低，
    实测 0.0107、红线再下移到 0.020，漂移容差仍 15%，隔离窗口相应收窄到 [1.15×, 红线)。
    本用例取 0.016（> 漂移线 0.0123、< 红线 0.020），只触发漂移、不越红线，
    从而保持「只测漂移方向语义」的隔离性。
    """
    now = _summary({"general": _m(mask_coverage=0.016)})
    base = _baseline()
    ok, problems = rg.evaluate_real(now, base)
    assert not ok
    hit = [p for p in problems if "mask_coverage" in p]
    assert hit, problems
    assert "上升" in hit[0]


def test_drift_detects_mask_iou_drop():
    now = _summary({"general": _m(mask_iou=0.030)})
    ok, problems = rg.evaluate_real(now, _baseline())
    assert not ok
    # 0.0300 同时越红线与漂移，故必须挑出**漂移**那条（而不是取第一条）。
    hit = [p for p in problems if "mask_iou" in p and "漂移" in p]
    assert hit, problems
    assert "下滑" in hit[0]


def test_drift_improvement_in_either_direction_never_fails():
    """两个方向都变好（覆盖率降、IoU 升）绝不能报错。

    阶段 240（Option A）按 29 图真实实测（0.0107 / 1.92 / 0.2714）重锚取值，
    确保三项**都是改进方向**且在各自 15% 漂移容差内（0.0100 / 1.85 / 0.28）。
    """
    now = _summary({"general": _m(mask_coverage=0.0100, coverage_ratio=1.85, mask_iou=0.28)})
    ok, problems = rg.evaluate_real(now, _baseline())
    assert ok, problems


def test_unregistered_metric_defaults_to_downward():
    """未登记契约的指标默认「越高越好」——不能因为忘记写方向而静默失效。"""
    assert rg._metric_direction("not_a_metric", "general") == "min"
    assert rg._drift_worse("min", now=0.5, base=1.0, rel_tol=0.1) is True
    assert rg._drift_worse("max", now=0.5, base=1.0, rel_tol=0.1) is False


def test_area_metrics_are_declared_crash_guards():
    """三条面积红线必须是**显式登记**的崩溃护栏。

    这个断言是「新增红线 = 一个决策点」的守卫：若有人把它们挪回
    「结论编码」类，就必须同时提供标定实验值，不能悄悄改分类。
    """
    for key in ("mask_coverage", "coverage_ratio", "mask_iou"):
        assert key in rg.REAL_CRASH_GUARD_METRICS, f"{key} 未登记为崩溃护栏"
        assert key not in rg.REAL_REDLINE_CALIBRATION, f"{key} 不该出现在标定表"


@pytest.mark.parametrize("key,measured", [
    ("mask_coverage", 0.0107),
    ("coverage_ratio", 1.92),
])
def test_max_red_lines_are_tight_but_not_born_red(key, measured):
    """`max` 型红线必须 **严格宽于实测、且不超过实测的 2 倍**。

    窄侧防「一上线就红灯」，宽侧防「放宽到变哑巴」——两侧都要守。
    阶段 240（Option A）按 29 图真实实测（0.0107 / 1.92）重锚。
    """
    _dir, thr = rg.REAL_PRESET_CONTRACT["general"][key]
    assert measured < thr, f"{key} 红线 {thr} 比实测 {measured} 还紧，会一上线就红灯"
    assert thr <= measured * 2.0, f"{key} 红线 {thr} 超过实测 {measured} 的 2 倍，等于哑巴"


def test_mask_iou_red_line_brackets_measured():
    """红线须夹在 [实测/2, 实测)：窄侧防一上线就红，宽侧防放宽到哑巴。

    阶段 106：实测 0.1163 → **0.1705**，红线随之 0.077 → **0.10**
    （0.1705/2 = 0.08525 ≤ 0.10 < 0.1705）。
    ⚠ **阶段 183**：177 用真实真值重算后实测抬到 **0.2975**（实测/2 = 0.14875），
    而红线仍停在被伪造真值期实测 0.1705 校准的 0.10 ⇒ **松于实测的一半**，
    违反本断言的不变式（也即 `real_gate.py` 注释里那条「红线不得松于实测的一半」）。
    按同一规则把红线抬到 **0.15**（= 实测 × 0.504），既守住防哑巴，又不至于一上线就红。
    ⚠ **阶段 240（Option A）**：q 系列扩图后 29 图实测抬到 **0.2714**（实测/2 = 0.1357），
    红线 0.15 仍满足 0.1357 ≤ 0.15 < 0.2714，夹逼不变式保持不变。
    """
    _dir, thr = rg.REAL_PRESET_CONTRACT["general"]["mask_iou"]
    measured = 0.2714
    assert measured / 2.0 <= thr < measured, f"mask_iou 红线 {thr} 未夹在合理区间"


# ===========================================================================
# S1 · 标注覆盖面契约
# ===========================================================================

def test_scope_detects_catch_all_gap():
    """catch-all 声明必须被**显式记录**（阶段 93 的意图，阶段 95 调整口径）。

    阶段 95 语义变更（用户决定「只处理角标 LOGO 类图片」）：
        `unspecified_images` 由「覆盖面缺口」降级为「**提示精度诊断**」。
        理由：承诺面已声明为「只做角标」且角标有真值，此时 recall 度量的正好是
        被承诺的那一类，而清单写 `mixed` 只是说明「这 30 张里可能混有别的样式」——
        那些样式不在承诺范围内，引用数字时带上范围说明即可（横幅**无条件出现**）。
        故本测试从「断言判缺口」改为「断言记录仍在」：
        `complete` 现在只在**承诺类缺真值证据**时为 False（见 test_watermark_scope95）。
    """
    sc = rg.annotation_scope(_manifest(["mixed", "mixed"]), _truth([["corner_logo"]]))
    assert sc["labeled"] == ["corner_logo"]
    assert sc["n_labeled_styles"] == 1
    assert sc["unspecified_images"] == 2      # 仍然被显式记录，没有被静默丢弃
    assert sc["declared_images"] == 0
    assert sc["committed_missing"] == []      # 承诺类（ai_logo）有真值
    assert sc["complete"] is True             # ← 阶段 95 口径：承诺面内覆盖完整


def test_scope_complete_when_hint_concrete_and_labeled():
    sc = rg.annotation_scope(_manifest(["corner_logo"]), _truth([["corner_logo"]]))
    assert sc["complete"] is True
    assert sc["declared_images"] == 1
    assert sc["unspecified_images"] == 0


def test_scope_treats_empty_and_unknown_as_catch_all():
    """空串与 `unknown` 与 `mixed` 同义——都不能被读成「该图没有水印」。

    阶段 95：同样从「判缺口」改为「断言记录在」（口径变更理由见上一条）。
    """
    sc = rg.annotation_scope(_manifest(["", "unknown"]), _truth([["corner_logo"]]))
    assert sc["unspecified_images"] == 2
    assert sc["declared_images"] == 0
    assert sc["committed_missing"] == []
    assert sc["complete"] is True


def test_scope_flags_declaration_truth_contradiction():
    """声明了具体风格却 0 标注 = 数据矛盾（硬错误，不是「覆盖面不足」）。"""
    sc = rg.annotation_scope(_manifest(["grid_watermark"]), _truth([["corner_logo"]]))
    assert sc["contradictory"], sc
    assert sc["complete"] is False


def test_scope_flags_style_outside_enum():
    sc = rg.annotation_scope(_manifest(["mixed"]), _truth([["definitely_not_a_style"]]))
    assert sc["unknown_styles"] == ["definitely_not_a_style"]


def test_banner_empty_without_scope_info():
    """没有覆盖面信息 ≠ 覆盖面不完整：不能凭空断言，也不能凭空告警。"""
    assert rg.render_scope_banner({}) == ""


def test_banner_present_and_mentions_limited_recall():
    banner = rg.render_scope_banner(_incomplete_scope())
    assert "标注覆盖面" in banner
    assert "corner_logo" in banner


def test_default_mode_warns_but_passes_on_incomplete_annotation():
    """默认不制造假红：覆盖面不足是**数据集现状**，不该让门禁永久变红。"""
    ok, problems = rg.evaluate_real(_summary(scope=_incomplete_scope()), None)
    assert ok, problems


def test_strict_scope_upgrades_incompleteness_to_failure():
    ok, problems = rg.evaluate_real(_summary(scope=_incomplete_scope()), None,
                                   strict_scope=True)
    assert not ok
    assert any("范围" in p for p in problems)


def test_contradiction_fails_even_without_strict_scope():
    """数据矛盾始终硬失败——那是标注错误，与门槛无关。"""
    scope = _incomplete_scope(contradictory=["03: hint=grid_watermark 但真值中该类 0 标注"])
    ok, problems = rg.evaluate_real(_summary(scope=scope), None)
    assert not ok
    assert any("矛盾" in p for p in problems)


def test_unknown_style_fails_even_without_strict_scope():
    scope = _incomplete_scope(unknown_styles=["bogus"])
    ok, problems = rg.evaluate_real(_summary(scope=scope), None)
    assert not ok
    assert any("枚举外" in p for p in problems)


def test_markdown_carries_scope_limit_with_the_numbers():
    """限定必须跟着数字走：把结果贴进文档的人自动带上它。"""
    md = rg.to_markdown(_summary(scope=_incomplete_scope()))
    assert "标注覆盖面" in md
    assert "仅代表" in md


def test_markdown_omits_banner_when_scope_complete():
    md = rg.to_markdown(_summary(scope=_incomplete_scope(complete=True,
                                                        unspecified_images=0,
                                                        declared_images=30,
                                                        labeled=["corner_logo", "edge_text"],
                                                        n_labeled_styles=2)))
    assert "标注覆盖面" not in md


# ===========================================================================
# S4 · 能力声明单一真值来源
# ===========================================================================

def _readme_type_rows():
    """只取 README「支持的水印类型」那张表的行（避免误伤正文里的其他预设名）。"""
    with open(README_PATH, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next(i for i, l in enumerate(lines) if "支持的水印类型" in l)
    rows = []
    for l in lines[start + 1:]:
        if not l.startswith("|"):
            if rows:
                break
            continue
        rows.append(l)
    return rows


def test_readme_advertised_types_subset_of_scope_table():
    """**结构上杜绝「承诺比证据多」**：README 声明的类型必须都已登记。"""
    rows = _readme_type_rows()
    assert rows, "README 里的水印类型表没找到（表格结构可能被改动）"
    advertised = set()
    for l in rows:
        advertised |= set(re.findall(r"`(ai_[a-z_]+)`", l))
    assert advertised, f"未解析出任何预设名：{rows}"
    unregistered = advertised - set(config.WATERMARK_TYPE_SCOPE)
    assert not unregistered, f"README 声明了未登记的类型：{sorted(unregistered)}"


def _synthetic_case_presets():
    """合成用例集覆盖的预设名——**唯一来源** `make_demo_assets.CASES`。"""
    from scripts.make_demo_assets import CASES
    return {v[1] for v in CASES.values()}


def test_scope_keys_cover_synthetic_cases_plus_declared_gap():
    """声明表键集合 == 合成用例覆盖的预设 ∪ 显式登记的已知缺口。

    双向断言：
      · 少一个 → 有类型在用却没有被声明（漏登记）
      · 多一个 → 声明了却不属于任何一类（幽灵条目）
    并钉住 `ai_gradient` 这个**已知缺口**：一旦给它补了合成用例，测试失败，
    强制把声明表与 README 一起更新（而不是让文档停在旧说法上）。
    """
    from scripts.make_demo_assets import CASES
    from scripts import quality_gate as qg

    cases = _synthetic_case_presets()
    assert cases == {"ai_faint_tiled", "ai_logo", "ai_text_heavy", "ai_grid"}, cases
    expected = cases | set(config.WATERMARK_TYPE_KNOWN_SYNTHETIC_GAP)
    assert set(config.WATERMARK_TYPE_SCOPE) == expected, (
        f"声明表键 {sorted(set(config.WATERMARK_TYPE_SCOPE))} 与期望 {sorted(expected)} 不符"
    )

    # `quality_gate.CASES` 必须是 from-import 的**同一对象**，不能另建一份。
    assert qg.CASES is CASES, "quality_gate 复制了用例表，出现两份来源"


def test_ai_gradient_has_no_positive_sample_anywhere():
    """显式记录阶段 92 的发现（docs/92 缺口 S4）。

    若日后为 `ai_gradient` 补了合成用例，本测试会失败——**强制**把声明表与 README
    一起更新，而不是让文档继续停留在旧说法上。
    """
    from scripts.make_demo_assets import CASES
    assert "ai_gradient" in config.WATERMARK_TYPE_KNOWN_SYNTHETIC_GAP
    assert "ai_gradient" not in _synthetic_case_presets()
    assert "gradient" not in CASES, "质量门禁出现了渐变用例 → 请同步更新声明表"


def test_readme_marks_unvalidated_types():
    """没有正向证据的类型，README 必须**逐行**标出「未验证」。"""
    cases = _synthetic_case_presets()
    for row in _readme_type_rows():
        for name in re.findall(r"`(ai_[a-z_]+)`", row):
            entry = config.WATERMARK_TYPE_SCOPE.get(name)
            if entry is None:
                continue
            has_synthetic = name in cases
            has_real = bool(_real_label_count(entry))
            if not (has_synthetic and has_real):
                assert "未验证" in row, f"{name} 缺证据但 README 未标注「未验证」：{row}"


def test_red_line_coverage_is_exactly_ai_logo():
    """**把「哪一类真的受红线约束」钉成事实**（不是一句声明）。

    推导规则：该类型在真实基准有标注 **且** `general` 预设已登记契约。
    阶段 150（Option A）：移除 60 条模板化伪造 corner_logo 标注后，真实基准曾 **0 条真值**。
    **阶段 172**：经 `inbox/` 收编 10 张豆包生成的真实图并逐图量取 `corner_logo` 真值
    （`manual:measured`）⇒ 「在真实基准有标注」的类型首次成为 **{ai_logo}**
    ——README 的验证状态列已同步（`ai_logo` 由「未验证」改为「已承诺且有真实证据」）。
    若日后补齐其他类型的真实标注，此断言会失败并要求同步。
    """
    assert "general" in rg.REAL_PRESET_CONTRACT
    covered = {name for name, entry in config.WATERMARK_TYPE_SCOPE.items()
               if _real_label_count(entry) > 0}
    assert covered == {"ai_logo"}, (
        f"受红线约束的类型变为 {sorted(covered)}——请同步更新 README 的验证状态列"
    )


def _real_label_count(entry):
    """从真值文件**现算**某类型的标注条数（不信任写死在表里的数字）。"""
    if not os.path.exists(TRUTH_PATH):
        return 0
    with open(TRUTH_PATH, encoding="utf-8") as f:
        truth = json.load(f)
    styles = set(entry["truth_styles"])
    return sum(1 for e in truth.get("entries", [])
               for w in (e.get("watermarks") or [])
               if w.get("style") in styles)


def test_truth_styles_are_all_mapped_by_some_type():
    """真值里出现的每个 style 都必须被某个声明映射到——否则它是「没人负责的标注」。"""
    with open(TRUTH_PATH, encoding="utf-8") as f:
        truth = json.load(f)
    actual = {w.get("style") for e in truth.get("entries", [])
              for w in (e.get("watermarks") or [])}
    actual.discard(None)
    mapped = set()
    for entry in config.WATERMARK_TYPE_SCOPE.values():
        mapped |= set(entry["truth_styles"])
    assert actual <= mapped, f"真值中有未映射的 style：{sorted(actual - mapped)}"


def test_declared_truth_styles_exist_in_enum():
    """声明表里的 `truth_styles` 必须在真值枚举内——**合成用例名不是真值 style 名**。

    `faint_tiled` 是合成用例名，真值侧对应的是 `translucent`；两者若混用，
    映射会静默失效（`_real_label_count` 恒为 0，看起来像"没标"，实为"名字写错"）。
    """
    from scripts.build_real_ground_truth import STYLE_ENUM
    for name, entry in config.WATERMARK_TYPE_SCOPE.items():
        for style in entry["truth_styles"]:
            assert style in STYLE_ENUM, f"{name}: truth_styles 含枚举外取值「{style}」"


def test_scope_keys_are_registered_detection_presets():
    """声明表键必须是真实存在的预设——不能声明一个不存在的模式。"""
    from core.detection_presets import list_presets
    registered = {p.name for p in list_presets()}
    unknown = set(config.WATERMARK_TYPE_SCOPE) - registered
    assert not unknown, f"声明表含未注册预设：{sorted(unknown)}"


def test_real_dataset_coverage_matches_declaration():
    """真实基准的类别覆盖必须与声明一致（阶段 150：5 类均为 0）。

    阶段 150（Option A）曾把 30 张图作为无痕自然参考集（0 条真值）。
    **阶段 172**：收编 10 张豆包真实图并逐图标注 `corner_logo`（`manual:measured`）
    ⇒ `ai_logo` = **10** 条；**阶段（q 系列导入）** 再由 `D:\照片存储` 收编 19 张
    （含 q1–q6 + q1;.png–q5;.png 及 a1–a4/1–4 等）⇒ `ai_logo` = **29** 条；
    **阶段 300（c/s 系列导入）** 再收编 **28** 张（c1–c11/c13–c15/s1–s14，
    2048×2048，右下「豆包 AI 生成」固定文字水印；bbox 由跨图平均高通 + 逐图足迹
    约束量取，`labeled_by=measured:per-image-constrained`。c12 与 c11 字节重复，
    collect 已按 hash 去重故不入基准）⇒ 当前 `ai_logo` = **57** 条，
    其余 4 类仍为 0。这条断言把当前覆盖钉住；日后若补齐其他类型，它会失败并提示
    同步更新 README 与 config 的说明。
    """
    counts = {name: _real_label_count(entry)
              for name, entry in config.WATERMARK_TYPE_SCOPE.items()}
    assert counts["ai_logo"] == 57, (
        f"ai_logo 真实标注数应为 57（阶段 172 收编 10 + q 系列 19 + c/s 系列 28）："
        f"{counts['ai_logo']}")
    missing = [n for n, c in counts.items() if c == 0]
    assert len(missing) == 4, (
        f"真实基准的类别覆盖已变化（0 标注的类型变为 {missing}）——"
        f"请同步更新 README 的验证状态列与 config 的说明"
    )


# ===========================================================================
# 基线快照必须携带限定
# ===========================================================================

def test_baseline_snapshot_carries_scope_and_area_metrics():
    """基线里若不带覆盖面与面积指标，日后看数字的人无从知道限定（阶段 93）。"""
    if not os.path.exists(BASELINE_PATH):
        pytest.skip("基线快照不存在（未跑过 real_gate.py --update）")
    with open(BASELINE_PATH, encoding="utf-8") as f:
        base = json.load(f)
    assert "scope" in base, "基线缺少 scope：限定没有跟着数字走"
    general = base["aggregate"]["per_preset"]["general"]
    for key in ("mask_coverage", "coverage_ratio"):
        assert key in general, f"基线缺少面积指标 {key}，新红线无法做漂移比对"
