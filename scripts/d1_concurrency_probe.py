#!/usr/bin/env python3
# ==============================================================================
# D1 并发吞吐探针 · d1_concurrency_probe（阶段 299 · 用户 RTX 5060 本机实测脚手架）
#
# 为什么需要它
# ------------
# `scripts/perf_probe.py` 只测**串行**口径的 P95（detect / full 各测单图时延），
# 但路线图的 D1（并发策略，对应 `config.API_ASYNC_MAX_WORKERS` worker 池）关注的是
# **并发吞吐**：多张图同时进来时，GPU/LaMa 管线的 img/s 与尾延迟怎么变。
# 串行探针看不到这一点 ⇒ D1 决策（默认 worker=2、区间 [1,8]）缺乏真机数据支撑。
#
# 本探针在**你本机**（managed env，torch 2.9.1+cu128，`WME_DEVICE_MODE=auto` 自动走
# CUDA）跑并发扫描：对每个 worker 数（默认 1/2/4/8），用线程池并发提交 `requests` 张图
# 到全链路 `api.remove_watermark_bytes`，测：
#   · 吞吐 throughput = requests / 总墙钟（img/s）
#   · 单请求时延分布 P50 / P95 / max（并发下尾延迟是否恶化）
#   · 设备指纹（cpu/cuda + gpu_name）+ 峰值显存占用（cuda 可用时）
# 吞吐随 worker 数是否抬升、在哪里饱和，直接告诉 D1 该把默认 worker 设几。
#
# ⚠ 沙箱无 GPU、且本机无 torch 时**无法真跑**（会卡在 `import api`）。本脚本是给你
#   的 RTX 5060 机器用的脚手架：我产出、你本地执行取数。CI 只校验它能被 import
#   （重依赖全部懒加载），不实际跑推理。
#
# 退出码：0 = 跑完并写出报告；2 = 无可用工作量（请放含水印图到 benchmark_real/images/）。
#
# 用法（在你本机、managed env 下）
# --------------------------------
#   # 默认：扫描 worker=1/2/4/8，全链路 lama，每组 16 请求
#   python scripts/d1_concurrency_probe.py
#
#   # 只用真实基准图的前 N 张，改请求数 / worker 列表
#   python scripts/d1_concurrency_probe.py --limit 12 --requests 24 --workers 1,2,4,8
#
#   # 只测检测阶段（CPU 绑定，并发通常无收益，用来对照）
#   python scripts/d1_concurrency_probe.py --stage detect
#
#   # 指定图目录 / 输出 JSON
#   python scripts/d1_concurrency_probe.py --images-dir /path/to/watermarked --out d1.json
#
# 配套 A5（串行 P95）：
#   python scripts/perf_probe.py --stage detect --update
#   python scripts/perf_probe.py --stage full   --update --enforce-target --target 1.5
# ==============================================================================
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.perf_probe import (  # noqa: E402
    iter_workload,
    device_fingerprint,
)
from utils.stats import nearest_rank_percentile  # noqa: E402

DEFAULT_OUT = os.path.join(_ROOT, "benchmark_real", "d1_concurrency.json")
DEFAULT_WORKERS = "1,2,4,8"


def _encode(arr: np.ndarray) -> bytes:
    import cv2  # 懒加载：仅 full 阶段需要
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    return buf.tobytes()


def _call_full(png: bytes, engine: str):
    import api as _api  # 懒加载：重依赖，仅真跑时才 import
    _api.remove_watermark_bytes(png, engine=engine, auto_preset=True,
                                allow_auto_erase=True)


def _call_detect(arr: np.ndarray):
    from core.detector import detect_watermark_mask  # 懒加载
    detect_watermark_mask(arr, preset="general", use_ocr=False)


def _timed(fn, payload):
    """在 callable 内部记录自身起止，返回单请求时延（秒）。"""
    t0 = time.perf_counter()
    fn(payload)
    return time.perf_counter() - t0


def _gpu_peak_mb():
    """cuda 可用时返回本次测量峰值显存(MB)，否则 None。"""
    try:
        import torch
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            mb = torch.cuda.max_memory_allocated(0) / (1024 * 1024)
            return round(mb, 1)
    except Exception:  # noqa: BLE001 - 无 torch / 无 cuda 一律当 None
        pass
    return None


def run_sweep(*, stage: str, items, workers, requests: int, engine: str,
              warmup: bool) -> list:
    """对给定 worker 列表做并发扫描，返回每档报告列表。"""
    # 预编码（full 阶段传 PNG 字节；detect 阶段传数组）。
    if stage == "full":
        payloads = [_encode(arr) for _, arr in items]
        call = lambda p: _call_full(p, engine)  # noqa: E731
    else:
        payloads = [arr for _, arr in items]
        call = _call_detect  # noqa: E731

    # 构造任务序列（轮转铺满 requests 张）。
    tasks = [payloads[i % len(payloads)] for i in range(max(1, requests))]

    rows = []
    for w in workers:
        if warmup and payloads:
            try:
                _timed(call, tasks[0])
            except Exception:  # noqa: BLE001 - 烧热失败不致命
                pass
        t_start = time.perf_counter()
        latencies = []
        with ThreadPoolExecutor(max_workers=w) as ex:
            futs = [ex.submit(_timed, call, p) for p in tasks]
            for f in as_completed(futs):
                latencies.append(f.result())
        wall = time.perf_counter() - t_start
        if not latencies:
            continue
        rows.append({
            "workers": w,
            "requests": len(latencies),
            "wall_s": round(wall, 4),
            "throughput_img_per_s": round(len(latencies) / wall, 4) if wall > 0 else None,
            "latency_p50_s": round(nearest_rank_percentile(latencies, 0.50), 4),
            "latency_p95_s": round(nearest_rank_percentile(latencies, 0.95), 4),
            "latency_max_s": round(max(latencies), 4),
            "latency_mean_s": round(sum(latencies) / len(latencies), 4),
        })
    return rows


def main(argv=None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="D1 并发吞吐探针（需本机 GPU 实跑）")
    ap.add_argument("--stage", choices=("full", "detect"), default="full",
                    help="full=检测+修复全链路(GPU/LaMa)；detect=仅检测(CPU 绑定)")
    ap.add_argument("--engine", default="lama", help="修复引擎（full 阶段）")
    ap.add_argument("--source", choices=("benchmark", "synthetic"), default="benchmark")
    ap.add_argument("--images-dir", default=None, help="基准图目录，默认 benchmark_real/images")
    ap.add_argument("--limit", type=int, default=12, help="采样张数上限")
    ap.add_argument("--requests", type=int, default=16, help="每个 worker 档提交的总请求数")
    ap.add_argument("--workers", default=DEFAULT_WORKERS,
                    help="逗号分隔的 worker 数扫描列表，默认 1,2,4,8")
    ap.add_argument("--size", type=int, default=1024, help="synthetic 兜底图尺寸")
    ap.add_argument("--seed", type=int, default=20261001)
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--out", default=DEFAULT_OUT, help="JSON 报告输出路径")
    args = ap.parse_args(argv)

    try:
        worker_list = [int(x) for x in str(args.workers).split(",") if x.strip()]
    except ValueError:
        print("  [错误] --workers 须为逗号分隔整数，如 1,2,4,8")
        return 2
    if not worker_list:
        worker_list = [1, 2, 4, 8]

    items, source_used = iter_workload(
        source=args.source, limit=args.limit, size=args.size,
        seed=args.seed, images_dir=args.images_dir)
    if not items:
        print("  [跳过] 无可用工作量；请放含水印图到 benchmark_real/images/ 或 --images-dir")
        return 2

    fp = device_fingerprint()
    print(f"  设备指纹: device={fp['device']} gpu={fp['gpu_name']} "
          f"stage={args.stage} source={source_used} 图数={len(items)}")

    # 测量前清一次峰值显存计数（若有）。
    try:
        import torch
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats(0)
    except Exception:  # noqa: BLE001
        pass

    rows = run_sweep(stage=args.stage, items=items, workers=worker_list,
                     requests=args.requests, engine=args.engine,
                     warmup=not args.no_warmup)

    peak = _gpu_peak_mb()
    report = {
        "tool": "d1_concurrency_probe",
        "stage": args.stage,
        "engine": args.engine if args.stage == "full" else None,
        "source": source_used,
        "n_images": len(items),
        "device": fp["device"],
        "gpu_name": fp["gpu_name"],
        "gpu_peak_mem_mb": peak,
        "requests_per_sweep": args.requests,
        "sweep": rows,
    }

    # 控制台摘要表。
    print("\n  worker | 吞吐(img/s) | P50(s) | P95(s) | max(s) | 墙钟(s)")
    print("  -------+-------------+--------+--------+--------+--------")
    for r in rows:
        print(f"  {r['workers']:>5}  | {r['throughput_img_per_s']:>10}   | "
              f"{r['latency_p50_s']:>6} | {r['latency_p95_s']:>6} | "
              f"{r['latency_max_s']:>6} | {r['wall_s']:>6}")
    if peak is not None:
        print(f"  峰值显存: {peak} MB")
    if fp["device"] != "cuda":
        print("  ⚠ 当前 device=cpu：并发收益主要体现在 full(GPU) 阶段；"
              "请在本机 CUDA 环境跑 --stage full 才有意义。")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  报告已写: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
