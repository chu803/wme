# -*- coding: utf-8 -*-
"""阶段 322a · 修复侧真值门禁（scripts/removal_truth_probe.py --gate）自身的守卫。

延续 `tests/test_media_gate.py` 的立场：门禁最怕两件事——
**一开门就红**（没人信，第二天就被关掉）与**永远是绿的哑巴**（等于没有）。
本文件用不跑全链路（≈10 分钟）的廉价手段钉住：
  · 判据正确：四条红线任一劣化 ⇒ 必须报问题；持平 ⇒ 必须不报
  · 基线自洽： committed 基线与自身比对必须 0 问题，且自身不踩绝对红线（防「 born red」）
  · 退出码契约：缺依赖/资产 ⇒ 2；缺基线/空结果 ⇒ 不是 0
  · 接入门禁清单：`--gates` 里有它，且默认步骤里没有
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import removal_truth_probe as rtp  # noqa: E402

CASES = ("corner_logo", "edge_text", "faint_tiled", "grid")


def _medians(recall=0.40, gain=0.30, collateral=1.5) -> dict:
    """四个类型都给一份「与基线持平」的中位数（构造判据输入，不跑管线）。"""
    return {c: {"n": 15, "det_recall": recall, "removal_gain": gain,
                "collateral_pct": collateral} for c in CASES}


def _baseline(gain_faint=0.01, gain_grid=0.002) -> dict:
    base = _medians()
    base["faint_tiled"]["removal_gain"] = gain_faint
    base["grid"]["removal_gain"] = gain_grid
    return {"by_case": base}


# ---------------------------------------------------------------------------
# ① 判据本身
# ---------------------------------------------------------------------------

def test_flat_run_produces_no_problems():
    """持平不得报问题（否则门禁一开门就红）。"""
    assert rtp._gate_problems(_medians(), _baseline()) == []


@pytest.mark.parametrize("mutate,prefix", [
    (lambda m: m["corner_logo"].update(det_recall=0.40 - rtp.RECALL_FLOOR_DELTA - 0.01), "R1"),
    (lambda m: m["edge_text"].update(removal_gain=0.30 - rtp.GAIN_FLOOR_DELTA - 0.01), "R2"),
    (lambda m: m["faint_tiled"].update(removal_gain=-0.001), "R2b"),
    (lambda m: m["grid"].update(collateral_pct=rtp.COLLATERAL_MAX + 0.1), "R3"),
])
def test_each_red_line_fires(mutate, prefix):
    """四条红线任一劣化 ⇒ 必须报，且要标出编号（能被人在日志里认出来）。"""
    cur = _medians()
    mutate(cur)
    problems = rtp._gate_problems(cur, _baseline())
    assert any(p.startswith(prefix) for p in problems), f"{prefix} 没触发：{problems}"


def test_tolerance_is_not_zero_width():
    """容差边界内不报（防止红线退化成「任何抖动都红」而被人关掉）。"""
    cur = _medians()
    cur["corner_logo"]["det_recall"] = 0.40 - rtp.RECALL_FLOOR_DELTA
    cur["edge_text"]["removal_gain"] = 0.30 - rtp.GAIN_FLOOR_DELTA
    cur["grid"]["collateral_pct"] = rtp.COLLATERAL_MAX
    assert rtp._gate_problems(cur, _baseline()) == []


def test_missing_case_is_flagged():
    """基线里有、本轮没有 ⇒ 必须是问题（跑挂/资产缺失不能算通过）。"""
    cur = _medians()
    cur.pop("edge_text")
    assert any(p.startswith("R0") for p in rtp._gate_problems(cur, _baseline()))


def test_empty_medians_are_never_green():
    """门禁最危险的失效形态：本轮一条数据都没有 ⇒ 绝不能读到「没问题」。"""
    problems = rtp._gate_problems({}, _baseline())
    assert len(problems) == len(CASES), problems


def test_uncomputable_gain_is_flagged():
    """擦除度算不出（None）不能静默跳过。"""
    cur = _medians()
    cur["edge_text"]["removal_gain"] = None
    assert any(p.startswith("R2") for p in rtp._gate_problems(cur, _baseline()))


def test_net_harm_line_scoped_to_declared_cases():
    """R2b 只管「近乎看不见的水印」两类；corner/edge 的负增益由 R2 相对基线管。"""
    cur = _medians()
    cur["corner_logo"]["removal_gain"] = -0.01
    assert not any(p.startswith("R2b") for p in rtp._gate_problems(cur, _baseline()))
    assert set(rtp.NET_HARM_CASES) == {"faint_tiled", "grid"}


# ---------------------------------------------------------------------------
# ② 「什么都没做」必须算进中位数（阶段 322a 首轮实测抓到的哑巴口）
# ---------------------------------------------------------------------------

def test_noop_row_measures_zero_not_missing():
    """输出==输入 时 measure 必须给出 擦除度 0 / 误伤 0 / 检回 0（这正是 skipped 分支的语义）。"""
    import numpy as np

    clean = (np.arange(64 * 64 * 3, dtype=np.int32).reshape(64, 64, 3) % 200).astype(np.uint8)
    marked = clean.copy()
    marked[8:16, 8:16] = 250                       # 造一个「水印」
    m = rtp.measure(clean, marked, marked, None)    # 修复=没修（输出等于输入）
    assert m["valid"] is True
    assert m["removal_gain"] == 0.0, m
    assert m["collateral_pct"] == 0.0, m
    assert m["det_recall"] == 0.0, m


def test_medians_count_undetected_rows():
    """空掩码（未检出）行必须进中位数：否则检测器集体失明只会让 n 变小、中位数照样漂亮。"""
    rows = []
    for i in range(15):
        rows.append({"valid": True, "case": "grid", "det_recall": 0.0,
                     "removal_gain": 0.0, "collateral_pct": 0.0,
                     "note": "未检出（跳过，输出=输入）"})
    med = rtp._gate_medians(rows)
    assert med["grid"]["n"] == 15, med
    # 检回 0 且基线非 0 ⇒ 必须被 R1 抓住（证明「失明」有代价）
    problems = rtp._gate_problems(med, {"by_case": {"grid": {
        "n": 15, "det_recall": 0.3, "removal_gain": 0.2, "collateral_pct": 1.0}}})
    assert any(p.startswith("R1") for p in problems), problems


# ---------------------------------------------------------------------------
# ③ 阈值与 committed 基线的自洽（秒级，替 10 分钟的门禁预演）
# ---------------------------------------------------------------------------

def test_gate_thresholds_are_meaningful():
    """红线不得被改成 0 宽/0 容差/单图样本——那会让守卫变装饰或恒红。"""
    assert rtp.RECALL_FLOOR_DELTA > 0 and rtp.GAIN_FLOOR_DELTA > 0
    assert 0 < rtp.COLLATERAL_MAX <= 10, rtp.COLLATERAL_MAX
    assert rtp.GATE_LIMIT >= 15, "门禁样本量不得缩到 15 张以下（中位数会抖）"
    assert rtp.GAIN_ABS_FLOOR == 0.0, "净伤害红线就是「不许比不擦更差」，不得放宽"


def test_committed_baseline_is_self_consistent():
    """基线文件与自身比对必须 0 问题，且自身不踩绝对红线 ⇒ 门禁不可能「出生就红」。"""
    assert os.path.isfile(rtp.BASELINE_PATH), f"缺基线文件：{rtp.BASELINE_PATH}"
    base = json.load(open(rtp.BASELINE_PATH, encoding="utf-8"))
    by = base.get("by_case") or {}
    assert set(by) == set(CASES), f"基线类型不全：{sorted(by)}"
    assert rtp._gate_problems(
        {k: dict(v) for k, v in by.items()}, base) == [], "基线自身就踩红线"
    for c in rtp.NET_HARM_CASES:
        assert by[c]["removal_gain"] >= rtp.GAIN_ABS_FLOOR, f"{c} 基线已是净伤害"
    for c in CASES:
        assert by[c]["collateral_pct"] <= rtp.COLLATERAL_MAX, f"{c} 基线已超误伤承诺"
        assert by[c]["n"] >= rtp.GATE_LIMIT // 2, f"{c} 基线样本太少：{by[c]['n']}"


# ---------------------------------------------------------------------------
# ③ 退出码与调用契约
# ---------------------------------------------------------------------------

def test_skip_when_clean_dir_missing(monkeypatch, tmp_path):
    """资产缺失必须 rc=2，绝不能 rc=0（假绿）。"""
    monkeypatch.setattr(rtp, "CLEAN_DIR", str(tmp_path / "nowhere"))
    assert rtp.main(["--gate"]) == 2


def test_skip_when_dir_has_no_clean_jpegs(monkeypatch, tmp_path):
    monkeypatch.setattr(rtp, "CLEAN_DIR", str(tmp_path))
    (tmp_path / "not_a_clean.jpg").write_text("x", encoding="utf-8")
    assert rtp.main(["--gate"]) == 2


def test_skip_when_ocr_unavailable(monkeypatch, tmp_path):
    """缺 easyocr ⇒ 环境不合格（2），不是「产品擦不干净」（1），更不是通过（0）。

    实测依据：用未装 easyocr 的解释器跑 60 用例，`edge_text` 检回中位 0.992→0.0、
    `faint_tiled` 擦除度 +0.010→−0.030，数字仍然「自洽」——量的是降级产品，会污染基线。
    """
    for i in range(3):
        (tmp_path / f"clean_{i:02d}.jpg").write_text("x", encoding="utf-8")
    monkeypatch.setattr(rtp, "CLEAN_DIR", str(tmp_path))
    monkeypatch.setattr(rtp, "_has_ocr", lambda: False)
    assert rtp.main(["--gate"]) == 2
    reason = rtp._skip_reason()
    assert reason and "EasyOCR" in reason, reason
    # 反向：OCR 在位时这条理由必须消失（否则门禁永久跳过 = 哑巴）
    monkeypatch.setattr(rtp, "_has_ocr", lambda: True)
    assert "EasyOCR" not in (rtp._skip_reason() or "")


def test_gate_mode_pins_sample_and_cases(monkeypatch, tmp_path):
    """门禁口径必须固定：忽略调用方传的 --limit/--cases/--engine（否则可被绕松）。"""
    captured = {}

    def _fake_run(photos, cases, engine, json_path, verbose, gate=False, **kw):
        captured.update(photos=photos, cases=cases, engine=engine,
                        json_path=json_path, gate=gate)
        return 0

    for i in range(20):
        (tmp_path / f"clean_{i:02d}.jpg").write_text("x", encoding="utf-8")
    maker = type("M", (), {"CASES": {c: (None, "-", c) for c in CASES}})()
    monkeypatch.setattr(rtp, "CLEAN_DIR", str(tmp_path))
    monkeypatch.setattr(rtp, "_skip_reason", lambda: None)
    monkeypatch.setattr(rtp, "_load_maker", lambda: maker)
    monkeypatch.setattr(rtp, "run", _fake_run)

    rc = rtp.main(["--gate", "--limit", "1", "--cases", "corner_logo", "--engine", "opencv"])
    assert rc == 0
    assert len(captured["photos"]) == rtp.GATE_LIMIT, captured["photos"]
    assert set(captured["cases"]) == set(CASES)
    assert captured["engine"] == "auto"
    assert captured["gate"] is True
    # 明细默认落到 cache/（gitignore），不污染仓库
    assert captured["json_path"].startswith(os.path.join(_ROOT, "cache"))


def test_write_baseline_requires_gate_mode(monkeypatch, tmp_path, capsys):
    """基线只能由门禁口径写：普通诊断跑 --write-baseline 不得改基线。"""
    target = tmp_path / "baseline.json"
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps({"rows": [
        {"valid": True, "case": c, "det_recall": 0.4, "removal_gain": 0.3,
         "collateral_pct": 1.0} for c in CASES]}), encoding="utf-8")

    for i in range(3):
        (tmp_path / f"clean_{i:02d}.jpg").write_text("x", encoding="utf-8")
    maker = type("M", (), {"CASES": {c: (None, "-", c) for c in CASES}})()

    def _fake_run(photos, cases, engine, json_path, verbose, gate=False, **kw):
        return 0

    monkeypatch.setattr(rtp, "CLEAN_DIR", str(tmp_path))
    monkeypatch.setattr(rtp, "BASELINE_PATH", str(target))
    monkeypatch.setattr(rtp, "_load_maker", lambda: maker)
    monkeypatch.setattr(rtp, "run", _fake_run)

    assert rtp.main(["--write-baseline", "--cases", "corner_logo",
                     "--json", str(rows)]) == 0
    assert not target.exists(), "非门禁模式不该写基线"


def test_wired_into_run_all_checks_gates():
    from scripts import run_all_checks as rac

    names = [s.name for s in rac.build_steps(full=False, gates=True, root=_ROOT)]
    assert "removal_truth_gate" in names, f"修复侧门禁未接入门禁步骤：{names}"


def test_not_in_default_steps():
    """默认（不带 --gates）不跑：真照片全链约 10 分钟，CI 轻量子集保持轻。"""
    from scripts import run_all_checks as rac

    names = [s.name for s in rac.build_steps(full=False, gates=False, root=_ROOT)]
    assert "removal_truth_gate" not in names, names
