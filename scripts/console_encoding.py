# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/console_encoding.py
 文件说明 : 控制台输出编码加固（阶段 170 抽为共用件）。

 为什么必须共用（阶段 170 门禁盲区审计的发现的元缺陷）
 ----------------------------------------------------
 阶段 146 已在 `real_gate.py` 里**详细记录并修复**过一个失效模式：

   Windows 上 stdout 被重定向到管道 / 文件时，Python 按 **ANSI 代码页**
   （简体中文 = GBK）编码输出；而本项目脚本会 `print` `✅/❌/⚠/⇒/✓/✗/−/²`
   等 **GBK 不可编码**字符 ⇒ 抛 `UnicodeEncodeError`，**进程退出码 1**。

   后果：**「输出内容」决定了「门禁退出码」**——一个**通过**的门禁会被 CI
   读成「未通过」；且 `--update` 之后的收尾 `print` 失败也会把**成功的动作**
   报成失败。这属于「假红」，比假绿更贵：会诱使你去改一个本来没坏的模块。

 但那次修复**只落在 3 个文件**（real_gate / prompted_area_gate /
 real_asset_preflight），另外 10 个同样 `print` 非 GBK 字符的脚本没同步
 ——**同一判据没有机械化地覆盖整族**。阶段 170 把它抽成唯一的共用实现，
 并新增守卫测试（`tests/test_scripts_console_encoding.py`）机器化地保证
 「凡 print 非 GBK 字符的脚本都必须调用本函数」，新脚本自动纳入。

 用法
 ----
     from scripts.console_encoding import force_utf8_stdio

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
