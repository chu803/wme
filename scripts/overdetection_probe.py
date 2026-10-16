# -*- coding: utf-8 -*-
"""
阶段 450+ · 检测过检收敛探针（S2 长期目标的量化 + 可逆杠杆扫描）。

背景（本次沙箱诊断结论）：committed 角标/通用通路在自然底图上的过检，
根因**不是 corner 策略**（corner 命中准确），而是 **translucent（conf≈0.40 的
小斑块泛滥）+ logo_watermark（把山景/纹理误判成实体 LOGO，大框）** 两个策略
在 ai_logo/general 预设里灌满掩码（cov_ratio 实测 6~37×）。收敛需调这两策
略的杠杆，但会动 committed general 基线 ⇒ 必须在能跑 real_gate 的 canonical
venv 里验证（recall/mask_recall 不得退、cov_ratio 下降）。

本探针（纯 numpy/cv2，沙箱可跑 ai_logo/no-ocr 子集；canonical venv 跑 general+ocr）：
  · 逐真值算 mask 级指标（recall / precision / mask_iou / coverage_ratio）；
  · **按区域 kind 分解过检来源**（translucent / corner_logo / logo_watermark /
    edge_text / grid …各贡献多少假阳性面积）——直接指认根因；
  · `--sweep-merge-dilate`：扫描可逆杠杆 DETECT_MERGE_DILATE（0/1/2），
    示范「收紧但不伤 recall」的标定法。

退出码：0=正常出报告；2=缺 benchmark 真值/图片（环境缺资产，非缺陷）。
"""
from __future__ import annotations
import os as _os
import sys as _sys
import json as _json
import argparse as _arg

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from scripts.console_encoding import force_utf8_stdio  # noqa: E402
force_utf8_stdio()

import numpy as np  # noqa: E402
import cv2  # noqa: E402

IMG_DIR = _os.path.join(_ROOT, "benchmark_real", "images")
GT_PATH = _os.path.join(_ROOT, "benchmark_real", "ground_truth.json")


def _find_file(iid: str):
    for n in _os.listdir(IMG_DIR):
        b = _os.path.splitext(n)[0]
        if b == iid or b.startswith(iid + "_") or iid.startswith(b + "_"):
            return n
    return None


def _metrics(pred, truth):
    inter = int((pred & truth).sum())
    pt = int(truth.sum()); pp = int(pred.sum())
    rec = inter / pt if pt else 0.0
    prec = inter / pp if pp else 0.0
    iou = inter / (pp + pt - inter) if (pp + pt - inter) else 0.0
    cr = pp / pt if pt else 0.0
    return rec, prec, iou, cr, pp, pt, inter


def _run(preset, use_ocr, merge_dilate=None):
    import config  # noqa: E402
    from core.detector import detect_watermark_mask  # noqa: E402
    gt = _json.load(open(GT_PATH, encoding="utf-8"))
    saved = config.DETECT_MERGE_DILATE
    if merge_dilate is not None:
        config.DETECT_MERGE_DILATE = merge_dilate
    rows = []
    try:
        for e in gt["entries"]:
            iid = e["image_id"]
            fn = _find_file(iid)
            if not fn:
                continue
            img = cv2.cvtColor(cv2.imread(_os.path.join(IMG_DIR, fn)), cv2.COLOR_BGR2RGB)
            if img is None:
                continue
            H, W = img.shape[:2]
            truth = np.zeros((H, W), bool)
            for wm in e["watermarks"]:
                x, y, w, h = wm["bbox"]
                truth[y:y + h, x:x + w] = True
            res = detect_watermark_mask(img, preset=preset, use_ocr=use_ocr)
            pred = np.asarray(res.mask) > 0
            rec, prec, iou, cr, pp, pt, inter = _metrics(pred, truth)
            # 按 kind 分解「假阳性面积」= 各 kind 区域框面积 - 与真值重叠（近似）
            kind_area = {}
            for r in getattr(res, "regions", []):
                rx, ry = max(0, r.x), max(0, r.y)
                rw, rh = min(r.w, W - rx), min(r.h, H - ry)
                if rw <= 0 or rh <= 0:
                    continue
                sub_truth = truth[ry:ry + rh, rx:rx + rw]
                fp = int(rw * rh) - int(sub_truth.sum())
                kind_area[r.kind] = kind_area.get(r.kind, 0) + max(0, fp)
            rows.append((iid, rec, prec, iou, cr, pp, pt, kind_area))
    finally:
        config.DETECT_MERGE_DILATE = saved
    return rows


def _summ(rows):
    import statistics as st
    n = len(rows)
    return dict(
        N=n,
        mask_recall=st.mean(r[1] for r in rows),
        precision=st.mean(r[2] for r in rows),
        mask_iou=st.mean(r[3] for r in rows),
        coverage_ratio=st.mean(r[4] for r in rows),
    )


def _report(tag, rows):
    s = _summ(rows)
    print(f"\n===== {tag} =====")
    print(f"N={s['N']}  mask_recall={s['mask_recall']:.4f}  precision={s['precision']:.4f}  "
          f"mask_iou={s['mask_iou']:.4f}  coverage_ratio={s['coverage_ratio']:.2f}")
    # 全局按 kind 汇总假阳性面积
    agg = {}
    for r in rows:
        for k, v in r[7].items():
            agg[k] = agg.get(k, 0) + v
    tot = sum(agg.values()) or 1
    print("过检来源（按 kind 汇总假阳性面积占比）：")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        print(f"    {k:16s} {v:>12d}  ({100*v/tot:5.1f}%)")
    print("coverage_ratio 最高 6（过检重灾区）：")
    for r in sorted(rows, key=lambda r: -r[4])[:6]:
        print(f"    {r[0]:26s} cov_ratio={r[4]:5.1f} recall={r[1]:.3f} pred_px={r[5]} truth_px={r[6]}")
    return s


def main() -> int:
    ap = _arg.ArgumentParser()
    ap.add_argument("--preset", default="ai_logo")
    ap.add_argument("--use-ocr", action="store_true")
    ap.add_argument("--sweep-merge-dilate", action="store_true")
    args = ap.parse_args()
    if not (_os.path.isdir(IMG_DIR) and _os.path.exists(GT_PATH)):
        print("缺 benchmark_real 真值/图片（环境资产缺失，非缺陷）")
        return 2
    base = _run(args.preset, args.use_ocr)
    _report(f"基线 preset={args.preset} use_ocr={args.use_ocr}", base)
    if args.sweep_merge_dilate:
        for md in (0, 2):
            rows = _run(args.preset, args.use_ocr, merge_dilate=md)
            _report(f"DETECT_MERGE_DILATE={md}", rows)
    print("\n判读：收敛过检应看 precision↑ / coverage_ratio↓ 且 mask_recall 不降；"
          "按 kind 分解指认泛滥策略（本次根因=translucent + logo_watermark）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
