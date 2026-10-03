# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/quality_gate.py
 文件说明 : 端到端质量门禁（阶段 57）。

 为什么需要这个门禁
 ------------------
 阶段 49~56 把检测器质量（误报率 69.7%→0.8%、精确率 7.9%→47.7%）与修复引擎默认选择
 （auto 默认 OpenCV）都做到了「有 benchmark 证据支撑」的最优解。但这些成果此前
 **只由手工脚本与文档维系**：`detector_diag.py` 要人工跑、数字人工抄进 README，
 `run_benchmark.py` 的 ΔPSNR 结论躺在 `docs/51-*` 里，而 `tests/test_benchmark.py`
 只是用**假数据**校验聚合逻辑——也就是说，任何人改坏检测器常量或修复参数，
 全量测试依然全绿，劣化会被静默放行。

 本脚本把「检测 + 修复」的端到端结果固化成**可复现的基线快照**，并提供机器可判定的
 双层约束：

   ① 绝对红线（不管基线怎么变都必须满足，防止基线被劣化结果"刷新"）：
      · 干净图误报率 FPR ≤ 2%
      · 平均精确率 ≥ 35%
      · 平均 IoU ≥ 35%
      · corner_logo / edge_text 两个"应该修得好"的用例 ΔPSNR > 0（修复确有净提升）

   ② 基线漂移（与 `docs/assets/quality_baseline.json` 比对，超出容差即失败）：
      相对容差见 TOLERANCES；用于捕捉"红线之内但明显变差"的慢性劣化。

 判定逻辑刻意做成纯函数 `evaluate()`，不依赖文件系统，便于单测直接喂数据校验。

 用法
 ----
    python scripts/quality_gate.py                 # 跑门禁并与基线比对（越线 exit 1）
    python scripts/quality_gate.py --update        # 刷新基线快照（确认劣化是有意的时候用）
    python scripts/quality_gate.py --quick         # 只跑 photo 底图 + 2 用例，快速迭代
    python scripts/quality_gate.py --with-lama     # 额外跑 LaMa（CPU 单图 3~4s，慢）
    python scripts/quality_gate.py --size 320      # 缩小底图尺寸加速（默认 640）

 依赖：仅 numpy / cv2 / core / scripts 的合成数据生成器（零 gradio，可在 CI 运行）。
==============================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np

# 项目根目录注入 sys.path（允许从任意工作目录运行）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.detector import detect_watermark_mask  # noqa: E402
from core.inpainter import inpaint  # noqa: E402
from core.quality import no_reference_inpaint_score  # noqa: E402
from services.postprocess_service import postprocess  # noqa: E402
from utils.exceptions import EmptyMaskError  # noqa: E402
from utils.path_helper import display_path  # noqa: E402

from scripts.detector_diag import BASE_KINDS, gt_mask, metrics  # noqa: E402
from scripts.make_demo_assets import CASES, mae, psnr  # noqa: E402
from scripts import baseline_guard  # noqa: E402  # 阶段 146：刷新基线护栏（三条门禁共用）

BASELINE_PATH = os.path.join(_ROOT, "docs", "assets", "quality_baseline.json")

# 干净图误报评估用的预设（与 detector_diag.run 保持一致口径）。
_CLEAN_PRESETS = ("general", "ai_faint_tiled", "ai_grid", "ai_logo")

# ---------------------------------------------------------------------------
# 约束定义
# ---------------------------------------------------------------------------

#: ① 绝对红线：键 → (比较方向, 阈值)。"max" 表示不得超过，"min" 表示不得低于。
RED_LINES: dict = {
    "clean_fpr": ("max", 0.02),      # 干净图平均误报率 ≤ 2%
    "precision": ("min", 0.35),      # 平均精确率 ≥ 35%
    "iou": ("min", 0.35),            # 平均 IoU ≥ 35%
}

#: 必须「修复后优于修复前」的用例（ΔPSNR > 0）。
#: faint_tiled 是已知信息论极限（极淡平铺 α≈0.09，任何参数召回 ≤7.4%），
#: grid 在纹理底图上同样受限，故不列入强约束，仅做漂移监控。
REQUIRED_POSITIVE_DELTA = ("corner_logo", "edge_text")

#: ② 基线漂移容差：键 → 最大允许相对下滑比例（0.25 = 允许下滑 25%）。
#: 只约束"变坏"方向——指标变好永远不失败。
TOLERANCES: dict = {
    "clean_fpr": 1.00,     # 误报是绝对量，基线本身接近 0，用 100% 相对容差（红线兜底）
    "precision": 0.20,
    "recall": 0.20,
    "iou": 0.20,
    "delta_psnr": 0.35,    # 修复净提升波动较大，放宽
    "delta_psnr_delivery": 0.35,   # 交付链（含后处理）同理
}

#: ③ 交付链保真红线（阶段 68）：后处理链**不得**让「掩码区（被擦除区域）」的 PSNR
#: 低于裸修复结果。这是阶段 60 用真值审计换来的结论——后处理链曾让角标用例的掩码区
#: PSNR 从 38.4 dB 掉到 11.5 dB（把水印像素按软掩码混回边界）、渐变底图掉 18 dB
#: （全图统计 + 标准差重缩放），而门禁当时只测裸 inpaint、完全看不见。容差仅用于吸收
#: 浮点误差。
DELIVERY_MASKED_MAX_DROP = 0.01

# ---------------------------------------------------------------------------
# 阶段 146 · 刷新基线的护栏参数
# ---------------------------------------------------------------------------
# 背景：`--update` 此前是**静默通道** —— 写完基线**直接 return 0**，
#   绕过绝对红线与漂移判定；且没有任何机制阻止把指标**合法化到更坏的值**。
#   而 `recall` 这类「只靠漂移层保护、没有绝对红线」的指标会被**渐进刷新**：
#       0.819 →(刷) 0.655 →(刷) 0.524 → …  每步都在 20% 容差内 ⇒ 门禁全程绿。
#   （e2e_real_probe 早已处理「刷新绕过判定」，本门禁是漏了对齐。）

#: 刷新护栏的方向表：`"max"` = 越低越好。未列出的键一律按 `"min"`（越高越好）。
_REFRESH_DIRECTIONS: dict = {"clean_fpr": "max"}

#: 近零指标的**绝对**劣化下限（相对容差在零附近会乱响）。
#: 与 `evaluate()` 漂移层里 clean_fpr 的 `+0.01` 绝对上浮口径保持一致。
_REFRESH_ABS_TOL: dict = {"clean_fpr": 0.01}


def _refresh_rules() -> dict:
    """把**既有**漂移容差表 `TOLERANCES` 适配成刷新护栏的规则表。

    刻意不新增任何阈值 —— 保证「漂移层允许的抖动」与「刷新层允许的下滑」
    **同口径**，否则会出现「漂移层判它违规、刷新层却放它写入」的自相矛盾。
    """
    return {
        key: (_REFRESH_DIRECTIONS.get(key, "min"), rel, _REFRESH_ABS_TOL.get(key, 0.0))
        for key, rel in TOLERANCES.items()
    }



def _psnr_region(a: np.ndarray, b: np.ndarray, region: np.ndarray) -> "float | None":
    """只统计 region（bool 掩码）内的 PSNR。

    「擦干净了没」看的是被擦除区域，而不是全图——全图指标按面积加权，会被大片
    未改动区域稀释（阶段 65 的裁决依据）。区域为空时返回 None。
    """
    if region is None or not np.any(region):
        return None
    diff = a.astype(np.float64) - b.astype(np.float64)
    if diff.ndim == 3:
        diff = diff.max(axis=2)
    mse = float(np.mean(diff[region] ** 2))
    if mse <= 1e-9:
        return 100.0
    return float(10.0 * np.log10(255.0 ** 2 / mse))


# ---------------------------------------------------------------------------
# 1. 跑门禁（真实计算）
# ---------------------------------------------------------------------------

def _detect(marked: np.ndarray, preset: str) -> np.ndarray:
    """跑一次检测，返回 0/255 单通道掩码。"""
    res = detect_watermark_mask(marked, preset=preset, use_ocr=False)
    mask = res.mask
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    return mask


def run_gate(
    kinds=("gradient", "textured", "photo"),
    cases=("faint_tiled", "corner_logo", "edge_text", "grid"),
    seed: int = 7,
    size: int = 640,
    engines=("opencv",),
    with_clean: bool = True,
) -> dict:
    """跑完整门禁，返回结构化 summary（不落盘、不做判定）。

    参数说明：
        kinds      : 底图种类，取自 `detector_diag.BASE_KINDS` 的键。
        cases      : 水印用例名，取自 `make_demo_assets.CASES` 的键。
        seed       : 底图随机种子（固定 = 结果可复现）。
        size       : 底图边长（像素）。调小可显著加速，但检测统计会变化，
                     故基线快照会记录 size，换尺寸需重新 `--update`。
        engines    : 修复引擎列表。LaMa 在 CPU 上单图 3~4s，默认不跑。
        with_clean : 是否评估干净图误报率。

    返回结构：
        {
          "config": {...},                 # 本次运行的配置（写入基线，供复现）
          "rows":   [ {...}, ... ],        # 每个 (kind, case, engine) 一条
          "clean":  [ {...}, ... ],        # 每个 (kind, preset) 一条干净图记录
          "aggregate": {...},              # 聚合指标（判定对象）
        }
    """
    rows: list = []
    clean_rows: list = []

    for kind in kinds:
        base_fn = BASE_KINDS[kind]
        base = base_fn(seed, w=size, h=size)

        # (a) 干净图：期望输出空掩码，任何命中都是误报。
        if with_clean:
            for preset in _CLEAN_PRESETS:
                pred = _detect(base, preset)
                m = metrics(pred, np.zeros_like(pred))
                clean_rows.append({
                    "kind": kind, "preset": preset,
                    "fpr": round(m["fpr"], 6),
                    "pred_cov": round(m["pred_cov"], 6),
                })

        # (b) 加水印图：检测 + 修复 + 量化。
        for case in cases:
            wm_fn, preset, label = CASES[case]
            marked = wm_fn(base)
            gt = gt_mask(base, marked)
            pred = _detect(marked, preset)
            det = metrics(pred, gt)

            coverage = float((pred > 0).mean() * 100.0)
            # 未检出掩码时无法修复，只登记检测侧指标（ΔPSNR 记为 None）。
            if coverage <= 0.0:
                rows.append({
                    "kind": kind, "case": case, "case_cn": label, "engine": None,
                    "fpr": round(det["fpr"], 6),
                    "recall": round(det["recall"], 6),
                    "precision": round(det["precision"], 6),
                    "iou": round(det["iou"], 6),
                    "pred_cov": round(det["pred_cov"], 6),
                    "gt_cov": round(det["gt_cov"], 6),
                    "coverage_pct": 0.0,
                    "delta_psnr": None, "mae_repaired": None, "quality_score": None,
                })
                continue

            for engine in engines:
                try:
                    out = inpaint(marked, pred, engine=engine)
                except EmptyMaskError:
                    continue
                try:
                    score = round(float(no_reference_inpaint_score(out, marked, pred)), 4)
                except Exception:  # noqa: BLE001 —— 打分失败不应让门禁整体失败
                    score = None
                # ---- 阶段 68：交付链臂（用户实际拿到的成品 = 裸修复 + 后处理默认链）----
                # 显式关闭可选超分：config.user_overrides.json 可能把真实超分设为默认开启，
                # 那样交付结果会变成 1280²、与 base 不可比（阶段 60 踩过）。此处只评估
                # 「标称默认增强链」（边界柔化 + 掩码外锐化 + 色彩还原[默认关]）。
                delivery = None
                try:
                    cand = postprocess(out, original_rgb=marked, mask=pred,
                                       superres=False, real_superres=False)
                    if cand.shape[:2] == out.shape[:2]:
                        delivery = cand
                except Exception:  # noqa: BLE001 —— 后处理失败不阻断门禁，但会被红线抓到
                    delivery = None
                region = pred > 0
                masked_bare = _psnr_region(out, base, region)
                masked_del = (_psnr_region(delivery, base, region)
                              if delivery is not None else None)
                rows.append({
                    "kind": kind, "case": case, "case_cn": label, "engine": engine,
                    "fpr": round(det["fpr"], 6),
                    "recall": round(det["recall"], 6),
                    "precision": round(det["precision"], 6),
                    "iou": round(det["iou"], 6),
                    "pred_cov": round(det["pred_cov"], 6),
                    "gt_cov": round(det["gt_cov"], 6),
                    "coverage_pct": round(coverage, 2),
                    "psnr_marked": round(psnr(marked, base), 2),
                    "psnr_repaired": round(psnr(out, base), 2),
                    "delta_psnr": round(psnr(out, base) - psnr(marked, base), 2),
                    "mae_repaired": round(mae(out, base), 3),
                    "quality_score": score,
                    "psnr_masked_bare": (round(masked_bare, 2)
                                         if masked_bare is not None else None),
                    "psnr_masked_delivery": (round(masked_del, 2)
                                             if masked_del is not None else None),
                    "masked_drop": (round(masked_bare - masked_del, 2)
                                    if None not in (masked_bare, masked_del) else None),
                    "delta_psnr_delivery": (
                        round(psnr(delivery, base) - psnr(marked, base), 2)
                        if delivery is not None else None),
                })

    return {
        "config": {
            "seed": seed, "size": size,
            "kinds": list(kinds), "cases": list(cases),
            "engines": list(engines),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "rows": rows,
        "clean": clean_rows,
        "aggregate": aggregate(rows, clean_rows),
    }


def aggregate(rows: list, clean_rows: list) -> dict:
    """把明细聚合成判定用的少数几个数字（纯函数，便于单测）。

    口径说明：
        · 检测侧（precision/recall/iou）只在「有 ground truth 水印」的行上平均，
          且对同一 (kind, case) 的多引擎重复只算一次（避免引擎数影响检测指标）；
        · clean_fpr 在全部干净图记录上平均；
        · delta_psnr 只在「修复成功」的行上平均（delta_psnr 非 None）；
        · delta_psnr_delivery 同理，衡量**交付链**（含后处理）的全图净提升；
        · masked_drop 取最差（最大）值——交付链相对裸修复在掩码区的劣化，正数代表更差。
    """
    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(float(np.mean(vals)), 6) if vals else 0.0

    # 检测侧去重：同一 (kind, case) 只取第一条（多引擎共享同一份检测掩码）。
    seen = set()
    det_rows = []
    for r in rows:
        key = (r.get("kind"), r.get("case"))
        if key in seen:
            continue
        seen.add(key)
        det_rows.append(r)

    drops = [r["masked_drop"] for r in rows if r.get("masked_drop") is not None]
    return {
        "clean_fpr": mean([c["fpr"] for c in clean_rows]),
        "precision": mean([r["precision"] for r in det_rows]),
        "recall": mean([r["recall"] for r in det_rows]),
        "iou": mean([r["iou"] for r in det_rows]),
        "delta_psnr": mean([r["delta_psnr"] for r in rows]),
        "delta_psnr_delivery": mean([r.get("delta_psnr_delivery") for r in rows]),
        "masked_drop_worst": round(max(drops), 2) if drops else 0.0,
        "n_cases": len(det_rows),
        "n_repaired": len([r for r in rows if r.get("delta_psnr") is not None]),
        "n_delivery": len(drops),
    }


# ---------------------------------------------------------------------------
# 2. 判定（纯函数）
# ---------------------------------------------------------------------------

def _violate(direction: str, value: float, threshold: float) -> bool:
    """按方向判断是否越线。"""
    if direction == "max":
        return value > threshold
    return value < threshold


def evaluate(summary: dict, baseline: "dict | None" = None) -> "tuple[bool, list[str]]":
    """对 summary 做双层判定，返回 (是否通过, 违规说明列表)。

    纯函数：不读文件、不跑计算，只吃数据结构——便于单测覆盖各种越线情形。

    层次：
        ① 绝对红线（RED_LINES + REQUIRED_POSITIVE_DELTA + 交付链保真）——与基线无关；
        ② 基线漂移（TOLERANCES）——只在提供 baseline 时检查，且只看"变坏"方向。
    """
    problems: list[str] = []
    agg = summary.get("aggregate", {})
    rows = summary.get("rows", [])

    # ① 绝对红线
    for key, (direction, threshold) in RED_LINES.items():
        value = agg.get(key)
        if value is None:
            continue
        if _violate(direction, float(value), threshold):
            problems.append(
                f"[红线] {key}={value:.4f} 越界（要求 {direction} {threshold}）"
            )

    # ① 必赢用例：修复必须有净提升
    for case in REQUIRED_POSITIVE_DELTA:
        vals = [r["delta_psnr"] for r in rows
                if r.get("case") == case and r.get("delta_psnr") is not None]
        if not vals:
            problems.append(f"[红线] 用例 {case} 无任何成功修复结果（无法验证净提升）")
            continue
        worst = min(vals)
        if worst <= 0:
            problems.append(
                f"[红线] 用例 {case} 存在 ΔPSNR≤0 的结果（最差 {worst:+.2f} dB）"
            )

    # ① 交付链保真红线（阶段 68）：后处理不得让掩码区（被擦除区域）比裸修复更差。
    #    阶段 60 的三个缺陷（边界混回原图 / 全图统计色彩还原 / 掩码内锐化）都只在这里
    #    才会暴露——它们改的正是"用户看到的那张图"。
    for r in rows:
        drop = r.get("masked_drop")
        if drop is None:
            continue
        if drop > DELIVERY_MASKED_MAX_DROP:
            problems.append(
                f"[红线] 交付链在掩码区劣于裸修复：{r.get('kind')}/{r.get('case')}"
                f"({r.get('engine')}) 掩码区 PSNR {r['psnr_masked_bare']} → "
                f"{r['psnr_masked_delivery']} dB（劣化 {drop:.2f} dB）"
            )

    # ② 基线漂移
    if baseline:
        base_agg = (baseline or {}).get("aggregate", {})
        cfg_now = summary.get("config", {})
        cfg_base = (baseline or {}).get("config", {})
        if cfg_now.get("size") != cfg_base.get("size") or \
                cfg_now.get("seed") != cfg_base.get("seed"):
            problems.append(
                f"[配置] 基线配置 size={cfg_base.get('size')}/seed={cfg_base.get('seed')} "
                f"与本次 size={cfg_now.get('size')}/seed={cfg_now.get('seed')} 不一致，"
                f"数字不可比（若有意变更请用 --update 刷新基线）"
            )
        for key, rel_tol in TOLERANCES.items():
            now, base = agg.get(key), base_agg.get(key)
            if now is None or base is None:
                continue
            if key == "clean_fpr":
                # 误报是越低越好；基线近 0 时相对容差无意义，只允许绝对上浮。
                if now > base + 0.01:
                    problems.append(
                        f"[漂移] clean_fpr {base:.4f} → {now:.4f} "
                        f"（绝对上浮超过 0.01）"
                    )
                continue
            # 其余指标：允许相对下滑 rel_tol；变好不报错。
            if now < base * (1.0 - rel_tol):
                problems.append(
                    f"[漂移] {key} {base:.4f} → {now:.4f} "
                    f"（下滑 {100.0 * (base - now) / max(base, 1e-9):.1f}%，"
                    f"容差 {100.0 * rel_tol:.0f}%）"
                )

    return (len(problems) == 0), problems


# ---------------------------------------------------------------------------
# 3. 尺寸鲁棒性扫描（阶段 57：分辨率依赖收敛的护栏）
# ---------------------------------------------------------------------------

#: 尺寸扫描档位：从 256² 到 640²（640 是 benchmark/门禁口径，其值必须不被修复改动）。
SWEEP_SIZES = (256, 320, 448, 640)
#: 扫描底图（纹理/照片两类是误报高发区；渐变几乎无纹理，无挑战性）。
SWEEP_KINDS = ("textured", "photo")
#: 尺寸扫描红线：任一尺寸四预设平均干净误报率不得超过该值。
SWEEP_MAX_FPR = 0.02


def sweep_clean_fpr(kinds=SWEEP_KINDS, sizes=SWEEP_SIZES, seed: int = 7) -> dict:
    """对多个底图尺寸扫描干净图误报率（四预设平均），返回结构化结果。

    阶段 57 修复前，干净图误报率存在强分辨率依赖：textured 底图从 896² 的
    0.39% 一路恶化到 256² 的 16.97%（photo 7.89%）。根因是 min_area 的相对
    门限随面积平方缩水、绝对像素尺寸的纹理噪点成群通过（见 docs/57-*）。
    修复后该扫描应全档 ≤ SWEEP_MAX_FPR，且 640 档与修复前完全一致
    （修复只钳小图，不碰 ≥ 锚点的行为）。

    返回：{"config": ..., "rows": [ {size, kind, fpr(四预设平均)} ], "max_fpr": ...}
    """
    rows: list = []
    for size in sizes:
        for kind in kinds:
            base = BASE_KINDS[kind](seed, w=size, h=size)
            fprs = []
            for preset in _CLEAN_PRESETS:
                r = detect_watermark_mask(base, preset=preset, use_ocr=False)
                mask = r.mask
                if mask.ndim == 3:
                    mask = mask[:, :, 0]
                fprs.append(float(np.count_nonzero(mask > 0)) / mask.size)
            rows.append({
                "size": size, "kind": kind,
                "fpr": round(float(np.mean(fprs)), 6),
                "presets": {p: round(f, 6) for p, f in zip(_CLEAN_PRESETS, fprs)},
            })
    return {
        "config": {"seed": seed, "kinds": list(kinds), "sizes": list(sizes),
                   "max_fpr": SWEEP_MAX_FPR},
        "rows": rows,
        "max_fpr": max(r["fpr"] for r in rows),
    }


def evaluate_sweep(sweep: dict) -> "tuple[bool, list[str]]":
    """对尺寸扫描结果做红线判定（纯函数，便于单测）。"""
    problems: list[str] = []
    limit = sweep["config"]["max_fpr"]
    for r in sweep["rows"]:
        if r["fpr"] > limit:
            problems.append(
                f"[尺寸红线] {r['kind']} {r['size']}² 干净误报 "
                f"{r['fpr'] * 100:.2f}% > 允许 {limit * 100:.2f}%"
            )
    return (len(problems) == 0), problems


def sweep_to_markdown(sweep: dict) -> str:
    """把尺寸扫描渲染成 Markdown 表。"""
    lines = ["| 尺寸 | 底图 | 误报率% |", "|---|---|---|"]
    for r in sweep["rows"]:
        lines.append(f"| {r['size']} | {r['kind']} | {r['fpr'] * 100:.2f} |")
    lines.append(f"\n扫描最大误报率：{sweep['max_fpr'] * 100:.2f}% "
                 f"（红线 {sweep['config']['max_fpr'] * 100:.2f}%）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. 渲染与落盘
# ---------------------------------------------------------------------------

def to_markdown(summary: dict) -> str:
    """把门禁结果渲染成 Markdown 表格（可直接贴进文档 / PR）。"""
    agg = summary["aggregate"]
    lines = [
        "| 底图 | 用例 | 引擎 | 覆盖率% | 精确率 | 召回 | IoU | ΔPSNR(裸) | "
        "ΔPSNR(交付) | 掩码区(裸→交付) | 质量分 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in summary["rows"]:
        delta = r.get("delta_psnr")
        delta_txt = f"{delta:+g}" if delta is not None else "-"
        ddel = r.get("delta_psnr_delivery")
        ddel_txt = f"{ddel:+g}" if ddel is not None else "-"
        mb, md = r.get("psnr_masked_bare"), r.get("psnr_masked_delivery")
        masked_txt = f"{mb:g}→{md:g}" if None not in (mb, md) else "-"
        score = r.get("quality_score")
        score_txt = f"{score:+.3f}" if score is not None else "-"
        lines.append(
            f"| {r['kind']} | {r['case_cn']} | {r.get('engine') or '-'} | "
            f"{r.get('coverage_pct', 0):.2f} | {r['precision']:.3f} | "
            f"{r['recall']:.3f} | {r['iou']:.3f} | {delta_txt} | {ddel_txt} | "
            f"{masked_txt} | {score_txt} |"
        )
    lines.append("")
    lines.append(
        f"聚合：干净图误报 {agg['clean_fpr'] * 100:.2f}% · "
        f"精确率 {agg['precision'] * 100:.1f}% · 召回 {agg['recall'] * 100:.1f}% · "
        f"IoU {agg['iou'] * 100:.1f}% · 平均 ΔPSNR {agg['delta_psnr']:+.2f} dB · "
        f"交付链 ΔPSNR {agg.get('delta_psnr_delivery', 0.0):+.2f} dB（掩码区最差劣化 "
        f"{agg.get('masked_drop_worst', 0.0):+.2f} dB）"
    )
    return "\n".join(lines)


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="端到端质量门禁（检测 + 修复）")
    ap.add_argument("--update", action="store_true",
                    help="把本次结果写入基线快照（确认劣化是有意的时候用）")
    ap.add_argument("--force-update", action="store_true",
                    help="与 --update 同用：跳过「向下刷新护栏」显式覆盖基线（阶段 146）。"
                         "仅在确认劣化是有意为之（换算法口径 / 改评测集）时使用")
    ap.add_argument("--quick", action="store_true",
                    help="只跑 photo 底图 + corner_logo/edge_text，用于快速迭代")
    ap.add_argument("--sweep", action="store_true",
                    help="只跑尺寸鲁棒性扫描（干净图误报率 vs 底图尺寸，阶段 57）")
    ap.add_argument("--size", type=int, default=640, help="底图边长（默认 640）")
    ap.add_argument("--seed", type=int, default=7, help="底图随机种子（默认 7）")
    ap.add_argument("--with-lama", action="store_true",
                    help="额外跑 LaMa 引擎（CPU 单图 3~4s，显著变慢）")
    ap.add_argument("--baseline", default=BASELINE_PATH, help="基线快照路径")
    args = ap.parse_args()

    # 尺寸鲁棒性模式：不涉修复引擎与基线，独立判定。
    if args.sweep:
        t0 = time.time()
        sweep = sweep_clean_fpr(seed=args.seed)
        elapsed = time.time() - t0
        print(sweep_to_markdown(sweep))
        print(f"\n耗时 {elapsed:.1f}s")
        ok, problems = evaluate_sweep(sweep)
        if ok:
            print("\n✅ 尺寸鲁棒性扫描通过（全档干净误报 ≤ 红线）")
            return 0
        print("\n❌ 尺寸鲁棒性扫描未通过：")
        for p in problems:
            print(f"  · {p}")
        return 1

    kinds = ("photo",) if args.quick else tuple(BASE_KINDS)
    cases = ("corner_logo", "edge_text") if args.quick else tuple(CASES)
    engines = ("opencv", "lama") if args.with_lama else ("opencv",)

    t0 = time.time()
    summary = run_gate(kinds=kinds, cases=cases, seed=args.seed,
                       size=args.size, engines=engines)
    elapsed = time.time() - t0

    print(to_markdown(summary))
    print(f"\n耗时 {elapsed:.1f}s（size={args.size}, kinds={len(kinds)}, "
          f"cases={len(cases)}, engines={engines}）")

    baseline = None
    if os.path.exists(args.baseline):
        try:
            with open(args.baseline, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"[警告] 基线读取失败（{exc}），跳过漂移比对。")

    if args.update:
        # 阶段 146：刷新基线是**受护栏约束的动作**，不再是静默通道。
        #   ① **向下刷新护栏**：本次结果若把某指标合法化到更坏的值（超出漂移层
        #      容差）⇒ **拒绝写入** 并返回 1；逃生门是显式 `--force-update`。
        #   ② **刷新后继续走判定**（对齐 `e2e_real_probe` 的既有裁决）：
        #      否则刷新动作会绕过绝对红线与漂移层 —— 用户看到「基线已刷新」
        #      + 退出码 0，会以为一切正常。
        if not args.force_update:
            _down = baseline_guard.check_downward_refresh(
                (baseline or {}).get("aggregate") or {},
                summary.get("aggregate") or {},
                _refresh_rules(),
            )
            if _down:
                for _line in baseline_guard.render_refusal(
                        _down, baseline_label="合成门禁基线"):
                    print(_line)
                return 1
        os.makedirs(os.path.dirname(args.baseline), exist_ok=True)
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n基线已刷新 → {display_path(args.baseline, _ROOT)}")
        print("（刷新后继续按红线与漂移判定：刷新动作不得成为绕过判定的通道）")
        # 用**刚写入的**基线做漂移比对（差值恒为 0），与 e2e 同口径 ——
        # 本次运行据此仍回答「新基线是否满足绝对红线」。
        baseline = summary

    ok, problems = evaluate(summary, baseline)
    if baseline is None:
        print("\n[提示] 未找到基线快照，本次只做绝对红线判定；"
              f"跑 `python scripts/quality_gate.py --update` 建立基线。")

    if ok:
        print("\n✅ 质量门禁通过")
        return 0

    print("\n❌ 质量门禁未通过：")
    for p in problems:
        print(f"  · {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
