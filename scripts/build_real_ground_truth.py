"""
方向 A · 真实图真值标注 CLI
============================

职责
----
- 把每张图的 watermark 真实位置（bbox）登记到 `benchmark_real/ground_truth.json`；
- 提供「**自动预填**」：用 detector 默认预设跑一遍，把命中的 regions 转成 bbox 候选；
  用户再用任何文本编辑器打开 JSON 微调（增删 / 改 style / 加 note）。

CLI 用法
--------
    # 自动预填：用 detector 的 general 预设跑每张图，把命中的 bbox 写入真值
    python scripts/build_real_ground_truth.py auto [--preset general]

    # 列出覆盖：哪些图有真值 / 哪些没有
    python scripts/build_real_ground_truth.py list

    # 校验真值：bbox 在图内、style 在枚举内、必填字段不缺
    python scripts/build_real_ground_truth.py validate

ground_truth.json schema
------------------------
    {
      "version": 1,
      "entries": [
        {
          "image_id": "mj_001",
          "watermarks": [
            {
              "bbox": [x, y, w, h],   # 像素，[x, y, w, h]
              "style": "corner_logo",  # 见 STYLE_ENUM
              "note": "右下角 MJ 签名"  # 可选
            }
          ],
          "labeled_at": "2026-09-13T14:30:00Z",
          "labeled_by": "auto:general"  # 标注来源（auto:preset_name / manual）
        }
      ]
    }

约束
----
- bbox 必须是 [x, y, w, h]，w/h > 0；
- 必须在原图尺寸内；
- style 必须在 STYLE_ENUM 中；
- 自动预填**不覆盖**已有真值（同 image_id 已有 entries 时跳过）；
  如需重生成请先手工删除对应 entries。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

BENCH_DIR = os.environ.get("WME_BENCH_DIR") or os.path.join(_ROOT, "benchmark_real")
IMAGES_DIR = os.path.join(BENCH_DIR, "images")
MANIFEST_PATH = os.path.join(BENCH_DIR, "manifest.json")
TRUTH_PATH = os.path.join(BENCH_DIR, "ground_truth.json")

# 与 core.detector.KIND_* 严格对齐；额外加 overlay / mixed / unknown。
STYLE_ENUM = {
    "corner_logo",          # = KIND_CORNER_LOGO
    "edge_text",            # = KIND_EDGE_TEXT
    "grid_watermark",       # = KIND_GRID
    "logo_watermark",       # = KIND_LOGO
    "gradient_watermark",   # = KIND_GRADIENT
    "translucent",          # = KIND_TRANSLUCENT
    "ocr_text",             # = KIND_OCR_TEXT（直接 OCR 命中，无明确视觉风格）
    "overlay",              # 整图半透明叠加（Firefly 风）
    "mixed",                # 多风格并存
    "unknown",
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_manifest() -> Dict[str, Any]:
    if not os.path.exists(MANIFEST_PATH):
        return {"version": 1, "images": []}
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_truth() -> Dict[str, Any]:
    if not os.path.exists(TRUTH_PATH):
        return {"version": 1, "entries": []}
    try:
        with open(TRUTH_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "entries" not in data:
            raise ValueError("ground_truth 结构异常")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[error] ground_truth.json 损坏：{e}（拒绝写入以免覆盖）", file=sys.stderr)
        sys.exit(2)


def _save_truth(truth: Dict[str, Any]) -> None:
    os.makedirs(BENCH_DIR, exist_ok=True)
    with open(TRUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)


def _abs_image_path(rel: str) -> str:
    p = rel if os.path.isabs(rel) else os.path.join(BENCH_DIR, rel)
    return os.path.normpath(p)


# -----------------------------------------------------------------------------#
# 子命令：auto / list / validate
# -----------------------------------------------------------------------------#
def cmd_auto(args: argparse.Namespace) -> int:
    """用 detector 预填真值。"""
    import numpy as np
    from PIL import Image

    from core.detector import detect_watermark_mask

    preset = args.preset
    manifest = _load_manifest()
    truth = _load_truth()
    have_ids = {e["image_id"] for e in truth["entries"]}

    added = 0
    skipped = 0
    failed = 0
    for im in manifest.get("images", []):
        iid = im.get("id")
        if not iid:
            continue
        if iid in have_ids:
            skipped += 1
            continue
        abs_p = _abs_image_path(im["path"])
        if not os.path.isfile(abs_p):
            print(f"[skip] 文件不存在：{abs_p}", file=sys.stderr)
            failed += 1
            continue
        try:
            arr = np.array(Image.open(abs_p).convert("RGB"))
        except Exception as e:
            print(f"[error] 读图失败 {abs_p}：{e}", file=sys.stderr)
            failed += 1
            continue
        try:
            res = detect_watermark_mask(arr, preset=preset, use_ocr=False)
        except Exception as e:
            print(f"[error] 检测失败 {iid}：{e}", file=sys.stderr)
            failed += 1
            continue
        H, W = arr.shape[0], arr.shape[1]
        watermarks = []
        for reg in res.regions:
            x, y, w, h = int(reg.x), int(reg.y), int(reg.w), int(reg.h)
            # 强制契约：auto 写入的真值须经得起 audit_truth 校验，否则会静默污染
            # ground_truth.json——越界/非正 bbox 令 validate 失败；空 watermarks 被读成
            # 「没水印」且污染 have_ids 阻断后续重标。故写入前就地校验，无效候选跳过。
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > W or y + h > H:
                print(f"[skip] {iid} 候选 bbox 越界/非正 ({x},{y},{w},{h})，跳过",
                      file=sys.stderr)
                continue
            style = reg.kind if reg.kind in STYLE_ENUM else "unknown"
            watermarks.append({"bbox": [x, y, w, h], "style": style, "note": ""})
        if not watermarks:
            print(f"[auto] {iid}  无有效候选 bbox（跳过，不写空标注）")
            continue
        truth["entries"].append({
            "image_id": iid,
            "watermarks": watermarks,
            "labeled_at": _now_iso(),
            "labeled_by": f"auto:{preset}",
        })
        added += 1
        print(f"[auto] {iid}  候选 bbox={len(watermarks)}  size={W}x{H}")

    _save_truth(truth)
    print(f"\n预填完成。新增 {added}、跳过(已有) {skipped}、失败 {failed}。"
          f"\n请用文本编辑器打开 ground_truth.json 微调（增删 / 改 style / 加 note）。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    truth = _load_truth()
    have = {e["image_id"]: e for e in truth["entries"]}
    print(f"{'id':<24} {'ai_source':<16} {'bbox数':<6} {'来源':<14}")
    print("-" * 70)
    miss = 0
    for im in manifest.get("images", []):
        iid = im.get("id", "?")
        e = have.get(iid)
        if e is None:
            miss += 1
            print(f"{iid:<24} {im.get('ai_source', '?'):<16} {'-':<6} {'(无真值)':<14}")
        else:
            print(f"{iid:<24} {im.get('ai_source', '?'):<16} {len(e.get('watermarks', [])):<6} "
                  f"{e.get('labeled_by', '?'):<14}")
    print(f"\n缺真值：{miss} 张；总计 {len(manifest.get('images', []))} 张。")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """校验 `ground_truth.json` 结构。

    阶段 153：**改为复用 `real_asset_preflight.structural_problems`**（惰性导入）。
    此前本命令自己实现了**一套更宽松**的检查——孤儿条目只 `[warn]` 不判错、不查
    `labeled_by`、不查空 `watermarks`、不识别 `auto:` 预填——于是同一个真值文件会出现
    「`validate` 绿（errors=0）而 `preflight` 红（rc=1）」的**工具矛盾**；而 `preflight`
    才是承诺面门禁，用户会被 validate 的绿灯误导（阶段 153 审计发现）。
    现在两处共用同一判据（含「bbox 非负 / 在图内」两条，已上收到 `audit_truth`）。
    """
    manifest = _load_manifest()
    truth = _load_truth()

    from scripts.real_asset_preflight import structural_problems   # 惰性：避免模块级循环导入
    problems = structural_problems(manifest, truth)
    for p in problems:
        print(f"[err] {p}", file=sys.stderr)
    errs = len(problems)
    n_marks = sum(len(e.get("watermarks") or []) for e in truth.get("entries", []))
    print(f"\n校验完成：ok={max(0, n_marks - errs)}, errors={errs}")

    # 阶段 93：清单声明 ↔ 真值覆盖面的相容性检查（**脚本级，不靠人眼**）。
    #   背景（docs/92 缺口 S1）：30 张图的清单全部声明 `watermark_style_hint="mixed"`，
    #   而真值 60 个标注全部是 `corner_logo`。未被标注的水印**不会计入漏检**，
    #   于是召回被结构性抬到 1.0000。此前这种不一致**是靠肉眼发现的**，现在由脚本发现。
    #   实现复用 `real_gate.annotation_scope`（同一份代码），避免「同一规则两套实现」。
    try:
        from scripts.real_gate import annotation_scope, render_scope_banner
        scope = annotation_scope(manifest, truth)
    except Exception as exc:                              # pragma: no cover - 兜底
        print(f"[warn] 覆盖面检查不可用：{exc}", file=sys.stderr)
        return 1 if errs else 0

    for c in scope["contradictory"]:
        print(f"[err] 声明/真值矛盾：{c}", file=sys.stderr)
        errs += 1
    # 注：枚举外 style 已由 `structural_problems → audit_truth` 报出，此处不再重复计数。

    banner = render_scope_banner(scope)
    if banner:
        print(banner)
    print(f"标注覆盖面：{scope['n_labeled_styles']} 类"
          f"（{'、'.join(scope['labeled']) or '无'}）；"
          f"具体声明 {scope['declared_images']} 张 / "
          f"catch-all 声明 {scope['unspecified_images']} 张"
          f" → {'完整' if scope['complete'] else '不完整（召回只代表已标注类型）'}")
    return 1 if errs else 0


# -----------------------------------------------------------------------------#
# 入口
# -----------------------------------------------------------------------------#
def main() -> int:
    ap = argparse.ArgumentParser(description="真实图真值标注 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_auto = sub.add_parser("auto", help="用 detector 预填（不覆盖已有）")
    p_auto.add_argument("--preset", default="general", help="检测预设（默认 general）")
    p_auto.set_defaults(func=cmd_auto)

    p_list = sub.add_parser("list", help="列出真值覆盖情况")
    p_list.set_defaults(func=cmd_list)

    p_val = sub.add_parser("validate", help="校验 ground_truth.json 结构与 bbox")
    p_val.set_defaults(func=cmd_validate)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())