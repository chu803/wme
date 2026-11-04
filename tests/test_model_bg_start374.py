# -*- coding: utf-8 -*-
"""阶段 374 · 首屏不再等模型下载：启动策略 + 「下载中」防半写文件。

背景（真机实测）：模型 205 MB、默认从 GitHub 拉；网络不通时回退 ghproxy 只有 ~50 KB/s，
旧流程先等下载完再起界面 ⇒ 新用户可能盯着空终端几十分钟。现在界面先起（OpenCV 可用），
下载+预热丢后台；完成后再自动用上 LaMa。

本文件锁三层：
  ① `model_startup_action` 的两条分支（同步 / 后台）；
  ② 「下载中」标记生效：`is_model_available()` 必须返回 False（防半写文件被加载），
     且**无论成功失败**都要在 `download_model` 结束时清掉；
  ③ 后台准备线程**吞掉异常**（一个下载失败不能把界面拖崩）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import main as app  # noqa: E402
from core import model_manager as mm  # noqa: E402


def test_model_startup_action_branches():
    assert app.model_startup_action(True) == "sync"
    assert app.model_startup_action(False) == "background"


def test_model_unavailable_while_downloading(monkeypatch):
    """下载进行中 ⇒ 一律不可用（否则推理线程会把半写权重喂给 torch.jit.load）。"""
    monkeypatch.setattr(mm, "_DOWNLOAD_IN_PROGRESS", False)
    before = mm.is_model_available(None)
    monkeypatch.setattr(mm, "_DOWNLOAD_IN_PROGRESS", True)
    assert mm.is_downloading() is True
    assert mm.is_model_available(None) is False, "下载中必须判为不可用"
    # 恢复后回到原值（本机有 models/big-lama.pt ⇒ before 通常为 True，但不强制）
    monkeypatch.setattr(mm, "_DOWNLOAD_IN_PROGRESS", False)
    assert mm.is_model_available(None) == before


def test_download_model_clears_in_progress_flag_on_failure(tmp_path, monkeypatch):
    """下载失败（这里让它直接抛错）后，「下载中」标记必须被清掉。"""
    import download_model as dm

    slot = mm.register_model_slot(
        "__bg374__", display_name="后台测试槽", filename="__bg374__.pt",
        urls=("http://example.invalid/x.pt",), builtin=False)
    try:
        monkeypatch.setattr(dm, "download_to", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            mm.download_model("__bg374__")
        assert mm.is_downloading() is False, "失败路径上没有清掉「下载中」标记"
    finally:
        mm._SLOTS.pop("__bg374__", None)
        _ = slot


def test_background_setup_swallows_exceptions(monkeypatch):
    def _boom():
        raise RuntimeError("download failed")

    monkeypatch.setattr(app, "ensure_model_ready", _boom)
    app._background_model_setup()   # 不抛出即通过
