#!/usr/bin/env python3
# ==============================================================================
#  阶段 214 · 锚点法（渐进劣化闸门）的守卫
# ------------------------------------------------------------------------------
#  要解决的问题（Stage 213 实证）
#  ------------------------------
#  标定 full 基线时 P95 跳到 27.664s，而 20 分钟前同口径是 13.652s。逐张明细：
#      前5 张 ~13s → 第6 张 15.8s → 第7~10 张稳定 ~27s
#  ⇒ **单轮内渐进劣化**（笔记本 GPU 持续负载降频）。
#
#  为什么已有的 `spread/P95` 抓不到
#  ------------------------------
#  `spread/P95` 衡量「**同一张图的重复之间**」的离散度；而劣化发生在「**图与图之间**」。
#  劣化组每张图内部很一致（27.2/28.0s）⇒ spread/P95 = 0.063 判「✅ 可信」，
#  **完全隐形**。而 P95 是分位数，**恰好取到劣化末期那张最慢图** ⇒ 污染被固化。
#  两者是**正交**的污染形态。
#
#  本文件的守卫对象
#  ----------------
#  ① 锚点法能抓渐进劣化、且**不误报**稳态负载（实测方向相反，见 threshold 注释）；
#  ② 阈值落在实测两组之间（钉死，避免被拍脑袋改动）；
#  ③ 噪声层与锚点层**互不遮蔽**（两种污染都能被抓到）；
#  ④ 拿不到锚点数据时**不假装可信**（保守）。
# ==============================================================================
from __future__ import annotations

import inspect
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import perf_probe as pp  # noqa: E402


def _base(p95=1.0, stage="detect"):
    return {"version": pp.BASELINE_VERSION, "p95": p95, "source": "benchmark",
            "stage": stage, "repeat": 3, "device": "cuda", "gpu_name": "X",
            # 阶段 215：补 engine/runs 两个可比性维度（缺任一 ⇒ 判「结构过旧」）。
            "engine": None, "runs": 10}


def _meta(spread=0.02, anchor=0.97, stage="detect"):
    return {"source": "benchmark", "stage": stage, "repeat": 3,
            "device": "cuda", "gpu_name": "X",
            "engine": None, "runs": 10,
            "spread_mean": spread, pp.ANCHOR_KEY: anchor}


# ==============================================================================
# ① 能抓 / 不误报
# ==============================================================================
def test_anchor_gate_catches_progressive_degradation():
    """渐进劣化（锚点比 1.74，实测值）⇒ 判跳过(2)，**绝不判通过**。"""
    code, reasons = pp.gate_decision(_base(), {"p95": 1.0},
                                     _meta(spread=0.063, anchor=1.7399))
    assert code == 2, f"渐进劣化必须判跳过，实际 {code}：{reasons}"
    assert any("渐进劣化" in r for r in reasons), \
        f"理由应指向「渐进劣化」，实际：{reasons}"


def test_anchor_gate_does_not_fire_on_steady_load():
    """稳态外部负载（锚点比 0.97，绝对值慢 2×）⇒ **不**被锚点层拦。

    为什么这很重要：稳态负载会真实抬高 P95，此时应由**回归护栏**（与基线比）
    决定红绿，而不是由「渐进劣化」层越权拦截——否则两个门禁职责混淆。
    """
    code, reasons = pp.gate_decision(_base(), {"p95": 1.0},
                                     _meta(spread=0.05, anchor=0.9731))
    assert code == 0, f"锚点比≈1 不应被锚点层拦（实际 {code}）：{reasons}"
    assert not any("渐进劣化" in r for r in reasons)


def test_anchor_gate_prevents_poisoned_baseline():
    """**核心反例**：污染值即使「好看」也不得判通过。

    构造 Stage 213 的实际场景：P95 因渐进劣化被推到 2×，若判通过就会把污染
    值固化成「以后不许变慢」的参照，之后真回退都判不出来。
    """
    code, _ = pp.gate_decision(_base(p95=1.0), {"p95": 0.01},
                               _meta(spread=0.063, anchor=1.7399))
    assert code == 2, "渐进劣化 + P95 极漂亮也不得判通过"


# ==============================================================================
# ② 阈值钉死
# ==============================================================================
def test_anchor_max_lies_in_measured_gap():
    """阈值必须落在实测两组之间（阶段 214 实测）。

    · 渐进劣化轮（干净环境，但本机GPU 持续降频）：锚点比 = **1.7399**
    · 稳态负载轮（12 进程占CPU）：锚点比 = **0.9731**
    ⇒ 阈值须 > 0.9731（不误报负载）且 < 1.7399（不漏放劣化）。
    """
    assert 0.9731 < pp.ANCHOR_MAX < 1.7399, (
        f"ANCHOR_MAX={pp.ANCHOR_MAX} 落在实测两组之外"
        f"（稳态负载≈0.97，渐进劣化≈1.74）——说明阈值被随意改动过")


def test_anchor_key_is_declared():
    assert isinstance(pp.ANCHOR_KEY, str) and pp.ANCHOR_KEY
    src = inspect.getsource(pp.measure)
    assert pp.ANCHOR_KEY in src, "measure() 必须写入锚点比"
    assert "anchor_head" in src and "anchor_tail" in src, \
        "锚点测量必须记录首/尾原始耗时（供人工判读，不能只存比值）"


# ==============================================================================
# ③ 两层互不遮蔽
# ==============================================================================
def test_noise_and_anchor_gates_are_orthogonal():
    """两种污染都必须能被抓到（互不遮蔽）。"""
    # 只噪声大
    c1, r1 = pp.gate_decision(_base(), {"p95": 1.0},
                              _meta(spread=0.35, anchor=0.97))
    assert c1 == 2 and any("测量被环境污染" in r for r in r1)

    # 只渐进劣化
    c2, r2 = pp.gate_decision(_base(), {"p95": 1.0},
                              _meta(spread=0.02, anchor=1.9))
    assert c2 == 2 and any("渐进劣化" in r for r in r2)

    # 两者都有⇒ 仍判跳过（任一即可）
    c3, _ = pp.gate_decision(_base(), {"p95": 1.0},
                             _meta(spread=0.35, anchor=1.9))
    assert c3 == 2

    # 两者都干净 ⇒ 才判通过
    c4, _ = pp.gate_decision(_base(), {"p95": 1.0},
                             _meta(spread=0.02, anchor=0.97))
    assert c4 == 0


def test_anchor_gate_precedes_regression_rail():
    """渐进劣化时**先**归因为「测量不可信」，不报「超护栏」。

    否则同一个红会被当成「代码变慢」，导致回滚无辜代码（LESSONS 232 同理）。
    """
    code, reasons = pp.gate_decision(_base(p95=0.1), {"p95": 5.0},
                                     _meta(spread=0.02, anchor=1.9))
    assert code == 2
    assert not any("超护栏" in r for r in reasons), \
        "渐进劣化不应同时报「超护栏」（会把归因引向代码回退）"


# ==============================================================================
# ④ 缺数据时保守
# ==============================================================================
def test_missing_anchor_does_not_pretend_clean():
    """锚点数据缺失时**不得**被当作「无渐变」而直接放行护栏。

    刻意与 `repeat<2` 的处置**相反**（那是「有证据但无区分力」→只提示）：
    锚点是**新采集的**信号，拿不到说明采集逻辑没跑到，**属无证据**。
    """
    m = _meta(spread=0.02)
    m.pop(pp.ANCHOR_KEY, None)
    code, reasons = pp.gate_decision(_base(), {"p95": 1.0}, m)
    assert code == 2, f"无锚点数据须保守判跳过，实际 {code}：{reasons}"
    assert any("锚点" in r for r in reasons), \
        f"理由应指向「锚点数据缺失」，实际：{reasons}"


def test_anchor_zero_or_negative_is_rejected():
    """锚点比 ≤ 0 是异常值（首轮耗时为 0/负）⇒ 按缺数据处理，不得放行。"""
    for bad in (0.0, -0.5):
        code, _ = pp.gate_decision(_base(), {"p95": 1.0},
                                   _meta(spread=0.02, anchor=bad))
        assert code == 2, f"锚点比={bad} 属异常，须保守判跳过（实际 {code}）"


# ==============================================================================
# ⑤ 生产路径与文档
# ==============================================================================
def test_gate_decision_docstring_documents_anchor_layer():
    doc = inspect.getdoc(pp.gate_decision) or ""
    assert "锚点" in doc, "gate_decision 的docstring 必须说明锚点层（否则后人不知其存在）"
    assert "正交" in doc, "必须写明与 spread/P95 层是**正交**关系"


def test_anchor_gate_ordered_after_noise_layer():
    """锚点层必须排在噪声层**之后**（先判瞬态、再判渐变）。

    顺序本身是语义：瞬态（瞬时占用）与渐变（热降频）可同时存在，
    报出先命中的那个更有诊断价值（噪声层提示「关闭其他任务」，
    锚点层提示「让机器冷却」）。

    ⚠ 定位必须**避开 docstring**：`gate_decision` 的文档里也会出现
    「渐进劣化」字样，直接 `find` 会命中文档而非代码（实测踩到）。
    正确做法：先跳过函数文档字符串（用 rindex 定位最后一个三引号闭合处），再定位。
    """
    src = inspect.getsource(pp.gate_decision)
    # 去掉模块级/函数级 docstring：找到最后一个三引号块之后的代码部分
    code_start = src.rindex('"""') + 3
    body = src[code_start:]
    i_noise = body.find("测量被环境污染")
    i_anchor = body.find("渐进劣化")
    assert i_noise != -1, "判定代码里应含噪声层提示语"
    assert i_anchor != -1, "判定代码里应含锚点层提示语"
    assert i_noise < i_anchor, (
        f"噪声层应在锚点层之前（噪声 @{i_noise} vs 锚点 @{i_anchor}）")