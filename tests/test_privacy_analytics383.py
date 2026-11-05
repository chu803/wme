# -*- coding: utf-8 -*-
"""阶段 383：隐私承诺与实际联网行为必须一致——默认关掉 Gradio 匿名统计。

背景：首屏写「本地处理、图片不上传，唯一联网是首次使用时的模型权重下载」，
但 Gradio 6.x 的匿名统计**默认开启**：启动时会向 `api.gradio.app` 发一次
"用了哪些组件类型"的统计（不含图片内容）。⇒ 那句"唯一联网"在默认配置下并不成立。

本守卫钉住：默认关闭、可显式打开、launch 真的把开关传下去、首屏文案说清状态。
"""

from __future__ import annotations

import os

import gradio as gr
import pytest

import config
import ui.app as app_mod
from utils.i18n import t_in


class _FakeApp:
    """鸭子类型替身：只记录 launch() 收到的实参。"""

    def __init__(self) -> None:
        self.kwargs = None

    def launch(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def launch_env(monkeypatch):
    monkeypatch.setattr(config, "SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "SERVER_PORT", 7860)
    monkeypatch.setattr(config, "SERVER_PORT_MAX_TRY", 5)
    monkeypatch.setattr(config, "SERVER_AUTO_PORT", False)
    monkeypatch.setattr(config, "SERVER_SHARE", False)
    monkeypatch.setattr(config, "SERVER_OPEN_BROWSER", False)
    monkeypatch.setattr(config, "SERVER_MAX_THREADS", 4)
    monkeypatch.setattr(config, "SERVER_SHOW_API", False)
    monkeypatch.setattr(config, "SERVER_THEME", "brand")
    return config


def test_analytics_is_off_by_default():
    assert config.SERVER_ANALYTICS is False, "默认必须关闭 Gradio 匿名统计"
    assert os.environ.get("GRADIO_ANALYTICS_ENABLED") == "False"
    assert gr.analytics.analytics_enabled() is False


def test_env_can_opt_in(monkeypatch):
    monkeypatch.setenv("WME_SERVER_ANALYTICS", "1")
    assert config._env_bool("SERVER_ANALYTICS", False) is True
    monkeypatch.setenv("WME_SERVER_ANALYTICS", "0")
    assert config._env_bool("SERVER_ANALYTICS", False) is False


def test_launch_passes_the_flag(launch_env, monkeypatch):
    """开关要传给 **Blocks 构造器**（Gradio 的 launch() 不接受该参数）。

    1.0.8 就是把 `analytics_enabled` 传给了 launch() ⇒ 发布物启动即抛 TypeError。
    """
    import inspect

    assert "analytics_enabled" in inspect.signature(gr.Blocks.__init__).parameters
    assert "analytics_enabled" not in inspect.signature(gr.Blocks.launch).parameters, (
        "Gradio 的 launch() 不接受 analytics_enabled；1.0.8 因此起不来"
    )

    demo = app_mod.build_app()
    assert demo.analytics_enabled is False

    monkeypatch.setattr(config, "SERVER_ANALYTICS", True)
    demo = app_mod.build_app()
    assert demo.analytics_enabled is True


def test_compliance_copy_states_statistics_are_off():
    zh = t_in("zh-CN", "app.compliance")
    en = t_in("en-US", "app.compliance")
    assert "匿名使用统计默认关闭" in zh
    assert "anonymous usage statistics are off by default" in en
    assert "本地处理" in zh and "never uploaded" in en
