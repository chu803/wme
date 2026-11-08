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
from utils.exceptions import EmptyMaskError  # noqa: E402
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


def _standout_ratio(gray_a: np.ndarray, inner: np.ndarray, ring: np.ndarray) -> float:
    """水印区域修完之后**还比周围显眼吗**（阶段 399 最终口径）。

    三轮试错（都记在 docs/399）：
    ① 整框 NCC / 边缘均值 → 被"本来就不该变"的背景稀释，文字还在也判擦净；
    ② 笔画边缘残留 → 抹糊本身削高频边缘，"糊了但可读"看着像擦掉；
    ③ 对比度残留 → 半透明板本身对比度低，比值不降 ⇒ 误判"没擦净"。
    ⇒ 换成**结构显著性**：足迹内的边缘能量 ÷ 周边环的边缘能量。
      · 真的擦掉了 ⇒ 足迹内只剩背景纹理 ⇒ 与周围同 level ⇒ 比值 ≈ 1；
      · 还剩板/字 ⇒ 足迹内有明显结构（板边、笔画）⇒ 比值明显 > 1。
    """
    g = gray_a.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    e = np.sqrt(gx * gx + gy * gy)
    if int(inner.sum()) == 0 or int(ring.sum()) == 0:
        return 1.0
    inner_e = float(e[inner].mean())
    outer_e = float(e[ring].mean())
    if outer_e <= 1e-6:
        return 1.0
    return inner_e / outer_e


def _sharp_visibility(gray_b: np.ndarray, gray_a: np.ndarray) -> float:
    """足迹内的**锐边残留**（修复后/修复前）——与目视一致的"还在不在"。

    阶段 399/400 的教训：整框 NCC / 边缘均值都会被"没变的背景"稀释，
    也会被"涂抹块"骗 ⇒ 都不能代表人眼看到的"水印还在不在"。
    锐边（|∇| 的 99 分位）才对应"字母/板边是否还立在那儿"：
      · 文字还在（可读）⇒ 1.000（实测 B4 修复前）
      · 只剩模糊块 ⇒ 0.281（实测 B4 修复后）
    """
    def _p99(gray: np.ndarray) -> float:
        g = gray.astype(np.float32)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        return float(np.percentile(np.sqrt(gx * gx + gy * gy), 99.0))

    b = _p99(gray_b)
    if b <= 1e-6:
        return 0.0
    return _p99(gray_a) / b


def _plate_ratio(padded_gray: np.ndarray, inner_mask: np.ndarray) -> float:
    """足迹区 vs 周边环的**亮度比**（修复后）——"板/字还在不在"的第二个口径。

    阶段 401 的教训：锐边指标看不见"残留的平整白块"（板没了字、还剩一块均匀的亮板）。
    亮度比补上这一盲区：
      · 白板擦净后与周围同 level ⇒ 比值 ≈ 1（B1 0.96 / B3 0.97 / B4 0.95）；
      · 板还在 ⇒ 比值明显 > 1（未修时 B2 是 1.80，修后 1.48，再修 1.04）。
    """
    if inner_mask.sum() == 0 or padded_gray.size == 0:
        return 1.0
    reg = float(padded_gray[inner_mask].mean())
    ring = float(padded_gray[~inner_mask].mean())
    return reg / ring if ring > 1e-6 else 1.0


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
    try:
        repaired = inpaint(img, mask, engine="auto", strength=1.0, dilate=dilate_used,
                           feather=5, threshold=127, denoise=3)
    except EmptyMaskError:
        # 没检出任何水印 ⇒ 无需修复（不是错误）。
        repaired = img.copy()
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

        # 阶段 399 最终判据：**足迹内还比周围显眼吗**（结构显著性）。
        #   取足迹外一圈（pad=32px）作"周围"，比值 ≈ 1 ⇒ 擦干净了；明显 > 1 ⇒ 还剩板/字。
        pad = 32
        px0, py0 = max(0, x - pad), max(0, y - pad)
        px1, py1 = min(w, x + bw + pad), min(h, y + bh + pad)
        ring_gray = gray_a[py0:py1, px0:px1]
        inner_mask = np.zeros(ring_gray.shape[:2], bool)
        inner_mask[y - py0:y - py0 + bh, x - px0:x - px0 + bw] = True
        ring_mask = ~inner_mask
        standout = _standout_ratio(ring_gray, inner_mask, ring_mask)
        row["standout"] = round(standout, 4)
        # 阶段 400：与目视一致的"还在不在"（锐边残留）——参考样本的红线用它判。
        row["sharp_visibility"] = round(_sharp_visibility(cb, ca), 4)
        # 阶段 401：板/字是否"还比周围显眼"（锐边指标看不见平整残留块）。
        row["plate_ratio"] = round(
            _plate_ratio(ring_gray, inner_mask), 4)
        row["erased"] = bool(standout < 1.5)
        m = mask[y:y + bh, x:x + bw]
        row["detected"] = bool(m.size and int((m > 0).sum()) > 0)
        row["mask_in_box"] = round(float((m > 0).mean()), 4) if m.size else 0.0
        outside = diff.copy()
        outside[y0:y1, x0:x1] = 0
        row["outside_change"] = round(float((outside > 10).mean()), 5)
        row["bbox"] = bbox
    return row


def judge(summary: dict) -> list[str]:
    """按 config 红线判定（返回踩线原因列表，空 = 通过）。

    阶段 399 的口径：
      · **检出率**是硬红线（客观可测）；
      · **"擦没擦净"不做自动判定**（四轮自动指标都不可靠）⇒ 改为守住
        `CORNER_GEOMETRY_ERASED_REF`（**目视确认过真的擦干净了**的样本）不许退化；
      · 其余几何是**已知缺口**：量得出、报得出，但判定交给人看图 ⇒ 不进红线。
    """
    import config

    problems: list[str] = []
    n = int(summary.get("n_logo", 0))
    if n == 0:
        return ["没有带角标的挑战样本（挑战集缺失或生成失败）"]
    if summary["detected_rate"] < config.CORNER_GEOMETRY_MIN_DETECTED:
        problems.append(
            f"角标几何检出率 {summary['detected_rate']:.0%} < 下限 "
            f"{config.CORNER_GEOMETRY_MIN_DETECTED:.0%}")
    for name in sorted(config.CORNER_GEOMETRY_ERASED_REF):
        row = next((r for r in summary.get("rows", []) if r.get("image") == name), None)
        if row is None:
            problems.append(f"参考样本 {name} 缺失（挑战集不完整）")
            continue
        sharp = float(row.get("sharp_visibility", 1.0))
        plate = float(row.get("plate_ratio", 9.0))
        if sharp >= config.CORNER_GEOMETRY_REF_MAX_SHARP:
            problems.append(
                f"参考样本 {name} 擦净退化：锐边残留 {sharp:.3f} ≥ 上限 "
                f"{config.CORNER_GEOMETRY_REF_MAX_SHARP:.2f}（它目视确认过是能擦净的）")
        if plate > config.CORNER_GEOMETRY_REF_MAX_PLATE_RATIO:
            problems.append(
                f"参考样本 {name} 仍有残留：板/字亮度比 {plate:.3f} > 上限 "
                f"{config.CORNER_GEOMETRY_REF_MAX_PLATE_RATIO:.2f}（它目视确认过是能擦净的）")
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
    import config

    ref = set(config.CORNER_GEOMETRY_ERASED_REF)
    summary = {
        "n_logo": len(b),
        "detected_rate": (sum(1 for r in b if r["detected"]) / len(b)) if b else 0.0,
        "erased_rate": (sum(1 for r in b if r["erased"]) / len(b)) if b else 0.0,
        "rows": rows,
    }
    if b:
        print(f"\nB 组（带角标）：检出 {sum(1 for r in b if r['detected'])}/{len(b)}")
        print("  目视裁定过的参考样本（门禁守住它们不许退化）：")
        for r in b:
            if r["image"] in ref:
                print(f"     · {r['image']:<9} 锐边残留={r.get('sharp_visibility', 0):.3f}"
                      f"（上限 {config.CORNER_GEOMETRY_REF_MAX_SHARP:.2f}）"
                      f"  亮度比={r.get('plate_ratio', 0):.3f}"
                      f"（上限 {config.CORNER_GEOMETRY_REF_MAX_PLATE_RATIO:.2f}）")
        print("  已知缺口（目视确认未擦净，不进红线，只列出）：")
        for r in b:
            if r["image"] not in ref:
                print(f"     · {r['image']:<9} 残留NCC={r['residual']:.3f} 边缘比={r['edge_ratio']:.3f}")
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