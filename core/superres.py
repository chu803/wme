# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/superres.py
 文件说明 : 阶段 32 · 真·超分（AI 模型 + torch 真实放大）后端。

            提供「真实超分辨率放大」能力（改变画布尺寸，如 4×），作为阶段 29
            「轻量超分」（同分辨率 cv2 细节重建）的补充 / 替代：

              · 真实超分依赖 torch + 预训练模型（ESRGAN / Real-ESRGAN 风格），
                lazy-import，缺依赖 / 缺权重时工厂函数返回 None，调用方
                （postprocess_service）回退到轻量 _superres，保证项目在无 torch
                环境下仍可完整测试与运行（向后兼容、torch-free-testable）。
              · 两种后端：
                  - "esrgan"    : 纯 torch 实现的 RRDB 风格生成器（nn.Module，真实
                                  可推理）；提供并成功加载 .pth 权重时为真·训练模型
                                  推理（used_real=True）；**缺权重（或加载失败）时退化为
                                  纯双三次上采样**（nn.Upsample，仍走 torch 张量管线；
                                  放大生效但非学习式增强），并如实标注 note。
                                  ⚠ 阶段 115 修复：此前「缺权重」分支保留的是**随机
                                  初始化的卷积网络**，其输出是噪声（实测与干净双三次
                                  mean_abs_diff≈102、画面均值 138→45），却被 note 误称
                                  为「双三次放大的真实推理」⇒ 开启真·超分即得到近黑
                                  噪声图。现改为真正的双三次上采样。
                  - "realesrgan": 尝试 lazy import realesrgan + basicsr，用官方 RRDBNet
                                  x4 权重；缺包即不可用（工厂返回 None）。
              · 统一入口：
                  get_superres_backend(kind, model_path, model_dir) -> SuperResBackend | None
                  SuperResBackend.upscale(arr, scale) -> (out, used_real, note)
                    out        : 放大后的 H×W×3 uint8（BGR，OpenCV 排布）。
                    used_real  : 是否用了「训练模型」（有权重）的真实推理；
                                 仅双三次回退时为 False。
                    note       : 人类可读说明（日志 / 界面提示降级原因）。

            设计约束：
              · 本模块属 core 层，零 gradio / 零 ui 依赖。
              · 严禁导入期 import torch（否则无 torch 环境 import 即崩）；仅在 backend
                实例化 / 调用时按需 lazy import，ImportError 被吞掉并转为 None。
              · 仅当后端确实能产生放大图时才返回非 None；调用方务必处理返回值的
                out 为 None 的情况（回退轻量超分）。
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from typing import Optional, Tuple

import numpy as np

# ------------------------------------------------------------------------------
# 路径加固：保证无论从哪个目录启动都能 import 到项目根模块。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from utils.logger import get_logger

log = get_logger("core.superres")

# 真实超分返回值类型：(放大后数组 | None, 是否真·训练模型推理, 说明)
SrResult = Tuple[Optional[np.ndarray], bool, str]


class SuperResBackend:
    """
    真实超分后端基类。子类实现 _infer(arr, scale) -> np.ndarray（放大后的 BGR uint8）。

    upscale() 负责数组↔tensor 转换与边界校验，返回 (out, used_real, note)。
    若无法产出放大图（异常 / 输入非法），返回 (None, False, note) 供调用方回退。
    """

    name = "base"

    def __init__(self, model_path: str = "", model_dir: str = "") -> None:
        self.model_path = model_path
        self.model_dir = model_dir
        self.used_real = False
        self.note = ""

    def _infer(self, arr: np.ndarray, scale: int) -> np.ndarray:
        raise NotImplementedError

    def upscale(self, arr: np.ndarray, scale: int) -> SrResult:
        """
        对 BGR uint8 数组做真实放大。返回 (out_or_None, used_real, note)。

        arr 形状非法，或推理异常时返回 (None, False, note)，调用方据此回退轻量超分。
        """
        if not isinstance(arr, np.ndarray) or arr.ndim != 3 or arr.shape[2] != 3:
            return None, False, "输入非 BGR 三通道数组，跳过真实超分"
        try:
            out = self._infer(arr, int(scale))
        except Exception as exc:  # noqa: BLE001
            log.warning("真实超分推理异常：%s", exc)
            return None, False, f"真实超分推理异常，回退轻量超分：{exc}"
        if not isinstance(out, np.ndarray) or out.ndim != 3 or out.shape[2] != 3:
            return None, False, "真实超分输出异常，回退轻量超分"
        return out, self.used_real, self.note or f"{self.name} 放大 {scale}×"


class _TorchESRGANBackend(SuperResBackend):
    """
    纯 torch RRDB 风格生成器后端（无需 basicsr）。

    架构为真实 nn.Module：卷积头 → 若干残差块 → 上采样（nn.Upsample 双三次）→ 卷积输出。
    提供并成功加载 .pth 时为真·训练模型推理（used_real=True）；**无权重（或加载失败）
    时退化为纯双三次上采样**（nn.Upsample，不走随机卷积），放大生效但非学习式增强。
    """

    name = "esrgan"

    def __init__(self, model_path: str = "", model_dir: str = "") -> None:
        super().__init__(model_path=model_path, model_dir=model_dir)
        # 延迟到首次推理时 lazy import torch 并构建模型，避免导入期依赖 torch。
        self._torch = None
        self._model = None
        self._built = False
        self._built_scale: Optional[int] = None  # 阶段 115：模型缓存按 scale 键控

    def _ensure_model(self, scale: int) -> None:
        # 阶段 115：缓存按 scale 键控——同一实例被要求不同倍数时必须重建，否则第二次
        # 请求会复用旧倍数模型，返回尺寸与请求不符（实测请求 4× 得到 2× 输出）。
        if self._built and self._built_scale == scale:
            return
        # lazy import torch（无 torch 环境此处抛 ImportError，由 upscale 捕获转 None）。
        import os as _os  # pylint: disable=import-outside-toplevel
        import torch  # type: ignore  # pylint: disable=import-outside-toplevel
        from torch import nn  # type: ignore  # pylint: disable=import-outside-toplevel
        self._torch = torch

        nf = 64
        n_blocks = 8

        class _ResBlock(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv1 = nn.Conv2d(nf, nf, 3, 1, 1)
                self.conv2 = nn.Conv2d(nf, nf, 3, 1, 1)
                self.relu = nn.ReLU(True)

            def forward(self, x):
                return self.relu(self.conv2(self.relu(self.conv1(x))) + x)

        class _RRDBMini(nn.Module):
            """轻量 RRDB 风格生成器：通道头 → 残差堆叠 → 上采样 → 输出。"""

            def __init__(self, scale: int = 4, nf: int = 64, n_blocks: int = 8):
                super().__init__()
                # 阶段 48 修复：forward 使用 self.relu，但此前 __init__ 从未定义它。
                # 该后端在无 torch 环境下从未真实执行过，2026-08-30 装好 torch 后
                # 首次运行即抛 "_RRDBMini object has no attribute 'relu'"。
                self.relu = nn.ReLU(True)
                self.head = nn.Conv2d(3, nf, 3, 1, 1)
                self.body = nn.Sequential(*[_ResBlock() for _ in range(n_blocks)])
                self.tail = nn.Conv2d(nf, nf, 3, 1, 1)
                self.up = nn.Upsample(scale_factor=scale, mode="bicubic",
                                      align_corners=False)
                self.out = nn.Conv2d(nf, 3, 3, 1, 1)

            def forward(self, x):
                f = self.relu(self.head(x))
                f = self.tail(self.body(f)) + f
                f = self.up(f)
                return self.out(f)

        # 阶段 115 修复：先判断是否真有可加载权重。**无权重时绝不保留随机初始化的卷积
        # 网络**——随机卷积输出是噪声（实测与干净双三次 mean_abs_diff≈102、画面均值
        # 138→45），旧实现却把它当「双三次放大的真实推理」返回，导致开启真·超分即得到
        # 近黑噪声图。此处改为纯双三次上采样（仍走 torch 张量管线，放大生效）。
        has_weights = bool(self.model_path) and _os.path.exists(self.model_path)
        if not has_weights:
            self._model = nn.Upsample(scale_factor=scale, mode="bicubic",
                                      align_corners=False)
            self.used_real = False
            self.note = (f"ESRGAN 后端无训练权重，已退化为纯双三次放大（{scale}×）；"
                         f"配置 REAL_SUPERRES_MODEL_PATH 可启用学习式增强")
            self._built = True
            self._built_scale = scale
            return

        model = _RRDBMini(scale=scale, nf=nf, n_blocks=n_blocks)
        model.eval()
        loaded = False
        try:
            sd = torch.load(self.model_path, map_location="cpu")  # noqa: S614
            if isinstance(sd, dict) and "state_dict" in sd:
                sd = sd["state_dict"]
            model.load_state_dict(sd, strict=False)
            loaded = True
        except Exception as exc:  # noqa: BLE001
            log.warning("加载 ESRGAN 权重失败：%s", exc)
        if loaded:
            self._model = model
            self.used_real = True
            self.note = f"ESRGAN 训练权重推理（{scale}×）"
        else:
            # 权重文件存在但加载失败：同样不得保留随机网络。
            self._model = nn.Upsample(scale_factor=scale, mode="bicubic",
                                      align_corners=False)
            self.used_real = False
            self.note = f"ESRGAN 权重加载失败，已退化为纯双三次放大（{scale}×）"
        self._built = True
        self._built_scale = scale

    def _infer(self, arr: np.ndarray, scale: int) -> np.ndarray:
        self._ensure_model(scale)
        torch = self._torch
        # BGR uint8 -> RGB float tensor (1,3,H,W)，归一 [0,1]。
        # 注意：arr[:, :, ::-1] 是负步长视图，torch>=2.9 的 from_numpy 不支持
        # 负步长（2026-08-30 实测报 "negative strides not supported"），须先拷贝。
        rgb = np.ascontiguousarray(arr[:, :, ::-1])
        t = torch.from_numpy(rgb).permute(2, 0, 1).float().div(255.0).unsqueeze(0)
        with torch.no_grad():
            out = self._model(t)
        out = out.squeeze(0).permute(1, 2, 0).clamp(0, 1).mul(255).byte().cpu().numpy()
        return out[:, :, ::-1]  # 回 BGR


class _RealESRGANBackend(SuperResBackend):
    """
    Real-ESRGAN 官方后端（lazy import realesrgan + basicsr）。

    需要第三方包 realesrgan / basicsr 与官方 RRDBNet x4 权重；任一缺失即不可用
    （工厂返回 None）。本后端为「真·训练模型」推理，used_real 恒为真。
    """

    name = "realesrgan"

    def __init__(self, model_path: str = "", model_dir: str = "") -> None:
        super().__init__(model_path=model_path, model_dir=model_dir)
        # lazy import 整个后端（无包即抛 ImportError -> 工厂捕获转 None）。
        from realesrgan import RealESRGANer  # type: ignore  # pylint: disable=import-outside-toplevel
        from basicsr.archs.rrdbnet_arch import RRDBNet  # type: ignore  # pylint: disable=import-outside-toplevel
        import os as _os  # pylint: disable=import-outside-toplevel
        model_dir = self.model_dir or config.REAL_SUPERRES_MODEL_DIR
        _os.makedirs(model_dir, exist_ok=True)
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=4)
        self._upsampler = RealESRGANer(
            scale=4,
            model_path=self.model_path or None,
            model=model,
            tile=0,
            tile_pad=10,
            pre_pad=0,
            half=False,
            gpu_id=None,
        )
        self.used_real = True
        self.note = "Real-ESRGAN 官方权重推理（4×）"

    def _infer(self, arr: np.ndarray, scale: int) -> np.ndarray:
        # RealESRGANer.enhance 接受 RGB uint8 ndarray，返回 (output, _)。
        out, _ = self._upsampler.enhance(arr[:, :, ::-1], outscale=scale)
        return out[:, :, ::-1]


def get_superres_backend(
    kind: str = "auto",
    model_path: str = "",
    model_dir: str = "",
) -> Optional[SuperResBackend]:
    """
    真实超分后端工厂。返回可用的 SuperResBackend 实例，或 None（无可用后端）。

    参数：
        kind      : "auto" | "esrgan" | "realesrgan"。
                     "auto" → 依次尝试 realesrgan → esrgan → None。
        model_path: 自定义 ESRGAN .pth 权重路径（esrgan 后端用）。
        model_dir : Real-ESRGAN 权重目录（realesrgan 后端用）。

    返回 None 的场景（调用方应回退轻量超分）：
        · 任意后端所需第三方包（torch / realesrgan / basicsr）缺失；
        · realesrgan 指定但权重无法就绪；
        · 未知 kind。
    """
    kind = (kind or "auto").lower()

    def _try(k: str) -> Optional[SuperResBackend]:
        try:
            if k == "esrgan":
                # 先验 torch：无 torch 直接放弃该后端，保持「工厂返回 None」契约。
                import torch  # type: ignore  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
                return _TorchESRGANBackend(model_path=model_path, model_dir=model_dir)
            if k == "realesrgan":
                return _RealESRGANBackend(model_path=model_path, model_dir=model_dir)
        except Exception as exc:  # noqa: BLE001
            log.info("真实超分后端 %s 不可用（将回退轻量超分）：%s", k, exc)
        return None

    if kind == "auto":
        for k in ("realesrgan", "esrgan"):
            b = _try(k)
            if b is not None:
                return b
        return None
    return _try(kind)


def superres_upscale(
    arr: np.ndarray,
    backend: Optional[SuperResBackend],
    scale: int = 4,
) -> SrResult:
    """
    便捷入口：用给定后端放大；backend 为 None 时返回 (None, False, note)。

    调用方（postprocess_service）据此判断是否需要回退轻量超分。
    """
    if backend is None:
        return None, False, "无可用真实超分后端"
    return backend.upscale(arr, scale)


# ==============================================================================
# 模块自检（无 torch 环境应优雅跳过，不报错）
# ==============================================================================
if __name__ == "__main__":
    # 无 torch 环境下：工厂应返回 None，superres_upscale 回退说明。
    b = get_superres_backend("auto")
    print("auto 后端（无 torch 期望 None）：", b)
    fake = np.zeros((64, 64, 3), dtype=np.uint8)
    out, used, note = superres_upscale(fake, b, 4)
    assert out is None, "无后端时 out 应为 None"
    print("superres_upscale 回退说明：", note)
    print("core/superres.py 自检通过（后端不可用路径）。")
