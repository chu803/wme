#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台
 文件名称 : scripts/removal_truth_probe.py
 文件说明 : 阶段 317 · **修复侧真值探针**——第一次能回答「擦干净了吗」。

 为什么缺它（缺口是有据可查的，不是感觉）
 --------------------------------------
 项目已有的效果证据全在**检测侧**：`real_gate` 的 mask_recall/precision、
 `quality_gate` 的 ΔPSNR。而 ΔPSNR 的用例底图是 `make_demo_assets` **程序生成的
 渐变/噪声图（640²）**——它证明的是「合成底图上误差变小了」，不是「真实照片上水印没了」。
 真实照片那一侧：`benchmark_real/images` 有水印但没有它的「未加水印版本」，
 成对图两次接入均判**不可用**（非像素配对，见 2026-10-19/21 记录与 git log）。
 ⇒ 「用户拿一张真照片，擦完到底干不干净」这个问题**至今没有一个数字**。

 本探针怎么把这个问题变成可算的
 --------------------------------
 `benchmark_real/clean/` 里躺着 15 张**真实无水印照片**（2048²，阶段 180 作金标准负样本
 接入，此前只用于「不得误报」，从未用于「修复得好不好」）。把它们作为底图，用项目**自带的**
 水印合成器（`make_demo_assets.CASES`）叠上已知水印：

   clean（真照片） --wm--> marked（带水印） ；truth = 确实被改动的像素

 `truth` 由 `marked` 与 `clean` 逐像素相减得到，**由构造保证像素级精确**（不需要人工标注，
 也不依赖任何检测器）。于是同一次运行能同时量出两根轴：

   检测轴  det_recall / det_precision —— 对 truth 而非对人工 bbox（比 human box 更严）
   修复轴  removal_gain = 1 - 残留/水印强度 —— truth 内是否回到了 clean
           collateral  = truth **外**的改动 —— 「只动水印区」的尺子（阶段 316 演练暴露 47%~88%）

 走的是**用户路径**：`process_image(auto_preset=True)`，预设由推荐器决定、引擎由 auto 决定，
 不强制、不给掩码。这样量到的就是真实交付质量，而不是给模型开小灶。

 退出码：**恒 0（纯诊断，第一版不设红线）**。
 理由与 `media_gate` 一致——没有历史基线就画红线，第一天就会常红、然后被人关掉。
 跑一段时间、把 `--json` 产出攒成基线后，再谈红线与 `run_all_checks` 接入。
==============================================================================
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

CLEAN_DIR = os.path.join(_ROOT, "benchmark_real", "clean")
TRUTH_MIN = 1          # truth 判据：|marked-clean| 的通道最大值 ≥ 1（由构造保证，非阈值猜测）
COLLATERAL_PCT_MIN = 2  # 「 collateral 有改动」的最小可见差
DILATE = 24            # truth 外扩多少才算「用户没要求动」
STRENGTH_MIN = 3.0     # 水印强度低于此值时 removal_gain 无意义（分母太小）


def _load_maker():
    """按路径加载 `scripts/make_demo_assets`（scripts/ 不在分发包里，不能用包导入）。"""
    spec = importlib.util.spec_from_file_location(
        "make_demo_assets", os.path.join(_ROOT, "scripts", "make_demo_assets.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def truth_mask(clean: np.ndarray, marked: np.ndarray) -> np.ndarray:
    """像素级真值：合成前后**确实被改变**的像素。由构造保证，无需标注。"""
    d = np.abs(marked.astype(np.int16) - clean.astype(np.int16)).max(axis=2)
    return d >= TRUTH_MIN


def _dilate(m: np.ndarray, px: int) -> np.ndarray:
    """真值外扩（collateral 的排除带）。

    ⚠ 必须显式给 `borderValue=0`：`cv2.dilate` 的默认边界值是 `morphologyDefaultBorderValue`
    （= DBL_MAX），对**膨胀**而言等价于「图外全是前景」⇒ 沿整张图四边凭空生出一条
    `px` 宽的实心带（128² 上实测左上角 20×20 被判成外扩区，阶段 317 守卫抓到）。
    不修则 collateral 会**少算一整圈画面边缘**（2048² 下约 4.7%）。
    """
    import cv2
    if px <= 0:
        return m
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * px + 1, 2 * px + 1))
    d = cv2.dilate(m.astype(np.uint8), k, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    return d > 0


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    if mse <= 1e-9:
        return 99.0
    return 10.0 * np.log10(255.0 * 255.0 / mse)


def measure(clean: np.ndarray, marked: np.ndarray, res: np.ndarray,
            det: np.ndarray | None) -> dict:
    """三方比对（clean 真值 / marked 输入 / res 输出）→ 一根根可解释的数字。"""
    t = truth_mask(clean, marked)
    tin = int(t.sum())
    if tin == 0:                       # 合成器没改动任何像素 = 用例本身坏了，不是产品坏了
        return {"valid": False, "reason": "truth 为空（水印合成无效）"}

    out = _dilate(t, DILATE)
    inside = t
    outside = ~out

    # ⚠ uint8 相减会**回绕**（250−251 得 255 而非 −1），必须先升位再取绝对值；
    #    否则 residual/collateral 会被虚高到不可用（同族：truth_mask 用 int16 的原因）。
    diff_in = np.abs(marked.astype(np.int16) - clean.astype(np.int16)).max(axis=2)
    diff_res = np.abs(res.astype(np.int16) - clean.astype(np.int16)).max(axis=2)
    strength = float(diff_in[inside].mean())
    residual = float(diff_res[inside].mean())
    gain = None if strength < STRENGTH_MIN else round(1.0 - residual / strength, 3)

    coll = diff_res[outside]
    d = {
        "valid": True,
        "truth_pct": round(100.0 * tin / t.size, 2),
        "wm_strength": round(strength, 2),
        "residual": round(residual, 2),
        "removal_gain": gain,                       # 1.0 = truth 内完全回到真值
        "collateral_mad": round(float(coll.mean()), 2) if coll.size else 0.0,
        "collateral_pct": round(100.0 * float((coll >= COLLATERAL_PCT_MIN).mean()), 2)
        if coll.size else 0.0,
        "delta_psnr": round(psnr(res, clean) - psnr(marked, clean), 2),
    }
    if det is None:
        d.update({"det_recall": 0.0, "det_precision": None, "det_coverage_pct": 0.0})
        d["note"] = "未检出任何水印"
    else:
        inter = float(np.logical_and(det > 0, t).sum())
        d["det_recall"] = round(inter / tin, 3)
        d["det_precision"] = round(inter / max(1.0, float((det > 0).sum())), 3)
        d["det_coverage_pct"] = round(100.0 * float((det > 0).mean()), 2)
    return d


def run(photos: list, cases: dict, engine: str, json_path: str | None,
        verbose: bool) -> int:
    from services.auto_pipeline import process_image
    import cv2

    rows = []
    t_all = time.time()
    for idx, p in enumerate(photos, 1):
        bgr = cv2.imread(p)
        if bgr is None:
            print(f"  跳过（读不回）：{os.path.basename(p)}")
            continue
        clean = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        for name, (wm, preset, cn) in cases.items():
            marked = wm(clean)
            t0 = time.time()
            core = process_image(marked, mode="auto", engine=engine, auto_preset=True)
            sec = round(time.time() - t0, 1)
            if core.get("status") != "success":
                rows.append({"photo": os.path.basename(p), "case": name,
                             "valid": False, "reason": f"管线未成功：{core.get('status')}",
                             "seconds": sec})
                continue
            res = core["result_rgb"]
            if res.shape[:2] != clean.shape[:2]:
                res = cv2.resize(res, (clean.shape[1], clean.shape[0]),
                                 interpolation=cv2.INTER_AREA)
            m = measure(clean, marked, res, core.get("mask"))
            m.update({"photo": os.path.basename(p), "case": name, "case_cn": cn,
                      "preset_hint": preset, "seconds": sec,
                      # 键名以 process_image 的真实返回为准（阶段 317 实测：没有 "preset" 键，
                      # 推荐结果在 "recommended_preset"；写错键名会静默得到 "auto" 而非报错）。
                      "preset_used": core.get("recommended_preset") or "-"})
            rows.append(m)
            if verbose:
                print(f"  [{idx}/{len(photos)}] {os.path.basename(p):<14} {name:<12} "
                      f"强度 {m.get('wm_strength'):>5} 残留 {m.get('residual'):>5} "
                      f"擦除度 {str(m.get('removal_gain')):>6} "
                      f"误伤 {m.get('collateral_pct'):>6}% "
                      f"检回 {m.get('det_recall')} 耗时 {sec}s")
    print(f"\n总耗时 {time.time() - t_all:.1f}s，用例 {len(rows)} 个")
    print_table(rows)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "photos": [os.path.basename(x) for x in photos],
                       "engine": engine, "dilate": DILATE,
                       "truth_min": TRUTH_MIN, "rows": rows}, f, ensure_ascii=False, indent=2)
        print(f"JSON 已写入：{json_path}")
    return 0


def print_table(rows: list) -> None:
    """按水印类型汇总（真实照片上的修复侧体检单）。"""
    by: dict = {}
    for r in rows:
        if r.get("valid"):
            by.setdefault(r["case"], []).append(r)
    print(f"\n{'类型':<14}{'n':>3}{'擦除度(中位)':>13}{'残留':>8}{'误伤%':>9}"
          f"{'检回(中位)':>12}{'检准(中位)':>12}{'ΔPSNR':>8}{'秒':>7}")
    print("-" * 88)
    for case in sorted(by):
        g = [x["removal_gain"] for x in by[case] if x["removal_gain"] is not None]
        rc = [x["det_recall"] for x in by[case]]
        pr = [x["det_precision"] for x in by[case] if x["det_precision"] is not None]
        md = lambda v: "-" if not v else round(float(np.median(v)), 3)  # noqa: E731
        print(f"{case:<14}{len(by[case]):>3}{md(g):>13}"
              f"{round(float(np.median([x['residual'] for x in by[case]])), 2):>8}"
              f"{round(float(np.median([x['collateral_pct'] for x in by[case]])), 2):>9}"
              f"{md(rc):>12}{md(pr):>12}"
              f"{round(float(np.median([x['delta_psnr'] for x in by[case]])), 2):>8}"
              f"{round(float(np.median([x['seconds'] for x in by[case]])), 1):>7}")
    undetected = [r for r in rows if r.get("note") == "未检出任何水印"]
    invalid = [r for r in rows if not r.get("valid")]
    if undetected:
        print(f"\n未检出水印的用例 {len(undetected)} 个："
              + ", ".join(f"{r['case']}/{r['photo']}" for r in undetected[:8]))
    if invalid:
        print(f"无效用例 {len(invalid)} 个：" + ", ".join(
            f"{r.get('case')}/{r.get('photo')}（{r.get('reason')}）" for r in invalid[:5]))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="修复侧真值探针（真实照片 + 构造保证的像素级真值）")
    ap.add_argument("--limit", type=int, default=5, help="用几张干净真照片（默认 5）")
    ap.add_argument("--cases", default="corner_logo,edge_text,faint_tiled,grid",
                    help="逗号分隔的水印类型（见 make_demo_assets.CASES）")
    ap.add_argument("--engine", default="auto", choices=["auto", "opencv", "lama"],
                    help="修复引擎（默认 auto = 用户实际会得到什么）")
    ap.add_argument("--json", dest="json_path", default=None, help="把逐用例明细写成 JSON")
    ap.add_argument("--quiet", action="store_true", help="只打汇总表，不打逐用例行")
    args = ap.parse_args(argv)

    if not os.path.isdir(CLEAN_DIR):
        print(f"跳过：缺干净真照片目录 {CLEAN_DIR}")
        return 0
    photos = sorted(glob.glob(os.path.join(CLEAN_DIR, "clean_*.jpg")))[: max(1, args.limit)]
    if not photos:
        print("跳过：clean/ 下没有 clean_*.jpg")
        return 0

    mk = _load_maker()
    want = [c.strip() for c in args.cases.split(",") if c.strip()]
    cases = {k: mk.CASES[k] for k in want if k in mk.CASES}
    if not cases:
        print(f"跳过：用例名都不认识，可选 {sorted(mk.CASES)}")
        return 0
    print(f"修复侧真值探针 · 真照片 {len(photos)} 张 × 水印类型 {len(cases)} 种 "
          f"= {len(photos) * len(cases)} 个用例 | 引擎={args.engine}")
    return run(photos, cases, args.engine, args.json_path, not args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
