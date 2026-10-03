# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台
 文件名称 : scripts/prompted_area_gate.py
 文件说明 : 阶段 121 · **次级档四类面积回归门禁**。

  保护对象：`benchmark_prompted/`（20 张「提示词渲染水印 × AI 底图」）上，
  四个未承诺预设（ai_text_heavy / ai_grid / ai_faint_tiled / ai_gradient）
  的**掩码面积画像**——一旦某次改动让某条检测通路过开火，本门禁必须红。

  为什么用「面积」而不是 recall（诚实口径）：
    这四类里 grid / tile / gradient 是**全图型**水印，bbox 级 recall/IoU 对其无意义
    （`scripts/real_gate.py:631` 同判）。项目对这四类的既定度量就是 `mask_coverage`
    ——`掩码像素 / 图面像素`，**不需要真值**。本门禁沿用同一度量。

  单一真值来源（不重写指标）：
    面积聚合复用 `scripts/real_gate._area_stats`；红线复用 `REAL_AREA_CONTRACT`；
    漂移容差复用 `AREA_DRIFT_TOLERANCE`。本文件只在其上加「可比性元数据 + 判定 + 渲染」。

  三层判定 + 退出码三分（与既有门禁同约定）：
    第 0 层 数据完整性：实际图数 / 每类张数 == 基线登记的  → 不等即失败（防静默跳过）
    第 1 层 绝对红线   ：`REAL_AREA_CONTRACT`（与基线无关）
    第 2 层 基线漂移   ：只查「朝红线方向」变化（面积越大越坏）
    退出码 0 通过 / 1 未通过 / 2 跳过（数据集或基线缺失）

  用法：
    python scripts/prompted_area_gate.py            # 判定
    python scripts/prompted_area_gate.py --update   # 刷新基线快照
    python scripts/prompted_area_gate.py --json out.json
==============================================================================
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys

import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from core.detector import detect_watermark_mask  # noqa: E402
from real_gate import (  # noqa: E402  ← 单一真值来源：面积聚合/红线/容差
    AREA_DRIFT_TOLERANCE,
    REAL_AREA_CONTRACT,
    _area_stats,
)
from baseline_guard import (  # noqa: E402  # 阶段 146：刷新基线护栏（四条门禁共用）
    check_downward_pairs,
    render_refusal,
)

BENCH_DIR = os.path.join(_ROOT, "benchmark_prompted")
MANIFEST_PATH = os.path.join(BENCH_DIR, "manifest.json")
BASELINE_PATH = os.path.join(BENCH_DIR, "area_baseline.json")

#: 受保护的四个预设（顺序即渲染顺序）。
PRESETS = ("ai_text_heavy", "ai_grid", "ai_faint_tiled", "ai_gradient")
#: 预设 → 真值 style（用于「每类张数」的数据完整性核对）。
PRESET_STYLE = {
    "ai_text_heavy": "edge_text",
    "ai_grid": "grid_watermark",
    "ai_faint_tiled": "translucent",
    "ai_gradient": "gradient_watermark",
}
#: 参与漂移比对的面积指标（全部「越高越坏」）。
DRIFT_KEYS = ("mask_coverage_mean", "mask_coverage_p90", "mask_coverage_max")
#: 基线值贴近 0 时相对容差不成立 → 改用绝对上浮阈值（经验：real_gate 同款处理）。
NEAR_ZERO = 0.001
ABS_TOLERANCE = 0.005
#: 阈值标定守卫用：红线不得高于此（否则红线形同虚设）。
RED_LINE_CEILING = 0.5


# ------------------------------------------------------------------------------
# 阶段 146 · 刷新基线的护栏参数
# ------------------------------------------------------------------------------
# 本门禁的 `--update` 与另两个主门禁同款缺口：写完基线**直接 return 0**
#   ⇒ 绕过数据完整性、绝对红线与漂移三层判定（详见 `scripts/baseline_guard.py`）。
# 规则：`DRIFT_KEYS` 全部「越高越坏」（面积类），容差复用本门禁漂移层的
#   `AREA_DRIFT_TOLERANCE`（相对）与 `ABS_TOLERANCE`（近零兜底）。
#
# ⚠ **一处有意的口径放宽（已登记）**：本门禁漂移层是「近零 ⇒ 绝对、否则 ⇒ 相对」
#   的 **if/else 二选一**；而 `baseline_guard` 取 `max(绝对, 相对)` 的**较宽者**。
#   两者仅在 `NEAR_ZERO < old < ABS_TOLERANCE / AREA_DRIFT_TOLERANCE ≈ 0.0333`
#   这一窄区间内有差异，方向是**本护栏更宽**（少拦、绝不误拦）。
#   刻意如此：若护栏比漂移层更严，正常波动会让 `--update` 失败，
#   用户就会转去用 `--force-update` ⇒ 护栏形同虚设。
_REFRESH_TOL = AREA_DRIFT_TOLERANCE
_REFRESH_ABS = ABS_TOLERANCE


def _refresh_pairs(old_agg, new_agg) -> list:
    """把两侧 `per_preset.<预设>.<指标>` 提取成 `(路径键, 旧值, 新值)` 序列。"""
    old_pp = (old_agg or {}).get("per_preset") or {}
    new_pp = (new_agg or {}).get("per_preset") or {}
    if not isinstance(old_pp, dict) or not isinstance(new_pp, dict):
        return []
    pairs: list = []
    for preset in sorted(set(old_pp) & set(new_pp)):
        o, n = old_pp[preset] or {}, new_pp[preset] or {}
        if not isinstance(o, dict) or not isinstance(n, dict):
            continue
        for key in DRIFT_KEYS:
            if key in o and key in n:
                pairs.append((f"per_preset.{preset}.{key}", o[key], n[key]))
    return pairs


def _refresh_rules_for(pairs) -> dict:
    """全部 `DRIFT_KEYS` 都是「越高越坏」⇒ 方向恒为 `"max"`。"""
    return {key: ("max", _REFRESH_TOL, _REFRESH_ABS)
            for key, _o, _n in pairs
            if key.rsplit(".", 1)[-1] in DRIFT_KEYS}


# ------------------------------------------------------------------------------
# 计算（复用 real_gate 的面积聚合）
# ------------------------------------------------------------------------------
def _imread_u(path: str) -> "np.ndarray | None":
    """非 ASCII 路径安全读图（Windows 上 cv2.imread 读不了中文路径）。"""
    try:
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        return None


def run_all(manifest: dict, extra: "dict | None" = None) -> list:
    """逐图 × 逐预设跑检测，产出与 `real_gate._area_stats` 兼容的 rows。"""
    rows = []
    for im in manifest.get("images", []):
        img = _imread_u(os.path.join(BENCH_DIR, im.get("path", "")))
        row = {"id": im.get("id"), "style": im.get("watermark_style_hint"), "results": {}}
        for preset in PRESETS:
            if img is None:
                row["results"][preset] = {"error": "读图失败"}
                continue
            try:
                res = detect_watermark_mask(
                    cv2.cvtColor(img, cv2.COLOR_BGR2RGB), preset=preset, **(extra or {})
                )
                row["results"][preset] = {
                    "mask_coverage": float((res.mask > 0).mean()),
                    "pred_count": len(res.regions),
                }
            except Exception as exc:  # noqa: BLE001
                row["results"][preset] = {"error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
    return rows


def build_summary(manifest: dict, rows: list) -> dict:
    """把 rows 聚合成判定所需的 summary（面积画像 + 完整性计数）。"""
    per_preset = {}
    for preset in PRESETS:
        st = _area_stats(rows, preset)
        per_preset[preset] = st or {"n": 0}
    by_style = {}
    for r in rows:
        by_style[r["style"]] = by_style.get(r["style"], 0) + 1
    return {
        "n_images": len(rows),
        "by_style": by_style,
        "per_preset": per_preset,
        "sha_digest": _sha_digest(manifest),
    }


def _sha_digest(manifest: dict) -> str:
    """manifest 内全部 sha256 的稳定摘要（可比性检查：数据集是否换了）。"""
    shas = sorted(str(im.get("sha256", "")) for im in manifest.get("images", []))
    return hashlib.sha256("|".join(shas).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------------------
# 判定（纯函数：不读文件、不跑计算）
# ------------------------------------------------------------------------------
def evaluate(summary: dict, baseline: dict) -> "tuple[bool, list]":
    """三层判定。返回 (ok, problems[])。"""
    problems: list = []
    cfg = (baseline or {}).get("config") or {}

    # ---- 第 0 层：数据完整性（防「静默跳过样本」）----
    exp_n = cfg.get("n_images")
    if exp_n is not None and summary.get("n_images") != exp_n:
        problems.append(
            f"[完整性] 实际参与评估的图数 {summary.get('n_images')} != 基线登记 {exp_n}"
            f"（有样本被静默跳过，指标不可比）"
        )
    exp_by_style = cfg.get("by_style") or {}
    for style, cnt in exp_by_style.items():
        got = (summary.get("by_style") or {}).get(style, 0)
        if got != cnt:
            problems.append(f"[完整性] 类型 {style} 张数 {got} != 基线 {cnt}（数据集被改动）")
    exp_sha = cfg.get("sha_digest")
    if exp_sha and summary.get("sha_digest") != exp_sha:
        problems.append(
            f"[可比性] 数据集指纹 {summary.get('sha_digest')} != 基线 {exp_sha}"
            f"（素材换过，面积数字不可比；如属有意变更请 --update）"
        )

    # ---- 第 1 层：绝对红线（与基线无关）----
    for preset, contract in REAL_AREA_CONTRACT.items():
        st = (summary.get("per_preset") or {}).get(preset)
        if not st or not st.get("n"):
            problems.append(f"[红线] {preset}: 无有效统计（n=0）")
            continue
        for metric, (direction, thr) in contract.items():
            val = st.get(metric)
            if val is None:
                problems.append(f"[红线] {preset}.{metric}: 缺失")
                continue
            if direction == "max" and float(val) > float(thr):
                problems.append(
                    f"[红线] {preset}.{metric} = {val} > 上限 {thr}（该通路过开火）"
                )
            elif direction == "min" and float(val) < float(thr):
                problems.append(f"[红线] {preset}.{metric} = {val} < 下限 {thr}")

    # ---- 第 2 层：基线漂移（只查变坏方向）----
    base_pp = (baseline or {}).get("per_preset") or {}
    for preset in PRESETS:
        now = (summary.get("per_preset") or {}).get(preset) or {}
        old = base_pp.get(preset) or {}
        for key in DRIFT_KEYS:
            if key not in old or key not in now:
                continue
            o, n = float(old[key]), float(now[key])
            if o <= NEAR_ZERO:
                limit = NEAR_ZERO + ABS_TOLERANCE
                if n > limit:
                    problems.append(
                        f"[漂移] {preset}.{key} 由 ≈0 升到 {n}（绝对容差 {ABS_TOLERANCE}）"
                    )
            else:
                limit = o * (1.0 + AREA_DRIFT_TOLERANCE)
                if n > limit:
                    problems.append(
                        f"[漂移] {preset}.{key} {o} → {n}（+{(n / o - 1) * 100:.0f}% > "
                        f"容差 {AREA_DRIFT_TOLERANCE * 100:.0f}%）"
                    )
    return (not problems), problems


# ------------------------------------------------------------------------------
# 渲染 + CLI
# ------------------------------------------------------------------------------
def _render(summary: dict, ok: bool, problems: list) -> None:
    print("次级档四类面积回归门禁（benchmark_prompted）")
    print(f"样本：{summary.get('n_images')} 张 | by_style={summary.get('by_style')}")
    print("-" * 78)
    print(f"{'预设':<16}{'n':>3}{'cov_mean':>10}{'cov_p90':>10}{'cov_max':>10}{'红线':>10}")
    for preset in PRESETS:
        st = (summary.get("per_preset") or {}).get(preset) or {}
        thr = (REAL_AREA_CONTRACT.get(preset) or {}).get("mask_coverage_mean", ("max", None))[1]
        print(f"{preset:<16}{st.get('n', 0):>3}"
              f"{st.get('mask_coverage_mean', '-'):>10}"
              f"{st.get('mask_coverage_p90', '-'):>10}"
              f"{st.get('mask_coverage_max', '-'):>10}{thr:>10}")
    print("-" * 78)
    if ok:
        print("✅ 通过（四类面积均在红线内且未劣化）")
    else:
        print(f"❌ 未通过（{len(problems)} 项）")
        for p in problems:
            print("  -", p)


def _safe_relpath(p: str) -> str:
    """展示用相对路径——跨盘符会抛 ValueError，故必须包住（技能提醒）。"""
    try:
        return os.path.relpath(p, _ROOT)
    except Exception:  # noqa: BLE001
        return p


def main() -> int:
    # 阶段 170：编码加固统一走共用件（此前只切 stdout，现 stdout+stderr 一并覆盖）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="次级档四类面积回归门禁")
    ap.add_argument("--update", action="store_true", help="刷新基线快照")
    ap.add_argument("--force-update", action="store_true",
                    help="与 --update 同用：跳过「向下刷新护栏」显式覆盖基线（阶段 146）")
    ap.add_argument("--baseline", default=BASELINE_PATH)
    ap.add_argument("--json", default=None, help="把 summary 写到该路径")
    args = ap.parse_args()

    if not os.path.isfile(MANIFEST_PATH):
        print("⏭ 跳过：数据集缺失（benchmark_prompted/manifest.json 不存在）")
        return 2
    manifest = json.load(io.open(MANIFEST_PATH, encoding="utf-8"))
    if not manifest.get("images"):
        print("⏭ 跳过：数据集为空")
        return 2

    summary = build_summary(manifest, run_all(manifest))

    # 数据缺失 ⇒ **跳过而非通过**（images/ 已 gitignore，新克隆时不会误判为失败）。
    if all((summary["per_preset"].get(p) or {}).get("n", 0) == 0 for p in PRESETS):
        print("⏭ 跳过：无可用图片（benchmark_prompted/images/ 未就位）")
        return 2

    if args.update:
        # 阶段 146：刷新基线是**受护栏约束的动作**，不再是静默通道。
        #   ① 向下刷新护栏（写盘**之前**判定，否则基线已被污染）；
        #   ② 刷新后**继续走 `evaluate()`** —— 否则刷新会绕过数据完整性、
        #      绝对红线与漂移三层判定（另三条门禁同款修复，见 docs/146）。
        _old = None
        if os.path.isfile(args.baseline):
            try:
                _old = json.load(io.open(args.baseline, encoding="utf-8"))
            except (OSError, ValueError) as exc:                      # noqa: BLE001
                print(f"[警告] 旧基线读取失败（{exc}），跳过向下刷新护栏。")
        snapshot = {
            "version": 1,
            "assessed_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      __import__("time").gmtime()),
            "config": {"n_images": summary["n_images"],
                       "by_style": summary["by_style"],
                       "presets": list(PRESETS),
                       "sha_digest": summary["sha_digest"]},
            "per_preset": summary["per_preset"],
        }
        if not args.force_update:
            _pk = _refresh_pairs(_old, snapshot)
            _down = check_downward_pairs(_pk, _refresh_rules_for(_pk))
            if _down:
                for _line in render_refusal(_down, baseline_label="次级档面积基线"):
                    print(_line)
                return 1
        io.open(args.baseline, "w", encoding="utf-8").write(
            json.dumps(snapshot, ensure_ascii=False, indent=2))
        print(f"已刷新基线：{_safe_relpath(args.baseline)}")
        print("（刷新后继续按数据完整性、红线与漂移判定：刷新动作不得成为绕过判定的通道）")
        baseline = snapshot          # 用刚写入的基线比漂移（差值恒为 0）
    else:
        if not os.path.isfile(args.baseline):
            print(f"⏭ 跳过：基线缺失（{_safe_relpath(args.baseline)}）；"
                  f"先跑 python scripts/prompted_area_gate.py --update")
            return 2
        baseline = json.load(io.open(args.baseline, encoding="utf-8"))

    ok, problems = evaluate(summary, baseline)
    _render(summary, ok, problems)
    if args.json:
        io.open(args.json, "w", encoding="utf-8").write(
            json.dumps({"summary": summary, "ok": ok, "problems": problems},
                       ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
