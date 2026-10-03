#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/scale_robustness.py
 文件说明 : 缩放 × 二次转码级联的检测鲁棒性审计（阶段 67）。

            真实链路上图片几乎必经「平台缩放 + 有损再编码」（缩略图、聊天工具、
            社交平台），而 benchmark / 门禁全部使用 640² 无损合成图。本脚本模拟该
            级联：640 原生带水印图 → 缩放到 S → 编码（PNG / JPEG75 / WebP75），
            测量检测召回 / 精确率 / IoU 与干净图误报。

            **关键度量口径（阶段 63 教训）**：真值（水印支撑区）只由「未受扰动的
            一对」（base vs marked，原生分辨率）求得；缩放同步施加到真值掩码上
            （最近邻）；有损编码**只施加在被测输入**，绝不参与求真值。

            **双口径**（阶段 67 的核心结论）：同一个检测器在大输入上「精度优先」，
            在小输入上会出现「完全空掩码」的静默漏检，而把小图规范化到标定尺度虽能
            消除漏检，却会把原生小图的高频噪点一起放大（误报 0.80% → 7.98%）。
            两条口径由不同的红线分别守护：
              ① 默认路径（兜底关闭）精度红线：干净图四预设平均误报 ≤ 2%
                 —— 这是阶段 57 锁定的小图保证，绝不允许回退；
              ② 放行路径（兜底开启）黑屏红线：任一「有真值」用例召回不得为 0
                 —— 这是本阶段要修的静默漏检，只要开启就必须修好；
              ③ 放行路径召回红线：分用例下限（已记录的边界组合除外）。
            放行路径的误报代价不设红线、只如实打印（实测 ≤ 2.22%），供使用者权衡。

 用 法 : python scripts/scale_robustness.py            # 全量（5 尺度 × 3 编码 × 双口径）
          python scripts/scale_robustness.py --quick    # JPEG75 的 256/320/640（快速迭代）
          python scripts/scale_robustness.py --json out.json
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

#: 检测尺度（长边像素）。640 为标定锚点；小于锚点的输入在「放行路径」下会启用
#: 低分辨率兜底重检，大于锚点的输入用于观察「上采样 + 有损编码」的衰减。
SIZES = (256, 320, 448, 640, 960)
#: 编码：png（无损）/ jpeg75 / webp75。WebP 的「质量 75」在 OpenCV 下比 JPEG 75
#: 更激进，实测退化更明显——这是本脚本保留两种编码的原因。
CODECS = ("png", "jpeg75", "webp75")
#: 默认路径（兜底关闭）干净图误报红线（四预设平均）。
CLEAN_FPR_MAX = 0.02
#: 放行路径（兜底开启）分用例召回下限（数值 = 实测最小值再留余量）。
RECALL_FLOORS = {"corner_logo": 0.60, "edge_text": 0.80, "grid": 0.45}
#: 黑屏红线豁免用例：极淡平铺（α≈0.09）是**信息论极限**用例，召回本就只有 ~2%
#: （阶段 49 已记录），其归零不代表回归，故不参与黑屏判定。
BLACKOUT_EXEMPT_CASES = {"faint_tiled"}
#: 干净图检测使用的预设（与门禁一致）。
CLEAN_PRESETS = ("general", "ai_faint_tiled", "ai_grid", "ai_logo")
#: 已记录的边界组合（(kind, codec, case)）：WebP 的平滑衰减 + 重采样插值会削弱角标残差。
#: **当前为空集**——唯一的历史条目 `("photo", "webp75", "corner_logo")`（阶段 67 记录：
#: 重采样后 16~29% 部分漏检）已由**阶段 69** 的「固定阈值 ∪ 自适应阈值」并集修复，
#: 实测全尺度 100%。机制保留，以备将来记录新边界（须写明依据与复核方式）。
KNOWN_BOUNDARIES: set = set()


def _encode(img: np.ndarray, codec: str) -> np.ndarray:
    """按编码名重编码一轮（PNG 无损 / JPEG / WebP 有损）。"""
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    if codec == "png":
        ok, buf = cv2.imencode(".png", bgr)
    elif codec.startswith("jpeg"):
        ok, buf = cv2.imencode(
            ".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(codec[4:])]
        )
    elif codec.startswith("webp"):
        ok, buf = cv2.imencode(
            ".webp", bgr, [int(cv2.IMWRITE_WEBP_QUALITY), int(codec[4:])]
        )
    else:
        raise ValueError(f"未知编码：{codec}")
    if not ok:
        raise RuntimeError(f"编码失败：{codec}")
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def _rescale(img: np.ndarray, side: int) -> np.ndarray:
    """把图缩放到长边 = side（缩小用 INTER_AREA，放大用 INTER_CUBIC）。"""
    h, w = img.shape[:2]
    s = side / float(max(h, w))
    if abs(s - 1.0) < 1e-6:
        return img
    interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=interp)


def _rescale_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if mask.shape[:2] == shape:
        return mask
    return cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)


def _det(img: np.ndarray, preset: str, rescue: bool) -> np.ndarray:
    m = detect_watermark_mask(
        img, preset=preset, use_ocr=False, lowres_rescue=rescue
    ).mask
    return m[:, :, 0] if m.ndim == 3 else m


def collect() -> dict:
    """跑完整矩阵，返回 {config, rows}；每行同时记录两条口径的结果。"""
    rows: list[dict] = []
    for size in SIZES:
        for codec in CODECS:
            for kind in BASE_KINDS:
                base = BASE_KINDS[kind](7, w=640, h=640)
                for case, (wm_fn, preset, _) in CASES.items():
                    marked = wm_fn(base)
                    gt = gt_mask(base, marked)          # 无损配对求真值
                    img = _encode(_rescale(marked, size), codec)
                    g = _rescale_mask(gt, img.shape[:2])
                    rec = {"size": size, "codec": codec, "kind": kind, "case": case,
                           "gt_px": int(np.count_nonzero(g > 0))}
                    for arm, rescue in (("off", False), ("on", True)):
                        mm = metrics(_det(img, preset, rescue), g)
                        rec[arm] = {"recall": mm["recall"],
                                    "precision": mm["precision"],
                                    "iou": mm["iou"]}
                    rows.append(rec)
                clean = _encode(_rescale(base, size), codec)
                blank = np.zeros(clean.shape[:2], np.uint8)
                crec = {"size": size, "codec": codec, "kind": kind, "case": "(clean)"}
                for arm, rescue in (("off", False), ("on", True)):
                    per = {p: metrics(_det(clean, p, rescue), blank)["fpr"]
                           for p in CLEAN_PRESETS}
                    crec[arm] = {"fpr": float(np.mean(list(per.values()))),
                                 "per_preset": per}
                rows.append(crec)
    return {"config": {"sizes": list(SIZES), "codecs": list(CODECS),
                       "clean_fpr_max": CLEAN_FPR_MAX,
                       "recall_floors": RECALL_FLOORS}, "rows": rows}


def evaluate(report: dict) -> tuple[bool, list[str]]:
    """三条红线判定（纯函数，便于单测）。"""
    problems: list[str] = []
    for r in report["rows"]:
        where = f"{r['size']}²/{r['codec']}/{r['kind']}/{r['case']}"
        if r["case"] == "(clean)":
            if r["off"]["fpr"] > CLEAN_FPR_MAX:
                problems.append(
                    f"[默认路径精度红线] {where} 干净图四预设平均误报 "
                    f"{r['off']['fpr']*100:.2f}% > {CLEAN_FPR_MAX*100:.0f}%"
                )
            continue
        recall_on = r["on"]["recall"]
        floor = RECALL_FLOORS.get(r["case"])
        if (r["gt_px"] > 0 and recall_on <= 0.0
                and r["case"] not in BLACKOUT_EXEMPT_CASES):
            problems.append(
                f"[放行路径黑屏红线] {where} 召回为 0（静默漏检，真值 {r['gt_px']}px）"
            )
        # 无真值的单元无召回可言（真值 0 时召回恒 0），不得按召回下限判定。
        if floor is None or r["gt_px"] <= 0:
            continue
        if (r["kind"], r["codec"], r["case"]) in KNOWN_BOUNDARIES:
            continue
        if recall_on < floor:
            problems.append(
                f"[放行路径召回红线] {where} 召回 {recall_on*100:.1f}% < "
                f"{floor*100:.0f}%"
            )
    return (len(problems) == 0), problems


def to_markdown(report: dict) -> str:
    lines = ["| 尺度 | 编码 | 底图 | 用例 | 默认召回% | 放行召回% | 放行精确率% | 备注 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in report["rows"]:
        if r["case"] == "(clean)":
            lines.append(
                f"| {r['size']} | {r['codec']} | {r['kind']} | (clean) | "
                f"误报 {r['off']['fpr']*100:.2f}% | 误报 {r['on']['fpr']*100:.2f}% | — | — |")
        else:
            note = ("已记录边界"
                    if (r["kind"], r["codec"], r["case"]) in KNOWN_BOUNDARIES else "")
            if r["off"]["recall"] <= 0 < r["on"]["recall"]:
                note = (note + " " if note else "") + "兜底救回"
            lines.append(
                f"| {r['size']} | {r['codec']} | {r['kind']} | {r['case']} | "
                f"{r['off']['recall']*100:.1f} | {r['on']['recall']*100:.1f} | "
                f"{r['on']['precision']*100:.1f} | {note} |")
    return "\n".join(lines)


def summarize(report: dict) -> str:
    """打印双口径汇总（含放行路径的误报代价，供权衡）。"""
    off = [r["off"]["recall"] for r in report["rows"] if r["case"] != "(clean)"]
    on = [r["on"]["recall"] for r in report["rows"] if r["case"] != "(clean)"]
    off_fpr = [r["off"]["fpr"] for r in report["rows"] if r["case"] == "(clean)"]
    on_fpr = [r["on"]["fpr"] for r in report["rows"] if r["case"] == "(clean)"]
    return (
        f"默认路径：平均召回 {np.mean(off)*100:.1f}% · 黑屏 {int(sum(1 for v in off if v <= 0))} 例 · "
        f"干净误报均值 {np.mean(off_fpr)*100:.2f}%（最大 {max(off_fpr)*100:.2f}%）\n"
        f"放行路径：平均召回 {np.mean(on)*100:.1f}% · 黑屏 {int(sum(1 for v in on if v <= 0))} 例 · "
        f"干净误报均值 {np.mean(on_fpr)*100:.2f}%（最大 {max(on_fpr)*100:.2f}%）"
    )


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="缩放 × 二次转码级联的检测鲁棒性审计")
    ap.add_argument("--quick", action="store_true",
                    help="只跑 JPEG75 的 256/320/640（快速迭代）")
    ap.add_argument("--json", default=None, help="把结果写入 JSON 文件")
    args = ap.parse_args()

    global SIZES, CODECS
    if args.quick:
        SIZES, CODECS = (256, 320, 640), ("jpeg75",)

    report = collect()
    print(to_markdown(report))
    print()
    print(summarize(report))
    ok, problems = evaluate(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({**report, "ok": ok, "problems": problems}, fh,
                      ensure_ascii=False, indent=2)
    print()
    if ok:
        print("✅ 缩放鲁棒性审计通过（默认路径精度达标、放行路径无黑屏且召回达标）")
        return 0
    print("❌ 缩放鲁棒性审计未通过：")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
