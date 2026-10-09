#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CPU 负载探针（Windows，零依赖）——**判据正确**的机器空闲度检测。

★ 为什么要有这个文件（阶段 224 踩过的坑）
-------------------------------------------
我先前用 `busy = kernel + user` 判读 `GetSystemTimes`，得出「CPU 占用 51.8%」，
据此差点判定「机器有外部负载、基线不可信」。**这是错的**。

**Windows `GetSystemTimes` 的 kernel 时间包含 idle**（微软文档明确）。因此：

    idle + kernel + user    ← 三者**重复计数**
    实测：131.47 核·满载秒（而物理上限只有 24 核）⇒ 重复计数坐实

正确公式（**已实测验证**）：

    非idle 占用 = (kernel - idle) + user
    总量        = kernel + user        ← kernel 已含 idle，无须再加 idle
    占用率      = (kernel - idle + user) / (kernel + user)

    实测：占用率 16.97%、空闲率 83.03% ⇒ **机器空闲**。

⚠ **我最初的错误**（阶段 224 踩坑，值得留档）
--------------------------------------
1. **公式错**：用 `busy = kernel + user` 得「占用 51.8%」⇒ 差点判定「有外部负载、
   基线不可信」，**凭空制造一个不存在的干扰源**（会据此去优化不存在的瓶颈，LESSONS 233）。
2. **自检阈值也错**：拿「三者和 vs 1×物理核数」当重复计数判据，而正确上界是 **2×N**
   （kernel 含 idle ⇒ 最多重复一次）。结果**任何正常采样都被判成「计时域异常」**
   （实测 131.47 vs 阈值 24）。现已放宽到 2.2×N。
3. **两个来源必须交叉验证**：系统级公式 16.97% vs 逐进程枚举 1.6% —— 同量级才可信。
   逐进程求和**会低估**（权限限制 + 不含驱动/中断/DPC），故不能单独作「机器空闲」的证据。

用法
----
    from scripts.cpu_load import CpuLoadProbe
    p = CpuLoadProbe()
    s = p.sample(3.0)
    print(s.busy_pct, s.verdict())
"""
from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass

__all__ = ["CpuSample", "CpuLoadProbe", "system_busy_pct", "process_busy_pct"]


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong),
                ("dwHighDateTime", ctypes.c_ulong)]

    @property
    def ticks(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


#: 100ns 时间单位：1 秒 = 1e7 ticks
_TICKS_PER_SEC = 1e7


def _get_system_times() -> tuple[int, int, int]:
    """返回 (idle, kernel, user) 的 ticks 增量基准值。"""
    idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
    ok = ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    if not ok:  # pragma: no cover - 极少见
        raise OSError("GetSystemTimes 调用失败")
    return idle.ticks, kernel.ticks, user.ticks


@dataclass(frozen=True)
class CpuSample:
    """一次 CPU 负载采样结果。"""

    busy_pct: float
    idle_pct: float
    busy_cores: float
    idle_cores: float
    seconds: float
    reliable: bool
    note: str = ""

    def verdict(self) -> str:
        """按「是否可作性能基线标定环境」给结论。"""
        if not self.reliable:
            return f"⚠ 数据不可靠：{self.note}"
        if self.busy_pct < 10.0:
            return "✅ 机器空闲，可作基线标定"
        if self.busy_pct < 25.0:
            return f"⚠ 轻度占用（{self.busy_pct:.1f}%），标定基线须谨慎"
        return f"❌ 明显外部负载（{self.busy_pct:.1f}%），**不可**用于基线标定"


class CpuLoadProbe:
    """系统级 CPU 负载探针（**判据已按Windows 语义订正**）。"""

    def __init__(self, logical_cores: int | None = None):
        self.n = int(logical_cores or os.cpu_count() or 1)

    def sample(self, seconds: float = 3.0) -> CpuSample:
        """采样 `seconds` 秒，返回负载。

        `reliable=False` 的情形（会明确告知原因，而不是给一个假数字）：
          · 三者和**超过**物理上限 ⇒ 计时域异常；
          · 三者和**远低于**下限（低于 1% 单核当量）⇒ 采样窗口过短/时钟异常。
        """
        if seconds <= 0:
            raise ValueError("seconds 必须 > 0")
        i0, k0, u0 = _get_system_times()
        t0 = time.perf_counter()
        time.sleep(seconds)
        i1, k1, u1 = _get_system_times()
        wall = time.perf_counter() - t0

        di, dk, du = i1 - i0, k1 - k0, u1 - u0
        # 物理上限：所有逻辑核满载时的 ticks
        ceiling = _TICKS_PER_SEC * self.n * wall
        # ★ 重复计数自检的正确阈值：
        #   因 kernel 含 idle，三者和 ≈ 2*idle + (内核非idle) + user
        #   ⇒ 其**理论上界是 2×N**（不是 1×N）。我最初拿 1×N 当上限，
        #   于是任何正常采样都被判成「计时域异常」（实测 131.47 vs 48，误报）。
        #   放宽到 2.2×N 后，只有真正异常的计时域才会被拦。
        if (di + dk + du) > ceiling * 2.2:
            return CpuSample(
                busy_pct=0.0, idle_pct=0.0,
                busy_cores=0.0, idle_cores=0.0, seconds=wall,
                reliable=False,
                note=f"三者和({(di + dk + du) / _TICKS_PER_SEC:.2f}核·秒)"
                     f"超过理论上界({2.2 * self.n:.0f} 核·秒)⇒ 计时域异常",
            )

        # ★ 正确判据：kernel 含 idle ⇒
        #   非 idle 占用 = (kernel - idle) + user
        #   总量= kernel + user  （kernel 已含 idle，无须再加）
        busy = (dk - di) + du
        total = dk + du
        if busy < 0 or total <= 0:
            return CpuSample(
                busy_pct=0.0, idle_pct=100.0,
                busy_cores=0.0, idle_cores=float(self.n), seconds=wall,
                reliable=False,
                note="采样窗口过短或时钟粒度不足（idle 增量 ≥ kernel 增量）",
            )

        busy_pct = 100.0 * busy / total
        idle_pct = 100.0 * di / total
        return CpuSample(
            busy_pct=busy_pct,
            idle_pct=idle_pct,
            busy_cores=busy / (_TICKS_PER_SEC * wall),
            idle_cores=di / (_TICKS_PER_SEC * wall),
            seconds=wall,
            reliable=True,
        )

    def sample_repeated(self, times: int = 5, seconds: float = 2.0) -> list[CpuSample]:
        """连续采样多次（用于看稳定性，min-of-k 只滤 run 内噪声，这里看跨窗口漂移）。"""
        return [self.sample(seconds) for _ in range(max(1, times))]


def system_busy_pct(seconds: float = 3.0) -> float:
    """便捷函数：系统级非 idle 占用百分比（**订正后**的口径）。"""
    return CpuLoadProbe().sample(seconds).busy_pct


def process_busy_pct(seconds: float = 3.0) -> float:
    """便捷函数：逐进程枚举求和（**会低估**，仅作交叉验证，不单独作结论依据）。

    返回 0.0 表示无法枚举（权限不足等）——此时应依赖 `system_busy_pct`。
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _PENTRY(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_char * 260),
        ]

    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.OpenProcess.restype = ctypes.c_void_p

    def _procs() -> dict[int, str]:
        snap = k32.CreateToolhelp32Snapshot(2, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return {}
        pe = _PENTRY()
        pe.dwSize = ctypes.sizeof(pe)
        out: dict[int, str] = {}
        try:
            if k32.Process32First(ctypes.c_void_p(snap), ctypes.byref(pe)):
                while True:
                    out[pe.th32ProcessID] = pe.szExeFile.decode("utf-8", "replace")
                    if not k32.Process32Next(ctypes.c_void_p(snap), ctypes.byref(pe)):
                        break
        finally:
            k32.CloseHandle(ctypes.c_void_p(snap))
        return out

    def _times() -> dict[int, int]:
        res: dict[int, int] = {}
        for pid in _procs():
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                continue
            u, k, e = _FILETIME(), _FILETIME(), _FILETIME()
            try:
                if k32.GetProcessTimes(ctypes.c_void_p(h), ctypes.byref(u),
                                       ctypes.byref(k), ctypes.byref(e)):
                    res[pid] = u.ticks + k.ticks + e.ticks
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
        return res

    try:
        a = _times()
        if not a:
            return 0.0
        time.sleep(seconds)
        b = _times()
        n = max(1, os.cpu_count() or 1)
        total = 0
        for pid, t2 in b.items():
            if pid in a and (d := t2 - a[pid]) > 0:
                total += d
        return 100.0 * (total / (_TICKS_PER_SEC * seconds)) / n
    except OSError:
        return 0.0


if __name__ == "__main__":
    # 阶段 234 补：本脚本打印 `≈` / `⇒` / `✅` 等**非 GBK 字符**，
    # 在 GBK 管道（Windows 控制台重定向）下会把「通过」读成「退出码 1」。
    # 同批被 `tests/test_scripts_console_encoding.py::test_all_emoji_printing_scripts_are_hardened`
    # 抓出（该文件于 224 新建时漏了加固，潜伏 10 个阶段才被全量跑抓到）。
    from scripts.console_encoding import force_utf8_stdio

    force_utf8_stdio()

    probe = CpuLoadProbe()
    print("=" * 70)
    print("CPU 负载探针（Windows · 判据已订正）")
    print("=" * 70)
    print(f"逻辑核数 = {probe.n}")
    print()
    for i, s in enumerate(probe.sample_repeated(times=5, seconds=2.0), 1):
        print(f"  第{i}次: busy={s.busy_pct:5.1f}%  idle={s.idle_pct:5.1f}%  "
              f"({s.busy_cores:.2f}/{probe.n} 核)  → {s.verdict()}")
    print()
    print(f"  逐进程枚举（会低估，仅交叉验证）= {process_busy_pct(3.0):.1f}%")
    print()
    print("⚠ 订正说明：Windows GetSystemTimes 的 kernel **含 idle**，")
    print("  故 非idle占用 = (kernel - idle) + user；用 kernel+user 会虚高。")
