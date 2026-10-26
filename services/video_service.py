# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/video_service.py
 文件说明 : 视频水印擦除服务（阶段 10 新增）。

            核心思路（和 PDF 一样是「拆 → 修 → 拼」）：
                第 1 步「拆」：用 OpenCV 逐帧读出视频里的每一张画面；
                第 2 步「修」：把画面交给 auto_pipeline.process_image 去水印；
                第 3 步「拼」：按原始帧率把修好的画面写回一个新视频文件。

            零基础必读的三个概念：
                · 帧(frame)      —— 视频里的一张静止画面。30fps 表示 1 秒 30 张。
                · 帧率(fps)      —— 每秒多少帧，决定播放速度，必须与原视频一致，
                                     否则输出视频会变快或变慢。
                · 抽帧步长(step) —— 每隔几帧才重新「找一次水印位置」。
                                     步长不会丢帧！所有帧都会被修复并写入，
                                     只是中间帧直接复用上一次找到的水印位置，
                                     对台标、右下角固定水印这类场景能快好几倍。

            重要提醒（务必告诉用户）：
                · 视频处理极慢。一段 30 秒 30fps 的视频 = 900 张图，
                  CPU 环境下可能要跑几十分钟，请先用短视频试水；
                · OpenCV 不处理音频，输出视频将【没有声音】。
                  如需保留原声，请在处理完成后用 ffmpeg 把原音轨合并回去
                  （文档 docs/10-多输入源设计.md 给了现成命令）。

            分层约束：本模块属于 services 层，只调用 core / utils / config
            与同层的 auto_pipeline，**不依赖 gradio**。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import cv2

import config
from core.device_manager import select_device
from core.superres import get_superres_backend, SuperResBackend
from services.auto_pipeline import process_image
from utils.path_helper import make_unique_path, ensure_parent_dir
from utils.logger import get_logger

log = get_logger("services.video")

ProgressCallback = Callable[[float, str], None]


def _noop_progress(_p: float, _msg: str) -> None:
    """空进度回调，调用方没传时兜底。"""
    return None


# ==============================================================================
# 第 1 节：能力探测与文件判断
# ==============================================================================
def is_video_available() -> bool:
    """
    探测当前环境是否具备视频处理能力。

    返回值：
        True 表示 OpenCV 的视频读写模块可用。

    说明：
        OpenCV 是本项目的硬依赖，正常情况下必定可用；
        这里仍做一次探测，是为了应对某些精简版 opencv 缺少 VideoWriter 的情况。
    """
    return hasattr(cv2, "VideoCapture") and hasattr(cv2, "VideoWriter")


def is_video_file(path: str | _os.PathLike) -> bool:
    """按扩展名判断是不是视频文件。"""
    return _os.path.splitext(str(path))[1].lower() in config.SUPPORTED_VIDEO_FORMATS


def get_video_info(src_path: str | _os.PathLike) -> Dict[str, Any]:
    """
    读取视频基本信息（只读，不做任何处理），供界面预览与耗时预估。

    返回值字典：
        ok        : 是否读取成功
        frames    : 总帧数
        fps       : 帧率
        width     : 画面宽度（像素）
        height    : 画面高度（像素）
        duration  : 时长（秒）
        size_mb   : 文件大小（MB）
        error     : 失败原因（ok=False 时有值）
    """
    src = str(src_path)
    info: Dict[str, Any] = {
        "ok": False, "frames": 0, "fps": 0.0, "width": 0, "height": 0,
        "duration": 0.0, "size_mb": 0.0, "error": None,
    }
    if not _os.path.isfile(src):
        info["error"] = f"找不到文件：{src}"
        return info

    cap = None
    try:
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            info["error"] = "OpenCV 无法打开该视频（可能是编码格式不受支持或文件损坏）。"
            return info

        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        info.update(
            ok=True,
            frames=max(0, frames),
            fps=round(fps, 3),
            width=width,
            height=height,
            duration=round(frames / fps, 2) if fps > 0 and frames > 0 else 0.0,
            size_mb=round(_os.path.getsize(src) / 1024 / 1024, 2),
        )
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:  # noqa: BLE001
            pass
    return info


def extract_sampled_frames(
    src_path: str | _os.PathLike,
    n: Optional[int] = None,
    *,
    return_indices: bool = True,
) -> "List[Dict[str, object]]":
    """
    从视频均匀抽取 n 帧（默认 config.VIDEO_COMPARE_FRAMES），用于结果浏览/拼图。

    参数说明：
        src_path       : 视频文件路径（源或结果均可）。
        n              : 抽样帧数；None 取 config.VIDEO_COMPARE_FRAMES。
        return_indices : 是否返回帧序号。True（默认）时每项含 "idx" 字段（帧序号，用于点击
                        定位对比帧）；False 时每项仅返回画面（{"image"}），不暴露帧序号。

    返回值：
        list[dict]。return_indices=True 时每项 {"idx": int, "image": np.ndarray(H×W×3 RGB uint8)}；
        return_indices=False 时每项仅 {"image": np.ndarray(H×W×3 RGB uint8)}（无 "idx" 字段）。
        越界 / 读不到 / 文件缺失时安全返回已成功抽取的帧（可能少于 n，甚至为空列表）。

    说明：
        采用「按目标帧序号集合顺序读取」策略：先算出目标帧序号集合（含首帧与末帧，
        均匀铺开），再顺序遍历视频帧，命中目标即抽取，抽到齐或读到末尾即收尾。
        这样对任何长度视频都只需顺序读一遍，不会反复 seek。
    """
    n = int(config.VIDEO_COMPARE_FRAMES if n is None else n)
    n = max(1, n)
    src = str(src_path)
    out: "List[Dict[str, object]]" = []

    cap = None
    try:
        if not _os.path.isfile(src):
            return out
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            return out

        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            # 读不到帧数（少数编码），改为「边读边抽前 n 帧」。
            idxs: "set[int]" = set(range(n))
        else:
            # 均匀铺开，并强制包含首帧与末帧。
            step = max(1, (total - 1) // max(1, n - 1)) if n > 1 else 1
            idxs = set()
            i = 0
            while len(idxs) < n and i <= total - 1:
                idxs.add(min(i, total - 1))
                i += step
            idxs.add(0)
            idxs.add(total - 1)
            idxs = {x for x in idxs if 0 <= x < total}

        # 顺序读一遍，命中目标帧序号就抽。
        captured: "set[int]" = set()
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx in idxs and idx not in captured:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                out.append({"idx": idx, "image": rgb})
                captured.add(idx)
            idx += 1
            if len(captured) >= len(idxs):
                break
    except Exception as exc:  # noqa: BLE001
        log.warning("视频抽样帧失败：%s", exc)
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:  # noqa: BLE001
            pass

    if not return_indices:
        # 仅返回画面、不暴露帧序号（用于不需要定位对比帧的纯展示场景）。
        out = [{"image": d["image"]} for d in out]
    return out


# ==============================================================================
# 第 2 节：结果数据结构
# ==============================================================================
@dataclass
class VideoResult:
    """视频处理结果汇总。"""

    src_path: str = ""                     # 源视频路径
    out_path: Optional[str] = None         # 结果视频路径
    total_frames: int = 0                  # 源视频总帧数
    written_frames: int = 0                # 实际写入输出视频的帧数
    repaired_frames: int = 0               # 真正被 AI 修复过的帧数
    detected_frames: int = 0               # 重新做过水印检测的帧数
    reused_frames: int = 0                 # 复用上一帧掩码的帧数
    clean_frames: int = 0                  # 未检出水印、原样写出的帧数
    failed_frames: int = 0                 # 处理失败、原样写出的帧数
    fps: float = 0.0                       # 输出帧率
    width: int = 0                         # 画面宽
    height: int = 0                        # 画面高
    codec: str = ""                        # 实际使用的编码器
    truncated: bool = False                # 是否因帧数上限而被截断
    seconds: float = 0.0                   # 总耗时
    status: str = "failed"                 # success / failed
    error: Optional[str] = None            # 失败原因
    warnings: List[str] = field(default_factory=list)  # 非致命提醒
    frame_stats: "List[object]" = field(default_factory=list)  # 阶段 23：逐帧检测命中率统计（DetectionStats 或 None）
    preset_counts: "Dict[str, int]" = field(default_factory=dict)  # 阶段 27：逐检测帧智能推荐预设分布
    recommend_conf_avg: float = 0.0   # 阶段 28：逐检测帧智能推荐平均置信度（detect_auto 时累计）
    _rec_conf_sum: float = 0.0        # 阶段 28：置信度累加（内部）
    _rec_conf_n: int = 0              # 阶段 28：置信度计数（内部）
    upscaled: bool = False            # 阶段 33：是否启用了真实超分（输出视频已被放大）
    real_scale_used: int = 0          # 阶段 33：实际放大倍数（未放大为 0）

    def summary(self) -> str:
        """生成一句人话总结。"""
        if self.status != "success":
            return f"视频处理失败：{self.error}"
        parts = [
            f"共写出 {self.written_frames} 帧（源 {self.total_frames} 帧）",
            f"修复 {self.repaired_frames} 帧",
            f"检测 {self.detected_frames} 次 / 复用掩码 {self.reused_frames} 帧",
            f"无水印 {self.clean_frames} 帧",
        ]
        if self.failed_frames:
            parts.append(f"失败 {self.failed_frames} 帧")
        if self.truncated:
            parts.append("⚠ 已达帧数上限被截断")
        if self.upscaled:
            parts.append(f"真实超分放大 {self.real_scale_used}×")
        parts.append(f"耗时 {self.seconds:.1f} 秒")
        return "，".join(parts) + "。"


# ==============================================================================
# 第 3 节：内部工具
# ==============================================================================
def _open_writer(
    dst: str, fps: float, width: int, height: int
) -> tuple[Optional[cv2.VideoWriter], str]:
    """
    依次尝试候选编码器，返回第一个能成功创建的 VideoWriter。

    参数说明：
        dst    : 输出文件路径。
        fps    : 帧率（必须与源视频一致，否则播放速度会变）。
        width  : 画面宽度。
        height : 画面高度。

    返回值：
        (VideoWriter 或 None, 实际使用的编码器名)。
        全部候选都失败时返回 (None, "")。

    零基础提示：
        「编码器」决定视频用什么方式压缩。不同电脑装的编解码器不一样，
        所以这里按 config.VIDEO_CODEC_CANDIDATES 挨个试，谁能用就用谁。
    """
    for name in config.VIDEO_CODEC_CANDIDATES:
        try:
            fourcc = cv2.VideoWriter_fourcc(*name)
            writer = cv2.VideoWriter(dst, fourcc, fps, (width, height))
            if writer is not None and writer.isOpened():
                log.info("视频编码器选定：%s", name)
                return writer, name
            if writer is not None:
                writer.release()
            log.warning("编码器 %s 不可用，尝试下一个。", name)
        except Exception as exc:  # noqa: BLE001
            log.warning("编码器 %s 创建失败：%s", name, exc)
    return None, ""


def _default_out_path(src_path: str, out_path: Optional[str]) -> str:
    """
    计算输出视频路径。

    规则与 PDF 一致：调用方指定就用指定的（自动补扩展名 + 建父目录），
    否则放到 outputs 目录并加 "_已去水印" 后缀、自动避重名。
    """
    ext = config.VIDEO_OUTPUT_EXT if config.VIDEO_OUTPUT_EXT.startswith(".") else f".{config.VIDEO_OUTPUT_EXT}"
    if out_path:
        dst = str(out_path)
        if not _os.path.splitext(dst)[1]:
            dst += ext
        ensure_parent_dir(dst)
        return dst

    stem = _os.path.splitext(_os.path.basename(src_path))[0]
    return str(make_unique_path(config.OUTPUT_DIR, f"{stem}_已去水印{ext}"))


def _mask_is_usable(mask: Optional[np.ndarray], shape: tuple[int, int]) -> bool:
    """
    判断缓存的掩码能否用在当前帧上。

    条件：非空、尺寸与当前帧一致、且确实含有白色区域。
    （视频中途分辨率变化极罕见，但做一次校验可以避免直接崩溃。）
    """
    if mask is None:
        return False
    if mask.shape[:2] != shape:
        return False
    return bool((mask > 0).any())


def _mask_iou(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    """两个掩码的 IoU（0.0~1.0）；任一为空/尺寸不一致时返回 0.0。

    阶段 64：用于「复用掩码的漂移检测」——水印静止时相邻检测帧的掩码 IoU ≈ 0.99，
    水印缓慢漂移时会快速下降，低于 `config.VIDEO_REUSE_MIN_IOU` 即说明旧掩码已失效。
    """
    if a is None or b is None:
        return 0.0
    if a.shape[:2] != b.shape[:2]:
        return 0.0
    A = a > 0
    B = b > 0
    union = int(np.count_nonzero(A | B))
    if union == 0:
        return 0.0
    return int(np.count_nonzero(A & B)) / union


# ==============================================================================
# 第 4 节：主流程 —— 处理整段视频
# ==============================================================================
def _resolve_video_backend(
    real_backend: "object",
    model_path: "Optional[str]",
    model_dir: "Optional[str]",
    enabled: bool,
) -> "Optional[SuperResBackend]":
    """
    阶段 33：视频场景「一次性」探测真实超分后端。

    视频是「拆→修→拼」，若真实放大，输出视频尺寸必须整体放大且所有帧同尺寸，
    否则 VideoWriter 报错。所以只在这里探测一次，并把同一后端实例复用给每一帧
    （避免逐帧重复 import torch / 加载模型的开销）。

    返回可用的 SuperResBackend 实例，或 None（缺 torch/权重，调用方回退轻量超分）。
    """
    if not enabled:
        return None
    if isinstance(real_backend, SuperResBackend):
        return real_backend
    kind = real_backend if isinstance(real_backend, str) and real_backend else config.REAL_SUPERRES_BACKEND
    mp = model_path if model_path is not None else config.REAL_SUPERRES_MODEL_PATH
    md = model_dir if model_dir is not None else config.REAL_SUPERRES_MODEL_DIR
    try:
        return get_superres_backend(kind, model_path=mp, model_dir=md)
    except Exception as exc:  # noqa: BLE001
        log.warning("视频真实超分后端探测失败，回退轻量超分：%s", exc)
        return None


def process_video(
    src_path: str | _os.PathLike,
    out_path: Optional[str | _os.PathLike] = None,
    *,
    frame_step: Optional[int] = None,
    max_frames: Optional[int] = None,
    reuse_mask: Optional[bool] = None,
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    enhance_tiled: Optional[bool] = None,
    search_bottom: Optional[bool] = None,
    postprocess: Optional[bool] = None,
    sharpen: Optional[float] = None,
    poisson_blend: Optional[bool] = None,
    poisson_strength: Optional[float] = None,
    texture_transfer: Optional[bool] = None,
    texture_amount: Optional[float] = None,
    artifact_reduce: Optional[bool] = None,
    artifact_amount: Optional[float] = None,
    auto_preset: bool = True,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[object] = None,
    real_model_path: Optional[str] = None,
    real_model_dir: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    preserve_untouched: Optional[bool] = None,
) -> VideoResult:
    """
    对一段视频逐帧去水印，输出一个新的视频文件（无音轨）。

    参数说明：
        src_path    : 源视频路径。
        out_path    : 结果视频路径；None 表示自动放到 outputs 目录。
        frame_step  : 抽帧步长，每隔几帧重新检测一次水印位置；
                      None 取 config.VIDEO_FRAME_STEP（默认 1，即每帧都检测）。
        max_frames  : 最多处理多少帧；None 取 config.VIDEO_MAX_FRAMES（默认 900）。
                      超出部分不会写入输出，结果里 truncated 会被置为 True。
        reuse_mask  : 是否复用上一次检测到的水印位置；None 取配置默认值。
        engine 等   : 与单图处理一致的修复参数。
        on_progress : 进度回调 (0~1, 中文提示)。

    返回值：
        VideoResult 对象。失败时 status="failed" 且 error 有值，不抛异常。

    每一帧的处理策略：
        · 「检测帧」（序号能被 step 整除）：完整跑检测 + 修复，并缓存掩码；
        · 「中间帧」（开启复用且有可用缓存）：跳过检测，直接用缓存掩码修复；
        · 未检出水印或处理失败的帧：原样写出，保证输出视频不丢帧、不变短。
    """
    on_progress = on_progress or _noop_progress
    started = time.time()
    src = str(src_path)
    result = VideoResult(src_path=src)

    # ---------- 前置校验 ----------
    if not _os.path.isfile(src):
        result.error = f"找不到文件：{src}"
        log.error(result.error)
        return result

    if not is_video_file(src):
        result.error = (
            f"不是受支持的视频格式，当前支持：{', '.join(config.SUPPORTED_VIDEO_FORMATS)}"
        )
        log.error(result.error)
        return result

    if not is_video_available():
        result.error = "当前 OpenCV 缺少视频读写模块，无法处理视频。"
        log.error(result.error)
        return result

    # ---------- 参数回填 ----------
    step = int(config.VIDEO_FRAME_STEP if frame_step is None else frame_step)
    step = max(1, min(step, 60))
    limit = int(config.VIDEO_MAX_FRAMES if max_frames is None else max_frames)
    limit = max(1, limit)
    reuse = bool(config.VIDEO_REUSE_MASK if reuse_mask is None else reuse_mask)

    # 阶段 27：逐帧智能推荐仅对「检测帧」有意义；复用掩码（固定水印）场景下
    # 水印位置固定，整段共用一个预设更合理，故强制关闭推荐。
    detect_auto = bool(auto_preset) and not reuse

    # 步长 > 1 但关掉了复用 —— 中间帧将无掩码可用，等于白白输出带水印的帧。
    # 这里不报错，而是自动把步长拉回 1，并把提醒写进结果，让用户看得见。
    if step > 1 and not reuse:
        result.warnings.append(
            f"抽帧步长 {step} 需要配合「复用掩码」才有意义，已自动改为逐帧检测（步长 1）。"
        )
        log.warning(result.warnings[-1])
        step = 1

    # 阶段 14：批量/视频场景预先选定一次设备并复用，省去逐帧重复探测设备的小开销。
    device = select_device() if config.PERF_REUSE_DEVICE else None

    cap = None
    writer = None
    try:
        # ---------- 打开源视频 ----------
        on_progress(0.01, "正在打开视频……")
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            result.error = "OpenCV 无法打开该视频（编码格式不受支持或文件已损坏）。"
            return result

        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        # 某些视频读不出帧率（返回 0 或异常大），给一个安全默认值。
        if not (0 < fps <= 240):
            result.warnings.append(f"源视频帧率异常（读到 {fps}），输出按 25fps 处理。")
            log.warning(result.warnings[-1])
            fps = 25.0

        if width <= 0 or height <= 0:
            result.error = f"读不到有效的画面尺寸（{width}×{height}）。"
            return result

        result.total_frames = total
        result.fps = round(fps, 3)

        # 阶段 33：真实超分（torch 模型放大）。视频是「拆→修→拼」，若真实放大，
        # 输出视频尺寸必须整体放大且所有帧同尺寸，否则 VideoWriter 报错。
        # 故在此【一次性探测后端】决定输出尺寸，并把同一后端实例复用给每一帧
        # （避免逐帧重复探测，省去重复 import torch / 加载模型的开销）。
        _real_enabled = config.REAL_SUPERRES_ENABLED if real_superres is None else real_superres
        _real_scale = int(config.REAL_SUPERRES_SCALE if real_scale is None else real_scale)
        _real_scale = max(1, min(_real_scale, 8))
        backend = _resolve_video_backend(real_backend, real_model_path, real_model_dir, _real_enabled)
        out_w, out_h = (width * _real_scale, height * _real_scale) if backend is not None else (width, height)
        if _real_enabled and backend is None:
            result.warnings.append(
                "已开启真实超分，但当前环境缺少 torch/权重，已自动回退轻量超分（同分辨率）。"
            )
        result.width = out_w
        result.height = out_h
        result.upscaled = backend is not None
        result.real_scale_used = _real_scale if backend is not None else 0

        plan = min(total, limit) if total > 0 else limit
        log.info(
            "开始处理视频：%s（%d×%d，%.2ffps，总帧 %d，计划处理 %d 帧，步长 %d，复用掩码=%s，真实超分=%s）",
            _os.path.basename(src), width, height, fps, total, plan, step, reuse,
            "是" if backend is not None else "否",
        )

        # ---------- 准备输出 ----------
        dst = _default_out_path(src, str(out_path) if out_path else None)
        writer, codec = _open_writer(dst, fps, out_w, out_h)
        if writer is None:
            result.error = (
                "所有候选视频编码器都无法使用，无法写出视频。"
                f"（已尝试：{', '.join(config.VIDEO_CODEC_CANDIDATES)}）"
            )
            return result
        result.codec = codec

        # ---------- 逐帧循环 ----------
        cached_mask: Optional[np.ndarray] = None
        idx = 0  # 已读取的帧序号（从 0 开始）

        while True:
            ok, frame = cap.read()
            if not ok:
                break  # 读完了（或读到坏帧），正常收尾

            # 阶段 338：`cap.read()` 给的是 OpenCV 的 **BGR**，而 `process_image` 的契约是 **RGB**
            # （本文件 `extract_sampled_frames` 早就是这么转的，见第 210 行）。
            # 以前整段逐帧循环直接把 BGR 喂进管线 ⇒ 视频页与单图页对同一画面用不同的通道权重，
            # 实测同一张图检测像素差 2.4 倍（详见 docs/338）。
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            if idx >= limit:
                result.truncated = True
                log.warning("已达帧数上限 %d，剩余帧不再处理。", limit)
                break

            # —— 进度汇报（按计划帧数折算，避免总帧数为 0 时除零）——
            if plan > 0:
                on_progress(
                    0.02 + 0.95 * min(1.0, idx / plan),
                    f"处理第 {idx + 1}/{plan} 帧……",
                )

            out_frame = frame_rgb  # 默认原样输出，任何异常都不会导致丢帧（**全程 RGB**，写出前才翻 BGR）
            need_detect = (idx % step == 0) or not _mask_is_usable(
                cached_mask, (height, width)
            )

            try:
                if need_detect:
                    # 检测帧：完整跑「检测 + 修复」
                    # 阶段 27：detect_auto 为真时传 preset=None 触发逐帧智能推荐；
                    #          否则用共享 preset（含 reuse 固定水印场景）。
                    core = process_image(
                        frame_rgb,
                        mode="auto",
                        engine=engine,
                        strength=strength,
                        dilate=dilate,
                        feather=feather,
                        threshold=threshold,
                        denoise=denoise,
                        sensitivity=sensitivity,
                        preset=(None if detect_auto else preset),
                        auto_preset=detect_auto,
                        enhance_tiled=enhance_tiled,
                        search_bottom=search_bottom,
                        postprocess=postprocess,
                        sharpen=sharpen,
                        poisson_blend=poisson_blend,
                        poisson_strength=poisson_strength,
                        texture_transfer=texture_transfer,
                        texture_amount=texture_amount,
                        artifact_reduce=artifact_reduce,
                        artifact_amount=artifact_amount,
                        real_superres=_real_enabled,
                        real_scale=_real_scale,
                        real_backend=backend,
                        real_model_path=real_model_path,
                        real_model_dir=real_model_dir,
                        device=device,
                        on_progress=None,
                        preserve_untouched=preserve_untouched,
                    )
                    result.detected_frames += 1
                    result.frame_stats.append(core.get("stats"))  # 阶段 23：收集逐帧命中率
                    # 阶段 27：仅在「逐帧智能推荐」真正生效的检测帧上累计推荐分布；
                    # 复用掩码（固定水印）场景 detect_auto=False，process_image 不会
                    # 计算 recommended_preset，故跳过计数，避免把 None 误记成分布。
                    if detect_auto:
                        _rp = core.get("recommended_preset")
                        if _rp:
                            result.preset_counts[_rp] = result.preset_counts.get(_rp, 0) + 1
                            # 阶段 28：累计推荐置信度，收尾求均值供媒体页展示。
                            _rc = (core.get("recommendation") or {}).get("confidence")
                            if _rc is not None:
                                result._rec_conf_sum = result._rec_conf_sum + float(_rc)
                                result._rec_conf_n += 1
                    if core.get("status") == "success":
                        out_frame = core["result_rgb"]
                        result.repaired_frames += 1
                        if reuse:
                            new_mask = core.get("mask")
                            # 阶段 64：复用掩码的漂移护栏。旧掩码一旦盖在错误位置，
                            # 真值水印区会留下明显残留（实测 13px 漂移时残留是逐帧
                            # 检测的 2.8~3.3 倍）。此处比较新旧掩码 IoU，低于阈值即
                            # 自动改回逐帧检测（step=1），并把原因写进告警让用户看得见。
                            if (step > 1 and cached_mask is not None
                                    and new_mask is not None):
                                drift_iou = _mask_iou(cached_mask, new_mask)
                                if drift_iou < config.VIDEO_REUSE_MIN_IOU:
                                    step = 1
                                    result.warnings.append(
                                        "检测到水印位置漂移（掩码重合度 "
                                        f"{drift_iou * 100:.0f}% < "
                                        f"{config.VIDEO_REUSE_MIN_IOU * 100:.0f}%），"
                                        "已自动改为逐帧检测，避免旧掩码残留水印。"
                                    )
                                    log.warning(result.warnings[-1])
                            cached_mask = new_mask
                    else:
                        result.clean_frames += 1
                        # 没检出水印就把缓存清掉，避免把旧位置误用到新画面上
                        cached_mask = None
                else:
                    # 中间帧：直接拿缓存掩码修复，省掉检测耗时
                    core = process_image(
                        frame_rgb,
                        mask=cached_mask,
                        mode="manual",
                        engine=engine,
                        strength=strength,
                        dilate=dilate,
                        feather=feather,
                        threshold=threshold,
                        denoise=denoise,
                        postprocess=postprocess,
                        sharpen=sharpen,
                        poisson_blend=poisson_blend,
                        poisson_strength=poisson_strength,
                        texture_transfer=texture_transfer,
                        texture_amount=texture_amount,
                        artifact_reduce=artifact_reduce,
                        artifact_amount=artifact_amount,
                        real_superres=_real_enabled,
                        real_scale=_real_scale,
                        real_backend=backend,
                        real_model_path=real_model_path,
                        real_model_dir=real_model_dir,
                        device=device,
                        on_progress=None,
                        preserve_untouched=preserve_untouched,
                    )
                    result.reused_frames += 1
                    result.frame_stats.append(core.get("stats"))  # 阶段 23：收集逐帧命中率（manual 为 None）
                    if core.get("status") == "success":
                        out_frame = core["result_rgb"]
                        result.repaired_frames += 1
                    else:
                        result.clean_frames += 1

            except Exception as exc:  # noqa: BLE001 —— 单帧异常绝不能中断整段视频
                result.failed_frames += 1
                if config.VIDEO_VERBOSE_FRAME_LOG:
                    log.exception("第 %d 帧处理异常", idx + 1)
                else:
                    log.warning("第 %d 帧处理异常（原样输出）：%s", idx + 1, exc)

            # 尺寸兜底：真实超分会改变画布尺寸。输出视频所有帧必须同尺寸，
            # 故兜底目标统一为 (out_w, out_h)：放大场景下原样/失败帧也放大到同尺寸；
            # 非放大场景 out_w/out_h == 源尺寸，行为与旧版一致。
            if out_frame.shape[0] != out_h or out_frame.shape[1] != out_w:
                out_frame = cv2.resize(out_frame, (out_w, out_h))

            # 阶段 338：管线内部一律 RGB，而 `cv2.VideoWriter` 按 BGR 解释数组 ⇒
            # 这是本轮唯一一次翻回去（以前整条循环拿着 BGR 当 RGB 用，两处错互相抵消，
            # 颜色看着正常、检测权重一直是反的）。
            writer.write(cv2.cvtColor(out_frame, cv2.COLOR_RGB2BGR))
            result.written_frames += 1

            if config.VIDEO_VERBOSE_FRAME_LOG:
                log.info(
                    "第 %d 帧写出（%s）", idx + 1, "检测" if need_detect else "复用掩码"
                )

            idx += 1

        # ---------- 收尾 ----------
        if result.written_frames == 0:
            result.error = "一帧都没能读出来，视频可能已损坏或编码不受支持。"
            return result

        # 必须先释放 writer，文件头才会被正确写入，否则得到的是 0 字节文件。
        writer.release()
        writer = None

        if not _os.path.isfile(dst) or _os.path.getsize(dst) == 0:
            result.error = "视频写出后文件为空，可能是编码器不兼容当前分辨率。"
            return result

        # 阶段 339：**「读到坏帧就 break」不等于「读完了」**。
        # 以前只有帧数上限那一支会置 `truncated`，解码中途停止时（VBR 的 mp4/mkv 很常见，
        # `CAP_PROP_FRAME_COUNT` 常常虚高）写出 210/900 帧也会一路报到「✅ 处理完成」+ 100% 进度，
        # `truncated=False`、`warnings` 里一个字没有 ⇒ 用户以为整段都修好了，实际后 3/4 原样都没动。
        _total = int(result.total_frames or 0)
        if not result.truncated and _total > 0 and result.written_frames < _total:
            result.truncated = True
            result.warnings.append(
                f"只写出 {result.written_frames}/{_total} 帧：解码在中途停止"
                "（源视频可能有坏帧，或帧数元数据不准）。未写出的部分不会出现在结果视频里。"
            )
            log.warning("视频提前结束：%s", result.warnings[-1])

        result.out_path = dst
        result.status = "success"
        # 阶段 28：收尾计算逐检测帧智能推荐的平均置信度。
        if result._rec_conf_n > 0:
            result.recommend_conf_avg = result._rec_conf_sum / result._rec_conf_n
        result.seconds = time.time() - started
        result.warnings.append("输出视频不含音轨（OpenCV 不处理音频），如需原声请用 ffmpeg 合并。")
        on_progress(1.0, "视频处理完成")
        log.info("视频处理完成：%s（%s）", dst, result.summary())
        return result

    except Exception as exc:  # noqa: BLE001
        log.exception("处理视频时发生异常")
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        result.seconds = time.time() - started
        return result

    finally:
        # 释放资源，避免 Windows 上文件被占用。
        for handle in (writer, cap):
            try:
                if handle is not None:
                    handle.release()
            except Exception:  # noqa: BLE001
                pass


# ==============================================================================
# 第 5 节：辅助 —— 生成 ffmpeg 合并音轨的命令（给用户直接复制）
# ==============================================================================
def build_audio_merge_command(src_video: str, processed_video: str, final_out: str) -> str:
    """
    生成一条可直接复制粘贴的 ffmpeg 命令，把原视频的声音合并回处理后的画面。

    参数说明：
        src_video       : 原始视频（提供音轨）。
        processed_video : 本工具处理后的无声视频（提供画面）。
        final_out       : 合并后的最终输出路径。

    返回值：
        命令行字符串。需要用户自行安装 ffmpeg（https://ffmpeg.org）。
    """
    return (
        f'ffmpeg -i "{processed_video}" -i "{src_video}" '
        f'-c:v copy -c:a aac -map 0:v:0 -map 1:a:0 -shortest "{final_out}"'
    )


# ==============================================================================
# 第 6 节：模块自测入口
# ------------------------------------------------------------------------------
# 直接运行 python services/video_service.py 会合成一段带水印的测试视频，
# 走完整流程处理一遍，并校验输出视频的帧数、尺寸、帧率是否与源一致。
# ==============================================================================
if __name__ == "__main__":
    # 阶段 333：本文件的 `__main__` 自检会打印 ⇒/✅/⚠ 这类 GBK 编不出的字符，
    # 中文 Windows 控制台下会把「自检通过」读成 UnicodeEncodeError + 退出码 1。
    from utils.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    print("=" * 70)
    print("视频服务自检")
    print("=" * 70)

    import tempfile

    print(f"[1] 视频能力可用性：{is_video_available()}")
    assert is_video_available(), "OpenCV 视频模块不可用"

    tmp_dir = tempfile.mkdtemp(prefix="video_selftest_")
    test_mp4 = _os.path.join(tmp_dir, "测试视频.mp4")

    # ---- 合成一段 12 帧的测试视频：渐变背景 + 右下角固定白色水印 ----
    W, H, FPS, N = 320, 240, 10.0, 12
    _w, _codec = _open_writer(test_mp4, FPS, W, H)
    assert _w is not None, "测试视频无法创建（编码器不可用）"
    for i in range(N):
        canvas = np.full((H, W, 3), 40 + i * 8, dtype=np.uint8)
        # 画点内容，让画面不是纯色
        cv2.circle(canvas, (60 + i * 15, 120), 25, (200, 120, 60), -1)
        # 右下角固定水印（高对比白字）
        cv2.putText(canvas, "MARK", (215, 225), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2)
        _w.write(canvas)
    _w.release()
    print(f"[2] 已生成测试视频：{test_mp4}（编码器 {_codec}）")

    info = get_video_info(test_mp4)
    print(f"[3] 视频信息：{info}")
    assert info["ok"], f"视频信息读取失败：{info['error']}"
    assert info["width"] == W and info["height"] == H, "尺寸读取不对"

    # ---- 用例 A：逐帧检测 ----
    out_a = _os.path.join(tmp_dir, "结果_逐帧.mp4")
    steps: list[str] = []
    res_a = process_video(
        test_mp4, out_a, frame_step=1,
        on_progress=lambda p, m: steps.append(f"{p:.0%} {m}"),
    )
    print(f"[4] 用例A（逐帧检测）：{res_a.status}")
    print(f"    {res_a.summary()}")
    assert res_a.status == "success", f"处理失败：{res_a.error}"
    assert res_a.written_frames == N, f"帧数不符：写出 {res_a.written_frames}，应为 {N}"
    assert res_a.detected_frames == N, "逐帧模式下检测次数应等于帧数"

    chk_a = get_video_info(res_a.out_path)
    print(f"[5] 输出视频信息：{chk_a}")
    assert chk_a["ok"] and chk_a["width"] == W and chk_a["height"] == H, "输出尺寸不一致"
    assert abs(chk_a["fps"] - FPS) < 0.5, "输出帧率与源不一致"

    # ---- 用例 B：步长 3 + 复用掩码（应显著减少检测次数）----
    out_b = _os.path.join(tmp_dir, "结果_复用.mp4")
    res_b = process_video(test_mp4, out_b, frame_step=3, reuse_mask=True)
    print(f"[6] 用例B（步长3+复用）：{res_b.status}")
    print(f"    {res_b.summary()}")
    assert res_b.status == "success", f"处理失败：{res_b.error}"
    assert res_b.written_frames == N, "复用模式下也必须写出全部帧"
    assert res_b.detected_frames < N, "复用模式的检测次数应少于总帧数"
    print(f"    检测 {res_b.detected_frames} 次 vs 逐帧 {res_a.detected_frames} 次 → 提速有效")

    # ---- 用例 C：帧数上限截断 ----
    out_c = _os.path.join(tmp_dir, "结果_截断.mp4")
    res_c = process_video(test_mp4, out_c, max_frames=5)
    print(f"[7] 用例C（上限5帧）：写出 {res_c.written_frames} 帧，截断={res_c.truncated}")
    assert res_c.status == "success" and res_c.written_frames == 5, "帧数上限未生效"
    assert res_c.truncated, "应标记为已截断"

    # ---- 用例 D：矛盾参数自动纠正（步长>1 但关闭复用）----
    out_d = _os.path.join(tmp_dir, "结果_纠正.mp4")
    res_d = process_video(test_mp4, out_d, frame_step=4, reuse_mask=False, max_frames=4)
    print(f"[8] 用例D（矛盾参数）：warnings={res_d.warnings[:1]}")
    assert res_d.status == "success", f"处理失败：{res_d.error}"
    assert any("步长" in w for w in res_d.warnings), "矛盾参数应给出提醒"
    assert res_d.detected_frames == res_d.written_frames, "纠正后应逐帧检测"

    # ---- 用例 E：异常路径 ----
    bad = process_video(_os.path.join(tmp_dir, "不存在.mp4"))
    assert bad.status == "failed" and bad.error, "不存在的文件应返回失败"
    print(f"[9] 异常路径校验通过：{bad.error}")

    # ---- 用例 F：真实超分上行链路（沙箱无 torch，用假后端模拟放大 2×）----
    try:
        from core.superres import SuperResBackend

        class _FakeBackend(SuperResBackend):
            name = "fake"

            def __init__(self, *a, **k):
                super().__init__()
                self.used_real = True
                self.note = "fake 2x"

            def upscale(self, arr, scale):
                h, w = arr.shape[:2]
                return cv2.resize(arr, (w * 2, h * 2)), True, "fake 2x"

        _save = get_superres_backend
        globals()["get_superres_backend"] = lambda *a, **k: _FakeBackend()
        out_f = _os.path.join(tmp_dir, "结果_超分.mp4")
        res_f = process_video(
            test_mp4, out_f, frame_step=1, real_superres=True, real_scale=2, max_frames=4,
        )
        globals()["get_superres_backend"] = _save
        print(f"[10] 用例F（真实超分上行）：{res_f.status} upscaled={res_f.upscaled}")
        assert res_f.status == "success", f"处理失败：{res_f.error}"
        assert res_f.upscaled, "应标记为已放大"
        assert res_f.real_scale_used == 2, "放大倍数应为 2"
        chk_f = get_video_info(res_f.out_path)
        assert chk_f["ok"], "输出视频读取失败"
        assert chk_f["width"] == W * 2 and chk_f["height"] == H * 2, "输出应为 2× 尺寸"
        print(f"     输出尺寸 {chk_f['width']}×{chk_f['height']}（源 {W}×{H}）✅")
    except Exception as exc:  # noqa: BLE001
        print(f"[10] 用例F 执行异常（不影响其他用例）：{exc}")

    # ---- ffmpeg 命令生成 ----
    cmd = build_audio_merge_command(test_mp4, res_a.out_path, _os.path.join(tmp_dir, "带声音.mp4"))
    print(f"[10] 音轨合并命令示例：\n     {cmd}")

    print("=" * 70)
    print("视频服务自检全部通过 ✅")
    print(f"输出文件：{res_a.out_path}")
    print("=" * 70)
