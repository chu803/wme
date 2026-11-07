# -*- coding: utf-8 -*-
"""挑战集探针：量「换了几何还认不认得出」与「干净图会不会被误改」。

样本：.scratch/challenge/images/（按豆包 manifest 的精确几何参数复刻的 A/B/C 三组）
生产链：`core.detector.detect_watermark_mask` → `services.auto_pipeline._apply_preset_dilate_floor`
        → `core.inpainter.inpaint`（与 scripts/corner_removal_probe.py 同一条路径）

指标：
  · preset        —— 推荐器（用户默认"通用检测"路径）给这张图选了哪个预设
  · detected      —— 掩码有没有盖到角标足迹上（B 组；足迹坐标是已知的）
  · residual      —— 角标足迹区域**修复前后**的相似度（1=原样还在，0=没了）
  · edge_ratio    —— 足迹内边缘能量 修复后/修复前（文字被抹平 ⇒ 趋近 0）
  · erased        —— edge_ratio < 0.35 且 residual < 0.5
  · outside       —— 足迹外被改动的像素占比（误伤）
  · changed       —— 整图被改动的像素占比（A/C 组 = 误伤幅度）
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import cv2  # noqa: E402

from core.detector import detect_watermark_mask  # noqa: E402
from core.inpainter import inpaint  # noqa: E402
from services.auto_pipeline import _apply_preset_dilate_floor  # noqa: E402

BASE = ROOT / "benchmark_real" / "challenge"
IMAGES = BASE / "images"
OUT_REPORT = ROOT / "benchmark_real" / "challenge_report.json"


def ensure_set() -> list:
    """挑战集**确定性生成**（不入库）：缺图就现生成，保证门禁在干净 checkout 上也能跑。"""
    manifest_path = BASE / "manifest.json"
    if manifest_path.exists() and IMAGES.exists() and any(IMAGES.glob("*.png")):
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    import build_challenge_set

    build_challenge_set.main()
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _read(path: pathlib.Path):
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    return img


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32).ravel() - float(a.mean())
    b = b.astype(np.float32).ravel() - float(b.mean())
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a.dot(b) / den) if den else 0.0


def _edge_energy(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.sqrt(gx * gx + gy * gy).mean())


def measure(entry: dict) -> dict:
    name = entry["image"]
    path = IMAGES / name
    img = _read(path)
    if img is None:
        return {"image": name, "error": "read failed"}
    h, w = img.shape[:2]

    from core.preset_recommender import recommend_preset
    rec = recommend_preset(img)
    preset_used = rec.preset

    t0 = time.time()
    det = detect_watermark_mask(img, preset=preset_used, effective_preset=preset_used)
    mask = det.mask
    dilate_used = _apply_preset_dilate_floor(preset_used, 0)
    repaired = inpaint(img, mask, engine="auto", strength=1.0, dilate=dilate_used,
                       feather=5, threshold=127, denoise=3)
    secs = round(time.time() - t0, 2)

    gray_b = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_a = cv2.cvtColor(repaired, cv2.COLOR_BGR2GRAY)
    diff = np.abs(img.astype(np.int16) - repaired.astype(np.int16)).max(axis=2)
    changed = float((diff > 10).mean())

    row = {
        "image": name,
        "group": entry["group"],
        "preset_used": preset_used,
        "confidence": round(float(rec.confidence), 3),
        "changed_ratio": round(changed, 5),
        "mask_ratio": round(float((mask > 0).mean()), 5),
        "seconds": secs,
    }

    bbox = entry.get("bbox")
    if bbox:
        x, y, bw, bh = bbox
        x0, y0 = max(0, x - 2), max(0, y - 2)
        x1, y1 = min(w, x + bw + 2), min(h, y + bh + 2)
        cb, ca = gray_b[y0:y1, x0:x1], gray_a[y0:y1, x0:x1]
        e_b, e_a = _edge_energy(cb), _edge_energy(ca)
        row["residual"] = round(_ncc(cb, ca), 4)
        row["edge_ratio"] = round(e_a / e_b, 4) if e_b else 0.0
        m = mask[y:y + bh, x:x + bw]
        row["detected"] = bool(m.size and int((m > 0).sum()) > 0)
        row["mask_in_box"] = round(float((m > 0).mean()), 4) if m.size else 0.0
        row["erased"] = bool(row["edge_ratio"] < 0.35 and row["residual"] < 0.5)
        outside = diff.copy()
        outside[y0:y1, x0:x1] = 0
        row["outside_change"] = round(float((outside > 10).mean()), 5)
        row["bbox"] = bbox
    return row


def judge(summary: dict) -> list[str]:
    """按 config 红线判定（返回踩线原因列表，空 = 通过）。"""
    import config

    problems: list[str] = []
    n = int(summary.get("n_logo", 0))
    if n == 0:
        return ["没有带角标的挑战样本（挑战集缺失或生成失败）"]
    if summary["detected_rate"] < config.CORNER_GEOMETRY_MIN_DETECTED:
        problems.append(
            f"角标几何检出率 {summary['detected_rate']:.0%} < 下限 "
            f"{config.CORNER_GEOMETRY_MIN_DETECTED:.0%}")
    if summary["erased_rate"] < config.CORNER_GEOMETRY_MIN_ERASED:
        problems.append(
            f"角标几何完全擦净率 {summary['erased_rate']:.0%} < 下限 "
            f"{config.CORNER_GEOMETRY_MIN_ERASED:.0%}")
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="角标几何挑战集探针")
    ap.add_argument("--gate", action="store_true", help="按 config 红线判定（0 通过 / 1 踩线 / 2 缺资产）")
    args = ap.parse_args(argv)

    manifest = ensure_set()
    rows = []
    for entry in manifest:
        try:
            rows.append(measure(entry))
        except Exception as exc:  # noqa: BLE001
            rows.append({"image": entry["image"], "error": f"{type(exc).__name__}: {exc}"})
        r = rows[-1]
        if "error" in r:
            print(f"{r['image']:<10} ERROR {r['error']}")
        elif r["group"] == "B":
            print(f"{r['image']:<10} 预设={r['preset_used']:<14} 检出={str(r['detected']):<5} "
                  f"残留={r['residual']:.3f} 边缘比={r['edge_ratio']:.3f} "
                  f"擦净={str(r['erased']):<5} 足迹外改动={r['outside_change']:.4%} 整图改动={r['changed_ratio']:.4%} {r['seconds']}s")
        else:
            print(f"{r['image']:<10} 预设={r['preset_used']:<14} 整图改动={r['changed_ratio']:.4%} "
                  f"掩码占比={r['mask_ratio']:.4%} {r['seconds']}s")
    (OUT_REPORT).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    b = [r for r in rows if r.get("group") == "B" and "error" not in r]
    ac = [r for r in rows if r.get("group") in ("A", "C") and "error" not in r]
    summary = {
        "n_logo": len(b),
        "detected_rate": (sum(1 for r in b if r["detected"]) / len(b)) if b else 0.0,
        "erased_rate": (sum(1 for r in b if r["erased"]) / len(b)) if b else 0.0,
    }
    if b:
        print(f"\nB 组（带角标）：检出 {sum(1 for r in b if r['detected'])}/{len(b)}，"
              f"擦净 {sum(1 for r in b if r['erased'])}/{len(b)}")
    if ac:
        vals = [r["changed_ratio"] for r in ac]
        print(f"A/C 组（无水印）：平均改动 {sum(vals)/len(vals):.4%}，"
              f"最大 {max(vals):.4%}，被改动的张数 {sum(1 for v in vals if v > 0.0005)}/{len(vals)}")
    print("报告:", OUT_REPORT)

    if not args.gate:
        return 0
    problems = judge(summary)
    if not summary["n_logo"]:
        return 2
    if problems:
        print("[FAIL] 角标几何覆盖踩线：")
        for p in problems:
            print("  -", p)
        return 1
    print("[OK] 角标几何覆盖在红线内")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())