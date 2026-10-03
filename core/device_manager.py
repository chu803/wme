# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/device_manager.py
 文件说明 : 硬件探测与设备选择模块。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【职责边界】
   本模块只回答一个问题：「当前应该用 GPU 还是 CPU 跑模型？」
   它不加载模型、不做推理，纯粹做「环境探测 + 决策」，因此可以被
   任何上层（命令行、Gradio、批处理脚本）安全复用。
==============================================================================
"""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from typing import Optional

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from utils.logger import get_logger

log = get_logger("core.device_manager")


@dataclass
class DeviceInfo:
    """
    描述当前选择的计算设备及其能力，供界面展示与日志打印。

    字段说明：
        name        : 设备可读名称，如 "NVIDIA GeForce RTX 3060" 或 "CPU"。
        kind        : "cuda" 或 "cpu"，供 PyTorch 创建 torch.device 用。
        index       : 显卡序号（CPU 时为 -1）。
        vram_mb     : 显卡总显存（MB），CPU 时为 0。
        is_gpu      : 是否为显卡加速模式。
        reason      : 选择该设备的原因（中文），便于排查「为什么没用上显卡」。
    """

    name: str
    kind: str
    index: int = -1
    vram_mb: int = 0
    is_gpu: bool = False
    reason: str = ""


# 本模块维护一个全局「已选定设备」缓存，避免每次推理都重新探测一遍。
_cached_device: Optional["_DeviceHandle"] = None


class _DeviceHandle:
    """
    设备句柄：封装选中的 torch.device 及配套配置。

    设计意图：
        device_manager 对外只暴露 get_device() 返回的 DeviceInfo（纯数据），
        真正创建 torch.device 的工作延迟到需要时才做，并缓存结果。
        这样「仅探测设备」时（例如启动展示）不会触发 import torch 的代价。
    """

    def __init__(self, info: DeviceInfo) -> None:
        self.info: DeviceInfo = info
        self._torch_device = None  # 懒加载

    @property
    def is_gpu(self) -> bool:
        """
        是否为显卡加速模式。

        委托给底层 DeviceInfo.is_gpu，便于模型管理器等调用方在不关心
        DeviceInfo 细节时直接使用。阶段 48 修复：此前缺此属性，导致
        LaMa 模型在 torch 可用时加载即抛 AttributeError
        （'_DeviceHandle' object has no attribute 'is_gpu'）。
        """
        return self.info.is_gpu

    @property
    def torch_device(self):
        """
        返回 torch.device 对象（懒加载并缓存）。
        若未安装 PyTorch，会抛出清晰的中文异常。
        """
        if self._torch_device is not None:
            return self._torch_device
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise RuntimeError(
                "未检测到 PyTorch 库，无法创建计算设备。\n"
                "请先安装 CPU 版 PyTorch：\n"
                "  pip install torch torchvision --index-url "
                "https://download.pytorch.org/whl/cpu"
            ) from exc

        if self.info.kind == "cuda":
            # 再次确认 CUDA 真的可用（可能驱动掉了）。
            if not torch.cuda.is_available():
                raise RuntimeError(
                    "配置要求使用 CUDA，但当前环境检测不到可用的 NVIDIA 显卡。"
                    "请检查显卡驱动，或把 DEVICE_MODE 改为 auto / cpu。"
                )
            self._torch_device = torch.device(f"cuda:{self.info.index}")
        else:
            self._torch_device = torch.device("cpu")
        return self._torch_device


def _detect_cuda() -> Optional[DeviceInfo]:
    """
    探测系统中是否存在可用的 NVIDIA 显卡。

    返回值：
        若可用，返回填充好名称/显存信息的 DeviceInfo；
        若不可用（没装 torch、没装驱动、没显卡），返回 None。
    """
    try:
        import torch
    except ImportError:
        log.warning("未安装 PyTorch，无法使用 GPU，将退回 CPU 模式。")
        return None

    if not torch.cuda.is_available():
        log.info("torch.cuda.is_available() 返回 False，系统无可用 NVIDIA 显卡。")
        return None

    try:
        index: int = torch.cuda.current_device()
        prop = torch.cuda.get_device_properties(index)
        name: str = prop.name
        vram_mb: int = int(prop.total_memory / (1024 * 1024))
        return DeviceInfo(
            name=name,
            kind="cuda",
            index=index,
            vram_mb=vram_mb,
            is_gpu=True,
            reason=f"检测到可用显卡 {name}（显存约 {vram_mb} MB）",
        )
    except Exception as exc:  # pragma: no cover - 极端异常
        log.warning("探测显卡属性时出错：%s，退回 CPU。", exc)
        return None


def _query_free_vram_mb(index: int) -> Optional[int]:
    """
    查询指定显卡当前剩余显存（MB）。

    返回值：
        剩余显存 MB；若无法查询（没装 pynvml / nvidia-ml-py）返回 None，
        此时调用方应保守处理，不依赖该数值做决策。

    实现说明：
        nvmlInit() 与 nvmlShutdown() 必须成对出现。此前只在成功路径上
        shutdown，一旦 nvmlDeviceGetHandleByIndex / GetMemoryInfo 抛错，
        异常被 except 吞掉，NVML 就停留在「已初始化未关闭」状态——
        典型的「静默容错把资源泄漏藏起来」。改用 try/finally 保证一定关闭。
    """
    pynvml = None
    try:
        # pynvml 是 nvidia-ml-py 的导入名，可选依赖。
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return int(mem.free / (1024 * 1024))
    except Exception:
        # 查不到就返回 None，不影响主流程。
        return None
    finally:
        # 早退 / 异常路径同样要关闭；nvmlInit 本身失败时 shutdown 会抛错，再兜一层。
        try:
            if pynvml is not None:
                pynvml.nvmlShutdown()
        except Exception:  # noqa: BLE001
            pass


def select_device(mode: Optional[str] = None) -> _DeviceHandle:
    """
    根据配置或传入的模式选择计算设备，返回可复用的设备句柄。

    参数说明：
        mode : 设备模式。传 None 时使用 config.DEVICE_MODE。
               取值 auto / cuda / cpu。

    返回值：
        _DeviceHandle 实例，后续可通过 .torch_device 拿到真正的 torch.device。

    决策逻辑：
        auto ：先探测 CUDA，探测成功且（显存充足或无需查询）则上 GPU，
               否则退回 CPU。
        cuda ：强制 GPU，若不可用则明确报错（不默默降级，避免用户误以为在用显卡）。
        cpu  ：强制 CPU。
    """
    global _cached_device

    # 若已缓存且模式未变化，直接复用缓存，避免重复探测抖动。
    effective_mode: str = (mode or config.DEVICE_MODE).lower()
    if _cached_device is not None:
        if _cached_device.info.kind == effective_mode or effective_mode == "auto":
            # auto 模式下，若之前已经定下来（无论 GPU/CPU）就复用。
            return _cached_device

    # ---- 强制 CPU ----
    if effective_mode == "cpu":
        info = DeviceInfo(
            name=f"CPU ({platform.processor() or '未知架构'})",
            kind="cpu",
            reason="用户强制指定 CPU 模式",
        )
        _cached_device = _DeviceHandle(info)
        log.info("已选择设备：CPU（%s）", info.reason)
        return _cached_device

    # ---- 强制 CUDA ----
    if effective_mode == "cuda":
        gpu: Optional[DeviceInfo] = _detect_cuda()
        if gpu is None:
            raise RuntimeError(
                "DEVICE_MODE=cuda 但系统无可用 NVIDIA 显卡。"
                "请安装显卡驱动，或把配置改为 auto / cpu。"
            )
        _cached_device = _DeviceHandle(gpu)
        log.info("已选择设备：%s（强制 CUDA）", gpu.name)
        return _cached_device

    # ---- auto 模式 ----
    gpu = _detect_cuda()
    if gpu is None:
        info = DeviceInfo(
            name=f"CPU ({platform.processor() or '未知架构'})",
            kind="cpu",
            reason="未检测到 NVIDIA 显卡，自动退回 CPU",
        )
        _cached_device = _DeviceHandle(info)
        log.info("自动选择设备：CPU（%s）", info.reason)
        return _cached_device

    # 探测到显卡，再做「显存安全水位」决策。
    free_mb: Optional[int] = _query_free_vram_mb(gpu.index)
    if free_mb is not None and free_mb < config.DEVICE_MIN_VRAM_MB:
        info = DeviceInfo(
            name=f"CPU ({platform.processor() or '未知架构'})",
            kind="cpu",
            reason=(
                f"显卡剩余显存约 {free_mb} MB，低于安全阈值 "
                f"{config.DEVICE_MIN_VRAM_MB} MB，自动退回 CPU 以保证稳定"
            ),
        )
        _cached_device = _DeviceHandle(info)
        log.warning("显存不足，自动降级：%s", info.reason)
        return _cached_device

    gpu.reason = gpu.reason or "自动模式选择 GPU"
    _cached_device = _DeviceHandle(gpu)
    log.info("自动选择设备：%s", gpu.name)
    return _cached_device


def get_device_info() -> DeviceInfo:
    """
    获取当前已选定设备的信息（不创建 torch.device，仅读取已缓存的元数据）。

    返回值：
        当前 DeviceInfo。若尚未选择，则先按默认模式选择一次。
    """
    handle: _DeviceHandle = select_device()
    return handle.info


def apply_cpu_threads(threads: Optional[int] = None) -> None:
    """
    设置 PyTorch / OpenCV 在 CPU 模式下的可用线程数。

    参数说明：
        threads : 线程数。传 None 使用 config.CPU_THREADS（0 表示用全部物理核心）。

    说明：
        LaMa 推理主要走 PyTorch；OpenCV 修复走 cv2。两者都应尊重该配置，
        避免一次性占满所有核心把电脑卡死（尤其是同时开着浏览器时）。
    """
    n: int = threads if threads is not None else config.CPU_THREADS
    try:
        import torch

        if n and n > 0:
            torch.set_num_threads(n)
            torch.set_num_interop_threads(n)
    except Exception:
        pass  # 没装 torch 也不影响下面的 cv2 设置

    try:
        import cv2

        if n and n > 0:
            cv2.setNumThreads(n)
    except Exception:
        pass

    if n and n > 0:
        # 也通过环境变量给可能用到的其他库（如 numpy 的 OpenBLAS）一个暗示。
        os.environ.setdefault("OMP_NUM_THREADS", str(n))
        os.environ.setdefault("MKL_NUM_THREADS", str(n))

    log.debug("CPU 线程数已设置为 %s", n if n else "自动（物理核心数）")


# 空显存释放节流：记录上次真实释放的时间戳（monotonic 秒）。
_last_empty_ts: float = 0.0


def empty_cache(*, force: bool = False) -> None:
    """
    在显存紧张的设备上，推理结束后主动释放 PyTorch 缓存的显存。

    阶段 14 优化：torch.cuda.empty_cache() 是同步操作，会打断 GPU 流水线。
    在批量 / 视频逐帧场景下如果每帧都调用会显著掉帧，因此按
    config.EMPTY_CACHE_MIN_INTERVAL_SEC 做节流——两次释放间隔不足时直接跳过，
    显存压力由 PyTorch 自身缓存池兜底。force=True 可绕过节流（如显式卸载时）。

    说明：
        PyTorch 默认会「缓存」已分配显存以便复用，这会让系统看起来显存一直很满。
        当 config.DEVICE_EMPTY_CACHE_AFTER_RUN 为 True 时，调用本函数及时归还。
    """
    global _last_empty_ts
    if not config.DEVICE_EMPTY_CACHE_AFTER_RUN:
        return
    if not force:
        # 节流：间隔未到则跳过，避免连续推理时反复同步掉帧。
        now = time.monotonic()
        if (now - _last_empty_ts) < config.EMPTY_CACHE_MIN_INTERVAL_SEC:
            return
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            _last_empty_ts = time.monotonic()
    except Exception:
        pass


def use_fp16() -> bool:
    """
    判断当前是否应使用半精度（float16）推理。

    返回值：
        True 表示可使用半精度；False 表示使用 float32。

    约束：
        仅在 GPU 模式下才允许半精度；CPU 上 float16 反而更慢且可能不支持。
    """
    if not config.DEVICE_USE_FP16:
        return False
    info: DeviceInfo = get_device_info()
    return info.is_gpu


def reset_cache() -> None:
    """
    清空设备选择缓存。在「切换模式」或「单元测试」时调用。
    """
    global _cached_device
    _cached_device = None


# 自检：直接运行会演示 auto 模式下的设备选择结果。
if __name__ == "__main__":
    apply_cpu_threads()
    handle = select_device("auto")
    info = handle.info
    print(f"设备类型 : {info.kind}")
    print(f"设备名称 : {info.name}")
    print(f"是否GPU  : {info.is_gpu}")
    print(f"选择原因 : {info.reason}")
    if info.is_gpu:
        print(f"总显存   : {info.vram_mb} MB")
    print(f"半精度推理: {use_fp16()}")
    print("\ndevice_manager.py 自检完成。")
