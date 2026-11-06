# -*- coding: utf-8 -*-
"""代理相关环境变量的清洗（阶段 386）。

问题：某些环境（含本机的自动化运行器）会注入

    NO_PROXY=localhost,.localhost,127.0.0.1,127.0.0.0/8,[::1],::1

httpx 在**导入时**解析这个变量，方括号写法 `[::1]` 被当成 "host:port"，
于是抛 `InvalidURL: Invalid port: ':1]'` —— 表现为大批用例在**收集阶段**报错，
或者（更糟）Gradio 在启动前就炸掉、界面根本起不来。

处理：把方括号形式去掉（httpx 认 `::1`），其余内容原样保留；不可解析的条目丢弃。
本模块在导入时**自动执行一次**，因此只要在任何 `import gradio` 之前导入它即可。
"""

from __future__ import annotations

import os


def sanitize_proxy_env() -> bool:
    """清洗 NO_PROXY / no_proxy；返回是否发生了修改。

    ⚠ Windows 上 `os.environ` **键名大小写不敏感**（`no_proxy` 与 `NO_PROXY` 是同一个键），
    所以这里按"实际存在的那个键"处理，不能对两个拼写各操作一次（否则第二个会把第一个的结果覆盖掉）。
    """
    changed = False
    handled: set[str] = set()
    for key in ("NO_PROXY", "no_proxy"):
        actual = None
        for existing in os.environ:
            if existing.lower() == key.lower() and existing not in handled:
                actual = existing
                break
        if actual is None:
            continue
        handled.add(actual)
        raw = os.environ[actual]
        parts: list[str] = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if item.startswith("[") and item.endswith("]"):
                item = item[1:-1]
                changed = True
            parts.append(item)
        fixed = ",".join(parts)
        if fixed != raw:
            os.environ[actual] = fixed
            changed = True
    return changed


sanitize_proxy_env()
