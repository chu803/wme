"""无参考质量指标的一致性校验（阶段 56，诚实验证）。

为什么需要这个脚本
------------------
阶段 56 新增 `core/quality.no_reference_inpaint_score`，用于「双跑择优」：
在没有 ground truth 时挑出修复质量更好的引擎。但本指标是**启发式**，
真实照片上的选优效力无法仅凭合成数据证明。

本脚本用**合成数据已知的 ground truth** 做一致性校验：
  · 同一用例分别用 opencv / lama 修复，得到两版结果；
  · 用全参考 ΔPSNR（修复 vs 真值）判定「ground-truth 赢家」；
  · 用 no_reference_inpaint_score 判定「指标推选家」；
  · 统计两者一致率。

诚实声明
--------
阶段 51 已证明合成数据上 OpenCV 8/8 占优、LaMa 全负。因此本校验里
「指标也选 OpenCV」在很大程度上是 trivial 的——它只能证明指标**方向正确、
未被写反**，而**不能**证明指标能在真实难图上正确挑出 LaMa。真实选优效力
仍待 P0 真实图验证（把图放进目录后跑 `make_demo_assets.py --input-dir`）。

用法：
  python scripts/validate_selector.py
  python scripts/validate_selector.py --seeds 1,2,3,4,5
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import make_demo_assets as mda  # noqa: E402
from core.quality import no_reference_inpaint_score  # noqa: E402
from utils.exceptions import EmptyMaskError  # noqa: E402


def _one(name: str, seed: int, base_fn, data_kind: str) -> "dict | None":
    apply_wm, preset, cn = mda.CASES[name]
    clean = base_fn(seed)
    marked = apply_wm(clean)

    res = mda.detect_watermark_mask(marked, preset=preset)
    mask = res.mask
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    if (mask > 0).mean() <= 0.0:
        return None

    out = {}
    for eng in ("opencv", "lama"):
        try:
            out[eng] = mda.inpaint(marked, mask, engine=eng)
        except Exception:  # noqa: BLE001
            out[eng] = None
    if out.get("opencv") is None:
        return None  # opencv 必可用，否则无意义

    row = {"case": name, "case_cn": cn, "data": data_kind, "seed": seed}
    for eng in ("opencv", "lama"):
        o = out.get(eng)
        if o is None:
            row[f"dpsnr_{eng}"] = None
            row[f"score_{eng}"] = None
            continue
        row[f"dpsnr_{eng}"] = round(mda.psnr(o, clean) - mda.psnr(marked, clean), 2)
        try:
            row[f"score_{eng}"] = round(no_reference_inpaint_score(o, marked, mask), 4)
        except Exception:  # noqa: BLE001
            row[f"score_{eng}"] = None
    return row


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="无参考质量指标一致性校验")
    ap.add_argument("--seeds", default="1,2,3,4",
                    help="逗号分隔的种子列表（默认 1,2,3,4）")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    bases = [("gradient", mda.make_base), ("textured", mda.make_textured_base)]
    rows = []
    for seed in seeds:
        for dk, base_fn in bases:
            for name in mda.CASES:
                r = _one(name, seed, base_fn, dk)
                if r is not None:
                    rows.append(r)

    if not rows:
        print("没有任何用例产出有效结果。")
        return 1

    agree = 0
    total = 0
    ties = 0
    invalid = 0
    print("\n" + "=" * 96)
    print("无参考指标 vs ground-truth ΔPSNR 一致性校验")
    print("（★ 一致 / ✗ 相悖 / = 平局·退化 / — 数据缺失；平局与缺失均**不计入**一致性）")
    print("| 数据 | 用例 | OCV ΔPSNR | LaMa ΔPSNR | OCV分 | LaMa分 | 一致 |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        do = r.get("dpsnr_opencv")
        dl = r.get("dpsnr_lama")
        so = r.get("score_opencv")
        sl = r.get("score_lama")
        # 只有两引擎都有效才计入一致性。
        if do is None or dl is None or so is None or sl is None:
            flag = "—"
            invalid += 1
        elif do == dl or so == sl:
            # 阶段 159：退化（真值或指标无判别力，平局）。平局不携带方向信息，
            # 若计入一致性会凭空抬高一致率（极端：全平局 ⇒ 100% 却零信息），
            # 与「证明指标方向未被写反」的脚本目的相悖。故单列、不计入。
            flag = "="
            ties += 1
        else:
            gt = "opencv" if do > dl else "lama"
            mt = "opencv" if so > sl else "lama"
            ok = (gt == mt)
            flag = "★" if ok else "✗"
            if ok:
                agree += 1
            total += 1
        print(f"| {r['data']} | {r['case_cn']} | {do} | {dl} | {so} | {sl} | {flag} |")

    print("-" * 96)
    if total:
        print(f"一致性：{agree}/{total} = {agree / total * 100:.1f}%"
              f"（平局·退化 {ties} 例、数据缺失 {invalid} 例，未计入）")
    else:
        print(f"⚠ 无携带方向信息的有效样本（平局·退化 {ties} 例、数据缺失 {invalid} 例）"
              f"—— 无法评估一致性，切勿读成「一致」。")
    print("=" * 96)
    print("\n诚实说明：合成数据上 OpenCV 8/8 占优，本一致率主要证明「指标方向未被写反」，")
    print("不证明其在真实照片难图上能正确挑出 LaMa。真实选优效力待 P0 真实图验证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
