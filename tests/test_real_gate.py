"""阶段 83 · 真实图门禁分册的回归测试。

分三层：
  1) **判定逻辑层**（纯函数，零 I/O）：`real_gate.evaluate_real` 的红线越界、漂移超容差、
     配置不一致、数据完整性——门禁"会不会正确报警"的核心，必须用确定数据锁死。
  2) **阈值标定层**：断言 `REAL_PRESET_CONTRACT` 与 `REAL_TOLERANCES` 的取值**严格夹在
     阶段 82 前后的实测值之间**。这是防「悄悄放宽阈值让门禁变哑巴」的守卫——经验 64：
     护栏阈值必须标定，且要能在无害/危险区之间被验证。
  3) **集成冒烟层**：用合成小图 + monkeypatch 路径，确认 `run_real_gate` / `main` 的
     接线真实可调（经验 20：端到端测试抓接线错误），且数据集缺失时返回 exit 2（跳过），
     **不与「通过」混淆**（经验 23：容错不能成为错误的藏身处）。

不依赖真实 AI 图、不依赖 LaMa（CPU 环境可跑）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import real_gate as rg  # noqa: E402


# ---------------------------------------------------------------------------
# 构造确定数据的辅助函数
# ---------------------------------------------------------------------------

def _preset_m(n=30, precision=0.0690, recall=0.8000, mean_iou=0.5347, mask_iou=0.2975):
    """一组「恰好等于当前基线」的指标（各字段可覆盖以构造越线场景）。

    阶段 100 更新：红线随角标 MSER 降级通路默认关闭而重标（precision 0.034→0.092、
    mask_iou 0.047→0.077），故默认值须同步到新的实测基线，否则「合法 summary」用例
    会撞上新红线。
    **阶段 106 再更新**：mask_coverage/coverage_ratio/mask_iou 三线随「框内证据几何收敛」
    重标（0.11→0.045 / 11.5→4.35 / 0.077→0.10），实测 **0.0365 / 3.75× / 0.1705**、
    precision 0.1478→**0.1552**（recall 1.0000 与 mean_iou 0.7518 逐位不变）⇒
    本夹具同步到该基线。**这不是放宽断言，而是把夹具同步到新基线**（经验 81）。
    ⚠ **阶段 183**：上述 100/106 值标定于**伪造真值期**（149 判定 GT 系模板复制；
    177 用真实真值重算基线）⇒ 夹具同步到真实实测
    （precision 0.0690 / recall 0.8000 / mean_iou 0.5347 / mask_iou 0.2975）。
    """
    return {"n": n, "precision": precision, "recall": recall,
            "mean_iou": mean_iou, "mask_iou": mask_iou}


def _summary(per_preset=None, n_images=30, n_expected=30, presets=("general",)):
    """构造一份结构合法的 summary（数值可覆盖，便于制造越线场景）。"""
    if per_preset is None:
        per_preset = {"general": _preset_m()}
    return {
        "config": {
            "presets": list(presets),
            "n_images": n_images,
            "n_expected": n_expected,
            "dataset_dir": "benchmark_real",
            "generated_at": "2026-09-14T00:00:00",
        },
        "aggregate": {"per_preset": per_preset, "n_images": n_images},
    }


def _baseline(per_preset=None, n_expected=30, presets=("general",)):
    if per_preset is None:
        per_preset = {"general": _preset_m()}
    # 阶段 107：基线结构新增 `aggregate.area_only`（未承诺预设的面积画像）。
    # 本夹具必须带上该键，否则会被「旧版基线，请 --update 刷新」这条**刻意的硬失败**拦下
    # ——同步夹具而非放宽判定（经验 81）。
    return {
        "config": {"presets": list(presets), "area_presets": [],
                   "n_images": 30, "n_expected": n_expected,
                   "dataset_dir": "benchmark_real", "generated_at": "2026-09-13T00:00:00"},
        "aggregate": {"per_preset": per_preset, "area_only": {}, "n_images": 30},
    }


def _joined(problems):
    return " | ".join(problems)


# ---------------------------------------------------------------------------
# 1 · 判定逻辑层
# ---------------------------------------------------------------------------

def test_pass_when_all_red_lines_met():
    ok, problems = rg.evaluate_real(_summary(), None)
    assert ok, f"合法 summary 应通过；实际：{problems}"
    assert problems == []


def test_violate_directions():
    assert rg._violate("min", 0.5, 0.6) is True
    assert rg._violate("min", 0.7, 0.6) is False
    assert rg._violate("max", 0.7, 0.6) is True
    assert rg._violate("max", 0.5, 0.6) is False


def test_recall_red_line_fails():
    """区域级召回跌破红线 → 报警。

    阶段 177 按真实实测重标（0.8000 × 0.8 = **0.64**）；注入取 0.55（< 0.64）
    代表「碎片聚合通路上出现明显回退」。
    """
    s = _summary({"general": _preset_m(recall=0.55)})
    ok, problems = rg.evaluate_real(s, None)
    assert not ok
    assert "红线" in _joined(problems) and "recall" in _joined(problems)


def test_precision_red_line_fails():
    """精确率跌破红线 → 报警（该红线同时编码「自然光影守卫」与「纹理稠密守卫」）。

    阶段沿革：A4 首轮 0.0129 → 阶段 82 0.0308（自然光影守卫）→ 阶段 84 0.0388
    （纹理稠密守卫）；红线 0.034 夹在 82 与 84 之间，故任一守卫被回退都会触发。
    """
    s = _summary({"general": _preset_m(precision=0.020)})
    ok, problems = rg.evaluate_real(s, None)
    assert not ok
    assert "红线" in _joined(problems) and "precision" in _joined(problems)


def test_iou_red_line_fails():
    # 阶段 177：崩溃护栏由 0.55 重标为 **0.35**（真实实测 0.5347 × 0.65）；注入 0.30
    s = _summary({"general": _preset_m(mean_iou=0.30)})
    ok, problems = rg.evaluate_real(s, None)
    assert not ok
    assert "红线" in _joined(problems) and "mean_iou" in _joined(problems)


def test_boundary_values_pass():
    """恰好等于阈值应通过（判定是严格不等式）。

    四道红线（precision / recall / mean_iou / mask_iou）全部取**边界值**，
    证明 `_violate` 用的是严格不等式而非 `>=`/`<=` 混用。
    ⚠ **阶段 183**：边界值改为**从契约派生**而非硬编码——旧写法写死了
    伪造真值期 / 阶段 106 的 (0.092 / 0.98 / 0.55 / 0.10)，红线一重标本用例
    就变成陈旧锚点（183 把 mask_iou 抬到 0.15 时即被暴露）。
    """
    c = rg.REAL_PRESET_CONTRACT["general"]
    s = _summary({"general": _preset_m(precision=c["precision"][1], recall=c["recall"][1],
                                       mean_iou=c["mean_iou"][1], mask_iou=c["mask_iou"][1])})
    ok, problems = rg.evaluate_real(s, None)
    assert ok, f"边界值应通过；实际：{problems}"


def test_data_integrity_mismatch_fails():
    """实评图数 < 标注图数 → 失败（读图失败被静默 skip 的防线）。"""
    s = _summary(n_images=28, n_expected=30)
    ok, problems = rg.evaluate_real(s, None)
    assert not ok
    assert "数据" in _joined(problems)
    assert "静默跳过" in _joined(problems)


def test_missing_counts_flags_data_problem():
    s = _summary()
    del s["config"]["n_expected"]
    ok, problems = rg.evaluate_real(s, None)
    assert not ok
    assert "数据" in _joined(problems)


def test_empty_per_preset_fails():
    ok, problems = rg.evaluate_real(_summary(per_preset={}), None)
    assert not ok
    assert "数据" in _joined(problems)


def test_unregistered_preset_gets_no_red_line():
    """未登记契约的预设（如 ai_gradient 设计上恒零）只监控，不判红线。"""
    s = _summary(per_preset={"ai_gradient": _preset_m(precision=0.0, recall=0.0, mean_iou=0.0)},
                 presets=("ai_gradient",))
    ok, problems = rg.evaluate_real(s, None)
    assert ok, f"未登记预设不应触发红线；实际：{problems}"


def test_registered_preset_absent_does_not_crash():
    """summary 里没有 general，但有别的预设 → 不应因取不到 general 而崩。"""
    s = _summary(per_preset={"ai_corner": _preset_m()}, presets=("ai_corner",))
    ok, problems = rg.evaluate_real(s, None)
    assert ok, f"实际：{problems}"


# ---------------------------------------------------------------------------
# 2 · 漂移层
# ---------------------------------------------------------------------------

def test_drift_recall_beyond_tolerance_fails():
    """召回相对下滑超 10% 容差 → 漂移告警。

    注意（阶段 86 起）：**general 的召回漂移检查已被红线吞掉**——红线 0.98 距上限
    1.00 只有 2% 空间，而容差是 10%，所以 general 上先触发的一定是红线。为独立
    验证漂移逻辑本身，这里改用**未登记契约的预设**（无红线，只做漂移）。
    """
    base = _baseline({"ai_corner": _preset_m(recall=0.94)}, presets=("ai_corner",))
    now = _summary({"ai_corner": _preset_m(recall=0.90)}, presets=("ai_corner",))
    ok, problems = rg.evaluate_real(now, base)          # 下滑 4.3% → 不报
    assert ok, f"4.3% 下滑在容差内；实际：{problems}"

    now2 = _summary({"ai_corner": _preset_m(recall=0.82)}, presets=("ai_corner",))
    ok2, problems2 = rg.evaluate_real(now2, base)       # 下滑 12.8% → 报
    assert not ok2
    assert "漂移" in _joined(problems2)


def test_recall_red_line_is_collapse_guard_below_drift_floor():
    """**分层设计**（阶段 177 随真实重标而明确）：漂移层守「变化」、红线守「崩溃」。

    重标后 recall 红线 **0.64** 比「基线×(1−容差)」更**宽** ⇒ 日常回退由漂移先拦、
    红线只在整段塌陷时兜底；两者分工明确、互不越位。
    （旧版此用例断言「红线先于漂移」——那是**伪造真值期**红线 0.98 极高时的形态，
      阶段 177 按真实实测重标后该形态已改变，故按**文档化的分层**重写。）
    """
    base = _baseline({"general": _preset_m(recall=1.0)})   # 显式 recall=1.0（本用例的漂移地板据此推导）
    thr = rg.REAL_PRESET_CONTRACT["general"]["recall"][1]
    drift_floor = 1.0 * (1.0 - rg.REAL_TOLERANCES["recall"])
    assert thr < drift_floor, (
        f"recall 红线 {thr} 应宽于漂移地板 {drift_floor:.2f}——"
        f"否则漂移层形同虚设、日常回退只能靠红线（与分层设计相反）"
    )
    # 落在「漂移地板 .. 红线」之间：由**漂移**抓（红线不响）
    mid = (thr + drift_floor) / 2.0
    ok, problems = rg.evaluate_real(_summary({"general": _preset_m(recall=mid)}), base)
    assert not ok and "漂移" in _joined(problems), (
        f"recall={mid:.3f} 落在漂移地板 {drift_floor:.3f} 与红线 {thr} 之间，应由漂移拦住：{problems}"
    )
    # 跌破红线的整段塌陷：由**红线**抓
    ok2, problems2 = rg.evaluate_real(
        _summary({"general": _preset_m(recall=thr - 0.05)}), base)
    assert not ok2 and "红线" in _joined(problems2), (
        f"recall={thr - 0.05:.3f} 已跌破红线 {thr}，应由红线拦住：{problems2}"
    )


def test_drift_improvement_never_fails():
    """指标变好永远不失败（precision 取 0.14，高于阶段 100 红线 0.092）。"""
    base = _baseline({"general": _preset_m(precision=0.0388, recall=0.95, mean_iou=0.6571)})
    now = _summary({"general": _preset_m(precision=0.14, recall=0.99, mean_iou=0.80)})
    ok, problems = rg.evaluate_real(now, base)
    assert ok, f"改善不应报错；实际：{problems}"


def test_drift_precision_beyond_tolerance_fails():
    base = _baseline({"general": _preset_m(precision=0.0388)})
    now = _summary({"general": _preset_m(precision=0.028)})  # 下滑 27.8% > 25%
    ok, problems = rg.evaluate_real(now, base)
    assert not ok
    assert "precis" in _joined(problems).lower()


def test_baseline_zero_value_skips_relative_drift():
    """基线值为 0 时相对容差无意义，不应产生除零或误报。

    阶段 93 调整：改用**未登记契约**的预设（`ai_gradient`）来隔离漂移逻辑——
    阶段 93 给 `general` 的 `mask_iou` 加了崩溃护栏，故 `mask_iou=0.0` 会先撞红线，
    使本用例不再能单独验证「零基线跳过相对漂移」这一意图。换预设后语义不变。
    """
    base = _baseline({"ai_gradient": _preset_m(mask_iou=0.0)}, presets=("ai_gradient",))
    now = _summary({"ai_gradient": _preset_m(mask_iou=0.0)}, presets=("ai_gradient",))
    ok, problems = rg.evaluate_real(now, base)
    assert ok, f"实际：{problems}"


def test_preset_set_mismatch_flags_config():
    base = _baseline(presets=("general",))
    now = _summary(presets=("general", "ai_corner"),
                   per_preset={"general": _preset_m(), "ai_corner": _preset_m(n=30)})
    ok, problems = rg.evaluate_real(now, base)
    assert not ok
    assert "配置" in _joined(problems)


def test_n_expected_change_flags_config():
    base = _baseline(n_expected=30)
    now = _summary(n_expected=28, n_images=28)
    ok, problems = rg.evaluate_real(now, base)
    assert not ok
    assert "配置" in _joined(problems)


def test_baseline_none_only_redlines():
    """无基线时只做红线 + 数据完整性。"""
    s = _summary({"general": _preset_m(precision=0.001)})  # 破红线
    ok, problems = rg.evaluate_real(s, None)
    assert not ok
    assert "红线" in _joined(problems)
    assert "漂移" not in _joined(problems)


def test_new_preset_not_in_baseline_skips_drift():
    """新增预设（基线没有）→ 不做漂移比对，也不崩。"""
    base = _baseline(presets=("general",))
    now = _summary(presets=("general", "ai_logo"),
                   per_preset={"general": _preset_m(), "ai_logo": _preset_m()})
    # 预设集合不同会报「配置」不一致；但不应出现 ai_logo 的漂移行。
    ok, problems = rg.evaluate_real(now, base)
    assert not any("ai_logo/" in p and "漂移" in p for p in problems)


# ---------------------------------------------------------------------------
# 3 · 阈值标定层（防「悄悄放宽阈值」的守卫）
# ---------------------------------------------------------------------------

def test_red_line_brackets_measured_values():
    """红线必须严格夹在「上一阶段实测值」与「本阶段实测值」之间。

    放低到上界以下 → 阶段收益被回退也不报警（门禁变哑巴）；
    抬高到下界以上 → 一上线就红灯（门禁被无视）。
    标定表是唯一来源（`REAL_REDLINE_CALIBRATION`），新增阶段时同步更新。
    """
    for metric, (lo, hi) in rg.REAL_REDLINE_CALIBRATION.items():
        direction, thr = rg.REAL_PRESET_CONTRACT["general"][metric]
        assert direction == "min"
        assert lo < hi, f"{metric} 标定区间非法：{lo} !< {hi}"
        assert lo < thr < hi, (
            f"{metric} 红线 {thr} 未夹在实测区间 ({lo}, {hi}) 内——已失去标定意义"
        )


def test_every_red_line_is_calibrated_or_declared_crash_guard():
    """新增红线必须显式做出「它编码了什么」的决策（标定 or 登记为崩溃护栏）。"""
    metrics = set(rg.REAL_PRESET_CONTRACT["general"])
    covered = set(rg.REAL_REDLINE_CALIBRATION) | set(rg.REAL_CRASH_GUARD_METRICS)
    assert metrics <= covered, f"未登记来由的红线指标：{sorted(metrics - covered)}"
    assert not (set(rg.REAL_REDLINE_CALIBRATION) & set(rg.REAL_CRASH_GUARD_METRICS)), \
        "同一指标不得既登记标定又登记为崩溃护栏"


def test_precision_red_line_catches_each_guard_failure():
    """precision 红线的下界必须能抓住每一个守卫的失效（注入实测值）。

    阶段沿革（红线值）：0.034（阶段 84，夹在 0.0308 与 0.0388 之间）
    → **0.092（阶段 100）**。实测回退值：
      · 关自然光影守卫       → 0.0224
      · 关纹理稠密守卫       → 0.0308
      · 开关板块判据（86）   → 0.0388
      · **开回角标 MSER 降级通路（100）→ 0.0392**
    四者都低于红线 0.092，故任一守卫被回退都会报警。
    """
    _dir, thr = rg.REAL_PRESET_CONTRACT["general"]["precision"]
    assert 0.0224 < thr, "红线低于「关自然光影守卫」的实测值 → 抓不住该回退"
    assert 0.0308 < thr, "红线低于「关纹理稠密守卫」的实测值 → 抓不住该回退"
    assert 0.0392 < thr, "红线低于「开回角标 MSER 降级通路」的实测值 → 抓不住该回退"


def test_mask_recall_red_line_catches_plate_judgment_failure():
    """**头条** `mask_recall` 红线必须能抓住「白底圆角板块判据」被关掉的回退。

    阶段 177 重标 + **重新注入实测**（真实真值，10 图）：
        `WME_DETECT_PLATE_ENABLED=false` → mask_recall **0.5619 → 0.3535**
    红线 0.45 高于 0.3535（抓住回退）且低于 0.5619（不会一上线就红灯）。

    ⚠ 旧版此处断言 `recall` 红线 > 0.9500——那个 0.9500 系**伪造真值期**实测值
    （阶段 149 判定原 60 条 GT 系模板复制），已作废；故改挂头条口径并用真实注入值。
    """
    _dir, thr = rg.REAL_PRESET_CONTRACT["general"]["mask_recall"]
    assert thr > 0.3535, (
        "mask_recall 红线低于「关板块判据」的真实实测 0.3535 → 抓不住该回退（门禁变哑巴）"
    )
    assert thr < 0.5619, (
        "mask_recall 红线高于当前实测 0.5619 → 一上线就红灯（门禁被无视）"
    )


def test_recall_tolerance_is_tighter_than_precision():
    """召回是 82 的核心收益，容差应比精确率更紧。"""
    assert rg.REAL_TOLERANCES["recall"] < rg.REAL_TOLERANCES["precision"]


# ---------------------------------------------------------------------------
# 5 · 输出编码不得决定退出码（阶段 107 修的真实缺陷）
# ---------------------------------------------------------------------------

def _scope_for_banner():
    return {"labeled": ["corner_logo"], "n_labeled_styles": 1, "declared_images": 0,
            "unspecified_images": 30, "contradictory": [], "unknown_styles": [],
            "committed_missing": [], "complete": True}


def test_scope_banner_contains_non_gbk_char():
    """前提：横幅确实含 GBK 编不出的字符（`⚠`）—— 这就是必须切 UTF-8 的原因。

    若某天横幅被改成纯 ASCII，本断言会失败，从而提醒把 `main()` 里的 reconfigure 一起删掉
    （守卫的前提变了，守卫要跟着变）。
    """
    banner = rg.render_scope_banner(_scope_for_banner())
    assert "⚠" in banner
    with pytest.raises(UnicodeEncodeError):
        banner.encode("gbk")


def test_main_reconfigures_streams_to_utf8():
    """`main()` 必须把 stdout/stderr 切到 UTF-8。

    背景（阶段 107 实测）：Windows 上 stdout 被重定向到管道时按 ANSI 代码页（GBK）编码，
    `print(to_markdown(...))` 里的 `⚠` 抛 `UnicodeEncodeError` ⇒ **退出码 1**。
    后果是「**输出内容决定了门禁退出码**」，而且 `--update` 的写盘在打印之后 ⇒ 基线也刷不了。
    本断言把修复钉在源码上（AST/文本守卫，与 `test_detector_audit100` 的守卫同风格）。
    """
    with open(rg.__file__, encoding="utf-8") as f:
        src = f.read()
    # 阶段 170：加固逻辑抽为共用件 `scripts/console_encoding.force_utf8_stdio()`
    #   ⇒ 本用例改断言「real_gate 调用共用件」+「共用件内部确实切 UTF-8 且带兜底」；
    #   全体脚本是否都接了共用件由 `tests/test_scripts_console_encoding.py` 的元守卫覆盖。
    assert "force_utf8_stdio" in src, (
        "real_gate.main 未做输出编码加固 —— 管道输出含 ⚠ 时会抛 UnicodeEncodeError，"
        "退出码 1 会被误读成「门禁未通过」")
    _ce = os.path.join(os.path.dirname(rg.__file__), "console_encoding.py")
    with open(_ce, encoding="utf-8") as f:
        ce = f.read()
    assert 'reconfigure(encoding="utf-8"' in ce, "共用件未把流切到 UTF-8"
    assert 'errors="replace"' in ce, "共用件应带 errors='replace' 兜底，绝不因编码中断判定"


def test_default_presets_is_general_only():
    """默认只跑 general（唯一有真实语义契约的预设）。"""
    assert rg.DEFAULT_REAL_PRESETS == ("general",)


# ---------------------------------------------------------------------------
# 4 · 集成冒烟层
# ---------------------------------------------------------------------------

def _make_synth_bench(bdir: Path) -> None:
    """在 bdir 下造 1 张合成图 + manifest + truth（绝对路径，绕开 BENCH_DIR）。"""
    from PIL import Image, ImageDraw

    (bdir / "images").mkdir(parents=True, exist_ok=True)
    img_path = bdir / "images" / "synth_001.png"
    im = Image.new("RGB", (256, 256), (200, 200, 200))
    ImageDraw.Draw(im).rectangle((50, 50, 80, 80), fill=(30, 30, 30))
    im.save(img_path)

    (bdir / "manifest.json").write_text(json.dumps({
        "version": 1,
        "images": [{
            "id": "synth_001",
            "filename": "synth_001.png",
            "path": str(img_path),          # 绝对路径，独立于 WME_BENCH_DIR
            "ai_source": "other",
            "watermark_style_hint": "logo_watermark",
            "original_size": [256, 256],
            "sha256": "deadbeef" * 8,
            "added_at": "2026-09-14T00:00:00Z",
            "source": "local",
        }],
    }, ensure_ascii=False), encoding="utf-8")

    (bdir / "ground_truth.json").write_text(json.dumps({
        "version": 1,
        "entries": [{
            "image_id": "synth_001",
            "watermarks": [{"bbox": [50, 50, 80, 80], "style": "logo_watermark",
                            "note": "synth"}],
            "labeled_at": "2026-09-14T00:00:00Z",
            "labeled_by": "manual",
        }],
    }, ensure_ascii=False), encoding="utf-8")


def test_run_real_gate_end_to_end(tmp_path, monkeypatch):
    """monkeypatch 路径 → run_real_gate 真实跑通，结构完整、计数自洽。"""
    bdir = tmp_path / "benchmark_real"
    _make_synth_bench(bdir)
    monkeypatch.setattr(rg, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(rg, "TRUTH_PATH", str(bdir / "ground_truth.json"))

    summary = rg.run_real_gate(presets=("general",))

    assert summary["config"]["n_images"] == 1
    assert summary["config"]["n_expected"] == 1
    assert "general" in summary["aggregate"]["per_preset"]
    m = summary["aggregate"]["per_preset"]["general"]
    assert 0.0 <= m["recall"] <= 1.0
    assert 0.0 <= m["precision"] <= 1.0
    # 数据完整性：1 == 1 → 不应出现「数据」类问题。
    # 注意：合成小图达不到真实图契约的红线（这是**设计如此**——红线按真实分布标定），
    # 故此处只断言「数据完整性通过」，不要求整体通过。
    _ok, problems = rg.evaluate_real(summary, None)
    assert not any("[数据]" in p for p in problems), f"不应有数据完整性问题：{problems}"


def test_display_path_survives_cross_drive(tmp_path, monkeypatch):
    """阶段 83 缺陷回归：跨盘符（Windows）时 relpath 抛 ValueError，展示路径不得让功能失败。

    原始症状：`--update` 成功写完基线后，打印确认信息时崩溃 → 报错与实际状态不符。
    """
    from utils.path_helper import display_path

    # 同盘符 → 相对路径
    base = tmp_path / "a"
    base.mkdir()
    target = base / "b" / "c.json"
    assert display_path(target, base) == os.path.join("b", "c.json")

    # 跨盘符：用两个不同盘符模拟（不存在也没关系，relpath 只做字符串/挂载点比较）。
    cross = display_path(r"Z:\somewhere\baseline.json", r"C:\project")
    assert cross == r"Z:\somewhere\baseline.json", f"跨盘符应退回绝对路径，实际 {cross}"

    # 显式 base=None 时用项目根，不应抛异常。
    assert isinstance(display_path(target), str)


def test_cli_returns_exit2_when_dataset_missing(tmp_path, monkeypatch):
    """数据集缺失 → exit 2（跳过），与「通过」(0) 严格区分。"""
    monkeypatch.setattr(rg, "MANIFEST_PATH", str(tmp_path / "nope_manifest.json"))
    monkeypatch.setattr(rg, "TRUTH_PATH", str(tmp_path / "nope_truth.json"))
    monkeypatch.setattr(sys, "argv", ["real_gate.py"])
    assert rg.main() == 2


def test_cli_update_writes_baseline(tmp_path, monkeypatch, capsys):
    """--update 应写出基线快照，且快照含 config + aggregate + **scope**。

    阶段 93：`scope`（标注覆盖面）必须随基线持久化——否则日后只看基线数字的人
    无从知道 `recall=1.0` 只代表一类水印。**限定必须跟着数字走。**

    阶段 146 订正：**不再断言 `rc == 0`**。
    `--update` 此前写完基线**直接 `return 0`** —— rc 恒 0 不是因为「动作成功」，
    而是因为**它跳过了全部判定**（本用例因此把这个缺陷固化成了期望）。
    修复后 `--update` 会**继续走红线/完整性/漂移判定**，而合成小图**必然破真实图红线**
    （红线按真实分布标定，见 `test_baseline_roundtrip_yields_zero_drift` 的说明）
    ⇒ rc 应为 1。本用例只断言它**真正关心的东西**：快照被写出且内容完整。
    """
    bdir = tmp_path / "benchmark_real"
    _make_synth_bench(bdir)
    baseline_path = tmp_path / "quality_baseline.json"
    monkeypatch.setattr(rg, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(rg, "TRUTH_PATH", str(bdir / "ground_truth.json"))
    monkeypatch.setattr(sys, "argv", ["real_gate.py", "--update",
                                      "--baseline", str(baseline_path)])

    rc = rg.main()
    assert rc in (0, 1), f"--update 的退出码只应是 0（全绿）或 1（判定未过），实得 {rc}"
    assert baseline_path.exists(), "--update 必须写出基线快照（即便判定未过）"
    snap = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert set(snap.keys()) == {"config", "aggregate", "scope"}
    assert "complete" in snap["scope"]
    assert "general" in snap["aggregate"]["per_preset"]


def test_cli_update_still_evaluates_red_lines(tmp_path, monkeypatch, capsys):
    """阶段 146：`--update` **不得**绕过判定 —— 合成图的红线失败必须反映到退出码。

    这是对「刷新是静默通道」这一缺陷的**正面守卫**：若有人把 `--update` 改回
    「写完直接 return 0」，本用例会立刻变红。
    """
    bdir = tmp_path / "benchmark_real"
    _make_synth_bench(bdir)
    baseline_path = tmp_path / "quality_baseline.json"
    monkeypatch.setattr(rg, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(rg, "TRUTH_PATH", str(bdir / "ground_truth.json"))
    monkeypatch.setattr(sys, "argv", ["real_gate.py", "--update",
                                      "--baseline", str(baseline_path)])

    assert rg.main() == 1, "--update 在红线失败时必须返回 1（不得成为静默通道）"
    out = capsys.readouterr().out
    assert "刷新后继续按红线" in out, "缺少「刷新不绕过判定」的可见提示"
    assert "红线" in out, "未打印红线违规明细"


def test_cli_force_update_skips_only_the_drift_guard(tmp_path, monkeypatch, capsys):
    """阶段 146：`--force-update` 只跳过「向下刷新护栏」，**永不跳过红线**。"""
    bdir = tmp_path / "benchmark_real"
    _make_synth_bench(bdir)
    baseline_path = tmp_path / "quality_baseline.json"
    monkeypatch.setattr(rg, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(rg, "TRUTH_PATH", str(bdir / "ground_truth.json"))
    monkeypatch.setattr(sys, "argv", ["real_gate.py", "--update", "--force-update",
                                      "--baseline", str(baseline_path)])

    assert rg.main() == 1, "force-update 不得绕过红线"
    assert baseline_path.exists(), "force-update 应完成写盘"


def test_baseline_roundtrip_yields_zero_drift(tmp_path, monkeypatch):
    """--update 写出的基线回读后自比对 → 零漂移、零配置不一致。

    注意：不能用「CLI 裸跑返回 0」来断言——合成小图必然破真实图红线（红线按真实分布标定），
    那是**设计如此**。此处直接验证基线往返的一致性语义。

    阶段 146 补充：该说明**当时就是对的**，但 `--update` 路径当时恰好没判红线
    ⇒ 「裸跑返回 0」的断言侥幸通过、把缺陷固化成了期望。现在只断言往返一致性。
    """
    bdir = tmp_path / "benchmark_real"
    _make_synth_bench(bdir)
    baseline_path = tmp_path / "quality_baseline.json"
    monkeypatch.setattr(rg, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(rg, "TRUTH_PATH", str(bdir / "ground_truth.json"))

    monkeypatch.setattr(sys, "argv", ["real_gate.py", "--update",
                                      "--baseline", str(baseline_path)])
    rg.main()                                 # 退出码由上面两条用例负责
    assert baseline_path.exists(), "--update 必须写出基线"

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    summary = rg.run_real_gate(presets=("general",))
    _ok, problems = rg.evaluate_real(summary, baseline)
    assert not any("[漂移]" in p for p in problems), f"自比对不应有漂移：{problems}"
    assert not any("[配置]" in p for p in problems), f"自比对不应有配置不一致：{problems}"


def test_cli_exit1_when_red_line_broken(tmp_path, monkeypatch, capsys):
    """红线被破 → CLI 返回 1（而非 0/2），且打印违规明细。

    用合成小图构造「检出为零」的场景：不满足 general 红线 → 必须 exit 1。
    """
    bdir = tmp_path / "benchmark_real"
    _make_synth_bench(bdir)
    baseline_path = tmp_path / "quality_baseline.json"
    monkeypatch.setattr(rg, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(rg, "TRUTH_PATH", str(bdir / "ground_truth.json"))
    monkeypatch.setattr(sys, "argv", ["real_gate.py", "--baseline", str(baseline_path)])

    assert rg.main() == 1
    out = capsys.readouterr().out
    assert "红线" in out


def test_markdown_renders_contract_tag():
    md = rg.to_markdown(_summary())
    assert "general" in md
    assert "红线" in md
    md2 = rg.to_markdown(_summary(per_preset={"ai_gradient": _preset_m()},
                                  presets=("ai_gradient",)))
    assert "监控" in md2
