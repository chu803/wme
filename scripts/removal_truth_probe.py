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

 退出码：**0 通过 / 1 踩红线 / 2 依赖或资产缺失**（阶段 322a 起）。
 317 那一版是「恒 0 的纯诊断」，理由是没有历史基线就画红线会第一天常红；
 三轮 60 用例数字稳定后已升成门禁（红线定义见下方「阶段 322a/323」常量区，取舍理由见 docs/322a、docs/323）。
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

# ---- 阶段 322a：门禁模式（--gate）------------------------------------------------
# 317/321 的 60 用例已跑过三轮，数字稳定，可以把「擦得干净吗」从"有数字"升级为"有守卫"。
# 红线按「类型中位数 × 绝对容差」组织（与 media_gate 的红线家族同一口价；先不做漂移层，
# 数据攒够再升级——同 315 的既定路径）。
BASELINE_PATH = os.path.join(_ROOT, "benchmark_real", "removal_truth_baseline.json")
GATE_LIMIT = 15            # 门禁固定用前 15 张干净真照片（排序稳定，保证可复现）
RECALL_FLOOR_DELTA = 0.05  # R1 检回中位不得比基线低 0.05（绝对容差）
GAIN_FLOOR_DELTA = 0.05    # R2 擦除度中位不得比基线低 0.05
GAIN_ABS_FLOOR = 0.0       # R2b faint_tiled/grid 的擦除度**不得 < 0**（净伤害红线）
NET_HARM_WORST = -0.25     # R2c（阶段 323）：**任何一张图**都不得被擦坏超过水印强度的 25%
COLLATERAL_MAX = 5.0       # R3 误伤中位 ≤ 5.0%（「只动水印区」承诺的守卫）
NET_HARM_CASES = ("faint_tiled", "grid")


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


def _has_ocr() -> bool:
    """EasyOCR 是否可用（只查可导入性，不加载模型，故开销≈0）。"""
    try:
        return importlib.util.find_spec("easyocr") is not None
    except (ImportError, ValueError):
        return False


def _skip_reason() -> str | None:
    """门禁模式的依赖/资产检查（缺 ⇒ 退出码 2，不得假绿成 0）。"""
    try:
        import cv2  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"OpenCV 不可用：{exc}"
    if not _has_ocr():
        # 阶段 322a 实测：用**没装 easyocr 的解释器**跑，`edge_text` 检回中位从 0.992 掉到 0.0、
        # `faint_tiled` 擦除度从 +0.010 掉到 −0.030——数字仍「自洽」，但量的不是交付品。
        # ⇒ 文字检测不在位时这是**环境不合格**（2），不是产品不合格（1），更不是通过（0）。
        return "EasyOCR 不可用：门禁口径要求文字检测在位（请确认用的是项目虚拟环境的解释器）"
    if not os.path.isdir(CLEAN_DIR):
        return f"缺干净真照片目录：{CLEAN_DIR}"
    if not glob.glob(os.path.join(CLEAN_DIR, "clean_*.jpg")):
        return "clean/ 下没有 clean_*.jpg"
    try:
        _load_maker()
    except Exception as exc:  # noqa: BLE001
        return f"水印合成器不可用：{exc}"
    return None


def _gate_medians(rows: list) -> dict:
    """按类型聚合中位数（门禁判据的输入）。"""
    by: dict = {}
    for r in rows:
        if not r.get("valid"):
            continue
        by.setdefault(r["case"], []).append(r)
    out: dict = {}
    for case, rs in by.items():
        gains = [x["removal_gain"] for x in rs if x["removal_gain"] is not None]
        out[case] = {
            "n": len(rs),
            "det_recall": round(float(np.median([x["det_recall"] for x in rs])), 4),
            "removal_gain": round(float(np.median(gains)), 4) if gains else None,
            # 阶段 323：中位数会**藏掉最坏那一张**（15 例里 6 例负功时中位仍可以是正的），
            # 所以同时留一个最坏值给 R2c。
            "worst_gain": round(float(min(gains)), 4) if gains else None,
            "collateral_pct": round(float(np.median([x["collateral_pct"] for x in rs])), 3),
        }
    return out


def _gate_problems(medians: dict, baseline: dict) -> list:
    """纯判据函数：当前中位数 vs 基线中位数 → 红线问题列表（可单测）。"""
    problems: list = []
    base = baseline.get("by_case") or {}
    for case in sorted(base):
        cur = medians.get(case)
        if cur is None:
            problems.append(f"R0 类型 {case} 本轮没有有效用例（基线里有 {base[case].get('n')} 例）")
            continue
        b = base[case]
        if cur["det_recall"] < b["det_recall"] - RECALL_FLOOR_DELTA:
            problems.append(
                f"R1 {case} 检回中位 {cur['det_recall']} < 基线 {b['det_recall']} − {RECALL_FLOOR_DELTA}")
        g, bg = cur["removal_gain"], b["removal_gain"]
        if g is None or bg is None:
            problems.append(f"R2 {case} 擦除度无法计算（cur={g} baseline={bg}）")
        else:
            if g < bg - GAIN_FLOOR_DELTA:
                problems.append(
                    f"R2 {case} 擦除度中位 {g} < 基线 {bg} − {GAIN_FLOOR_DELTA}")
            if case in NET_HARM_CASES and g < GAIN_ABS_FLOOR:
                problems.append(
                    f"R2b {case} 擦除度 {g} < 0（净伤害红线：不许擦完比不擦更差）")
        # R2c（阶段 323）：单图最坏值。中位数过关 ≠ 没有一张图被擦坏——
        # 323 实测 faint_tiled 中位 +0.010 的同时，最差一张是 **−0.191**（15 例里 6 例负功）。
        w = cur.get("worst_gain")
        if w is not None and w < NET_HARM_WORST:
            problems.append(
                f"R2c {case} 单图最坏擦除度 {w} < {NET_HARM_WORST}"
                "（有一张照片被擦坏得超过红线，而中位数把它藏住了）")
        if cur["collateral_pct"] > COLLATERAL_MAX:
            problems.append(
                f"R3 {case} 误伤中位 {cur['collateral_pct']}% > {COLLATERAL_MAX}%"
                "（只动水印区承诺）")
    return problems


def _gate_margins(medians: dict) -> list:
    """红线余量（每类一行）。阶段 323：余量必须每次打印——R2b 只有千分点，
    不写出来就会被读成「绿 = 有富余」，然后在一次无关改动后突然常红。"""
    lines = []
    for case in sorted(medians):
        m = medians[case]
        bits = []
        if m.get("removal_gain") is not None:
            bits.append(f"擦除度余量 {round(m['removal_gain'] - GAIN_ABS_FLOOR, 4)}"
                        f"{'' if case in NET_HARM_CASES else '（不看绝对线）'}")
        if m.get("worst_gain") is not None:
            bits.append(f"最坏单图余量 {round(m['worst_gain'] - NET_HARM_WORST, 4)}")
        bits.append(f"误伤余量 {round(COLLATERAL_MAX - m['collateral_pct'], 2)}%")
        lines.append(f"  {case:<13} " + " | ".join(bits))
    return lines


def run(photos: list, cases: dict, engine: str, json_path: str | None,
        verbose: bool, gate: bool = False, write_baseline: bool = False) -> int:
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
            status = core.get("status")
            if status == "skipped":
                # **必须计入而不是丢弃**：空掩码 = 系统什么都没做 = 输出就是输入。
                # 若把它当「无效用例」排除，检测器一旦集体失明，门禁只会看到样本数变少、
                # 中位数却仍然漂亮 ⇒ R1 被反向喂成绿球（阶段 322a 首轮实测抓到）。
                m = measure(clean, marked, marked, None)
                m.update({"photo": os.path.basename(p), "case": name, "case_cn": cn,
                          "preset_hint": preset, "seconds": sec,
                          "preset_used": core.get("recommended_preset") or "-",
                          "used_ocr": bool(core.get("used_ocr")),
                          "note": "未检出（跳过，输出=输入）"})
                rows.append(m)
                if verbose:
                    print(f"  [{idx}/{len(photos)}] {os.path.basename(p):<14} {name:<12} "
                          f"未检出 ⇒ 擦除度 {m.get('removal_gain')} "
                          f"误伤 {m.get('collateral_pct')}% 检回 0.0 耗时 {sec}s")
                continue
            if status != "success":
                rows.append({"photo": os.path.basename(p), "case": name,
                             "valid": False, "reason": f"管线未成功：{status}",
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
                      "preset_used": core.get("recommended_preset") or "-",
                      # used_ocr：同一份数字是「交付品」还是「降级链路」的唯一可回溯标记
                      # （阶段 322a 预演用过没装 easyocr 的解释器，检回 0.992→0.0 却看不出来）。
                      "used_ocr": bool(core.get("used_ocr"))})
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
    if not gate:
        return 0
    # ---- 门禁模式（阶段 322a）：与基线比对，红线任一破 ⇒ 退出码 1 ----
    medians = _gate_medians(rows)
    try:
        baseline = json.load(open(BASELINE_PATH, encoding="utf-8"))
    except FileNotFoundError:
        if write_baseline:
            print(f"\n基线尚不存在 ⇒ 本轮作为首份基线（--write-baseline）：{BASELINE_PATH}")
            return 0
        print(f"\n❌ 缺基线文件：{BASELINE_PATH}（用 --gate --write-baseline 生成）")
        return 1
    problems = _gate_problems(medians, baseline)
    if problems:
        print("\n红线未过：")
        for p in problems:
            print(f"  ❌ {p}")
        return 1
    print("\n✅ 修复侧门禁通过（检回/擦除度未回退、无净伤害、误伤在承诺内）")
    print("红线余量（越小越脆，改动默认值前先看到这里）：")
    for line in _gate_margins(medians):
        print(line)
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
    undetected = [r for r in rows if str(r.get("note", "")).startswith("未检出")]
    invalid = [r for r in rows if not r.get("valid")]
    if undetected:
        print(f"\n未检出水印的用例 {len(undetected)} 个："
              + ", ".join(f"{r['case']}/{r['photo']}" for r in undetected[:8]))
    if invalid:
        print(f"无效用例 {len(invalid)} 个：" + ", ".join(
            f"{r.get('case')}/{r.get('photo')}（{r.get('reason')}）" for r in invalid[:5]))


def main(argv: list | None = None) -> int:
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()   # 门禁打印 ✅/❌，GBK 管道下会 UnicodeEncodeError（console_encoding 守卫）

    ap = argparse.ArgumentParser(description="修复侧真值探针（真实照片 + 构造保证的像素级真值）")
    ap.add_argument("--limit", type=int, default=5, help="用几张干净真照片（默认 5）")
    ap.add_argument("--cases", default="corner_logo,edge_text,faint_tiled,grid",
                    help="逗号分隔的水印类型（见 make_demo_assets.CASES）")
    ap.add_argument("--engine", default="auto", choices=["auto", "opencv", "lama"],
                    help="修复引擎（默认 auto = 用户实际会得到什么）")
    ap.add_argument("--json", dest="json_path", default=None, help="把逐用例明细写成 JSON")
    ap.add_argument("--quiet", action="store_true", help="只打汇总表，不打逐用例行")
    ap.add_argument("--gate", action="store_true",
                    help="门禁模式：固定 15 图 × 4 类型，与 benchmark_real/removal_truth_baseline.json "
                         "比对红线（检回/擦除度回退、净伤害、误伤承诺）；退出码 0/1/2")
    ap.add_argument("--write-baseline", action="store_true",
                    help="（配合 --gate）把本轮中位数写成新基线后退出 0")
    args = ap.parse_args(argv)

    if args.gate:
        # 门禁口径固定，保证可复现：忽略 --limit/--cases/--engine 的自定义。
        reason = _skip_reason()
        if reason:
            print(f"跳过（依赖/资产缺失）：{reason}")
            return 2
        args.limit = GATE_LIMIT
        args.cases = "corner_logo,edge_text,faint_tiled,grid"
        args.engine = "auto"
        # 写基线需要逐用例明细 ⇒ 未显式给 --json 时落到 cache/（已 gitignore）。
        args.json_path = args.json_path or os.path.join(
            _ROOT, "cache", "removal_truth_gate_rows.json")

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
          f"= {len(photos) * len(cases)} 个用例 | 引擎={args.engine}"
          + (" | 门禁模式" if args.gate else ""))
    rc = run(photos, cases, args.engine, args.json_path, not args.quiet,
             gate=args.gate, write_baseline=args.write_baseline)
    if args.gate and args.write_baseline and rc == 0:
        # 基线 = 本轮（配置必须是当前交付默认：只动水印区开、跟随输入开、引擎 auto）。
        try:
            rows = json.load(open(args.json_path, encoding="utf-8"))["rows"]
        except (OSError, KeyError, ValueError) as exc:
            print(f"❌ 写基线失败：读不到逐用例明细（{exc}）")
            return 1
        medians = _gate_medians(rows)
        if not medians:
            print("❌ 写基线失败：没有可用明细（请同时传 --json）")
            return 1
        payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "engine": args.engine, "limit": GATE_LIMIT,
                   "note": "阶段 322a 基线（323 补 worst_gain 与 R2c）：类型中位数 + 最坏单图；"
                           "红线=检回/擦除度 −0.05、faint_tiled/grid gain≥0、"
                           "任意图 worst_gain≥−0.25、误伤中位≤5%",
                   "by_case": medians}
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"基线已写入：{BASELINE_PATH}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
