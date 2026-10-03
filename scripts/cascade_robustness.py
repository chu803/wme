#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/cascade_robustness.py
 文件说明 : 多次转码级联的检测鲁棒性审计（阶段 72）。

            真实平台会对图片**反复**再压缩（上传 JPEG → 存储 WebP → 缩略图 JPEG…），
            阶段 63/67 只覆盖了「单轮」。本脚本模拟 N 轮「JPEG75 → WebP75 → JPEG75」
            级联（分辨率不变，隔离"次数"这一个变量），测量召回/精确率/IoU 与干净误报。

            **度量口径（沿用阶段 63/67）**：真值只由「未受扰动的一对」（无损 base vs
            marked）求支撑区；级联只施加于被测输入，绝不参与求真值。

            红线（越线 exit 1）：
              ① 黑屏红线：任一「有真值」用例召回为 0（静默漏检）；
              ② 召回下限：corner ≥90% / edge ≥90% / grid ≥55%（按实测最小值留余量）；
              ③ 自动模式误报红线：**general 预设**干净误报全轮次 ≤2% —— 这是"自动模式"
                 的实际契约（重压缩图上推荐器只会给出 general，激进预设的上涨不进入该口径）；
              ④ 四预设平均误报：n ≤ 2 ≤ 2%（正常质量区间沿用阶段 57 红线）；
                 n ≥ 3 ≤ 5%（极端再压缩下的**已记录代价**，实测最大 4.76%）。

 用 法 : python scripts/cascade_robustness.py          # 全量（6 轮数 × 3 底图 × 4 用例）
          python scripts/cascade_robustness.py --quick  # 只跑 n=0/3/8 的 textured/photo
          python scripts/cascade_robustness.py --json out.json
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

#: 级联轮数（每轮 = JPEG75 → WebP75 → JPEG75 三次有损编码）。
ROUNDS = (0, 1, 2, 3, 5, 8)
#: JPEG / WebP 再编码质量（与阶段 63/67 的口径一致）。
JPEG_QUALITY = 75
WEBP_QUALITY = 75
#: 分用例召回下限（实测最小值再留余量）。
RECALL_FLOORS = {"corner_logo": 0.90, "edge_text": 0.90, "grid": 0.55}
#: 黑屏红线豁免用例（信息论极限，阶段 49 已记录）。
BLACKOUT_EXEMPT_CASES = {"faint_tiled"}
#: 干净图检测预设（与门禁一致）。
CLEAN_PRESETS = ("general", "ai_faint_tiled", "ai_grid", "ai_logo")
#: 自动模式误报红线：general 预设全轮次 ≤2%。
GENERAL_FPR_MAX = 0.02
#: 四预设平均误报：正常质量区间（n≤2）沿用阶段 57 红线。
AVG_FPR_MAX_NORMAL = 0.02
#: 四预设平均误报：极端再压缩区间（n≥3）的已记录代价上限。
AVG_FPR_MAX_DEGRADED = 0.05
#: 正常质量区间的最大轮数（超过即按"退化区间"红线判定）。
NORMAL_ROUNDS_MAX = 2
#: 缩放维度（阶段 73 新增，`--with-scale` 启用）：1.0 = 原尺寸（既有口径）；
#: 0.75/0.5 = 平台先下采样再反复转码的真实链路。scale < 1 的行按阶段 67 的
#: 「低分辨率」既有结论归档：只保留黑屏红线（且仅 0.75——0.5 属阶段 67 已记录的
#: opt-in 兜底区间），召回/误报为记录性数据。
SCALES = (1.0, 0.75, 0.5)


def rescale(img: np.ndarray, factor: float) -> np.ndarray:
    """按比例缩放图像（<1 用 INTER_AREA 抗混叠，≥1 原样）。"""
    if factor == 1.0:
        return img
    h, w = img.shape[:2]
    return cv2.resize(img, (max(1, int(w * factor)), max(1, int(h * factor))),
                      interpolation=cv2.INTER_AREA if factor < 1 else cv2.INTER_CUBIC)


def _encode_jpeg(img: np.ndarray, q: int = JPEG_QUALITY) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                           [int(cv2.IMWRITE_JPEG_QUALITY), q])
    assert ok
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def _encode_webp(img: np.ndarray, q: int = WEBP_QUALITY) -> np.ndarray:
    ok, buf = cv2.imencode(".webp", cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                           [int(cv2.IMWRITE_WEBP_QUALITY), q])
    assert ok
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def cascade(img: np.ndarray, rounds: int) -> np.ndarray:
    """施加 rounds 轮「JPEG → WebP → JPEG」有损再编码（分辨率不变）。"""
    for _ in range(rounds):
        img = _encode_jpeg(_encode_webp(_encode_jpeg(img)))
    return img


def _det(img: np.ndarray, preset: str) -> np.ndarray:
    m = detect_watermark_mask(img, preset=preset, use_ocr=False).mask
    return m[:, :, 0] if m.ndim == 3 else m


def collect(kinds=BASE_KINDS, rounds=ROUNDS, scales=SCALES) -> dict:
    rows: list[dict] = []
    for kind in kinds:
        base = BASE_KINDS[kind](7, w=640, h=640)
        for case, (wm_fn, preset, _) in CASES.items():
            marked640 = wm_fn(base)
            gt640 = gt_mask(base, marked640)                # 无损配对求真值
            for scale in scales:
                marked = rescale(marked640, scale)
                # 真值掩码随图像同步缩放（最近邻保持 0/255）。
                gt = (cv2.resize(gt640, (marked.shape[1], marked.shape[0]),
                                 interpolation=cv2.INTER_NEAREST)
                      if scale != 1.0 else gt640)
                rec = {"kind": kind, "case": case, "scale": scale,
                       "gt_px": int(np.count_nonzero(gt > 0)), "rounds": {}}
                for n in rounds:
                    mm = metrics(_det(cascade(marked, n), preset), gt)
                    rec["rounds"][n] = {"recall": mm["recall"],
                                        "precision": mm["precision"],
                                        "iou": mm["iou"]}
                rows.append(rec)
        clean640 = base
        for scale in scales:
            clean = rescale(clean640, scale)
            blank = np.zeros(clean.shape[:2], np.uint8)
            crec = {"kind": kind, "case": "(clean)", "scale": scale, "rounds": {}}
            for n in rounds:
                img = cascade(clean, n)
                per = {p: metrics(_det(img, p), blank)["fpr"] for p in CLEAN_PRESETS}
                crec["rounds"][n] = {
                    "avg": float(np.mean(list(per.values()))),
                    "general": per["general"],
                    "per_preset": per,
                }
            rows.append(crec)
    return {"config": {"rounds": list(rounds), "scales": list(scales),
                       "jpeg_quality": JPEG_QUALITY,
                       "webp_quality": WEBP_QUALITY,
                       "recall_floors": RECALL_FLOORS}, "rows": rows}


def evaluate(report: dict) -> tuple[bool, list[str]]:
    """红线判定（纯函数，便于单测）。

    scale == 1.0 的行套用阶段 72 的四条红线；scale < 1 的行（阶段 73 新增的
    「先下采样再转码」组合）按阶段 67 的低分辨率既有结论归档：只保留黑屏红线
    （且仅 scale=0.75——0.5 属阶段 67 已记录的 opt-in 兜底区间），召回/误报为
    记录性数据。
    """
    problems: list[str] = []
    for r in report["rows"]:
        scale = float(r.get("scale", 1.0))
        where = f"{r['kind']}/{r['case']}" + ("" if scale == 1.0 else f"@x{scale}")
        if r["case"] == "(clean)":
            if scale != 1.0:
                continue        # 低分辨率误报属阶段 67 记录性数据
            for n, d in r["rounds"].items():
                if d["general"] > GENERAL_FPR_MAX:
                    problems.append(
                        f"[自动模式误报红线] {where} n={n} general 干净误报 "
                        f"{d['general']*100:.2f}% > {GENERAL_FPR_MAX*100:.0f}%")
                limit = (AVG_FPR_MAX_NORMAL if n <= NORMAL_ROUNDS_MAX
                         else AVG_FPR_MAX_DEGRADED)
                if d["avg"] > limit:
                    tag = "正常区间" if n <= NORMAL_ROUNDS_MAX else "退化区间"
                    problems.append(
                        f"[{tag}误报红线] {where} n={n} 四预设平均误报 "
                        f"{d['avg']*100:.2f}% > {limit*100:.0f}%")
            continue
        floor = RECALL_FLOORS.get(r["case"])
        for n, d in r["rounds"].items():
            if (r["gt_px"] > 0 and d["recall"] <= 0.0
                    and r["case"] not in BLACKOUT_EXEMPT_CASES
                    and (scale == 1.0 or scale >= 0.75)):
                problems.append(
                    f"[黑屏红线] {where} n={n} 召回为 0（静默漏检，真值 {r['gt_px']}px）")
            # 无真值（gt_px=0）的单元无召回可言（召回恒 0），不得按召回下限判定。
            if scale != 1.0:
                continue        # 低分辨率召回为记录性数据（阶段 67 归档）
            if floor is None or r["gt_px"] <= 0 or r["case"] in BLACKOUT_EXEMPT_CASES:
                continue
            if d["recall"] < floor:
                problems.append(
                    f"[召回红线] {where} n={n} 召回 {d['recall']*100:.1f}% < "
                    f"{floor*100:.0f}%")
    return (len(problems) == 0), problems


def to_markdown(report: dict) -> str:
    rounds = report.get("config", {}).get("rounds", ROUNDS)
    lines = ["| 底图/用例 | " + " | ".join(f"n={n}" for n in rounds) + " |",
             "|---|" + "---|" * len(rounds)]
    for r in report["rows"]:
        label = r["kind"] + "/" + r["case"] + (
            f"@x{r['scale']}" if float(r.get("scale", 1.0)) != 1.0 else "")
        if r["case"] == "(clean)":
            cells = [f"均 {d['avg']*100:.2f}% / general {d['general']*100:.2f}%"
                     for d in r["rounds"].values()]
        else:
            cells = [f"{d['recall']*100:.0f}/{d['precision']*100:.0f}"
                     for d in r["rounds"].values()]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="多次转码级联的检测鲁棒性审计")
    ap.add_argument("--quick", action="store_true",
                    help="只跑 n=0/3/8 与 textured/photo（快速迭代）")
    ap.add_argument("--with-scale", action="store_true",
                    help="附加「先下采样再转码」维度（x0.75 / x0.5，阶段 73）")
    ap.add_argument("--json", default=None, help="把结果写入 JSON 文件")
    args = ap.parse_args()

    rounds = (0, 3, 8) if args.quick else ROUNDS
    scales = SCALES if args.with_scale else (1.0,)
    report = collect(rounds=rounds, scales=scales)

    print(to_markdown(report))
    ok, problems = evaluate(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({**report, "ok": ok, "problems": problems}, fh,
                      ensure_ascii=False, indent=2)
    print()
    if ok:
        print("✅ 级联鲁棒性审计通过（无黑屏、召回与误报均在红线内）")
        return 0
    print("❌ 级联鲁棒性审计未通过：")
    for pr in problems:
        print(f"  - {pr}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
