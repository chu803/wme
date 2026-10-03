# -*- coding: utf-8 -*-
"""main.py 的 REST 服务启动入口契约测试（D1 产品化 · API 接入）。

确保 `python main.py --api [--api-host H --api-port P]` 的命令行契约稳定：
- 默认 --api 关闭（保持原有 Gradio 启动行为不变）；
- 传入 --api / --api-host / --api-port 后能正确解析。

不实际启动服务（start_server 会阻塞），只验证参数解析与模块可导入
（导入 main 不应因 server 模块缺失而失败，server 在 --api 分支内延迟导入）。
"""

import sys

import main


def _parse(argv):
    old = sys.argv
    sys.argv = argv
    try:
        return main.parse_args()
    finally:
        sys.argv = old


def test_parse_args_api_defaults_off():
    """默认不开启 --api，host/port 为 None（交给 config 兜底）。"""
    ns = _parse(["main.py"])
    assert ns.api is False
    assert ns.api_host is None
    assert ns.api_port is None


def test_parse_args_api_flags_parsed():
    """--api 及其 host/port 能被正确解析。"""
    ns = _parse(["main.py", "--api", "--api-host", "0.0.0.0", "--api-port", "9000"])
    assert ns.api is True
    assert ns.api_host == "0.0.0.0"
    assert ns.api_port == 9000


def test_parse_args_api_without_host_port():
    """只给 --api 时 host/port 仍为空（运行时由 config.API_HOST/PORT 兜底）。"""
    ns = _parse(["main.py", "--api"])
    assert ns.api is True
    assert ns.api_host is None
    assert ns.api_port is None
