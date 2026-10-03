#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/mask_param_audit.py
 文件说明 : 掩码「膨胀 / 羽化」参数的真值标定审计（阶段 65）。

            掩码膨胀（`MASK_DILATE_DEFAULT`）是"擦不擦得干净"的直接杠杆：太小 → 水印
            边缘残留半透明轮廓；太大 → 白改好像素（过度重绘）。本脚本对 benchmark 用例
            扫描 dilate / feather，量化两个代价：

              残留   residual  = |out − clean| 在【真值水印区】的均值（擦不干净的代价）
              过度   overpaint = |out − clean| 在【掩码扩张带】的均值（白改好像素的代价）
              过度像素         = 该带内被改动 > 2 灰阶的像素数（破坏面积）

            **阶段 65 结论（640²，OpenCV 引擎）**：膨胀单调有害——残留不随膨胀下降，
            过度重绘线性上升（grid 过度像素 2484→38135，15 倍）；唯一边际收益在细字
            （edge_text，dilate 2~4 残留最低）。根因是检测器掩码本身已带 padding，叠加
            大膨胀属重复扩张。默认值已从 8 收敛为 **2**。

            ⚠️ **阶段 105 修正**：上一条「细字需要膨胀」是**混杂结论**。当时的扫描
            （见 `run()`）只在 dilate 维上变档，feather 仅在 `d == 默认值` 时才换挡，
            因此 **(dilate=0, feather=0) 这个角从未被测量** ⇒ 把「羽化冲淡细笔画」的
            代价记到了膨胀头上。补齐交叉档后：
                edge_text 残留  (d0,f5) 12.72/12.64  vs  (d0,f0) 9.09/8.48
                               (d2,f5)  9.95/ 8.44
            即 f=0 时不膨胀就已经和旧默认持平；真正的缺陷是高斯羽化把 1~2px 笔画的
            融合权重压到约 0.3（峰值 0.32×255≈82 < 阈值）。阶段 105 已修
            （`_prepare_mask` 第 4 步 `max(soft, binary)`，羽化只在外侧产生软坡），
            修后 (d0,f5) 残留 9.10/8.48、过度像素 1498/1515、外溢 0.7%
            ⇒ 默认膨胀随之归零（`MASK_DILATE_DEFAULT = 0`），在残留、破坏面积、
            外溢三项上全面优于旧的 (d2,f5)。
            本脚本的 `run()` 已改为**全交叉扫描**，防止同类交互项再次藏进死角。

 用 法 : python scripts/mask_param_audit.py            # 全交叉扫描 + 红线判定
          python scripts/mask_param_audit.py --quick    # 只扫默认羽化档，跳过其他羽化档
 版 本 号 : 1.1.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cv2
import numpy as np

import config
from core.detector import detect_watermark_mask
from core.inpainter import inpaint
from scripts.detector_diag import BASE_KINDS, gt_mask
from scripts.make_demo_assets import CASES

#: 膨胀扫描档位（默认值必须落在"低破坏"区间内）。
DILATES = (0, 2, 4, 8, 16)
FEATHERS = (0, 5, 15)
CASES_AUDITED = ("corner_logo", "edge_text", "grid")
KINDS_AUDITED = ("textured", "photo")

#: 红线：任一用例的「默认配置改动像素 / 裸掩码(0,0)改动像素」上限（grid 是敏感放大镜：
#: 旧默认(2,5) 时 photo/grid ≈4.0×，dilate=8 时 ≈21.6×；阶段 105 新默认实测 ≈1.00×）。
#: 对照组刻意取**裸掩码 (dilate=0, feather=0)** 而不是「dilate=0 的默认羽化」：
#: 后者在默认膨胀归零后会与默认配置是同一行，比值恒为 1.0，红线退化成哑巴。
MAX_DEFAULT_OVERPAINT_RATIO = 5.0
#: 红线：**跨用例平均残留比**（默认配置 vs 裸掩码 (0,0)）上限。
#: 实测平均残留比（阶段 105 全交叉扫描）：
#:   · 旧默认 (2,5) → 1.032（textured/edge_text 单项高达 1.412：羽化冲淡细笔画）
#:   · **新默认 (0,5) → 1.000**（各用例残留与裸掩码逐项相同，只差 0.01 的量化噪声）
#: 阈值取 1.02 而不是 1.00：这条红线要抓的失效模式是「羽化又在冲淡内部」，其幅度 ≥1.35×，
#: 1.02 只是噪声地板而非放宽的守卫（1e-6 会让 0.01 的量化抖动直接把门禁点红 —— 恒红 = 哑巴）。
#: 用"平均值"而非"逐例"是因为各用例的响应方向不同，单例会互相抵消；
#: 但整体不得比「什么都不做」更差。
MAX_MEAN_RESIDUAL_RATIO = 1.02

#: 检测结果缓存：掩码与 dilate/feather 无关，全交叉扫描下重复检测纯属浪费。
#: key = (kind, case, seed)。**若测试里 monkeypatch 了 BASE_KINDS / CASES，须先
#: 调用 `_clear_mask_cache()`**，否则会命中旧掩码。
_MASK_CACHE: "dict[tuple, tuple]" = {}


def _clear_mask_cache() -> None:
    """清空掩码缓存（monkeypatch 合成器/用例前后使用）。"""
    _MASK_CACHE.clear()


def _mask(img, preset):
    m = detect_watermark_mask(img, preset=preset, use_ocr=False).mask
    return m[:, :, 0] if m.ndim == 3 else m


def _cached_scene(kind, case, seed=7):
    """返回 (base, marked, mask)，同一组合只生成/检测一次。"""
    key = (kind, case, seed)
    hit = _MASK_CACHE.get(key)
    if hit is None:
        base = BASE_KINDS[kind](seed, w=640, h=640)
        marked = CASES[case][0](base)
        hit = (base, marked, np.array(_mask(marked, CASES[case][1]), copy=True))
        _MASK_CACHE[key] = hit
    return hit


def _ring(gt, grow=12):
    k = np.ones((2 * grow + 1, 2 * grow + 1), np.uint8)
    return (cv2.dilate(gt.astype(np.uint8), k) > 0) & (~gt)


def analyse(kind, case, dilate, feather, seed=7):
    """返回 {residual, overpaint, changed_px, gt_cov}。"""
    base, marked, mask = _cached_scene(kind, case, seed)
    out = inpaint(marked, mask, engine="opencv", dilate=dilate, feather=feather)
    gt = gt_mask(base, marked) > 0      # 0/255 → 布尔（否则 diff[gt] 会变成花式索引）
    diff = np.abs(out.astype(np.int16) - base.astype(np.int16)).max(axis=2).astype(np.float32)
    band = _ring(gt)
    return {
        "residual": round(float(diff[gt].mean()), 3),
        "overpaint": round(float(diff[band].mean()), 3),
        "changed_px": int(np.count_nonzero(diff[band] > 2)),
        "gt_cov": round(float(gt.mean() * 100), 3),
    }


def run(dilates=DILATES, feathers=FEATHERS,
        kinds=KINDS_AUDITED, cases=CASES_AUDITED, seed=7) -> dict:
    """全交叉扫描（dilates × feathers）。

    阶段 65 的旧实现只在 dilate 维变档、feather 仅在 `d == 默认值` 时才换挡，
    漏掉了 (0,0) 这类角，导致把羽化的代价误记为膨胀的收益（见模块文档）。此处改为
    完整笛卡尔积：档位少、成本可控，但交互项无处可藏。
    """
    rows = []
    for kind in kinds:
        for case in cases:
            for d in dilates:
                for fe in feathers:
                    r = analyse(kind, case, d, fe, seed=seed)
                    rows.append({"kind": kind, "case": case, "dilate": d,
                                 "feather": fe, **r})
    return {"config": {"seed": seed, "default_dilate": config.MASK_DILATE_DEFAULT,
                       "default_feather": config.MASK_FEATHER_DEFAULT,
                       "max_overpaint_ratio": MAX_DEFAULT_OVERPAINT_RATIO},
            "rows": rows}


def evaluate(report: dict) -> "tuple[bool, list[str]]":
    """红线判定：① 单例过度重绘 ≤ 上限；② 跨用例平均残留不得劣于裸掩码 (0,0)。

    对照组 = **(dilate=0, feather=0)**，即"掩码原样送修、不做任何后处理"。这个基准
    不随默认值漂移，因此默认值改动后红线依然有判别力（若拿"默认 vs dilate=0"当基准，
    默认膨胀一旦归零，两条记录就是同一行，红线会静默退化为恒等）。
    """
    problems: list[str] = []
    rows = report["rows"]
    d_def = config.MASK_DILATE_DEFAULT
    f_def = config.MASK_FEATHER_DEFAULT
    ratios: list[float] = []

    def _find(kind, case, d, fe):
        return next((r for r in rows if r["kind"] == kind and r["case"] == case
                     and r["dilate"] == d and r["feather"] == fe), None)

    for kind in KINDS_AUDITED:
        for case in CASES_AUDITED:
            base_row = _find(kind, case, 0, 0)
            def_row = _find(kind, case, d_def, f_def)
            # 缺行 = 报告不完整 ⇒ 必须报错，不能静默跳过（静默跳过 = 哑巴门禁，阶段 95 教训）。
            if base_row is None:
                problems.append(
                    f"[对照组缺失] {kind}/{case} 报告里没有裸掩码 (dilate=0, feather=0) 一行 —— "
                    f"没有对照组就没有判定依据，请用完整交叉扫描重跑")
                continue
            if def_row is None:
                problems.append(
                    f"[默认组缺失] {kind}/{case} 报告里没有默认配置 "
                    f"(d={d_def}, f={f_def}) 一行")
                continue
            ratio = def_row["changed_px"] / max(1, base_row["changed_px"])
            if ratio > MAX_DEFAULT_OVERPAINT_RATIO:
                problems.append(
                    f"[过度重绘红线] {kind}/{case} 默认配置(d={d_def},f={f_def}) 改动像素是"
                    f"裸掩码(0,0)的 {ratio:.1f} 倍 > {MAX_DEFAULT_OVERPAINT_RATIO:.1f} 倍")
            if base_row["residual"] > 1e-6:
                ratios.append(def_row["residual"] / base_row["residual"])
    if ratios:
        mean_ratio = sum(ratios) / len(ratios)
        if mean_ratio > MAX_MEAN_RESIDUAL_RATIO + 1e-6:
            problems.append(
                f"[残留红线] 默认配置(d={d_def},f={f_def}) 跨用例平均残留比 {mean_ratio:.3f} > "
                f"{MAX_MEAN_RESIDUAL_RATIO:.3f}（整体不如裸掩码 (0,0)，多半是羽化又在冲淡内部）")
    return (len(problems) == 0), problems


def to_markdown(report: dict) -> str:
    lines = ["| 底图 | 用例 | dilate | feather | 残留 | 过度均值 | 过度像素 |",
             "|---|---|---|---|---|---|---|"]
    for r in report["rows"]:
        lines.append(f"| {r['kind']} | {r['case']} | {r['dilate']} | {r['feather']} | "
                     f"{r['residual']:.2f} | {r['overpaint']:.2f} | {r['changed_px']} |")
    return "\n".join(lines)


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="掩码膨胀/羽化真值标定审计（阶段 65 立，阶段 105 全交叉修正）")
    ap.add_argument("--quick", action="store_true",
                    help="只扫「默认羽化 + 0」两档羽化，跳过 15 档（快速迭代用）")
    args = ap.parse_args()
    feathers = (0, config.MASK_FEATHER_DEFAULT) if args.quick else FEATHERS
    report = run(feathers=feathers)
    print(to_markdown(report))
    ok, problems = evaluate(report)
    if ok:
        print(f"\n✅ 掩码参数标定审计通过（默认 dilate={config.MASK_DILATE_DEFAULT}、"
              f"feather={config.MASK_FEATHER_DEFAULT}，破坏/残留均在红线内）")
        return 0
    print("\n❌ 掩码参数标定审计未通过：")
    for p in problems:
        print(f"  · {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
