# -*- coding: utf-8 -*-
"""2×5 网格：每张图叠加其自测 bbox，供人眼批量复核贴合度。"""
import os

import cv2  # type: ignore
import numpy as np
from PIL import Image

SRC = r"D:\Desktop\照片"
BOX = {
    "ai_logo_02_snow_mountain_lake.jpg": (1676, 1888, 1968, 1962),
    "ai_logo_03_white_coffee_cup.jpg": (1657, 1880, 1949, 1954),
    "ai_logo_04_neon_street_night.jpg": (1701, 1919, 1993, 1993),
    "ai_logo_05_fruit_platter.jpg": (1682, 1892, 1974, 1966),
    "ai_logo_06_flower_field_portrait.jpg": (1664, 1881, 1956, 1955),
    "ai_logo_07_glass_curtain_building.jpg": (1667, 1885, 1959, 1959),
    "ai_logo_08_abstract_fluid.jpg": (1666, 1884, 1958, 1958),
    "ai_logo_09_vintage_typewriter.jpg": (1638, 1866, 1930, 1940),
    "ai_logo_10_lavender_hot_air_balloon.jpg": (1644, 1886, 1936, 1960),
    "ai_logo_test_01_orange_cat.jpg": (1680, 1889, 1972, 1963),
}
files = [f for f in sorted(os.listdir(SRC)) if f.lower().startswith("ai_logo")]
CX0, CY0, CX1, CY1 = 1560, 1820, 2048, 2040
tiles = []
for f in files:
    a = np.asarray(Image.open(os.path.join(SRC, f)).convert("RGB")).copy()
    x0, y0, x1, y1 = BOX[f]
    cv2.rectangle(a, (x0, y0), (x1, y1), (255, 0, 0), 3)
    tiles.append(a[CY0:CY1, CX0:CX1])

th, tw = tiles[0].shape[:2]
grid = np.full((th * 5 + 40, tw * 2 + 30, 3), 255, np.uint8)
for i, t in enumerate(tiles):
    r, c = divmod(i, 2)
    grid[r * (th + 8):r * (th + 8) + th, c * (tw + 10):c * (tw + 10) + tw] = t
Image.fromarray(grid).save(".verify172_view.png")
print("saved .verify172_view.png", grid.shape)
