"""
方向 A · LaMa vs OpenCV 真实图对决
==================================

职责
----
对 `benchmark_real/manifest.json` + `ground_truth.json` 中每张图：
1. 拿到掩码（`--mask-source detect` 走检测器，`truth` 走真值框 → 纯矩形掩码）；
2. 分别用 **OpenCV** 与 **LaMa** 两个引擎修复；
3. 计算三个**互补**指标（见下）；
4. 统计：哪种引擎胜出比例 + 平均差距 + 最差情形。

度量口径（阶段 A 修正）
----------------------
旧版只有 `masked_psnr`（掩码区 vs 原图的 PSNR）。**该指标实际量的是「改写量」
而不是「质量」**：水印是亮的、背景是暗的，谁改得越多 PSNR 越低、看起来越"输"。
于是「把水印擦干净」这件事反而会被判负 —— 阶段 51 的「OpenCV 8/8 全胜」正是
被这个口径误导，叠加 LaMa 值域缺陷（见 `core/inpainter.py::_run_lama_tile`）。
现改为三指标并用，其中前两个才是**因果正确**的质量代理：

| 指标 | 含义 | 方向 |
|---|---|---|
| `seam_delta`  | 掩码区均值 vs 周边环带均值的色差（无参考） | **越低越好**（水印被替换且与邻域融合） |
| `white_frac`  | 掩码区「三通道均 ≥250」的像素占比（白板指纹） | **越低越好**（饱和 = 失败） |
| `masked_psnr` | 掩码区改写量（保留供审计对照） | 仅供对照，**高 ≠ 好** |

CLI
---
    python scripts/real_benchmark_decision.py
    python scripts/real_benchmark_decision.py --presets general --mask-source truth

注意
----
- 真实图**无「未加水印的原图」**，故 `seam_delta` 用「邻域环带」当参照物 ——
  环带像素未被掩码覆盖、未被修改，是可信的局部风格基准。
- 没有真值的真修复质量仍是开放的；`seam_delta` 是当前可得的最佳代理。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

BENCH_DIR = os.environ.get("WME_BENCH_DIR") or os.path.join(_ROOT, "benchmark_real")
RESULTS_DIR = os.path.join(BENCH_DIR, "results")
MANIFEST_PATH = os.path.join(BENCH_DIR, "manifest.json")
TRUTH_PATH = os.path.join(BENCH_DIR, "ground_truth.json")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _masked_psnr(orig: Any, repaired: Any, mask: Any) -> Optional[float]:
    """掩码区修复前后 PSNR（dB）。mask > 0 的像素视为「修改目标」。"""
    try:
        import numpy as np
        if orig.shape != repaired.shape or orig.shape[:2] != mask.shape:
            return None
        m = (mask > 0)
        if not m.any():
            return None
        diff = (orig.astype(np.float32) - repaired.astype(np.float32)) ** 2
        mse = float(diff[m].mean())
        if mse <= 1e-6:
            return 99.0  # 几乎无修改 → 满分
        import math
        return 10.0 * math.log10((255.0 ** 2) / mse)
    except Exception:
        return None


def _ring_mean(arr: Any, mask: Any, k: int = 25) -> Optional[Any]:
    """
    计算掩码「周边环带」的通道均值，作为局部风格基准（阶段 A）。

    环带 = 掩码膨胀 k 像素后的区域减去掩码本身。这些像素**未被修复引擎触碰**，
    因此是「这张图在这个位置本该长什么样」的最可信参照。

    参数说明：
        arr  : H×W×3 uint8。
        mask : H×W 掩码（非 0 为待修复区）。
        k    : 膨胀核边长（像素）。

    返回值：
        长度 3 的 float 均值向量；掩码为空或无环带像素时返回 None。
    """
    try:
        import numpy as np
        import cv2
        m = (mask > 0)
        if not m.any():
            return None
        dil = cv2.dilate(mask, np.ones((k, k), np.uint8))
        ring = (dil > 0) & (~m)
        if not ring.any():
            return None
        return arr[ring].astype(np.float32).mean(axis=0)
    except Exception:
        return None


def _seam_delta(arr: Any, repaired: Any, mask: Any, k: int = 25) -> Optional[float]:
    """
    接缝色差：|修复后掩码区均值 − 周边环带均值| 的通道平均（阶段 A 核心指标）。

    为什么它比 masked_psnr 更可信：水印是「亮色块贴在暗背景上」，真正的修复结果
    应当在均值上向邻域靠拢（色差变小）；而**饱和白板**会让色差暴涨（实测 169.5），
    **原图残留**则维持在中高位（实测 36.5）。方向单一、没有反直觉区。

    返回值：
        色差（0~255 量纲，越低越好）；无法计算时 None。
    """
    try:
        import numpy as np
        ring = _ring_mean(arr, mask, k=k)
        if ring is None:
            return None
        m = (mask > 0)
        region = repaired[m].astype(np.float32).mean(axis=0)
        return float(np.abs(region - ring).mean())
    except Exception:
        return None


def _white_frac(repaired: Any, mask: Any) -> Optional[float]:
    """
    白板指纹：掩码区「三通道均 ≥250」的像素占比（阶段 A 饱和检测）。

    来历：LaMa 值域缺陷下该值高达 0.87（整片掩码变白），是**无需看图**即可
    判定引擎失败的最强单值信号。健康输出应 <0.02。

    返回值：
        占比 0~1（越低越好）；无法计算时 None。
    """
    try:
        import numpy as np
        m = (mask > 0)
        if not m.any():
            return None
        region = repaired[m]
        return float(np.mean(np.all(region >= 250, axis=1)))
    except Exception:
        return None


def _try_niqe(img: Any) -> Optional[float]:
    """尝试无参考质量分；不可用则返回 None。"""
    try:
        # 优先使用项目自身的 evaluate_quality（如果有 NIQE 实现）
        from services.quality_service import evaluate_quality_arrays
        rep = evaluate_quality_arrays(img, img, compute_no_ref=True)
        if rep and getattr(rep, "niqe", None) is not None:
            return float(rep.niqe)
        return None
    except Exception:
        return None


def _repair_one(image_rgb: Any, mask: Any, engine: str) -> Optional[Any]:
    """跑单个引擎。`inpaint()` 返回 H×W×3 uint8 RGB（与输入同色序）。"""
    try:
        from core.inpainter import inpaint
        return inpaint(image_rgb, mask, engine=engine)
    except Exception as e:
        print(f"[warn] {engine} 修复失败：{e}", file=sys.stderr)
        return None


def _truth_rect_mask(arr: Any, truth_entry: Dict[str, Any]) -> Optional[Any]:
    """
    由真值框生成**纯矩形掩码**（阶段 A「干净对决」用）。

    为什么需要它：检测器在真实图上的误报率极高（F1：半透明通路占误报 92.5%），
    若拿检测掩码去对决引擎，两个引擎都在「修一大堆根本没水印的地方」，
    比出来的差异被噪声淹没。用真值框（已知水印的确切位置）当掩码，
    才是「同一块待修区域，两种引擎谁修得好」的干净对照。

    返回值：
        H×W uint8 掩码（255=待修）；真值缺失或框非法时返回 None。
    """
    try:
        import numpy as np
        h, w = arr.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        # ground_truth.json 的字段名是 `watermarks`（每条含 bbox=[x,y,w,h] + style）。
        for reg in truth_entry.get("watermarks", truth_entry.get("regions", [])):
            bbox = reg.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            x, y, bw, bh = (int(v) for v in bbox)
            if bw <= 0 or bh <= 0:
                continue
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + bw), min(h, y + bh)
            if x1 > x0 and y1 > y0:
                mask[y0:y1, x0:x1] = 255
        return mask if (mask > 0).any() else None
    except Exception:
        return None


def _run_image(
    arr: Any,
    truth_entry: Dict[str, Any],
    preset: str,
    mask_source: str = "detect",
) -> Dict[str, Any]:
    from core.detector import detect_watermark_mask

    if mask_source == "truth":
        mask = _truth_rect_mask(arr, truth_entry)
        if mask is None:
            return {"preset": preset, "empty_mask": True, "mask_source": "truth", "engines": {}}
        mask_meta: Dict[str, Any] = {"mask_source": "truth"}
    else:
        try:
            res = detect_watermark_mask(arr, preset=preset, use_ocr=False)
            mask = res.mask
        except Exception as e:
            return {"error": f"detect failed: {e}"}
        mask_meta = {"mask_source": "detect", "preset": preset}
        if mask is None or (mask > 0).sum() == 0:
            return {**mask_meta, "empty_mask": True, "engines": {}}

    # 原图自身的接缝色差（下界参照：修复到不劣于此才算「真的擦掉了水印」）。
    baseline_seam = _seam_delta(arr, arr, mask)

    engines_out: Dict[str, Any] = {}
    for eng in ("opencv", "lama"):
        repaired = _repair_one(arr, mask, engine=eng)
        if repaired is None:
            engines_out[eng] = {"available": False}
            continue
        repaired_rgb = repaired  # inpaint() 返回 RGB，与输入同色序
        engines_out[eng] = {
            "available": True,
            "masked_psnr": _masked_psnr(arr, repaired_rgb, mask),
            "seam_delta": _seam_delta(arr, repaired_rgb, mask),
            "white_frac": _white_frac(repaired_rgb, mask),
            "niqe": _try_niqe(repaired_rgb),
        }
    return {
        **mask_meta,
        "mask_pixels": int((mask > 0).sum()),
        "baseline_seam_delta": baseline_seam,
        "engines": engines_out,
    }


def _decide_winner(
    ocv: Any,
    lam: Any,
    baseline: Any,
    tol: float = 3.0,
) -> str:
    """
    真实对决的胜者判定（阶段 154 抽出为纯函数，固化 baseline 下界契约）。

    契约来源：`_run_image` 计算的 `baseline_seam_delta` 是「原图自身、未修复」的
    接缝色差（见第 233 行注释）。它代表「什么都不做」的下界 —— 一个引擎只有
    比这个下界**更低**（修复后色差更小），才算「真的擦掉了水印」。

    判定规则：
        - ocv / lam 非双数值        → "n/a"（对决不成立）
        - 两引擎都 ≥ baseline       → "inconclusive"（都不如不修复，不构成胜出）
        - 否则按 seam_delta 越低越好，|差| < tol → "tie"，否则小者胜
          （opencv / lama）

    返回值（5 态）：
        "opencv" | "lama" | "tie" | "inconclusive" | "n/a"

    关键修复：历史上 baseline 只被打印、从不进决策，导致「两引擎都比不修复更差」
    时仍会判出一个 winner（假胜出）。本函数把下界契约写进返回值。
    """
    if not (isinstance(ocv, (int, float)) and isinstance(lam, (int, float))):
        return "n/a"
    if isinstance(baseline, (int, float)):
        # 两引擎都不如「留着水印不修复」→ 不构成任何胜出（阶段 154 契约修复）。
        if ocv >= baseline and lam >= baseline:
            return "inconclusive"
    if abs(ocv - lam) < tol:
        return "tie"
    return "opencv" if ocv < lam else "lama"


def main() -> int:
    ap = argparse.ArgumentParser(description="LaMa vs OpenCV 真实图对决")
    ap.add_argument("--presets", default="general,ai_corner",
                    help="逗号分隔；每个预设分别对决（--mask-source truth 时忽略）")
    ap.add_argument("--mask-source", default="detect", choices=["detect", "truth"],
                    help="detect=用检测器掩码（含误报）；truth=用真值框纯矩形掩码（干净对决）")
    ap.add_argument("--output", default=None,
                    help=f"JSON 输出路径；默认写入 {os.path.join(RESULTS_DIR, 'decision_<ts>.json')}")
    args = ap.parse_args()

    if not os.path.exists(MANIFEST_PATH) or not os.path.exists(TRUTH_PATH):
        print("[error] manifest 或 ground_truth 不存在；先 collect + build_ground_truth",
              file=sys.stderr)
        return 1

    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    truth = json.load(open(TRUTH_PATH, encoding="utf-8"))
    truth_by_id = {e["image_id"]: e for e in truth.get("entries", [])}
    labeled = [im for im in manifest.get("images", []) if im["id"] in truth_by_id]
    if not labeled:
        print("[error] 没有图同时有 manifest + ground_truth；先采集 + 标注", file=sys.stderr)
        return 2
    presets = [p.strip() for p in args.presets.split(",") if p.strip()]
    if args.mask_source == "truth":
        # 真值掩码与预设无关（预设只影响检测器），折叠为单一标签避免重复跑。
        presets = ["truth"]

    import numpy as np
    from PIL import Image

    results: List[Dict[str, Any]] = []
    for im in labeled:
        iid = im["id"]
        rel = im["path"]
        abs_p = rel if os.path.isabs(rel) else os.path.join(BENCH_DIR, rel)
        try:
            arr = np.array(Image.open(abs_p).convert("RGB"))
        except Exception as e:
            print(f"[error] 读图失败 {iid}: {e}", file=sys.stderr)
            continue
        row: Dict[str, Any] = {"image_id": iid, "ai_source": im.get("ai_source")}
        for preset in presets:
            row[preset] = _run_image(
                arr,
                truth_by_id[iid],
                preset="general" if preset == "truth" else preset,
                mask_source=args.mask_source,
            )
        results.append(row)

    out = {
        "timestamp": _now_iso(),
        "n_images": len(results),
        "presets": presets,
        "mask_source": args.mask_source,
        "note": (
            "质量以 seam_delta（掩码区 vs 邻域环带色差，越低越好）为准；"
            "white_frac 为白板指纹（越低越好）；"
            "masked_psnr 仅为改写量对照，高 ≠ 好"
        ),
        "results": results,
    }
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.output or os.path.join(RESULTS_DIR, f"decision_{ts}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 简明 stdout —— 以 seam_delta（越低越好）为判定主指标。
    SEAM_TOL = 3.0  # dB 量纲下的色差容差；小于此值视为平手
    print("\n## LaMa vs OpenCV 真实图对决")
    print(f"\n- 图像数：{len(results)}；掩码来源：{args.mask_source}；预设：{presets}")
    print("\n| image_id | preset | orig_seam | opencv_seam | lama_seam | opencv_white | lama_white | 胜者 |")
    print("|---|---|---|---|---|---|---|---|")

    tally: Dict[str, int] = {"opencv": 0, "lama": 0, "tie": 0, "inconclusive": 0, "n/a": 0}
    gaps: List[float] = []
    for r in results:
        for preset in presets:
            ent = r.get(preset, {})
            engs = ent.get("engines", {})
            base = ent.get("baseline_seam_delta")
            ocv = engs.get("opencv", {}).get("seam_delta")
            lam = engs.get("lama", {}).get("seam_delta")
            ow = engs.get("opencv", {}).get("white_frac")
            lw = engs.get("lama", {}).get("white_frac")
            # 阶段 154：胜者判定改为走 _decide_winner，固化 baseline 下界契约。
            winner = _decide_winner(ocv, lam, base, SEAM_TOL)
            if winner in ("tie", "opencv", "lama"):
                gaps.append(abs(ocv - lam))
            tally[winner] += 1

            def _f(v: Any, p: int = 1) -> str:
                return f"{v:.{p}f}" if isinstance(v, (int, float)) else "—"

            print(
                f"| {r['image_id']} | {preset} | {_f(base)} | {_f(ocv)} | {_f(lam)} | "
                f"{_f(ow, 3)} | {_f(lw, 3)} | {winner} |"
            )

    print(f"\n**胜出统计（seam_delta 越低越好）：** opencv {tally['opencv']} / "
          f"lama {tally['lama']} / tie {tally['tie']} / "
          f"inconclusive {tally['inconclusive']}（两引擎都不如不修复）/ n/a {tally['n/a']}")
    if gaps:
        print(f"**平均色差差距：** {sum(gaps)/len(gaps):.2f}（量纲 0~255）")
    print(f"\n详细指标：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())