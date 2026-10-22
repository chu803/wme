# -*- coding: utf-8 -*-
"""控制台编码加固（兼容导入路径）。

实现已于阶段 314 迁至 `utils/console_encoding.py`——分发包（pyproject 的
`packages.find`）刻意不含 `scripts/`，所以**产品入口必须从 utils 导入**；
本文件仅为 scripts/ 下 20+ 个门禁脚本保留原有导入路径，避免无意义的批量改名。
"""
from __future__ import annotations

from utils.console_encoding import force_utf8_stdio

__all__ = ["force_utf8_stdio"]
