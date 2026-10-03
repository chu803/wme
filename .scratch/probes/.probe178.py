"""阶段 178 探针：lift 连通块形态独立标定。

复现 core/detector 的角落搜索区(DETECT_CORNER_RATIO=0.25)与 lift 计算口径
(lift = V - blur(V,k) > delta)，扫描 10 张真实图，对 lift 连通块做形态画像：
area / aspect / solidity / ink，并与 GT 牌框交叠(IOU)。

目标：找一组「lift 专属」过滤阈值（不改 whiteness），使得
  - 4 张低对比底(06/07/08/09)上，有 lift 连通块命中 GT 牌框；
  - 6 张对照(02/03/04/05/10/test_01)上，非牌区无 lift 连通块通过阈值（无误报）。
"""
import sys, os, json
import numpy as np
sys.path.insert(0, ".")
import config
from PIL import Image
import cv2  # 直接依赖，与 detector 一致

B = "benchmark_real"
truth = json.load(open(B + "/ground_truth.json", encoding="utf-8"))
tby = {e["image_id"]: e["watermarks"][0]["bbox"] for e in truth["entries"]}
# 4 张低对比底目标 + 6 张对照
TARGET = {"ai_logo_06_flower_field_", "ai_logo_07_glass_curtain",
          "ai_logo_08_abstract_flui", "ai_logo_09_vintage_typew"}
man = json.load(open(B + "/manifest.json", encoding="utf-8"))
id2path = {im["id"]: im["path"] for im in man["images"]}

RATIO = config.DETECT_CORNER_RATIO
INVALID = (1 << 31) - 1


def corner_patches(h, w, ratio, search_bottom=config.DETECT_SEARCH_BOTTOM):
    side = max(8, int(min(h, w) * ratio))
    ps = [(0, 0, side, side), (w - side, 0, side, side),
          (0, h - side, side, side), (w - side, h - side, side, side),
          (w // 2 - side // 2, h // 2 - side // 2, side, side)]
    if search_bottom:
        bw = int(side * 2.2); bh = side
        bx = max(0, w // 2 - bw // 2); by = max(0, h - bh)
        ps.append((bx, by, min(bw, w - bx), bh))
    return ps


def iou_box(b1, b2):
    x1, y1, w1, h1 = b1; x2, y2, w2, h2 = b2
    xa, ya = max(x1, x2), max(y1, y2)
    xb, yb = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
    iw, ih = max(0, xb - xa), max(0, yb - ya)
    inter = iw * ih
    return inter / max(1, (w1 * h1 + w2 * h2 - inter))


def cc_morph(bin_c, gray, px, py):
    """复现 _plate_boxes_in_patch 的形态量，但不过滤，返回全部 CC 画像。"""
    bin_c = bin_c.astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(bin_c, 8)
    out = []
    for i in range(1, num):
        x, y, bw, bh, area = (int(t) for t in stats[i])
        if bw < 2 or bh < 2:
            continue
        aspect = max(bw, bh) / max(1, min(bw, bh))
        solid = area / float(bw * bh)
        comp = labels[y:y + bh, x:x + bw] == i
        sub = gray[y:y + bh, x:x + bw]
        ref = float(np.median(sub[comp])) if comp.any() else 0.0
        ink = float((sub <= config.DETECT_PLATE_INK_RATIO * ref).mean()) if ref > 0 else 0.0
        out.append({"bbox": (px + x, py + y, bw, bh),
                    "area": area, "aspect": aspect, "solid": solid, "ink": ink})
    return out


def lift_mask(v, k, delta):
    k = max(3, k)
    if k % 2 == 0:
        k += 1
    return (v - cv2.blur(v, (k, k))) > float(delta)


print("=" * 100)
print("探针：lift 连通块形态画像（逐图 / 逐 (k_frac,delta)）")
print("=" * 100)
# 多种 (k_frac, delta) 组合
KDELTAS = [(0.15, 10), (0.25, 10), (0.40, 14), (0.25, 16)]
for im in man["images"]:
    iid = im["id"]
    if iid not in tby:
        continue
    arr = np.asarray(Image.open(B + "/" + im["path"]).convert("RGB"))
    h, w = arr.shape[:2]
    g = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    v = hsv[:, :, 2].astype(np.float32)
    s = hsv[:, :, 1].astype(np.int16)
    gt = tby[iid]  # (x,y,w,h) 原图坐标
    is_tgt = iid in TARGET
    print(f"\n### {iid}  {'<<<目标低对比底>>>' if is_tgt else '(对照)'}  dim={w}x{h}  GT={gt}")

    # whiteness 命中情况（作为参照）
    white = ((v >= config.DETECT_PLATE_V_MIN) & (s <= config.DETECT_PLATE_S_MAX)).astype(np.uint8)
    white_recall = float((white[gt[1]:gt[1]+gt[3], gt[0]:gt[0]+gt[2]] > 0).mean())
    print(f"  [whiteness] GT框内覆盖={white_recall:.3f}")

    for (kf, delta) in KDELTAS:
        k = int(round(kf * max(h, w)))
        lm = lift_mask(v, k, delta)
        # 扫描所有搜索区，统计
        plate_hit = None
        fp_any = []  # 非牌区命中的 CC
        for (px, py, pw, ph) in corner_patches(h, w, RATIO):
            m = lm[py:py + ph, px:px + pw]
            ccs = cc_morph(m, g[py:py+ph, px:px+pw], px, py)
            for c in ccs:
                bi = iou_box(c["bbox"], gt)
                if bi > 0.01:
                    if plate_hit is None or c["area"] > plate_hit["area"]:
                        plate_hit = dict(c, iou=bi)
                else:
                    # 非牌区：记录其形态（潜在误报）
                    fp_any.append(c)
        if plate_hit:
            ph2 = plate_hit
            print(f"  lift(k={k},d={delta}): 命中牌 iou={ph2['iou']:.2f} "
                  f"area={ph2['area']} aspect={ph2['aspect']:.2f} "
                  f"solid={ph2['solid']:.3f} ink={ph2['ink']:.3f}")
        else:
            # 看最大 CC 是否接近牌（取所有 CC 中离牌最近的）
            print(f"  lift(k={k},d={delta}): 无任何 CC 命中牌框 | 非牌区 CC 数={len(fp_any)}")
        if fp_any and is_tgt is False:
            # 对照图：报告非牌区最大的几个 CC
            fp_any.sort(key=lambda c: -c["area"])
            top = fp_any[:2]
            for c in top:
                print(f"      [对照误报候选] area={c['area']} aspect={c['aspect']:.2f} "
                      f"solid={c['solid']:.3f} ink={c['ink']:.3f} bbox={c['bbox']}")
