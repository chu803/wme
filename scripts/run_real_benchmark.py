"""
方向 A · 真实图门禁（detector 评估）
====================================

职责
----
对 `benchmark_real/manifest.json` 中每张图 × 10 个预设（general / ai_tiled /
ai_signature / ai_corner / ai_text_heavy / ai_faint_tiled / ai_grid / ai_gradient /
ai_logo / ai_multi），与 `ground_truth.json` 真值对比，计算：
- **Recall**（真值水印被检测到的覆盖率，按区域；TP 区域数 / 真值区域总数）
- **Precision**（检测出的区域有多少是真水印；TP 区域数 / 预测区域总数）
- **IoU**（区域级：TP 区域 IoU 均值）
- **FP count**（误报区域数）
- **Mask coverage**（掩码占整图比例；与 recall 互补，验证「掩码是否覆盖到真水印」）

CLI
---
    python scripts/run_real_benchmark.py
    python scripts/run_real_benchmark.py --presets general,ai_corner
    python scripts/run_real_benchmark.py --output benchmark_real/results/bench_<ts>.json

输出
----
- JSON：`results/<file>.json` 含每图每预设的逐项指标 + 聚合；
- stdout：Markdown 表格（per-image + per-preset summary）。

匹配规则
--------
- 区域级匹配：predicted bbox 与 truth bbox IoU >= 0.3 视为同一区域；
- 一对一（贪心）：按 IoU 降序匹配，匹配成功的 predicted 与 truth 各计一次。
- 未匹配的 predicted → FP；未匹配的 truth → FN。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

BENCH_DIR = os.environ.get("WME_BENCH_DIR") or os.path.join(_ROOT, "benchmark_real")
RESULTS_DIR = os.path.join(BENCH_DIR, "results")
MANIFEST_PATH = os.path.join(BENCH_DIR, "manifest.json")
TRUTH_PATH = os.path.join(BENCH_DIR, "ground_truth.json")

DEFAULT_PRESETS = [
    "general", "ai_tiled", "ai_signature", "ai_corner", "ai_text_heavy",
    "ai_faint_tiled", "ai_grid", "ai_gradient", "ai_logo", "ai_multi",
]
IOU_MATCH_THRESHOLD = 0.3


# -----------------------------------------------------------------------------#
# 几何工具
# -----------------------------------------------------------------------------#
def _iou(a: List[int], b: List[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(ax, bx)
    iy = max(ay, by)
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    iw = max(0, min(ax2, bx2) - ix)
    ih = max(0, min(ay2, by2) - iy)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _match_regions(preds: List[List[int]], truths: List[List[int]]) -> Tuple[int, int, int, List[float]]:
    """贪心匹配 IoU >= 阈值。返回 (TP, FP, FN, 匹配 IoU 列表)。"""
    pairs: List[Tuple[float, int, int]] = []  # (iou, pred_idx, truth_idx)
    for pi, p in enumerate(preds):
        for ti, t in enumerate(truths):
            iou = _iou(p, t)
            if iou >= IOU_MATCH_THRESHOLD:
                pairs.append((iou, pi, ti))
    pairs.sort(key=lambda x: -x[0])
    used_p, used_t, ious = set(), set(), []
    for iou, pi, ti in pairs:
        if pi in used_p or ti in used_t:
            continue
        used_p.add(pi)
        used_t.add(ti)
        ious.append(iou)
    tp = len(ious)
    fp = len(preds) - tp
    fn = len(truths) - tp
    return tp, fp, fn, ious, used_p, used_t


def _mask_iou(pred_mask: Optional[Any], truth_mask: Optional[Any]) -> float:
    if pred_mask is None or truth_mask is None:
        return 0.0
    try:
        import numpy as np
        p = (pred_mask > 0).astype(bool)
        t = (truth_mask > 0).astype(bool)
        if p.shape != t.shape:
            return 0.0
        inter = int(np.logical_and(p, t).sum())
        union = int(np.logical_or(p, t).sum())
        return inter / union if union > 0 else 0.0
    except Exception:
        return 0.0


def _mask_px(pred_mask: Any, truth_mask: Any) -> Tuple[int, int]:
    """返回 `(交集像素, 真值像素)` —— 掩码级召回的分子/分母（阶段 175）。

    掩码级召回 = 预测掩码 ∩ 真值掩码 / 真值掩码。它是**区域级 IoU 的替代头条口径**：
    区域级逐项 IoU 把「一块水印」当「一个框」，对「1 真值框 vs N 预测碎片」的目标
    （实测检测器会把角标拆成多个区域）系统性失真，且与真值框大小强耦合
    （IoU ≈ 预测面积/真值面积）。掩码级不受这两点影响。
    """
    try:
        import numpy as np
        p = (pred_mask > 0).astype(bool)
        t = (truth_mask > 0).astype(bool)
        if p.shape != t.shape:
            return 0, int(t.sum())
        return int(np.logical_and(p, t).sum()), int(t.sum())
    except Exception:  # noqa: BLE001
        return 0, 0


def _bbox_to_mask(bboxes: List[List[int]], shape: Tuple[int, int]) -> Any:
    import numpy as np
    m = np.zeros(shape, dtype=np.uint8)
    for x, y, w, h in bboxes:
        if w <= 0 or h <= 0:
            continue
        x2 = min(x + w, shape[1])
        y2 = min(y + h, shape[0])
        if x2 <= x or y2 <= y:
            continue
        m[y:y2, x:x2] = 255
    return m


# -----------------------------------------------------------------------------#
# 单图评估
# -----------------------------------------------------------------------------#
def evaluate_one(image_rgb: Any,
                  truth_entry: Dict[str, Any],
                  preset: str,
                  use_ocr: bool = False) -> Dict[str, Any]:
    from core.detector import detect_watermark_mask

    h, w = image_rgb.shape[:2]
    truths = [list(map(int, wm["bbox"])) for wm in truth_entry.get("watermarks", [])]
    truth_styles = [str(wm.get("style", "unknown")) for wm in truth_entry.get("watermarks", [])]
    truth_mask = _bbox_to_mask(truths, (h, w))
    try:
        res = detect_watermark_mask(image_rgb, preset=preset, use_ocr=use_ocr)
        preds = [list(map(int, (r.x, r.y, r.w, r.h))) for r in res.regions]
        pred_kinds = [str(getattr(r, "kind", "unknown")) for r in res.regions]
        pred_mask = res.mask
    except Exception as e:
        return {
            "preset": preset, "error": str(e),
            "tp": 0, "fp": 0, "fn": len(truths),
            "precision": 0.0, "recall": 0.0,
            "mean_iou": 0.0, "mask_iou": 0.0, "mask_recall": 0.0,
            "mask_inter_px": 0, "truth_px": 0,
            "mask_coverage": 0.0, "truth_coverage": 0.0, "coverage_ratio": None,
            "pred_count": 0, "truth_count": len(truths),
            "recall_by_kind": {}, "precision_by_kind": {},
        }
    tp, fp, fn, ious, used_p, used_t = _match_regions(preds, truths)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    mask_iou = _mask_iou(pred_mask, truth_mask)
    mask_inter, truth_px = _mask_px(pred_mask, truth_mask)
    mask_recall = mask_inter / truth_px if truth_px > 0 else 0.0
    cov = float((pred_mask > 0).sum() / pred_mask.size) if pred_mask is not None else 0.0
    # 阶段 93：真值面积与「过检倍率」。
    #   coverage_ratio = 预测掩码面积 ÷ 真值水印面积 —— 理想值 1.0。
    #   它是**唯一与「水印种类」无关的面积判据**：角标只看几千像素、平铺看整幅，
    #   绝对覆盖率对两者不可比，而倍率可比。真值面积为 0 时无定义 → None（聚合时剔除）。
    truth_cov = (float((truth_mask > 0).sum() / truth_mask.size)
                 if truth_mask is not None else 0.0)
    ratio = round(cov / truth_cov, 2) if truth_cov > 0 else None
    # 阶段 P1：per-kind（按水印 style）召回/精确率 —— 直接回答「grid_watermark /
    # translucent / edge_text 等具体类是否被测到」，是 301/296 量化的关键视角。
    # 区域级匹配已给出 used_p / used_t，此处按 truth style / pred kind 归并。
    _rec: Dict[str, List[int]] = {}
    for ti, s in enumerate(truth_styles):
        bucket = _rec.setdefault(s, [0, 0])
        bucket[1] += 1
        if ti in used_t:
            bucket[0] += 1
    _prec: Dict[str, List[int]] = {}
    for pi, k in enumerate(pred_kinds):
        bucket = _prec.setdefault(k, [0, 0])
        bucket[1] += 1
        if pi in used_p:
            bucket[0] += 1
    recall_by_kind = {
        s: {"matched": m, "total": t,
            "recall_micro": round(m / t, 4) if t else 0.0}
        for s, (m, t) in _rec.items()
    }
    precision_by_kind = {
        k: {"matched": m, "total": t,
            "precision_micro": round(m / t, 4) if t else 0.0}
        for k, (m, t) in _prec.items()
    }
    return {
        "preset": preset,
        "tp": tp, "fp": fp, "fn": fn,
        "recall_by_kind": recall_by_kind,
        "precision_by_kind": precision_by_kind,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "mean_iou": round(sum(ious) / len(ious), 4) if ious else 0.0,
        "mask_iou": round(mask_iou, 4),
        "mask_recall": round(mask_recall, 4),
        "mask_inter_px": mask_inter,
        "truth_px": truth_px,
        "mask_coverage": round(cov, 4),
        "truth_coverage": round(truth_cov, 4),
        "coverage_ratio": ratio,
        "pred_count": len(preds), "truth_count": len(truths),
    }


# -----------------------------------------------------------------------------#
# 全量跑
# -----------------------------------------------------------------------------#
def run_all(presets: List[str], manifest: Dict[str, Any], truth: Dict[str, Any]) -> Dict[str, Any]:
    import config  # 阶段审计修正：门禁须对齐产品真实默认（DETECT_USE_OCR）
    import numpy as np
    from PIL import Image

    truth_by_id = {e["image_id"]: e for e in truth.get("entries", [])}
    images = manifest.get("images", [])
    if not images:
        raise RuntimeError("manifest 为空；先 collect_real_assets.py scan")
    labeled = [im for im in images if im["id"] in truth_by_id]
    if not labeled:
        raise RuntimeError("manifest 中没有任何图有真值；先 build_real_ground_truth.py auto / 手工标注")

    # 阶段 243：把**图像级已知检测缺口**（`config.WATERMARK_KNOWN_GAP_IMAGES`，目前是
    # a1~a4「大颗多面体宝石角标」）从**参与红线的聚合**里剔除——它们是真值完备但检测器
    # 尚未能处理的子风格（见 config 注释与 docs/243）。设计要点：
    #   ① **不静默丢弃**：缺口图仍会被检测、指标仍算出来，放进独立的 `known_gap_rows`
    #      段返回，供门禁报告原样展示（否则「留白会被读成忘了」，见经验与 232 教训）；
    #   ② **只影响聚合口径**，不改真值、不改 manifest；
    #   ③ 若缺口图恰好不在本数据集（数据集切换/子集跑），不报错——按交集处理。
    _gap_ids = set(getattr(config, "WATERMARK_KNOWN_GAP_IMAGES", ()) or ())
    gap_images = [im for im in labeled if im["id"] in _gap_ids]
    eval_images = [im for im in labeled if im["id"] not in _gap_ids]
    if not eval_images:                       # 全是缺口图⇒ 退化保护：仍评估全部
        eval_images, gap_images = labeled, []

    rows: List[Dict[str, Any]] = []
    for im in eval_images:
        iid = im["id"]
        rel = im["path"]
        abs_p = rel if os.path.isabs(rel) else os.path.join(BENCH_DIR, rel)
        try:
            arr = np.array(Image.open(abs_p).convert("RGB"))
        except Exception as e:
            print(f"[error] 读图失败 {iid}: {e}", file=sys.stderr)
            continue
        truth_entry = truth_by_id[iid]
        per_preset = {}
        for preset in presets:
            per_preset[preset] = evaluate_one(arr, truth_entry, preset=preset, use_ocr=config.DETECT_USE_OCR)
        rows.append({
            "image_id": iid,
            "ai_source": im.get("ai_source"),
            "original_size": im.get("original_size"),
            "watermark_count": len(truth_entry.get("watermarks", [])),
            "results": per_preset,
        })
    if not rows:
        raise RuntimeError("所有图都读失败；检查路径")

    # 阶段 243：缺口图也照常检测，指标单独成段返回（**不参与聚合、不参与红线**）。
    #   这样门禁既能对承诺面红线负责，又能把「已知未处理」的子风格原样摊开。
    gap_rows: List[Dict[str, Any]] = []
    for im in gap_images:
        iid = im["id"]
        rel = im["path"]
        abs_p = rel if os.path.isabs(rel) else os.path.join(BENCH_DIR, rel)
        try:
            arr = np.array(Image.open(abs_p).convert("RGB"))
        except Exception as e:
            print(f"[error] 缺口图读图失败 {iid}: {e}", file=sys.stderr)
            continue
        truth_entry = truth_by_id[iid]
        per_preset = {p: evaluate_one(arr, truth_entry, preset=p, use_ocr=config.DETECT_USE_OCR)
                      for p in presets}
        gap_rows.append({
            "image_id": iid,
            "watermark_count": len(truth_entry.get("watermarks", [])),
            "results": per_preset,
        })

    out = _aggregate(rows, presets)
    # 阶段 243：`per_image` 供**面积监控**消费（量「该通路过开火面积」，无真值、
    #   对缺口图同样有意义）⇒ 含缺口图（29 张）；而 `per_preset_aggregate` 供
    #   **承诺面质量红线**消费（需真值）⇒ 只含非缺口图（25 张）。两个口径**分开**：
    #   缺口是「已知不会处理」，不是「不测它的过检」。
    out["per_image"] = rows + gap_rows
    out["known_gap_rows"] = gap_rows
    out["known_gap_image_ids"] = sorted(r["image_id"] for r in gap_rows)
    return out


def _aggregate(rows: List[Dict[str, Any]], presets: List[str]) -> Dict[str, Any]:
    """按预设聚合 precision/recall/IoU/掩码指标；同时输出 per-image 原始数据。

    阶段 93 增补 `mask_coverage_mean`：此前**只算不聚合**，导致「过检面积」这一维度
    在聚合层不可见（红线无从挂载）。掩码覆盖率的**方向与 IoU 相反**——
    越高越坏，故它必须成对地带上方向语义供门禁消费。

    阶段 155 增补 micro 聚合（`precision_micro`/`recall_micro`/`tp_total`/`fp_total`/`fn_total`）：
    **头条口径是 micro**——按汇总 TP/FP/FN 算（precision=tp/(tp+fp)，recall=tp/(tp+fn)），
    与文件头对 recall 的定义「TP 区域数 / 真值区域总数」一致。保留的 `precision_mean`/
    `recall_mean` 是 per-image 均值（macro），仅作次级参考；macro 会**系统性高估**
    （少数干净小图与多数重残大图权重相同），喂给红线可能误绿，故 `real_gate` 改用 micro。
    """
    agg: Dict[str, Dict[str, float]] = {}
    for preset in presets:
        valid = [r for r in rows if "error" not in r["results"][preset]]
        precs = [r["results"][preset]["precision"] for r in valid]
        recs = [r["results"][preset]["recall"] for r in valid]
        ious = [r["results"][preset]["mean_iou"] for r in valid]
        mask_ious = [r["results"][preset]["mask_iou"] for r in valid]
        # 阶段 175：掩码级召回（头条口径）。micro = Σ交集像素 / Σ真值像素。
        mrecs = [r["results"][preset].get("mask_recall", 0.0) for r in valid]
        m_inter_total = sum(int(r["results"][preset].get("mask_inter_px", 0)) for r in valid)
        truth_px_total = sum(int(r["results"][preset].get("truth_px", 0)) for r in valid)
        covs = [r["results"][preset]["mask_coverage"] for r in valid]
        rcovs = [r["results"][preset]["truth_coverage"] for r in valid]
        ratios = [r["results"][preset].get("coverage_ratio") for r in valid]
        ratios = [x for x in ratios if x is not None]
        # 阶段 155：micro 聚合（按汇总 TP/FP/FN）——与 docstring 对 recall 的定义
        # 「TP 区域数 / 真值区域总数」一致；此前 precision_mean/recall_mean 是 per-image
        # 均值（macro），会**系统性高估**（探针：单完美图 + 单重残图 → +0.25 绝对差），
        # 喂给 real_gate 红线可能误绿。故 micro 为头条口径，macro 降为次级参考。
        tp_total = sum(r["results"][preset]["tp"] for r in valid)
        fp_total = sum(r["results"][preset]["fp"] for r in valid)
        fn_total = sum(r["results"][preset]["fn"] for r in valid)
        agg[preset] = {
            "n": len(precs),
            "precision_micro": round(tp_total / (tp_total + fp_total), 4) if (tp_total + fp_total) > 0 else 0.0,
            "recall_micro": round(tp_total / (tp_total + fn_total), 4) if (tp_total + fn_total) > 0 else 0.0,
            "tp_total": tp_total, "fp_total": fp_total, "fn_total": fn_total,
            "precision_mean": round(sum(precs) / len(precs), 4) if precs else 0.0,
            "recall_mean": round(sum(recs) / len(recs), 4) if recs else 0.0,
            "mean_iou_mean": round(sum(ious) / len(ious), 4) if ious else 0.0,
            "mask_iou_mean": round(sum(mask_ious) / len(mask_ious), 4) if mask_ious else 0.0,
            # 阶段 175：掩码级召回 = Σ交集 / Σ真值（micro）；mean 仅作次级参考。
            "mask_recall_micro": (round(m_inter_total / truth_px_total, 4)
                                  if truth_px_total > 0 else 0.0),
            "mask_recall_mean": round(sum(mrecs) / len(mrecs), 4) if mrecs else 0.0,
            "mask_inter_px_total": m_inter_total,
            "truth_px_total": truth_px_total,
            "mask_coverage_mean": round(sum(covs) / len(covs), 4) if covs else 0.0,
            "truth_coverage_mean": round(sum(rcovs) / len(rcovs), 4) if rcovs else 0.0,
            "coverage_ratio_mean": round(sum(ratios) / len(ratios), 2) if ratios else 0.0,
            "coverage_ratio_n": len(ratios),
        }
    # 阶段 P1：per-kind（按水印 style）聚合 —— 跨图按汇总 matched/total 算 micro，
    # 直接暴露「某类水印召回多少」，让 301/296 从数据缺口变可量化（缺口图已不在 rows）。
    per_kind: Dict[str, Any] = {}
    for preset in presets:
        _rec: Dict[str, List[int]] = {}
        _prec: Dict[str, List[int]] = {}
        n_imgs = 0
        for r in rows:
            res = r.get("results", {}).get(preset, {})
            if "error" in res:
                continue
            n_imgs += 1
            for s, v in (res.get("recall_by_kind") or {}).items():
                b = _rec.setdefault(s, [0, 0])
                b[0] += v["matched"]; b[1] += v["total"]
            for k, v in (res.get("precision_by_kind") or {}).items():
                b = _prec.setdefault(k, [0, 0])
                b[0] += v["matched"]; b[1] += v["total"]
        per_kind[preset] = {
            "n_images": n_imgs,
            "recall_by_kind": {
                s: {"matched": m, "total": t,
                    "recall_micro": round(m / t, 4) if t else 0.0}
                for s, (m, t) in _rec.items()
            },
            "precision_by_kind": {
                k: {"matched": m, "total": t,
                    "precision_micro": round(m / t, 4) if t else 0.0}
                for k, (m, t) in _prec.items()
            },
        }

    return {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_images": len(rows),
        "presets": presets,
        "per_image": rows,
        "per_preset_aggregate": agg,
        "per_kind": per_kind,
    }


def _print_markdown(result: Dict[str, Any]) -> None:
    print("\n## 真实图门禁结果")
    print(f"\n- 图像数：{result['n_images']}")
    print(f"- 预设数：{len(result['presets'])}\n")
    print("### 每预设聚合")
    print("| 预设 | n | **mask_recall(micro)** | recall(区域micro) | mask_iou | mask_coverage | mean_iou(区域) | precision(区域micro) |")
    print("|---|---|---|---|---|---|---|---|")
    for preset, a in result["per_preset_aggregate"].items():
        print(f"| {preset} | {a['n']} | **{a.get('mask_recall_micro', 0.0):.4f}** | "
              f"{a['recall_micro']:.4f} | {a['mask_iou_mean']:.4f} | "
              f"{a.get('mask_coverage_mean', 0.0):.4f} | {a['mean_iou_mean']:.4f} | "
              f"{a['precision_micro']:.4f} |")
    print("\n> **头条口径 = `mask_recall`（掩码级）**：区域级逐项 IoU 对「1 真值框 vs "
          "N 预测碎片」失真、且与真值框大小强耦合（IoU ≈ 预测面积/真值面积），"
          "故区域级三列降为次级参考。")
    print("\n### 每图每预设（recall）")
    print("| image_id | ai_source | " + " | ".join(result["presets"]) + " |")
    print("|---|---|" + "|".join(["---"] * len(result["presets"])) + "|")
    for r in result["per_image"]:
        cells = [f"{r['results'][p]['recall']:.3f}" if "error" not in r["results"][p] else "ERR"
                 for p in result["presets"]]
        print(f"| {r['image_id']} | {r.get('ai_source', '?')} | " + " | ".join(cells) + "|")

    print("\n### 每类水印（per-kind，区域级 micro）")
    printed = False
    for preset, pk in result.get("per_kind", {}).items():
        rbk = pk.get("recall_by_kind", {})
        pbk = pk.get("precision_by_kind", {})
        if not rbk and not pbk:
            continue
        printed = True
        print(f"\n**{preset}**（n={pk.get('n_images', 0)}）")
        print("| 水印类 | 召回 matched/total | 召回(micro) | 精确率 matched/total | 精确率(micro) |")
        print("|---|---|---|---|---|")
        for k in sorted(set(rbk) | set(pbk)):
            r = rbk.get(k, {"matched": 0, "total": 0, "recall_micro": 0.0})
            p = pbk.get(k, {"matched": 0, "total": 0, "precision_micro": 0.0})
            print(f"| {k} | {r['matched']}/{r['total']} | {r['recall_micro']:.4f} | "
                  f"{p['matched']}/{p['total']} | {p['precision_micro']:.4f} |")
    if not printed:
        print("\n（无 per-kind 数据）")


def main() -> int:
    ap = argparse.ArgumentParser(description="真实图门禁（detector 评估）")
    ap.add_argument("--presets", default=",".join(DEFAULT_PRESETS),
                    help=f"逗号分隔；默认 {len(DEFAULT_PRESETS)} 个预设")
    ap.add_argument("--output", default=None,
                    help=f"JSON 输出路径；默认写入 {os.path.join(RESULTS_DIR, 'bench_<ts>.json')}")
    args = ap.parse_args()

    if not os.path.exists(MANIFEST_PATH):
        print(f"[error] manifest 不存在：{MANIFEST_PATH}（先 collect_real_assets.py scan）",
              file=sys.stderr)
        return 1
    if not os.path.exists(TRUTH_PATH):
        print(f"[error] ground_truth 不存在：{TRUTH_PATH}（先 build_real_ground_truth.py auto）",
              file=sys.stderr)
        return 1

    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    truth = json.load(open(TRUTH_PATH, encoding="utf-8"))
    presets = [p.strip() for p in args.presets.split(",") if p.strip()]

    try:
        result = run_all(presets, manifest, truth)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.output or os.path.join(RESULTS_DIR, f"bench_{ts}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    _print_markdown(result)
    print(f"\n指标已写入：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())