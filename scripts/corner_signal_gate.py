#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台
 文件名称 : scripts/corner_signal_gate.py
 文件说明 : 阶段 322b · **角标信号门禁**——把「角标 → ai_logo 的路由」从一次性标定升成守卫。

 为什么是它（而不是原计划的「扩模板库」）
 --------------------------------------
 原计划 322b 想扩角标模板库来提角标检回。`scripts/make_corner_templates.py --ab` 在
 **同一批真实图**（现目录 108 张，其中角标真值 49 张）上扫了 5 个阈值，结论是否定的：

   · 9 模板 @0.30 → 角标真值命中 **38/49**、非角标误命中 **0**；
   · 17 模板（+4 位姿增广 +4 合成风格）@0.30 → 命中仍是 **38**、误命中 **1**（`edge_text_A.jpg`）；
   · 更低的 0.26 才多救回 1 张，代价是误命中从 1 涨到 4。
   ⇒ 模板库的召回**天花板不是风格覆盖**，扩库买不到召回、只买误命中 ⇒ 不入库（负结果见 docs/322b）。
   （分母口径：本门禁重做 GT↔文件名匹配后是 49 张角标真值；阶段 450 当年记的是 50 张，
     见 §「匹配口径」——38 这个**分子两次实测完全一致**。）

 但这次标定顺手暴露了另一件事：**角标路由本身没有任何守卫**。它是阶段 450 一次性标定的
 结论（NCC≥0.30、命中 38、零误命中），此后 ROI 切片比例、`MIN_EDGE` 下限、模板解码条数
 都改过，却只有一个**只打印不判定**的 `verify_corner_reco.py`。任何一次「顺手调参」都可能
 把它悄悄打回 24/50 而 CI 全绿。

 本门禁就是把这个结论钉住：**走生产函数** `core.preset_recommender._corner_watermark_signal`
 （含 `config.RECOMMEND_CORNER_WATERMARK_*` 的全部口径），在全目录 108 张图上数两件事——
   R1 角标 GT 命中率 ≥ 下限（防路由失效）
   R2 非角标图误命中 == 0（防把自然图/边文字图送进 ai_logo 白跑一遍修复）

 退出码：0 通过 / 1 踩线 / 2 依赖或资产缺失（缺 cv2、缺 benchmark_real 图或 GT ⇒ 跳过，不是通过）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

IMAGES_DIR = os.path.join(_ROOT, "benchmark_real", "images")
GT_PATH = os.path.join(_ROOT, "benchmark_real", "ground_truth.json")

# 红线：38/50 是 450 标定与 322b 复测两次一致的数（见 docs/322b §二）。
# 下限取 36 而不是 38：留 2 张余量给「换 OpenCV 版本/图像解码差异」这类非产品抖动；
# **误命中必须是 0**——这是 450 当初选 0.30 而非 0.25 的理由（精确率 100%），没有余量可留。
CORNER_HIT_FLOOR = 36
NATURAL_FP_MAX = 0


def _skip_reason() -> str | None:
    try:
        import cv2  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"OpenCV 不可用：{exc}"
    if not os.path.isdir(IMAGES_DIR):
        return f"缺真实图目录：{IMAGES_DIR}"
    if not os.path.isfile(GT_PATH):
        return f"缺真值文件：{GT_PATH}"
    try:
        data = json.load(open(GT_PATH, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"真值文件读不出：{exc}"
    if not data.get("entries"):
        return "真值文件里没有条目"
    return None


def _is_bottom_right(bbox, w: int, h: int) -> bool:
    """角标判定：外接框中心落在图的右下区（比例化，兼容不同分辨率）。

    ⚠ 不复用 `verify_corner_reco._is_br` 的写死像素阈值（`x>1000 and y>1400`）——
    那套数只对 2048/2560 成立；这里按比例判，小图也能判。
    """
    if not bbox or len(bbox) != 4:
        return False
    x, y, bw, bh = bbox
    if bw <= 0 or bh <= 0:
        return False
    cx, cy = x + bw / 2.0, y + bh / 2.0
    return cx >= w * 0.62 and cy >= h * 0.62


# 已知且已核实的 GT 数据集缺口（2026-10-25 实测：`benchmark_real/images` 里没有这些 id 的
# 对应文件，`q1;`/`q2;` 这类带分号的图是**另外的图**，不能当它们的替身）。
# 门禁把「未匹配集合必须恰好等于这份清单」写成断言：新增的丢失照样报红，
# 已知的这 5 条不再每次都把门禁拖成常红。
KNOWN_MISSING_GT_IDS = frozenset({"q1_2", "q2_2", "q3_2", "q4_2", "q5_2"})


def _entry_for(stem: str, by_id: dict):
    """文件名 stem → GT 条目（**只认三种安全形态**，实测口径见下）。

    `ground_truth.json` 的 id 有两类历史形态：完整名（`a1`、`q1`、`1`）与 24 字符截断名
    （`ai_logo_03_white_coffee_`）。对应的文件名叫法：
      A. 精确同名；
      B. `id + "_"` 前缀（`a1` → `a1_extra`、`ai_logo_02_snow_mountain` → `..._mountain_lake`）；
      C. id 恰为 24 字符且是 stem 前缀（截断遗留：`ai_logo_03_white_coffee_` → `..._coffee_cup`）。

    ⚠ 不能写「stem 是 id 的前缀」这一条：GT 里有 `q1` 也有 `q1_2`，后者是**没入库的图**，
    一旦让 `q1` 去认领 `q1_2`（或反过来），「数据集缺图」就被洗成「匹配成功」。
    ⚠ C 必须带 `len(id)==24` 限制：GT 里有 47 个短 id（`1`…`c9`），裸 `stem.startswith(id)`
    会让 `1` 认领 `10_东京街头.jpg`，实测把角标真值集从 50 张撑到 **75 张**、命中率从 0.76 假降到 0.51。
    """
    if stem in by_id:
        return by_id[stem]
    cand = [i for i in by_id
            if stem.startswith(i + "_") or (len(i) == 24 and stem.startswith(i))]
    return by_id[max(cand, key=len)] if cand else None


def load_corner_ids() -> tuple[dict, set]:
    """返回 {文件名 stem: 该图的角标 bbox 列表} 与 GT 里出现过的 id 集合（供未匹配检查）。"""
    gt = json.load(open(GT_PATH, encoding="utf-8"))
    by_id = {e.get("image_id"): e for e in gt.get("entries", []) if e.get("image_id")}
    return by_id, set(by_id)


def _read_image(path: str):
    """中文文件名必须走 `np.fromfile + imdecode`（`cv2.imread` 在非 ASCII 路径上直接返回 None，
    项目里 `prompted_area_gate` 早就踩过；本阶段首轮实测：30 张中文名图「读不回」）。"""
    import cv2
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def classify() -> tuple[list, list, list, list, int, int]:
    """跑生产信号，返回 (角标命中, 角标漏, 误命中, 读不回的图, 角标图总数, GT 未匹配数)。

    「角标图」= 该图 GT 标注里存在落在右下区的框；判定用**图的真实宽高**做比例，
    因此换分辨率不会失效（`verify_corner_reco` 的 `x>1000 and y>1400` 是写死像素的）。
    """
    import cv2
    from core.preset_recommender import _corner_watermark_signal

    by_id, ids = load_corner_ids()
    matched_ids = set()
    hits, misses, fps, skipped = [], [], [], []
    corner_total = 0
    for fn in sorted(os.listdir(IMAGES_DIR)):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        stem = os.path.splitext(fn)[0]
        bgr = _read_image(os.path.join(IMAGES_DIR, fn))
        if bgr is None:
            skipped.append(fn)
            continue
        h, w = bgr.shape[:2]
        e = _entry_for(stem, by_id)
        is_corner = bool(e) and any(_is_bottom_right(tuple(x.get("bbox") or ()), w, h)
                                    for x in (e.get("watermarks") or []))
        if e:
            matched_ids.add(e.get("image_id"))
        fired = _corner_watermark_signal(np.asarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))) >= 1.0
        if is_corner:
            corner_total += 1
            (hits if fired else misses).append(fn)
        elif fired:
            fps.append(fn)
    return hits, misses, fps, skipped, corner_total, (ids - matched_ids)


def judge(hits: list, misses: list, fps: list, corner_total: int,
          unmatched_ids: set, skipped: int) -> tuple[list, dict]:
    """纯判据（可单测）：红线问题列表 + 本轮指标。"""
    problems: list = []
    got = len(hits) + len(misses)
    if corner_total == 0:
        problems.append("R0 真值里没有任何右下角标图（GT 解析或图像匹配坏了）")
    if got != corner_total:
        problems.append(f"R0 角标图分类不闭合：命中+漏={got} ≠ 角标总数 {corner_total}")
    if skipped:
        problems.append(f"R0 有 {skipped} 张图读不回（评测覆盖不全，不算通过）")
    new_missing = set(unmatched_ids) - set(KNOWN_MISSING_GT_IDS)
    recovered = set(KNOWN_MISSING_GT_IDS) - set(unmatched_ids)
    if new_missing:
        problems.append(f"R0 GT 新增丢失 {len(new_missing)} 条（图不在目录里）："
                        f"{', '.join(sorted(new_missing)[:6])}")
    if recovered:
        problems.append(f"R0 已知丢失清单里的 {', '.join(sorted(recovered))} 又有图了"
                        "⇒ 请把 KNOWN_MISSING_GT_IDS 更新掉（别留着当借口）")
    if len(hits) < CORNER_HIT_FLOOR:
        problems.append(f"R1 角标命中 {len(hits)}/{corner_total} < 下限 {CORNER_HIT_FLOOR}"
                        "（角标路由失效：这些图会被误推成 ai_text_heavy 等）")
    if len(fps) > NATURAL_FP_MAX:
        problems.append(f"R2 非角标图误命中 {len(fps)} > {NATURAL_FP_MAX}"
                        f"（它们会白跑一遍 ai_logo）：{', '.join(fps[:5])}")
    metrics = {"corner_total": corner_total, "hits": len(hits), "misses": len(misses),
               "fps": len(fps), "unmatched_gt": sorted(unmatched_ids), "skipped": skipped,
               "hit_rate": round(len(hits) / corner_total, 4) if corner_total else 0.0}
    return problems, metrics


def main(argv: list | None = None) -> int:
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()   # 门禁打印 ✅/❌，GBK 管道下会 UnicodeEncodeError（console_encoding 守卫）

    ap = argparse.ArgumentParser(description="角标信号门禁（真实图 × 生产信号）")
    ap.add_argument("--json", dest="json_path", default=None, help="把逐类明细写成 JSON")
    args = ap.parse_args(argv)

    reason = _skip_reason()
    if reason:
        print(f"跳过（依赖/资产缺失）：{reason}")
        return 2

    t0 = time.time()
    hits, misses, fps, skipped, corner_total, unmatched_ids = classify()
    problems, m = judge(hits, misses, fps, corner_total, unmatched_ids, len(skipped))
    print(f"角标信号门禁 · 耗时 {time.time() - t0:.1f}s")
    print(f"  角标真值 {m['corner_total']} 张：命中 {m['hits']}（命中率 {m['hit_rate']}）、"
          f"漏 {m['misses']}（下限 {CORNER_HIT_FLOOR}）")
    print(f"  非角标图误命中 {m['fps']} 张（必须为 {NATURAL_FP_MAX}）")
    print(f"  GT 未匹配 {len(unmatched_ids)} 条（已知缺口清单 {len(KNOWN_MISSING_GT_IDS)} 条）")
    if misses:
        print("  漏命中样本：" + ", ".join(misses[:8]))
    if skipped:
        print(f"  读不回的图 {len(skipped)} 张：{', '.join(skipped[:5])}")
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump({"metrics": m, "hits": hits, "misses": misses, "fps": fps,
                       "skipped": skipped,
                       "floors": {"hit": CORNER_HIT_FLOOR, "fp": NATURAL_FP_MAX}},
                      f, ensure_ascii=False, indent=2)
    if problems:
        print("\n红线未过：")
        for p in problems:
            print(f"  ❌ {p}")
        return 1
    print("\n✅ 角标路由在承诺内（命中未掉线、非角标零误命中）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
