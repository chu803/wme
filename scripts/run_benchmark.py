"""LaMa vs OpenCV 全量 benchmark 驱动（阶段 49 检测器修复后）。

与 `make_demo_assets.py --compare` 的区别：
  · 多种子（默认 4 个）取均值/标准差，结论更稳健；
  · 同时跑「纯渐变」与「类自然纹理」两种底图，分别检验两个引擎的舒适区；
  · 产出 docs/assets/benchmark.json（逐条）+ docs/assets/benchmark_summary.json（聚合）；
  · 顺带落几张代表性四联对比图供人工检视。

用法：
  python scripts/run_benchmark.py
  python scripts/run_benchmark.py --seeds 1,2,3,4,5
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from scripts import make_demo_assets as mda  # noqa: E402

OUT_DIR = os.path.join(_ROOT, "docs", "assets")
BASE_KINDS = {
    "gradient": (mda.make_base, "纯渐变"),
    "textured": (mda.make_textured_base, "类自然纹理"),
}


def _agg(rows):
    """按 (data, case, engine) 聚合 ΔPSNR / 覆盖率 / MAE 的均值与标准差。"""
    import numpy as np

    groups = {}
    for m in rows:
        key = (m["data"], m["case"], m["engine"])
        groups.setdefault(key, []).append(m)

    out = []
    for (dk, case, eng), ms in groups.items():
        dps = np.array([m["delta_psnr"] for m in ms], float)
        cov = np.array([m["coverage_pct"] for m in ms], float)
        mae = np.array([m["mae_repaired"] for m in ms], float)
        out.append({
            "data": dk,
            "data_cn": BASE_KINDS[dk][1],
            "case": case,
            "case_cn": mda.CASES[case][2],
            "engine": eng,
            "n": len(ms),
            "delta_psnr_mean": float(dps.mean()),
            "delta_psnr_std": float(dps.std()),
            "coverage_mean": float(cov.mean()),
            "mae_mean": float(mae.mean()),
        })
    return out


def _compare_table(summary):
    """产出「同用例下 LaMa − OpenCV 的 ΔPSNR 差值」表。"""
    by = {(s["data"], s["case"], s["engine"]): s for s in summary}
    rows = []
    for dk in ("gradient", "textured"):
        for case, (_, _, cn) in mda.CASES.items():
            a = by.get((dk, case, "opencv"))
            b = by.get((dk, case, "lama"))
            if a and b:
                rows.append({
                    "data": dk,
                    "data_cn": BASE_KINDS[dk][1],
                    "case": case,
                    "case_cn": cn,
                    "opencv_dpsnr": a["delta_psnr_mean"],
                    "lama_dpsnr": b["delta_psnr_mean"],
                    "diff": b["delta_psnr_mean"] - a["delta_psnr_mean"],
                })
    return rows


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1,2,3,4",
                    help="逗号分隔的种子列表（默认 1,2,3,4）")
    ap.add_argument("--save-samples", action="store_true",
                    help="额外落几张代表性四联对比图")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    engines = ["opencv", "lama"]

    os.makedirs(OUT_DIR, exist_ok=True)
    import cv2

    rows = []
    sample_specs = [
        ("gradient", "corner_logo"),
        ("gradient", "grid"),
        ("textured", "faint_tiled"),
        ("textured", "edge_text"),
    ]
    saved = set()

    for seed in seeds:
        for dk, (base_fn, _) in BASE_KINDS.items():
            for name in mda.CASES:
                for eng in engines:
                    m, canvas = mda.run_case(
                        name, eng, seed=seed, base_fn=base_fn, data_kind=dk
                    )
                    if m is None:
                        print(f"[跳过] {dk}/{name}/{eng}/seed{seed}：未检出掩码")
                        continue
                    m["seed"] = seed
                    rows.append(m)
                    # 落代表性样本图：opencv 四联（上） + lama 四联（下），直观对比两引擎。
                    if args.save_samples and (dk, name) not in saved and eng == "opencv":
                        prefix = "tex_" if dk == "textured" else ""
                        path = os.path.join(
                            OUT_DIR, f"bench_{prefix}{name}_seed{seed}.png"
                        )
                        canvas_ocv = canvas  # 当前 eng=="opencv" 已算出的四联图
                        _, canvas_lama = mda.run_case(
                            name, "lama", seed=seed, base_fn=base_fn, data_kind=dk
                        )
                        h, w = canvas_ocv.shape[:2]
                        label = np.zeros((28, w, 3), np.uint8)
                        cv2.putText(label, f"{BASE_KINDS[dk][1]} / {mda.CASES[name][2]}",
                                    (12, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                                    (255, 255, 255), 2, cv2.LINE_AA)
                        combined = np.vstack([label, canvas_ocv, canvas_lama])
                        cv2.imwrite(path, combined)
                        saved.add((dk, name))
                        print(f"[样本] {os.path.relpath(path, _ROOT)}")

    if not rows:
        print("没有任何用例产出有效结果。")
        return 1

    summary = _agg(rows)
    cmp = _compare_table(summary)

    with open(os.path.join(OUT_DIR, "benchmark.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "benchmark_summary.json"), "w", encoding="utf-8") as f:
        json.dump({
            "seeds": seeds,
            "per_case_engine": summary,
            "lama_minus_opencv": cmp,
        }, f, ensure_ascii=False, indent=2)

    # 打印结论摘要。
    print("\n" + "=" * 82)
    print("各用例 ΔPSNR（均值，越高越好）：  OpenCV vs LaMa，差值=LaMa−OpenCV")
    print("| 数据 | 用例 | OpenCV | LaMa | 差值 |")
    print("|---|---|---|---|---|")
    for r in cmp:
        flag = "✓LaMa" if r["diff"] > 0.5 else ("✓OCV" if r["diff"] < -0.5 else "≈")
        print(f"| {r['data_cn']} | {r['case_cn']} | {r['opencv_dpsnr']:+.2f} | "
              f"{r['lama_dpsnr']:+.2f} | {r['diff']:+.2f} | {flag} |")

    # 整体胜负。
    wins = sum(1 for r in cmp if r["diff"] > 0.5)
    losses = sum(1 for r in cmp if r["diff"] < -0.5)
    print("-" * 82)
    print(f"LaMa 占优用例：{wins}    OpenCV 占优用例：{losses}    "
          f"持平：{len(cmp) - wins - losses}")
    print("=" * 82)
    print(f"\n明细已写入 docs/assets/benchmark.json（{len(rows)} 条）")
    print(f"聚合已写入 docs/assets/benchmark_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
