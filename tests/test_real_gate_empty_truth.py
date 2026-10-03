# -*- coding: utf-8 -*-
"""
阶段 170 守卫：`real_gate` 在「真值存在但为空」时必须给出**设计好的判定**，而非崩溃。

背景（阶段 170 门禁盲区审计 S1）：阶段 150 移除伪造的 corner_logo 标注后，
`benchmark_real/ground_truth.json` 的 `entries` 变为空——这是**合法状态**，
其 `_note` 声称「real_gate 正确报 committed_missing=['ai_logo'] 并硬失败」。

但 `run_real_benchmark.run_all` 在「manifest 非空、无任何图有真值」时会
`raise RuntimeError`；`real_gate.run_real_gate` 直接调用它且**未捕获** ⇒
门禁以一段 traceback 崩溃（退出码 1），
且 `evaluate_real` 的数据完整性层 / 承诺面层 / 面积监控层**全部被跳过**——
文档声称的裁决根本没走到。

本守卫钉住：
  1. 空真值下 `run_real_gate()` **不抛异常**；
  2. 判定为**不通过**，且携带「承诺类型缺少真值证据」这一设计判定；
  3. 同时携带「没有任何预设结果」的数据层判定；
  4. 承诺面横幅可渲染。

刻意不用 pytest fixture（tmp_path/monkeypatch 均无），使其既能进 pytest 套件、
也能被独立脚本直接调用核验。
"""

import json
import os
import tempfile

import scripts.real_gate as rg

_MANIFEST = {
    "version": 1,
    "images": [
        {"id": "01", "filename": "a.jpg", "path": "images/a.jpg",
         "watermark_style_hint": "mixed", "ai_source": "other"},
        {"id": "02", "filename": "b.jpg", "path": "images/b.jpg",
         "watermark_style_hint": "mixed", "ai_source": "other"},
    ],
}

_TRUTH_EMPTY = {"version": 1, "entries": []}


def _run_with(manifest, truth):
    """用临时 manifest/truth 跑一次 run_real_gate，返回 (summary, ok, problems)。"""
    d = tempfile.mkdtemp(prefix="wme170_")
    mp = os.path.join(d, "manifest.json")
    tp = os.path.join(d, "truth.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    with open(tp, "w", encoding="utf-8") as f:
        json.dump(truth, f)
    old_m, old_t = rg.MANIFEST_PATH, rg.TRUTH_PATH
    rg.MANIFEST_PATH, rg.TRUTH_PATH = mp, tp
    try:
        summary = rg.run_real_gate()          # 关键：不得抛异常
    finally:
        rg.MANIFEST_PATH, rg.TRUTH_PATH = old_m, old_t
    ok, problems = rg.evaluate_real(summary, None)
    return summary, ok, problems


def test_empty_truth_does_not_crash():
    """空真值不得让门禁抛异常（回归：此前 raise RuntimeError）。"""
    summary, _ok, _problems = _run_with(_MANIFEST, _TRUTH_EMPTY)  # no exception == pass
    assert summary["config"]["n_expected"] == 0


def test_empty_truth_verdict_is_designed_not_traceback():
    summary, ok, problems = _run_with(_MANIFEST, _TRUTH_EMPTY)
    assert ok is False, "空真值下门禁不应判通过"
    assert any("承诺类型缺少真值证据" in p for p in problems), \
        f"缺少承诺面判定（committed_missing）：{problems}"
    assert any("没有任何预设结果" in p for p in problems), \
        f"缺少数据层判定：{problems}"


def test_empty_truth_scope_reports_committed_missing():
    summary, _ok, _problems = _run_with(_MANIFEST, _TRUTH_EMPTY)
    scope = summary.get("scope") or {}
    assert scope.get("committed_missing"), "commit 面应报缺真值"
    assert scope.get("complete") is False


def test_empty_truth_markdown_renders_banner():
    summary, _ok, _problems = _run_with(_MANIFEST, _TRUTH_EMPTY)
    md = rg.to_markdown(summary)
    assert "承诺" in md or "标注覆盖面" in md, "承诺面横幅未渲染"


def test_empty_manifest_still_raises():
    """边界：manifest 里**没有任何图**（「未就绪」，非「空真值」）时语义不变——
    仍由 run_all 报「manifest 为空」。本修复只接管「有图但无真值」这一状态。"""
    d = tempfile.mkdtemp(prefix="wme170_")
    mp = os.path.join(d, "manifest.json")
    tp = os.path.join(d, "truth.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "images": []}, f)
    with open(tp, "w", encoding="utf-8") as f:
        json.dump(_TRUTH_EMPTY, f)
    old_m, old_t = rg.MANIFEST_PATH, rg.TRUTH_PATH
    rg.MANIFEST_PATH, rg.TRUTH_PATH = mp, tp
    try:
        raised = False
        try:
            rg.run_real_gate()
        except Exception:  # noqa: BLE001
            raised = True
        assert raised, "空 manifest 应仍由 run_all 报错（未就绪），不应被静默吞掉"
    finally:
        rg.MANIFEST_PATH, rg.TRUTH_PATH = old_m, old_t
