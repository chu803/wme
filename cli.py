# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : cli.py
 文件说明 : 命令行批量处理入口（阶段 8 拓展功能）。

            无需打开浏览器，直接在终端 / 服务器上批量擦除水印。
            复用 services.auto_pipeline（自动检测 + 修复）与
            services.quality_service（质量评估），零 gradio 依赖。

            常用示例：
              # 处理单个文件
              python cli.py -i 照片.jpg -o 结果

              # 处理整个文件夹（自动检测 + 评估质量）
              python cli.py -i 原图目录 -o 结果目录 --evaluate

              # 递归处理子目录，并限制为 OpenCV 引擎、WebP 输出
              python cli.py -i 原图目录 -o 结果目录 -r -f WEBP --engine opencv

              # 手动模式：用同名 .mask.png 作为掩码
              python cli.py -i 原图目录 --mode manual -o 结果目录

              # 真·超分：指定后端与倍数（缺 torch/权重自动回退轻量超分）
              python cli.py -i 原图目录 -o 结果目录 --real-superres --real-backend realesrgan --real-scale 4

              # 启动 REST 服务（HTTP 接口，默认 127.0.0.1:7861）
              python cli.py --serve
              # 让 REST 服务监听全网（需自行做好防火墙与鉴权）
              python cli.py --serve --host 0.0.0.0 --port 7861

            阶段 10 新增（PDF 文档 / 视频，按扩展名自动分派，无需额外开关）：
              # 处理 PDF 的第 1~5 页
              python cli.py -i 合同.pdf -o 结果 --pages 1-5

              # 处理视频，每 3 帧检测一次水印位置，最多 600 帧
              python cli.py -i 片段.mp4 -o 结果 --frame-step 3 --max-frames 600

              # 处理整个目录时，连同其中的 PDF 与视频一起处理
              python cli.py -i 素材目录 -o 结果 -r --include-media

            退出码（阶段 316 明确，此前恒为 0 使 CI 无法判败）：
              0  全部成功（含「跳过」——跳过是正常裁决，不算失败）
              1  存在处理失败项（如损坏图片）
              2  参数或输入问题（未给 -i、目录中无可处理文件）
 版 权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

# 阶段 314：必须在其它业务模块导入之前加固（插件注册等日志在导入期就会输出）。
from utils.console_encoding import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse  # noqa: E402
import os as _os
import sys as _sys
import time

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from services.auto_pipeline import process_file
from services.quality_service import evaluate_quality, QualityReport
from server import start_server

# ------------------------------------------------------------------------------
# 动态获取可用修复引擎（内建 lama/opencv/auto + 已加载的插件引擎），
# 用于 --engine 的选项校验与 --list-engines 展示。插件在导入时即被扫描发现。
# ------------------------------------------------------------------------------
try:
    from core import plugins as _plugin_mod

    ENGINE_CHOICES = _plugin_mod.get_engine_choices()
except Exception:  # noqa: BLE001
    # 极少数情况下（如核心插件模块异常）回退为内建三选一，保证 CLI 仍可用。
    ENGINE_CHOICES = ["auto", "lama", "opencv"]

from core.detection_presets import get_preset_names

# 支持的图像扩展名（小写）。
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".avif")
# 阶段 10：PDF 与视频扩展名，直接取自配置，保证与界面/接口口径一致。
DOC_EXTS = tuple(config.SUPPORTED_DOC_FORMATS)
VIDEO_EXTS = tuple(config.SUPPORTED_VIDEO_FORMATS)


def _kind_of(path: str) -> str:
    """
    按扩展名判断文件类型。

    返回值：
        "image" / "pdf" / "video" / "unknown"
    """
    ext = _os.path.splitext(str(path))[1].lower()
    if ext in DOC_EXTS:
        return "pdf"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return "unknown"


def _collect_inputs(input_path: str, recursive: bool, include_media: bool = False) -> list[str]:
    """
    收集待处理的文件路径列表。

    参数说明：
        input_path    : 文件或文件夹路径。
        recursive     : 文件夹模式下是否递归子目录。
        include_media : 文件夹模式下是否连 PDF / 视频一起收（阶段 10 新增）。
                        单文件模式不受此开关影响 —— 直接指定 PDF 就一定处理它。

    规则：
        · 输入是文件：图片 / PDF / 视频都收（类型由 _kind_of 判断）；
        · 输入是文件夹：默认只收图片，加 --include-media 才连 PDF、视频一起收。
    """
    input_path = str(input_path)
    if _os.path.isfile(input_path):
        # 单文件：只要认得出类型就收下，让主流程去分派。
        return [input_path] if _kind_of(input_path) != "unknown" else []

    if _os.path.isdir(input_path):
        wanted = IMAGE_EXTS + (DOC_EXTS + VIDEO_EXTS if include_media else ())
        found: list[str] = []
        walker = _os.walk(input_path)
        for root, _dirs, files in walker:
            for f in files:
                if f.lower().endswith(wanted):
                    found.append(_os.path.join(root, f))
            if not recursive:
                break  # 不递归：只处理顶层目录。
        return sorted(found)

    return []


def _build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器（中文帮助）。"""
    fmt_choices = list(config.SUPPORTED_OUTPUT_FORMATS)
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="AI 智能水印擦除工作台 —— 命令行批量处理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python cli.py -i 原图目录 -o 结果目录 --evaluate",
    )
    p.add_argument("-i", "--input", default=None,
                   help="输入文件或文件夹（--serve 模式可不填）")
    p.add_argument("-o", "--output", default=None, help="输出目录（缺省为 outputs/cli）")
    p.add_argument("--serve", action="store_true",
                   help="启动 REST HTTP 服务（忽略文件处理，改用 server.py 监听接口）")
    p.add_argument("--host", default=None, help="--serve 模式专用：监听地址（缺省取 API_HOST）")
    p.add_argument("--port", type=int, default=None,
                   help="--serve 模式专用：监听端口（缺省取 API_PORT）")
    p.add_argument("--mode", choices=["auto", "manual"], default="auto",
                   help="auto=智能检测水印（默认）；manual=用同名掩码图修复")
    p.add_argument("--engine", choices=ENGINE_CHOICES, default="auto",
                   help="修复引擎（默认 auto：有模型用 LaMa，否则 OpenCV；"
                        "可用 --list-engines 查看全部，含插件引擎）")
    p.add_argument("--dual-run", dest="dual_run", action="store_true",
                   help="双跑择优（阶段 74）：主引擎与副引擎（默认 LaMa，见"
                        " INPAINT_DUAL_SECONDARY）各跑一次，按无参考质量分择优；"
                        "耗时约翻倍。默认关闭")
    p.add_argument("--list-engines", action="store_true",
                   help="列出所有可用修复引擎（含插件）并退出，不做任何处理")
    p.add_argument("-f", "--format", dest="fmt", type=str.upper, choices=fmt_choices,
                   default=config.DEFAULT_OUTPUT_FORMAT, help="输出格式（默认取配置）")
    p.add_argument("-q", "--quality", type=int, default=config.DEFAULT_OUTPUT_QUALITY,
                   help="输出画质 1~100（仅 JPG/WebP 生效）")
    p.add_argument("-s", "--sensitivity", type=float,
                   default=config.DETECT_TRANSLUCENT_SENSITIVITY,
                   help="自动检测敏感度 0~1（越高越能揪出淡 watermark，但误判上升）")
    p.add_argument("--detect-preset", choices=get_preset_names(),
                   default=None,
                   help="检测预设（阶段 20）：显式指定后关闭自动推荐；留空则由「自动预设推荐」"
                        "（阶段 25）根据图像特征挑选 general / ai_tiled / ai_signature / "
                        "ai_corner / ai_text_heavy / ai_faint_tiled / ai_grid / ai_gradient / "
                        "ai_logo / ai_multi（多重水印，≥2 类信号时自动选用）")
    p.add_argument("--auto-preset", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="自动预设推荐（阶段 25，默认开）：未显式指定 --detect-preset 时，"
                        "自动分析图像特征挑选最契合的检测预设；--no-auto-preset 关闭，退回裸检测")
    p.add_argument("-r", "--recursive", action="store_true", help="递归遍历子文件夹")
    p.add_argument("-e", "--evaluate", action="store_true", help="处理完成后评估质量并给出告警")
    p.add_argument("--mask-dir", default=None, help="manual 模式：掩码文件所在目录（默认同输入目录）")
    p.add_argument("--strength", type=float, default=config.INPAINT_STRENGTH_DEFAULT, help="修复力度")
    p.add_argument("--dilate", type=int, default=config.MASK_DILATE_DEFAULT, help="掩码膨胀像素")
    p.add_argument("--feather", type=int, default=config.MASK_FEATHER_DEFAULT, help="边缘平滑")
    p.add_argument("--threshold", type=int, default=config.MASK_THRESHOLD_DEFAULT, help="掩码阈值")
    p.add_argument("--denoise", type=int, default=config.DENOISE_STRENGTH_DEFAULT, help="降噪强度")
    p.add_argument("--workers", type=int, default=1, help="并发线程数（默认 1，CPU 安全）")

    # ------------------------------------------------------------------
    # 后处理深化（阶段 21：结果画质增强深化，默认全关，向后兼容）
    # 三道 pass 需原图 + 掩码，仅在 auto 模式且已检测/修复后生效。
    # ------------------------------------------------------------------
    pp = p.add_argument_group("后处理深化（阶段 21 画质增强，默认全关闭）")
    pp.add_argument("--poisson-blend", dest="poisson_blend", action="store_true",
                    help="Poisson 无缝融合：把修复区按原图梯度无缝融入周围，消除接缝台阶")
    pp.add_argument("--poisson-strength", dest="poisson_strength", type=float,
                    default=config.POSTPROCESS_POISSON_BLEND_STRENGTH,
                    help=f"Poisson 融合强度 0~1（默认 {config.POSTPROCESS_POISSON_BLEND_STRENGTH}；"
                         f"越小越保留原修复细节）")
    pp.add_argument("--texture-transfer", dest="texture_transfer", action="store_true",
                    help="纹理细节重建：把原图周围高频纹理注入修复区，减少修补块观感")
    pp.add_argument("--texture-amount", dest="texture_amount", type=float,
                    default=config.POSTPROCESS_TEXTURE_TRANSFER_AMOUNT,
                    help=f"纹理重建强度 0~1（默认 {config.POSTPROCESS_TEXTURE_TRANSFER_AMOUNT}；"
                         f"过大会显脏）")
    pp.add_argument("--artifact-reduce", dest="artifact_reduce", action="store_true",
                    help="伪影抑制：在修复区内做边缘保持滤波，抑制 inpaint 网格/块状伪影")
    pp.add_argument("--artifact-amount", dest="artifact_amount", type=float,
                    default=config.POSTPROCESS_ARTIFACT_REDUCE_AMOUNT,
                    help=f"伪影抑制强度 0~1（默认 {config.POSTPROCESS_ARTIFACT_REDUCE_AMOUNT}；"
                         f"过大会磨平细节）")
    pp.add_argument("--superres", dest="superres", action="store_true",
                    help="轻量超分（细节重建）：注入原图高频细节并锐化，抵消 inpaint 模糊（同分辨率）")
    pp.add_argument("--superres-amount", dest="superres_amount", type=float,
                    default=config.POSTPROCESS_SUPERRES_AMOUNT,
                    help=f"轻量超分强度 0~1（默认 {config.POSTPROCESS_SUPERRES_AMOUNT}；过大会显噪）")
    pp.add_argument("--real-superres", dest="real_superres",
                    action=argparse.BooleanOptionalAction,
                    default=config.REAL_SUPERRES_ENABLED,
                    help="真·超分（AI 模型放大）：用 torch + 预训练模型真实放大输出（如 4×）；"
                         "缺 torch/权重自动回退轻量超分。"
                         f"默认取设置页/用户覆盖配置（当前 {config.REAL_SUPERRES_ENABLED}），"
                         "可用 --no-real-superres 强制关闭")
    pp.add_argument("--real-scale", dest="real_scale", type=int,
                    default=config.REAL_SUPERRES_SCALE,
                    help=f"真·超分放大倍数（默认取设置页/用户覆盖配置 {config.REAL_SUPERRES_SCALE}；"
                         "主流 ESRGAN 权重为 4）")
    pp.add_argument("--real-backend", dest="real_backend",
                    choices=["auto", "esrgan", "realesrgan"],
                    default=config.REAL_SUPERRES_BACKEND,
                    help="真·超分后端（auto/esrgan/realesrgan，默认取设置页/用户覆盖配置 "
                         f"当前 {config.REAL_SUPERRES_BACKEND}）："
                         "auto=依次尝试 Real-ESRGAN → ESRGAN；"
                         "esrgan=纯 torch RRDB（无需 basicsr）；"
                         "realesrgan=官方权重（需 realesrgan+basicsr）")

    # ------------------------------------------------------------------
    # 阶段 10：更多输入源（PDF 文档 / 视频）
    # ------------------------------------------------------------------
    media = p.add_argument_group("更多输入源（PDF / 视频，按扩展名自动分派）")
    media.add_argument("--include-media", action="store_true",
                       help="处理文件夹时，连同其中的 PDF 与视频一起处理（单文件无需此开关）")
    media.add_argument("--pages", default=None,
                       help="PDF 专用：页码范围，如 1-5 或 1,3,7（缺省为全部页）")
    media.add_argument("--dpi", type=int, default=config.PDF_RENDER_DPI,
                       help=f"PDF 专用：渲染清晰度（默认 {config.PDF_RENDER_DPI}；"
                            f"72 快而糊，300 印刷级但很慢）")
    media.add_argument("--frame-step", dest="frame_step", type=int,
                       default=config.VIDEO_FRAME_STEP,
                       help=f"视频专用：每隔几帧重新检测一次水印位置"
                            f"（默认 {config.VIDEO_FRAME_STEP}；固定水印设 3~5 可提速）")
    media.add_argument("--max-frames", dest="max_frames", type=int,
                       default=config.VIDEO_MAX_FRAMES,
                       help=f"视频专用：最多处理多少帧（默认 {config.VIDEO_MAX_FRAMES}）")
    media.add_argument("--no-reuse-mask", dest="no_reuse_mask", action="store_true",
                       help="视频专用：关闭掩码复用，每帧独立检测"
                            "（滚动字幕、飘动水印必须加此开关）")

    # ------------------------------------------------------------------
    # 阶段 12：模型管理（多模型权重切换 / 下载 / 校验）
    # ------------------------------------------------------------------
    mm = p.add_argument_group("模型管理（多权重切换，阶段 12 新增）")
    mm.add_argument("--list-models", action="store_true",
                    help="列出全部模型槽（含内置/自动发现/配置登记）及当前模型，并退出")
    mm.add_argument("--download-model", nargs="?", const="__active__", default=None,
                    metavar="NAME",
                    help="下载指定模型权重（默认当前模型）；带实时进度条。需联网")
    mm.add_argument("--verify-model", nargs="?", const="__active__", default=None,
                    metavar="NAME",
                    help="校验指定模型权重完整性（存在性/体积/SHA256）；默认当前模型")
    mm.add_argument("--set-model", default=None, metavar="NAME",
                    help="将指定模型槽设为当前模型（LaMa 引擎将使用其权重），并持久化")
    return p


def _run_one(path: str, args: argparse.Namespace, show_progress: bool,
             index: int = 0, total: int = 0) -> dict:
    """处理单张图（供串行 / 并发复用）。返回 process_file 的结果字典。"""
    def _prog(p: float, m: str) -> None:
        if show_progress and index:
            _sys.stderr.write(f"\r   [{index}/{total}] {_os.path.basename(path)} "
                              f"{p * 100:.0f}% {m}")
            _sys.stderr.flush()

    # 手动模式：若指定了掩码目录，拼出同名 .mask.png。
    mask_path = None
    if args.mode == "manual" and args.mask_dir:
        stem = _os.path.splitext(_os.path.basename(path))[0]
        cand = _os.path.join(args.mask_dir, stem + ".mask.png")
        mask_path = cand if _os.path.exists(cand) else None

    try:
        rd = process_file(
            path, mode=args.mode, engine=args.engine,
            strength=args.strength, dilate=args.dilate, feather=args.feather,
            threshold=args.threshold, denoise=args.denoise,
            sensitivity=args.sensitivity, out_fmt=args.fmt, quality=args.quality,
            preset=args.detect_preset,
            auto_preset=args.auto_preset,
            mask_path=mask_path, out_dir=args.output_dir,
            poisson_blend=args.poisson_blend,
            poisson_strength=args.poisson_strength,
            texture_transfer=args.texture_transfer,
            texture_amount=args.texture_amount,
            artifact_reduce=args.artifact_reduce,
            artifact_amount=args.artifact_amount,
            superres=args.superres,
            superres_amount=args.superres_amount,
            real_superres=args.real_superres,
            real_scale=args.real_scale,
            real_backend=args.real_backend,
            on_progress=_prog if show_progress else None,
        )
    except Exception as exc:  # noqa: BLE001 - 单张失败不中断整批。
        rd = {"status": "failed", "original_name": _os.path.basename(path),
              "input_path": path, "error": str(exc)}
    # 统一补齐「展示键」：_print_result 依 original_name → input_path 拼输出行，
    # 二者皆缺时会打印空文件名（阶段 114 实测）。process_file 当前各分支都带这两键，
    # 但 CLI 侧不应依赖它的分支实现——在此自保证，使 _run_one 返回值恒含展示所需键。
    rd.setdefault("kind", "image")
    rd.setdefault("original_name", _os.path.basename(path))
    rd.setdefault("input_path", path)
    return rd


def _run_pdf(path: str, args: argparse.Namespace, index: int = 0, total: int = 0) -> dict:
    """
    处理一份 PDF（阶段 10）。

    返回值：
        与 _run_one 同构的结果字典，额外带 kind="pdf" 与 note（一句话摘要），
        方便 _print_result 统一打印。
    """
    name = _os.path.basename(path)

    def _prog(p: float, m: str) -> None:
        if index:
            _sys.stderr.write(f"\r   [{index}/{total}] {name} {p * 100:.0f}% {m}")
            _sys.stderr.flush()

    try:
        from services.pdf_service import process_pdf

        res = process_pdf(
            path,
            _os.path.join(args.output_dir, _os.path.splitext(name)[0] + "_已去水印.pdf"),
            pages=args.pages,
            dpi=args.dpi,
            engine=args.engine,
            strength=args.strength, dilate=args.dilate, feather=args.feather,
            threshold=args.threshold, denoise=args.denoise,
            sensitivity=args.sensitivity,
            preset=args.detect_preset,
            auto_preset=args.auto_preset,
            poisson_blend=args.poisson_blend,
            poisson_strength=args.poisson_strength,
            texture_transfer=args.texture_transfer,
            texture_amount=args.texture_amount,
            artifact_reduce=args.artifact_reduce,
            artifact_amount=args.artifact_amount,
            superres=args.superres,
            superres_amount=args.superres_amount,
            real_superres=args.real_superres,
            real_scale=args.real_scale,
            real_backend=args.real_backend,
            on_progress=_prog,
        )
        return {
            "kind": "pdf",
            "status": res.status,
            "original_name": name,
            "input_path": path,
            "out_path": res.out_path,
            "error": res.error,
            "note": res.summary() if res.status == "success" else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"kind": "pdf", "status": "failed", "original_name": name,
                "input_path": path, "error": f"{type(exc).__name__}: {exc}"}


def _run_video(path: str, args: argparse.Namespace, index: int = 0, total: int = 0) -> dict:
    """
    处理一段视频（阶段 10）。

    返回值：
        与 _run_one 同构的结果字典，额外带 kind="video" 与 note。
    """
    name = _os.path.basename(path)

    def _prog(p: float, m: str) -> None:
        if index:
            _sys.stderr.write(f"\r   [{index}/{total}] {name} {p * 100:.0f}% {m}")
            _sys.stderr.flush()

    try:
        from services.video_service import process_video

        res = process_video(
            path,
            _os.path.join(
                args.output_dir,
                _os.path.splitext(name)[0] + "_已去水印" + config.VIDEO_OUTPUT_EXT,
            ),
            frame_step=args.frame_step,
            max_frames=args.max_frames,
            reuse_mask=not args.no_reuse_mask,
            engine=args.engine,
            strength=args.strength, dilate=args.dilate, feather=args.feather,
            threshold=args.threshold, denoise=args.denoise,
            sensitivity=args.sensitivity,
            preset=args.detect_preset,
            auto_preset=args.auto_preset,
            poisson_blend=args.poisson_blend,
            poisson_strength=args.poisson_strength,
            texture_transfer=args.texture_transfer,
            texture_amount=args.texture_amount,
            artifact_reduce=args.artifact_reduce,
            artifact_amount=args.artifact_amount,
            real_superres=args.real_superres,
            real_scale=args.real_scale,
            real_backend=args.real_backend,
            on_progress=_prog,
        )
        return {
            "kind": "video",
            "status": res.status,
            "original_name": name,
            "input_path": path,
            "out_path": res.out_path,
            "error": res.error,
            "note": res.summary() if res.status == "success" else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"kind": "video", "status": "failed", "original_name": name,
                "input_path": path, "error": f"{type(exc).__name__}: {exc}"}


def _dispatch(path: str, args: argparse.Namespace, show_progress: bool,
              index: int = 0, total: int = 0) -> dict:
    """
    按文件类型把任务分派给对应的处理函数（阶段 10 新增的统一入口）。

    这样无论是图片、PDF 还是视频，主循环都只需要调用这一个函数。
    """
    kind = _kind_of(path)
    if kind == "pdf":
        return _run_pdf(path, args, index if show_progress else 0, total)
    if kind == "video":
        return _run_video(path, args, index if show_progress else 0, total)
    return _run_one(path, args, show_progress, index, total)


def _print_result(rd: dict, args: argparse.Namespace,
                  summary: dict, quality_reports: list) -> None:
    """打印单张处理结果，并按需评估质量、累计统计。"""
    name = rd.get("original_name") or _os.path.basename(str(rd.get("input_path", "")))
    kind = rd.get("kind", "image")
    if rd["status"] == "success":
        summary["success"] += 1
        icon = {"pdf": "📄", "video": "🎬"}.get(kind, "✅")
        line = f"  {icon} {name} -> {rd.get('out_path')}"
        # 质量评估只对「单张图片」有意义：PDF 是多页、视频是多帧，
        # 拿整份文件去算 SSIM 没有可比性，因此这两类直接跳过评估。
        if args.evaluate and kind == "image" and rd.get("out_path"):
            q = evaluate_quality(rd["input_path"], rd["out_path"])
            quality_reports.append((name, q))
            line += f"  [质量分 {q.score} · {q.level}]"
        print(line)
        if rd.get("note"):
            print(f"      {rd['note']}")
    elif rd["status"] == "skipped":
        summary["skipped"] += 1
        print(f"  ⏭️  {name} 跳过：{rd.get('error', '')}")
    else:
        summary["failed"] += 1
        print(f"  ❌ {name} 失败：{rd.get('error', '')}")


def _batch_exit_code(summary: dict) -> int:
    """批量阶段的退出码。

    动机（阶段 315 演练实测）：17 个文件、2 个失败，进程仍返回 0 ⇒ 脚本与 CI
    无法分辨「全部成功」和「全军覆没」，只能去解析中文汇总文本（脆且不可依赖）。
    口径与门禁家族一致：**0 = 全部成功**（「跳过」是正常裁决，不计失败）/
    **1 = 存在处理失败**；2 保留给「参数或输入缺失」（在 main 前段返回）。
    """
    return 1 if summary.get("failed", 0) else 0


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口，返回进程退出码（0=全部成功 / 1=存在失败 / 2=参数或输入问题）。"""
    # argparse 对 -h 会自己退出，这里仅做解析。
    args = _build_arg_parser().parse_args(argv)

    # ----- 双跑择优（阶段 74）：CLI 开关覆写 config（默认关闭）-----
    if getattr(args, "dual_run", False):
        config.INPAINT_DUAL_RUN = True
        print("已启用双跑择优（主引擎 + LaMa 各跑一次，按无参考质量分择优）。")

    # ----- 列出可用引擎并退出（阶段 11 新增）-----
    if args.list_engines:
        try:
            from core import plugins as _pm

            details = _pm.get_engine_detail()
        except Exception:  # noqa: BLE001
            details = [
                {"name": "auto", "display_name": "自动选择", "builtin": True},
                {"name": "lama", "display_name": "LaMa 深度学习", "builtin": True},
                {"name": "opencv", "display_name": "OpenCV 传统算法", "builtin": True},
            ]
        print("可用修复引擎：")
        for e in details:
            tag = "（内建）" if e.get("builtin") else "（插件）"
            desc = e.get("description") or ""
            # 阶段 77：插件引擎追加来源文件，便于排查「同名引擎到底是谁」。
            src = e.get("source") or ""
            src_tag = f"  ← {_os.path.basename(src)}" if src else ""
            print(f"  {e['name']:<12} → {e.get('display_name', e['name'])} {tag}  {desc}{src_tag}")
        # 阶段 77：一并提示插件加载失败原因（若有），不必翻日志。
        try:
            from core import plugins as _pm2

            rep = _pm2.get_plugin_report()
            if rep.get("failed"):
                print("\n有插件加载失败：")
                for item in rep["failed"]:
                    print(f"  ✗ {_os.path.basename(str(item.get('file', '')))}：{item.get('error', '')}")
            if rep.get("overridden"):
                print("\n被覆盖的引擎名：" + ", ".join(rep["overridden"]))
        except Exception:  # noqa: BLE001
            pass
        return 0

    # ----- 模型管理命令（阶段 12 新增）-----
    if args.list_models or args.download_model is not None or args.verify_model is not None or args.set_model:
        try:
            from core import model_manager as _mm
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  模型管理模块加载失败：{exc}", file=_sys.stderr)
            return 1

        # 列出全部模型槽并标出当前模型。
        if args.list_models:
            slots = _mm.list_models()
            active = _mm.get_active_model()
            print("模型槽清单（* 为当前模型）：")
            for s in slots:
                mark = " *" if s["name"] == active else "  "
                avail = "✅" if _mm.is_model_available(s["name"]) else "❌"
                sz = f"{s['size_mb']}MB" if s.get("size_mb") else (
                    f"{s['expected_mb']}MB预期" if s.get("expected_mb") else "")
                print(f"  {mark} {s['name']:<16} → {s['display_name']}  {avail}  {sz}")
            print(f"当前模型：{active}")
            # 仅 --list-models（无其它模型动作）时直接结束。
            if not (args.download_model or args.verify_model or args.set_model):
                return 0

        # 切换当前模型并持久化。
        if args.set_model:
            if _mm.set_active_model(args.set_model):
                print(f"✅ 已将当前模型切换为：{args.set_model}")
            else:
                print(f"⚠️  未找到模型槽：{args.set_model}", file=_sys.stderr)
                return 1

        # 校验模型完整性。
        if args.verify_model is not None:
            vname = None if args.verify_model == "__active__" else args.verify_model
            v = _mm.verify_model(vname)
            print(f"模型校验 [{v['name']}]：{'通过' if v['ok'] else '未通过'} —— {v['reason']}")
            # 仅校验且未通过、且无后续下载动作时返回非 0。
            if not v["ok"] and args.download_model is None:
                return 1

        # 下载模型（带实时进度条形文本）。
        if args.download_model is not None:
            dname = None if args.download_model == "__active__" else args.download_model
            target = dname or _mm.get_active_model()
            try:
                print(f"开始下载模型：{target} ...")
                path = _mm.download_model(dname, progress_cb=None)
                print(f"\n✅ 模型已就绪：{path}")
            except Exception as exc:  # noqa: BLE001
                print(f"\n❌ 下载失败：{exc}", file=_sys.stderr)
                return 1
        return 0

    # ----- REST 服务模式：直接启动 HTTP 接口，不走文件处理 -----
    if args.serve:
        try:
            start_server(host=args.host, port=args.port)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  启动 REST 服务失败：{exc}", file=_sys.stderr)
            return 1
        return 0

    # 非服务模式：输入是必填项。
    if not args.input:
        print("⚠️  请通过 -i/--input 指定输入文件或文件夹，或使用 --serve 启动 REST 服务。",
              file=_sys.stderr)
        return 2

    # 解析输出目录（缺省 outputs/cli）。
    args.output_dir = args.output or _os.path.join(str(config.OUTPUT_DIR), "cli")
    _os.makedirs(args.output_dir, exist_ok=True)

    inputs = _collect_inputs(args.input, args.recursive, args.include_media)
    if not inputs:
        hint = ", ".join(IMAGE_EXTS)
        if args.include_media:
            hint += " | " + ", ".join(DOC_EXTS + VIDEO_EXTS)
        print(f"⚠️  在「{args.input}」中未找到可处理的文件（支持扩展名：{hint}）。",
              file=_sys.stderr)
        return 2

    # 按类型分类统计，让用户一眼看清这批任务的构成。
    kinds = [_kind_of(p) for p in inputs]
    n_img = kinds.count("image")
    n_pdf = kinds.count("pdf")
    n_vid = kinds.count("video")
    compose = f"{n_img} 张图"
    if n_pdf:
        compose += f" + {n_pdf} 份 PDF"
    if n_vid:
        compose += f" + {n_vid} 段视频"

    print(f"开始处理：{compose} | 模式={args.mode} | 引擎={args.engine} | "
          f"格式={args.fmt} | 输出={args.output_dir}")
    if args.recursive:
        print("（已开启递归遍历子目录）")
    if n_pdf:
        print(f"（PDF：页码范围={args.pages or '全部'}，渲染精度={args.dpi} DPI）")
    if n_vid:
        print(f"（视频：抽帧步长={args.frame_step}，最多 {args.max_frames} 帧，"
              f"掩码复用={'关闭' if args.no_reuse_mask else '开启'}）")
        print("（提示：视频处理很慢，且输出不含音轨）")
    if args.real_superres:
        print(f"（真·超分：开启 | 后端={args.real_backend} | 倍数={args.real_scale}×）")
    print("-" * 60)

    summary = {"success": 0, "skipped": 0, "failed": 0}
    quality_reports: list[tuple[str, QualityReport]] = []
    t0 = time.time()

    if args.workers and args.workers > 1:
        # ----- 并发模式：进度不打到 stderr（避免交错），完成后统一打印 -----
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(_dispatch, p, args, False): p for p in inputs}
            for fut in as_completed(futures):
                _print_result(fut.result(), args, summary, quality_reports)
    else:
        # ----- 串行模式：实时进度（单行刷新）-----
        for i, p in enumerate(inputs, 1):
            rd = _dispatch(p, args, show_progress=True, index=i, total=len(inputs))
            # 清掉进度行（宽度取 110，覆盖「[3/3] 文件名 100% 提示语」的最长情况）。
            _sys.stderr.write("\r" + " " * 110 + "\r")
            _sys.stderr.flush()
            _print_result(rd, args, summary, quality_reports)

    # ----- 汇总 -----
    cost = time.time() - t0
    print("-" * 60)
    print(f"处理汇总：成功 {summary['success']} / 跳过 {summary['skipped']} / "
          f"失败 {summary['failed']} / 共 {len(inputs)} （耗时 {cost:.1f}s）")
    print(f"输出目录：{args.output_dir}")
    if args.evaluate and quality_reports:
        from collections import Counter
        c = Counter(q.level for _, q in quality_reports)
        print(f"质量评估：需复核 {c.get('review', 0)} / 告警 {c.get('warn', 0)} / "
              f"良好 {c.get('good', 0)}")
        flagged = [(n, q) for n, q in quality_reports if q.level in ("review", "warn")]
        if flagged:
            print("需关注的结果：")
            for n, q in flagged:
                print(f"  🔍 {n}：分 {q.score} · {q.level} —— {q.detail}")
    print("完成。")
    rc = _batch_exit_code(summary)
    if rc:
        print(f"存在 {summary['failed']} 个失败项，退出码 1（供脚本/CI 判断，无需解析上面的文本）。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
