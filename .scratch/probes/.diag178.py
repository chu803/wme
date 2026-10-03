"""诊断：真实检测器在 4 张低对比底上的实际输出 + 逐图 mask_recall。"""
import sys, json
import numpy as np
sys.path.insert(0, ".")
from PIL import Image
import config
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
    inter = int((m[gt_mask > 0] > 0).sum())
    gt_area = gw * gh
    recall = inter / gt_area
    # 输出 corner_logo 区域
    regs = [r for r in res.regions if getattr(r, "kind", None) and "corner" in r.kind]
    print(f"\n### {iid}  mask_recall={recall:.3f}  corner_logo区域数={len(regs)}")
    for r in regs:
        # 该区域与 GT 交叠
        ix = max(r.x, gx); iy = max(r.y, gy)
        ix2 = min(r.x + r.w, gx + gw); iy2 = min(r.y + r.h, gy + gh)
        ov = max(0, ix2 - ix) * max(0, iy2 - iy)
        print(f"   region x={r.x} y={r.y} w={r.w} h={r.h} kind={r.kind} "
              f"conf={getattr(r,'confidence',None):.2f} 与GT交叠={ov}px")
    if not regs:
        print("   >>> 无任何 corner_logo 区域输出！")
