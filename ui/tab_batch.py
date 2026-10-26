# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/tab_batch.py
 文件说明 : 批量处理标签页（阶段 5 核心界面）。
            提供：本地文件夹/多文件批量导入 → 任务排队 → 并发处理 →
            实时进度条 + 成功/失败日志 + 结果画廊。

            处理原子单元复用：
            · 自动模式：core.detector.detect_watermark_mask 生成掩码后，
              把 (原图 + 红色掩码图层) 组装成 ImageEditor 值，直接调用
              ui.tab_auto.process_auto_repair —— 与「智能自动检测」标签页
              走完全相同的修复路径，保证两种入口行为一致。
            · 手动掩码模式：从并行「掩码文件夹」读取同名灰度掩码，组装后
              同样调用 process_auto_repair。

            并发与进度由 services.queue_worker.BatchQueue 驱动（生成器流式产出），
            本文件只负责「收集输入、展示队列进度、登记缓存」。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

from typing import Optional, Tuple

import os as _os
import sys as _sys
import time as _time

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import gradio as gr

import config
from utils.i18n import t
from ui.i18n_rt import reg_label, reg_label_info, reg_value, reg_textbox, register
from core.detector import detect_watermark_mask
from core.image_utils import load_image
from core.preset_recommender import recommend_preset
from services.cache_service import CacheService, compute_file_hash, get_cache
from services.file_service import load_for_display
from services.queue_worker import BatchQueue, Task, TaskStatus
from services.quality_service import detail_text, evaluate_quality, level_text
from services.export_service import make_compare_array
from services.report_service import ReportSource, export_report
from services.validators import validate_image_file
from ui.tab_auto import process_auto_repair, _preset_choice_label
from core.detection_presets import list_presets
from utils.recommend_view import reason_line
from ui.components import (
    build_repair_params,
    build_detection_stats_markdown,
    build_aggregate_detection_stats_markdown,
    repair_params_text,
    normalize_file_paths,
    format_elapsed,
)
from ui.compare_view import build_compare_viewer, render_pair
from utils.exceptions import ImageTooLargeError, UnsupportedFormatError, format_exception_for_ui
from utils.logger import get_logger

log = get_logger("ui.tab_batch")

# 模块级队列引用：run_batch 创建，cancel_batch 复用，便于「取消」按钮中断。
_CURRENT_QUEUE: "BatchQueue | None" = None

# 复用的缓存服务实例（线程安全，构造时自动加载索引 + 启动清理）。
_CACHE = get_cache()

# 最近一次批量运行产出的记录（用于「导出本次报告」）；每次 run_batch 开始时清空。
_batch_run_products: list = []

# 阶段 38：画廊顺序 → (原图路径, 结果路径, 条目名) 配对表。
#   画廊项与本表严格同序追加，因此 select 事件拿到的 index 可直接定位配对，
#   供「前后对比查看器」渲染该条目的原图 vs 结果。每次 run_batch 开始时清空。
_batch_pairs: list = []

# 缩略图后缀（与结果同名，便于画廊定位）。
_MASK_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

# 预设名 → 展示标签（阶段 26：日志/画廊里把内部名转成可读文案）。
_PRESET_DISPLAY = {p.name: p.display for p in list_presets()}


def _preset_display(name: str) -> str:
    """把内部预设名映射成展示标签；未知名原样返回。"""
    return _PRESET_DISPLAY.get(name, name)


def _normalize_file_value(value) -> list[str]:
    """
    把 Gradio File 组件的返回值规整成「纯路径字符串列表」。

    阶段 191：本函数**只做转发**——实现收敛到 `ui.components.normalize_file_paths`
    （唯一真值来源）。此前本文件与 `ui/tab_media.py` 各写一份，对「带 `.name` 的对象」
    处理不一致（批量 `str(obj)` vs 媒体 `obj.name`），且都会把 `pathlib.Path`
    当成有 `.name` 的对象而**丢掉目录**。

    参数说明：
        value : Gradio 的返回值（None / 路径字符串 / 列表 / dict / 带 .name 的对象…）。

    返回值：
        路径字符串列表（已去重、保序）。
    """
    return normalize_file_paths(value)


def _mask_path_for(mask_folder: str, image_path: str) -> "str | None":
    """
    在掩码文件夹里寻找与本图同名的灰度掩码文件。

    参数说明：
        mask_folder : 掩码文件夹路径。
        image_path  : 原图路径。

    返回值：
        匹配到的掩码路径；未找到返回 None。
    """
    if not mask_folder or not _os.path.isdir(mask_folder):
        return None
    stem = _os.path.splitext(_os.path.basename(image_path))[0]
    for ext in _MASK_EXTS:
        cand = _os.path.join(mask_folder, stem + ext)
        if _os.path.exists(cand):
            return cand
    return None


def mask_candidates(mask_folder_value, mask_files: "list | None" = None) -> dict:
    """把「用户上传的掩码」整理成 `{文件名主干: 掩码路径}` 索引（阶段 334）。

    为什么需要它：`gr.File(file_count="directory")` 返回的是**目录里的每个文件**，不是目录本身。
    而旧写法 `(_normalize_file_value(v) or [""])[0]` 取第一个元素当目录用 ⇒
    `_os.path.isdir(<某个 .png 的路径>)` 恒为 False ⇒ **手动掩码批量每张都报「未找到同名掩码文件」**
    （功能整条死路，且没有任何用例会走到，因为测试都直接传目录字符串给 `_mask_path_for`）。

    参数说明：
        mask_folder_value : Gradio 返回值（会被 `_normalize_file_value` 规整）。
        mask_files        : 已规整好的路径列表（测试或调用方可直接给）。

    返回值：
        dict[str, str]：只收 `_MASK_EXTS` 后缀的文件；同名多个时保留第一个（稳定）。
        目录路径也会被展开成其中的掩码文件，兼容旧调用形态。
    """
    paths = list(mask_files or [])
    if not paths and mask_folder_value:
        paths = _normalize_file_value(mask_folder_value)
    out: dict = {}
    for p in paths:
        if not p:
            continue
        # 给的是目录 ⇒ 展开一层（旧行为：整目录扫描）。
        if _os.path.isdir(p):
            for name in sorted(_os.listdir(p)):
                full = _os.path.join(p, name)
                if _os.path.isfile(full) and full.lower().endswith(_MASK_EXTS):
                    out.setdefault(_os.path.splitext(name)[0], full)
            continue
        if p.lower().endswith(_MASK_EXTS):
            out.setdefault(_os.path.splitext(_os.path.basename(p))[0], p)
    return out


def _mask_path_for_index(index: dict, image_path: str) -> "str | None":
    """按「文件名主干」在掩码索引里查本图的掩码（阶段 334，替代「必须是目录」的旧判据）。"""
    return index.get(_os.path.splitext(_os.path.basename(image_path))[0])


def batch_input_extensions() -> tuple:
    """批量输入的扩展名白名单 —— **唯一真值来源是 `config.SUPPORTED_INPUT_FORMATS`**（阶段 334）。

    旧写法用的是 `_MASK_EXTS`（`.png/.jpg/.jpeg/.bmp/.webp`，名字与文档都写着「缩略图/掩码后缀」）
    当输入过滤器 ⇒ `.tif/.tiff/.jpe/.jfif` 被**静默丢掉**：挑 20 张 TIFF 会得到「未选择任何图片文件」，
    混合目录则少掉 N 张且日志里一个字都不提。而 `config.py:1937` 的输入白名单与 README §1 都写明
    `.tif/.tiff` 是支持的（`services/validators.is_supported_input` 用的就是它）⇒ 同一件事两份口径。
    """
    return tuple(str(e).lower() for e in config.SUPPORTED_INPUT_FORMATS)


def select_batch_inputs(paths) -> "tuple[list, list]":
    """把上传路径分成「可处理的图片」与「被扩展名挡下的」。

    返回值：
        (kept, dropped)。**dropped 一定要能被调用方说出来**——旧版直接丢弃、不报告，
        用户的文件「凭空消失」是最难自查的一类问题。
    """
    exts = batch_input_extensions()
    kept, dropped = [], []
    for p in paths:
        (kept if str(p).lower().endswith(exts) else dropped).append(p)
    return kept, dropped


def cache_signature_for(params: dict) -> str:
    """把「决定输出的全部入参」压成稳定短哈希 —— 批量去重的**命中键**（阶段 334）。

    为什么不能只用文件哈希：同一张图换引擎、改灵敏度、换掩码，输出**本来就不同**，
    旧的 `find_by_hash(file_hash)` 会把上一次的旧成品当成本次结果返回，还在日志里记成成功。
    判据来自 `_process_one` 自己的入参快照 ⇒ **将来新增一个会影响输出的参数，签名自动变**，
    不需要谁记得去维护一份参数清单（手写清单必然漏，同 #167/#312）。
    """
    import hashlib
    import json
    payload = json.dumps({k: params[k] for k in sorted(params)}, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _build_editor_value(image: np.ndarray, mask: np.ndarray) -> dict:
    """
    把 (原图 + 二值掩码) 组装成 ImageEditor 的 dict 值，供 process_auto_repair 复用。

    参数说明：
        image : H×W×3 原图。
        mask  : H×W 二值掩码（0/255）。

    返回值：
        Gradio 6.x 的 ImageEditor dict：{'background','layers','composite'}。
    """
    h, w = mask.shape[:2]
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, 0] = 255
    rgba[:, :, 1] = 59
    rgba[:, :, 2] = 48
    rgba[:, :, 3] = mask  # alpha = 掩码
    return {
        "background": image,
        "layers": [rgba],
        "composite": None,
    }


def _resolve_batch_preset(
    image: "np.ndarray",
    name: str,
    sensitivity: float,
    auto_preset: bool,
    fallback_preset: str,
) -> Tuple[str, "Optional[dict]", "Optional[str]"]:
    """
    阶段 26：为单张图解析「实际使用的检测预设」。

    参数说明：
        image           : RGB 数组（来自 load_image）。
        name            : 文件名（仅用于日志）。
        sensitivity     : 检测敏感度。
        auto_preset     : 是否开启批量智能推荐（True=每张图独立推荐）。
        fallback_preset : 关闭智能推荐时使用的共享预设（来自 detect_preset 下拉）。

    返回值：
        (preset_to_use, recommendation_dict_or_None, recommended_preset_or_None)
        · preset_to_use      : 实际传给 detect_watermark_mask 的预设名。
        · recommendation     : Recommendation.to_dict()（仅 auto_preset 成功时非空）。
        · recommended_preset : 推荐出的预设名（仅 auto_preset 成功时非空）。
    """
    fallback = str(fallback_preset or config.RECOMMEND_DEFAULT_PRESET)
    if not auto_preset:
        return fallback, None, None
    try:
        rec = recommend_preset(image, sensitivity=float(sensitivity))
        recommended_preset = rec.preset
        recommendation = rec.to_dict()
        log.info(
            "批量智能推荐：%s → %s（置信度 %.2f，触发特征 %s）",
            name, rec.preset, rec.confidence, ",".join(rec.triggered) or "无",
        )
        # 推荐到具体预设才覆盖共享预设；推荐回默认 general 视为「无强信号」，
        # 回退到下拉所选（关闭智能推荐时即为整批共享预设）。
        preset_to_use = (
            recommended_preset
            if recommended_preset and recommended_preset != config.RECOMMEND_DEFAULT_PRESET
            else fallback
        )
        return preset_to_use, recommendation, recommended_preset
    except Exception as exc:  # noqa: BLE001
        log.warning("批量智能推荐失败，回退默认检测：%s", exc)
        return fallback, None, None


def _process_one(
    task: Task,
    on_progress,
    *,
    mode: str,
    engine: str,
    strength: float,
    dilate: int,
    feather: int,
    threshold: int,
    denoise: int,
    sensitivity: float,
    mask_folder: str,
    mask_index: "dict | None" = None,
    preset: str = "general",
    auto_preset: bool = True,
    sharpen=None,
    enable_postprocess: bool = True,
    postprocess_eval: bool = False,
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
) -> dict:
    """
    单图批量处理单元：被 queue_worker 在每个线程里调用。

    参数说明：
        task        : 队列任务（含 path / name）。
        on_progress : 进度回调。
        mode/engine/... : 共享处理参数。
        mask_folder : 手动模式下的掩码文件夹。

    返回值：
        结果字典：{status, out_path, result_path, thumb, skipped}。

    说明：
        本函数复用 ui.tab_auto.process_auto_repair，确保与单图/自动页签一致。
    """
    path = task.path
    # 阶段 334：**命中键在函数入口就地由自己的入参算**（不另存参数清单）。
    # `locals()` 此刻恰好只有 `_process_one` 的入参 ⇒ 新增参数会自动进入签名，不会「忘了登记」。
    # 掩码目录/文件本身排除在外：它由「实际命中的那张掩码的哈希」单独进入签名（见下），
    # 否则「同一批掩码换个临时目录名」会白白失效。
    _sig_params = {k: v for k, v in locals().items()
                   if k not in ("task", "on_progress", "path", "_sig_params",
                                "mask_folder", "mask_index", "mask_files")}
    sig_base = cache_signature_for(_sig_params)

    # 1) 解码校验（损坏/超大/格式非法在此拦截，不拖垮批次）。
    try:
        meta = validate_image_file(path)
    except (UnsupportedFormatError, ImageTooLargeError) as exc:
        raise ValueError(t("batch.task_skip", reason=exc))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(t("batch.task_read_fail", reason=exc))

    on_progress(0.1, t("batch.task_reading_mask"))

    # 2) 手动模式：先把本图的掩码定下来（阶段 334）。
    #    旧顺序是「先查缓存再找掩码」⇒ 掩码内容**根本没进命中键**，换了掩码仍复用旧成品。
    mask_path = None
    if mode == "manual":
        mask_path = _mask_path_for_index(mask_index or {}, path) or _mask_path_for(mask_folder, path)
        if not mask_path:
            raise ValueError(t("batch.task_no_mask_file", name=_os.path.basename(path)))

    # 3) 缓存去重：同一张图**且同一套会改变输出的参数**（含掩码内容）才复用。
    file_hash = compute_file_hash(path)
    cache_sig = sig_base if mask_path is None else f"{sig_base}|{compute_file_hash(mask_path)}"
    if file_hash:
        hit = _CACHE.find_by_hash(file_hash, cache_sig)
        if hit and hit.result_path and _os.path.exists(hit.result_path):
            on_progress(1.0, t("batch.task_cache_hit_msg"))
            return {
                "status": t("batch.task_cache_hit_status"),
                "out_path": hit.result_path,
                "result_path": hit.result_path,
                "thumb": hit.thumb_path or hit.result_path,
                "skipped": True,
            }

    # 4) 生成掩码。
    image = load_image(path)
    detect_stats = None  # 阶段 23：累计本次检测的命中率统计（仅自动模式有值）。
    recommendation = None       # 阶段 26：批量智能推荐结果（仅 auto 模式 + 开启推荐时有值）。
    recommended_preset = None   # 阶段 26：推荐出的预设名。
    if mode == "manual":
        mp = mask_path
        mask_arr = load_image(mp)
        # 灰度化 + 二值化。
        if mask_arr.ndim == 3:
            mask_gray = mask_arr[..., :3].mean(axis=2).astype(np.uint8)
        else:
            mask_gray = mask_arr.astype(np.uint8)
        _, mask_bin = _cv2_threshold(mask_gray)
        mask = (mask_bin > 0).astype(np.uint8) * 255
        on_progress(0.3, t("batch.task_mask_read"))
    else:
        # 自动模式：智能检测。
        # 阶段 26：批量智能推荐——为每张图独立挑选检测预设（关闭时整批共用下拉所选）。
        preset_to_use, recommendation, recommended_preset = _resolve_batch_preset(
            image, task.name, float(sensitivity), auto_preset, str(preset or "general"),
        )
        res = detect_watermark_mask(
            image,
            sensitivity=float(sensitivity),
            use_ocr=config.DETECT_USE_OCR,
            preset=preset_to_use,
        )
        mask = res.mask
        detect_stats = res.stats
        if res.stats is not None:
            cov = res.stats.coverage_ratio * 100.0
            on_progress(0.35, t("batch.task_detect_done_cov",
                                n=len(res.regions), cov=f"{cov:.1f}"))
        else:
            on_progress(0.35, t("batch.task_detect_done", n=len(res.regions)))

    if mask.sum() == 0:
        raise ValueError(t("batch.task_no_region"))

    # 4) 复用自动页签的修复路径。
    editor_value = _build_editor_value(image, mask)
    _, result, status, _, out_path = process_auto_repair(
        editor_value,
        engine=engine,
        strength=strength,
        dilate=dilate,
        feather=feather,
        threshold=threshold,
        denoise=denoise,
        enable_postprocess=enable_postprocess,
        sharpen=sharpen,
        postprocess_eval=postprocess_eval,
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
        dual_run=dual_run,
    )
    if result is None or not out_path:
        raise ValueError(t("batch.task_no_result"))

    on_progress(0.9, t("batch.task_saving"))

    # 5) 登记缓存（生成缩略图，便于历史页浏览）。
    # 阶段 110：参数摘要改为**共享 + i18n**（此前硬编码中文，会被报告层渲染出来）。
    params = repair_params_text(engine, strength, dilate, feather, threshold, denoise)
    rec = _CACHE.add_success(
        path, result,
        original_name=task.name,
        mode=mode,
        engine=engine,
        params=params,
        file_hash=file_hash,
        cache_sig=cache_sig,
    )
    if rec is not None:
        _CACHE.update_result_path(rec, out_path)
        thumb = rec.thumb_path or out_path
    else:
        thumb = out_path

    return {
        "status": status or t("batch.task_done"),
        "out_path": out_path,
        "result_path": out_path,
        "thumb": thumb,
        "skipped": False,
        "stats": detect_stats,
        "recommendation": recommendation,
        "recommended_preset": recommended_preset,
    }


def _cv2_threshold(gray: np.ndarray):
    """延迟导入 cv2 做 Otsu 二值化（掩码文件夹模式用）。"""
    import cv2  # type: ignore
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)


def run_batch(
    folder_value,
    files_value,
    mask_folder_value,
    mode: str,
    engine: str,
    strength: float,
    dilate: int,
    feather: int,
    threshold: int,
    denoise: int,
    sensitivity: float,
    poisson_blend: bool,
    poisson_strength: float,
    texture_transfer: bool,
    texture_amount: float,
    artifact_reduce: bool,
    artifact_amount: float,
    superres: bool,
    superres_amount: float,
    real_superres: bool,
    real_scale: int,
    real_backend: str = config.REAL_SUPERRES_BACKEND,
    evaluate: bool = True,
    compare: bool = True,
    preset: str = config.DETECT_PRESET_DEFAULT,
    auto_preset: bool = True,
    dual_run: "bool | None" = None,
    progress: "gr.Progress" = gr.Progress(),
):
    """
    批量处理生成器：收集文件 → 入队 → 并发执行 → 流式回报进度。

    参数说明：
        progress      : Gradio 注入的进度条对象（顶部原生进度）。按 Gradio 6.x 约定，
                        该参数放在「所有普通输入之后」，且默认值为 gr.Progress() 实例，
                        Gradio 会自动注入，不占用 inputs 列表的位置。
        folder_value  : 文件夹导入组件的返回值。
        files_value   : 多文件导入组件的返回值。
        mask_folder_value : 手动掩码文件夹。
        mode/engine/... : 共享处理参数。

    产出（每次任务完成 yield 一次）：
        三元组 (进度比例: float, 日志文本: str, 结果画廊: list[(路径, 说明)])。
        画廊项在开启质量评估时为 (结果路径, "文件名 · 质量N分⚠️") 形式。
    """
    global _CURRENT_QUEUE

    # 把队列快照渲染成「正在处理」状态文本（阶段 15 批量进度可视化）。
    def _status_md(s: dict) -> str:
        return t(
            "batch.status_running",
            current=s.get("current") or "—",
            done=s["done"], failed=s["failed"],
            cancelled=s["cancelled"], total=s["total"],
        )

    file_paths = _normalize_file_value(folder_value) + _normalize_file_value(files_value)
    # 阶段 334：输入白名单回到 `config.SUPPORTED_INPUT_FORMATS`（此前误用 `_MASK_EXTS`，
    # 把 .tif/.tiff/.jpe/.jfif 静默丢掉）。被挡下的要写进日志，不能让文件凭空消失。
    img_paths, dropped = select_batch_inputs(file_paths)
    if dropped:
        log.warning("批量输入跳过 %d 个未支持扩展名：%s", len(dropped),
                    ", ".join(_os.path.basename(str(p)) for p in dropped[:5]))
    if not img_paths:
        yield 0.0, t("batch.msg.no_files"), [], t("batch.msg.no_files"), build_detection_stats_markdown(None)
        return

    # 阶段 334：`gr.File(file_count="directory")` 返回的是**目录里的每个文件**，不是目录。
    # 旧代码取 `[0]` 当目录用 ⇒ `isdir()` 恒假 ⇒ 手动掩码批量**每张都失败**。统一建索引，
    # 同时保留 `mask_folder`（真给目录时仍可用，且 `_mask_path_for` 的既有测试口径不变）。
    mask_paths = _normalize_file_value(mask_folder_value) if mask_folder_value else []
    mask_folder = mask_paths[0] if mask_paths else ""
    mask_index = mask_candidates(None, mask_paths)
    if mode == "manual" and not (mask_index or mask_folder):
        yield 0.0, t("batch.msg.need_mask"), [], t("batch.msg.need_mask"), \
            build_detection_stats_markdown(None)
        return

    # 入队。
    queue = BatchQueue()
    _CURRENT_QUEUE = queue
    rejected = 0
    for p in img_paths:
        try:
            queue.submit(_os.path.basename(p), p)
        except Exception as exc:  # noqa: BLE001 - 队列满等
            rejected += 1
            log.warning("任务被拒：%s（%s）", p, exc)

    total = queue.summary()["total"]
    if total == 0:
        yield 0.0, t("batch.msg.no_tasks", rejected=rejected), [], \
            t("batch.msg.no_tasks", rejected=rejected), build_detection_stats_markdown(None)
        return

    log_lines = [t("batch.start", total=total, rejected=rejected)]
    gallery: list = []
    # 清空上一次运行记录，仅保留本次批量产出的结果（供「导出本次报告」）。
    _batch_run_products.clear()
    # 阶段 38：清空上一次的对比配对表（与画廊同生命周期）。
    _batch_pairs.clear()

    # 阶段 23：累计整批检测的命中率统计（自动模式下才有值）。
    _agg_stats: list = []

    # 阶段 26：累计每张图智能推荐的预设分布（仅开启推荐时有值）。
    _preset_counts: dict = {}

    def _agg_stats_md() -> str:
        """把累计统计渲染成 Markdown（无自动检测时显示占位）。"""
        return build_aggregate_detection_stats_markdown(_agg_stats)

    def _on_update(task: Task) -> None:
        """每完成一个任务：追加日志、收集画廊项（含可选质量评估）。"""
        if task.status == TaskStatus.DONE:
            rd = task.result or {}
            name = task.name
            log_lines.append(t("batch.log_done", name=name,
                               status=rd.get("status") or t("batch.task_done")))
            # 阶段 23：累计本次检测的命中率统计。
            _st = rd.get("stats")
            if _st is not None:
                _agg_stats.append(_st)
            # 阶段 26/28：累计每张图智能推荐的预设 + 展示「推荐理由」（而非仅预设名）。
            _rec = rd.get("recommendation")
            _rp = (_rec or {}).get("preset") or rd.get("recommended_preset")
            if _rp:
                _preset_counts[_rp] = _preset_counts.get(_rp, 0) + 1
                if _rec:
                    # 阶段 28：单行紧凑理由（预设 + 置信度 + 触发特征）。
                    log_lines.append(t("batch.log_preset_reason", name=name,
                                        reason=reason_line(_rec)))
                else:
                    log_lines.append(t("batch.log_preset", name=name,
                                        preset=_preset_display(_rp)))
            rp = rd.get("result_path") or rd.get("out_path")
            if rp:
                caption = name
                # 可选质量评估：原图或结果缺失则跳过（不阻断批次）。
                if evaluate and _os.path.exists(task.path) and _os.path.exists(rp):
                    try:
                        q = evaluate_quality(task.path, rp)
                        icon = {"good": " ✅", "warn": " ⚠️", "review": " 🔍"}.get(q.level, "")
                        caption = t("batch.caption_quality", name=name,
                                    score=q.score, icon=icon)
                        log_lines.append(
                            # 阶段 337：级别与诊断都走 t()（此前把英文原值 `q.level` 与
                            # 服务层产的中文句子 `q.detail` 直接塞进翻译模板 ⇒ 中英界面各漏一半）。
                            t("batch.log_quality", name=name, score=q.score,
                              level=level_text(q.level), detail=detail_text(q))
                        )
                    except (AttributeError, NameError, TypeError):
                        # 阶段 337：**编程错误不许当「评估失败」吞掉**。
                        # 当天实测：改完 `QualityReport` 字段后，测试替身缺新字段 ⇒
                        # AttributeError 被下面那句 `log.warning` 吞成「质量评估失败（不影响结果）」，
                        # 用例照样全绿，而真实界面少一行诊断——静默降级正是最难发现的那种坏。
                        # 只有**数据/环境类**异常才允许降级（原图损坏、评估依赖缺失等）。
                        raise
                    except Exception as exc:  # noqa: BLE001
                        log.warning("质量评估失败（不影响结果）：%s", exc)
                # 「结果对比视图」：开启时用内存拼接的 Before|After 数组作为缩略图。
                if compare and _os.path.exists(task.path) and _os.path.exists(rp):
                    try:
                        orig_arr = load_image(task.path)
                        res_arr = load_image(rp)
                        arr = make_compare_array(orig_arr, res_arr)
                        gallery.append((arr, caption))
                    except Exception as exc:  # noqa: BLE001
                        log.warning("对比缩略图生成失败，退回结果图：%s", exc)
                        gallery.append((rp, caption))
                else:
                    gallery.append((rp, caption))
                # 阶段 38：与画廊严格同序登记配对，供「前后对比查看器」按索引定位。
                _batch_pairs.append((task.path, rp, name))
                # 记录本次运行产出，供「导出本次报告」使用。
                _batch_run_products.append(ReportSource(
                    original_path=task.path, result_path=rp,
                    original_name=name, engine=engine, mode=mode,
                    # 阶段 110：改用**共享 + i18n** 的摘要（此前这里硬编码中文、且只写 3 个参数
                    # ⇒ 与其它三处格式不一致，报告里同一语义有四种长相）。
                    params=repair_params_text(engine, strength, dilate, feather,
                                              threshold, denoise),
                    ts=_time.time(),
                    recommendation=rd.get("recommendation"),
                ))
        elif task.status == TaskStatus.FAILED:
            log_lines.append(t("batch.log_fail", name=task.name, error=task.error))
        elif task.status == TaskStatus.CANCELLED:
            log_lines.append(t("batch.log_cancel", name=task.name))

    # 处理器闭包：捕获本批次共享参数。
    def _processor(task: Task, on_progress):
        return _process_one(
            task, on_progress,
            mode=mode, engine=engine,
            strength=strength, dilate=dilate,
            feather=feather, threshold=threshold,
            denoise=denoise, sensitivity=sensitivity,
            mask_folder=mask_folder,
            mask_index=mask_index,
            preset=str(preset or config.DETECT_PRESET_DEFAULT),
            auto_preset=auto_preset,
            sharpen=params.sharpen.value,
            enable_postprocess=params.postprocess.value,
            postprocess_eval=bool(evaluate),
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
            dual_run=dual_run,
        )

    s = queue.summary()
    progress(0.0, t("batch.progress_queue", n=s["total"]))
    yield 0.0, "\n".join(log_lines[-200:]), list(gallery), _status_md(s), _agg_stats_md()

    # 阶段 193（D2）：整批处理耗时 = 从「开始跑队列」到「队列结束」的墙钟时间。
    # 不含前面的文件收集 / 入队（毫秒级准备），只量真正的修复工作量。
    _t0 = _time.perf_counter()
    try:
        for _finished_task in queue.run(_processor, on_update=_on_update):
            s = queue.summary()
            frac = s["progress"]
            progress(frac, t("batch.progress_done", done=s["finished"], total=s["total"]))
            yield frac, "\n".join(log_lines[-200:]), list(gallery), _status_md(s), _agg_stats_md()

        s = queue.summary()
        summary_line = t(
            "batch.summary",
            done=s["done"], failed=s["failed"],
            cancelled=s["cancelled"], total=s["total"],
        )
        log_lines.append(summary_line)
        # 阶段 193（D2）：把整批耗时拼进产物，与单图页 _compose_status 共用 format_elapsed 接缝。
        log_lines.append(format_elapsed(_time.perf_counter() - _t0, key="batch.msg.elapsed"))
        # 阶段 26：智能推荐预设分布（仅在开启推荐且有分布时展示）。
        if auto_preset and _preset_counts:
            _dist = t("batch.list_sep").join(
                f"{_preset_display(k)}×{v}" for k, v in _preset_counts.items()
            )
            log_lines.append(t("batch.log_preset_dist", dist=_dist))
        yield 1.0, "\n".join(log_lines[-200:]), list(gallery), _status_md(s), _agg_stats_md()
    finally:
        # 阶段 336：批次结束（含异常、含前端提前关闭生成器）必须把「当前队列」清空，
        # 否则 `cancel_batch()` 会对着空队列声称「已发送取消信号」。
        _CURRENT_QUEUE = None


def cancel_batch():
    """取消当前批次的剩余任务。

    阶段 336：旧实现只判 `_CURRENT_QUEUE is not None`，而它**从不置回 None** ⇒
    批跑结束后按「取消」（或快捷键 Shift+C）仍然回一句「⏹ 已发送取消信号…」，
    对着一个已经空了的队列声称做了事——对外可见的假陈述。
    现在两处一起收：跑完在 `finally` 里置回 None；再按队列**真实状态**判
    （没有 pending/running 就等于「没有正在运行的批量」，用既有的 `cancel_none` 文案，不新增键）。
    """
    global _CURRENT_QUEUE
    q = _CURRENT_QUEUE
    if q is None:
        return t("batch.msg.cancel_none")
    s = q.summary()
    if not ((s.get("pending") or 0) + (s.get("running") or 0)):
        # 队列已排空（跑完 / 已取消过）：说真话，别声称发了信号。
        _CURRENT_QUEUE = None
        return t("batch.msg.cancel_none")
    q.cancel()
    return t("batch.msg.cancel_done")


def on_gallery_select(evt: gr.SelectData):
    """
    阶段 38：批量结果画廊点选事件 —— 把该条目的配对送进「前后对比查看器」。

    参数说明：
        evt : Gradio 注入的 SelectData，含 .index（画廊中的位置）。
              形参显式标注 gr.SelectData，避免 Gradio 误判入参个数而告警。

    返回值：
        三元组 (pair_state 新值, 对比 HTML, 摘要 Markdown)。
        索引越界或本次批量尚无产出时，pair_state 置 None 并给出提示文案。
    """
    idx = getattr(evt, "index", None)
    # 部分 Gradio 版本对 Gallery 会给出 (row, col) 形式的索引，这里做一次规整。
    # 空序列（() / []）同样规整为 None——否则 `and idx` 判空放行后，
    # `int(())` 会抛 TypeError 而非按契约降级为「未选中」提示。
    if isinstance(idx, (tuple, list)):
        idx = idx[0] if idx else None
    if idx is None or not (0 <= int(idx) < len(_batch_pairs)):
        return None, "", t("compare.viewer.no_select")
    pair = _batch_pairs[int(idx)]
    html, summary = render_pair(pair, config.UI_COMPARE_DEFAULT_MODE)
    return pair, html, summary


def on_gallery_select_with_mode(evt: gr.SelectData, mode: str):
    """
    与 on_gallery_select 相同，但沿用用户当前所选的对比模式（而非默认模式）。

    参数说明：
        evt  : Gradio 注入的 SelectData。
        mode : 查看器 Radio 的当前值。

    返回值：
        三元组 (pair_state 新值, 对比 HTML, 摘要 Markdown)。
    """
    idx = getattr(evt, "index", None)
    # 与 on_gallery_select 同一规整（含空序列 → None，避免 int(()) 崩溃）。
    if isinstance(idx, (tuple, list)):
        idx = idx[0] if idx else None
    if idx is None or not (0 <= int(idx) < len(_batch_pairs)):
        return None, "", t("compare.viewer.no_select")
    pair = _batch_pairs[int(idx)]
    html, summary = render_pair(pair, mode)
    return pair, html, summary


def export_report_from_batch(fmt: str):
    """
    把「本次批量运行产出的成功结果」导出为修复报告（HTML / PDF）。

    与历史页不同，这里只导出「当次批量」的结果（_batch_run_products），
    不依赖历史缓存，适合「批量跑完立即交付」的闭环。

    返回值：
        (报告文件路径 or None, 提示信息)
    """
    if not _batch_run_products:
        return None, t("batch.msg.report_empty")
    try:
        path = export_report(_batch_run_products, fmt=(fmt or "html"))
        return path, t("batch.report_ok", path=path)
    except FileNotFoundError as exc:
        return None, t("batch.export_err", exc=format_exception_for_ui(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("导出批量报告失败")
        return None, t("batch.export_err", exc=format_exception_for_ui(exc))


def build_batch_tab():
    """构建「批量处理队列」标签页的全部 Gradio 组件并绑定回调。"""
    with gr.Row(equal_height=False):
        # ---------------- 左列：导入 + 参数 ----------------
        with gr.Column(scale=3):
            reg_value(gr.Markdown(t("batch.import_title")), "batch.import_title")
            folder_input = gr.File(
                label=t("batch.folder_label"),
                file_count="directory",
                file_types=["image"],
                type="filepath",
                elem_id="batch_folder",
            )
            reg_label(folder_input, "batch.folder_label")
            files_input = gr.File(
                label=t("batch.files_label"),
                file_count="multiple",
                file_types=["image"],
                type="filepath",
                elem_id="batch_files",
            )
            reg_label(files_input, "batch.files_label")

            mode = gr.Radio(
                choices=[(t("batch.mode_auto"), "auto"), (t("batch.mode_manual"), "manual")],
                value="auto",
                label=t("batch.mode_label"),
                info=t("batch.mode_info"),
            )
            register(mode, lambda: {
                "choices": [(t("batch.mode_auto"), "auto"), (t("batch.mode_manual"), "manual")],
                "label": t("batch.mode_label"),
                "info": t("batch.mode_info"),
            })
            # 阶段 21：检测模式（预设）下拉，一键适配 AI 生成图常见水印样式。
            # 仅在自动模式下有效（手动掩码模式由掩码文件夹驱动，忽略此下拉）。
            detect_preset = gr.Dropdown(
                choices=[(_preset_choice_label(p.name, p.display), p.name)
                         for p in list_presets()],
                value=config.DETECT_PRESET_DEFAULT,
                label=t("batch.detect_mode_label"),
                info=t("batch.detect_mode_info"),
                # 阶段 26：默认开启智能推荐，下拉默认禁用（避免误导）；关闭推荐后恢复可选。
                interactive=False,
            )
            reg_label_info(detect_preset, "batch.detect_mode_label", "batch.detect_mode_info")
            # 阶段 26：批量智能推荐开关（默认开启）。
            #   开启 → 忽略 detect_preset，为每张图独立推荐；禁用下拉避免误导。
            #   关闭 → 整批共用 detect_preset 所选预设，恢复下拉可选。
            auto_preset_toggle = gr.Checkbox(
                value=True,
                label=t("batch.auto_preset_label"),
                info=t("batch.auto_preset_info"),
            )
            reg_label_info(auto_preset_toggle, "batch.auto_preset_label", "batch.auto_preset_info")
            auto_preset_toggle.change(
                fn=lambda on: gr.update(interactive=not on),
                inputs=[auto_preset_toggle],
                outputs=[detect_preset],
            )
            mask_folder = gr.File(
                label=t("batch.mask_folder_label"),
                file_count="directory",
                type="filepath",
                visible=False,
            )
            reg_label(mask_folder, "batch.mask_folder_label")

            def _toggle_mask_folder(m):
                return (
                    gr.update(visible=(m == "manual")),
                    gr.update(visible=(m == "auto")),
                )

            mode.change(fn=_toggle_mask_folder, inputs=[mode], outputs=[mask_folder, detect_preset])

            # 复用阶段 6 的「修复参数面板」组件，避免与各标签页重复造轮子。
            params = build_repair_params(
                accordion_title="batch.params_accordion",
                open_=True,
            )

            evaluate_checkbox = gr.Checkbox(
                value=True,
                label=t("batch.evaluate_label"),
                info=t("batch.evaluate_info"),
            )
            reg_label_info(evaluate_checkbox, "batch.evaluate_label", "batch.evaluate_info")
            # 阶段 74/75：双跑择优（默认取配置；设置页可持久化默认值）。
            dual_run = gr.Checkbox(
                value=config.INPAINT_DUAL_RUN,
                label=t("params.dual_run_label"),
                info=t("params.dual_run_info"),
            )
            reg_label_info(dual_run, "params.dual_run_label", "params.dual_run_info")
            compare_toggle = gr.Checkbox(
                value=config.UI_COMPARE_VIEW,
                label=t("batch.compare_label"),
                info=t("batch.compare_info"),
            )
            reg_label_info(compare_toggle, "batch.compare_label", "batch.compare_info")

            with gr.Row():
                run_btn = gr.Button(
                    t("batch.run"), variant="primary", size="lg", elem_id="batch_run"
                )
                reg_value(run_btn, "batch.run")
                cancel_btn = gr.Button(
                    t("batch.cancel"), variant="stop", elem_id="batch_cancel"
                )
                reg_value(cancel_btn, "batch.cancel")

        # ---------------- 右列：进度 + 日志 + 画廊 ----------------
        with gr.Column(scale=2):
            reg_value(gr.Markdown(t("batch.progress_title")), "batch.progress_title")
            batch_status = gr.Markdown(
                value=t("batch.status_init"),
                label=t("batch.status_label"),
            )
            register(batch_status, lambda: {
                "value": t("batch.status_init"), "label": t("batch.status_label"),
            })
            batch_bar = gr.Slider(
                minimum=0.0, maximum=1.0, value=0.0,
                label=t("batch.bar_label"), interactive=False,
            )
            reg_label(batch_bar, "batch.bar_label")
            batch_log = gr.Textbox(
                label=t("batch.log_label"),
                lines=12, interactive=False,
                placeholder=t("batch.log_placeholder"),
            )
            reg_textbox(batch_log, "batch.log_label", "batch.log_placeholder")
            batch_gallery = gr.Gallery(
                label=t("batch.gallery_label"),
                columns=2, height=360, object_fit="contain",
            )
            reg_label(batch_gallery, "batch.gallery_label")

            # 阶段 38：批量结果的「前后对比查看器」——点选画廊任意一张即可
            # 逐张核查修复质量（拼接 / 滑块 / 热图三模式 + 差异摘要 + 导出）。
            batch_viewer = build_compare_viewer("batch", open_=False)

            # 阶段 23：整批检测的命中率统计（覆盖率 / 区域数 / 按类别命中 / OCR）。
            batch_detect_stats = gr.Markdown(
                value=build_detection_stats_markdown(None),
                label=t("detect.stats.label"),
            )
            reg_value(batch_detect_stats, "detect.stats.label")

            # 阶段 17：本次批量运行结果 → 一键导出修复报告。
            reg_value(gr.Markdown(t("batch.report_title")), "batch.report_title")
            with gr.Row():
                batch_report_fmt = gr.Radio(
                    choices=["HTML", "PDF"], value="HTML",
                    label=t("batch.report_fmt_label"),
                    info=t("batch.report_fmt_info"),
                )
                reg_label_info(batch_report_fmt, "batch.report_fmt_label", "batch.report_fmt_info")
                batch_report_btn = gr.Button(
                    t("batch.report_btn"), variant="primary"
                )
                reg_value(batch_report_btn, "batch.report_btn")
            batch_report_file = gr.File(
                label=t("batch.report_file_label"), interactive=False
            )
            reg_label(batch_report_file, "batch.report_file_label")

    # 绑定：批量处理（生成器流式输出）。输出新增 batch_status（正在处理提示）。
    run_btn.click(
        fn=run_batch,
        inputs=[
            folder_input, files_input, mask_folder, mode, params.engine,
            params.strength, params.dilate, params.feather, params.threshold,
            params.denoise, params.sensitivity,
            params.poisson_blend, params.poisson_strength,
            params.texture_transfer, params.texture_amount,
            params.artifact_reduce, params.artifact_amount,
            params.superres, params.superres_amount,
            params.real_superres, params.real_scale, params.real_backend,
            evaluate_checkbox, compare_toggle, detect_preset, auto_preset_toggle,
            dual_run,
        ],
        outputs=[batch_bar, batch_log, batch_gallery, batch_status, batch_detect_stats],
    )

    # 绑定：取消。
    cancel_btn.click(fn=cancel_batch, inputs=[], outputs=[batch_log])

    # 阶段 38：画廊点选 → 载入该条目到对比查看器（沿用用户当前所选模式）。
    batch_gallery.select(
        fn=on_gallery_select_with_mode,
        inputs=[batch_viewer.mode],
        outputs=[batch_viewer.pair_state, batch_viewer.html, batch_viewer.summary],
    )

    # 绑定：导出本次批量报告（HTML / PDF）。
    batch_report_btn.click(
        fn=export_report_from_batch,
        inputs=[batch_report_fmt],
        outputs=[batch_report_file, batch_status],
    )
