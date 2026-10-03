# -*- coding: utf-8 -*-
"""
==============================================================================
 示例插件：自定义修复模型接入模板（demo_model）
==============================================================================
 本文件演示「如何把一个非 LaMa 的修复模型接入本工作台」。

 它是开箱即跑的：
   · 未配置自定义模型时，用 OpenCV TELEA 算法兜底，保证插件立即可用；
   · 一旦设置环境变量 WME_CUSTOM_MODEL_PATH 指向你的 TorchScript 模型，
     会自动加载，并在 inpaint() 中调用 _run_custom_model()。

 想接入你自己的模型，只需：
   1. 复制本文件，改名（如 my_diffusion_plugin.py）；
   2. 改 name / display_name / description；
   3. 在 _run_custom_model() 里按你模型的输入/输出签名改写；
   4. 把文件放进 plugins/ 目录；
   5. 启动程序，即可在「修复引擎」里选到它。

 接口约定（务必遵守）：
   inpaint(image, mask, *, device=None, progress=None) -> np.ndarray
     image : H×W×3 uint8 RGB 原图
     mask  : H×W uint8 0/255 二值掩码（已膨胀/阈值/羽化后二值化）
     返回  : H×W×3 uint8 RGB，掩码区域已被填充，其余应与原图一致

 注意：本文件位于 plugins/ 目录，由 core.plugins.load_plugins() 在启动时自动导入，
       导入时即执行末尾的 register_engine(DemoModelEngine) 完成注册。
==============================================================================
"""

from __future__ import annotations

import os

import numpy as np

from core.plugins import InpaintEnginePlugin, register_engine
from utils.logger import get_logger

log = get_logger("plugin.sample_model")


class DemoModelEngine(InpaintEnginePlugin):
    # ---- 引擎元信息（会出现在界面 / 命令行 / API 的引擎列表里）----
    name = "demo_model"                       # 唯一键（小写），命令行/API 用它选择本引擎
    display_name = "示例·自定义模型"            # 界面显示名
    description = "自定义模型接入模板：默认 OpenCV TELEA 兜底，可替换为任意 inpaint 模型"
    requires_model = False                    # 设为 True 时，分派前会先调一次 setup()

    def __init__(self) -> None:
        self._model = None

    # ------------------------------------------------------------------
    # setup()：一次性初始化（加载权重 / 建立连接）。
    # 由框架在「requires_model=True」时、首次修复前调用一次。
    # 本示例设为 False，因此不会被调用；此处仅为演示如何加载模型。
    # ------------------------------------------------------------------
    def setup(self) -> None:
        path = os.environ.get("WME_CUSTOM_MODEL_PATH", "").strip()
        if not path or not os.path.exists(path):
            log.info("[示例插件] 未配置 WME_CUSTOM_MODEL_PATH，使用 OpenCV TELEA 兜底。")
            self._model = None
            return
        try:
            import torch

            self._model = torch.jit.load(path, map_location="cpu")
            self._model.eval()
            log.info("[示例插件] 已加载自定义模型：%s", path)
        except Exception as exc:  # noqa: BLE001
            log.warning("[示例插件] 自定义模型加载失败，回退 OpenCV：%s", exc)
            self._model = None

    # ------------------------------------------------------------------
    # inpaint()：核心修复接口，框架会传入已预处理好的原图与二值掩码。
    # 掩码后处理、降噪、力度融合由框架统一完成，这里只管「填充掩码区」。
    # ------------------------------------------------------------------
    def inpaint(self, image, mask, *, device=None, progress=None) -> np.ndarray:
        if progress:
            progress(0.4, "示例插件：正在修复...")

        if self._model is not None:
            return self._run_custom_model(image, mask)
        return self._opencv_fallback(image, mask)

    # ------------------------------------------------------------------
    # 自定义模型推理（模板）：按你模型的真实签名改写本方法即可。
    # 下面给出与 LaMa 一致的 (image, mask) -> 归一化输出 的 TorchScript 调用框架，
    # 输入/输出约定为 [-1,1]（LaMa 风格），如你的模型不同请相应调整。
    # ------------------------------------------------------------------
    def _run_custom_model(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        import torch

        from core.image_utils import mask_to_tensor, rgb_to_tensor, tensor_to_rgb

        # 转张量并归一化到 [-1, 1]（与 LaMa 训练约定一致）。
        img_t = rgb_to_tensor(image, device_str="cpu") * 2 - 1
        mask_t = mask_to_tensor(mask, device_str="cpu")
        with torch.no_grad():
            out = self._model(img_t, mask_t)  # 输出在 [-1, 1]
        out = (out * 0.5 + 0.5).clamp(0, 1)    # 反归一化回 [0, 1]
        return tensor_to_rgb(out)

    # ------------------------------------------------------------------
    # OpenCV 兜底：未配置自定义模型时使用，保证插件开箱即跑。
    # ------------------------------------------------------------------
    def _opencv_fallback(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        import cv2

        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        method = cv2.INPAINT_TELEA
        result = cv2.inpaint(bgr, mask, 5, method)
        return cv2.cvtColor(result, cv2.COLOR_BGR2RGB)


# 模块导入时即注册本引擎（框架自动发现 plugins/ 目录下的本文件）。
register_engine(DemoModelEngine)
