# -*- coding: utf-8 -*-
"""写入 10 条 ai_logo 真值（bbox 逐图量取；labeled_by=manual:measured）。"""
import datetime
import json

P = "benchmark_real/ground_truth.json"

# id -> (x0, y0, x1, y1)  右下角「豆包AI」角标板
BOXES = {
    "ai_logo_02_snow_mountain": (1690, 1884, 2005, 2002),
    "ai_logo_03_white_coffee_": (1688, 1884, 2005, 2002),
    "ai_logo_04_neon_street_n": (1629, 1884, 1995, 2002),
    "ai_logo_05_fruit_platter": (1701, 1884, 2005, 2002),
    "ai_logo_06_flower_field_": (1680, 1884, 2005, 2002),
    "ai_logo_07_glass_curtain": (1680, 1884, 2005, 2002),
    "ai_logo_08_abstract_flui": (1678, 1884, 2005, 2002),
    "ai_logo_09_vintage_typew": (1620, 1884, 1985, 2002),
    "ai_logo_10_lavender_hot_": (1651, 1884, 2005, 2002),
    "ai_logo_test_01_orange_c": (1692, 1884, 2005, 2002),
}

d = json.load(open(P, encoding="utf-8"))
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
entries = []
for iid, (x0, y0, x1, y1) in BOXES.items():
    entries.append({
        "image_id": iid,
        "watermarks": [{
            "bbox": [x0, y0, x1 - x0, y1 - y0],
            "style": "corner_logo",
            "note": "右下角「豆包AI」平台角标（豆包生成图自带水印）",
        }],
        "labeled_at": ts,
        "labeled_by": "manual:measured",
    })
d["version"] = 1
d["entries"] = entries
d["_note"] = ("阶段 172：收编 10 张豆包生成的真实图（原图带右下角「豆包AI」角标）。"
              "bbox 逐图量取（亮度剖面定位角标板边界 + 人眼复核），"
              "labeled_by=manual:measured（非检测器输出，非 auto）。"
              "对应承诺类型 ai_logo（truth_styles=corner_logo/logo_watermark）。")
with open(P, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print(f"wrote {len(entries)} entries")
