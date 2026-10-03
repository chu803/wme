# -*- coding: utf-8 -*-
"""
阶段 97 审计注入式反例（ui/tab_batch + ui/compare_view 根因修复）

两条真实缺陷的回归守卫：
  · I1 (P1)：手动掩码模式但未选「掩码文件夹」时，run_batch 的早退 yield
            必须产出 5 个值（与 click 绑定的 5 个 outputs 对齐）；
            历史上只 yield 了 4 个值，会在该路径触发 Gradio 元组长度不匹配崩溃。
  · I2 (P2)：render_pair 的文档承诺「各类异常都返回可读提示而非抛错」，
            但渲染引擎调用原先未加保护——一旦 render_pair_html 抛错，
            异常会穿透到画廊 .select 回调泄露 traceback。修复后必须兜底返回提示。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

import gradio as gr
import pytest

import ui.tab_batch as tb
import ui.compare_view as cv
from utils.i18n import t


def _call_run_batch(**overrides):
    """以最小可抵达早起退路径的参数集调用 run_batch（生成器）。"""
    base = dict(
        folder_value="/fake/image.png",  # 结尾为图片扩展名，才能进入「有文件但手动无掩码」路径
        files_value=None,
        mask_folder_value=None,
        mode="manual",
        engine="opencv",
        strength=1.0,
        dilate=0,
        feather=0,
        threshold=128,
        denoise=0,
        sensitivity=0.5,
        poisson_blend=False,
        poisson_strength=0.0,
        texture_transfer=False,
        texture_amount=0.0,
        artifact_reduce=False,
        artifact_amount=0.0,
        superres=False,
        superres_amount=0.0,
        real_superres=False,
        real_scale=1,
        real_backend="auto",
        evaluate=False,
        compare=False,
        preset="general",
        auto_preset=False,
        dual_run=None,
        progress=MagicMock(),
    )
    base.update(overrides)
    return tb.run_batch(**base)


def test_i1_manual_no_mask_yields_five_values():
    """I1：手动模式 + 无掩码文件夹 → 早退 yield 必须为 5 个值（与 outputs 对齐）。"""
    gen = _call_run_batch(mode="manual", mask_folder_value=None)
    yielded = next(gen)
    assert len(yielded) == 5, f"手动无掩码路径应 yield 5 个值，实际 {len(yielded)}"
    # 清理生成器，避免 ResourceWarning。
    gen.close()


def test_i1_no_files_yields_five_values():
    """I1 同型守卫：无任何文件 → 早退 yield 也必须是 5 个值（兄弟路径，防回归）。"""
    gen = _call_run_batch(folder_value=None, files_value=None, mode="auto")
    yielded = next(gen)
    assert len(yielded) == 5, f"无文件路径应 yield 5 个值，实际 {len(yielded)}"
    gen.close()


def _make_png(path):
    """生成一张最小合法 PNG，便于 render_pair 通过 isfile 校验抵达渲染调用。"""
    from PIL import Image

    Image.new("RGB", (4, 4), (255, 0, 0)).save(path)


def test_i2_render_pair_render_error_is_guarded(tmp_path):
    """I2：render_pair_html 抛错时，render_pair 必须兜底返回可读提示而非泄露异常。"""
    orig = tmp_path / "orig.png"
    res = tmp_path / "res.png"
    _make_png(orig)
    _make_png(res)
    with patch.object(cv._cs, "render_pair_html", side_effect=RuntimeError("boom")):
        html, summary = cv.render_pair((str(orig), str(res), "x"), "concat")
    assert html == "", "渲染失败时 html 应为空字符串"
    assert summary == t("compare.viewer.render_fail"), \
        "渲染失败应返回 compare.viewer.render_fail 提示，而非上抛异常"
    # 关键：此处不得抛出 RuntimeError。


def test_i2_on_gallery_select_with_mode_does_not_leak(tmp_path):
    """I2 端到端：画廊点选回调在渲染引擎异常时返回三元组而非泄露 traceback。"""
    orig = tmp_path / "orig.png"
    res = tmp_path / "res.png"
    _make_png(orig)
    _make_png(res)
    saved = tb._batch_pairs
    tb._batch_pairs = [(str(orig), str(res), "测试图.png")]
    try:
        with patch.object(cv._cs, "render_pair_html", side_effect=RuntimeError("boom")):
            out_pair, html, summary = tb.on_gallery_select_with_mode(
                _FakeEvt(0), "concat"
            )
    finally:
        tb._batch_pairs = saved
    assert out_pair is not None
    assert html == ""
    assert summary == t("compare.viewer.render_fail")


class _FakeEvt:
    """最小 SelectData 替身：仅暴露 index。"""

    def __init__(self, index):
        self.index = index


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
