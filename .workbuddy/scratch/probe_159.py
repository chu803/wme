# -*- coding: utf-8 -*-
"""Stage 159 探针：量化 validate_selector 一致性判定的「退化样本被读成一致」风险。

不加载 torch：直接 stub `validate_selector._one` 造出受控 rows，再跑真实 `main()`
的一致性判定块，观察打印的一致性率。
"""
from __future__ import annotations

import contextlib
import io
import sys

ROOT = "D:/work 111/2026-08-03-17-38-51"
sys.path.insert(0, ROOT)

from scripts import validate_selector as v  # noqa: E402
from scripts import make_demo_assets as mda  # noqa: E402


def _row(name, cn, dk, seed, do, dl, so, sl):
    return {"case": name, "case_cn": cn, "data": dk, "seed": seed,
            "dpsnr_opencv": do, "dpsnr_lama": dl,
            "score_opencv": so, "score_lama": sl}


def _run_with(stub_one, seeds="1"):
    mda.CASES = {"A": (None, None, "用例A"), "B": (None, None, "用例B")}
    v._one = stub_one
    buf = io.StringIO()
    old_argv = sys.argv
    sys.argv = ["prog", "--seeds", seeds]
    try:
        with contextlib.redirect_stdout(buf):
            rc = v.main()
    finally:
        sys.argv = old_argv
    return rc, buf.getvalue()


def _print_last_lines(out, n=6):
    lines = [l for l in out.splitlines() if l.strip()]
    for l in lines[-n:]:
        print("   |", l)


def scenario_all_ties():
    """所有用例两引擎都平局 ⇒ 指标零判别力，一致性应为「无有效样本」。"""
    def stub(name, seed, base_fn, dk):
        return _row(name, f"用例{name}", dk, seed, 0.0, 0.0, 0.5, 0.5)  # 全平局
    rc, out = _run_with(stub)
    print("[探针1] 全部平局（指标零判别力）:")
    _print_last_lines(out, 8)
    print()


def scenario_mixed():
    """A=真值与指标相反（不一致）；B=双平局（退化）。观察退化样本是否被计入一致性。"""
    def stub(name, seed, base_fn, dk):
        if name == "A":
            # 真值选 opencv(5>1)，指标选 lama(0.1<0.9) ⇒ 不一致
            return _row("A", "用例A", dk, seed, 5.0, 1.0, 0.1, 0.9)
        # 双平局 ⇒ 退化
        return _row("B", "用例B", dk, seed, 0.0, 0.0, 0.5, 0.5)
    rc, out = _run_with(stub)
    print("[探针2] A 不一致 + B 退化平局:")
    _print_last_lines(out, 10)
    print()


if __name__ == "__main__":
    scenario_all_ties()
    scenario_mixed()
    print("PROBE DONE")
