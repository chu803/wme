# -*- coding: utf-8 -*-
"""真实「平铺/点阵」水印样本的**成对性校验 + 当前检测召回测量**（阶段 349）。

为什么需要这个脚本：任务 #21（提升 grid 点阵水印的检测召回）被「真实样本」卡住，
而样本的**成对性**是能不能用的分水岭（`docs/P0` §六）：只有「同一张文件的两种导出」
（像素逐位对齐、只多一层水印叠印）才能得到构造保证的像素真值
（`truth = |带水印 − 无水印| ≥ 1`）；重新生成的「很像的」图当无水印版 ⇒ 真值里全是画面差异
（317 之前两次接入成对图都因此被判**不可用**）。

用法（把成对样本放进 pairs 目录，每一对一个子目录）：
    pairs/
      pair1/  watermarked.png   clean.png
      pair2/  watermarked.jpg   clean.jpg
    （也接受扁平的 pair1_wm.png + pair1_clean.png 命名）

    python scripts/grid_sample_check.py --pairs D:\\wme-1.0.3\\samples_grid\\pairs
    python scripts/grid_sample_check.py --pairs <目录> --measure   # 顺带测当前检测召回

退出码：
    0 = 全部成对可用（且 --measure 时已给出召回）
    1 = 存在不可用项（逐条打印原因）
    2 = 参数/目录问题（目录为空、找不到成对文件等）

⚠ 本脚本**只读**样本目录，不修改、不删除任何文件。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.console_encoding import force_utf8_stdio  # noqa: E402

#: 成对样本的硬性要求（`docs/P0` §六 / §一）
MIN_LONG_EDGE = 1024
#: 差异像素占比上限：超过它多半**不是同一张图的两种导出**（而是两张不同的画面）
MAX_DIFF_RATIO = 0.30
#: 差异像素占比下限：低于它说明两张一样（没有水印叠印）
MIN_DIFF_RATIO = 0.0005
#: 建议的成对数量（`docs/P0` §六：≥20 张 grid）
DEFAULT_MIN_PAIRS = 20

_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def _load(path: Path):
    """读图（中文路径安全：np.fromfile + imdecode）。返回 RGB uint8。"""
    import numpy as np
    import cv2

    buf = np.fromfile(str(path), dtype=np.uint8)
    if buf.size == 0:
        raise ValueError("文件为空")
    arr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("无法解码（格式不支持或文件损坏）")
    return cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)


def find_pairs(root: Path) -> list:
    """找出成对组。两种形态都支持：子目录 {watermarked, clean}，或扁平的 *_wm / *_clean。"""
    pairs = []
    if not root.is_dir():
        return pairs
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        wm = _find_one(sub, ("watermarked", "wm", "带水印"))
        cl = _find_one(sub, ("clean", "no_watermark", "无水印"))
        if wm and cl:
            pairs.append((sub.name, wm, cl))
    flat = {}
    for f in sorted(root.glob("*")):
        if not f.is_file() or f.suffix.lower() not in _IMAGE_EXT:
            continue
        stem = f.stem
        for tag in ("_watermarked", "_wm", "_clean", "_nowm"):
            if stem.lower().endswith(tag):
                key = stem[: -len(tag)].lower()
                flat.setdefault(key, {})[tag] = f
                break
    for key, m in flat.items():
        wm = m.get("_watermarked") or m.get("_wm")
        cl = m.get("_clean") or m.get("_nowm")
        if wm and cl:
            pairs.append((key, wm, cl))
    return pairs


def _find_one(folder: Path, names: tuple):
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.suffix.lower() not in _IMAGE_EXT:
            continue
        stem = f.stem.lower()
        if any(stem == n or stem.startswith(n + "_") or stem.endswith("_" + n) for n in names):
            return f
    return None


def check_pair(name: str, wm_path: Path, cl_path: Path) -> dict:
    """校验一对样本，返回 {ok, reasons, truth_ratio, truth_pixels, wm, clean}。"""
    import numpy as np

    out = {"name": name, "ok": True, "reasons": [], "truth_ratio": 0.0,
           "truth_pixels": 0, "wm": None, "clean": None}
    try:
        wm = _load(wm_path)
        clean = _load(cl_path)
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["reasons"].append(f"读取失败：{exc}")
        return out

    out["wm"], out["clean"] = wm, clean
    if wm.shape != clean.shape:
        out["ok"] = False
        out["reasons"].append(
            f"尺寸不一致（{wm.shape[1]}×{wm.shape[0]} vs {clean.shape[1]}×{clean.shape[0]}）"
            "⇒ 不是像素对齐的同一张图，真值不可用")
        return out

    long_edge = max(wm.shape[0], wm.shape[1])
    if long_edge < MIN_LONG_EDGE:
        out["ok"] = False
        out["reasons"].append(f"长边 {long_edge} < {MIN_LONG_EDGE}（要求原图导出、不裁剪）")

    truth = np.abs(wm.astype(np.int16) - clean.astype(np.int16)).max(axis=2)
    n_truth = int((truth >= 1).sum())
    ratio = n_truth / max(1, truth.size)
    out["truth_pixels"] = n_truth
    out["truth_ratio"] = ratio
    if n_truth == 0:
        out["ok"] = False
        out["reasons"].append("两张图完全相同 ⇒ 没有水印叠印")
    elif ratio > MAX_DIFF_RATIO:
        out["ok"] = False
        out["reasons"].append(
            f"差异像素占比 {ratio * 100:.1f}% > {MAX_DIFF_RATIO * 100:.0f}%"
            "⇒ 疑似两张不同画面，不是同一张图的两种导出")
    return out


def measure_recall(results: list, preset: str = "ai_grid") -> dict:
    """对可用的成对样本跑检测器，报告**真值区被检出的比例**（= #21 要提升的召回基线）。"""
    import numpy as np

    from core.detector import detect_watermark_mask

    recs = []
    for r in results:
        if not r["ok"]:
            continue
        truth = (np.abs(r["wm"].astype(np.int16) - r["clean"].astype(np.int16)).max(axis=2)) >= 1
        try:
            res = detect_watermark_mask(r["wm"], sensitivity=0.5, use_ocr=False, preset=preset)
            det = res.mask > 0
        except Exception as exc:  # noqa: BLE001
            recs.append({"name": r["name"], "recall": None, "error": str(exc)})
            continue
        t = int(truth.sum())
        hit = int((det & truth).sum())
        recs.append({"name": r["name"], "recall": (hit / t) if t else None,
                     "truth_px": t, "det_px": int(det.sum()), "hit_px": hit,
                     "regions": len(res.regions)})
    return {"preset": preset, "per_pair": recs}


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="真实平铺/点阵水印样本的成对性校验 + 召回测量")
    ap.add_argument("--pairs", required=True, help="成对样本目录（每对一个子目录，或扁平命名）")
    ap.add_argument("--min-pairs", type=int, default=DEFAULT_MIN_PAIRS,
                    help=f"建议的最少对数（默认 {DEFAULT_MIN_PAIRS}）")
    ap.add_argument("--measure", action="store_true", help="顺带测量当前检测召回（需 torch）")
    ap.add_argument("--preset", default="ai_grid", help="测量用的检测预设（默认 ai_grid）")
    args = ap.parse_args(argv)

    root = Path(args.pairs).expanduser()
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2
    pairs = find_pairs(root)
    if not pairs:
        print(f"没找到成对样本：{root}\n"
              "期望形态一：<pairs>/<名字>/watermarked.png + clean.png\n"
              "期望形态二：<pairs>/<名字>_watermarked.png + <名字>_clean.png", file=sys.stderr)
        return 2

    print(f"找到 {len(pairs)} 对候选样本（目录：{root}）")
    print(f"{'名称':<28}{'可用':<6}{'真值像素':>10}{'差异占比':>10}  说明")
    results = []
    for name, wm, cl in pairs:
        r = check_pair(name, wm, cl)
        results.append(r)
        print(f"{name[:26]:<28}{'是' if r['ok'] else '否':<6}"
              f"{r['truth_pixels']:>10}{r['truth_ratio'] * 100:>9.2f}%  "
              f"{'；'.join(r['reasons'])}")

    good = [r for r in results if r["ok"]]
    print(f"\n可用 {len(good)} / {len(results)} 对"
          f"（目标 ≥{args.min_pairs}）"
          + ("" if len(good) >= args.min_pairs else " ⇒ 还没到可推进 #21 的量"))

    if args.measure and good:
        m = measure_recall(good, args.preset)
        print(f"\n当前检测召回（预设 {m['preset']}，真值 = |带水印 − 无水印| ≥ 1）")
        vals = []
        for p in m["per_pair"]:
            if p.get("recall") is None:
                print(f"  {p['name'][:26]:<28} 测不了：{p.get('error', '未知')}")
                continue
            vals.append(p["recall"])
            print(f"  {p['name'][:26]:<28} 召回 {p['recall'] * 100:5.1f}%  "
                  f"（真值 {p['truth_px']} px / 检出 {p['det_px']} px / 命中 {p['hit_px']} px / 区域 {p['regions']}）")
        if vals:
            vals_sorted = sorted(vals)
            med = vals_sorted[len(vals_sorted) // 2]
            print(f"  中位召回 {med * 100:.1f}%（{len(vals)} 对）"
                  "  ← 这是 #21 要提升的那个数")
    return 0 if len(good) >= args.min_pairs else 1


if __name__ == "__main__":
    raise SystemExit(main())
