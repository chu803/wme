"""实测 179 牌体补全先验（杠杆 ON）对 10 张真实图的影响。"""
import sys, json, os
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
TARGET = {"ai_logo_06_flower_field_", "ai_logo_07_glass_curtain",
          "ai_logo_08_abstract_flui", "ai_logo_09_vintage_typew"}

ti = tt = 0
print("%-28s %6s %10s" % ("image", "mask_rec", "corner#"))
for im in man["images"]:
    iid = im["id"]
    if iid not in tby:
        continue
    arr = np.asarray(Image.open(B + "/" + id2path[iid]).convert("RGB"))
    res = detect_watermark_mask(arr, preset="general", use_ocr=False)
    m = res.mask
    if m.ndim == 3:
        m = m[:, :, 0]
    gx, gy, gw, gh = tby[iid]
    inter = int((m[gy:gy+gh, gx:gx+gw] > 0).sum())
    rec = inter / (gw * gh)
    ti += inter; tt += gw * gh
    ncl = sum(1 for r in res.regions if getattr(r, "kind", None) == "corner_logo")
    tag = "<<目标>>" if iid in TARGET else ""
    print("%-28s %6.3f %10d %s" % (iid, rec, ncl, tag))

print("\nMICRO mask_recall (10 图) = %.4f" % (ti / tt))
