# -*- coding: utf-8 -*-
"""
方向 A · 真实图「三格对比图」生成器
===================================

职责
----
对 `benchmark_real` 中每张图的**每个真值水印框**，生成一张三格对比图：

    +------------+------------+------------+
    | 原始带水印  | OpenCV     | LaMa       |
    +------------+------------+------------+

为什么要有这个工具
------------------
数值指标会骗人。阶段 A 的教训：
- `masked_psnr`（掩码区 vs 原图）量的是「改写量」，把「擦得干净」判成负分；
- LaMa 值域缺陷下掩码区整片变白，而当时**没有一张修复结果被人眼看过**。

故本工具与 `real_benchmark_decision.py` 配套：数值给方向，肉眼给结论。
每张图的水印框是**分开裁剪**的（而不是取全图包围盒），因为同一张图上
左上角小标与右下角文字的风格差异极大，混在一张图里看不出问题。

CLI
---
    # 全部有真值的图 -> benchmark_real/results/visual/
    python scripts/make_real_compare_crops.py

    # 只跑前 5 张、真值框周边各留 40px、并额外生成总览图
    python scripts/make_real_compare_crops.py --limit 5 --pad 40 --sheet

输出文件
--------
    每个水印框一张 `{image_id}_{mark_idx}_{style}.png`；
    `--sheet` 时额外生成 `contact_sheet.png`（纵向堆叠的总览图）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

BENCH_DIR = os.environ.get("WME_BENCH_DIR") or os.path.join(_ROOT, "benchmark_real")
MANIFEST_PATH = os.path.join(BENCH_DIR, "manifest.json")
TRUTH_PATH = os.path.join(BENCH_DIR, "ground_truth.json")
DEFAULT_OUT = os.path.join(BENCH_DIR, "results", "visual")

PANEL_TITLES = ("original", "opencv", "lama")
GAP = 8
BG = 32


def _load_inputs() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """读 manifest 与 ground_truth，返回 (有真值的图列表, image_id -> 真值条目)。"""
    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    truth = json.load(open(TRUTH_PATH, encoding="utf-8"))
    truth_by_id = {e["image_id"]: e for e in truth.get("entries", [])}
    labeled = [im for im in manifest.get("images", []) if im["id"] in truth_by_id]
    return labeled, truth_by_id


def _watermarks(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """取真值条目里的水印列表（兼容 `watermarks` / `regions` 两种字段名）。"""
    return list(entry.get("watermarks", entry.get("regions", [])))


def _rect_mask(
    shape: Tuple[int, int, int], bbox: List[int], pad: int
) -> Optional[Any]:
    """由单个真值框生成矩形掩码（外扩 pad 像素，夹到图内）。"""
    import numpy as np

    h, w = shape[:2]
    x, y, bw, bh = (int(v) for v in bbox)
    if bw <= 0 or bh <= 0:
        return None
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    mask = np.zeros((h, w), np.uint8)
    mask[y0:y1, x0:x1] = 255
    return mask


def _label(panel: Any, text: str, font_scale: float = 0.85) -> Any:
    """在单格左上角写标题（先描黑边再写白字，深浅底都可读）。"""
    import cv2

    out = panel.copy()
    org = (10, 30)
    cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def _hstack(panels: List[Any]) -> Any:
    """横向拼接多格（带分隔缝，高度不齐时补底）。"""
    import numpy as np

    h = max(p.shape[0] for p in panels)
    norm = []
    for p in panels:
        if p.shape[0] < h:
            pad = np.full((h - p.shape[0], p.shape[1], 3), BG, np.uint8)
            p = np.vstack([p, pad])
        norm.append(p)
    sep = np.full((h, GAP, 3), BG, np.uint8)
    pieces: List[Any] = []
    for i, p in enumerate(norm):
        if i:
            pieces.append(sep)
        pieces.append(p)
    return np.hstack(pieces)


def main() -> int:
    ap = argparse.ArgumentParser(description="真实图三格对比图生成器")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help=f"输出目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（0 = 全部）")
    ap.add_argument("--ids", default="", help="逗号分隔的 image_id 白名单，如 01,14,23")
    ap.add_argument("--pad", type=int, default=24,
                    help="真值框外扩像素（**掩码**外扩；取景见 --view，建议 --view ≥ --pad）")
    ap.add_argument("--view", type=int, default=70,
                    help="对比图额外留白像素（纯取景，不进掩码；小于 --pad 时会被夹紧到 --pad）")
    ap.add_argument("--sheet", action="store_true", help="额外生成 contact_sheet.png 总览图")
    args = ap.parse_args()

    if not (os.path.exists(MANIFEST_PATH) and os.path.exists(TRUTH_PATH)):
        print("[error] manifest.json 或 ground_truth.json 不存在", file=sys.stderr)
        return 1

    import numpy as np
    from core.image_utils import load_image, save_image
    from core.inpainter import inpaint

    labeled, truth_by_id = _load_inputs()
    if args.ids:
        allow = {s.strip() for s in args.ids.split(",") if s.strip()}
        labeled = [im for im in labeled if im["id"] in allow]
    if args.limit > 0:
        labeled = labeled[: args.limit]
    if not labeled:
        print("[error] 没有可处理的图（检查 --ids / --limit）", file=sys.stderr)
        return 2

    # 阶段 160：取景必须**完整覆盖被修复区域**（mask = bbox ± pad）。
    # 旧实现 crop = bbox ± view 且 view/pad 相互独立；当 view < pad（而 --pad 帮助文本
    # 又写着「掩码与取景同步」，易诱导）时，crop 会**整幅落在 mask 内** ⇒ 对比图里既没有
    # 水印也没有上下文，肉眼复核被静默误导，而退出码仍是 0。探针实测：pad=40,view=10 时
    # crop 内落在 mask 的列比例 = 1.00（正常应 < 1，两翼各留原图上下文）。
    # 现强制 eff_view = max(view, pad)，保证 crop ⊇ mask；被夹紧时显式告警。
    eff_view = max(args.view, args.pad)
    if args.view < args.pad:
        print(f"[warn] --view({args.view}) < --pad({args.pad})：取景已夹紧到 {eff_view}px，"
              f"以完整覆盖被修复区域（否则对比图看不到水印与上下文）。", file=sys.stderr)

    os.makedirs(args.out_dir, exist_ok=True)
    rows: List[Any] = []
    made = 0

    for im in labeled:
        iid = im["id"]
        rel = im["path"]
        abs_p = rel if os.path.isabs(rel) else os.path.join(BENCH_DIR, rel)
        try:
            img = load_image(abs_p)
        except Exception as e:
            print(f"[error] 读图失败 {iid}: {e}", file=sys.stderr)
            continue

        h, w = img.shape[:2]
        marks = _watermarks(truth_by_id[iid])
        for mi, reg in enumerate(marks):
            bbox = reg.get("bbox")
            if not bbox:
                continue
            style = reg.get("style", "unknown")
            mask = _rect_mask(img.shape, bbox, pad=args.pad)
            if mask is None:
                continue

            repaired: Dict[str, Optional[Any]] = {}
            for eng in ("opencv", "lama"):
                try:
                    repaired[eng] = inpaint(img, mask, engine=eng)
                except Exception as e:
                    print(f"[warn] {iid}#{mi} {eng} 修复失败：{e}", file=sys.stderr)
                    repaired[eng] = None

            # 取景框：真值框再外扩 --view 像素（纯视觉留白，比掩码的外扩更大）
            x, y, bw, bh = (int(v) for v in bbox)
            # 取景用 eff_view（≥ pad），保证把被修复区域整幅纳入（阶段 160）
            x0, y0 = max(0, x - eff_view), max(0, y - eff_view)
            x1, y1 = min(w, x + bw + eff_view), min(h, y + bh + eff_view)

            panels = [_label(img[y0:y1, x0:x1], f"orig {iid}#{mi}")]
            for eng in ("opencv", "lama"):
                r = repaired[eng]
                if r is None:
                    panels.append(_label(np.full_like(img[y0:y1, x0:x1], 60), f"{eng} (failed)"))
                else:
                    panels.append(_label(r[y0:y1, x0:x1], eng))

            composite = _hstack(panels)
            path = os.path.join(args.out_dir, f"{iid}_{mi}_{style}.png")
            save_image(path, composite)
            made += 1
            rows.append(composite)

    if args.sheet and rows:
        # 总览图：统一缩放到统一宽度后纵向堆叠
        import cv2

        target_w = 1400
        scaled = []
        for r in rows:
            scale = target_w / r.shape[1]
            scaled.append(
                cv2.resize(r, (target_w, max(1, int(r.shape[0] * scale))),
                           interpolation=cv2.INTER_AREA)
            )
            scaled.append(np.full((12, target_w, 3), BG, np.uint8))
        sheet = np.vstack(scaled[:-1])
        sheet_path = os.path.join(args.out_dir, "contact_sheet.png")
        save_image(sheet_path, sheet)
        print(f"总览图：{sheet_path}")

    print(f"完成：{made} 张对比图 -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
