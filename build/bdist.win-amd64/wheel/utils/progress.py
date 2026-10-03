# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/progress.py
 文件说明 : 进度回调节流工具（阶段 14 新增）。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【为什么需要它】
   批量 / 视频 / 分块推理会高频调用 progress(p, msg)：
     · 分块推理：每块都上报一次，几百块就有几百次回调；
     · 视频：每帧一次，1 分钟视频就是上千次回调；
     · Gradio 每次回调都会触发一次前端重绘，过密会卡住交互。
   本模块提供 ThrottledProgress 包装器，把"高频回调"合并成"低频真实上报"，
   只有「时间间隔到了」或「进度跃变超过阈值」或「最终完成」时才真正通知上层。

 【约束】
   本模块属于 utils 工具层，严禁 import gradio / 任何界面组件，零第三方依赖。
==============================================================================
"""

from __future__ import annotations

import time
from typing import Callable, Optional

# 进度回调类型：callable(progress: 0~1, message: str) -> None
ProgressCallback = Callable[[float, str], None]


def _noop_progress(p: float, msg: str) -> None:
    """默认的空进度回调，什么都不做。"""
    return None


class ThrottledProgress:
    """
    进度回调节流包装器。

    用法：
        raw = lambda p, m: print(f"{p:.0%} {m}")
        tp = ThrottledProgress(raw, min_interval=0.08)
        for i in range(1000):
            tp(i / 1000, "处理中")      # 绝大多数被合并丢弃，只按节奏真正打印

    节流策略（三者满足其一才真正上报）：
       1. force=True（例如 p>=1.0 的最终完成信号）；
       2. 距上次上报已超过 min_interval 秒；
       3. 进度相对上次上报的跃变 >= min_step。
    这样既能避免高频抖动，又保证「关键进度点」和「最终完成」一定不丢。
    """

    def __init__(
        self,
        progress: Optional[ProgressCallback],
        min_interval: float = 0.08,
        min_step: float = 0.02,
    ) -> None:
        self._progress = progress or _noop_progress
        # min_interval<=0 表示关闭节流（任何回调都立即上报）。
        self._min_interval = max(0.0, float(min_interval))
        self._min_step = max(0.0, float(min_step))
        self._last_time = 0.0
        self._last_p = -1.0

    def __call__(self, p: float, msg: str = "", *, force: bool = False) -> float:
        p = max(0.0, min(1.0, float(p)))
        now = time.monotonic()
        # 强制 / 首次 / 最终完成：永远放行，确保 100% 一定被上层收到。
        if force or p >= 1.0 or self._last_p < 0:
            self._last_time = now
            self._last_p = p
            self._progress(p, msg)
            return p
        # 时间条件：min_interval>0 且间隔未到时为 False；min_interval<=0 表示不按时间节流。
        time_ok = self._min_interval <= 0 or (now - self._last_time) >= self._min_interval
        step_ok = abs(p - self._last_p) >= self._min_step
        # 上报判定：
        #   · 开启时间节流（min_interval>0）：时间到了「或」步进达标，二者其一即上报；
        #   · 关闭时间节流（min_interval<=0）：仅看步进，避免无时间维度时每帧都上报。
        emit = step_ok if self._min_interval <= 0 else (time_ok or step_ok)
        if emit:
            self._last_time = now
            self._last_p = p
            self._progress(p, msg)
        return p

    def flush(self, msg: str = "") -> None:
        """强制把最终进度（1.0）推送给上层。常用于循环结束收尾。"""
        self(1.0, msg, force=True)


def make_scoped(progress: Optional[ProgressCallback], a: float, b: float) -> ProgressCallback:
    """
    把子任务的 0~1 进度，线性映射到全局 [a, b] 区间。

    参数说明：
        progress : 全局进度回调。
        a, b     : 全局区间起止（如把"分块修复"映射进 [0.2, 0.8]）。

    返回值：
        一个可当作 progress 传给子任务的回调，内部自动映射到全局区间。

    用法：
        sub = make_scoped(global_progress, 0.2, 0.8)
        for i, t in enumerate(tiles):
            sub(i / len(tiles), "修复分块")   # 实际会上报 0.2 + 0.6*i/N
    """
    base = float(a)
    span = float(b) - base

    def _scoped(p: float, msg: str) -> None:
        p = max(0.0, min(1.0, float(p)))
        if progress is not None:
            progress(base + span * p, msg)

    return _scoped


# 自检：高频回调应被大幅节流，但首帧与末帧必须保留。
if __name__ == "__main__":
    calls: list[float] = []

    def _collect(p: float, msg: str) -> None:
        calls.append(round(p, 3))

    # 用例：禁用时间节流、仅按 min_step 节流（确定性，不依赖真实耗时）。
    # 101 次回调（每次 +0.01），min_step=0.05 → 只在跃变 >=0.05 时上报，
    # 预期约 21 次，且必含首帧 0.0 与末帧 1.0。
    tp = ThrottledProgress(_collect, min_interval=0.0, min_step=0.05)
    for i in range(101):
        tp(i / 100.0, "x")
    # 末帧强制完成。
    tp(1.0, "done", force=True)

    # 节流后真实上报次数应远小于 101，且必须含首帧 0.0 与末帧 1.0。
    assert len(calls) < 60, f"节流未生效，真实上报 {len(calls)} 次"
    assert calls[0] == 0.0, "首帧丢失"
    assert calls[-1] == 1.0, "末帧丢失"
    # 中间关键进度点必须被保留（浮点边界不精确，故只校验「存在 (0.1,0.9) 区间内的上报」）。
    assert any(0.1 < c < 0.9 for c in calls), "中间关键进度点丢失"
    print(f"utils/progress.py 自检通过：101 次回调被节流为 {len(calls)} 次，含首/末帧。")
