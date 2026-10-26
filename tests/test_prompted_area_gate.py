# -*- coding: utf-8 -*-
"""
阶段 121 测试：次级档四类面积回归门禁（`scripts/prompted_area_gate.py`）。

覆盖：
  · 结构：基线格式 / 数据集完整性（20 张、每类 5 张、sha 可核）；
  · **单一真值来源**：红线与容差确实复用 `real_gate`（不是本文件里另写一份）；
  · **阈值标定守卫**：基线必须落在红线之内（否则门禁生来就红）、红线不得过松（否则形同虚设）；
  · **判定逻辑（纯函数）**：完整性 / 红线 / 漂移 / 可比性 四类越线都能被抓住，且「变好」永不失败；
  · 集成冒烟（慢）：端到端跑门禁 → 退出码 0。
"""

import json
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import prompted_area_gate as gag  # noqa: E402
import real_gate  # noqa: E402


def _baseline() -> dict:
    return json.load(open(gag.BASELINE_PATH, encoding="utf-8"))


def _manifest() -> dict:
    return json.load(open(gag.MANIFEST_PATH, encoding="utf-8"))


def _clean() -> dict:
    """以基线为「干净态」构造 summary——保证默认情形必然通过。"""
    b = _baseline()
    return {
        "n_images": b["config"]["n_images"],
        "by_style": dict(b["config"]["by_style"]),
        "sha_digest": b["config"]["sha_digest"],
        "per_preset": {p: dict(st) for p, st in b["per_preset"].items()},
    }


# ------------------------------------------------------------------------------
# 结构 / 单一真值来源 / 标定守卫
# ------------------------------------------------------------------------------
def test_baseline_is_wellformed():
    b = _baseline()
    assert b["config"]["n_images"] == 20
    assert set(b["config"]["by_style"]) == set(gag.PRESET_STYLE.values())
    # 每个预设都在**全部 20 张**上跑（面积监控 = 量「该预设在整套图上的过开火」）
    for p in gag.PRESETS:
        assert p in b["per_preset"] and b["per_preset"][p]["n"] == 20


def test_red_lines_and_tolerance_reused_from_real_gate():
    """红线/容差必须是 real_gate 的同一对象——否则就是第二条真值来源。"""
    assert gag.REAL_AREA_CONTRACT is real_gate.REAL_AREA_CONTRACT
    assert gag.AREA_DRIFT_TOLERANCE == real_gate.AREA_DRIFT_TOLERANCE
    assert gag._area_stats is real_gate._area_stats


def test_calibration_guard_baseline_under_red_line():
    """标定守卫：基线须在红线内（留余量），且红线不得过松。"""
    b = _baseline()
    for preset, st in b["per_preset"].items():
        thr = gag.REAL_AREA_CONTRACT[preset]["mask_coverage_mean"][1]
        assert st["mask_coverage_mean"] < thr, f"{preset}：基线已在红线上 ⇒ 门禁生来就红"
        assert st["mask_coverage_mean"] < thr * 0.9, f"{preset}：基线贴线，无余量"
        assert thr <= gag.RED_LINE_CEILING, f"{preset}：红线 {thr} 过松 ⇒ 失去标定意义"


def test_dataset_completeness_and_sha():
    m = _manifest()
    assert len(m["images"]) == 20
    from collections import Counter
    c = Counter(im["watermark_style_hint"] for im in m["images"])
    for style in gag.PRESET_STYLE.values():
        assert c[style] == 5, f"{style} 张数 {c[style]} != 5"
    # 若 images/ 已就位（未被 gitignore 清空）则逐张核文件存在性
    present = [im for im in m["images"] if os.path.isfile(os.path.join(gag.BENCH_DIR, im["path"]))]
    if present:
        assert len(present) == 20, "部分图片缺失（混入不全的数据集会让面积失真）"


# ------------------------------------------------------------------------------
# 判定逻辑（纯函数，毫秒级）
# ------------------------------------------------------------------------------
def test_evaluate_clean_passes():
    ok, problems = gag.evaluate(_clean(), _baseline())
    assert ok, problems


def test_evaluate_flags_sample_drop():
    s = _clean()
    s["n_images"] = 17
    ok, problems = gag.evaluate(s, _baseline())
    assert not ok and any("完整性" in x for x in problems)


def test_evaluate_flags_class_count_change():
    s = _clean()
    s["by_style"]["edge_text"] = 4
    ok, problems = gag.evaluate(s, _baseline())
    assert not ok and any("完整性" in x for x in problems)


def test_evaluate_flags_dataset_swap():
    s = _clean()
    s["sha_digest"] = "deadbeefdeadbeef"
    ok, problems = gag.evaluate(s, _baseline())
    assert not ok and any("可比性" in x for x in problems)


def test_evaluate_flags_red_line_breach():
    s = _clean()
    for p in gag.PRESETS:                       # 让面积普遍爆表
        s["per_preset"][p].update(mask_coverage_mean=0.30, mask_coverage_p90=0.40,
                                  mask_coverage_max=0.50)
    ok, problems = gag.evaluate(s, _baseline())
    assert not ok and any("红线" in x for x in problems)


def test_evaluate_flags_gradient_silent_guard():
    """ai_gradient 的静默守卫：n_with_mask 必须为 0（该通路在真实图上恒沉默）。"""
    s = _clean()
    s["per_preset"]["ai_gradient"]["n_with_mask"] = 3
    ok, problems = gag.evaluate(s, _baseline())
    assert not ok and any("n_with_mask" in x for x in problems)


def test_evaluate_flags_drift_up():
    b = _baseline()
    s = _clean()
    for p in gag.PRESETS:
        s["per_preset"][p]["mask_coverage_mean"] = b["per_preset"][p]["mask_coverage_mean"] * 1.5 + 1e-6
        s["per_preset"][p]["mask_coverage_max"] = b["per_preset"][p]["mask_coverage_max"] * 1.5 + 1e-6
    ok, problems = gag.evaluate(s, b)
    assert not ok and any("漂移" in x for x in problems)


def test_evaluate_improvement_never_fails():
    """指标变好（面积变小）永远不失败。"""
    b = _baseline()
    s = _clean()
    for p in gag.PRESETS:
        v = max(0.0, b["per_preset"][p]["mask_coverage_mean"] * 0.5)
        s["per_preset"][p].update(mask_coverage_mean=v, mask_coverage_p90=v,
                                  mask_coverage_max=v, n_with_mask=0)
    ok, problems = gag.evaluate(s, b)
    assert ok, problems


def test_evaluate_near_zero_uses_absolute_tolerance():
    """基线≈0 时相对容差不成立 → 用绝对上浮阈值：小幅上升即应报漂移。"""
    b = _baseline()
    s = _clean()
    s["per_preset"]["ai_gradient"].update(mask_coverage_mean=0.02, mask_coverage_p90=0.02,
                                          mask_coverage_max=0.02)
    ok, problems = gag.evaluate(s, b)
    assert not ok and any("漂移" in x for x in problems)


# ------------------------------------------------------------------------------
# 集成冒烟（慢：真跑 20 图 × 4 预设，**实测 ≈171s**，见 docs/185 的登记）
# ⚠ 阶段 191：本测试给子进程传 `timeout=600`，而 `pytest.ini` 的**全局**
#   `--timeout=300` 作用于**测试函数本身** ⇒ 外层比内层还小：一旦机器有负载或
#   在覆盖率插桩下运行，pytest-timeout 会在 300s 掐断整个 session
#   （实测：整场中断、16 分钟的覆盖率数据全丢）。
#   故此处显式抬高**外层**超时，使其严格大于内层（不变式由
#   `tests/test_timeout_contract.py` 机械化守住）。
# ------------------------------------------------------------------------------
@pytest.mark.slow
@pytest.mark.timeout(900)
def test_gate_end_to_end_exit0():
    res = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "scripts", "prompted_area_gate.py")],
        cwd=_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    assert res.returncode == 0, f"门禁未通过：\n{res.stdout}\n{res.stderr}"
    assert "通过" in res.stdout
