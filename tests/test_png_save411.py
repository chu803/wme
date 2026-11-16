# -*- coding: utf-8 -*-
"""阶段 411：PNG 保存提速的无损回归守卫。

PNG 无损 ⇒ 压缩级别只权衡速度/体积，解码像素必须逐位一致。
锁定默认压缩级=1（提速标定），并验证 lvl1 与 lvl6 输出像素相同。
ci311venv 可跑（cv2/numpy，无 torch）。
"""
from __future__ import annotations

import numpy as np
import pytest

import config
from core import image_utils as IU

cv2 = pytest.importorskip("cv2")


def _img():
    rng = np.random.default_rng(3)
    return (rng.random((256, 256, 3)) * 255).astype(np.uint8)


def test_default_png_compress_level_is_one():
    assert config.PNG_COMPRESS_LEVEL == 1


def test_png_lossless_across_levels(tmp_path, monkeypatch):
    img = _img()
    outs = []
    for lvl in (1, 6):
        monkeypatch.setattr(config, "PNG_COMPRESS_LEVEL", lvl)
        p = str(tmp_path / f"lvl{lvl}.png")
        IU.save_image(p, img, fmt="PNG")
        outs.append(cv2.imread(p))
    assert np.array_equal(outs[0], outs[1]), "PNG 压缩级别不得改变解码像素（无损）"
    assert np.array_equal(outs[0], cv2.cvtColor(img, cv2.COLOR_RGB2BGR))