"""检测掩码膨胀量的真值审计（阶段 66）。

背景
----
检测器有三条产出掩码的通路，各自有一个「掩码内部膨胀半径」：

    · `detect_translucent`      → `DETECT_TRANSLUCENT_MASK_DILATE`（下游 general / ai_logo / ai_multi …）
    · `detect_tiled_translucent`→ `DETECT_TILED_MASK_DILATE`（阶段 54 收敛为 0）
    · `detect_grid_watermark`   → `DETECT_GRID_MASK_DILATE`（阶段 53 收敛为 0）

半透明通路的膨胀量长期是硬编码的 `dilate=4`，从未被统一口径标定过，使 general 预设的掩码
比兄弟通路宽 4px。叠加管线 `MASK_DILATE_DEFAULT`（阶段 65 标定为 2）后，general 路径的
**有效膨胀达 6px**，而手绘掩码路径（`ui/tab_single` 画笔/框选，无任何上游 padding）只有 2px
——两条输入路径并不对称。

本脚本用**双口径**回答「这个膨胀应该取多少」：

    检测口径：端到端（general 预设）的精确率 / 召回 / IoU / 预测覆盖率 / 干净图误报
    修复口径：残留（真值水印区 |out−base|）与越界（真值外 12px 环带 |out−base|）

红线
----
把标定结论固化为可执行断言，防止日后有人把默认值调回高膨胀：

    ① 默认值的端到端精确率 ≥ 参考值（{REFERENCE}）的精确率
    ② 默认值的 IoU         ≥ 参考值的 IoU
    ③ 默认值的召回         ≥ 参考值召回 − {RECALL_TOLERANCE}pp
    ④ 默认值的「越界」     ≤ 参考值的越界
    ⑤ 默认值的修复残留     ≤ 参考值残留 + {RESIDUAL_TOLERANCE}
    ⑥ 任何档位下干净图误报 ≤ {CLEAN_FPR_MAX}%

用法
----
    python scripts/detector_mask_dilate_audit.py            # 全量（检测 + 修复）
    python scripts/detector_mask_dilate_audit.py --no-repair # 只跑检测口径（快）
    python scripts/detector_mask_dilate_audit.py --json      # 输出 JSON（供 CI/门禁消费）

退出码：0 = 全部红线通过；1 = 存在违规。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import config
import core.detector as det
from core.inpainter import inpaint
from scripts.detector_diag import BASE_KINDS, gt_mask, metrics
from scripts.make_demo_assets import CASES

try:
    import cv2
except ImportError:  # pragma: no cover
    raise SystemExit("需要 cv2（opencv-python）")

#: 扫描的膨胀档位。
DILATES: tuple[int, ...] = (0, 2, 4, 8)
#: 参考值：历史默认（旧硬编码 dilate=4），用于"新默认不得更差"的对照。
REFERENCE: int = 4
#: 审计用的底图种类（gradient 的信号太弱，不参与修复口径）。
KINDS: tuple[str, ...] = ("gradient", "textured", "photo")
#: 修复口径只跑最具代表性的两个用例（控制单次审计的 inpaint 次数，规避大批量
#: inpaint 在本环境偶发的进程级硬退出）。
REPAIR_KINDS: tuple[str, ...] = ("textured", "photo")
REPAIR_CASES: tuple[str, ...] = ("corner_logo", "edge_text")
#: 底图边长（与质量门禁 / 阶段 57 的参考分辨率锚点一致）。
SIZE: int = 640

#: 红线：干净图平均误报上限（%）。
CLEAN_FPR_MAX: float = 2.0
#: 红线：允许让渡的召回（pp）——膨胀换召回是本条通路的已知取舍方向。
RECALL_TOLERANCE: float = 2.0
#: 红线：允许的修复残留回升（灰度级）。
RESIDUAL_TOLERANCE: float = 0.5


def set_dilate(value: int) -> None:
    """把半透明通路的掩码膨胀量临时改为 `value`（配置常量运行期可写）。"""
    config.DETECT_TRANSLUCENT_MASK_DILATE = int(value)


def _general_mask(img: np.ndarray) -> np.ndarray:
    """跑 `general` 预设端到端，返回单通道掩码。"""
    m = det.detect_watermark_mask(img, preset="general", use_ocr=False).mask
    return m[:, :, 0] if m.ndim == 3 else m


def _clean_fpr() -> float:
    """干净图平均误报率（%）：干净底图上被标记为水印的像素占比。"""
    vals = []
    for kind in KINDS:
        base = BASE_KINDS[kind](7, w=SIZE, h=SIZE)
        m = _general_mask(base)
        vals.append(float((m > 0).sum()) / m.size * 100.0)
    return float(np.mean(vals))


def detection_row(value: int) -> dict:
    """检测口径：`general` 预设端到端，在全部 底图×用例 上聚合。"""
    set_dilate(value)
    pr, rc, iou, pc = [], [], [], []
    for kind in KINDS:
        base = BASE_KINDS[kind](7, w=SIZE, h=SIZE)
        for _case, (wm_fn, _preset, _label) in CASES.items():
            marked = wm_fn(base)
            g = gt_mask(base, marked)
            if not (g > 0).any():
                continue
            mm = metrics(_general_mask(marked), g)
            pr.append(mm["precision"])
            rc.append(mm["recall"])
            iou.append(mm["iou"])
            pc.append(mm["pred_cov"])
    return {
        "dilate": value,
        "precision": float(np.mean(pr)),
        "recall": float(np.mean(rc)),
        "iou": float(np.mean(iou)),
        "pred_cov": float(np.mean(pc)),
        "clean_fpr": _clean_fpr(),
    }


def repair_row(value: int) -> dict:
    """修复口径：`general` 掩码 → OpenCV inpaint（管线 dilate 固定 0，隔离变量）。"""
    set_dilate(value)
    residual, overpaint = [], []
    for kind in REPAIR_KINDS:
        base = BASE_KINDS[kind](7, w=SIZE, h=SIZE)
        for case in REPAIR_CASES:
            wm_fn = CASES[case][0]
            marked = wm_fn(base)
            g = gt_mask(base, marked) > 0
            kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            ring = cv2.dilate(
                g.astype(np.uint8), kern, iterations=12
            ).astype(bool) & (~g)
            m = _general_mask(marked)
            if int((m > 0).sum()) == 0:
                out = marked.astype(np.int16)  # 未检出 → 等于没擦
            else:
                out = inpaint(marked, m, engine="opencv", dilate=0).astype(np.int16)
            diff = np.abs(out - base.astype(np.int16)).max(axis=2).astype(np.float64)
            residual.append(float(diff[g].mean()))
            overpaint.append(float(diff[ring].mean()))
    return {
        "dilate": value,
        "residual": float(np.mean(residual)),
        "overpaint": float(np.mean(overpaint)),
    }


def evaluate(report: dict) -> tuple[bool, list[str]]:
    """红线判定：新默认值相对历史参考值（dilate=4）不得更差。"""
    problems: list[str] = []
    rows = {r["dilate"]: r for r in report["detection"]}
    default = report["default"]
    ref = rows.get(REFERENCE)
    cur = rows.get(default)
    if cur is None or ref is None:
        return False, ["审计数据缺失：默认值或参考值档位未采集"]

    if cur["precision"] < ref["precision"] - 1e-9:
        problems.append(
            f"[精确率红线] 默认 dilate={default} 精确率 {cur['precision']*100:.1f}% "
            f"< 参考 dilate={REFERENCE} 的 {ref['precision']*100:.1f}%")
    if cur["iou"] < ref["iou"] - 1e-9:
        problems.append(
            f"[IoU 红线] 默认 dilate={default} IoU {cur['iou']*100:.1f}% "
            f"< 参考 {ref['iou']*100:.1f}%")
    if cur["recall"] < ref["recall"] - RECALL_TOLERANCE / 100.0:
        problems.append(
            f"[召回红线] 默认 dilate={default} 召回 {cur['recall']*100:.1f}% "
            f"低于参考 {ref['recall']*100:.1f}% 逾 {RECALL_TOLERANCE}pp")

    for row in report["detection"]:
        if row["clean_fpr"] > CLEAN_FPR_MAX:
            problems.append(
                f"[误报红线] dilate={row['dilate']} 干净图误报 "
                f"{row['clean_fpr']:.2f}% > {CLEAN_FPR_MAX}%")

    if report.get("repair"):
        rrows = {r["dilate"]: r for r in report["repair"]}
        rref, rcur = rrows.get(REFERENCE), rrows.get(default)
        if rref is not None and rcur is not None:
            if rcur["overpaint"] > rref["overpaint"] + 1e-9:
                problems.append(
                    f"[越界红线] 默认 dilate={default} 越界 {rcur['overpaint']:.2f} "
                    f"> 参考 {rref['overpaint']:.2f}")
            if rcur["residual"] > rref["residual"] + RESIDUAL_TOLERANCE:
                problems.append(
                    f"[残留红线] 默认 dilate={default} 残留 {rcur['residual']:.2f} "
                    f"高于参考 {rref['residual']:.2f} 逾 {RESIDUAL_TOLERANCE}")
    return (len(problems) == 0), problems


def to_markdown(report: dict) -> str:
    """把审计结果渲染成 Markdown 表格。"""
    lines = [
        f"默认值 DETECT_TRANSLUCENT_MASK_DILATE = {report['default']}"
        f"（参考：历史值 {REFERENCE}）",
        "",
        "检测口径（general 端到端，3 底图 × 4 用例）",
        "| dilate | 精确率% | 召回% | IoU% | 预测覆盖率% | 干净图误报% |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["detection"]:
        lines.append(
            f"| {r['dilate']} | {r['precision']*100:.1f} | {r['recall']*100:.1f} | "
            f"{r['iou']*100:.1f} | {r['pred_cov']*100:.2f} | {r['clean_fpr']:.2f} |")
    if report.get("repair"):
        lines += [
            "",
            "修复口径（general 掩码 → OpenCV inpaint，2 底图 × 2 用例）",
            "| dilate | 残留 | 越界 |",
            "|---|---|---|",
        ]
        for r in report["repair"]:
            lines.append(f"| {r['dilate']} | {r['residual']:.2f} | {r['overpaint']:.2f} |")
    ok, problems = report["ok"], report["problems"]
    lines += ["", "判定：" + ("✅ 全部红线通过" if ok else "❌ 存在违规")]
    lines += [f"  - {p}" for p in problems]
    return "\n".join(lines)


def run(repair: bool = True) -> dict:
    """执行完整审计，返回结果字典。"""
    default = int(config.DETECT_TRANSLUCENT_MASK_DILATE)
    report: dict = {
        "default": default,
        "reference": REFERENCE,
        "detection": [detection_row(v) for v in DILATES],
        "repair": [repair_row(v) for v in DILATES] if repair else [],
    }
    set_dilate(default)  # 复原，避免影响后续调用
    ok, problems = evaluate(report)
    report["ok"], report["problems"] = ok, problems
    return report


def main() -> int:
    # 阶段 331：输出编码加固（GBK 管道下打印 ⇒/✅ 会 UnicodeEncodeError ⇒ 「通过」被读成退出码 1）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="检测掩码膨胀量真值审计（阶段 66）")
    ap.add_argument("--no-repair", action="store_true",
                    help="跳过修复口径（只跑检测，显著更快）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    report = run(repair=not args.no_repair)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
