# -*- coding: utf-8 -*-
"""D1 GPU 实测（Stage 305）：真机 RTX 5060 上量 detect / full P95。

背景：此前 D1 被标为「阻塞：沙箱为 CPU torch」。本脚本以探针实测裁决该前提，
并**用项目自有门禁口径**在真 GPU 上量时延。若前提为假，则 D1 可收口。

口径对齐 `scripts/perf_probe.py`（不重造指标）：
  · detect  = detect_watermark_mask
  · full    = detect + inpaint（superres 默认关、INFER_CROP_TO_MASK 默认开）
  · 冷启动与热态分离：warmup 后量 P95，否则首帧 EasyOCR/LaMa 加载污染数据
  · 用真实 benchmark_real 图（≥2048²），与既有 CPU 基线同口径可比

输出：GPU 通道结论 + detect/full 的 P50/P95；EXIT=0 表示通道打通且完成测量。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import config  # noqa: E402
from core.device_manager import get_device_info, select_device, use_fp16  # noqa: E402


def pct(vals: list[float], p: float) -> float:
    """最近秩百分位（与既有 perf 口径一致，不做插值平滑）。"""
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = max(1, min(len(s), int(round((p / 100.0) * len(s) + 0.5))))
    return s[k - 1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12, help="量测图片数（默认 12）")
    ap.add_argument("--min-side", type=int, default=2048, help="最短边门槛（默认 2048）")
    ap.add_argument("--warmup", type=int, default=2, help="预热次数（不计入）")
    ap.add_argument("--full", dest="do_full", action="store_true", default=True, help="量 full（默认开）")
    ap.add_argument("--no-full", dest="do_full", action="store_false", help="只量 detect")
    ap.add_argument("--out", default=None, help="结果 JSON 落盘路径")
    ap.add_argument(
        "--allow-cpu",
        action="store_true",
        help="允许在 CPU 模式下测量（作 GPU 的严格 A/B 对照；WME_DEVICE_MODE=cpu 配合使用）",
    )
    args = ap.parse_args()

    print("=" * 72)
    print("D1 · RTX 5060 GPU 实测（detect / full P95）")
    print("=" * 72)

    # ---- 设备裁决 ----
    info = get_device_info()
    handle = select_device("auto")
    print(f"设备      : {info.name} ({info.kind}, vram={info.vram_mb}MB)")
    print(f"torch_dev : {handle.torch_device}")
    print(f"fp16 开关 : DEVICE_USE_FP16={config.DEVICE_USE_FP16} / use_fp16()={use_fp16()}")
    print(f"裁剪修复  : INFER_CROP_TO_MASK={config.INFER_CROP_TO_MASK}")
    print(f"超分      : {getattr(config, 'SUPERRES_ENABLED', 'n/a')}")

    if not info.is_gpu and not args.allow_cpu:
        print("\n结论：CPU 模式（GPU 不可用），无法做 D1 GPU 实测"
              "（如需 CPU 对照测量，加 --allow-cpu）")
        return 2

    import torch

    # ---- 选图：真实 benchmark_real 图，短边≥2048 ----
    img_dir = ROOT / "benchmark_real" / "images"
    paths = []
    if img_dir.is_dir():
        from PIL import Image

        for p in sorted(img_dir.iterdir()):
            if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            try:
                with Image.open(p) as im:
                    w, h = im.size
            except Exception:
                continue
            if min(w, h) >= args.min_side:
                paths.append(p)
    paths = paths[: args.n]
    if not paths:
        print(f"\n未找到 ≥{args.min_side}px 的真实图（目录 {img_dir}），无法测量")
        return 2
    print(f"样本      : {len(paths)} 张真实图（短边 ≥{args.min_side}px）\n")

    from PIL import Image

    from core.detector import detect_watermark_mask
    from core.inpainter import inpaint

    det_times: list[float] = []
    full_times: list[float] = []
    per_img = []

    for i, p in enumerate(paths):
        with Image.open(p) as im:
            rgb = np.array(im.convert("RGB"))

        # ---- 预热（不计入）：首次会加载 EasyOCR/LaMa，冷启动污染 P95 ----
        if i < args.warmup:
            try:
                r = detect_watermark_mask(rgb)
                if args.do_full:
                    inpaint(rgb, r.mask, engine="lama")
            except Exception as exc:  # noqa: BLE001
                print(f"  预热失败 {p.name}: {type(exc).__name__}: {exc}")
            torch.cuda.synchronize() if torch.cuda.is_available() else None
            continue

        t0 = time.perf_counter()
        res = detect_watermark_mask(rgb)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        d_dt = time.perf_counter() - t0

        f_dt = None
        if args.do_full:
            t1 = time.perf_counter()
            try:
                inpaint(rgb, res.mask, engine="lama")
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                f_dt = time.perf_counter() - t1
            except Exception as exc:  # noqa: BLE001
                print(f"  full 失败 {p.name}: {type(exc).__name__}: {exc}")

        det_times.append(d_dt)
        if f_dt is not None:
            full_times.append(f_dt)
        per_img.append(
            {
                "image": p.name,
                "size": [int(rgb.shape[1]), int(rgb.shape[0])],
                "detect_s": round(d_dt, 4),
                "full_s": round(f_dt, 4) if f_dt is not None else None,
                "regions": len(res.regions),
            }
        )
        print(f"  {p.name:<24} {rgb.shape[1]}x{rgb.shape[0]:<5} detect {d_dt:6.3f}s  full {f_dt if f_dt is None else round(f_dt,3)}")

    print("-" * 72)
    summary = {
        "device": info.name,
        "kind": info.kind,
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "use_fp16": bool(use_fp16()),
        "crop_to_mask": bool(config.INFER_CROP_TO_MASK),
        "n_detect": len(det_times),
        "detect_p50": round(pct(det_times, 50), 4) if det_times else None,
        "detect_p95": round(pct(det_times, 95), 4) if det_times else None,
        "n_full": len(full_times),
        "full_p50": round(pct(full_times, 50), 4) if full_times else None,
        "full_p95": round(pct(full_times, 95), 4) if full_times else None,
        "detect_target_p95": 1.5,
        "detect_target_met": bool(det_times and pct(det_times, 95) < 1.5),
        "per_image": per_img,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "per_image"}, ensure_ascii=False, indent=2))
    print(f"\ndetect P95 = {summary['detect_p95']}s（目标 <1.5s → {'达标' if summary['detect_target_met'] else '未达标'}）")
    if summary["full_p95"] is not None:
        print(f"full   P95 = {summary['full_p95']}s")
    mode = "GPU" if info.is_gpu else "CPU（对照组）"
    print(f"结论：{mode} 通道测量完成（device={info.kind}）")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已落盘：{out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())