# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/model_loader.py
 文件说明 : LaMa 模型加载与单例缓存。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【职责边界】
   本模块只负责「把磁盘上的 big-lama.pt 变成内存里可用的推理模型」，
   包括：存在性校验、格式一致性校验、设备映射、eval 模式、单例缓存。
   具体的「喂数据 / 取结果」推理逻辑放在 inpainter.py，二者解耦。
==============================================================================
"""

from __future__ import annotations

import hashlib
import os
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
from core.device_manager import _DeviceHandle, get_device_info
from utils.exceptions import (
    ModelCorruptedError,
    ModelLoadError,
    ModelNotFoundError,
)
from utils.logger import get_logger

# 阶段 12：所有加载/校验逻辑统一委托给 core.model_manager（多模型槽 + active 切换）。
#   本模块仅保留对外公开函数签名，便于既有调用方（inpainter / plugins / main / api）零改动。
from core import model_manager

log = get_logger("core.model_loader")


# 单例缓存：模型权重约 200MB，反复加载既慢又吃内存，必须只加载一次。
_cached_model = None
_cached_model_path: Optional[str] = None


def _validate_model_file(model_path: str) -> None:
    """
    在加载前对模型文件做基础校验，尽早暴露问题。

    参数说明：
        model_path : 模型文件完整路径。

    异常：
        ModelNotFoundError    : 文件不存在。
        ModelCorruptedError   : 文件体积远小于预期（下载不完整）。
    """
    if not os.path.exists(model_path):
        raise ModelNotFoundError(
            f"未找到 LaMa 模型文件：{model_path}\n"
            f"请执行 download_model.py 自动下载，或检查 MODEL_DIR 配置。"
        )

    size = os.path.getsize(model_path)
    # 预期大小 * 90% 作为底线，低于此值基本可判定下载被截断。
    min_size = int(config.MODEL_EXPECTED_SIZE * 0.9)
    if size < min_size:
        raise ModelCorruptedError(
            f"模型文件体积异常：{size / 1e6:.1f} MB，"
            f"预期约 {config.MODEL_EXPECTED_SIZE / 1e6:.0f} MB。\n"
            f"文件可能下载不完整，请删除后重新下载。"
        )

    # 可选：若配置了 SHA256，则做哈希校验，防止文件被篡改/损坏。
    expected_sha = config.MODEL_SHA256.strip()
    if expected_sha:
        actual = _sha256_of_file(model_path)
        if actual.lower() != expected_sha.lower():
            raise ModelCorruptedError(
                f"模型文件 SHA256 校验失败。\n期望：{expected_sha}\n实际：{actual}"
            )


def _sha256_of_file(path: str, chunk: int = 8 * 1024 * 1024) -> str:
    """
    以分块方式计算大文件的 SHA256，避免一次性读入内存。

    参数说明：
        path  : 文件路径。
        chunk : 每次读取的字节数（默认 8MB）。

    返回值：
        十六进制 SHA256 字符串。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def load_lama_model(device: Optional[_DeviceHandle] = None, force_reload: bool = False):
    """
    加载「当前模型槽（active）」的 TorchScript 权重，返回单例（已设为 eval 模式）。

    阶段 12 起，LaMa 引擎一律加载 model_manager 中标记为 active 的模型槽权重，
    因此「切换模型」只需改 active 槽，无需改动此处或任何调用方。

    参数说明：
        device       : 目标设备句柄。传 None 时按当前配置自动选择。
        force_reload : 强制重新加载（忽略缓存），用于切换设备后刷新。

    返回值：
        已加载的 torch.jit 模型对象（可直接调用 model(image, mask)）。

    异常：
        ModelLoadError : 模型加载失败（如 PyTorch 版本不兼容、文件损坏）。
    """
    # 委托给 model_manager：加载 active 槽权重（含存在性/完整性校验与缓存）。
    return model_manager.load_model(None, device, force_reload)


def is_model_available() -> bool:
    """
    判断「当前模型槽（active）」权重是否已就位（不加载，只检查存在性与体积）。

    返回值：
        True 表示存在且体积合理，可以加载；False 表示缺失或不完整。
    """
    return model_manager.is_model_available(None)


def clear_model_cache() -> None:
    """
    清空模型单例缓存，并主动释放显存。用于切换设备或卸载场景。
    """
    model_manager.clear_cache()


def get_model_info() -> dict:
    """
    返回模型当前状态信息，供 UI / API 展示。

    返回值：
        字典，含：exists / path / size_mb / loaded / device（兼容旧字段），
        以及 active_model / models（阶段 12 新增）。
    """
    info = model_manager.get_model_info()
    # 兼容旧调用方（api.get_capabilities 读 model_available / model_path）。
    info["model_available"] = bool(info.get("exists", False))
    info["model_path"] = info.get("path", "")
    return info


# 自检：仅检查「能否发现模型」，不实际加载（避免无 torch 时报错）。
if __name__ == "__main__":
    info = get_model_info()
    print("模型路径 :", info["path"])
    print("是否存在 :", info["exists"])
    if info["exists"]:
        print(f"文件体积 : {info['size_mb']} MB")
        print("加载状态 :", "已加载" if info["loaded"] else "未加载")
    else:
        print("提示：模型尚未下载，运行 download_model.py 即可自动获取。")
