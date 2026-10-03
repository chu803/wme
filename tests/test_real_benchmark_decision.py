"""方向 A · 真实对决决策基线守卫（阶段 154）。

目的
----
`real_benchmark_decision.py` 的胜者判定（`main()` 内）历史上**只比较 opencv vs lama**，
完全忽略 `_run_image` 计算出的 `baseline_seam_delta`（原图未修复的接缝色差下界）。
这导致「两个引擎都比『留着水印不修复』更差」时，脚本仍会判出一个 winner（假胜出），
违背文档第 233 行的下界契约。

阶段 154 把判定抽成纯函数 `_decide_winner(ocv, lam, baseline, tol)`，并把
「两引擎都劣于 baseline → inconclusive（不构成胜出）」写进契约。本测试守护该契约。

测试矩阵
--------
1. `_decide_winner` 五态覆盖：inconclusive / opencv / lama / tie / n/a；
2. 历史假胜出回归：ocv=50, lam=60, baseline=10 必须 ≠ opencv（即判 inconclusive）；
3. 集成守卫：monkeypatch `_run_image` 让两引擎都劣于 baseline，`main()` 的标准输出
   必须把该结果记为 `inconclusive 1`，且 `opencv`/`lama` 计数为 0（不得假胜出）。

约束
----
- 不依赖真实 AI 图、不依赖 LaMa（CPU 可跑）；
- 集成测试用 monkeypatch 注入合成结果，避免读真实 manifest/truth。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.real_benchmark_decision import _decide_winner, main


# ---------------------------------------------------------------------------
# 1. 纯函数五态覆盖
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ocv, lam, base, exp",
    [
        # 两引擎都劣于 baseline → inconclusive（核心契约）
        (50.0, 60.0, 10.0, "inconclusive"),
        # opencv 优于 baseline → opencv 胜
        (20.0, 60.0, 40.0, "opencv"),
        # lama 优于 baseline → lama 胜
        (60.0, 20.0, 40.0, "lama"),
        # 都优于 baseline 但差值 < tol → tie
        (30.1, 31.5, 40.0, "tie"),
        # 都优于 baseline 且 lama 略好 → lama
        (20.0, 15.0, 40.0, "lama"),
        # 平手区但两引擎都劣于 baseline → inconclusive（而非误导的 tie）
        (30.1, 31.5, 10.0, "inconclusive"),
        # 任一引擎缺值 → n/a
        (None, 60.0, 10.0, "n/a"),
        (60.0, None, 10.0, "n/a"),
        # 两引擎恰好等于 baseline（≥baseline）→ inconclusive
        (10.0, 10.0, 10.0, "inconclusive"),
    ],
)
def test_decide_winner_states(ocv, lam, base, exp):
    assert _decide_winner(ocv, lam, base, tol=3.0) == exp


# ---------------------------------------------------------------------------
# 2. 历史假胜出回归（最关键）
# ---------------------------------------------------------------------------
def test_false_winner_regression():
    """阶段 154 之前：ocv=50, lam=60, baseline=10 会被判 opencv 胜（假胜出）。

    修复后必须判 inconclusive，且绝不能判成任一引擎胜出。
    """
    verdict = _decide_winner(50.0, 60.0, 10.0, tol=3.0)
    assert verdict != "opencv"
    assert verdict != "lama"
    assert verdict == "inconclusive"


def test_one_engine_better_normal_decision():
    """至少一个引擎优于 baseline 时，正常按 seam_delta 决出胜负。"""
    assert _decide_winner(20.0, 60.0, 40.0, tol=3.0) == "opencv"
    assert _decide_winner(60.0, 20.0, 40.0, tol=3.0) == "lama"


# ---------------------------------------------------------------------------
# 3. 集成守卫：main() 的标准输出必须如实记 inconclusive
# ---------------------------------------------------------------------------
def test_main_tally_reports_inconclusive_when_both_worse(tmp_path, capsys, monkeypatch):
    """两个引擎都劣于 baseline 时，main() 不得假判某引擎胜出。"""
    import importlib
    import sys

    import scripts.real_benchmark_decision as _mod

    # 让 BENCH_DIR 指向 tmp，从而 MANIFEST/TRUTH/RESULTS 都解析到 tmp（无需 patch json.load）。
    monkeypatch.setenv("WME_BENCH_DIR", str(tmp_path))
    rbd = importlib.reload(_mod)  # 重新计算模块级常量，使其指向 tmp

    # 在 tmp 下写最小 manifest + truth，凑出 1 张已标注图。
    # 注意：main() 在调 _run_image 前会先 Image.open(manifest 的 path)，
    # 故必须放一张真实存在的占位 PNG（内容无关，_run_image 已被 mock）。
    from PIL import Image

    dummy = tmp_path / "x.png"
    Image.new("RGB", (8, 8), (128, 128, 128)).save(dummy)
    manifest = {"images": [{"id": "img1", "path": "x.png", "ai_source": "doubao"}]}
    truth = {"entries": [{"image_id": "img1", "watermarks": []}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")

    # 注入合成结果：两引擎都劣于 baseline（opencv=50, lama=60, baseline=10）。
    def fake_run(arr, truth_entry, preset, mask_source="detect"):
        return {
            "mask_pixels": 100,
            "baseline_seam_delta": 10.0,
            "engines": {
                "opencv": {"available": True, "seam_delta": 50.0, "white_frac": 0.0,
                           "niqe": None, "masked_psnr": 10.0},
                "lama": {"available": True, "seam_delta": 60.0, "white_frac": 0.0,
                         "niqe": None, "masked_psnr": 10.0},
            },
        }

    # main() 用 argparse 读 sys.argv，必须给它干净的 argv（否则会解析到 pytest 的参数）。
    # 固定单预设，使结果精确为 1 行（inconclusive 1）。
    monkeypatch.setattr(sys, "argv", ["real_benchmark_decision.py", "--presets", "general"])
    with patch.object(rbd, "_run_image", fake_run):
        rc = rbd.main()
    out = capsys.readouterr().out

    assert rc == 0
    # 关键契约：该结果必须记为 inconclusive，且不得把任一引擎计为假胜出。
    assert "inconclusive 1" in out
    assert "opencv 0" in out
    assert "lama 0" in out
