# -*- coding: utf-8 -*-
"""阶段 380：Gradio ImageEditor 预处理重试层的判定与接线守卫。

背景：Gradio 6.31 前端在点击事件里重新上传画布文件后立即发 queue/join，
后端偶尔在文件写完前 PIL.Image.open，报 UnidentifiedImageError，修复按钮直接失败。
兼容层只对 ImageEditor.preprocess 做有限重试，耗尽后原样抛出。
"""

from __future__ import annotations

from pathlib import Path

import gradio as gr
import pytest

import ui.gradio_compat as gc

ROOT = Path(__file__).resolve().parent.parent


def test_retry_succeeds_after_transient_failures():
    calls: list[str] = []

    def flaky(component, payload):
        calls.append(payload)
        if len(calls) < 3:
            raise RuntimeError("partial file")
        return "ok"

    sleeps: list[float] = []
    result = gc.preprocess_with_retry(flaky, None, "payload", sleep=sleeps.append)

    assert result == "ok"
    assert len(calls) == 3
    assert sleeps == [gc._DELAY_SEC, gc._DELAY_SEC]


def test_retry_reraises_after_exhaustion():
    calls: list[int] = []

    def always_broken(component, payload):
        calls.append(1)
        raise ValueError("bad image")

    with pytest.raises(ValueError, match="bad image"):
        gc.preprocess_with_retry(always_broken, None, "payload", sleep=lambda _s: None)
    assert len(calls) == gc._RETRIES


def test_install_is_idempotent_and_preserves_payload():
    original = gr.ImageEditor.preprocess
    old_flag = gc._INSTALLED
    seen: list[str] = []

    def fake(self, payload):
        seen.append(payload)
        return "wrapped-ok"

    try:
        gr.ImageEditor.preprocess = fake
        gc._INSTALLED = False

        assert gc.install_image_editor_retry() is True
        wrapped = gr.ImageEditor.preprocess
        assert wrapped is not fake

        assert gc.install_image_editor_retry() is False, "重复装载不应再包一层"
        assert gr.ImageEditor.preprocess is wrapped
        assert wrapped(object(), "the-payload") == "wrapped-ok"
        assert seen == ["the-payload"]
    finally:
        gr.ImageEditor.preprocess = original
        gc._INSTALLED = old_flag


def test_build_app_installs_retry_layer():
    src = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
    assert "install_image_editor_retry()" in src, (
        "build_app 没有装载 ImageEditor 重试层，上传竞态会再次让修复按钮失败"
    )
