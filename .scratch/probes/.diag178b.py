"""诊断：4 张低对比底的全部检测区域（看 mask_recall 的来源通路）。"""
import sys, json
import numpy as np
sys.path.insert(0, ".")
from PIL import Image
from core.detector import detect_watermark_mask

B = "benchmark_real"
truth = json.load(open(B + "/ground_truth.json", encoding="utf-8"))
tby = {e["image_id"]: e["watermarks"][0]["bbox"] for e in truth["entries"]}
man = json.load(open(B + "/manifest.json", encoding="utf-8"))
id2path = {im["id"]: im["path"] for im in man["images"]}
TARGET = ["ai_logo_06_flower_field_", "ai_logo_07_glass_curtain",
          "ai_logo_08_abstract_flui", "ai_logo_09_vintage_typew"]

for iid in TARGET:
    arr = np.asarray(Image.open(B + "/" + id2path[iid]).convert("RGB"))
    res = detect_watermark_mask(arr, preset="general", use_ocr=False)
    m = res.mask
    if m.ndim == 3:
        m = m[:, :, 0]
    gx, gy, gw, gh = tby[iid]
    gt_mask = np.zeros_like(m)
    gt_mask[gy:gy+gh, gx:gx+gw] = 1
    recall = int((m[gt_mask > 0] > 0).sum()) / (gw * gh)
    print(f"\n### {iid}  mask_recall={recall:.3f}  总区域数={len(res.regions)}")
    # 按 kind 统计
    from collections import Counter
    c = Counter(getattr(r, "kind", "?") for r in res.regions)
    print("   kind计数:", dict(c))
    for r in res.regions:
        ix = max(r.x, gx); iy = max(r.y, gy)
        ix2 = min(r.x + r.w, gx + gw); iy2 = min(r.y + r.h, gy + gh)
        ov = max(0, ix2 - ix) * max(0, iy2 - iy)
        in_gt = "INSIDE" if (r.x >= gx-5 and r.y >= gy-5 and r.x+r.w <= gx+gw+5 and r.y+r.h <= gy+gh+5) else ""
        print(f"   x={r.x} y={r.y} w={r.w} h={r.h} kind={getattr(r,'kind','?')} "
              f"conf={getattr(r,'confidence',None):.2f} GT交叠={ov}px {in_gt}")
