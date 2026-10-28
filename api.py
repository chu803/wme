# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : api.py
 文件说明 : 程序化调用库（阶段 9 拓展功能，顶层模块）。

            把「自动检测 + 修复」封装成一行就能调用的函数，让本工具既能当
            网页用、又能当【Python 库】嵌进你自己的脚本 / 服务里：

                from api import remove_watermark, remove_watermark_bytes

                # 处理一张本地图片（阶段 233 起：全自动擦除须显式允许，见下）
                res = remove_watermark("原图.jpg", dst="结果.png", evaluate=True,
                                       allow_auto_erase=True)
                print(res.status, res.out_path, res.quality)

                # 处理内存里的字节（例如从网络收到的图片）
                out_bytes, qr = remove_watermark_bytes(image_bytes, fmt="PNG",
                                                       allow_auto_erase=True)

                # ⚠ 阶段 233：不传 allow_auto_erase 且未指定 preset 时，
                #   会抛 AutoEraseNotConfirmed —— 检测器是「召回优先」设计
                #   （红线有意容忍高误检），在**文字密集的插画/封面**上
                #   「自动选预设 + 直接擦除」可能大面积误检。请任选其一：
                #     · 显式指定 preset（如 preset="ai_logo"）；或
                #     · 显式传 allow_auto_erase=True 承担全自动风险；或
                #     · 先 detect_watermark_bytes() 预览掩码、确认后再传 mask。
                #   GUI 不受影响（本就是「预览→确认→擦除」流程）。
                #   恢复旧行为：config.API_REQUIRE_AUTO_ERASE_CONFIRM=False。

                # 处理 numpy 数组（例如从摄像头 / 视频帧读到的帧）
                out_arr, qr = remove_watermark_array(frame_bgr)

                # 阶段 10：整份 PDF / 整段视频也能一行搞定
                from api import remove_watermark_pdf, remove_watermark_video
                print(remove_watermark_pdf("合同.pdf", pages="1-3").summary())
                print(remove_watermark_video("片段.mp4", frame_step=3).summary())

            设计约束（与项目分层铁律一致）：
            · 本模块只调用 services / core / utils / config，不依赖 gradio；
            · 文件读写交给 process_file，内存编解码交给本模块的字节/数组接口，
              核心「检测 + 修复」统一复用 services.auto_pipeline.process_image，
              保证 GUI / CLI / 库 / REST 四套入口行为完全一致。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import shutil
import base64
import tempfile
from dataclasses import dataclass, field, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    # 仅供类型检查器识别返回类型，运行时不会真的导入，
    # 这样即使环境里没装 PyMuPDF，导入 api 模块也不会报错。
    from services.pdf_service import PdfResult
    from services.video_service import VideoResult

# ------------------------------------------------------------------------------
# 路径加固：保证无论从哪个目录启动都能 import 到项目根模块。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import cv2  # 图像处理硬依赖，整个项目都需要它。

import config
from services.auto_pipeline import process_file, process_image
from services.quality_service import evaluate_quality, QualityReport
from core.model_loader import get_model_info
from core.image_utils import save_image
from core.detector import detect_watermark_mask
from core.inpainter import inpaint
from utils.path_helper import make_unique_path
from utils.logger import get_logger

log = get_logger("api")


# ==============================================================================
# 第 1 节：结果数据类
# ==============================================================================
@dataclass
class RemoveResult:
    """单张图处理结果的统一数据结构（四入口共用的返回形态）。"""

    original_name: str = ""          # 原图文件名
    out_path: Optional[str] = None   # 结果图路径（仅 success 有）
    status: str = "failed"           # success / skipped / failed
    mask_pixels: int = 0             # 掩码像素数
    regions: int = 0                 # 检测到的水印区域数
    used_ocr: bool = False            # 是否启用了 OCR
    engine: str = ""                 # 实际使用的修复引擎
    error: Optional[str] = None      # 失败 / 跳过原因
    quality: Optional[QualityReport] = field(default=None)  # 质量评估（evaluate=True 才有）
    # 注意：显式请求的增强被部署闸静默跳过等情况下的可观测信号（Stage 125 新增）。
    # 默认空列表，向后兼容；调用方应检查本字段以发现「请求了但没生效」的情形。
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        base = f"RemoveResult(status={self.status}, name={self.original_name})"
        if self.status == "success":
            extra = f" -> {self.out_path}"
            if self.quality is not None:
                extra += f" [质量分 {self.quality.score} · {self.quality.level}]"
            return base + extra
        return base + f" ({self.error})"


# ==============================================================================
# 第 1.5 节：全自动擦除的确认门（阶段 233）
# ==============================================================================
class AutoEraseNotConfirmed(RuntimeError):
    """**「系统替用户选预设 + 直接擦除」这条静默路径被拒绝**时抛出（阶段 233）。

    为什么是异常而不是「返回原图 / 返回状态码」：
        本门的目的是**绝不能被静默忽略**。若返回原图，调用方会把「没擦」误当成
        「擦干净了」；若只加一个 `status` 字段，旧调用方不看也会静默通过。
        抛异常则**无法忽略**，且异常消息直接给出三条可行出路。

    背景（阶段 232 审计结论）：检测器是**召回优先**设计（红线 precision ≥ 0.050，
    有意容忍 ≥95% 误检）。在**文字密集的插画/封面**上会大面积误检
    （实测某用户封面 21 区域中 15 个 translucent **全是误检**）。
    三条「改进检测器」的路径先后被数据否决（像素判据不可分 / 区域数不是信号 /
    收紧会撞红线）⇒ 定性为**产品预期不符**，故在**产品层**要求显式确认。

    三条出路：
        ① 显式指定 `preset="ai_logo"` 等——调用方自己做了选择，即视为已确认；
        ② 显式传 `allow_auto_erase=True`——明确承担全自动风险；
        ③ 先调 `api.detect_watermark_bytes()` 预览掩码，人工确认后再传 `mask` 修复。

    可用 `config.API_REQUIRE_AUTO_ERASE_CONFIRM=False`（或环境变量
    `WME_API_REQUIRE_AUTO_ERASE_CONFIRM=0`）恢复旧的全自动静默行为。
    """

    def __init__(self, *, entry: str, hint: str = "") -> None:
        self.entry = entry
        self.hint = hint
        super().__init__(
            f"已拒绝「自动选预设 + 直接擦除」（入口 {entry}）：该路径在文字密集的"
            f"插画/封面上可能大面积误检（检测器为召回优先，有意容忍高误检）。\n"
            f"请任选其一：\n"
            f"  · 显式指定 preset（如 preset=\"ai_logo\"），或\n"
            f"  · 显式传 allow_auto_erase=True 承担全自动风险，或\n"
            f"  · 先调 detect_watermark_bytes() 预览掩码、确认后再传 mask。\n"
            f"如需恢复旧的全自动行为：config.API_REQUIRE_AUTO_ERASE_CONFIRM=False"
            f"（或 WME_API_REQUIRE_AUTO_ERASE_CONFIRM=0）。"
            + (f"\n{hint}" if hint else "")
        )


def _guard_auto_erase(
    *,
    entry: str,
    preset: Optional[str],
    auto_preset: bool,
    allow_auto_erase: bool,
) -> None:
    """确认门判定（纯函数式，便于单测；不抛则放行）。

    放行条件（任一满足即可，逻辑上互斥）：
      · 配置关闭了该门（`API_REQUIRE_AUTO_ERASE_CONFIRM=False`）；
      · 调用方**显式指定**了 preset（`preset is not None`）——已做出选择；
      · 调用方**显式允许**（`allow_auto_erase=True`）；
      · 未启用 `auto_preset`（系统不会替调用方选预设 ⇒ 不构成「静默」）。

    拒绝条件：以上皆非 —— 即「调用方没指定预设、又让系统自动选、还没显式允许」。
    """
    from config import API_REQUIRE_AUTO_ERASE_CONFIRM  # 局部取，便于测试 monkeypatch

    if not API_REQUIRE_AUTO_ERASE_CONFIRM:
        return
    if preset is not None:
        return
    if allow_auto_erase:
        return
    if not auto_preset:
        return
    raise AutoEraseNotConfirmed(entry=entry)


def detect_auto_erase_required(
    *,
    preset: Optional[str],
    auto_preset: bool = True,
    allow_auto_erase: bool = False,
) -> bool:
    """**只查询不抛**：当前参数组合是否会被确认门拒绝。

    供 CLI / REST / 脚本在**动手前**探测，提前给出提示而不必等异常。
    """
    try:
        _guard_auto_erase(entry="probe", preset=preset, auto_preset=auto_preset,
                          allow_auto_erase=allow_auto_erase)
        return False
    except AutoEraseNotConfirmed:
        return True


# ==============================================================================
# 第 2 节：内部小工具
# ==============================================================================
def _encode_bytes(rgb: np.ndarray, fmt: Optional[str], quality: Optional[int]) -> bytes:
    """
    把 RGB 数组编码成图片字节。
        · PNG / JPG / WEBP 走 cv2.imencode（快、内存内完成）；
        · TIFF / AVIF 等 cv2 不一定支持的格式，落到 save_image 临时文件再读回，
          保证任何格式都能返回字节，不抛编码异常。
    """
    fmt = (fmt or config.API_DEFAULT_FMT).upper()
    if fmt in ("JPG", "JPEG"):
        ext = ".jpg"
        q = quality if quality is not None else config.DEFAULT_OUTPUT_QUALITY
        params = [cv2.IMWRITE_JPEG_QUALITY, int(q)]
    elif fmt == "WEBP":
        ext = ".webp"
        q = quality if quality is not None else config.DEFAULT_OUTPUT_QUALITY
        params = [cv2.IMWRITE_WEBP_QUALITY, int(q)]
    elif fmt == "PNG":
        ext = ".png"
        params = []
    else:
        # TIFF / AVIF：cv2.imencode 大多不支持，委托 save_image 落盘再读回。
        d = tempfile.mkdtemp(prefix="wme_api_")
        p = make_unique_path(d, "api_out." + fmt.lower())
        save_image(p, rgb, fmt=fmt, quality=quality)
        with open(p, "rb") as f:
            data = f.read()
        try:
            _os.remove(p)
            _os.rmdir(d)
        except OSError:
            pass
        return data

    # 阶段 353：本函数入参是 **RGB**（auto_pipeline 契约 + 本函数 docstring），
    # 而 `cv2.imencode` 按 **BGR** 解释数组 ⇒ 原样交给它会**红蓝互换**
    # （实测：纯红 RGB → PNG/JPG 解码出来是蓝；TIFF/AVIF 分支走 save_image 反而正确）。
    ok, buf = cv2.imencode(ext, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), params)
    if not ok:
        raise RuntimeError(f"图片编码失败：{fmt}")
    return buf.tobytes()


def _evaluate_arrays(orig_rgb: np.ndarray, result_rgb: np.ndarray) -> QualityReport:
    """内存里直接比较「原图数组」与「结果数组」的质量（无需真实文件路径）。"""
    d = tempfile.mkdtemp(prefix="wme_q_")
    p0 = make_unique_path(d, "orig.png")
    p1 = make_unique_path(d, "result.png")
    save_image(p0, orig_rgb, fmt="PNG")
    save_image(p1, result_rgb, fmt="PNG")
    qr = evaluate_quality(p0, p1)
    try:
        _os.remove(p0)
        _os.remove(p1)
        _os.rmdir(d)
    except OSError:
        pass
    return qr


def _move_file(src: str, dst: str) -> None:
    """
    把文件从 src 移到 dst，兼容「跨磁盘驱动器」场景（os.replace 在跨盘时会失败）。
    若 dst 已存在则先删除，等价于覆盖。目标目录会被自动创建。

    鲁棒性说明：在个别受限环境（如安全沙箱会拦截文件删除）中，
    shutil.move 的「复制后删除源文件」一步可能被拦截并抛异常。
    此时退化为「复制 + 尽力删除源」，删除失败也不影响结果文件已就位的事实，
    保证批处理流程在受限环境下依旧可用。
    """
    src = str(src)
    dst = str(dst)
    _os.makedirs(_os.path.dirname(_os.path.abspath(dst)), exist_ok=True)
    if _os.path.exists(dst):
        try:
            _os.remove(dst)
        except OSError:
            pass
    try:
        shutil.move(src, dst)
    except Exception:  # noqa: BLE001
        # 兜底：复制后尽力删除源（删除被环境拦截时忽略）。
        shutil.copy2(src, dst)
        try:
            _os.remove(src)
        except OSError:
            pass


def _list_engines_safe() -> list[dict]:
    """安全获取引擎清单（含插件），失败返回空列表，绝不影响主流程。"""
    try:
        from core import plugins as _pg

        return _pg.get_engine_detail()
    except Exception:  # noqa: BLE001
        return []


def _plugin_report_safe() -> dict:
    """安全获取插件框架状态（阶段 77），失败返回最小结构，绝不影响主流程。

    用途：集成方（脚本 / 运维 / 前端）可据此判断「哪个插件加载失败了、为什么」，
    不必去翻日志。文件路径只暴露文件名，避免泄露本机目录结构。
    """
    fallback = {
        "count": 0, "allow_override": False,
        "loaded": [], "failed": [], "skipped": [], "overridden": [],
    }
    try:
        from core import plugins as _pg

        rep = _pg.get_plugin_report()
        return {
            "count": sum(1 for e in rep.get("engines", []) if not e.get("builtin")),
            "allow_override": bool(getattr(config, "PLUGIN_ALLOW_OVERRIDE", False)),
            "loaded": [_os.path.basename(str(p)) for p in rep.get("loaded", [])],
            "failed": [
                {"file": _os.path.basename(str(i.get("file", ""))), "error": str(i.get("error", ""))}
                for i in rep.get("failed", [])
            ],
            "skipped": [_os.path.basename(str(i.get("file", ""))) for i in rep.get("skipped", [])],
            "overridden": list(rep.get("overridden", [])),
        }
    except Exception:  # noqa: BLE001
        return fallback


def _list_models_safe() -> list[dict]:
    """安全获取模型槽清单，失败返回空列表，绝不影响主流程。"""
    try:
        from core import model_manager as _mm

        return _mm.list_models()
    except Exception:  # noqa: BLE001
        return []


def get_capabilities() -> dict:
    """
    返回当前运行环境的能力快照，供 REST /health 端点或你自己的程序查询。

    包含：应用名/版本、设备（cuda/cpu）、LaMa 模型是否可用、支持的输出格式。
    """
    info = get_model_info()  # dict: {device, model_available, model_path, ...}

    # 阶段 10：探测 PDF / 视频这两种「更多输入源」是否可用。
    # 探测过程本身不能抛异常，否则 /health 端点会挂掉。
    try:
        from services.pdf_service import is_pdf_available
        pdf_ok = bool(is_pdf_available())
    except Exception:  # noqa: BLE001
        pdf_ok = False
    try:
        from services.video_service import is_video_available
        video_ok = bool(is_video_available())
    except Exception:  # noqa: BLE001
        video_ok = False

    # ---- 阶段 20 新增：AI 水印检测预设（四入口能力快照）----
    try:
        from core.detection_presets import list_presets
        detect_presets = [
            {"name": p.name, "display": p.display, "description": p.description}
            for p in list_presets()
        ]
    except Exception:  # noqa: BLE001
        detect_presets = []

    return {
        "app_name": getattr(config, "APP_NAME", "AI 智能水印擦除工作台"),
        "app_version": getattr(config, "APP_VERSION", "1.0.0"),
        "device": info.get("device", "cpu"),
        "lama_available": bool(info.get("model_available", False)),
        "output_formats": list(config.SUPPORTED_OUTPUT_FORMATS),
        "api_default_fmt": config.API_DEFAULT_FMT,
        "engine_default": config.INPAINT_ENGINE,
        # ---- 阶段 10 新增字段 ----
        "pdf_available": pdf_ok,
        "video_available": video_ok,
        "input_formats": list(config.SUPPORTED_INPUT_FORMATS),
        "doc_formats": list(config.SUPPORTED_DOC_FORMATS),
        "video_formats": list(config.SUPPORTED_VIDEO_FORMATS),
        "pdf_render_dpi": config.PDF_RENDER_DPI,
        "pdf_max_pages": config.PDF_MAX_PAGES,
        "video_max_frames": config.VIDEO_MAX_FRAMES,
        # ---- 阶段 11 新增：可用修复引擎（含插件）----
        "engines": _list_engines_safe(),
        # ---- 阶段 77 新增：插件框架状态（覆盖保护 / 加载成败可观测）----
        "plugins": _plugin_report_safe(),
        # ---- 阶段 20 新增：AI 水印检测预设 ----
        "detect_presets": detect_presets,
        # ---- 阶段 21 新增：结果画质增强深化（后处理三道 pass 开关）----
        "postprocess_poisson_blend": bool(getattr(config, "POSTPROCESS_POISSON_BLEND", False)),
        "postprocess_poisson_blend_strength": float(getattr(config, "POSTPROCESS_POISSON_BLEND_STRENGTH", 1.0)),
        "postprocess_texture_transfer": bool(getattr(config, "POSTPROCESS_TEXTURE_TRANSFER", False)),
        "postprocess_texture_transfer_amount": float(getattr(config, "POSTPROCESS_TEXTURE_TRANSFER_AMOUNT", 0.5)),
        "postprocess_artifact_reduce": bool(getattr(config, "POSTPROCESS_ARTIFACT_REDUCE", False)),
        "postprocess_artifact_reduce_amount": float(getattr(config, "POSTPROCESS_ARTIFACT_REDUCE_AMOUNT", 0.5)),
        # ---- 阶段 318b：只动水印区（掩码+带之外逐字节还原）----
        "postprocess_preserve_untouched": bool(
            getattr(config, "POSTPROCESS_PRESERVE_UNTOUCHED", False)),
        "postprocess_preserve_band_px": int(
            getattr(config, "POSTPROCESS_PRESERVE_BAND_PX", 8)),
        # ---- 阶段 12 新增：模型管理（多权重切换 / 校验）----
        "active_model": _get_active_model_safe(),
        "models": _list_models_safe(),
        # ---- 阶段 76 新增：处理偏好与鲁棒性能力快照 ----
        #   用途：REST 客户端可「先问后调」——不必读源码即可知道当前进程的默认
        #   偏好与可用能力（与设置页 / 环境变量 / user_overrides 同源）。
        "mask_dilate_default": int(getattr(config, "MASK_DILATE_DEFAULT", 0)),
        "mask_feather_default": int(getattr(config, "MASK_FEATHER_DEFAULT", 0)),
        "inpaint_auto_prefer": str(getattr(config, "INPAINT_AUTO_PREFER", "opencv")),
        "dual_run_default": bool(getattr(config, "INPAINT_DUAL_RUN", False)),
        "dual_run_secondary": str(getattr(config, "INPAINT_DUAL_SECONDARY", "lama")),
        "output_alpha_formats": list(getattr(config, "OUTPUT_ALPHA_FORMATS", ())),
        "output_flatten_bg_color": list(
            getattr(config, "OUTPUT_FLATTEN_BG_COLOR", (255, 255, 255))),
        "keep_alpha_default": bool(getattr(config, "PNG_KEEP_ALPHA", True)),
        # 阶段 320c：输出格式跟随输入（实测 PNG 4.65× 输入 → JPG 1.13× / WebP 1.00×）
        "output_fmt_follow_input": bool(getattr(config, "OUTPUT_FMT_FOLLOW_INPUT", False)),
        "exif_orientation_applied": bool(
            getattr(config, "IMAGE_APPLY_EXIF_ORIENTATION", True)),
        "bit_depth_normalized": bool(getattr(config, "IMAGE_NORMALIZE_BIT_DEPTH", True)),
        "icc_preserved_on_save": True,
        "lowres_rescue": bool(getattr(config, "DETECT_LOWRES_RESCUE", False)),
        "lowres_rescue_side": int(getattr(config, "DETECT_LOWRES_RESCUE_SIDE", 640)),
        "detect_min_area_ref": int(getattr(config, "DETECT_MIN_AREA_REF", 640)),
        "optional_enhance_gates": {
            "poisson_blend": bool(getattr(config, "POSTPROCESS_POISSON_BLEND", False))
            and bool(getattr(config, "POSTPROCESS_POISSON_ALLOW", False)),
            "texture_transfer": bool(
                getattr(config, "POSTPROCESS_TEXTURE_TRANSFER", False))
            and bool(getattr(config, "POSTPROCESS_TEXTURE_ALLOW", False)),
            "artifact_reduce": bool(
                getattr(config, "POSTPROCESS_ARTIFACT_REDUCE", False)),
        },
        "user_overrides_path": getattr(config, "USER_OVERRIDES_PATH", ""),
    }


def _get_active_model_safe() -> str:
    """安全获取当前模型槽名称，失败返回空串。"""
    try:
        from core import model_manager as _mm

        return _mm.get_active_model()
    except Exception:  # noqa: BLE001
        return ""


# ==============================================================================
# 第 2.x 节：模型管理公开函数（阶段 12 新增）
# ------------------------------------------------------------------------------
# 说明：与 CLI/界面一致的「模型管理」程序化入口。所有函数对异常做兜底，
#       不会让调用方因模型问题而崩溃。
# ==============================================================================
def list_models() -> list[dict]:
    """返回全部模型槽信息（含内置/自动发现/配置登记）。"""
    return _list_models_safe()


def get_active_model() -> str:
    """返回当前模型槽名称。"""
    return _get_active_model_safe()


def verify_model(name: str | None = None) -> dict:
    """
    校验指定（或当前）模型权重完整性。

    参数说明：
        name : 模型槽名称；None 表示当前 active 槽。

    返回值：
        校验结果字典（含 ok / reason / size_mb 等）。
        键集合与 core.model_manager.verify_model 完全一致——包括异常兜底分支，
        否则调用方会在「槽不存在」这种最普通的失败上遇到 KeyError。
    """
    try:
        from core import model_manager as _mm

        return _mm.verify_model(name)
    except Exception as exc:  # noqa: BLE001
        reason = f"校验异常：{exc}"
        try:
            # 复用 model_manager 的空白结果，保持「键集合唯一来源」。
            from core import model_manager as _mm

            return _mm.blank_verify_result(name, reason)
        except Exception:  # noqa: BLE001
            return {
                "name": name or "", "exists": False, "size_mb": 0.0,
                "expected_mb": 0.0, "size_ok": False, "sha_ok": False,
                "sha_error": "", "ok": False, "reason": reason,
            }


def set_active_model(name: str) -> bool:
    """
    切换当前模型槽（LaMa 引擎将使用其权重）。

    参数说明：
        name : 目标模型槽名称。

    返回值：
        True 表示切换成功；False 表示槽不存在。
    """
    try:
        from core import model_manager as _mm

        return _mm.set_active_model(name)
    except Exception:  # noqa: BLE001
        return False


def download_model(name: str | None = None, progress_cb=None) -> str:
    """
    下载指定（或当前）模型权重，带备份 + 校验 + 失败回滚。

    参数说明：
        name       : 模型槽名称；None 表示当前 active 槽。
        progress_cb: 进度回调 (downloaded, total, speed_kbps)。

    返回值：
        下载完成的权重文件绝对路径。

    异常：
        下载失败时抛出（来自 core.model_manager）。
    """
    from core import model_manager as _mm

    return _mm.download_model(name, progress_cb=progress_cb)


# ==============================================================================
# 第 3 节：对外公开函数
# ==============================================================================
def remove_watermark(
    src: str | _os.PathLike,
    *,
    dst: Optional[str | _os.PathLike] = None,
    mode: str = "auto",
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    evaluate: bool = False,
    poisson_blend: Optional[bool] = None,
    poisson_strength: Optional[float] = None,
    texture_transfer: Optional[bool] = None,
    texture_amount: Optional[float] = None,
    artifact_reduce: Optional[bool] = None,
    artifact_amount: Optional[float] = None,
    superres: Optional[bool] = None,
    superres_amount: Optional[float] = None,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[str] = None,
    dual_run: Optional[bool] = None,
    allow_auto_erase: bool = False,
    preserve_untouched: Optional[bool] = None,
) -> RemoveResult:
    """
    处理一张本地图片文件，返回 RemoveResult。

    参数：
        src     : 原图路径（支持中文路径）。
        dst     : 结果保存路径；不传则落到统一的 outputs 目录（按日期分文件夹）。
        mode    : "auto"（智能检测）/"manual"（同名掩码）。
        engine  : "lama"/"opencv"/"auto"。
        fmt     : 输出格式（PNG/JPG/WEBP/TIFF/AVIF），缺省取 config。
        quality : 画质 1~100（仅 JPG/WebP 生效）。
        evaluate: True 时处理完评估质量，结果放入 RemoveResult.quality。
        allow_auto_erase: **显式允许「自动选预设 + 直接擦除」**（阶段 233）。
                  默认 False ⇒ 若同时 `preset=None` 且 `auto_preset=True`，
                  会抛 `AutoEraseNotConfirmed`（详见该类文档）。GUI 走预览确认流程，
                  故不受影响；脚本/批量若确要全自动，请显式传 True。
    其余为修复参数，缺省取 config。
    """
    _guard_auto_erase(entry="remove_watermark", preset=preset,
                      auto_preset=auto_preset, allow_auto_erase=allow_auto_erase)
    out_dir = _os.path.dirname(_os.path.abspath(dst)) if dst else None
    rd = process_file(
        src,
        mode=mode,
        engine=engine,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        sensitivity=sensitivity,
        preset=preset,
        auto_preset=auto_preset,
        dual_run=dual_run,
        out_fmt=fmt,
        quality=quality,
        out_dir=out_dir,
        preserve_untouched=preserve_untouched,
        poisson_blend=poisson_blend,
        poisson_strength=poisson_strength,
        texture_transfer=texture_transfer,
        texture_amount=texture_amount,
        artifact_reduce=artifact_reduce,
        artifact_amount=artifact_amount,
        superres=superres,
        superres_amount=superres_amount,
        real_superres=real_superres,
        real_scale=real_scale,
        real_backend=real_backend,
        on_progress=None,
    )
    out_path = rd.get("out_path")
    if dst and out_path and _os.path.exists(out_path):
        # 用户指定了精确输出路径：把 process_file 生成的文件移动到目标位置。
        _move_file(out_path, str(dst))
        out_path = str(dst)

    qr: Optional[QualityReport] = None
    if evaluate and rd.get("status") == "success" and out_path and _os.path.exists(out_path):
        qr = evaluate_quality(str(src), out_path)

    # Stage 125：显式请求了某增强，但部署闸（POSTPROCESS_*_ALLOW）关闭时，下游
    # postprocess_service 只会打一条 INFO 日志并静默跳过（见 Stage 122 未修登记）。
    # 为避免「调用方以为生效、实际没生效」的静默契约背离，这里给出可观测信号：
    # 既写进 RemoveResult.warnings，也打 WARNING 级日志。
    warn_list: list[str] = []
    if poisson_blend is True and not getattr(config, "POSTPROCESS_POISSON_ALLOW", False):
        msg = (
            "poisson_blend 已请求，但部署闸 POSTPROCESS_POISSON_ALLOW=False，"
            "该后处理增强已被静默跳过（结果中未应用 Poisson 融合）。"
        )
        log.warning(msg)
        warn_list.append(msg)
    if texture_transfer is True and not getattr(config, "POSTPROCESS_TEXTURE_ALLOW", False):
        msg = (
            "texture_transfer 已请求，但部署闸 POSTPROCESS_TEXTURE_ALLOW=False，"
            "该后处理增强已被静默跳过（结果中未应用纹理重建）。"
        )
        log.warning(msg)
        warn_list.append(msg)

    return RemoveResult(
        original_name=rd.get("original_name", _os.path.basename(str(src))),
        out_path=out_path,
        status=rd.get("status", "failed"),
        mask_pixels=rd.get("mask_pixels", 0),
        regions=rd.get("regions", 0),
        used_ocr=rd.get("used_ocr", False),
        engine=rd.get("engine", ""),
        error=rd.get("error"),
        quality=qr,
        warnings=warn_list,
    )


def remove_watermark_bytes(
    data: bytes,
    *,
    mode: str = "auto",
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    evaluate: bool = False,
    poisson_blend: Optional[bool] = None,
    poisson_strength: Optional[float] = None,
    texture_transfer: Optional[bool] = None,
    texture_amount: Optional[float] = None,
    artifact_reduce: Optional[bool] = None,
    artifact_amount: Optional[float] = None,
    superres: Optional[bool] = None,
    superres_amount: Optional[float] = None,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[str] = None,
    dual_run: Optional[bool] = None,
    allow_auto_erase: bool = False,
    preserve_untouched: Optional[bool] = None,
) -> tuple[bytes, Optional[QualityReport]]:
    """
    处理内存里的图片字节（例如从 HTTP 请求 / 数据库读到的图），返回 (结果字节, 质量报告)。

    注意：字节模式按 BGR 三通道解码（OpenCV 默认），透明通道会被丢弃；
          若需保留透明通道，请用 remove_watermark() 文件接口。

    阶段 233：新增 `allow_auto_erase`（默认 False）。若同时 `preset=None` 且
    `auto_preset=True` 且未显式允许，会抛 `AutoEraseNotConfirmed`——
    因为「系统替用户选预设 + 直接擦除」在文字密集插画上可能大面积误检。
    详见 `AutoEraseNotConfirmed` 与 `config.API_REQUIRE_AUTO_ERASE_CONFIRM`。
    """
    _guard_auto_erase(entry="remove_watermark_bytes", preset=preset,
                      auto_preset=auto_preset, allow_auto_erase=allow_auto_erase)
    fmt = (fmt or config.API_DEFAULT_FMT).upper()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("无法解码图片字节（不是合法的图片数据）")
    # 阶段 353：`imdecode` 给的是 **BGR**，而核心管线（`process_image` / `detect_watermark_mask`
    # / `inpaint`）只认 **RGB**（auto_pipeline 契约）⇒ 这里必须转，否则 REST 入口的检测结果
    # 与文件/界面路径**不是同一件事**（与阶段 338 修的 PDF/视频同类）。
    arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)

    core = process_image(
        arr,
        mode=mode,
        engine=engine,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        sensitivity=sensitivity,
        preset=preset,
        auto_preset=auto_preset,
        dual_run=dual_run,
        preserve_untouched=preserve_untouched,
        poisson_blend=poisson_blend,
        poisson_strength=poisson_strength,
        texture_transfer=texture_transfer,
        texture_amount=texture_amount,
        artifact_reduce=artifact_reduce,
        artifact_amount=artifact_amount,
        superres=superres,
        superres_amount=superres_amount,
        real_superres=real_superres,
        real_scale=real_scale,
        real_backend=real_backend,
        on_progress=None,
    )
    if core["status"] != "success":
        raise RuntimeError(f"处理失败：{core.get('error', core['status'])}")

    result_rgb = core["result_rgb"]
    out_bytes = _encode_bytes(result_rgb, fmt, quality)
    qr = _evaluate_arrays(arr, result_rgb) if evaluate else None
    return out_bytes, qr


def detect_watermark_bytes(
    data: bytes,
    *,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    sensitivity: Optional[float] = None,
) -> tuple[bytes, dict]:
    """
    检测内存图片字节中的水印，返回 (掩码 PNG 字节, 检测统计字典)。

    与 remove_watermark_bytes 的区别：只做「检测」不做「修复」，把掩码单独返回，
    供 REST /api/detect 与脚本先预览水印位置，再决定要不要修、或把掩码回传做
    人工/算法二次圈定。

    参数说明：
        data        : 原始图片字节（与 remove_watermark_bytes 同解码口径：cv2 默认 BGR）。
        preset      : 检测预设名（见 core/detection_presets）；None 走默认全策略。
        auto_preset : 是否允许按图自动推荐预设（缺省开）。
        sensitivity : 半透明检测敏感度 0~1；None 取 config 默认值。

    返回值：
        (mask_bytes, stats)
        · mask_bytes : 单通道灰度 PNG（0/255），尺寸与原图一致，可直接叠加回原图。
        · stats      : {"mask_pixels","coverage_ratio","region_count","by_kind",
                        "used_ocr","regions":[{"x","y","w","h","kind","confidence"}]}

    异常：
        非法图片字节会抛 ValueError（与 remove_watermark_bytes 同口径）。
    """
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("无法解码图片字节（不是合法的图片数据）")
    arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)   # 阶段 353：同上，imdecode→BGR，核心要 RGB

    det = detect_watermark_mask(
        arr,
        preset=preset,
        sensitivity=sensitivity,
    )
    mask = det.mask
    if mask is None:
        # 理论上不会为 None；兜底给全黑掩码，保接口不崩。
        mask = np.zeros(arr.shape[:2], dtype=np.uint8)

    ok, buf = cv2.imencode(".png", mask)
    if not ok:
        raise RuntimeError("检测掩码编码失败")
    mask_bytes = buf.tobytes()

    h, w = mask.shape[:2]
    area = int(h * w)
    pixels = int(np.count_nonzero(mask > 0))
    by_kind: dict[str, int] = {}
    for r in det.regions:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    stats = {
        "mask_pixels": pixels,
        "coverage_ratio": (pixels / area) if area > 0 else 0.0,
        "region_count": len(det.regions),
        "by_kind": by_kind,
        "used_ocr": det.used_ocr,
        "regions": [
            {
                "x": int(r.x), "y": int(r.y), "w": int(r.w), "h": int(r.h),
                "kind": r.kind,
                "confidence": float(getattr(r, "confidence", 0.0)),
            }
            for r in det.regions
        ],
    }
    return mask_bytes, stats


def inpaint_watermark_bytes(
    data: bytes,
    mask_data: bytes,
    *,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    engine: Optional[str] = None,
    dual_run: Optional[bool] = None,
) -> bytes:
    """
    对内存图片字节，按调用方提供的掩码字节做修复，返回结果图片字节。

    与 remove_watermark_bytes 的区别：跳过「自动检测」，直接用调用方给的掩码，
    适合「先 /api/detect 预览 → 人工/算法圈定掩码 → 再修复」的两段式流程，
    也允许脚本用自定义掩码（例如外部 OCR / 分割模型产出）。

    参数说明：
        data     : 原图字节（cv2 默认 BGR 三通道）。
        mask_data: 掩码字节（PNG/其他格式均可）；自动取单通道——
                   4 通道取 alpha、3 通道转灰度、否则用原单通道。语义 0/255（非0即修复）。
        fmt/quality/修复参数：与 remove_watermark_bytes 完全一致。

    返回值：
        修复后图片字节。

    异常：
        原图/掩码字节非法、或掩码全黑（没有要修复的区域）会抛异常
        （与原 inpaint 入口一致，由调用方决定如何呈现）。
    """
    fmt = (fmt or config.API_DEFAULT_FMT).upper()
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("无法解码原图字节（不是合法的图片数据）")
    arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)   # 阶段 353：同上

    mk = cv2.imdecode(np.frombuffer(mask_data, np.uint8), cv2.IMREAD_UNCHANGED)
    if mk is None:
        raise ValueError("无法解码掩码字节（不是合法的图片数据）")
    if mk.ndim == 3:
        if mk.shape[2] == 4:
            mk = mk[:, :, 3]
        else:
            mk = cv2.cvtColor(mk, cv2.COLOR_BGR2GRAY)
    # mk 现在应为 (H, W) 单通道；inpaint 内部会把 0/255 与 bool 抬到 0/255 值域。

    result_rgb = inpaint(
        arr,
        mk,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        engine=engine,
        dual_run=dual_run,
    )
    return _encode_bytes(result_rgb, fmt, quality)


def remove_watermark_array(
    arr: np.ndarray,
    *,
    mode: str = "auto",
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    evaluate: bool = False,
    poisson_blend: Optional[bool] = None,
    poisson_strength: Optional[float] = None,
    texture_transfer: Optional[bool] = None,
    texture_amount: Optional[float] = None,
    artifact_reduce: Optional[bool] = None,
    artifact_amount: Optional[float] = None,
    superres: Optional[bool] = None,
    superres_amount: Optional[float] = None,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[str] = None,
    dual_run: Optional[bool] = None,
    preserve_untouched: Optional[bool] = None,
) -> tuple[np.ndarray, Optional[QualityReport]]:
    """
    处理内存里的 numpy 数组（BGR uint8，OpenCV 排布），返回 (结果数组, 质量报告)。
    适合视频帧、摄像头画面等「已经在内存里」的图像。
    """
    core = process_image(
        arr,
        mode=mode,
        engine=engine,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        sensitivity=sensitivity,
        preset=preset,
        auto_preset=auto_preset,
        dual_run=dual_run,
        preserve_untouched=preserve_untouched,
        poisson_blend=poisson_blend,
        poisson_strength=poisson_strength,
        texture_transfer=texture_transfer,
        texture_amount=texture_amount,
        artifact_reduce=artifact_reduce,
        artifact_amount=artifact_amount,
        superres=superres,
        superres_amount=superres_amount,
        real_superres=real_superres,
        real_scale=real_scale,
        real_backend=real_backend,
        on_progress=None,
    )
    if core["status"] != "success":
        raise RuntimeError(f"处理失败：{core.get('error', core['status'])}")

    result_rgb = core["result_rgb"]
    qr = _evaluate_arrays(arr, result_rgb) if evaluate else None
    return result_rgb, qr


def batch_remove(
    paths: list[str | _os.PathLike],
    out_dir: str | _os.PathLike,
    *,
    mode: str = "auto",
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    evaluate: bool = False,
    dual_run: Optional[bool] = None,
    allow_auto_erase: bool = False,
    preserve_untouched: Optional[bool] = None,
) -> list[RemoveResult]:
    """批量处理一组本地图片到指定目录，返回 RemoveResult 列表。

    阶段 233：转发 `allow_auto_erase` 给 `remove_watermark`。
    ⚠ 若不转发，确认门会让**每一张**都被判 failed（异常被下面单张容错吞掉），
    表现为「整批静默全失败」——这正是本门要避免的**静默**形态。
    """
    out_dir = str(out_dir)
    _os.makedirs(out_dir, exist_ok=True)
    results: list[RemoveResult] = []
    for p in paths:
        try:
            res = remove_watermark(
                p, dst=None, mode=mode, engine=engine,
                strength=strength, dilate=dilate, feather=feather,
                threshold=threshold, denoise=denoise,                 sensitivity=sensitivity,
                preset=preset, auto_preset=auto_preset,
                fmt=fmt, quality=quality, evaluate=evaluate,
                dual_run=dual_run,
                allow_auto_erase=allow_auto_erase,
                preserve_untouched=preserve_untouched,
            )
            # 重定向到用户指定目录（remove_watermark 默认落 outputs/，这里统一挪过来）。
            if res.out_path and _os.path.exists(res.out_path):
                target = make_unique_path(out_dir, _os.path.basename(res.out_path))
                _move_file(res.out_path, str(target))
                res.out_path = str(target)
        except Exception as exc:  # noqa: BLE001 - 单张失败不中断整批。
            res = RemoveResult(
                original_name=_os.path.basename(str(p)),
                status="failed",
                error=str(exc),
            )
        results.append(res)
    return results


# ==============================================================================
# 第 4 节：更多输入源接口（阶段 10 新增：PDF 文档 / 视频）
# ------------------------------------------------------------------------------
# 设计说明：
#   PDF 与视频的实现细节全部封装在 services/pdf_service.py 与
#   services/video_service.py 里，本模块只做「一层薄封装」，目的是让你在
#   自己的脚本里保持统一的调用姿势：
#
#       from api import remove_watermark_pdf, remove_watermark_video
#
#       r1 = remove_watermark_pdf("合同.pdf", dst="合同_干净.pdf", pages="1-3")
#       print(r1.summary())
#
#       r2 = remove_watermark_video("片段.mp4", frame_step=3)
#       print(r2.summary())
# ==============================================================================
def remove_watermark_pdf(
    src: str | _os.PathLike,
    *,
    dst: Optional[str | _os.PathLike] = None,
    pages: Optional[str] = None,
    dpi: Optional[int] = None,
    engine: Optional[str] = None,
    strength: Optional[float] = None,
    dilate: Optional[int] = None,
    feather: Optional[int] = None,
    threshold: Optional[int] = None,
    denoise: Optional[int] = None,
    sensitivity: Optional[float] = None,
    preset: Optional[str] = None,
    auto_preset: bool = True,
    on_progress=None,
    poisson_blend: Optional[bool] = None,
    poisson_strength: Optional[float] = None,
    texture_transfer: Optional[bool] = None,
    texture_amount: Optional[float] = None,
    artifact_reduce: Optional[bool] = None,
    artifact_amount: Optional[float] = None,
    superres: Optional[bool] = None,
    superres_amount: Optional[float] = None,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[str] = None,
    preserve_untouched: Optional[bool] = None,
) -> "PdfResult":
    """
    对一份 PDF 逐页去水印，输出新的 PDF 文件。

    参数说明：
        src      : 源 PDF 路径。
        dst      : 输出路径；None 表示自动放到 outputs 目录并自动避重名。
        pages    : 页码范围，如 "1-5"、"1,3,7"；None 表示全部页。
        dpi      : 渲染精度（默认取 config.PDF_RENDER_DPI，即 150）。
        其余参数 : 与单图处理完全一致的修复参数。

    返回值：
        PdfResult 对象（含逐页明细）；用 res.summary() 可拿到一句人话总结。

    注意：
        输出 PDF 的文字将变为图像，不可再被选中或搜索 —— 这是「整页重绘」
        方案的固有代价，详见 docs/10-多输入源设计.md。
    """
    from services.pdf_service import process_pdf  # 延迟导入：没装 PyMuPDF 也不影响图片功能

    return process_pdf(
        src,
        dst,
        pages=pages,
        dpi=dpi,
        mode="auto",
        engine=engine,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        sensitivity=sensitivity,
        preset=preset,
        auto_preset=auto_preset,
        poisson_blend=poisson_blend,
        poisson_strength=poisson_strength,
        texture_transfer=texture_transfer,
        texture_amount=texture_amount,
        artifact_reduce=artifact_reduce,
        artifact_amount=artifact_amount,
        superres=superres,
        superres_amount=superres_amount,
        real_superres=real_superres,
        real_scale=real_scale,
        real_backend=real_backend,
        on_progress=on_progress,
        preserve_untouched=preserve_untouched,
    )


def remove_watermark_video(
    src: str | _os.PathLike,
    *,
    dst: Optional[str | _os.PathLike] = None,
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
    auto_preset: bool = True,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[object] = None,
    real_model_path: Optional[str] = None,
    real_model_dir: Optional[str] = None,
    on_progress=None,
    preserve_untouched: Optional[bool] = None,
) -> "VideoResult":
    """
    对一段视频逐帧去水印，输出新的视频文件（无音轨）。

    参数说明：
        src        : 源视频路径。
        dst        : 输出路径；None 表示自动放到 outputs 目录。
        frame_step : 每隔几帧重新检测一次水印位置（默认 1，即每帧都检测）。
                     固定位置水印（台标、角标）设为 3~5 可显著提速。
        max_frames : 最多处理多少帧（默认 900，约 30fps 下的 30 秒），防呆用。
        reuse_mask : 是否复用上一次检测到的水印位置（默认 True）。
        preset     : 检测预设名（如 "ai_logo"）；None 表示由 auto_preset 决定。
        auto_preset: True（默认）= 逐帧智能推荐预设；False = 整段共用 preset。
                     ⚠ 阶段 315 补齐：本入口此前**没有**这两个参数，而 PDF 入口有
                     ⇒ 脚本化调用无法为视频钉预设（门面不透出，服务层其实支持）。
        其余参数   : 与单图处理一致的修复参数（含 real_superres / real_scale 真实超分放大）。

    返回值：
        VideoResult 对象；用 res.summary() 拿总结，res.warnings 拿注意事项。

    注意：
        输出视频【没有声音】。需要原声请用 ffmpeg 合并，可调用
        services.video_service.build_audio_merge_command() 生成现成命令。
    """
    from services.video_service import process_video  # 延迟导入，保持启动轻量

    return process_video(
        src,
        dst,
        frame_step=frame_step,
        max_frames=max_frames,
        reuse_mask=reuse_mask,
        engine=engine,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        sensitivity=sensitivity,
        preset=preset,
        auto_preset=auto_preset,
        real_superres=real_superres,
        real_scale=real_scale,
        real_backend=real_backend,
        real_model_path=real_model_path,
        real_model_dir=real_model_dir,
        on_progress=on_progress,
        preserve_untouched=preserve_untouched,
    )


def detect_input_kind(path: str | _os.PathLike) -> str:
    """
    按扩展名判断输入文件属于哪一类，供调用方自动分派。

    返回值：
        "image" / "pdf" / "video" / "unknown"

    用途：
        CLI 与界面用它实现「拖进来什么就处理什么」，不需要用户手动选类型。
    """
    ext = _os.path.splitext(str(path))[1].lower()
    if ext in config.SUPPORTED_DOC_FORMATS:
        return "pdf"
    if ext in config.SUPPORTED_VIDEO_FORMATS:
        return "video"
    if ext in config.SUPPORTED_INPUT_FORMATS:
        return "image"
    return "unknown"


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    from PIL import Image, ImageDraw  # type: ignore

    # 造一张带半透明水印的测试图（字节形式 + 文件路径形式）。
    img = Image.new("RGB", (320, 200), (240, 240, 240))
    d = ImageDraw.Draw(img, "RGBA")
    for j in range(4):
        d.text((20 + j * 45, 80 + (j % 2) * 30), "WATERMARK", fill=(170, 170, 170, 140))
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    print("== 用例1：字节接口 ==")
    out_b, qr = remove_watermark_bytes(raw_bytes, engine="opencv", evaluate=True,
                                       allow_auto_erase=True)
    back = cv2.imdecode(np.frombuffer(out_b, np.uint8), cv2.IMREAD_COLOR)
    assert back is not None and back.size > 0, "字节接口返回空图"
    print(f"  通过：返回 {len(out_b)} 字节，质量分 {qr.score if qr else 'N/A'}（{qr.level if qr else '-'}）")

    print("== 用例2：数组接口 ==")
    # 形状不变量断言必须与环境解耦（Stage 122 铁律）：本机 user_overrides.json 可能把
    # REAL_SUPERRES_ENABLED 设为 True，真实超分会放大输出尺寸，使 out_a.shape != arr.shape。
    # 故显式钉死 real_superres=False / superres=False，保证形状断言在任何环境下都确定。
    out_a, qr2 = remove_watermark_array(
        arr, engine="opencv", evaluate=True, real_superres=False, superres=False
    )
    assert out_a is not None and out_a.shape == arr.shape, "数组接口形状异常"
    print(f"  通过：返回数组 {out_a.shape}，质量分 {qr2.score if qr2 else 'N/A'}")

    print("== 用例3：文件接口 + 精确 dst ==")
    tmp_in = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_in.close()
    img.save(tmp_in.name)
    tmp_out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_out.close()
    res = remove_watermark(tmp_in.name, dst=tmp_out.name, engine="opencv", evaluate=True,
                           allow_auto_erase=True)
    assert res.status == "success" and _os.path.exists(tmp_out.name), "文件接口失败"
    print(f"  通过：{res}")

    print("== 用例4：批量接口 ==")
    bdir = tempfile.mkdtemp(prefix="wme_api_batch_")
    res_list = batch_remove([tmp_in.name], bdir, engine="opencv",
                            allow_auto_erase=True)
    assert len(res_list) == 1 and res_list[0].status == "success", "批量接口失败"
    print(f"  通过：批量 {len(res_list)} 张，状态 {res_list[0].status}")

    print("== 用例4b：全自动擦除确认门 ==")
    # 阶段 233：默认必须拒绝「自动选预设 + 直接擦除」。
    try:
        remove_watermark(tmp_in.name, dst=tmp_out.name, engine="opencv")
    except AutoEraseNotConfirmed:
        print("  通过：未显式允许时如实拒绝（AutoEraseNotConfirmed）")
    else:
        raise AssertionError("确认门失效：未显式允许却通过了全自动擦除")
    # 关掉配置应恢复旧行为（可逆杠杆）。
    _old_gate = config.API_REQUIRE_AUTO_ERASE_CONFIRM
    config.API_REQUIRE_AUTO_ERASE_CONFIRM = False
    try:
        _r = remove_watermark(tmp_in.name, dst=tmp_out.name, engine="opencv")
        assert _r.status == "success", "关闭确认门后应恢复旧行为"
        print("  通过：关闭确认门可恢复全自动（可逆）")
    finally:
        config.API_REQUIRE_AUTO_ERASE_CONFIRM = _old_gate

    print("== 用例5：能力快照 ==")
    caps = get_capabilities()
    print(f"  通过：device={caps['device']} lama_available={caps['lama_available']} "
          f"formats={caps['output_formats']}")
    print(f"        pdf_available={caps['pdf_available']} "
          f"video_available={caps['video_available']}")

    print("== 用例6：输入类型自动识别（阶段 10）==")
    assert detect_input_kind("a.png") == "image"
    assert detect_input_kind("b.PDF") == "pdf"
    assert detect_input_kind("c.mp4") == "video"
    assert detect_input_kind("d.exe") == "unknown"
    print("  通过：image / pdf / video / unknown 四类识别正确")

    print("== 用例7：PDF 接口（阶段 10）==")
    if caps["pdf_available"]:
        import fitz  # type: ignore

        pdf_dir = tempfile.mkdtemp(prefix="wme_api_pdf_")
        pdf_in = _os.path.join(pdf_dir, "api测试.pdf")
        _d = fitz.open()
        _pg = _d.new_page(width=420, height=595)
        _pg.insert_text((50, 90), "content", fontsize=16)
        _pg.draw_rect(fitz.Rect(260, 500, 400, 545), color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
        _pg.insert_text((272, 528), "WATERMARK", fontsize=14, color=(1, 1, 1))
        _d.save(pdf_in)
        _d.close()

        pdf_res = remove_watermark_pdf(pdf_in, dst=_os.path.join(pdf_dir, "out.pdf"), dpi=100)
        assert pdf_res.status == "success", f"PDF 接口失败：{pdf_res.error}"
        assert pdf_res.out_path and _os.path.isfile(pdf_res.out_path)
        print(f"  通过：{pdf_res.summary()}")
    else:
        print("  跳过：当前环境未安装 PyMuPDF")

    print("== 用例8：视频接口（阶段 10）==")
    if caps["video_available"]:
        vid_dir = tempfile.mkdtemp(prefix="wme_api_video_")
        vid_in = _os.path.join(vid_dir, "api测试.mp4")
        _fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        _vw = cv2.VideoWriter(vid_in, _fourcc, 10.0, (240, 180))
        for _i in range(6):
            _cv = np.full((180, 240, 3), 50 + _i * 10, dtype=np.uint8)
            cv2.putText(_cv, "MARK", (150, 168), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (255, 255, 255), 2)
            _vw.write(_cv)
        _vw.release()

        vid_res = remove_watermark_video(
            vid_in, dst=_os.path.join(vid_dir, "out.mp4"), frame_step=2, max_frames=6
        )
        assert vid_res.status == "success", f"视频接口失败：{vid_res.error}"
        assert vid_res.written_frames == 6, "视频接口帧数不符"
        print(f"  通过：{vid_res.summary()}")
    else:
        print("  跳过：当前环境 OpenCV 缺少视频模块")

    print("== 用例9：detect_watermark_bytes（阶段 D1 细粒度端点）==")
    mask_b, stats = detect_watermark_bytes(raw_bytes, preset=None)
    assert mask_b and len(mask_b) > 0, "检测返回空掩码"
    mk_arr = cv2.imdecode(np.frombuffer(mask_b, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert mk_arr is not None and mk_arr.shape == arr.shape[:2], "掩码尺寸与原图不符"
    print(f"  通过：mask={mk_arr.shape} 命中像素={stats['mask_pixels']} 区域数={stats['region_count']}")

    print("== 用例10：inpaint_watermark_bytes（阶段 D1 细粒度端点）==")
    # 用合成实心掩码覆盖水印区域，验证「跳过检测、直接用掩码修复」链路。
    syn_mk = np.zeros(arr.shape[:2], dtype=np.uint8)
    cv2.rectangle(syn_mk, (10, 70), (300, 130), 255, -1)
    _, syn_buf = cv2.imencode(".png", syn_mk)
    out_inpaint = inpaint_watermark_bytes(raw_bytes, syn_buf.tobytes(), engine="opencv")
    back2 = cv2.imdecode(np.frombuffer(out_inpaint, np.uint8), cv2.IMREAD_COLOR)
    assert back2 is not None and back2.shape == arr.shape, "修复返回空图或尺寸不符"
    print(f"  通过：返回 {len(out_inpaint)} 字节，形状 {back2.shape}")

    _os.remove(tmp_in.name)
    _os.remove(tmp_out.name)
    print("api.py 自检通过。")
