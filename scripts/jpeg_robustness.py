#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/jpeg_robustness.py
 文件说明 : 有损压缩（JPEG）鲁棒性审计（阶段 63）。

            真实输入几乎必经 JPEG（截图 / 社交平台再压缩），而 benchmark 全部使用
            无损合成图。本脚本在 JPEG 质量 90 / 75 / 60 下重编码带水印图并测量检测
            召回 / 精确率 / IoU 与干净图误报，回答「有损压缩会不会毁掉检测」。

            **关键度量口径（阶段 63 踩过的坑）**：真值必须取「无损配对」的水印支撑区
            （|wm(base) − base| > 0，用未压缩图算）——若改用「压缩后配对」求差，两图
            各自的 JPEG 噪声互相独立，会把真值膨胀到近全图，召回被稀释成假性崩塌
            （实测 grid 召回「12%」其实是 GT 从 1.2% 膨胀到 ~90% 的假象）。

 用 法 : python scripts/jpeg_robustness.py            # 全量（3 底图 × 4 用例 × 4 质量）
          python scripts/jpeg_robustness.py --quick    # 只跑 JPEG75
          python scripts/jpeg_robustness.py --json out.json
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cv2
import numpy as np

from core.detector import detect_watermark_mask
from scripts.detector_diag import BASE_KINDS, gt_mask, metrics
from scripts.make_demo_assets import CASES

CLEAN_PRESETS = ("general", "ai_faint_tiled", "ai_grid", "ai_logo")
QUALITIES = (None, 90, 75, 60)

#: 召回红线（阶段 63 实测值的保守下限，留 ~8~10pp 余量防抖动）：
#: 键 = (用例, 底图类)；值为该组合在 JPEG90/75/60 三档下都必须达到的最低召回。
RECALL_FLOORS = {
    ("corner_logo", "gradient"): 0.95, ("corner_logo", "textured"): 0.95,
    ("corner_logo", "photo"): 0.95,
    ("edge_text", "gradient"): 0.95, ("edge_text", "textured"): 0.90,
    ("edge_text", "photo"): 0.90,
    ("grid", "gradient"): 0.95, ("grid", "textured"): 0.70, ("grid", "photo"): 0.90,
}
#: 干净图误报红线（JPEG 下不得反弹）。
CLEAN_FPR_MAX = 0.02


def jpeg_roundtrip(img: np.ndarray, quality) -> np.ndarray:
    """JPEG 编解码往返（RGB in / RGB out）；quality=None 表示不压缩。"""
    if quality is None:
        return img
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                           [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        return img
    dec = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return cv2.cvtColor(dec, cv2.COLOR_BGR2RGB)


def _det_mask(img: np.ndarray, preset: str) -> np.ndarray:
    m = detect_watermark_mask(img, preset=preset, use_ocr=False).mask
    return m[:, :, 0] if m.ndim == 3 else m


def run(qualities=QUALITIES, kinds=tuple(BASE_KINDS), seed: int = 7) -> dict:
    """跑审计，返回 {config, rows}；row 含 quality/kind/case/recall/precision/iou。"""
    rows = []
    for q in qualities:
        tag = "PNG" if q is None else f"JPEG{q}"
        for kind in kinds:
            base_raw = BASE_KINDS[kind](seed, w=640, h=640)
            base = jpeg_roundtrip(base_raw, q)
            for case, (wm_fn, preset, _) in CASES.items():
                # 真值：无损配对求水印支撑区（与压缩条件解耦）。
                gt = gt_mask(base_raw, wm_fn(base_raw))
                marked = jpeg_roundtrip(wm_fn(base_raw), q)
                mm = metrics(_det_mask(marked, preset), gt)
                rows.append({
                    "quality": tag, "kind": kind, "case": case,
                    "recall": round(float(mm["recall"]), 4),
                    "precision": round(float(mm["precision"]), 4),
                    "iou": round(float(mm["iou"]), 4),
                })
            fprs = []
            for preset in CLEAN_PRESETS:
                m = _det_mask(base, preset)
                fprs.append(float(np.count_nonzero(m > 0)) / m.size)
            rows.append({
                "quality": tag, "kind": kind, "case": "(clean_fpr)",
                "recall": None, "precision": None,
                "iou": round(float(np.mean(fprs)), 4),
            })
    return {"config": {"seed": seed, "qualities": ["PNG" if q is None else q
                                                   for q in qualities],
                       "recall_floors": {f"{c}/{k}": v
                                         for (c, k), v in RECALL_FLOORS.items()},
                       "clean_fpr_max": CLEAN_FPR_MAX},
            "rows": rows}


def evaluate(report: dict) -> "tuple[bool, list[str]]":
    """红线判定（纯函数）：带水印召回达标 + 干净误报不反弹。"""
    problems: list[str] = []
    for r in report["rows"]:
        if r["case"] == "(clean_fpr)":
            if r["iou"] is not None and r["iou"] > CLEAN_FPR_MAX:
                problems.append(
                    f"[误报红线] {r['quality']} {r['kind']} 干净误报 "
                    f"{r['iou'] * 100:.2f}% > {CLEAN_FPR_MAX * 100:.0f}%")
            continue
        floor = RECALL_FLOORS.get((r["case"], r["kind"]))
        if floor is not None and r["recall"] < floor:
            problems.append(
                f"[召回红线] {r['quality']} {r['kind']}/{r['case']} 召回 "
                f"{r['recall'] * 100:.1f}% < 要求 {floor * 100:.0f}%")
    return (len(problems) == 0), problems


def to_markdown(report: dict) -> str:
    lines = ["| 质量 | 底图 | 用例 | 召回% | 精确率% | IoU% |", "|---|---|---|---|---|---|"]
    for r in report["rows"]:
        rc = "-" if r["recall"] is None else f"{r['recall'] * 100:.1f}"
        pr = "-" if r["precision"] is None else f"{r['precision'] * 100:.1f}"
        lines.append(f"| {r['quality']} | {r['kind']} | {r['case']} | {rc} | {pr} | "
                     f"{r['iou'] * 100:.1f} |")
    return "\n".join(lines)


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="JPEG 有损压缩鲁棒性审计（阶段 63）")
    ap.add_argument("--quick", action="store_true", help="只跑 JPEG75")
    ap.add_argument("--json", default=None, help="把结果写入 JSON 路径")
    args = ap.parse_args()

    qualities = (75,) if args.quick else QUALITIES
    report = run(qualities=qualities)
    print(to_markdown(report))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.json}")
    ok, problems = evaluate(report)
    if ok:
        print("\n✅ JPEG 鲁棒性审计通过（召回/误报均在红线内）")
        return 0
    print("\n❌ JPEG 鲁棒性审计未通过：")
    for p in problems:
        print(f"  · {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
