"""阶段 159 · `scripts/validate_selector.py` 一致性判定守卫。

背景
----
该脚本用「合成数据已知 ground truth」校验无参考指标 `no_reference_inpaint_score`
的选优方向是否被写反（阶段 56 遗留，此前**零专属测试**）。其一致性判定原实现：

    gt = "opencv" if do > dl else "lama"
    mt = "opencv" if so > sl else "lama"
    ok = (gt == mt)

在**平局**（`do == dl` 或 `so == sl`）时静默倒向 "lama"，于是「指标零判别力」的退化样本
被计入一致性：全平局时打印「一致性 N/N = 100%」——**零信息却给满分**，直接与该脚本
「证明指标方向未被写反」的目的相悖（对齐经验 7/15/22：度量会奖励劣化；
「跳过」必须与「通过」区分；缺数据须与零可区分）。

修复（阶段 159）
----------------
平局单列 `=`、**不计入**一致性；数据缺失单列 `—`；两者分别计数并在表尾汇报；
当**无**携带方向信息的有效样本时，显式声明「无法评估一致性」，绝不打印一致性率。

本守卫用 stub 的 `_one` 驱动真实 `main()` 的判定块，断言上述契约。
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import validate_selector as v  # noqa: E402
from scripts import make_demo_assets as mda  # noqa: E402


# --------------------------------------------------------------------------- #
# 夹具：stub 掉 `_one`（避免加载 torch），只驱动 main() 的一致性判定块
# --------------------------------------------------------------------------- #
def _row(cn, dk, seed, do, dl, so, sl):
    return {"case": cn, "case_cn": cn, "data": dk, "seed": seed,
            "dpsnr_opencv": do, "dpsnr_lama": dl,
            "score_opencv": so, "score_lama": sl}


def _run(monkeypatch, stub_one, seeds: str = "1"):
    """用两个用例名（A/B）× 两个底图跑 main()，返回 (rc, 标准输出)。"""
    monkeypatch.setattr(mda, "CASES", {"A": (None, None, "用例A"),
                                       "B": (None, None, "用例B")})
    monkeypatch.setattr(v, "_one", stub_one)
    monkeypatch.setattr(sys, "argv", ["prog", "--seeds", seeds])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = v.main()
    return rc, buf.getvalue()


def _out(res):
    return res[1]


# --------------------------------------------------------------------------- #
# 1 · 核心：全平局绝不是「100% 一致」
# --------------------------------------------------------------------------- #
def test_all_ties_never_reports_perfect(monkeypatch):
    def stub(name, seed, base_fn, dk):        # 两引擎全平局 ⇒ 指标零判别力
        return _row(f"用例{name}", dk, seed, 0.0, 0.0, 0.5, 0.5)

    rc, out = _run(monkeypatch, stub)
    assert rc == 0
    assert "100.0%" not in out, "全平局不得被读成 100% 一致（阶段 159）"
    assert "无法评估一致性" in out, "无有效判别样本时必须显式声明无法评估"
    assert "平局·退化 4 例" in out, "应汇报退化样本计数"


# --------------------------------------------------------------------------- #
# 2 · 平局不得计入分子（否则把 0/2 抬成 2/4）
# --------------------------------------------------------------------------- #
def test_ties_excluded_from_agreement(monkeypatch):
    def stub(name, seed, base_fn, dk):
        if name == "A":                       # 真值 opencv / 指标 lama ⇒ 相悖
            return _row("用例A", dk, seed, 5.0, 1.0, 0.1, 0.9)
        return _row("用例B", dk, seed, 0.0, 0.0, 0.5, 0.5)   # 双平局 ⇒ 退化

    rc, out = _run(monkeypatch, stub)
    assert rc == 0
    assert "0/2 = 0.0%" in out, "平局必须被剔除：诚实结果是 0/2=0%，不是 2/4=50%"
    assert "2/4" not in out
    assert "平局·退化 2 例" in out


# --------------------------------------------------------------------------- #
# 3 · 正常的真实一致/相悖仍应照常计入（回归：不误伤正常路径）
# --------------------------------------------------------------------------- #
def test_real_agreement_counted(monkeypatch):
    def stub(name, seed, base_fn, dk):        # 真值 opencv / 指标 opencv ⇒ 一致
        return _row(f"用例{name}", dk, seed, 5.0, 1.0, 0.9, 0.1)

    rc, out = _run(monkeypatch, stub)
    assert rc == 0
    assert "4/4 = 100.0%" in out
    assert "平局·退化 0 例" in out


def test_real_disagreement_zero(monkeypatch):
    def stub(name, seed, base_fn, dk):        # 真值 opencv / 指标 lama ⇒ 相悖
        return _row(f"用例{name}", dk, seed, 5.0, 1.0, 0.1, 0.9)

    rc, out = _run(monkeypatch, stub)
    assert rc == 0
    assert "0/4 = 0.0%" in out


# --------------------------------------------------------------------------- #
# 4 · 数据缺失（某引擎失败）单列且不计入
# --------------------------------------------------------------------------- #
def test_invalid_rows_flagged_and_excluded(monkeypatch):
    def stub(name, seed, base_fn, dk):
        if name == "A":                       # 有效且一致
            return _row("用例A", dk, seed, 5.0, 1.0, 0.9, 0.1)
        r = _row("用例B", dk, seed, 5.0, 1.0, 0.9, 0.1)
        r["dpsnr_lama"] = None                # lama 失败 ⇒ 数据缺失
        r["score_lama"] = None
        return r

    rc, out = _run(monkeypatch, stub)
    assert rc == 0
    assert "2/2 = 100.0%" in out, "缺失样本必须被剔除"
    assert "数据缺失 2 例" in out


# --------------------------------------------------------------------------- #
# 5 · 无任何有效结果 → rc=1
# --------------------------------------------------------------------------- #
def test_no_rows_returns_1(monkeypatch):
    def stub(name, seed, base_fn, dk):
        return None

    rc, out = _run(monkeypatch, stub)
    assert rc == 1
    assert "没有任何用例产出有效结果" in out


# --------------------------------------------------------------------------- #
# 6 · 图例必须登记「= 平局」与「— 缺失」两种标记
# --------------------------------------------------------------------------- #
def test_legend_documents_tie_and_missing(monkeypatch):
    def stub(name, seed, base_fn, dk):
        return _row(f"用例{name}", dk, seed, 5.0, 1.0, 0.9, 0.1)

    _, out = _run(monkeypatch, stub)
    assert "平局" in out and "缺失" in out, "图例必须区分平局与缺失两种非判定标记"
