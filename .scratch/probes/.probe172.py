# -*- coding: utf-8 -*-
"""列出交图目录里 ai_logo_* 的尺寸 / sha256 / 模式（ASCII 落盘）。"""
import hashlib
import os

from PIL import Image

SRC = r"D:\Desktop\照片"
lines = []
for fn in sorted(os.listdir(SRC)):
    if not fn.lower().startswith("ai_logo"):
        continue
    p = os.path.join(SRC, fn)
    try:
        with Image.open(p) as im:
            w, h = im.size
            mode = im.mode
            fmt = im.format
    except Exception as exc:  # noqa: BLE001
        lines.append(f"{fn}\tERR {exc!r}")
        continue
    sha = hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]
    lines.append(f"{fn}\t{w}x{h}\t{mode}/{fmt}\tsha={sha}")

with open(".probe172.result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines).encode("ascii", "replace").decode("ascii") + "\n")
print("done")
