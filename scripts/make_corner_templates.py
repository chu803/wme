#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台
 文件名称 : scripts/make_corner_templates.py
 文件说明 : 阶段 322b · 生成扩充版角标模板库（内嵌 base64 PNG），并做 A/B 标定。

 为什么要扩（证据不是感觉）
 ------------------------
 阶段 321 的 60 用例探针：角标图里只有 **5/15** 被推荐成 `ai_logo`，其余 9/15 落到
 `ai_text_heavy`；角标检回中位 0.267。根因是**风格覆盖**——内嵌模板库的 9 张全部来自
 同一个平台（豆包）的同一块角标板，NCC 最大归并只认那一种外观。

 本脚本生成 8 张新模板（9 → 17），两类来源：
   · **位姿增广**（4 张）：对真值模板做小平移/小旋转/小幅缩放，补「同一风格的不同摆位」；
   · **合成风格**（4 张）：圆徽、双行板、纯字线、深色板——都是**边缘图**层面的结构，
     与真值同处一个尺度（48×64、能量集中在右下）。

 合成模板的风险是**抬高自然图的最大 NCC**（库越大，max 越大 ⇒ 多重比较膨胀），
 所以 `--ab` 会在 88 张真实图上扫阈值，分别报告「角标召回」与「自然图误命中」，
 由数字决定用不用、阈值定多少；而不是先改了再说。

 用法：
   python scripts/make_corner_templates.py --stats          # 只看生成结果的结构统计
   python scripts/make_corner_templates.py --emit cache/b64.txt   # 写出新库的 base64
   python scripts/make_corner_templates.py --ab             # 88 图阈值扫描（9 vs 17，≈2 分钟）
==============================================================================
"""
from __future__ import annotations

import argparse
import base64
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TILE_W, TILE_H = 48, 64          # 与 core.preset_recommender 的 ROI 切片尺寸一致
REAL_COUNT = 9                   # 现有内嵌库张数（阶段 450）
BANK_COUNT = 17                  # 扩充后张数（阶段 322b）

SS = 4                           # 超采样倍数：细笔画直接画在 48×64 上会断


def _cv2():
    import cv2
    return cv2


def _edge_map(gray: np.ndarray) -> np.ndarray:
    """与生产信号同款的边缘图：Sobel 梯度幅值，再按最大值归一到 0~255。"""
    cv2 = _cv2()
    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    em = np.sqrt(gx * gx + gy * gy)
    peak = float(np.percentile(em, 99.9))
    if peak <= 1e-6:
        return np.zeros((TILE_H, TILE_W), np.uint8)
    out = np.clip(em / peak * 255.0, 0, 255)
    return cv2.resize(out.astype(np.uint8), (TILE_W, TILE_H), interpolation=cv2.INTER_AREA)


def _plate_gray(kind: str, seed: int) -> np.ndarray:
    """在超采样画布上画一块角标板（灰度），右下角为主（真值模板的能量质心在此）。"""
    cv2 = _cv2()
    H, W = TILE_H * SS, TILE_W * SS
    img = np.full((H, W), 128, np.uint8)
    rng = np.random.default_rng(seed)
    t = max(2, SS)                                   # 线宽随超采样放大

    def _box(cx: float, cy: float, bw: float, bh: float):
        """cx/cy 为归一化中心，bw/bh 为归一化宽高。"""
        x0 = int(cx * W - bw * W / 2); x1 = int(cx * W + bw * W / 2)
        y0 = int(cy * H - bh * H / 2); y1 = int(cy * H + bh * H / 2)
        return x0, y0, x1 - x0, y1 - y0

    if kind == "round_plate":
        x, y, w, h = _box(0.62, 0.80, 0.62, 0.30)
        cv2.rectangle(img, (x, y), (x + w, y + h), 245, -1)
        # 字形：3~5 个竖笔 + 一条横笔（近似「文字」的边缘结构）
        for i in range(4):
            gx0 = x + int(w * (0.14 + 0.19 * i))
            cv2.line(img, (gx0, y + int(h * 0.30)), (gx0, y + int(h * 0.72)), 20, 2 * t)
        cv2.line(img, (x + int(w * 0.10), y + int(h * 0.52)),
                 (x + int(w * 0.88), y + int(h * 0.52)), 20, 2 * t)
    elif kind == "circle_badge":
        cx, cy = int(0.66 * W), int(0.78 * H)
        r = int(0.19 * H)
        cv2.circle(img, (cx, cy), r, 240, -1)
        cv2.circle(img, (cx, cy), int(r * 0.62), 25, 2 * t)
        cv2.line(img, (cx - int(r * 0.5), cy), (cx + int(r * 0.5), cy), 25, 2 * t)
    elif kind == "two_line_plate":
        x, y, w, h = _box(0.60, 0.72, 0.66, 0.42)
        cv2.rectangle(img, (x, y), (x + w, y + h), 240, -1)
        for row, k in enumerate((0.32, 0.66)):
            yy = y + int(h * k)
            cv2.line(img, (x + int(w * 0.08), yy), (x + int(w * 0.90), yy), 25, 2 * t)
            for i in range(5):
                xx = x + int(w * (0.10 + 0.18 * i)) + int(rng.integers(-2, 3))
                cv2.line(img, (xx, yy - int(h * 0.12)), (xx, yy + int(h * 0.12)), 25, t)
    elif kind == "text_only":
        # 无底板的一行角标文字（很多平台是这种：只有描边字）
        y = int(0.80 * H)
        for i in range(6):
            xx = int(W * (0.30 + 0.11 * i))
            hh = int(H * (0.09 + 0.02 * (i % 3)))
            cv2.rectangle(img, (xx, y - hh), (xx + int(0.05 * W), y + hh), 235, t)
            cv2.line(img, (xx, y), (xx + int(0.05 * W), y - hh), 235, t)
    else:
        raise ValueError(f"未知合成风格：{kind}")
    return img


def _augment(tile: np.ndarray, dx: float, dy: float,
             ang: float, scale: float) -> np.ndarray:
    """真值模板的位姿增广：小平移 + 小旋转 + 小幅缩放（边缘图直接做仿射，够用且保序）。"""
    cv2 = _cv2()
    H, W = tile.shape[:2]
    M = cv2.getRotationMatrix2D((W * 0.62, H * 0.80), ang, scale)
    M[0, 2] += dx * W
    M[1, 2] += dy * H
    out = cv2.warpAffine(tile.astype(np.float32), M, (W, H),
                         flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    peak = float(np.percentile(out, 99.9))
    if peak > 1e-6:
        out = out / peak * 255.0
    return np.clip(out, 0, 255).astype(np.uint8)


def _decode_bank(b64: str) -> list:
    """解出现有内嵌库（不复制一份真值数据，单一来源 = 生产代码里的常量）。"""
    cv2 = _cv2()
    raw = base64.b64decode(b64)
    png = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if png is None:
        raise RuntimeError("内嵌模板库 PNG 解码失败")
    th = png.shape[0] // REAL_COUNT
    return [png[i * th:(i + 1) * th, :].astype(np.float32) for i in range(REAL_COUNT)]


def build_new_tiles() -> list:
    """8 张新模板（uint8, 48×64 高）：4 张位姿增广 + 4 张合成风格。"""
    from core import preset_recommender as pr
    real = _decode_bank(pr._CORNER_WATERMARK_BANK_B64)
    tiles = []
    # ① 位姿增广：挑真值里「结构最干净」的 4 张（能量占比最低，见 --stats 输出）
    for src, dx, dy, ang, sc in (
            (0, 0.04, -0.03, 3.0, 1.06),
            (3, -0.05, 0.02, -4.0, 0.92),
            (5, 0.03, 0.04, 2.0, 1.10),
            (8, -0.03, -0.05, -2.0, 0.95)):
        tiles.append(_augment(real[src], dx, dy, ang, sc))
    # ② 合成风格
    for i, kind in enumerate(("round_plate", "circle_badge", "two_line_plate", "text_only")):
        gray = _plate_gray(kind, 100 + i)
        tiles.append(_edge_map(gray))
    return tiles


def build_bank_b64() -> "tuple[str, list]":
    """拼成 17 张纵向堆叠的 PNG 并转 base64；返回 (b64, 全部 tile 列表)。"""
    cv2 = _cv2()
    from core import preset_recommender as pr
    real = [np.clip(t, 0, 255).astype(np.uint8) for t in _decode_bank(pr._CORNER_WATERMARK_BANK_B64)]
    tiles = real + build_new_tiles()
    assert len(tiles) == BANK_COUNT, len(tiles)
    sheet = np.vstack(tiles)
    ok, buf = cv2.imencode(".png", sheet)
    if not ok:
        raise RuntimeError("模板库 PNG 编码失败")
    return base64.b64encode(buf.tobytes()).decode("ascii"), tiles


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    x = a - a.mean()
    y = b - b.mean()
    den = float(np.sqrt((x * x).sum()) * np.sqrt((y * y).sum()))
    return float((x * y).sum() / den) if den > 1e-6 else 0.0


def _roi_scores(gray: np.ndarray) -> list:
    """复刻生产信号的 ROI 取法（br / mr），返回 [(roi 边缘图, 边缘均值), ...]。"""
    cv2 = _cv2()
    h, w = gray.shape[:2]
    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    em = np.sqrt(gx * gx + gy * gy)
    rois = []
    for sub in (em[int(h * 0.80):, int(w * 0.74):], em[int(h * 0.45):int(h * 0.72), int(w * 0.78):]):
        if sub.size == 0:
            continue
        r = cv2.resize(sub, (TILE_W, TILE_H))
        rois.append((r, float(r.mean())))
    return rois


def _max_ncc(gray: np.ndarray, bank: list) -> float:
    best = 0.0
    for r, _ in _roi_scores(gray):
        for tpl in bank:
            best = max(best, _ncc(r, tpl), _ncc(-r, tpl))
    return best


def run_ab(thresholds: list) -> int:
    """88 张真实图（含角标 GT 与自然图）上扫阈值：9 模板 vs 17 模板。"""
    import cv2
    import json
    from core import preset_recommender as pr
    from scripts.verify_corner_reco import _find_file, _is_br, IMG_DIR, GT

    real = [t.astype(np.float32) for t in _decode_bank(pr._CORNER_WATERMARK_BANK_B64)]
    bank_new = real + [t.astype(np.float32) for t in build_new_tiles()]

    corner_ids, natural_ids = [], []
    gt_corner_files = set()
    for e in GT["entries"]:
        hit_br = any(_is_br(tuple(w.get("bbox") or [])) for w in e["watermarks"])
        fn = _find_file(e["image_id"])
        if not fn:
            continue
        if hit_br:
            corner_ids.append(fn)
            gt_corner_files.add(fn)
    for n in sorted(os.listdir(IMG_DIR)):
        if n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")) and n not in gt_corner_files:
            natural_ids.append(n)

    scores: dict = {}
    for fn in corner_ids + natural_ids:
        bgr = cv2.imread(os.path.join(IMG_DIR, fn))
        if bgr is None:
            continue
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        scores[fn] = (_max_ncc(gray, real), _max_ncc(gray, bank_new))
    print(f"实测 {len(scores)} 张（角标 GT {len(corner_ids)}、非角标 {len(natural_ids)}）")

    print(f"\n{'阈值':>6}{'9 模板召回':>14}{'9 FP':>8}{'17 模板召回':>14}{'17 FP':>8}")
    print("-" * 52)
    for th in thresholds:
        c9 = sum(1 for f in corner_ids if scores.get(f, (0, 0))[0] >= th)
        c17 = sum(1 for f in corner_ids if scores.get(f, (0, 0))[1] >= th)
        f9 = [f for f in natural_ids if scores.get(f, (0, 0))[0] >= th]
        f17 = [f for f in natural_ids if scores.get(f, (0, 0))[1] >= th]
        n = len(corner_ids)
        print(f"{th:>6.2f}{f'{c9}/{n}':>14}{len(f9):>8}{f'{c17}/{n}':>14}{len(f17):>8}")
        if f17:
            print(f"        17 库误命中：{', '.join(f17[:4])}")
        # 逐图差异：谁被新库救回、谁被新库误伤
        gained = [f for f in corner_ids
                  if scores.get(f, (0, 0))[1] >= th > scores.get(f, (0, 0))[0]]
        lost = [f for f in corner_ids
                if scores.get(f, (0, 0))[0] >= th > scores.get(f, (0, 1))[1]]
        if gained or lost:
            print(f"        新库救回 {len(gained)}：{', '.join(x[:22] for x in gained[:4])}"
                  f" | 新库反而掉出 {len(lost)}：{', '.join(x[:22] for x in lost[:4])}")
    # 新库里每张模板「最强命中来自哪张真值图」：若每张新模板的最好命中都是
    # 自然图而非角标图，说明它买不到召回、只买误命中。
    print("\n每张新模板在 88 图上的最高 NCC（>0.30 才算有机会起作用）：")
    roi_cache = {}
    for fn in scores:                      # ROI 只算一次（Sobel 在 2048² 上不便宜）
        gray = cv2.cvtColor(cv2.imread(os.path.join(IMG_DIR, fn)), cv2.COLOR_BGR2GRAY)
        roi_cache[fn] = [r for r, _ in _roi_scores(gray)]
    for j, tpl in enumerate(bank_new[REAL_COUNT:]):
        best, best_fn = 0.0, ""
        for fn, rois in roi_cache.items():
            for r in rois:
                s = max(_ncc(r, tpl), _ncc(-r, tpl))
                if s > best:
                    best, best_fn = s, fn
        kind = "角标 GT" if best_fn in corner_ids else "自然图/非角标"
        print(f"  新模板 #{REAL_COUNT + j}: {best:.3f} @ {best_fn}（{kind}）")
    return 0


def run_stats() -> int:
    tiles = build_new_tiles()
    print(f"新模板 {len(tiles)} 张，形状 {tiles[0].shape} dtype {tiles[0].dtype}")
    print(f"{'#':>3}{'均值':>8}{'p99':>7}{'能量占比':>10}{'质心 x,y':>14}")
    for i, t in enumerate(tiles):
        v = t.astype(np.float32)
        m = v >= v.max() * 0.35
        ys, xs = np.nonzero(m)
        cx = xs.mean() / t.shape[1] if m.any() else -1
        cy = ys.mean() / t.shape[0] if m.any() else -1
        print(f"{i + REAL_COUNT:>3}{v.mean():>8.1f}{np.percentile(v, 99):>7.0f}"
              f"{m.mean():>10.3f}({cx:.2f},{cy:.2f})")
    print("\n参照：真值 9 张的能量占比 0.021~0.242、质心 x≈0.43~0.64、y≈0.52~0.86")
    return 0


def main(argv: list | None = None) -> int:
    # 阶段 331：输出编码加固（GBK 管道下打印 ⇒/✅ 会 UnicodeEncodeError ⇒ 「通过」被读成退出码 1）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="角标模板库扩充（阶段 322b）")
    ap.add_argument("--stats", action="store_true", help="打印新模板的结构统计")
    ap.add_argument("--emit", default=None, help="把 17 张合并库的 base64 写到该文件")
    ap.add_argument("--ab", action="store_true", help="88 图阈值扫描（9 vs 17 模板）")
    ap.add_argument("--thresholds", default="0.25,0.30,0.35,0.40",
                    help="逗号分隔的扫描阈值（默认覆盖现行 0.30 附近）")
    args = ap.parse_args(argv)

    if args.ab:
        ths = [float(x) for x in args.thresholds.split(",") if x.strip()]
        return run_ab(ths)
    if args.emit:
        b64, tiles = build_bank_b64()
        with open(args.emit, "w", encoding="ascii") as f:
            f.write(b64)
        print(f"已写 {args.emit}：{len(tiles)} 张 × {TILE_H}×{TILE_W}，base64 长度 {len(b64)}")
        return 0
    return run_stats()


if __name__ == "__main__":
    raise SystemExit(main())
