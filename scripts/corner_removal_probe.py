#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""承诺能力（角标 LOGO）在**真实标注图**上的擦除效果实测（阶段 385）。

为什么需要这张表
----------------
现有门禁量的是两件事，都不是用户会问的那个问题：
  · `corner_signal_gate`：角标图有没有被**路由**到 ai_logo（量的是"选没选对策略"）；
  · `removal_truth_probe`：合成图上的**像素级真值**（量的是"掩码对不对"）。
缺的是：**真实角标图跑完"检测 + 修复"之后，角标到底擦掉了没有、误伤了没有。**

口径（与生产代码同源，不另造一套）
----------------------------------
  · 逐张跑**生产路径**：`detect_watermark_mask(preset="ai_logo")` → `inpaint(engine="auto")`，
    参数取界面默认值（力度 1 / 膨胀 0 / 羽化 5 / 阈值 127 / 降噪 3）。
  · **角标可见度**：把 GT 外接框裁剪出来算 Sobel 边缘图、缩放到 48x64，
    与角标模板库（9 张）逐张求 NCC（双极性取大）——与推荐器 `_corner_watermark_signal` 同一套量法。
    修复前高 ⇒ 角标在；修复后掉到 0.30 以下 ⇒ 角标已擦净（0.30 是路由用的同一阈值）。
  · **检出**：修复掩码与 GT 外接框有交集。
  · **误伤**：GT 外接框**之外**发生变化的像素占比（变化判定：任一通道差 > 8）。

退出码：0 正常（无论指标好坏，指标本身由人读）；2 资产缺失（缺图 / 缺真值 / 缺 cv2）。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

IMAGES_DIR = os.path.join(_ROOT, "benchmark_real", "images")
GT_PATH = os.path.join(_ROOT, "benchmark_real", "ground_truth.json")
OUT_PATH = os.path.join(_ROOT, "benchmark_real", "corner_removal_baseline.json")
#: 用户默认路径（走推荐器）的基线，与"强制角标预设"的基线分开存，避免互相覆盖。
OUT_PATH_AUTO = os.path.join(_ROOT, "benchmark_real", "corner_removal_auto_baseline.json")

#: "角标已擦净"的判据 = **低于路由用的同一个 NCC 阈值**（读 config，避免与推荐器漂移）。
#: 含义：修复后该角落**不再**像角标模板了（路由器不会再把它当角标）。
def _ncc_erased_threshold() -> float:
    import config
    return float(getattr(config, "RECOMMEND_CORNER_WATERMARK_NCC_MIN", 0.30))
#: 像素变化判定阈值（任一通道差超过它算"变了"）。
CHANGE_LEVEL = 8


def _skip_reason() -> str | None:
    try:
        import cv2  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"OpenCV 不可用：{exc}"
    if not os.path.isdir(IMAGES_DIR):
        return f"缺真实图目录：{IMAGES_DIR}"
    if not os.path.isfile(GT_PATH):
        return f"缺真值文件：{GT_PATH}"
    return None


def _read_image(path: str):
    """中文文件名必须走 fromfile + imdecode（cv2.imread 在非 ASCII 路径上返回 None）。"""
    import cv2
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _match_entry(stem: str, by_id: dict):
    """文件名 stem → GT 条目（口径同 `corner_signal_gate._entry_for`）。"""
    if stem in by_id:
        return by_id[stem]
    cand = [i for i in by_id
            if stem.startswith(i + "_") or (len(i) == 24 and stem.startswith(i))]
    return by_id[max(cand, key=len)] if cand else None


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt((a * a).sum()) * np.sqrt((b * b).sum())
    return float((a * b).sum() / den) if den > 1e-6 else 0.0


def _corner_ncc_raw(gray_u8: np.ndarray) -> float:
    """角标可见度（**路由器的同一套几何**：右下/中右 ROI → 48x64 → 模板库最大 NCC）。

    为什么不用 GT 框裁剪：角标模板是 64x48 的**字形边缘图**，按 GT 框裁剪再缩放会
    破坏与模板的几何对应（实测修复前 NCC 只有 0.14，压根量不到角标）。
    这里与 `core.preset_recommender._corner_watermark_signal` 用同一套 ROI 与阈值口径，
    区别只是**不提前阈值化**，以便看清"修复前多高、修复后掉到多少"。
    """
    import cv2
    from core.preset_recommender import _load_corner_watermark_templates

    bank = _load_corner_watermark_templates()
    if not bank:
        return 0.0
    h, w = gray_u8.shape[:2]
    g = gray_u8.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    em = np.sqrt(gx * gx + gy * gy)

    best = 0.0
    for y0, y1, x0, x1 in ((int(h * 0.80), h, int(w * 0.74), w),
                           (int(h * 0.45), int(h * 0.72), int(w * 0.78), w)):
        sub = em[y0:y1, x0:x1]
        if sub.size == 0:
            continue
        roi = cv2.resize(sub, (48, 64))
        for tpl in bank:
            s = max(_ncc(roi, tpl), _ncc(-roi, tpl))
            if s > best:
                best = s
    return best


def _changed_ratio(before: np.ndarray, after: np.ndarray, bbox) -> tuple[float, float]:
    """返回 (框外变化像素占比, 框内变化像素占比)。"""
    diff = np.abs(before.astype(np.int16) - after.astype(np.int16)).max(axis=2)
    changed = diff > CHANGE_LEVEL
    H, W = changed.shape
    x, y, w, h = bbox
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    inside = np.zeros_like(changed)
    inside[y0:y1, x0:x1] = True
    out_changed = int((changed & ~inside).sum())
    out_total = int((~inside).sum())
    in_changed = int((changed & inside).sum())
    in_total = int(inside.sum())
    return (out_changed / out_total if out_total else 0.0,
            in_changed / in_total if in_total else 0.0)


def _bbox_of(entry: dict):
    """取该图第一个角标 GT 外接框。"""
    for wm in entry.get("watermarks", []):
        if wm.get("style") == "corner_logo" and wm.get("bbox"):
            return tuple(int(v) for v in wm["bbox"])
    return None


def _pick_preset(img: np.ndarray, mode: str):
    """返回 (要用的预设, 路由信息)。

    · `ai_logo`：强制角标预设（量的是"角标这条路本身能擦多少"）。
    · `auto`   ：走**推荐器**（用户界面"通用检测/智能推荐"的真实路径），
                  顺带记录这张图被推荐到了哪个预设 ⇒ 能看出"默认路径"的表现。
    """
    if mode == "ai_logo":
        return "ai_logo", None
    from core.preset_recommender import recommend_preset

    rec = recommend_preset(img)
    return rec.preset, {"confidence": round(float(rec.confidence), 3),
                        "triggered": list(rec.triggered or [])}


def run(limit: int | None = None, dilate: int = 0, feather: int = 5,
        denoise: int = 3, strength: float = 1.0, preset_mode: str = "ai_logo") -> dict:
    import cv2
    from core.detector import detect_watermark_mask
    from core.inpainter import inpaint
    from services.auto_pipeline import _apply_preset_dilate_floor

    gt = json.load(open(GT_PATH, encoding="utf-8"))
    by_id = {e.get("image_id"): e for e in gt.get("entries", []) if e.get("image_id")}
    files = sorted(os.listdir(IMAGES_DIR))

    rows: list[dict] = []
    for name in files:
        stem = os.path.splitext(name)[0]
        entry = _match_entry(stem, by_id)
        if entry is None:
            continue
        bbox = _bbox_of(entry)
        if bbox is None:
            continue
        img = _read_image(os.path.join(IMAGES_DIR, name))
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        ncc_before = _corner_ncc_raw(gray)

        preset_used, routing = _pick_preset(img, preset_mode)
        t0 = time.time()
        try:
            det = detect_watermark_mask(img, preset=preset_used)
            mask = det.mask
            # 与生产管线同一套"按预设的膨胀下限"（阶段 388），否则探针量不到产品真实行为。
            dilate_used = _apply_preset_dilate_floor(preset_used, dilate)
            repaired = inpaint(img, mask, engine="auto", strength=strength,
                               dilate=dilate_used, feather=feather, threshold=127,
                               denoise=denoise)
        except Exception as exc:  # noqa: BLE001
            rows.append({"image": name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        gray_after = cv2.cvtColor(repaired, cv2.COLOR_BGR2GRAY)
        ncc_after = _corner_ncc_raw(gray_after)
        out_ratio, in_ratio = _changed_ratio(img, repaired, bbox)

        x, y, w, h = bbox
        m = mask[y:y + h, x:x + w]
        detected = bool(m is not None and m.size and int((m > 0).sum()) > 0)

        rows.append({
            "image": name,
            "preset_used": preset_used,
            "routing": routing,
            "ncc_before": round(ncc_before, 4),
            "ncc_after": round(ncc_after, 4),
            "detected": detected,
            "erased": bool(ncc_after < _ncc_erased_threshold()),
            "dilate_used": dilate_used,
            "outside_change_ratio": round(out_ratio, 5),
            "inside_change_ratio": round(in_ratio, 5),
            "seconds": round(time.time() - t0, 2),
        })
        if limit and len(rows) >= limit:
            break

    ok = [r for r in rows if "error" not in r]
    import collections
    summary = {
        "params": {"dilate": dilate, "feather": feather, "denoise": denoise,
                   "strength": strength, "preset_mode": preset_mode},
        "preset_distribution": dict(collections.Counter(r["preset_used"] for r in ok)),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_images": len(ok),
        "n_errors": len(rows) - len(ok),
        "detected_rate": round(sum(1 for r in ok if r["detected"]) / len(ok), 4) if ok else 0.0,
        "erased_rate": round(sum(1 for r in ok if r["erased"]) / len(ok), 4) if ok else 0.0,
        "erased_rate_given_detected": (
            round(sum(1 for r in ok if r["detected"] and r["erased"])
                  / sum(1 for r in ok if r["detected"]), 4)
            if any(r["detected"] for r in ok) else 0.0),
        "median_ncc_before": (
            round(statistics.median([r["ncc_before"] for r in ok if r["detected"]]), 4)
            if any(r["detected"] for r in ok) else 0.0),
        "median_ncc_after": (
            round(statistics.median([r["ncc_after"] for r in ok if r["detected"]]), 4)
            if any(r["detected"] for r in ok) else 0.0),
        "median_outside_change_ratio": (
            round(statistics.median([r["outside_change_ratio"] for r in ok]), 5) if ok else 0.0),
        "median_inside_change_ratio": (
            round(statistics.median([r["inside_change_ratio"] for r in ok]), 5) if ok else 0.0),
        "rows": rows,
    }
    return summary


def judge(summary: dict) -> list[str]:
    """按 config 的三条红线判定（纯函数，便于单测做反例）。"""
    import config

    problems: list[str] = []
    detected = float(summary.get("detected_rate", 0.0))
    erased = float(summary.get("erased_rate_given_detected", 0.0))
    outside = float(summary.get("median_outside_change_ratio", 0.0))
    if detected < config.CORNER_REMOVAL_MIN_DETECTED_RATE:
        problems.append(f"检出率 {detected:.1%} < 红线 {config.CORNER_REMOVAL_MIN_DETECTED_RATE:.0%}")
    if erased < config.CORNER_REMOVAL_MIN_ERASED_RATE:
        problems.append(f"完全擦净率（已检出）{erased:.1%} < 红线 {config.CORNER_REMOVAL_MIN_ERASED_RATE:.0%}")
    if outside > config.CORNER_REMOVAL_MAX_OUTSIDE_RATIO:
        problems.append(f"框外变化中位数 {outside:.3%} > 红线 {config.CORNER_REMOVAL_MAX_OUTSIDE_RATIO:.0%}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="角标 LOGO 真实图擦除效果实测")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 张（调试用）")
    parser.add_argument("--out", default=None,
                        help="结果 JSON 路径（缺省按预设模式选：auto → 自动路径基线）")
    parser.add_argument("--dilate", type=int, default=0, help="掩码膨胀（像素）")
    parser.add_argument("--feather", type=int, default=5, help="边缘羽化")
    parser.add_argument("--denoise", type=int, default=3, help="降噪强度")
    parser.add_argument("--strength", type=float, default=1.0, help="修复力度")
    parser.add_argument("--preset", choices=("ai_logo", "auto"), default="ai_logo",
                        help="ai_logo=强制角标预设；auto=走推荐器（用户默认路径）")
    parser.add_argument("--gate", action="store_true",
                        help="门禁模式：按 config 的三条红线判定（0 通过 / 1 踩线 / 2 缺资产）")
    args = parser.parse_args(argv)

    if args.out is None:
        args.out = (OUT_PATH_AUTO if args.preset == "auto" else OUT_PATH)

    reason = _skip_reason()
    if reason:
        print(f"[SKIP] {reason}")
        return 2

    summary = run(limit=args.limit, dilate=args.dilate, feather=args.feather,
                  denoise=args.denoise, strength=args.strength,
                  preset_mode=args.preset)
    if args.gate:
        problems = judge(summary)
        if problems:
            print("[FAIL] 角标 LOGO 真实图擦除效果踩线：")
            for item in problems:
                print(f"  · {item}")
            return 1
        print(f"[OK] 角标 LOGO 真实图擦除效果通过：检出 {summary['detected_rate']:.1%}、"
              f"完全擦净 {summary['erased_rate_given_detected']:.1%}、"
              f"框外变化 {summary['median_outside_change_ratio']:.3%}")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    s = {k: v for k, v in summary.items() if k != "rows"}
    print("角标 LOGO 真实图擦除效果（生产路径）")
    for k, v in s.items():
        print(f"  {k}: {v}")
    print(f"  结果已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
