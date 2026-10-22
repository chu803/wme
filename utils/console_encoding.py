# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/console_encoding.py
 文件说明 : 控制台输出编码加固（唯一实现；阶段 314 由 scripts/ 迁入 utils/）。

 为什么必须存在（阶段 146 / 170 / 314 三次踩同一坑）
 --------------------------------------------------
 Windows 上 stdout 被重定向到管道/文件时，Python 按 **ANSI 代码页**
 （简体中文 = GBK）编码输出；而本项目会 `print` `✅/❌/⚠/⇒/📄/🎬` 等
 **GBK 不可编码**字符 ⇒ 抛 `UnicodeEncodeError`，**进程退出码 1**。

 后果：**「输出内容」决定了「退出码」**——一个已经成功完成的任务，会因为最后一行
 汇总带了 `✅` 而被读成失败；`python cli.py ... > 日志.txt` 这种最常见的用法直接崩。

 阶段 146 修了 3 个门禁脚本，阶段 170 抽成共用件并机械化覆盖 scripts/，
 但**产品自己的三个入口**（main.py / cli.py / server.py）一直没加固 ——
 阶段 314 补上，并把实现迁到 `utils/`：分发包（pyproject 的 packages.find）
 刻意不含 `scripts/`，入口依赖 scripts 会在 `pip install` 后 ImportError。

 用法
 ----
     from utils.console_encoding import force_utf8_stdio

     def main() -> int:
         force_utf8_stdio()          # ← main() 的**第一条语句**
         ...

 设计约束：只标准库；`errors="replace"` 兜底，**绝不因编码中断判定**。
==============================================================================
"""
from __future__ import annotations

__all__ = ["force_utf8_stdio"]


def force_utf8_stdio() -> None:
    """把 stdout / stderr 切到 UTF-8（`errors="replace"`），使输出编码不影响退出码。

    幂等、无副作用（除切换流编码）；对不支持 `reconfigure` 的非常规流
    （测试中可能被替换）静默跳过，保证在任意环境下都「不会因此崩溃」。
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 非常规流（已被替换为不支持 reconfigure 的对象）——跳过即可。
            pass
