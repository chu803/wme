"""Stage 125 回归守卫：api.py 三处真缺陷（A 的形状解耦由 test_legacy_selfcheck 子进程守卫覆盖）。

覆盖：
  - 缺陷 B：remove_watermark(poisson_blend=True / texture_transfer=True) 在部署闸
    POSTPROCESS_*_ALLOW=False（默认）时必须给出可观测信号（RemoveResult.warnings 非空），
    不能静默吞掉（见 Stage 122 未修登记）。
  - 缺陷 C：remove_watermark_pdf 必须能正常接收 preset / auto_preset 形参（修复前引用未定义
    变量导致 NameError）。
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

import api


def _make_watermark_png(path: str) -> None:
    img = Image.new("RGB", (320, 200), (240, 240, 240))
    d = ImageDraw.Draw(img, "RGBA")
    for j in range(4):
        d.text((20 + j * 45, 80 + (j % 2) * 30), "WATERMARK",
               fill=(170, 170, 170, 140))
    img.save(path)


def test_remove_watermark_poisson_blend_gate_warns(tmp_path):
    """缺陷 B：显式请求 poisson_blend 但部署闸关闭，必须写入 warnings。"""
    inp = tmp_path / "in.png"
    outp = tmp_path / "out.png"
    _make_watermark_png(str(inp))
    res = api.remove_watermark(
        str(inp), dst=str(outp), engine="opencv",
        poisson_blend=True, real_superres=False, superres=False,
    )
    assert res.status == "success"
    assert isinstance(res.warnings, list)
    assert any("poisson" in w.lower() for w in res.warnings), res.warnings


def test_remove_watermark_texture_transfer_gate_warns(tmp_path):
    """缺陷 B：显式请求 texture_transfer 但部署闸关闭，必须写入 warnings。"""
    inp = tmp_path / "in.png"
    outp = tmp_path / "out.png"
    _make_watermark_png(str(inp))
    res = api.remove_watermark(
        str(inp), dst=str(outp), engine="opencv",
        texture_transfer=True, real_superres=False, superres=False,
    )
    assert res.status == "success"
    assert any("texture" in w.lower() for w in res.warnings), res.warnings


def test_remove_watermark_no_gate_warning_when_not_requested(tmp_path):
    """未显式请求时不应产生 gated 警告（避免误报）。"""
    inp = tmp_path / "in.png"
    outp = tmp_path / "out.png"
    _make_watermark_png(str(inp))
    res = api.remove_watermark(
        str(inp), dst=str(outp), engine="opencv",
        real_superres=False, superres=False,
    )
    assert not any(
        ("poisson" in w.lower() or "texture" in w.lower()) for w in res.warnings
    )


def test_remove_watermark_pdf_accepts_preset_and_autopreset(tmp_path):
    """缺陷 C：remove_watermark_pdf 必须能正常透传 preset / auto_preset（修复前 NameError）。"""
    fitz = pytest.importorskip("fitz")
    pdf_in = tmp_path / "api_test.pdf"
    d = fitz.open()
    pg = d.new_page(width=420, height=595)
    pg.insert_text((50, 90), "content", fontsize=16)
    pg.draw_rect(fitz.Rect(260, 500, 400, 545), color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
    pg.insert_text((272, 528), "WATERMARK", fontsize=14, color=(1, 1, 1))
    d.save(str(pdf_in))
    d.close()

    out = tmp_path / "out.pdf"
    res = api.remove_watermark_pdf(
        str(pdf_in), dst=str(out), dpi=100, preset="general", auto_preset=True
    )
    assert res.status == "success"
    assert out.is_file()
