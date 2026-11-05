# -*- coding: utf-8 -*-
"""Gradio ImageEditor 预处理的重试兼容层。

背景（阶段 380）：
  Gradio 6.31 的前端在点击事件里会把画布背景/图层重新上传，随后立即
  queue/join；后端 preprocess 有时在文件尚未写完时 PIL.Image.open，
  报 UnidentifiedImageError，事件直接失败。实测文件在毫秒级之后即可读，
  属于上传与读取的竞态，不是坏图。

做法：只包装 ImageEditor.preprocess，遇到异常按短间隔重试几次；
  非该组件的调用不受影响，重试耗尽仍按原异常抛出（不吞错）。
"""

from __future__ import annotations

import time

import gradio as gr

_RETRIES = 6
_DELAY_SEC = 0.15
_INSTALLED = False


def preprocess_with_retry(original, component, payload, sleep=time.sleep):
    """调用 original 并在异常时重试；最后一次仍失败则抛出原异常。"""
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            return original(component, payload)
        except Exception as exc:  # noqa: BLE001 —— 读文件竞态与坏图都走重试
            last_exc = exc
            if attempt == _RETRIES - 1:
                raise
            sleep(_DELAY_SEC)
    raise last_exc  # pragma: no cover —— 循环内已 raise


def install_image_editor_retry() -> bool:
    """安装一次性的 preprocess 重试包装；返回本次是否真的安装了。"""
    global _INSTALLED
    if _INSTALLED:
        return False
    original = gr.ImageEditor.preprocess

    def _preprocess_with_retry(self, payload):
        return preprocess_with_retry(original, self, payload)

    gr.ImageEditor.preprocess = _preprocess_with_retry
    _INSTALLED = True
    return True
