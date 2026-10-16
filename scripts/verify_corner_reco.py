# -*- coding: utf-8 -*-
"""阶段 450：校验 corner_watermark 判据 —— 角标真值图是否→ai_logo，且无自然图误命中。"""
from __future__ import annotations
import os as _os
import os
import sys as _sys
import json as _json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from scripts.console_encoding import force_utf8_stdio  # noqa: E402
force_utf8_stdio()

from core.preset_recommender import recommend_preset_for_path  # noqa: E402

IMG_DIR = os.path.join(_ROOT, "benchmark_real", "images")
GT = _json.load(open(os.path.join(_ROOT, "benchmark_real", "ground_truth.json")))


def _find_file(iid: str):
    # 直接匹配，或前缀匹配（gt id 如 'ai_logo_02_snow_mountain' → 文件 'ai_logo_02_snow_mountain_lake.jpg'）。
    cand = []
    for n in os.listdir(IMG_DIR):
        base = os.path.splitext(n)[0]
        if base == iid or base.startswith(iid + "_") or iid.startswith(base + "_") or base.startswith(iid):
            cand.append(n)
    if not cand:
        return None
    # 优先精确
    for n in cand:
        if os.path.splitext(n)[0] == iid:
            return n
    return cand[0]


def _is_br(bb) -> bool:
    if not bb:
        return False
    x, y, w, h = bb
    # 近似 2048/2560 尺寸：右下角判定
    return x > 1000 and y > 1400


def main() -> int:
    gt_rows = []
    for e in GT["entries"]:
        iid = e["image_id"]
        for w in e["watermarks"]:
            gt_rows.append((iid, w.get("style"), tuple(w.get("bbox") or [])))

    reco_cache = {}
    def reco(name):
        if name not in reco_cache:
            try:
                reco_cache[name] = recommend_preset_for_path(os.path.join(IMG_DIR, name))
            except Exception as ex:  # noqa: BLE001
                reco_cache[name] = f"ERR:{ex}"
        return reco_cache[name]

    # 1) 角标真值图推荐分布
    br_hit = 0
    br_total = 0
    non_br_total = 0
    non_br_ailogo = 0
    miss = []
    for iid, style, bb in gt_rows:
        fn = _find_file(iid)
        if not fn:
            miss.append(iid)
            continue
        r = reco(fn)
        if isinstance(r, str):
            print(f"  ! {iid} ({fn}): {r}")
            continue
        if _is_br(bb):
            br_total += 1
            if r.preset == "ai_logo":
                br_hit += 1
            else:
                print(f"  [右下角标未→ai_logo] {iid} ({fn}) -> {r.preset} "
                      f"corner_watermark={r.scores.get('corner_watermark')} grid={r.scores.get('grid')} "
                      f"solid_logo={r.scores.get('solid_logo')} text={r.scores.get('text')}")
        else:
            non_br_total += 1
            if r.preset == "ai_logo":
                non_br_ailogo += 1

    print(f"\n右下角标真值图：{br_hit}/{br_total} 正确推荐 ai_logo")
    print(f"非右下角标真值图：{non_br_total} 张；其中被推 ai_logo 的 {non_br_ailogo} 张（a1-a4 等非右下角，误命中需关注）")
    if miss:
        print(f"未匹配到文件的 gt id（{len(miss)}）：{miss[:20]}")

    # 2) corner_watermark 误命中自然图？遍历全量，列出触发 corner_watermark 但非角标真值图
    print("\n=== 全量中触发 corner_watermark 的图片 ===")
    fp = 0
    for n in sorted(os.listdir(IMG_DIR)):
        if not n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        r = reco(n)
        if isinstance(r, str):
            continue
        if r.scores.get("corner_watermark", 0) >= 1.0:
            # 是否在 gt 角标集合
            in_gt = any(_find_file(iid) == n for iid, _, _ in gt_rows)
            tag = "GT角标" if in_gt else "非GT(疑似误命中)"
            if not in_gt:
                fp += 1
            print(f"  {n:34s} -> {r.preset:14s} {tag}")
    print(f"\ncorner_watermark 非GT误命中数：{fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
