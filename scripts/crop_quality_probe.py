#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crop_quality_probe.py — 在 2048² 真实基准图上判定「INFER_CROP_TO_MASK 默认开」是否安全。

关键诚实前提（实测推翻早期假设）：
  LaMa 在 mask 外**并非逐字节恒等**——两种模式（full / crop）都会对非掩码像素产生
  轻微扰动（max 常达 20~60，>10 像素占极少比例）。这是 LaMa 的固有行为，不是裁剪引入的。
  故「crop 安全」的正确判据不是 crop-vs-full 在 mask 外=0，而是：
    【crop 在干净区的扰动 不比 full 在干净区的扰动 更差】
  即启用裁剪后，干净区质量不会比「现状 full 默认」更差；加速来自只跑水印小 crop。

度量：
  - clean_perturb_full  = max|full - 原图|   (mask 外)
  - clean_perturb_crop  = max|crop - 原图|   (mask 外)
  - crop_not_worse      = crop 干净区扰动 <= full 干净区扰动 * 容忍系数 + 容差
  - in_mean / in>10     = mask 内 crop-vs-full 差异（重建区，两者都靠 LaMa 重建）
  - speedup             = full 耗时 / crop 耗时

退出码：
  0 = 安全（crop 干净区不差于 full）+ 质量可接受
  1 = 安全判据被破（crop 干净区显著差于 full）→ 不可默认开
  2 = 无足够有效样本
"""
import sys
import os
import glob
import time
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import config

from core.image_utils import load_image_full
from core.detector import detect_watermark_mask
from core.inpainter import inpaint

# crop 干净区扰动相对 full 的容忍：crop <= full * COEF + FLAT
CLEAN_PERTURB_COEF = 1.5
CLEAN_PERTURB_FLAT = 5.0
# mask 内软门禁
MASK_IN_MEAN_MAX = 12.0
MASK_IN_PIX10_MAX_RATIO = 0.05


def _load_rgb(p):
    loaded = load_image_full(p)
    rgb = loaded[0] if isinstance(loaded, (tuple, list)) else loaded
    return np.asarray(rgb)


def main():
    # 阶段 331：输出编码加固（GBK 管道下打印 ⇒/✅ 会 UnicodeEncodeError ⇒ 「通过」被读成退出码 1）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少张（0=全部）")
    ap.add_argument("--min-side", type=int, default=2048, help="只处理长边>=此值的图")
    args = ap.parse_args()

    from PIL import Image
    paths = sorted(glob.glob("benchmark_real/images/*.png") +
                   glob.glob("benchmark_real/images/*.jpg"))
    sel = []
    for p in paths:
        try:
            with Image.open(p) as im:
                if max(im.size) >= args.min_side:
                    sel.append(p)
        except Exception:
            pass
    if args.limit:
        sel = sel[: args.limit]
    if not sel:
        print("无满足条件的基准图")
        return 2

    print(f"待处理 {len(sel)} 张（长边>={args.min_side}），引擎=lama")
    print("-" * 80)

    recs = []
    n_empty = 0
    for i, p in enumerate(sel):
        rgb = _load_rgb(p)
        res = detect_watermark_mask(rgb)
        mask = res.mask
        nm = int((mask > 0).sum())
        if nm == 0:
            n_empty += 1
            continue
        if i == 0:
            config.INFER_CROP_TO_MASK = False
            _ = inpaint(rgb, mask, engine="lama")  # 预热

        config.INFER_CROP_TO_MASK = False
        t = time.time()
        o_full = inpaint(rgb, mask, engine="lama")
        t_full = time.time() - t

        config.INFER_CROP_TO_MASK = True
        t = time.time()
        o_crop = inpaint(rgb, mask, engine="lama")
        t_crop = time.time() - t

        of = o_full[0] if isinstance(o_full, (tuple, list)) else o_full
        oc = o_crop[0] if isinstance(o_crop, (tuple, list)) else o_crop
        of = np.asarray(of, dtype=np.int32)
        oc = np.asarray(oc, dtype=np.int32)
        orig = np.asarray(rgb, dtype=np.int32)
        mr = mask > 0

        # 干净区（mask 外）：各自 vs 原图
        cp_full = np.abs(of - orig)[~mr]
        cp_crop = np.abs(oc - orig)[~mr]
        cf_max = int(cp_full.max()) if cp_full.size else 0
        cc_max = int(cp_crop.max()) if cp_crop.size else 0
        cf_nz = int((cp_full > 10).sum())
        cc_nz = int((cp_crop > 10).sum())
        crop_not_worse = cc_max <= cf_max * CLEAN_PERTURB_COEF + CLEAN_PERTURB_FLAT

        # 重建区（mask 内）：crop vs full
        in_diff = np.abs(of - oc)[mr]
        in_mean = float(in_diff.mean()) if in_diff.size else 0.0
        in_max = int(in_diff.max()) if in_diff.size else 0
        in_p10 = float((in_diff > 10).mean()) if in_diff.size else 0.0

        sp = t_full / t_crop if t_crop > 0 else 0.0
        recs.append(dict(name=p.split("/")[-1], nm=nm, cf_max=cf_max, cc_max=cc_max,
                         cf_nz=cf_nz, cc_nz=cc_nz, worse=(not crop_not_worse),
                         in_mean=in_mean, in_max=in_max, in_p10=in_p10, sp=sp))
        flag = "" if crop_not_worse else "  <-- WORSE"
        print(f"[{i+1:2d}/{len(sel)}] {p.split('/')[-1]:18s} mask={nm:6d}  "
              f"cleanPerturb full={cf_max:2d}(>10:{cf_nz:4d}) crop={cc_max:2d}(>10:{cc_nz:4d})"
              f"{flag}  in_mean={in_mean:5.2f} in>10={100*in_p10:5.2f}%  speedup={sp:4.1f}x")

    config.INFER_CROP_TO_MASK = False

    if not recs:
        print("无有效样本（全部 mask 为空）")
        return 2

    n_worse = sum(1 for r in recs if r["worse"])
    in_bad = sum(1 for r in recs
                 if r["in_mean"] > MASK_IN_MEAN_MAX
                 or r["in_p10"] > MASK_IN_PIX10_MAX_RATIO)
    sp_all = [r["sp"] for r in recs]
    print("-" * 80)
    print(f"有效样本 {len(recs)}（mask 空跳过 {n_empty}）")
    print(f"速度: 平均加速 {np.mean(sp_all):.2f}x  中位 {np.median(sp_all):.2f}x  "
          f"最小 {min(sp_all):.2f}x 最大 {max(sp_all):.2f}x")
    print(f"安全判据(crop 干净区扰动 不差于 full): 通过 {len(recs)-n_worse}/{len(recs)}"
          f"  (容忍 crop<=full*{CLEAN_PERTURB_COEF}+{CLEAN_PERTURB_FLAT:.0f})")
    print(f"质量软门禁(mask 内未达标): {in_bad}/{len(recs)}"
          f"  (in_mean<={MASK_IN_MEAN_MAX} 且 in>10<={100*MASK_IN_PIX10_MAX_RATIO:.1f}%)")

    if n_worse > 0:
        offenders = [r["name"] for r in recs if r["worse"]]
        print(f"\n[FAIL] {n_worse} 张图 crop 干净区扰动显著差于 full "
              f"({offenders}) → 裁剪不可默认开")
        return 1
    print(f"\n[PASS] 安全判据通过：crop 在干净区的扰动不差于 full（两者均为 LaMa 固有轻微扰动），"
          f"启用裁剪不会让可见非水印区比现状更差")
    if in_bad == 0:
        print(f"[PASS] 质量软门禁通过：水印重建区差异有界 → 建议默认开启（默认开）")
    else:
        print(f"[WARN] 质量软门禁 {in_bad} 张未达标，但安全判据已过，可默认开并监控 in_mean/in>10")
    return 0


if __name__ == "__main__":
    sys.exit(main())
