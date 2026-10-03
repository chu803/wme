# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/stats.py
 文件说明 : 通用统计小工具 —— **分位数的唯一实现**（阶段 186 抽取）。
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.

 为什么必须共用（而不是各处再写一份）
 ------------------------------------
 `scripts/real_gate.py` 的面积画像（P90 掩码覆盖率）与 `scripts/perf_probe.py`
 的延迟门禁（P50 / P95）都需要「分位数」。本项目反复吃过
 **同一判据两处实现 ⇒ 慢慢漂移** 的亏（LESSONS 187「打印契约≠强制契约」、
 195「推广到 N 个文件必须带整族守卫」）。故此处抽出**唯一实现**，
 两处都调用它；口径改动只需改这一处。

 口径（与阶段 93 起沿用的完全一致）
 ----------------------------------
 **最近秩（nearest-rank）**，不做线性插值：

     idx = min(n-1, max(0, round(q * (n-1))))

 取的是「有序样本里真实出现过的那个值」。选它而不是插值版，是因为本项目的
 分位数都用来做**判据**（红线 / 漂移 / 预算），判据必须能被解释成
 「确实有一张图 / 一次运行达到了这个值」——插值出来的数在物理上不存在。
 ⚠ 注意 `round()` 是**银行家舍入**（round-half-to-even）：`round(1.5) == 2`、
 `round(2.5) == 2`。这是**既有行为**，抽取时原样保留（改动它会静默改变 P90 取值，
 进而影响面积漂移判定）。
==============================================================================
"""
from __future__ import annotations

__all__ = ["nearest_rank_percentile"]


def nearest_rank_percentile(values, q: float):
    """返回 `values` 在 q 分位处的**实际样本值**（最近秩，无插值）。

    参数说明：
        values : 可迭代的数值序列（不必有序；内部会排序）。
        q      : 分位点，闭区间 [0.0, 1.0]。q=0 → 最小值；q=1 → 最大值。

    返回值：
        该分位处的样本值；`values` 为空时返回 `None`。

    异常：
        ValueError —— q 不在 [0, 1] 内（含 NaN，因为 `NaN <= 0` 与 `NaN >= 1`
        都为 False，会穿透朴素判断 —— 阶段 145 的同类教训）。
    """
    vals = list(values)
    if not vals:
        return None
    # 显式拦 NaN：`0.0 <= nan <= 1.0` 恒为 False，但为了让报错信息明确，
    # 这里单独判断（`q != q` 是 NaN 的唯一可靠识别方式）。
    if q != q or not (0.0 <= q <= 1.0):
        raise ValueError(f"q 必须落在 [0, 1] 区间内，实际为 {q!r}")
    xs = sorted(vals)
    n = len(xs)
    idx = min(n - 1, max(0, int(round(q * (n - 1)))))
    return xs[idx]
