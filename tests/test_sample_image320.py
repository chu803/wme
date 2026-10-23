# -*- coding: utf-8 -*-
"""阶段 320 ·「试一张示例」的首屏体验守卫。

为什么要有它：
- 示例图是**随包发布的二进制资源**——最容易被后续「清理无用资产」误删；
- `load_sample_image()` 的返回值必须与 `sample_btn.click(outputs=[upload, editor, status_box])`
  **三值对齐**，错位在 Gradio 里不会报错、只会把图片塞进状态栏（静默错位，同族 LESSONS #297）；
- 资源缺失时必须**降级为提示**，不能让首屏崩掉。
"""
import importlib.util
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ui.tab_auto as tab  # noqa: E402
from utils import i18n  # noqa: E402


def test_sample_asset_exists_and_is_a_real_photo():
    p = tab.SAMPLE_IMAGE_PATH
    assert os.path.isfile(p), f"示例图缺失：{p}（首屏「试一张示例」会失效）"
    from PIL import Image
    with Image.open(p) as im:
        assert im.size == (640, 640), f"示例图尺寸变了：{im.size}"
        assert im.convert("RGB").size == (640, 640)


def test_load_sample_returns_aligned_triplet():
    up, ed, msg = tab.load_sample_image()
    assert isinstance(up, np.ndarray) and up.shape == (640, 640, 3)
    assert up.dtype == np.uint8
    # 真机实测（320）：ImageEditor **不接受**「background + 空 layers」的预填（浏览器回传后当没图），
    # 故示例图只填上传框，编辑框交给「② 自动检测水印」去填（那是已验证可用的形态）。
    assert _is_update(ed), f"编辑框应保持不动（gr.update），实得 {type(ed)}"
    assert isinstance(msg, str) and msg and msg != "auto.sample_loaded"


def _is_update(v) -> bool:
    # Gradio 6 的 gr.update() 返回普通 dict：{'__type__': 'update'}
    return isinstance(v, dict) and v.get("__type__") == "update"


def test_missing_asset_degrades_to_message(monkeypatch):
    monkeypatch.setattr(tab, "SAMPLE_IMAGE_PATH", os.path.join(_ROOT, "docs", "assets", "__nope__.png"))
    up, ed, msg = tab.load_sample_image()
    assert _is_update(up) and _is_update(ed), (
        "资源缺失时应返回 gr.update() 占位（不动现有画布），而不是把 None 直接塞进组件")
    assert "失败" in msg or "fail" in msg.lower()


@pytest.mark.parametrize("key", ["auto.sample_btn", "auto.sample_loaded", "auto.sample_fail"])
def test_sample_i18n_keys_both_langs(key):
    for code in i18n.TRANSLATIONS:
        i18n.set_language(code)
        val = i18n.t(key)
        assert val and val != key, f"{code} 缺键 {key}"
