# -*- coding: utf-8 -*-
"""按豆包 manifest 的**精确几何参数**本地复刻那 12 张测试图。

为什么要复刻：豆包把 PNG 放在它自己的沙盒里（附件只有 md）⇒ 我这边没有像素。
但 #42 要量的是**几何**（板宽/位置/透明度/有无底板），几何参数 manifest 里写得很死
⇒ 用同样的参数 + 本地纹理底图复刻，量到的召回/误伤对"换几何还认不认得出"这个问题是等价的。

字体：统一用**随源码分发**的 `scripts/assets/DejaVuSans-Bold.ttf`（阶段 369 的字体纪律：
不许引用系统字体目录；仓库里没有 CJK 字体 ⇒ 板内文字用 ASCII，等价且可复现）。
参数来源：manifest.md（B 组 PIL 叠加参数明细）。
"""
from __future__ import annotations

import json
import os
import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmark_real" / "challenge" / "images"
OUT.mkdir(parents=True, exist_ok=True)
CLEAN = ROOT / "benchmark_real" / "clean"
FONT_PATH = ROOT / "scripts" / "assets" / "DejaVuSans-Bold.ttf"

TEXT = "Doubao AI"
DARK = (26, 26, 26)


def _font(size: int):
    if not FONT_PATH.exists():
        raise SystemExit(f"缺随源码分发的字体：{FONT_PATH}")
    return ImageFont.truetype(str(FONT_PATH), size)


def _fit_font(bbox_wh: tuple[int, int]) -> ImageFont:
    """选一个能把文字塞进板内的字号（留 2×14px 横向 padding）。"""
    w, h = bbox_wh
    for size in range(48, 9, -1):
        f = _font(size)
        bb = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), TEXT, font=f)
        if (bb[2] - bb[0]) <= w - 24 and (bb[3] - bb[1]) <= h - 8:
            return f
    return _font(12)


def _bg(name: str, size: int = 1024) -> Image.Image:
    """拿项目自带的干净图当纹理底图（避免 AI 生成的图再要豆包重出）。"""
    src = CLEAN / name
    img = Image.open(src).convert("RGB")
    # 中心裁剪到正方形再缩放
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2)).resize((size, size), Image.LANCZOS)
    return img


def _rounded_board(size: tuple[int, int], radius: int, alpha: int) -> Image.Image:
    board = Image.new("RGBA", size, (255, 255, 255, alpha))
    ImageDraw.Draw(board).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                             radius=radius, fill=(255, 255, 255, alpha))
    return board


def build_b(name: str, bg_file: str, spec: dict) -> dict:
    img = _bg(bg_file)
    d = ImageDraw.Draw(img)
    margin = 20
    pos = spec["pos"]
    info = {"image": f"{name}.png", "group": "B", "spec": spec}

    if spec["board"]:
        bw, bh = spec["board"]
        x = margin if pos == "left" else img.width - bw - margin
        y = img.height - bh - margin
        board = _rounded_board((bw, bh), spec["radius"], spec["alpha"])
        img.paste(board, (x, y), board)
        f = _fit_font((bw, bh))
        bb = d.textbbox((0, 0), TEXT, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        d.text((x + (bw - tw) / 2 - bb[0], y + (bh - th) / 2 - bb[1]), TEXT, font=f, fill=DARK)
        info["bbox"] = [x, y, bw, bh]
    else:
        f = _font(28)
        bb = d.textbbox((0, 0), TEXT, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = img.width - tw - margin - bb[0]
        y = img.height - th - margin - bb[1]
        d.text((x, y), TEXT, font=f, fill=DARK)
        info["bbox"] = [x + bb[0], y + bb[1], tw, th]

    img.save(OUT / info["image"])
    return info


def main() -> None:
    manifest = []
    b_specs = [
        ("B1", "clean_01.jpg", {"pos": "left", "board": (174, 59), "radius": 14, "alpha": 255, "font": 40}),
        ("B2", "clean_02.jpg", {"pos": "right", "board": (358, 67), "radius": 16, "alpha": 255, "font": 48}),
        ("B3", "clean_03.jpg", {"pos": "right", "board": (174, 59), "radius": 14, "alpha": 128, "font": 40}),
        ("B4", "clean_04.jpg", {"pos": "right", "board": None, "radius": 0, "alpha": 255, "font": 40}),
    ]
    for name, bg, spec in b_specs:
        manifest.append(build_b(name, bg, spec))

    # A 组：4 张纯场景（无水印，"上传演练"）= 干净底图
    for i, bg in enumerate(["clean_05.jpg", "clean_06.jpg", "clean_07.jpg", "clean_08.jpg"], 1):
        if not (CLEAN / bg).exists():
            break
        img = _bg(bg)
        img.save(OUT / f"A{i}.png")
        manifest.append({"image": f"A{i}.png", "group": "A", "spec": {"watermark": False}})

    # C 组：4 张干净干扰图（右下角有易混元素）= 再取 4 张干净底图
    for i, bg in enumerate(["clean_09.jpg", "clean_10.jpg", "clean_11.jpg", "clean_12.jpg"], 1):
        if not (CLEAN / bg).exists():
            break
        img = _bg(bg)
        img.save(OUT / f"C{i}.png")
        manifest.append({"image": f"C{i}.png", "group": "C", "spec": {"watermark": False}})

    (OUT.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已生成:", sorted(p.name for p in OUT.glob("*.png")))
    print("清单:", OUT.parent / "manifest.json")


if __name__ == "__main__":
    main()