#!/usr/bin/env python3
# ==============================================================================
# 干净图误报探针 · clean_bench_probe
#
# 阶段 180：闭环 179 的「干净图精度留白」。
#
# 背景：179 实现 `plate_footprint_prior`（信号锚定的右下角牌体补全，杠杆
#   `DETECT_PLATE_COMPLETE_FROM_SIGNAL`），把 4 张低对比底 mask_recall 从
#   0.03~0.39 抬到 0.62~0.80。但该先验是**豆包专属**的，且当前基准 10 张全为
#   带水印豆包图、**无干净图**——故「干净图上误发整牌」的误报率**不可测量**，
#   杠杆被迫默认 OFF（宁可漏检不可滥杀）。
#
# 本探针把「不可测量」变成「可证伪门禁」：
#   1. 从 benchmark 中挑出干净图（manifest `"clean": true` 且真值 `watermarks: []`）；
#   2. 对每张跑检测器（杠杆 ON / OFF 两遍），量取**误发 corner_logo 区域数**与
#      牌足迹区（右下角 340×128 @2048²）的掩码覆盖率；
#   3. 汇总 false_plate_rate（出现≥1 个误发 corner_logo 的干净图占比），
#      与阈值比较，给出门禁判定。
#
# 退出码（门禁家族契约 0/1/2）：
#   0 = 通过（ON 误报率 ≤ --max-fp-rate）
#   1 = 未通过（超过阈值）
#   2 = 跳过（无干净图，数据集未就位）——**独立于 0**，不污染其它门禁。
#
# ⚠ 与 real_gate 的关系：real_gate 的红线与面积监控都**依赖真实水印真值**，
#   干净图（watermarks:[]）会让其指标退化/污染面积监控，故干净图**单独成集**
#   （默认 `benchmark_real/clean/`），不进 real_gate 主清单。本探针是独立门禁。
# ==============================================================================
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402
from core.detector import (  # noqa: E402
    KIND_CORNER_LOGO,
    detect_watermark_mask,
)


def _plate_footprint(h: int, w: int):
    """复刻 179 先验的规范化足迹（阶段 179 docs）。"""
    fw = max(8, int(round(340 * min(h, w) / 2048.0)))
    fh = max(8, int(round(128 * min(h, w) / 2048.0)))
    return fw, fh, w - fw, h - fh  # bx, by


def _spurious_metrics(arr: np.ndarray, lever_on: bool) -> dict:
    """对单张图跑检测器，返回误报相关指标。"""
    saved = config.DETECT_PLATE_COMPLETE_FROM_SIGNAL
    config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = lever_on
    try:
        res = detect_watermark_mask(arr, preset="general", use_ocr=False)
    finally:
        config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = saved

    h, w = arr.shape[:2]
    corner_logo = [r for r in res.regions if r.kind == KIND_CORNER_LOGO]
    mask = res.mask
    if mask is None or mask.size == 0:
        coverage = 0.0
    else:
        coverage = float(np.count_nonzero(mask) / mask.size)

    fw, fh, bx, by = _plate_footprint(h, w)
    plate_region = mask[by:by + fh, bx:bx + fw] if mask is not None else None
    plate_cov = (float(np.count_nonzero(plate_region) / plate_region.size)
                 if plate_region is not None and plate_region.size else 0.0)

    return {
        "n_corner_logo": len(corner_logo),
        "mask_coverage": coverage,
        "plate_footprint_coverage": plate_cov,
    }


def _warmup() -> None:
    """阶段 181：检测器冷启动伪影加固。

    首次推理（刚 import torch/detector 后）因 CV 线程池 / 算子 warming-up 可能与其他次
    推理差之毫厘，导致「是否误发 corner_logo」在边界样本上翻转。干净图误报门禁若在
    进程内第一次检测就量，可能踩到这个冷启动离群点 → 门禁漂绿/漂红。

    对策：在正式测量前做一次 1×1 废图推理，把检测器烧热，保证后续测量落在稳态。
    """
    saved = config.DETECT_PLATE_COMPLETE_FROM_SIGNAL
    config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = False
    try:
        detect_watermark_mask(np.zeros((1, 1, 3), dtype=np.uint8),
                              preset="general", use_ocr=False)
    except Exception:
        pass
    finally:
        config.DETECT_PLATE_COMPLETE_FROM_SIGNAL = saved


def run_clean_probe(root_dir: str, *, max_fp_rate: float,
                    gate_on: str = "prior", preset: str = "general",
                    warmup: bool = True
                    ) -> "tuple[int, list[str], dict]":
    """对干净图集跑误报探针（每张同时测 ON/OFF），返回 (exit_code, 打印行, 报告)。

    `root_dir` 下须有 `manifest.json` + `ground_truth.json`（与 benchmark_real 同构）。
    干净图 = manifest 中 `"clean": true` 且真值 `watermarks` 为空列表的条目。

    同时测 ON/OFF 是为了**隔离 179 先验的归因误报**：
      · baseline_fp_rate = 误发 corner_logo 的图占比（杠杆 OFF，基线通路本身）
      · prior_fp_rate    = (ON 误发 − OFF 误发) > 0 的图占比（**179 先验新增**的误报）
      · 179 先验由 `if 已有 corner_logo: 跳过` 守卫，故正常情况下 prior_fp_rate = 0。
    `gate_on="prior"`（默认）只对 179 归因误报判红线；`gate_on="total"` 对总误报率判。
    """
    man_path = os.path.join(root_dir, "manifest.json")
    truth_path = os.path.join(root_dir, "ground_truth.json")
    if not (os.path.isfile(man_path) and os.path.isfile(truth_path)):
        return 2, [f"[跳过] 缺失清单/真值：{man_path} 或 {truth_path}"], {}

    manifest = json.load(open(man_path, encoding="utf-8"))
    truth = json.load(open(truth_path, encoding="utf-8"))
    tby = {e["image_id"]: e for e in truth.get("entries", [])}

    clean = []
    for im in manifest.get("images", []):
        if not im.get("clean"):
            continue
        e = tby.get(im["id"])
        if e is None:
            continue
        if e.get("watermarks"):  # 有真值水印 → 非干净图，跳过
            continue
        clean.append(im)

    if not clean:
        return 2, [f"[跳过] 干净图集为空（root={root_dir}，无 clean:true 且 watermarks:[] 的条目）"], {}

    if warmup:
        _warmup()

    rows = []
    baseline_fp = prior_fp = total_on_fp = 0
    for im in clean:
        p = os.path.join(root_dir, im["path"])
        if not os.path.isfile(p):
            rows.append((im["id"], "MISSING", 0, 0, 0.0, 0.0))
            continue
        arr = np.asarray(Image.open(p).convert("RGB"))
        m_on = _spurious_metrics(arr, lever_on=True)
        m_off = _spurious_metrics(arr, lever_on=False)
        n_off = m_off["n_corner_logo"]
        n_on = m_on["n_corner_logo"]
        baseline_fp += 1 if n_off > 0 else 0
        prior_fp += 1 if (n_on - n_off) > 0 else 0
        total_on_fp += 1 if n_on > 0 else 0
        rows.append((
            im["id"], n_on, n_off,
            m_on["mask_coverage"], m_on["plate_footprint_coverage"],
        ))

    n = len(rows)
    baseline_rate = baseline_fp / n if n else 0.0
    prior_rate = prior_fp / n if n else 0.0
    total_rate = total_on_fp / n if n else 0.0
    gated_rate = prior_rate if gate_on == "prior" else total_rate
    passed = gated_rate <= max_fp_rate
    exit_code = 0 if passed else 1

    lines = []
    lines.append(f"干净图误报探针 · root={os.path.basename(root_dir)} · n={n} · gate_on={gate_on}")
    lines.append("")
    lines.append(f"{'image_id':<32} {'ON误发':<7} {'OFF误发':<7} {'掩码覆盖率':<10} {'牌足迹覆盖率':<12}")
    for r in rows:
        lines.append(f"{r[0]:<32} {r[1]:<7} {r[2]:<7} {r[3]:<10.4f} {r[4]:<12.4f}")
    lines.append("")
    lines.append(f"基线误报率(baseline, OFF)      = {baseline_fp}/{n} = {baseline_rate:.4f}")
    lines.append(f"179 归因误报率(prior, ON−OFF)  = {prior_fp}/{n} = {prior_rate:.4f}  ← 179 专属风险")
    lines.append(f"总误报率(total, ON)           = {total_on_fp}/{n} = {total_rate:.4f}")
    lines.append(f"门禁口径 = {gate_on} → {gated_rate:.4f}  阈值 = {max_fp_rate:.4f}  → {'✅ 通过' if passed else '❌ 未通过'}")
    report = {
        "root": root_dir, "n": n,
        "baseline_fp_rate": baseline_rate, "prior_fp_rate": prior_rate,
        "total_on_fp_rate": total_rate, "gate_on": gate_on,
        "max_fp_rate": max_fp_rate, "passed": passed,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return exit_code, lines, report


def main() -> int:
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="干净图误报探针（补全先验门禁）")
    ap.add_argument("--root", default=os.path.join(_ROOT, "benchmark_real", "clean"),
                    help="干净图集目录（含 manifest.json + ground_truth.json）")
    ap.add_argument("--max-fp-rate", type=float, default=0.0,
                    help="允许的最大误报率（默认 0.0：干净图上任何误发整牌即失败）")
    ap.add_argument("--gate-on", choices=["prior", "total"], default="prior",
                    help="门禁口径：prior=只对 179 归因误报判（默认）/ total=对总误报率判")
    ap.add_argument("--json", default=None, help="把结构化报告写到此路径")
    args = ap.parse_args()

    code, lines, report = run_clean_probe(
        args.root, max_fp_rate=args.max_fp_rate, gate_on=args.gate_on)
    print("\n".join(lines))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
