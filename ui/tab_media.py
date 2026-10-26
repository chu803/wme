# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/tab_media.py
 文件说明 : 「文档与视频」标签页（阶段 10 新增，阶段 19 接入 i18n）。

            把 PDF 文档与视频这两种「多页/多帧」输入源接进网页界面：

              左半区 · PDF：上传 → 自动读出页数 → 选页码范围与清晰度 → 一键处理
                            → 下载结果 PDF
              右半区 · 视频：上传 → 自动读出时长/帧数 → 选抽帧步长与帧数上限
                            → 一键处理 → 在线预览 + 下载

            界面层职责边界（严格遵守项目分层铁律）：
              · 本文件只负责「收集用户操作 + 展示结果 + 汇报进度」；
              · 真正的拆页 / 抽帧 / 修复 / 回写，全部委托给
                services.pdf_service 与 services.video_service；
              · 不在本文件里写任何图像算法。
              · 文案统一走 utils.i18n 的 t()，组件注册进 ui.i18n_rt 以支持实时切换。
 版 本号 : 1.1.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import cv2

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import gradio as gr

import config
from ui.components import (
    build_repair_params,
    build_detection_stats_markdown,
    build_aggregate_detection_stats_markdown,
    normalize_file_paths,
    format_elapsed,
)
import time as _time
from utils.logger import get_logger
from utils.i18n import t
from core.detection_presets import list_presets, preset_display
from utils.recommend_view import reason_line
from services.cache_service import get_cache
from services.video_export import export_frame_compare, export_contact_sheet
from services.validators import validate_pdf_file, validate_video_file
from utils.exceptions import MediaError, format_exception_for_ui
from ui.i18n_rt import (
    register,
    reg_value,
    reg_label,
    reg_label_info,
    reg_textbox,
)

log = get_logger("ui.tab_media")
_CACHE = get_cache()


def _preset_choice_label(name: str, default_display: str) -> str:
    """
    把预设名映射成本地化展示名（与 ui.tab_auto 同逻辑）。

    优先用 i18n 键 detect.preset.<name>.title；缺失则回退预设内置中文 display，
    保证界面绝不出现裸键名。
    """
    label = t(f"detect.preset.{name}.title")
    if label.startswith("detect.preset."):
        return default_display
    return label


def _media_preset_args(auto_preset: bool, preset: str) -> "Dict[str, object]":
    """
    把媒体页的「逐页/逐帧智能推荐」开关换算成传给服务的 preset/auto_preset 参数。

    参数说明：
        auto_preset : 勾选框状态。True → 整份媒体逐页/逐帧独立推荐；
                      False → 整份共用下拉里选的预设。
        preset      : 下拉选中的共享预设名（auto_preset 关闭时生效）。

    返回值：
        · auto_preset=True  → {"preset": None, "auto_preset": True}
          服务层会对每页/每检测帧内部调用 recommend_preset 挑预设。
        · auto_preset=False → {"preset": <共享预设>, "auto_preset": False}
    """
    if auto_preset:
        return {"preset": None, "auto_preset": True}
    return {"preset": str(preset or config.DETECT_PRESET_DEFAULT), "auto_preset": False}


# ==============================================================================
# 第 1 节：通用小工具
# ==============================================================================
def _normalize_file_value(value) -> str:
    """
    把 gr.File 的返回值统一成「一个本地文件路径字符串」。

    阶段 191：实现收敛到 `ui.components.normalize_file_paths`（唯一真值来源），
    本函数只负责「取第一个」这一**本页特有**的形状约定。
    顺带修掉一个潜在缺陷：旧实现用 `getattr(value, "name", None)` 兜底，而
    `pathlib.Path` 也有 `.name`（**只是末段文件名**）⇒ `/tmp/a.png` 会被截成 `a.png`。
    新实现把 `os.PathLike` 判断排在 `.name` 之前。

    返回值：
        文件路径；拿不到时返回空字符串。
    """
    paths = normalize_file_paths(value)
    return paths[0] if paths else ""


def _human_seconds(sec: float) -> str:
    """把秒数转成「x分x秒」这种人话表述，界面上比裸数字友好。"""
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.1f} 秒"
    minutes = int(sec // 60)
    return f"{minutes} 分 {sec - minutes * 60:.0f} 秒"


# ==============================================================================
# 第 2 节：PDF 相关回调
# ==============================================================================
def on_pdf_uploaded(file_value) -> str:
    """
    用户上传 PDF 后触发：读出页数、尺寸、体积，回显一段说明文字。

    参数说明：
        file_value : gr.File 组件的返回值。

    返回值：
        Markdown 文本，展示在「文档信息」区域。
    """
    path = _normalize_file_value(file_value)
    if not path:
        return t("media.pdf.info_init")

    try:
        from services.pdf_service import get_pdf_info, is_pdf_available

        if not is_pdf_available():
            return t("media.pdf.no_fitz")

        info = get_pdf_info(path)
        if not info["ok"]:
            return t("media.pdf.read_fail", error=info["error"])

        # 粗略预估耗时：按经验，CPU 下每页约 1~3 秒（150 DPI）。
        est = info["pages"] * 2
        title = info["title"] or t("common.unknown_title")
        return t(
            "media.pdf.loaded",
            name=_os.path.basename(path),
            pages=info["pages"],
            size=info["page_size"],
            title=title,
            mb=info["size_mb"],
            est=_human_seconds(est),
            max=config.PDF_MAX_PAGES,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("读取 PDF 信息失败")
        return t("media.pdf.read_exc", exc=format_exception_for_ui(exc))


def run_pdf(
    file_value,
    pages_spec: str,
    dpi: int,
    engine: str,
    strength: float,
    dilate: int,
    feather: int,
    threshold: int,
    denoise: int,
    sensitivity: float,
    auto_preset: bool = True,
    preset: str = config.DETECT_PRESET_DEFAULT,
    progress: "gr.Progress" = gr.Progress(),
):
    """
    执行 PDF 去水印。

    参数说明：
        file_value  : 上传的 PDF。
        pages_spec  : 页码范围字符串（空 = 全部）。
        dpi         : 渲染清晰度。
        engine 等   : 修复参数（与其他标签页共用同一套面板）。
        progress    : Gradio 注入的进度对象。按 Gradio 6.x 约定，
                      该参数必须放在所有普通输入之后，且默认值为 gr.Progress()。

    返回值（三元组，对应三个输出组件）：
        (日志文本, 结果文件组件的更新对象——同时携带文件路径与可见性,
         检测命中率统计 Markdown)
    """
    path = _normalize_file_value(file_value)
    if not path:
        return (t("media.pdf.no_file"), gr.update(value=None, visible=False),
                build_detection_stats_markdown(None))

    try:
        from services.pdf_service import process_pdf, is_pdf_available

        if not is_pdf_available():
            return (t("media.pdf.no_fitz"), gr.update(value=None, visible=False),
                    build_detection_stats_markdown(None))

        # 阶段 44：预校验闸门 —— 在处理前先挡掉格式非法 / 体积超限 / 损坏的 PDF，
        # 避免把一个无效文件推进沉重的逐页修复流程才报错。
        try:
            validate_pdf_file(path)
        except MediaError as exc:
            return (
                t("media.pdf.invalid", exc=format_exception_for_ui(exc)),
                gr.update(value=None, visible=False),
                build_detection_stats_markdown(None),
            )

        progress(0.0, desc=t("media.pdf.progress_desc"))

        def _cb(p: float, msg: str) -> None:
            # 把服务层的 0~1 进度直接转交给 Gradio 顶部进度条。
            progress(max(0.0, min(1.0, p)), desc=msg)

        # 阶段 193（D2）：本次 PDF 处理耗时（从进入修复到返回结果）。
        _t0 = _time.perf_counter()
        res = process_pdf(
            path,
            None,  # 输出路径交给服务层自动生成（落在 outputs 目录）
            pages=(pages_spec or "").strip() or None,
            dpi=int(dpi),
            engine=engine,
            strength=strength,
            dilate=int(dilate),
            feather=int(feather),
            threshold=int(threshold),
            denoise=int(denoise),
            sensitivity=sensitivity,
            **_media_preset_args(auto_preset, preset),
            on_progress=_cb,
        )
        _pdf_elapsed = _time.perf_counter() - _t0

        if res.status != "success":
            return (t("media.pdf.fail", error=res.error), gr.update(value=None, visible=False),
                    build_detection_stats_markdown(None))

        # 拼一份逐页明细，让用户知道每页发生了什么。
        _preset_counts: "Dict[str, int]" = {}
        lines = [
            t("media.pdf.done", name=_os.path.basename(res.out_path or "")),
            "",
            res.summary(),
            "",
            t("media.pdf.per_page"),
        ]
        for pr in res.pages:
            icon = {"success": "✅", "skipped": "➖", "failed": "❌"}.get(pr.status, "·")
            if pr.error:
                detail = t("media.pdf.page_detail_err", error=pr.error)
            else:
                detail = t("media.pdf.page_detail_ok", pixels=pr.mask_pixels)
            # 阶段 27/28：逐页智能推荐预设展示 + 分布累计；阶段 28 追加「推荐理由」。
            if auto_preset and pr.recommended_preset:
                _preset_counts[pr.recommended_preset] = _preset_counts.get(pr.recommended_preset, 0) + 1
                lines.append(
                    f"  {icon} 第 {pr.index} 页 {detail} · {pr.seconds:.1f}s"
                    f" · {t('media.pdf.page_preset', preset=preset_display(pr.recommended_preset))}"
                )
                # 阶段 28：把推荐理由（预设 + 置信度 + 触发特征）作为子行展示。
                _rl = reason_line(pr.recommendation)
                if _rl:
                    lines.append(f"      ↳ {_rl}")
            else:
                lines.append(f"  {icon} 第 {pr.index} 页 {detail} · {pr.seconds:.1f}s")

        # 阶段 27：智能推荐预设分布汇总。
        if auto_preset and _preset_counts:
            dist = "，".join(f"{preset_display(k)}×{v}" for k, v in _preset_counts.items())
            lines.append("")
            lines.append(t("media.pdf.preset_dist", dist=dist))

        tip = t("media.pdf.tip")
        lines += ["", tip]
        # 阶段 23：聚合整份 PDF 各页的检测命中率统计。
        stats_md = build_aggregate_detection_stats_markdown([pr.stats for pr in res.pages])

        # 阶段 40：处理结果登记进历史缓存（首页缩略图 + 元信息），供历史页回访/下载。
        _store_pdf_record(res, path)

        # 阶段 193（D2）：把本次 PDF 耗时拼进产物，与单图页共用 format_elapsed 接缝。
        lines.append(format_elapsed(_pdf_elapsed, key="media.msg.elapsed"))

        return "\n".join(lines), gr.update(value=res.out_path, visible=True), stats_md

    except Exception as exc:  # noqa: BLE001
        log.exception("PDF 处理异常")
        return (
            t("media.pdf.exc", type=type(exc).__name__, exc=format_exception_for_ui(exc)),
            gr.update(value=None, visible=False),
            build_detection_stats_markdown(None),
        )


# ==============================================================================
# 第 3 节：视频相关回调
# ==============================================================================
def on_video_uploaded(file_value) -> str:
    """
    用户上传视频后触发：读出帧数、帧率、分辨率、时长，并预估处理耗时。

    返回值：
        Markdown 文本，展示在「视频信息」区域。
    """
    path = _normalize_file_value(file_value)
    if not path:
        return t("media.video.info_init")

    try:
        from services.video_service import get_video_info

        info = get_video_info(path)
        if not info["ok"]:
            return t("media.video.read_fail", error=info["error"])

        frames = info["frames"]
        plan = min(frames, config.VIDEO_MAX_FRAMES) if frames else config.VIDEO_MAX_FRAMES
        # 经验值：CPU 下每帧约 0.3~1 秒（取决于分辨率），这里按 0.5 秒粗估。
        est = plan * 0.5

        warn = ""
        if frames > config.VIDEO_MAX_FRAMES:
            warn = t("media.video.warn", frames=frames, max=config.VIDEO_MAX_FRAMES)

        return t(
            "media.video.loaded",
            name=_os.path.basename(path),
            w=info["width"],
            h=info["height"],
            fps=info["fps"],
            frames=frames,
            dur=info["duration"],
            mb=info["size_mb"],
            plan=plan,
            est=_human_seconds(est),
            warn=warn,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("读取视频信息失败")
        return t("media.video.read_exc", exc=format_exception_for_ui(exc))


def _build_video_browse_gallery(out_path) -> list:
    """
    处理完成后从结果视频均匀抽样若干帧，组装 gr.Gallery 值（图片数组 + 标题），
    供用户不播放视频也能快速浏览修复效果；点击任一帧可加载到抽帧对比面板。

    阶段 340：顺手记下「画廊位置 → 真实帧号」的映射（见 `_browse_frame_indices`）。
    标题写的是真实帧号（`第 {f['idx']} 帧`），而 `gr.Gallery` 的 `evt.index` 是**位置**
    （0..n-1）——两者以前被混用，点「第 240 帧」会把滑块设成 4。
    """
    if not out_path or not _os.path.isfile(out_path):
        return []
    try:
        from services.video_service import extract_sampled_frames

        frames = extract_sampled_frames(
            out_path, n=config.VIDEO_COMPARE_FRAMES, return_indices=True
        )
        _VIDEO_BROWSE_INDICES[str(out_path)] = [int(f["idx"]) for f in frames]
        return [(f["image"], f"第 {f['idx']} 帧") for f in frames]
    except Exception as exc:  # noqa: BLE001
        log.warning("视频浏览帧抽样失败：%s", exc)
        return []


#: 画廊位置 → 真实帧号（按结果视频路径缓存）。
#: 为什么用缓存而不是点一次抽一次：抽 6 帧要解码 6 张图，点选是即时交互，
#: 每次都重抽会把一次点击变成几百毫秒的解码；而重新抽样也和画廊同源（同一个
#: `extract_sampled_frames`），不会漂出第二套抽样公式（LESSONS #110/#114）。
_VIDEO_BROWSE_INDICES: "dict[str, list[int]]" = {}


def _browse_frame_indices(out_path) -> list:
    """取某条结果视频的「位置→帧号」表；缓存未命中时按画廊同一公式抽一次。"""
    key = str(out_path or "")
    if not key or not _os.path.isfile(key):
        return []
    cached = _VIDEO_BROWSE_INDICES.get(key)
    if cached:
        return cached
    _build_video_browse_gallery(key)          # 顺带填缓存
    return _VIDEO_BROWSE_INDICES.get(key, [])


def gallery_position_to_frame(indices: list, position) -> "int | None":
    """把 `gr.Gallery` 的点击位置翻译成**真实帧号**；越界/无表 ⇒ None。

    判据来自 339/336 同一条：界面不能把「我做的是 A」写成「B」。
    以前这里直接把位置当帧号用 ⇒ 对比面板显示的是另一帧，而且**看起来是成功的**。
    """
    if indices is None or position is None:
        return None
    try:
        pos = int(position)
    except (TypeError, ValueError):
        return None
    if pos < 0 or pos >= len(indices):
        return None
    return int(indices[pos])


def _store_video_record(res, src_path) -> None:
    """把成功视频结果登记进历史缓存（首帧缩略图 + 元信息），供历史页回访。"""
    try:
        _CACHE.add_video_record(
            src_path,
            res.out_path,
            original_name=_os.path.basename(str(src_path)),
            params=res.summary(),
            duration=(res.total_frames / res.fps) if res.fps > 0 else 0.0,
            fps=res.fps,
            frames=res.written_frames,
            width=res.width,
            height=res.height,
            upscaled=res.upscaled,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("视频历史登记失败：%s", exc)


def _store_pdf_record(res, src_path) -> None:
    """阶段 40：把成功 PDF 结果登记进历史缓存（首页缩略图 + 元信息），供历史页回访/下载。"""
    try:
        if not res.out_path or not _os.path.isfile(res.out_path):
            log.warning("PDF 结果路径无效，跳过历史登记：%s", res.out_path)
            return
        _CACHE.add_pdf_record(
            src_path,
            res.out_path,
            original_name=_os.path.basename(str(src_path)),
            params=res.summary(),
            page_count=res.total_pages,
            file_size=int(_os.path.getsize(res.out_path)),
            upscaled=False,  # PDF 媒体页当前未暴露真实超分开关，统一记 False（字段保留便于后续扩展）
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF 历史登记失败：%s", exc)


def run_video(
    file_value,
    frame_step: int,
    max_frames: int,
    reuse_mask: bool,
    engine: str,
    strength: float,
    dilate: int,
    feather: int,
    threshold: int,
    denoise: int,
    sensitivity: float,
    real_superres: bool = False,
    real_scale: int = config.REAL_SUPERRES_SCALE,
    real_backend: str = config.REAL_SUPERRES_BACKEND,
    auto_preset: bool = True,
    preset: str = config.DETECT_PRESET_DEFAULT,
    progress: "gr.Progress" = gr.Progress(),
):
    """
    执行视频去水印。

    参数说明：
        file_value  : 上传的视频。
        frame_step  : 抽帧步长（每隔几帧重新检测一次水印位置）。
        max_frames  : 本次最多处理多少帧。
        reuse_mask  : 是否复用上一次检测到的水印位置。
        engine 等   : 修复参数。
        progress    : Gradio 注入的进度对象（必须位于所有普通输入之后）。

    返回值（六元组，对应六个输出组件）：
        (日志文本, 视频组件更新对象, ffmpeg 合并命令文本, 检测命中率统计 Markdown,
         浏览画廊值(各检测帧抽样图，供阶段 37 结果浏览), 导出文件组件占位(None))
    """
    path = _normalize_file_value(file_value)
    if not path:
        return (t("media.video.no_file"), gr.update(value=None, visible=False), "",
                build_detection_stats_markdown(None), [], None)

    try:
        from services.video_service import process_video, build_audio_merge_command

        # 阶段 44：预校验闸门 —— 在处理前先挡掉格式非法 / 体积超限 / 损坏的视频，
        # 避免把一个无效文件推进沉重的逐帧修复流程才报错。
        try:
            validate_video_file(path)
        except MediaError as exc:
            return (
                t("media.video.invalid", exc=format_exception_for_ui(exc)),
                gr.update(value=None, visible=False),
                "",
                build_detection_stats_markdown(None),
                [],
                None,
            )

        progress(0.0, desc=t("media.video.progress_desc"))

        def _cb(p: float, msg: str) -> None:
            progress(max(0.0, min(1.0, p)), desc=msg)

        # 阶段 193（D2）：本次视频处理耗时（从进入修复到返回结果）。
        _t0 = _time.perf_counter()
        res = process_video(
            path,
            None,
            frame_step=int(frame_step),
            max_frames=int(max_frames),
            reuse_mask=bool(reuse_mask),
            engine=engine,
            strength=strength,
            dilate=int(dilate),
            feather=int(feather),
            threshold=int(threshold),
            denoise=int(denoise),
            sensitivity=sensitivity,
            real_superres=real_superres,
            real_scale=real_scale,
            real_backend=real_backend,
            **_media_preset_args(auto_preset, preset),
            on_progress=_cb,
        )
        _video_elapsed = _time.perf_counter() - _t0

        if res.status != "success":
            return (t("media.video.fail", error=res.error), gr.update(value=None, visible=False), "",
                    build_detection_stats_markdown(None), [], None)

        lines = [
            t("media.video.done", name=_os.path.basename(res.out_path or "")),
            "",
            res.summary(),
            "",
            t("media.video.spec", w=res.width, h=res.height, fps=res.fps, codec=res.codec),
        ]
        # 阶段 33：真实超分放大徽章。
        if getattr(res, "upscaled", False):
            lines.append(
                t("media.video.upscaled",
                  scale=getattr(res, "real_scale_used", 1) or config.REAL_SUPERRES_SCALE,
                  w=res.width, h=res.height)
            )
        if res.warnings:
            lines.append("")
            lines.append(t("media.video.warn_title"))
            lines += [f"  · {w}" for w in res.warnings]

        # 阶段 27：智能推荐预设分布汇总（复用掩码场景下服务层会强制关闭推荐，
        # 此时 preset_counts 为空，不展示分布）。
        if auto_preset and getattr(res, "preset_counts", None):
            dist = "，".join(f"{preset_display(k)}×{v}" for k, v in res.preset_counts.items())
            lines.append("")
            lines.append(t("media.video.preset_dist", dist=dist))
            # 阶段 28：展示逐检测帧智能推荐的平均置信度（衡量推荐整体靠谱程度）。
            _avg = getattr(res, "recommend_conf_avg", 0.0) or 0.0
            if _avg > 0:
                lines.append(t("media.video.recommend_conf", conf=_avg))

        # 生成一条可直接复制的 ffmpeg 命令，帮用户把原声合并回去。
        final_out = _os.path.splitext(res.out_path or "")[0] + "_带声音.mp4"
        cmd = build_audio_merge_command(path, res.out_path or "", final_out)

        # 阶段 23：聚合整段视频各帧的检测命中率统计。
        stats_md = build_aggregate_detection_stats_markdown(res.frame_stats)

        # 阶段 37：处理结果浏览 + 历史登记。
        gallery_value = _build_video_browse_gallery(res.out_path)
        _store_video_record(res, path)

        # 阶段 193（D2）：把本次视频耗时拼进产物，与单图页共用 format_elapsed 接缝。
        lines.append(format_elapsed(_video_elapsed, key="media.msg.elapsed"))

        return ("\n".join(lines), gr.update(value=res.out_path, visible=True),
                cmd, stats_md, gallery_value, None)

    except Exception as exc:  # noqa: BLE001
        log.exception("视频处理异常")
        return (
            t("media.video.exc", type=type(exc).__name__, exc=format_exception_for_ui(exc)),
            gr.update(value=None, visible=False),
            "",
            build_detection_stats_markdown(None),
            [],
            None,
        )


# ==============================================================================
# 第 3.5 节：视频抽帧对比查看（阶段 33）
# ==============================================================================
def _extract_frame(path: str, idx: int):
    """
    从视频抽第 idx 帧，返回 RGB uint8 数组；失败 / 越界返回 None。

    越界时自动 clamp 到有效范围，避免用户选了超长帧序号直接报错。
    """
    cap = None
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total > 0:
            idx = max(0, min(int(idx), total - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    except Exception as exc:  # noqa: BLE001
        log.warning("视频抽帧失败：%s", exc)
        return None
    finally:
        if cap is not None:
            cap.release()


def run_video_compare(src_value, out_value, frame_idx, mode):
    """
    抽帧对比查看：从源视频与结果视频各抽同一帧，渲染拼接 / 滑块 / 热图。

    参数说明：
        src_value : 源视频组件值（gr.File）。
        out_value : 结果视频组件值（gr.Video）。
        frame_idx : 抽帧序号（0 = 首帧）。
        mode      : "concat" / "slider" / "heatmap"（复用 compare 块键）。

    返回值：
        gr.HTML 可渲染的对比内容；缺资源 / 异常时返回提示段落。

    说明：
        阶段 38：渲染实现下沉到 services.compare_service（与图片页三模式同源），
        本函数只负责抽帧、尺寸对齐与本地化提示。
    """
    # 延迟导入，避免 UI 层硬依赖服务层 import 时序。
    from services import compare_service as _cs

    src = _normalize_file_value(src_value)
    out = _normalize_file_value(out_value)
    if not out or not _os.path.isfile(out) or not src or not _os.path.isfile(src):
        return t("media.video.compare_no_video")

    fidx = max(0, int(frame_idx or 0))
    orig = _extract_frame(src, fidx)
    proc = _extract_frame(out, fidx)
    if orig is None or proc is None:
        return t("media.video.compare_no_frame")

    # 结果可能已被真实超分放大；对比聚焦去水印效果而非放大，故缩回源尺寸
    # （compare_service.align_pair 内部也会兜底对齐，这里保留显式一步更直观）。
    if proc.shape[:2] != orig.shape[:2]:
        proc = cv2.resize(proc, (orig.shape[1], orig.shape[0]), interpolation=cv2.INTER_AREA)

    html = _cs.render_compare_html(
        orig, proc, mode,
        heatmap_caption=t("compare.heatmap_label"),
    )
    if not html:
        return t("media.video.compare_no_frame")
    return html


def on_frame_gallery_select(evt: "gr.SelectData", src_value, out_value, mode):
    """
    点击「结果帧浏览」画廊的某一帧：把该帧序号写入抽帧对比面板，
    并立即渲染对比图（拼接 / 滑块 / 热图）。

    返回值：(更新抽帧序号滑块, 对比 HTML)

    阶段 340：`evt.index` 是**画廊里的位置**（0..n-1），不是帧号。
    以前直接把它当帧号写进滑块并抽帧 ⇒ 点标题为「第 240 帧」的缩略图，
    下面对比面板显示的是第 4 帧，而且界面看起来一切正常（比报错更糟）。
    """
    idx = getattr(evt, "index", None)
    if idx is None:
        return gr.update(), t("media.video.compare_no_frame")
    src = _normalize_file_value(src_value)
    out = _normalize_file_value(out_value)
    if not out or not _os.path.isfile(out) or not src or not _os.path.isfile(src):
        return gr.update(value=idx), t("media.video.compare_no_video")
    # 位置 → 真实帧号；拿不到映射时宁可明说「没选中」，也不显示另一帧冒充。
    frame_idx = gallery_position_to_frame(_browse_frame_indices(out), idx)
    if frame_idx is None:
        return gr.update(), t("media.video.compare_no_frame")
    html = run_video_compare(src_value, out_value, frame_idx, mode)
    return gr.update(value=frame_idx), html


def export_frame_file(src_value, out_value, frame_idx, mode):
    """导出当前对比帧的「原图 | 结果」拼接对比图（PNG）。

    返回值：(文件路径或 None, 写进视频日志的一句真话)

    阶段 343：以前失败路径一律 `return None`，而按钮只接一个 `gr.File` 组件 ⇒
    「点了没反应」与「成功」在界面上完全不可区分。
    更巧的是 `media.video.export_no_video` / `export_done` 两条文案**早就中英双语写好了**，
    只是没有任何代码用它们（`grep` 全仓零引用）⇒ 反馈这件事停在半路。
    """
    src = _normalize_file_value(src_value)
    out = _normalize_file_value(out_value)
    if not out or not _os.path.isfile(out) or not src or not _os.path.isfile(src):
        return None, t("media.video.export_no_video")
    try:
        path = export_frame_compare(src, out, int(frame_idx or 0))
    except Exception as exc:  # noqa: BLE001 —— 导出失败不许静默，要把原因说给用户
        log.exception("导出对比帧图失败")
        return None, t("media.video.export_fail", exc=format_exception_for_ui(exc))
    if not path:
        return None, t("media.video.export_fail", exc=t("media.video.compare_no_frame"))
    return path, t("media.video.export_done", path=path)


def export_sheet_file(out_value):
    """导出结果视频的「帧拼图 / 联系表」（PNG）。返回值同 `export_frame_file`。"""
    out = _normalize_file_value(out_value)
    if not out or not _os.path.isfile(out):
        return None, t("media.video.export_no_video")
    try:
        path = export_contact_sheet(out)
    except Exception as exc:  # noqa: BLE001
        log.exception("导出帧拼图失败")
        return None, t("media.video.export_fail", exc=format_exception_for_ui(exc))
    if not path:
        return None, t("media.video.export_fail", exc=t("media.video.compare_no_frame"))
    return path, t("media.video.export_done", path=path)


# ==============================================================================
# 第 4 节：页签装配
# ==============================================================================
def build_media_tab():
    """构建「文档与视频」标签页的全部组件并绑定回调。"""

    _media_title = gr.Markdown(t("media.title"))
    reg_value(_media_title, "media.title")

    with gr.Tabs():
        # ======================================================================
        # 子页 A：PDF 文档
        # ======================================================================
        with gr.TabItem(t("media.pdf.tab"), id="media_pdf", elem_id="media_pdf"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=3):
                    pdf_file = gr.File(
                        label=t("media.pdf.file_label"),
                        file_count="single",
                        file_types=[".pdf"],
                        type="filepath",
                        elem_id="media_pdf",
                    )
                    pdf_info = gr.Markdown(t("media.pdf.info_init"))

                    with gr.Row():
                        pdf_pages = gr.Textbox(
                            label=t("media.pdf.pages_label"),
                            placeholder=t("media.pdf.pages_placeholder"),
                            value="",
                            info=t("media.pdf.pages_info"),
                        )
                        reg_textbox(
                            pdf_pages,
                            "media.pdf.pages_label",
                            placeholder_key="media.pdf.pages_placeholder",
                        )
                        pdf_dpi = gr.Slider(
                            minimum=72, maximum=300, step=6,
                            value=config.PDF_RENDER_DPI,
                            label=t("media.pdf.dpi_label"),
                            info=t("media.pdf.dpi_info"),
                        )
                        reg_label_info(
                            pdf_dpi, "media.pdf.dpi_label", "media.pdf.dpi_info"
                        )

                    pdf_params = build_repair_params(
                        accordion_title="media.pdf.params_accordion",
                        open_=False,
                        sensitivity_label="params.sensitivity_label",
                        sensitivity_info="params.sensitivity_info",
                    )

                    # 阶段 21：检测模式（预设）下拉，一键适配 AI 生成图常见水印样式。
                    pdf_detect_preset = gr.Dropdown(
                        choices=[(_preset_choice_label(p.name, p.display), p.name)
                                 for p in list_presets()],
                        value=config.DETECT_PRESET_DEFAULT,
                        label=t("media.pdf.detect_mode_label"),
                        info=t("media.pdf.detect_mode_info"),
                    )
                    reg_label_info(pdf_detect_preset, "media.pdf.detect_mode_label",
                                   "media.pdf.detect_mode_info")

                    # 阶段 27：逐页智能推荐开关（默认开；开启时整份 PDF 每页独立挑预设，
                    # 故置灰 detect_preset 下拉避免"选了又没用"的误导）。
                    pdf_auto_preset = gr.Checkbox(
                        value=True,
                        label=t("media.pdf.auto_preset_label"),
                        info=t("media.pdf.auto_preset_info"),
                    )
                    reg_label_info(pdf_auto_preset, "media.pdf.auto_preset_label",
                                   "media.pdf.auto_preset_info")

                    pdf_btn = gr.Button(t("media.pdf.btn"), variant="primary", size="lg")
                    reg_label(pdf_btn, "media.pdf.btn")

                with gr.Column(scale=2):
                    _pdf_result_title = gr.Markdown(t("media.pdf.result_title"))
                    reg_value(_pdf_result_title, "media.pdf.result_title")
                    pdf_log = gr.Textbox(
                        label=t("media.pdf.log_label"),
                        lines=16, interactive=False,
                        placeholder=t("media.pdf.log_placeholder"),
                    )
                    reg_textbox(
                        pdf_log, "media.pdf.log_label",
                        placeholder_key="media.pdf.log_placeholder",
                    )
                    pdf_out = gr.File(label=t("media.pdf.out_label"), visible=False)
                    reg_label(pdf_out, "media.pdf.out_label")

                    # 阶段 23：整份 PDF 各页检测命中率统计（覆盖率 / 区域数 / 按类别命中 / OCR）。
                    pdf_detect_stats = gr.Markdown(
                        value=build_detection_stats_markdown(None),
                        label=t("detect.stats.label"),
                    )
                    reg_value(pdf_detect_stats, "detect.stats.label")

            _pdf_note = gr.Markdown(t("media.pdf.note"))
            reg_value(_pdf_note, "media.pdf.note")

            # ---- 事件绑定：上传后自动读信息 ----
            pdf_file.change(fn=on_pdf_uploaded, inputs=[pdf_file], outputs=[pdf_info])

            # ---- 事件绑定：开始处理 ----
            pdf_btn.click(
                fn=run_pdf,
                inputs=[
                    pdf_file, pdf_pages, pdf_dpi,
                    pdf_params.engine, pdf_params.strength, pdf_params.dilate,
                    pdf_params.feather, pdf_params.threshold, pdf_params.denoise,
                    pdf_params.sensitivity, pdf_auto_preset, pdf_detect_preset,
                ],
                outputs=[pdf_log, pdf_out, pdf_detect_stats],
            )

            # 阶段 27：智能推荐开启时禁用下拉（避免"选了又没用"的误导）。
            pdf_auto_preset.change(
                fn=lambda on: gr.update(interactive=not on),
                inputs=[pdf_auto_preset],
                outputs=[pdf_detect_preset],
            )

        # ======================================================================
        # 子页 B：视频
        # ======================================================================
        with gr.TabItem(t("media.video.tab"), id="media_video", elem_id="media_video"):
            with gr.Row(equal_height=False):
                with gr.Column(scale=3):
                    video_file = gr.File(
                        label=t("media.video.file_label"),
                        file_count="single",
                        file_types=list(config.SUPPORTED_VIDEO_FORMATS),
                        type="filepath",
                        elem_id="media_video",
                    )
                    video_info = gr.Markdown(t("media.video.info_init"))

                    with gr.Row():
                        v_step = gr.Slider(
                            minimum=1, maximum=15, step=1,
                            value=config.VIDEO_FRAME_STEP,
                            label=t("media.video.step_label"),
                            info=t("media.video.step_info"),
                        )
                        reg_label_info(
                            v_step, "media.video.step_label", "media.video.step_info"
                        )
                        v_max = gr.Slider(
                            minimum=10, maximum=config.VIDEO_MAX_FRAMES, step=10,
                            value=min(300, config.VIDEO_MAX_FRAMES),
                            label=t("media.video.max_label"),
                            info=t("media.video.max_info"),
                        )
                        reg_label_info(
                            v_max, "media.video.max_label", "media.video.max_info"
                        )

                    v_reuse = gr.Checkbox(
                        value=config.VIDEO_REUSE_MASK,
                        label=t("media.video.reuse_label"),
                        info=t("media.video.reuse_info"),
                    )
                    reg_label_info(
                        v_reuse, "media.video.reuse_label", "media.video.reuse_info"
                    )

                    video_params = build_repair_params(
                        accordion_title="media.video.params_accordion",
                        open_=False,
                        sensitivity_label="params.sensitivity_label",
                        sensitivity_info="params.sensitivity_info",
                    )

                    # 阶段 21：检测模式（预设）下拉，一键适配 AI 生成图常见水印样式。
                    video_detect_preset = gr.Dropdown(
                        choices=[(_preset_choice_label(p.name, p.display), p.name)
                                 for p in list_presets()],
                        value=config.DETECT_PRESET_DEFAULT,
                        label=t("media.video.detect_mode_label"),
                        info=t("media.video.detect_mode_info"),
                    )
                    reg_label_info(video_detect_preset, "media.video.detect_mode_label",
                                   "media.video.detect_mode_info")

                    # 阶段 27：逐帧智能推荐开关（默认开；开启时每段视频的每个检测帧独立挑预设）。
                    # 复用掩码（固定水印）场景下推荐无意义，故勾选 reuse 时禁用本开关并强制关闭。
                    video_auto_preset = gr.Checkbox(
                        value=True,
                        label=t("media.video.auto_preset_label"),
                        info=t("media.video.auto_preset_info"),
                    )
                    reg_label_info(video_auto_preset, "media.video.auto_preset_label",
                                   "media.video.auto_preset_info")

                    video_btn = gr.Button(t("media.video.btn"), variant="primary", size="lg")
                    reg_label(video_btn, "media.video.btn")

                with gr.Column(scale=2):
                    _video_result_title = gr.Markdown(t("media.video.result_title"))
                    reg_value(_video_result_title, "media.video.result_title")
                    video_log = gr.Textbox(
                        label=t("media.video.log_label"),
                        lines=12, interactive=False,
                        placeholder=t("media.video.log_placeholder"),
                    )
                    reg_textbox(
                        video_log, "media.video.log_label",
                        placeholder_key="media.video.log_placeholder",
                    )
                    video_out = gr.Video(label=t("media.video.out_label"), visible=False)
                    reg_label(video_out, "media.video.out_label")
                    # 注意：Gradio 6.x 的 Textbox 已移除 show_copy_button 参数，
                    # 这里保持默认（用户可直接框选复制），避免版本不兼容报错。
                    ffmpeg_cmd = gr.Textbox(
                        label=t("media.video.ffmpeg_label"),
                        lines=3, interactive=False,
                        placeholder=t("media.video.ffmpeg_placeholder"),
                    )
                    reg_textbox(
                        ffmpeg_cmd, "media.video.ffmpeg_label",
                        placeholder_key="media.video.ffmpeg_placeholder",
                    )

                    # 阶段 33：抽帧对比查看（拼接 / 滑块 / 热图）。
                    _video_compare_title = gr.Markdown(t("media.video.compare_title"))
                    reg_value(_video_compare_title, "media.video.compare_title")
                    with gr.Row():
                        v_compare_frame = gr.Slider(
                            minimum=0, maximum=config.VIDEO_MAX_FRAMES, step=1, value=0,
                            label=t("media.video.compare_frame_label"),
                            info=t("media.video.compare_frame_info"),
                        )
                        reg_label_info(
                            v_compare_frame, "media.video.compare_frame_label",
                            "media.video.compare_frame_info",
                        )
                        v_compare_mode = gr.Radio(
                            choices=[
                                (t("compare.mode_concat"), "concat"),
                                (t("compare.mode_slider"), "slider"),
                                (t("compare.mode_heatmap"), "heatmap"),
                            ],
                            value="concat",
                            label=t("compare.enh_mode_label"),
                        )
                        reg_label(v_compare_mode, "compare.enh_mode_label")
                    v_compare_btn = gr.Button(t("compare.enh_btn"))
                    reg_label(v_compare_btn, "compare.enh_btn")
                    v_compare_html = gr.HTML(value="")

                    # 阶段 37：结果帧浏览（处理完成后自动填充抽样帧，点击即加载对比）。
                    _video_browse_title = gr.Markdown(t("media.video.browse_title"))
                    reg_value(_video_browse_title, "media.video.browse_title")
                    v_frame_gallery = gr.Gallery(
                        value=[],
                        label=t("media.video.browse_info"),
                        columns=config.VIDEO_COMPARE_FRAMES, height=160,
                        object_fit="contain",
                    )
                    reg_label(v_frame_gallery, "media.video.browse_info")

                    # 阶段 37：结果导出（对比帧图 / 帧拼图）。
                    with gr.Row():
                        v_export_frame_btn = gr.Button(
                            t("media.video.export_frame_label"), variant="secondary"
                        )
                        reg_label(v_export_frame_btn, "media.video.export_frame_label")
                        v_export_sheet_btn = gr.Button(
                            t("media.video.export_sheet_label"), variant="secondary"
                        )
                        reg_label(v_export_sheet_btn, "media.video.export_sheet_label")
                    v_export_file = gr.File(
                        label=t("history.download_label"), interactive=False
                    )
                    reg_label(v_export_file, "history.download_label")

                    # 阶段 23：整段视频各帧检测命中率统计（覆盖率 / 区域数 / 按类别命中 / OCR）。
                    video_detect_stats = gr.Markdown(
                        value=build_detection_stats_markdown(None),
                        label=t("detect.stats.label"),
                    )
                    reg_value(video_detect_stats, "detect.stats.label")

            _video_note = gr.Markdown(t("media.video.note"))
            reg_value(_video_note, "media.video.note")

            # ---- 事件绑定 ----
            video_file.change(
                fn=on_video_uploaded, inputs=[video_file], outputs=[video_info]
            )
            video_btn.click(
                fn=run_video,
                inputs=[
                    video_file, v_step, v_max, v_reuse,
                    video_params.engine, video_params.strength, video_params.dilate,
                    video_params.feather, video_params.threshold, video_params.denoise,
                    video_params.sensitivity, video_params.real_superres, video_params.real_scale, video_params.real_backend,
                    video_auto_preset, video_detect_preset,
                ],
                outputs=[video_log, video_out, ffmpeg_cmd, video_detect_stats,
                          v_frame_gallery, v_export_file],
            )

            # 阶段 27：智能推荐开启时禁用下拉；复用掩码勾选时禁用推荐开关（固定水印无需推荐）。
            video_auto_preset.change(
                fn=lambda on: gr.update(interactive=not on),
                inputs=[video_auto_preset],
                outputs=[video_detect_preset],
            )
            v_reuse.change(
                fn=lambda reuse: gr.update(value=False, interactive=not reuse),
                inputs=[v_reuse],
                outputs=[video_auto_preset],
            )

            # 阶段 33：抽帧对比查看按钮（源视频 + 结果视频 + 帧序号 + 模式）。
            v_compare_btn.click(
                fn=run_video_compare,
                inputs=[video_file, video_out, v_compare_frame, v_compare_mode],
                outputs=[v_compare_html],
            )

            # 阶段 37：点击浏览画廊的某一帧 → 写入对比帧序号并立即渲染对比图。
            v_frame_gallery.select(
                fn=on_frame_gallery_select,
                inputs=[video_file, video_out, v_compare_mode],
                outputs=[v_compare_frame, v_compare_html],
            )

            # 阶段 37：导出对比帧图 / 帧拼图。
            # 阶段 343：两个按钮都多接一个 `video_log` ⇒ 失败时用户看得见一句真话，
            # 不再「点了没反应」。
            v_export_frame_btn.click(
                fn=export_frame_file,
                inputs=[video_file, video_out, v_compare_frame, v_compare_mode],
                outputs=[v_export_file, video_log],
            )
            v_export_sheet_btn.click(
                fn=export_sheet_file,
                inputs=[video_out],
                outputs=[v_export_file, video_log],
            )
