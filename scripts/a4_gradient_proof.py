# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 194（A4 准备）· ai_gradient 真渐变证伪实验脚本

 背景
 ------
 `config.WATERMARK_TYPE_KNOWN_SYNTHETIC_GAP = {"ai_gradient"}` —— 检测器对「全局
 渐变叠加水印」通路的真实表现**从未被充分证伪**：阶段 118 的 3 张合成样本实测
 0/0/0，但那 3 张其实**不是真渐变水印**（v1 提示词只说「叠加渐变」、没禁别的，
 被豆包画成了大号 LOGO）。`docs/117` §3.4 已给出 **v2 修正版提示词**（强否定约束 +
 自检清单），可重出 3 张**真渐变**图一锤定音。

 本脚本是「等图闭环」的骨架：你用 v2 提示词出 3 张真渐变图后，一条命令即可得出
 `ai_gradient` 通路能否被识别的结论，据此决定「**修通路**」还是「**从宣传面降级**」。

 关键陷阱（务必读）
 -------------------
 `config.DETECT_GRADIENT_ENABLED` **默认 False**（config.py:1487）。也就是说，
 默认 `detect_watermark_mask` **根本不跑渐变通道** —— 若直接用默认值，会得到假阴性的
 「零触发」并被误读为「证伪成功」。故本脚本**强制 `enable_gradient=True`**，只回答
 「渐变通道打开后，真渐变图能否被识别」这一个干净的问题。

 用法
 ----
   python scripts/a4_gradient_proof.py --dir <出图目录>
   python scripts/a4_gradient_proof.py --image a.png b.png c.png
   python scripts/a4_gradient_proof.py --dir <dir> --json out.json

 完整闭环（你出图后由 AI 接管的部分）：
   1. 用 `docs/117` §3.4 v2 提示词 + §3.5 金标增强，在豆包出 3~5 张真渐变图（带 _clean 版）
   2. python scripts/collect_real_assets.py add <路径> --ai-source other --style gradient_watermark --copy
   3. python scripts/real_asset_preflight.py        # 来源与结构预检
   4. python scripts/a4_gradient_proof.py --dir <出图目录>   # ← 本脚本
   5. 看 N 张里触发了几张 → 判定（见下方 print_summary 的判定建议）

 判定口径
 --------
   触发数 == 0  → 检测器对真渐变**恒零触发**，证据支持「要么修通路（改 is_gradient
                  判据/特征），要么把 ai_gradient 从宣传面降级（README 不再列其为支持类型）」。
   触发数  > 0  → 通路能识别真渐变，但还需进一步测「修复质量」（mask 是否整图、修复后是否留痕）。

 版本：1.0.0
==============================================================================
"""
from __future__ import annotations

import argparse
import glob
import json
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402

from core.detector import KIND_GRADIENT, detect_watermark_mask  # noqa: E402


def prove_image(path: str, *, force_gradient: bool = True) -> dict:
    """对单张图显式开启渐变通道跑检测，统计 KIND_GRADIENT 命中。

    参数：
        path            : 图片路径。
        force_gradient  : 强制 enable_gradient=True（默认）。除非你特意想对比默认行为，
                          否则不要关 —— 默认 False 会假阴性零触发，误导证伪结论。

    返回：
        {
          "path": str,
          "gradient_triggered": bool,        # 是否有任意 KIND_GRADIENT 区域
          "gradient_region_count": int,     # KIND_GRADIENT 区域数（命中应为 1：整图）
          "coverage_ratio": float,          # 渐变掩码占整图比例（命中应为 ~1.0）
          "all_region_count": int,          # 全部区域数（含其它通道，仅供观察）
        }
    """
    img = np.asarray(Image.open(path).convert("RGB"))
    res = detect_watermark_mask(img, enable_gradient=force_gradient)
    grad_regions = [r for r in res.regions if r.kind == KIND_GRADIENT]
    h, w = img.shape[:2]
    covered = sum(int(r.w) * int(r.h) for r in grad_regions)
    return {
        "path": _os.path.basename(path),
        "gradient_triggered": len(grad_regions) > 0,
        "gradient_region_count": len(grad_regions),
        "coverage_ratio": (covered / (h * w)) if (h * w) else 0.0,
        "all_region_count": len(res.regions),
    }


def collect_images(args) -> list[str]:
    paths: list[str] = []
    if args.image:
        paths.extend(args.image)
    if args.dir:
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            paths.extend(glob.glob(_os.path.join(args.dir, ext)))
    # 去重、保序
    seen = set()
    out = []
    for p in paths:
        ap = _os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            out.append(p)
    return sorted(out)


def print_summary(records: list[dict]) -> int:
    """打印每图一行 + 汇总，返回触发图数（供判定）。"""
    n = len(records)
    triggered = sum(1 for r in records if r["gradient_triggered"])
    print(f"\n=== A4 真渐变证伪：共 {n} 张，渐变通道命中 {triggered} 张 ===")
    for r in records:
        flag = "✅命中" if r["gradient_triggered"] else "❌零触发"
        cov = f"{r['coverage_ratio'] * 100:.1f}%"
        print(f"  {flag}  {r['path']:<40}  区域数={r['gradient_region_count']}  覆盖={cov}")

    print("\n--- 判定建议 ---")
    if triggered == 0:
        print("  ⚠ 检测器对真渐变**恒零触发**（渐变通道已强制开启）。")
        print("    证据支持二选一：① 修通路（放宽 detect_gradient_watermark 的 is_gradient 判据，")
        print("                      如 plane_mag / explained / res_std 阈值，或换更鲁棒的低频特征）；")
        print("                    ② 从宣传面降级（README 不再把 ai_gradient 列为「支持类型」，")
        print("                      保留于 KNOWN_SYNTHETIC_GAP 显式钉死缺口）。")
    else:
        print(f"  渐变通路可识别真渐变（{triggered}/{n} 张命中）。")
        print("  下一步需测「修复质量」：mask 是否整图、修复后是否留痕、与其它通道是否冲突。")
    return triggered


def main(argv=None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。本脚本打印 ✅/❌/⚠ 等非
    # GBK 字符，若不加固，在 GBK 管道的 CI 下会抛 UnicodeEncodeError 使退出码变 1（假红）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="A4 ai_gradient 真渐变证伪实验脚本")
    ap.add_argument("--image", nargs="+", help="直接指定图片路径（可多张）")
    ap.add_argument("--dir", help="目录，递归 glob 常见图片后缀")
    ap.add_argument("--json", help="把每图结果与汇总写到的 JSON 路径")
    ap.add_argument("--no-force-gradient", action="store_true",
                    help="危险：故意用默认 enable_gradient=False 跑（仅用于对比假阴性）")
    args = ap.parse_args(argv)

    paths = collect_images(args)
    if not paths:
        print("未找到任何图片。用法见脚本 docstring（--dir 或 --image）。", file=_sys.stderr)
        return 2

    force = not args.no_force_gradient
    records = [prove_image(p, force_gradient=force) for p in paths]

    triggered = print_summary(records)

    if args.json:
        payload = {
            "force_gradient": force,
            "total": len(records),
            "triggered": triggered,
            "records": records,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 摘要已写入：{args.json}")

    # 退出码：0=有触发（通路可用）/ 1=零触发（需决策）/ 2=无输入
    return 0 if triggered > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
