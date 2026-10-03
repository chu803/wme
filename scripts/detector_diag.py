"""
检测器诊断脚本 —— 量化「过度检测」问题，并为修复提供回归基线。

为什么需要这个脚本
------------------
项目 48 个阶段的检测器测试只验证「不报错、流程通、覆盖率在 0~1 之间」，
从未验证一件最基本的事：

    **一张没有水印的图，检测器应该输出空掩码。**

这就是「过度检测」缺陷能一直潜伏到今天的原因——没有基线，缺陷无法被测出来。
本脚本把检测质量拆成两个可度量的量：

    · 误报率  FPR = 干净图上被标记为水印的像素占比（应接近 0）
    · 召回率  Recall = 真值水印像素中被命中的比例（应接近 1）
    · 精确率  Precision = 被标记像素中真的是水印的比例（应接近 1）

一个健康的检测器应当是「低 FPR + 高 Recall」。当前版本是「高 FPR + 高 Recall」——
靠把整图都标成水印来换取召回，这不是检测，是投降。

底图种类
--------
三种底图逐档加大难度，用来定位检测器在什么条件下崩溃：

    gradient  纯四角渐变（最舒适区，无纹理）
    textured  渐变 + 分形纹理（类自然照片的中频/高频能量）
    photo     渐变 + 纹理 + 大尺度暗角与光斑（最接近真实照片）

用法
----
    python scripts/detector_diag.py                 # 全量诊断，输出 Markdown 表
    python scripts/detector_diag.py --json out.json # 同时导出 JSON
    python scripts/detector_diag.py --quick         # 只跑关键组合，快速迭代

说明：不依赖 gradio / torch，纯 numpy + opencv，可在 CI 环境运行。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

# 项目根目录注入 sys.path（允许从任意工作目录运行）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.detector import detect_watermark_mask  # noqa: E402
from scripts.make_demo_assets import (  # noqa: E402
    CASES,
    _fractal_noise,
    make_base,
    make_textured_base,
)


# ---------------------------------------------------------------------------
# 1. 底图生成（第三种：photo，最接近真实照片）
# ---------------------------------------------------------------------------

def make_photo_base(seed: int, w: int = 640, h: int = 640) -> np.ndarray:
    """
    合成一张「最像真实照片」的底图：渐变 + 分形纹理 + 大尺度明暗斑块 + 暗角。

    与 make_textured_base 的区别：
        · textured 只有纹理（中高频能量），缺少真实照片的「大尺度明暗结构」。
        · photo 额外叠加了低频光斑与暗角（vignetting），这会让基于局部统计的
          检测器在中低频上产生系统性偏差——正是真实场景中最常见的误报来源。

    返回值：H×W×3 uint8 RGB 数组。
    """
    rng = np.random.default_rng(seed + 20_000)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = xx / max(w - 1, 1)
    v = yy / max(h - 1, 1)

    # 柔和整体明暗（四角渐变）。
    cols = rng.uniform(60, 200, size=(4, 3)).reshape(4, 1, 1, 3)
    top = cols[0] * (1 - u[:, :, None]) + cols[1] * u[:, :, None]
    bot = cols[2] * (1 - u[:, :, None]) + cols[3] * u[:, :, None]
    img = (top * (1 - v[:, :, None]) + bot * v[:, :, None]).astype(np.float32)

    # 大尺度光斑：3~4 个随机高斯亮/暗块，模拟主体照明。
    for _ in range(rng.integers(3, 5)):
        cy, cx = rng.uniform(0.15, 0.85, size=2)
        sy = rng.uniform(0.10, 0.30)
        sx = rng.uniform(0.10, 0.30)
        amp = rng.uniform(-45, 55)
        d2 = ((v - cy) / sy) ** 2 + ((u - cx) / sx) ** 2
        img += (amp * np.exp(-d2))[:, :, None]

    # 暗角（vignetting）：中心亮、四角暗，几乎所有真实照片都有。
    rr = np.sqrt((u - 0.5) ** 2 + (v - 0.5) ** 2) / 0.7071
    img *= (1.0 - 0.35 * rr ** 2)[:, :, None]

    # 纹理（与 textured 同款，但幅度略低，避免完全被纹理主导）。
    lum = _fractal_noise(h, w, rng, octaves=5, base_scale=8)
    img += (lum - 0.5)[:, :, None] * 70
    for c in range(3):
        tex = _fractal_noise(h, w, rng, octaves=5, base_scale=8 + c * 5)
        img[:, :, c] += (tex - 0.5) * 32 * (0.85 + 0.15 * c)

    # 轻微高斯噪声（传感器噪声），这是压垮过度敏感检测器的最后一根稻草。
    img += rng.normal(0, 1.6, (h, w, 1)).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


BASE_KINDS = {
    "gradient": make_base,
    "textured": make_textured_base,
    "photo": make_photo_base,
}


# ---------------------------------------------------------------------------
# 2. 指标
# ---------------------------------------------------------------------------

def gt_mask(base: np.ndarray, marked: np.ndarray) -> np.ndarray:
    """
    由「底图 vs 加水印图」的逐像素差反推水印真值掩码。

    说明：合成水印是以 alpha 合成的 RGBA 图层，凡是与底图有差异的像素
    即为水印覆盖处。用 max(diff) > 0 判定而非阈值化，保证真值尽可能干净。
    """
    diff = np.abs(marked.astype(np.int16) - base.astype(np.int16)).max(axis=2)
    return (diff > 0).astype(np.uint8) * 255


def metrics(pred: np.ndarray, gt: np.ndarray) -> dict:
    """
    计算预测掩码相对真值的 FPR / Recall / Precision / IoU，以及真值覆盖率。

    参数说明：
        pred : 检测器输出的 0/255 掩码。
        gt   : 真值 0/255 掩码（干净图上为全 0）。

    返回值：各指标字典（均为 0~1 比例，另附百分比便于阅读）。
    """
    p = pred > 0
    g = gt > 0
    total = p.size
    tp = int(np.count_nonzero(p & g))
    fp = int(np.count_nonzero(p & ~g))
    fn = int(np.count_nonzero(~p & g))
    gsum = int(np.count_nonzero(g))

    return {
        "fpr": fp / total,                            # 误报率：干净像素被标成水印
        "recall": (tp / gsum) if gsum else 0.0,       # 召回：真值水印被命中比例
        "precision": (tp / (tp + fp)) if (tp + fp) else 0.0,
        "iou": (tp / (tp + fp + fn)) if (tp + fp + fn) else 0.0,
        "pred_cov": int(np.count_nonzero(p)) / total,  # 预测覆盖率
        "gt_cov": gsum / total,                        # 真值覆盖率
    }


# ---------------------------------------------------------------------------
# 3. 诊断主流程
# ---------------------------------------------------------------------------

def run(quick: bool = False) -> list:
    """
    跑完整诊断：三种底图 × 四类水印 ×（干净 / 加水印）。

    参数说明：
        quick : True 时只跑 photo 底图，用于快速迭代。

    返回值：结果字典列表，每项对应一次检测。
    """
    kinds = ["photo"] if quick else list(BASE_KINDS)
    rows: list = []
    seed = 7

    for kind in kinds:
        base_fn = BASE_KINDS[kind]
        base = base_fn(seed)

        # (a) 干净图 —— 期望输出空掩码。这是本脚本最核心的一组。
        for preset in ("general", "ai_faint_tiled", "ai_grid", "ai_logo"):
            res = detect_watermark_mask(base, preset=preset, use_ocr=False)
            m = metrics(res.mask, np.zeros_like(res.mask))
            rows.append({
                "base": kind, "case": "(clean)", "preset": preset, **m,
            })

        # (b) 加水印图 —— 期望命中水印且不误伤背景。
        for case_name, (wm_fn, preset, _label) in CASES.items():
            marked = wm_fn(base)
            g = gt_mask(base, marked)
            res = detect_watermark_mask(marked, preset=preset, use_ocr=False)
            m = metrics(res.mask, g)
            rows.append({
                "base": kind, "case": case_name, "preset": preset, **m,
            })

    return rows


def to_markdown(rows: list) -> str:
    """把诊断结果渲染成 Markdown 表格（可直接贴进文档 / PR）。"""
    lines = [
        "| 底图 | 用例 | 预设 | 误报率 FPR | 召回 Recall | 精确率 Prec | IoU |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['base']} | {r['case']} | {r['preset']} "
            f"| {r['fpr']*100:.1f}% | {r['recall']*100:.1f}% "
            f"| {r['precision']*100:.1f}% | {r['iou']*100:.1f}% |"
        )
    return "\n".join(lines)


def summarize(rows: list) -> str:
    """给出整体健康度小结：干净图平均误报率 + 带水印图平均召回/IoU。"""
    clean = [r for r in rows if r["case"] == "(clean)"]
    marked = [r for r in rows if r["case"] != "(clean)"]
    if not clean or not marked:
        return "（样本不足，无法汇总）"
    fpr = sum(r["fpr"] for r in clean) / len(clean)
    rec = sum(r["recall"] for r in marked) / len(marked)
    iou = sum(r["iou"] for r in marked) / len(marked)
    prec = sum(r["precision"] for r in marked) / len(marked)
    verdict = "健康" if fpr < 0.02 and rec > 0.5 else "存在过度检测"
    return (
        f"干净图平均误报率 = {fpr*100:.1f}%（目标 < 2%）\n"
        f"带水印图平均召回 = {rec*100:.1f}%（目标 > 50%）\n"
        f"带水印图平均精确率 = {prec*100:.1f}%\n"
        f"带水印图平均 IoU = {iou*100:.1f}%\n"
        f"综合判定：{verdict}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="检测器过度检测诊断")
    ap.add_argument("--json", dest="json_path", default=None,
                    help="额外把结果导出为 JSON 文件")
    ap.add_argument("--quick", action="store_true",
                    help="只跑 photo 底图，快速迭代")
    args = ap.parse_args()

    rows = run(quick=args.quick)
    print(to_markdown(rows))
    print()
    print(summarize(rows))

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"\n已导出 JSON：{args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
