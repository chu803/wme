# -*- coding: utf-8 -*-
"""干净图误伤红线门禁（阶段 394）。

量的是**用户上传一张没有水印的图，会被改掉多少画面**：
默认路径（界面「通用检测」= preset general）→ 修复 → 统计改动像素占比。

为什么单独设门禁：
· `clean_bench_probe` 统计的是「**整牌误发**」（把整块牌补上去），看不到**局部涂抹**；
· 局部涂抹才伤用户：一张玻璃幕墙照被"修复"了 5.15%，画面被抹花（阶段 394 实测）。

红线（config）：
· 单张改动占比 ≤ `CLEAN_FP_MAX_CHANGE_RATIO`
· 全部干净图中位改动 ≤ `CLEAN_FP_MEDIAN_CHANGE_RATIO`
已知缺口（`CLEAN_FP_KNOWN_GAP_IMAGES`）**显式列出、不静默丢弃**（沿用阶段 243 的做法）。

退出码：0 通过 / 1 踩线 / 2 缺资产（没有干净图集）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config

CLEAN_DIR = os.path.join(_ROOT, "benchmark_real", "clean")
OUT_PATH = os.path.join(_ROOT, "benchmark_real", "clean_fp_baseline.json")
#: 引擎固定 OpenCV：门禁量的是「掩码+修复」本身的影响，不随本机装了 LaMa 而漂移。
ENGINE = "opencv"


def _read(path: str):
    import cv2
    import numpy as np

    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    return img


def _clean_images() -> list[str]:
    """干净图 = manifest 里 clean=true 且真值 watermarks 为空的条目。"""
    man_path = os.path.join(CLEAN_DIR, "manifest.json")
    truth_path = os.path.join(CLEAN_DIR, "ground_truth.json")
    if not (os.path.exists(man_path) and os.path.exists(truth_path)):
        return []
    manifest = json.load(open(man_path, encoding="utf-8"))
    truth = json.load(open(truth_path, encoding="utf-8"))
    empty = {e.get("image_id") for e in truth.get("entries", []) if not e.get("watermarks")}
    out = []
    for im in manifest.get("images", []):
        if not im.get("clean"):
            continue
        iid = im.get("id")
        if empty and iid not in empty:
            continue
        p = os.path.join(CLEAN_DIR, im.get("path", ""))
        if os.path.exists(p):
            out.append((iid or os.path.basename(p), p))
    return out


def run() -> dict:
    import numpy as np
    from core.detector import detect_watermark_mask
    from core.inpainter import inpaint

    items = _clean_images()
    if not items:
        return {"n_images": 0, "rows": [], "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    rows = []
    for iid, path in items:
        img = _read(path)
        if img is None:
            continue
        try:
            det = detect_watermark_mask(img, preset="general", effective_preset="general")
            mask = det.mask
            changed = 0.0
            if int((mask > 0).sum()) > 0:
                rep = inpaint(img, mask, engine=ENGINE, strength=1.0, dilate=0, feather=5,
                              threshold=127, denoise=3)
                diff = np.abs(img.astype(np.int16) - rep.astype(np.int16)).max(axis=2)
                changed = float((diff > 10).mean())
            rows.append({
                "image_id": iid,
                "mask_ratio": round(float((mask > 0).mean()), 5),
                "changed_ratio": round(changed, 5),
                "regions": len(det.regions),
                "known_gap": iid in config.CLEAN_FP_KNOWN_GAP_IMAGES,
            })
        except Exception as exc:  # noqa: BLE001
            rows.append({"image_id": iid, "error": f"{type(exc).__name__}: {exc}"})

    ok = [r for r in rows if "error" not in r]
    gaps = [r for r in ok if r.get("known_gap")]
    judged = [r for r in ok if not r.get("known_gap")]
    vals = sorted(r["changed_ratio"] for r in judged)
    summary = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "engine": ENGINE,
        "n_images": len(ok),
        "n_errors": len(rows) - len(ok),
        "median_change_ratio": round(vals[len(vals) // 2], 5) if vals else 0.0,
        "max_change_ratio": round(max(vals), 5) if vals else 0.0,
        "known_gap_rows": gaps,
        "rows": rows,
    }
    return summary


def judge(summary: dict) -> list[str]:
    """返回踩线原因列表（空 = 通过）。"""
    problems: list[str] = []
    if not summary.get("n_images"):
        return ["没有干净图集（缺 benchmark_real/clean/manifest.json）"]
    if summary.get("n_errors"):
        problems.append(f"{summary['n_errors']} 张干净图处理出错")
    if summary["max_change_ratio"] > config.CLEAN_FP_MAX_CHANGE_RATIO:
        worst = max((r for r in summary["rows"] if not r.get("known_gap") and "error" not in r),
                    key=lambda r: r["changed_ratio"], default=None)
        problems.append(
            f"单张改动 {summary['max_change_ratio']:.4%} > 上限 {config.CLEAN_FP_MAX_CHANGE_RATIO:.2%}"
            + (f"（最坏：{worst['image_id']}）" if worst else "")
        )
    if summary["median_change_ratio"] > config.CLEAN_FP_MEDIAN_CHANGE_RATIO:
        problems.append(
            f"中位改动 {summary['median_change_ratio']:.4%} > 上限 {config.CLEAN_FP_MEDIAN_CHANGE_RATIO:.2%}"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    from scripts.console_encoding import force_utf8_stdio

    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="干净图误伤红线门禁")
    ap.add_argument("--gate", action="store_true", help="按 config 红线判定（0 通过 / 1 踩线 / 2 缺资产）")
    ap.add_argument("--out", default=OUT_PATH, help="结果 JSON 路径")
    args = ap.parse_args(argv)

    summary = run()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("干净图误伤实测（默认路径 general / 引擎 %s）" % ENGINE)
    print("  图数: %d（其中已知缺口 %d）" % (summary.get("n_images", 0), len(summary.get("known_gap_rows", []))))
    print("  中位改动: %.4f%%   最大改动: %.4f%%" % (
        summary.get("median_change_ratio", 0) * 100, summary.get("max_change_ratio", 0) * 100))
    for gap in summary.get("known_gap_rows", []):
        print("  ⚠ 已知缺口（不计入红线，但列出）: %s 改动 %.4f%%" % (
            gap["image_id"], gap["changed_ratio"] * 100))
    print("  结果已写入:", args.out)

    if not args.gate:
        return 0
    problems = judge(summary)
    if not summary.get("n_images"):
        return 2
    if problems:
        print("[FAIL] 干净图误伤踩线：")
        for p in problems:
            print("  -", p)
        return 1
    print("[OK] 干净图误伤在红线内")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
