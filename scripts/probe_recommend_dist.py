# -*- coding: utf-8 -*-
"""阶段 450：跑 recommend 全量分布 + 关键信号分数，验证 corner_watermark 判据有效性。"""
from __future__ import annotations
import os as _os
import sys as _sys
import json as _json

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from scripts.console_encoding import force_utf8_stdio  # noqa: E402
force_utf8_stdio()

from core.preset_recommender import recommend_preset_for_path  # noqa: E402

IMG_DIR = _os.path.join(_ROOT, "benchmark_real", "images")


def main() -> int:
    names = sorted(
        n for n in _os.listdir(IMG_DIR)
        if n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
    )
    dist = {}
    rows = []
    for n in names:
        p = _os.path.join(IMG_DIR, n)
        try:
            rec = recommend_preset_for_path(p)
        except Exception as e:  # noqa: BLE001
            print(f"! {n}: ERR {type(e).__name__}: {e}")
            continue
        dist[rec.preset] = dist.get(rec.preset, 0) + 1
        s = rec.scores
        rows.append((n, rec.preset, s.get("corner_watermark", 0), s.get("grid", 0),
                     s.get("text", 0), s.get("solid_logo", 0),
                     s.get("signature", 0)))
    print("=== 预设分布 ===")
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {k:16s} {v:3d}")
    print(f"  合计 {len(rows)}")
    print("\n=== 触发 corner_watermark 或 grid 的图片 ===")
    for n, preset, ct, grid, text, logo, sig in rows:
        if ct >= 1.0 or grid >= 1.0:
            print(f"  {n:32s} -> {preset:14s} corner_watermark={ct} grid={grid} "
                  f"text={text} solid_logo={logo} signature={sig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
