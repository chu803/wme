# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/tab_history.py
 文件说明 : 历史记录标签页（阶段 5 核心界面）。
            展示「文件缓存」中保存的近期处理记录：缩略图画廊浏览、
            点击查看详情、下载结果、用当前参数重新处理、删除单条、清空全部。

            数据来自 services.cache_service.CacheService（本地 JSON 索引 +
            缩略图目录），与批量处理共用同一套缓存，保证「处理完即入库、
            历史页即可回访」的闭环。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
import time as _time

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import gradio as gr

# 阶段 147：本文件在 3 处注解里用了 `Optional[str]`，但**从未导入 typing**
#   —— 之所以一直没炸，是因为第 18 行的 `from __future__ import annotations` 让
#   所有注解延迟为字符串、永不求值。运行时静默，但 `typing.get_type_hints()` /
#   文档生成 / 类型检查器会立刻 `NameError`。由 `scripts/undef_scan.py` 发现。
from typing import Optional

import config
from utils.format_utils import format_bytes
from utils.i18n import t
from ui.i18n_rt import reg_label, reg_label_info, reg_value
from core.detector import detect_watermark_mask
from core.image_utils import load_image
from services.cache_service import CacheService, HistoryRecord, get_cache
from services.export_service import export_history_as_zip, make_compare_array
from services.file_service import make_thumbnail, save_thumbnail
from services.report_service import export_report
from ui.components import get_engine_choices, repair_params_text_short
from ui.compare_view import build_compare_viewer, render_pair
from ui.tab_auto import process_auto_repair
from ui.tab_batch import _build_editor_value
from utils.logger import get_logger
from utils.exceptions import format_exception_for_ui

log = get_logger("ui.tab_history")

_CACHE = get_cache()
# 模块级「画廊顺序 → 记录 id」映射，供 select 事件按索引定位记录。
_HISTORY_ID_MAP: list = []
# 最近一次选中的记录 id（由 on_select 写入），供删除/重处理按钮使用。
_LAST_SELECTED_ID: str = ""


def _format_time(ts: float) -> str:
    """把时间戳格式化为可读字符串。"""
    return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts))


def _fmt_dur(sec: float) -> str:
    """把秒数格式化为人话（与 tab_media._human_seconds 同口径）。"""
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.1f} 秒"
    return f"{int(sec // 60)} 分 {sec - sec // 60 * 60:.0f} 秒"


def _fmt_bytes(n: int) -> str:
    """
    把字节数格式化为人类可读字符串（B / KB / MB / GB / TB）。

    阶段 48 重构：实现已下沉到 utils.format_utils.format_bytes（纯函数、
    零第三方依赖），此处保留同名包装，供本模块既有的「磁盘占用」面板调用，
    确保与设置页「磁盘清理」面板的输出格式完全一致。
    """
    return format_bytes(n)


def build_disk_usage_md() -> str:
    """
    阶段 47 新增：生成历史结果「磁盘占用」统计 Markdown（随语言变化）。
    展示总体积 / 已配置上限 / 按类型拆分（图片 / 视频 / PDF）。
    """
    try:
        stats = _CACHE.get_stats()
    except Exception:  # noqa: BLE001
        stats = {"total_bytes": 0, "total_count": 0, "by_kind": {}}
    total = int(stats.get("total_bytes", 0))
    count = int(stats.get("total_count", 0))
    cap = int(getattr(config, "CACHE_MAX_BYTES", 0))
    by_kind = stats.get("by_kind", {}) or {}
    kind_label = {
        "image": t("history.filter_image"),
        "video": t("history.filter_video"),
        "pdf": t("history.filter_pdf"),
    }
    parts = [
        t("history.disk_usage.title"),
        t("history.disk_usage.total",
          total=_fmt_bytes(total), count=count, cap=_fmt_bytes(cap)),
    ]
    for k in ("image", "video", "pdf"):
        d = by_kind.get(k)
        if d and int(d.get("count", 0)):
            parts.append(
                t("history.disk_usage.kind",
                  kind=kind_label.get(k, k),
                  count=int(d["count"]),
                  size=_fmt_bytes(int(d["bytes"])))
            )
    return "\n".join(parts)


def load_gallery(compare: bool = False, kind_filter: str = "all",
                 query: Optional[str] = None) -> tuple[list, str]:
    """
    读取缓存记录，组装画廊数据与统计文本。

    参数说明：
        compare     : 是否启用「结果对比视图」，开启时缩略图改为
                      「原图 | 处理后」内存拼接数组（受 config.UI_COMPARE_VIEW 控制）。
        kind_filter : 阶段 41 新增 —— 类型筛选；"all" 不过滤，
                      否则仅展示 getattr(r, "kind", "image") == kind_filter 的记录。
        query       : 阶段 42 新增 —— 文件名模糊匹配；空/None 忽略，
                      否则仅展示 original_name 含 query（不区分大小写）的记录；
                      与 kind_filter 叠加生效。

    返回值：
        (gallery_value, info_text)
        gallery_value : list[(缩略图路径, 标题)]，供 gr.Gallery 展示。
        info_text     : 统计信息文本。
    """
    global _HISTORY_ID_MAP
    # 未筛选且无关键词时走无参 list_records()，保持与既有桩实现（如 lambda:[rec]）
    # 兼容；仅在实际筛选时才透传 kind=/query=，避免对无参 list_records 桩造成 TypeError。
    kind = None if kind_filter in (None, "all") else kind_filter
    q = (query or "").strip()
    if kind is None and not q:
        records = _CACHE.list_records()
    else:
        records = _CACHE.list_records(kind=kind, query=q if q else None)
    _HISTORY_ID_MAP = [(r.id, r.thumb_path or r.result_path) for r in records]
    gallery_value = []
    for r in records:
        thumb = r.thumb_path or r.result_path
        if not thumb or not _os.path.exists(thumb):
            thumb = r.result_path
        if not thumb or not _os.path.exists(thumb):
            continue
        caption = f"{r.original_name}｜{r.mode}/{r.engine}"
        # 阶段 37 / 40：视频、PDF 记录用醒目徽章，区别于图片记录。
        _kind = getattr(r, "kind", "image")
        if _kind == "video":
            caption = f"🎬 {r.original_name}｜视频"
        elif _kind == "pdf":
            caption = f"📄 {r.original_name}｜PDF · {getattr(r, 'page_count', 0) or 0}页"
        # 「结果对比视图」：仅图片记录适用（视频/PDF 为媒体文件，无法用
        # load_image 拼 Before|After，直接展示其缩略图即可）。
        if compare and _kind == "image" and r.original_path and _os.path.exists(r.original_path):
            try:
                orig_arr = load_image(r.original_path)
                res_arr = load_image(thumb)
                arr = make_compare_array(orig_arr, res_arr)
                gallery_value.append((arr, caption))
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("历史对比缩略图生成失败，退回普通缩略图：%s", exc)
        gallery_value.append((thumb, caption))
    if records:
        info = t("history.count", n=len(records)) + t("history.tip_select")
        # 阶段 42：命中筛选时追加一行「筛选条件」提示，便于用户感知当前视图范围。
        if kind is not None or q:
            info += t(
                "history.filter_active",
                kind=(t("history.filter_all") if kind is None else kind),
                q=(q or ""),
                n=len(records),
            )
    else:
        info = t("history.info_empty")
    return gallery_value, info


def _record_detail(rec: HistoryRecord) -> str:
    """生成单条记录的详情 Markdown。"""
    status_icon = "✅" if rec.status == "success" else "❌"
    detail = (
        t("history.detail.title", icon=status_icon, name=rec.original_name) + "\n"
        + t("history.detail.id", id=rec.id) + "\n"
        + t("history.detail.time", time=_format_time(rec.ts)) + "\n"
        + t("history.detail.mode_engine", mode=rec.mode, engine=rec.engine) + "\n"
        + t("history.detail.status", status=rec.status) + "\n"
        + t("history.detail.params", params=rec.params or "—") + "\n"
        + t("history.detail.result", result=rec.result_path or "—") + "\n"
    )
    # 阶段 47：图片 / 视频记录展示结果体积（PDF 在其专属分支已展示）。
    if getattr(rec, "kind", "image") in ("image", "video"):
        sz = getattr(rec, "file_size", 0) or 0
        if not sz and rec.result_path and _os.path.exists(rec.result_path):
            try:
                sz = _os.path.getsize(rec.result_path)
            except OSError:
                sz = 0
        if sz:
            detail += t("history.detail.size", size=_fmt_bytes(sz)) + "\n"
    # 阶段 37：视频记录展示元信息与提示（分辨率/帧率/帧数/时长/是否超分）。
    if getattr(rec, "kind", "image") == "video":
        up = "已超分放大" if getattr(rec, "upscaled", False) else "未超分"
        detail += (
            t("history.detail.video",
              w=getattr(rec, "width", 0) or 0,
              h=getattr(rec, "height", 0) or 0,
              fps=getattr(rec, "fps", 0.0) or 0.0,
              frames=getattr(rec, "frames", 0) or 0,
              dur=_fmt_dur(getattr(rec, "duration", 0.0) or 0.0),
              upscaled=up)
            + "\n"
            + t("history.detail.video_hint") + "\n"
        )
    # 阶段 40：PDF 记录展示元信息（页数 / 结果体积）与提示。
    elif getattr(rec, "kind", "image") == "pdf":
        detail += (
            t("history.detail.pdf",
              pages=getattr(rec, "page_count", 0) or 0,
              size_mb=round((getattr(rec, "file_size", 0) or 0) / 1024 / 1024, 2))
            + "\n"
            + t("history.detail.pdf_hint") + "\n"
        )
    return detail


def on_select(evt: gr.SelectData, mode: "str | None" = None):
    """
    画廊选中事件：按索引从 _HISTORY_ID_MAP 定位记录并展示详情。

    参数说明：
        evt  : Gradio 注入的 SelectData，含 .index（选中位置）。
        mode : 阶段 38 新增 —— 对比查看器当前所选模式（concat/slider/heatmap）；
               缺省取 config.UI_COMPARE_DEFAULT_MODE。

    返回值：
        六元组（前三项与阶段 37 语义完全一致，后三项为阶段 38 新增）：
          1. 详情 Markdown；
          2. 结果文件路径 or None（供下载）；
          3. 视频预览路径 or None（图片记录为 None）；
          4. 对比配对 (原图, 结果, 名称) or None（视频记录为 None）；
          5. 对比区 HTML；
          6. 对比摘要 Markdown。
    """
    global _LAST_SELECTED_ID
    m = mode or config.UI_COMPARE_DEFAULT_MODE
    idx = getattr(evt, "index", None)
    if isinstance(idx, (tuple, list)) and idx:
        idx = idx[0]
    if idx is None or not (0 <= int(idx) < len(_HISTORY_ID_MAP)):
        return t("history.msg.no_select"), None, None, None, "", t("compare.viewer.no_select")
    rec_id = _HISTORY_ID_MAP[int(idx)][0]
    _LAST_SELECTED_ID = rec_id
    rec = _CACHE.get(rec_id)
    if rec is None:
        return t("history.not_found"), None, None, None, "", t("compare.viewer.no_select")

    is_video = getattr(rec, "kind", "image") == "video"
    is_pdf = getattr(rec, "kind", "image") == "pdf"
    video_path = rec.video_path if is_video else None
    # 媒体型记录（视频 / PDF）不走图片对比查看器，引导到对应媒体页。
    if is_video or is_pdf:
        hint = t("compare.viewer.video_hint") if is_video else t("compare.viewer.pdf_hint")
        return (_record_detail(rec), rec.result_path, video_path,
                None, "", hint)

    pair = (rec.original_path, rec.result_path, rec.original_name)
    html, summary = render_pair(pair, m)
    return _record_detail(rec), rec.result_path, video_path, pair, html, summary


def delete_selected(rec_id: str, compare: bool = False, confirm: bool = False):
    """
    删除一条记录并刷新画廊。阶段 43：增加 confirm 二次确认守卫——
    未勾选确认复选框时拒绝执行，避免误删。
    """
    if not confirm:
        return (t("history.msg.confirm_needed"), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    if not rec_id:
        return (t("history.msg.no_select"), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    ok = _CACHE.delete(rec_id)
    msg = t("history.msg.deleted") if ok else t("history.msg.delete_fail")
    gallery, _ = load_gallery(compare=compare)
    # 执行后复位确认复选框，强制下次再确认。
    return msg, gallery, None, "", t("compare.viewer.no_select"), gr.update(value=False)


def clear_all(compare: bool = False, confirm: bool = False):
    """
    清空全部历史记录。阶段 43：增加 confirm 二次确认守卫；返回六元组
    （与 delete_selected / delete_filtered 对齐），最后一项复位确认复选框。
    """
    if not confirm:
        return (t("history.msg.confirm_needed"), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    n = _CACHE.clear()
    gallery, _ = load_gallery(compare=compare)
    return (t("history.clear_ok", n=n), gallery, None, "",
            t("compare.viewer.no_select"), gr.update(value=False))


def delete_filtered(compare: bool = False, confirm: bool = False,
                    kind_filter: str = "all", query: Optional[str] = None):
    """
    阶段 43：批量删除「当前筛选视图」下的全部记录（类型 + 文件名关键词），
    并刷新画廊。必须经确认复选框二次确认，且删除前再次以同一过滤口径核对，
    保证「所见即所删」。

    返回值（六元组，与 delete_selected 对齐）：
        1. 信息文本；
        2. 画廊数据；
        3. 对比配对 or None（删除后复位查看器）；
        4. 对比 HTML；
        5. 对比摘要 Markdown；
        6. 确认复选框复位（gr.update(value=False)）。
    """
    if not confirm:
        return (t("history.delete_filtered_warn"), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    kind = None if kind_filter in (None, "all") else kind_filter
    q = (query or "").strip() or None
    n = _CACHE.delete_by_filter(kind=kind, query=q)
    gallery, _ = load_gallery(compare=compare)
    msg = t("history.delete_filtered_ok", n=n) if n else t("history.delete_filtered_empty")
    return msg, gallery, None, "", t("compare.viewer.no_select"), gr.update(value=False)


def prune_by_policy(compare: bool = False, confirm: bool = False):
    """
    阶段 46：按当前保留策略（保留天数 / 最大条数）清理过期与超额记录，并刷新画廊。
    必须经确认复选框二次确认，防止误清。

    返回值（六元组，与 delete_filtered 对齐）：
        1. 信息文本；
        2. 画廊数据；
        3. 对比配对 or None（清理后复位查看器）；
        4. 对比 HTML；
        5. 对比摘要 Markdown；
        6. 确认复选框复位（gr.update(value=False)）。
    """
    if not confirm:
        return (t("history.msg.confirm_needed"), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    try:
        n = _CACHE.prune()
    except Exception as exc:  # noqa: BLE001
        log.exception("按策略清理历史失败")
        return (t("history.msg.reprocess_fail", exc=format_exception_for_ui(exc)), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    gallery, _ = load_gallery(compare=compare)
    msg = t("history.prune_policy_ok", n=n) if n else t("history.prune_policy_none")
    return msg, gallery, None, "", t("compare.viewer.no_select"), gr.update(value=False)


def prune_by_size_policy(compare: bool = False, confirm: bool = False):
    """
    阶段 47：按「总体积上限」（config.CACHE_MAX_BYTES）从最旧记录开始清理，
    直至剩余总体积低于上限，并刷新画廊。必须经确认复选框二次确认，防止误清。

    返回值（六元组，与 prune_by_policy 对齐）：
        1. 信息文本；
        2. 画廊数据；
        3. 对比配对 or None（清理后复位查看器）；
        4. 对比 HTML；
        5. 对比摘要 Markdown；
        6. 确认复选框复位（gr.update(value=False)）。
    """
    if not confirm:
        return (t("history.msg.confirm_needed"), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    try:
        n = _CACHE.prune_by_size(config.CACHE_MAX_BYTES)
    except Exception as exc:  # noqa: BLE001
        log.exception("按体积上限清理历史失败")
        return (t("history.msg.reprocess_fail", exc=format_exception_for_ui(exc)), load_gallery(compare=compare)[0],
                None, "", t("compare.viewer.no_select"), gr.update(value=False))
    gallery, _ = load_gallery(compare=compare)
    msg = t("history.prune_size_ok", n=n) if n else t("history.prune_size_none")
    return msg, gallery, None, "", t("compare.viewer.no_select"), gr.update(value=False)


def reprocess(rec_id: str, engine: str, strength: float, denoise: int, compare: bool = False):
    """
    用当前参数重新处理所选记录的原图（重新检测 + 修复），并更新缓存。

    返回值：
        (信息文本, 画廊数据)
    """
    if not rec_id:
        return t("history.msg.no_select"), load_gallery(compare=compare)[0]
    rec = _CACHE.get(rec_id)
    # 阶段 37 / 40：视频、PDF 记录为媒体型，不支持「重新处理」，分别引导回对应媒体页。
    if getattr(rec, "kind", "image") == "video":
        return t("history.detail.video_hint"), load_gallery(compare=compare)[0]
    if getattr(rec, "kind", "image") == "pdf":
        return t("history.detail.pdf_hint"), load_gallery(compare=compare)[0]
    if rec is None or not rec.original_path or not _os.path.exists(rec.original_path):
        return t("history.msg.no_original"), load_gallery(compare=compare)[0]

    try:
        image = load_image(rec.original_path)
        res = detect_watermark_mask(image, use_ocr=config.DETECT_USE_OCR)
        mask = res.mask
        if int(mask.sum()) == 0:
            return t("history.msg.reprocess_no_mask"), load_gallery(compare=compare)[0]
        editor_value = _build_editor_value(image, mask)
        _, result, status, _, out_path = process_auto_repair(
            editor_value,
            engine=engine,
            strength=strength,
            dilate=config.MASK_DILATE_DEFAULT,
            feather=config.MASK_FEATHER_DEFAULT,
            threshold=config.MASK_THRESHOLD_DEFAULT,
            denoise=denoise,
            enable_postprocess=True,
            record=False,
        )
        if result is None or not out_path:
            return t("history.reprocess_no_result"), load_gallery(compare=compare)[0]

        # 更新缩略图与结果路径，并持久化。
        thumb = make_thumbnail(result, size=config.CACHE_THUMBNAIL_SIZE)
        new_thumb = str(save_thumbnail(thumb, rec.original_name))
        rec.result_path = out_path
        rec.thumb_path = new_thumb
        rec.engine = engine
        rec.status = "success"
        # 阶段 110：摘要改走**共享 + i18n**（此前硬编码中文，会被报告层渲染出来）。
        rec.params = repair_params_text_short(engine, strength, denoise)
        _CACHE.update_record(rec)

        gallery, _ = load_gallery(compare=compare)
        return t("history.reprocess_done", name=rec.original_name, status=status), gallery
    except Exception as exc:  # noqa: BLE001
        log.exception("重新处理失败")
        return t("history.msg.reprocess_fail", exc=format_exception_for_ui(exc)), load_gallery(compare=compare)[0]


def reprocess_and_refresh(
    rec_id: str, engine: str, strength: float, denoise: int,
    compare: bool = False, mode: "str | None" = None,
):
    """
    阶段 38：重新处理所选记录，并同步把「新结果」刷进对比查看器。

    参数说明：
        rec_id / engine / strength / denoise / compare : 同 reprocess。
        mode : 对比查看器当前所选模式。

    返回值：
        五元组 (信息文本, 画廊数据, 对比配对 or None, 对比 HTML, 摘要 Markdown)。
        重处理失败、记录为视频或原图缺失时，查看器复位为「未选择」态。
    """
    msg, gallery = reprocess(rec_id, engine, strength, denoise, compare)
    rec = _CACHE.get(rec_id) if rec_id else None
    if (rec is None or getattr(rec, "kind", "image") in ("video", "pdf")
            or not rec.original_path or not _os.path.isfile(rec.original_path)
            or not rec.result_path or not _os.path.isfile(rec.result_path)):
        return msg, gallery, None, "", t("compare.viewer.no_select")
    pair = (rec.original_path, rec.result_path, rec.original_name)
    html, summary = render_pair(pair, mode or config.UI_COMPARE_DEFAULT_MODE)
    return msg, gallery, pair, html, summary


def export_report_from_history(fmt: str):
    """
    把当前历史记录（成功结果）导出为修复报告（HTML / PDF）。

    返回值：
        (报告文件路径 or None, 提示信息)
    """
    records = _CACHE.list_records()
    try:
        path = export_report(records, fmt=(fmt or "html"))
        return path, t("batch.report_ok", path=path)
    except FileNotFoundError as exc:
        return None, t("batch.export_err", exc=format_exception_for_ui(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("导出修复报告失败")
        return None, t("batch.export_err", exc=format_exception_for_ui(exc))


def export_history_filtered_zip(kind_filter: str = "all",
                                 query: Optional[str] = None):
    """
    阶段 41/42：按当前类型筛选 + 文件名关键词，把对应「成功结果」打包为 ZIP 供一键下载。

    参数说明：
        kind_filter : "all" 导出全部；否则仅导出该类型（image/video/pdf）的结果。
        query       : 文件名模糊匹配关键词；空/None 忽略，与 kind_filter 叠加生效。

    返回值：
        (ZIP 路径 or None, 提示信息)
    """
    kind = None if kind_filter in (None, "all") else kind_filter
    q = (query or "").strip() or None
    try:
        path = export_history_as_zip(_CACHE, kind=kind, query=q)
        return path, t("history.export_filtered_ok", path=path)
    except FileNotFoundError as exc:
        return None, t("history.export_filtered_empty", kind=(kind_filter or "all"))
    except Exception as exc:  # noqa: BLE001
        log.exception("按类型导出 ZIP 失败")
        return None, t("batch.export_err", exc=format_exception_for_ui(exc))


def build_history_tab():
    """构建「历史记录」标签页的全部 Gradio 组件并绑定回调。"""
    init_gallery, init_info = load_gallery()

    with gr.Column():
        reg_value(gr.Markdown(t("history.title")), "history.title")
        compare_toggle = gr.Checkbox(
            value=config.UI_COMPARE_VIEW,
            label=t("history.compare_label"),
            info=t("history.compare_info"),
        )
        reg_label_info(compare_toggle, "history.compare_label", "history.compare_info")
        with gr.Row():
            refresh_btn = gr.Button(
                t("history.refresh"), variant="secondary", elem_id="history_refresh"
            )
            reg_value(refresh_btn, "history.refresh")
            clear_btn = gr.Button(
                t("history.clear"), variant="stop", elem_id="history_clear"
            )
            reg_value(clear_btn, "history.clear")
        # 阶段 41：历史记录类型筛选（全部 / 图片 / 视频 / PDF）。
        kind_filter = gr.Dropdown(
            choices=[
                (t("history.filter_all"), "all"),
                (t("history.filter_image"), "image"),
                (t("history.filter_video"), "video"),
                (t("history.filter_pdf"), "pdf"),
            ],
            value="all",
            label=t("history.filter_label"),
            info=t("history.filter_info"),
        )
        reg_label_info(kind_filter, "history.filter_label", "history.filter_info")
        # 阶段 42：历史记录文件名搜索框（与「类型筛选」叠加生效）。
        search_box = gr.Textbox(
            label=t("history.search_label"),
            placeholder=t("history.search_placeholder"),
            info=t("history.search_info"),
            scale=2,
        )
        reg_label_info(search_box, "history.search_label", "history.search_info")
        # 阶段 47：磁盘占用统计面板（随语言变化；由下方 Timer 定时刷新）。
        disk_usage = gr.Markdown(value=build_disk_usage_md(),
                                 label=t("history.disk_usage.title"))
        reg_label(disk_usage, "history.disk_usage.title")
        with gr.Row():
            report_fmt = gr.Radio(
                choices=["HTML", "PDF"], value="HTML",
                label=t("history.report_fmt_label"),
                info=t("history.report_fmt_info"),
            )
            reg_label_info(report_fmt, "history.report_fmt_label", "history.report_fmt_info")
            report_btn = gr.Button(t("history.report_btn"), variant="primary")
            reg_value(report_btn, "history.report_btn")
        report_file = gr.File(label=t("history.report_file_label"), interactive=False)
        reg_label(report_file, "history.report_file_label")
        # 阶段 41：按当前筛选类型打包下载全部结果。
        export_filtered_btn = gr.Button(
            t("history.export_filtered_btn"), variant="secondary"
        )
        reg_value(export_filtered_btn, "history.export_filtered_btn")
        export_zip_file = gr.File(
            label=t("history.export_filtered_file_label"), interactive=False
        )
        reg_label(export_zip_file, "history.export_filtered_file_label")
        # 阶段 43：批量删除当前筛选结果 + 二次确认复选框。
        # 阶段 46：新增「按策略清理」（复用同一 confirm_cb 守卫，避免误清）。
        # confirm_cb 同时守护上方的「清空全部」、下方的「删除所选」与「按策略清理」。
        with gr.Row():
            delete_filtered_btn = gr.Button(
                t("history.delete_filtered_btn"), variant="stop"
            )
            reg_value(delete_filtered_btn, "history.delete_filtered_btn")
            prune_policy_btn = gr.Button(
                t("history.prune_policy_btn"), variant="secondary"
            )
            reg_value(prune_policy_btn, "history.prune_policy_btn")
            prune_size_btn = gr.Button(
                t("history.prune_size_btn"), variant="secondary"
            )
            reg_value(prune_size_btn, "history.prune_size_btn")
            confirm_cb = gr.Checkbox(
                value=False,
                label=t("history.delete_filtered_confirm"),
            )
            reg_label(confirm_cb, "history.delete_filtered_confirm")
        with gr.Row(equal_height=False):
            with gr.Column(scale=2):
                gallery = gr.Gallery(
                    value=init_gallery,
                    label=t("history.gallery_label"),
                    columns=3, height=420, object_fit="contain",
                )
                reg_label(gallery, "history.gallery_label")

                # 阶段 38：历史记录的「前后对比查看器」——点选任意一条历史结果，
                # 即可回看当时的原图 vs 结果（三模式 + 差异摘要 + 导出对比图）。
                hist_viewer = build_compare_viewer("history", open_=False)
            with gr.Column(scale=1):
                detail = gr.Markdown(value=init_info, label=t("history.detail_label"))
                reg_label(detail, "history.detail_label")
                download_file = gr.File(label=t("history.download_label"), interactive=False)
                reg_label(download_file, "history.download_label")
                # 阶段 37：视频记录选中时展示结果视频预览（图片记录为空）。
                video_preview = gr.Video(label=t("history.video_preview_label"),
                                         interactive=False, visible=True)
                reg_label(video_preview, "history.video_preview_label")
                with gr.Accordion(t("history.reprocess_accordion"), open=False) as _rep_acc:
                    reg_label(_rep_acc, "history.reprocess_accordion")
                    engine = gr.Radio(
                        choices=get_engine_choices(),
                        value=config.INPAINT_ENGINE,
                        label=t("params.engine_label"),
                        info=t("params.engine_info"),
                    )
                    reg_label_info(engine, "params.engine_label", "params.engine_info")
                    strength = gr.Slider(
                        minimum=config.INPAINT_STRENGTH_MIN,
                        maximum=config.INPAINT_STRENGTH_MAX,
                        value=config.INPAINT_STRENGTH_DEFAULT,
                        step=config.INPAINT_STRENGTH_STEP,
                        label=t("params.strength_label"),
                    )
                    reg_label_info(strength, "params.strength_label", "params.strength_info")
                    denoise = gr.Slider(
                        minimum=config.DENOISE_STRENGTH_MIN,
                        maximum=config.DENOISE_STRENGTH_MAX,
                        value=config.DENOISE_STRENGTH_DEFAULT,
                        step=config.DENOISE_STRENGTH_STEP,
                        label=t("params.denoise_label"),
                    )
                    reg_label_info(denoise, "params.denoise_label", "params.denoise_info")
                reprocess_btn = gr.Button(
                    t("history.reprocess_btn"), variant="primary"
                )
                reg_value(reprocess_btn, "history.reprocess_btn")
                delete_btn = gr.Button(t("history.delete_btn"), variant="stop")
                reg_value(delete_btn, "history.delete_btn")

        # 选中事件：展示详情 + 准备下载 + 视频预览 + 记录 id，
        # 并（阶段 38）同步把该条目载入「前后对比查看器」。
        gallery.select(
            fn=on_select,
            inputs=[hist_viewer.mode],
            outputs=[detail, download_file, video_preview,
                     hist_viewer.pair_state, hist_viewer.html, hist_viewer.summary],
        )

        # 刷新（带对比视图开关 + 类型筛选 + 文件名搜索）。
        refresh_btn.click(
            fn=lambda c, kf, q: load_gallery(compare=c, kind_filter=kf, query=q),
            inputs=[compare_toggle, kind_filter, search_box],
            outputs=[gallery, detail],
        )

        # 阶段 41/42：切换类型筛选或输入搜索关键词时即时重载画廊与详情。
        kind_filter.change(
            fn=lambda kf, c, q: load_gallery(compare=c, kind_filter=kf, query=q),
            inputs=[kind_filter, compare_toggle, search_box],
            outputs=[gallery, detail],
        )
        # 阶段 42：搜索框回车提交 / 输入变化即时筛选。
        search_box.submit(
            fn=lambda c, kf, q: load_gallery(compare=c, kind_filter=kf, query=q),
            inputs=[compare_toggle, kind_filter, search_box],
            outputs=[gallery, detail],
        )
        search_box.change(
            fn=lambda c, kf, q: load_gallery(compare=c, kind_filter=kf, query=q),
            inputs=[compare_toggle, kind_filter, search_box],
            outputs=[gallery, detail],
        )

        # 阶段 41/42：按当前筛选类型 + 文件名关键词打包下载结果。
        export_filtered_btn.click(
            fn=export_history_filtered_zip,
            inputs=[kind_filter, search_box],
            outputs=[export_zip_file, detail],
        )

        # 清空（带对比视图开关 + 二次确认）；同时复位查看器与确认框。
        clear_btn.click(
            fn=lambda c, conf: clear_all(compare=c, confirm=conf),
            inputs=[compare_toggle, confirm_cb],
            outputs=[detail, gallery, hist_viewer.pair_state,
                     hist_viewer.html, hist_viewer.summary, confirm_cb],
        )

        # 导出修复报告（HTML / PDF）。
        report_btn.click(
            fn=export_report_from_history,
            inputs=[report_fmt],
            outputs=[report_file, detail],
        )

        # 删除所选（依赖 on_select 写入的 _LAST_SELECTED_ID）；带二次确认，
        # 同时复位查看器与确认框。
        delete_btn.click(
            fn=lambda c, conf: delete_selected(_LAST_SELECTED_ID, c, conf),
            inputs=[compare_toggle, confirm_cb],
            outputs=[detail, gallery, hist_viewer.pair_state,
                     hist_viewer.html, hist_viewer.summary, confirm_cb],
        )

        # 阶段 43：批量删除当前筛选结果（类型 + 文件名关键词），带二次确认；
        # 删除后复位查看器与确认框，并刷新画廊。
        delete_filtered_btn.click(
            fn=delete_filtered,
            inputs=[compare_toggle, confirm_cb, kind_filter, search_box],
            outputs=[detail, gallery, hist_viewer.pair_state,
                     hist_viewer.html, hist_viewer.summary, confirm_cb],
        )

        # 阶段 46：按保留策略清理（保留天数 / 最大条数），复用同一 confirm_cb 守卫；
        # 清理后复位查看器与确认框，并刷新画廊。
        prune_policy_btn.click(
            fn=lambda c, conf: prune_by_policy(compare=c, confirm=conf),
            inputs=[compare_toggle, confirm_cb],
            outputs=[detail, gallery, hist_viewer.pair_state,
                     hist_viewer.html, hist_viewer.summary, confirm_cb],
        )

        # 阶段 47：按「总体积上限」清理（从最旧记录删起），复用同一 confirm_cb 守卫；
        # 清理后复位查看器与确认框，并刷新画廊。
        prune_size_btn.click(
            fn=lambda c, conf: prune_by_size_policy(compare=c, confirm=conf),
            inputs=[compare_toggle, confirm_cb],
            outputs=[detail, gallery, hist_viewer.pair_state,
                     hist_viewer.html, hist_viewer.summary, confirm_cb],
        )

        # 阶段 47：磁盘占用面板定时刷新（与清理/删除操作解耦，始终反映最新占用）。
        _disk_timer = gr.Timer(5.0)
        _disk_timer.tick(fn=build_disk_usage_md, outputs=[disk_usage])

        # 重新处理所选（带对比视图开关）；处理完把新结果刷进查看器。
        reprocess_btn.click(
            fn=lambda e, s, d, c, m: reprocess_and_refresh(_LAST_SELECTED_ID, e, s, d, c, m),
            inputs=[engine, strength, denoise, compare_toggle, hist_viewer.mode],
            outputs=[detail, gallery, hist_viewer.pair_state,
                     hist_viewer.html, hist_viewer.summary],
        )


# ==============================================================================
# 模块自检（阶段 139）
# ==============================================================================
# 阶段 139：tab_history 原无 __main__ 自检 —— 工具函数（_fmt_dur 边界 /
# _format_time / _record_detail / build_disk_usage_md）从未被任何自动化验证。
# 范式与阶段 138 components 一致：自检必须在不依赖 gradio UI 启动的前提下
# 真正构造数据并跑断言；任何异常必须 SystemExit 非零退出，不允许静默吞掉。
def _run_selfcheck() -> None:
    # 1. _fmt_dur 边界（秒 → 分·秒；同口径见 tab_media._human_seconds）
    assert _fmt_dur(0.0) == "0.0 秒"
    assert _fmt_dur(59.9) == "59.9 秒"
    assert _fmt_dur(60.0) == "1 分 0 秒"
    assert _fmt_dur(125.0) == "2 分 5 秒"
    assert _fmt_dur(3661.5).startswith("61 分 ")

    # 2. _format_time 格式（YYYY-MM-DD HH:MM:SS）
    s = _format_time(1700000000.0)
    assert len(s) == 19 and s[4] == "-" and s[7] == "-" and s[10] == " " \
        and s[13] == ":" and s[16] == ":", f"_format_time 格式异常: {s!r}"

    # 3. _fmt_bytes 字节级（与 utils.format_utils 同一实现，<1024 输出整数）
    assert _fmt_bytes(0) == "0 B"
    assert _fmt_bytes(1024) == "1.00 KB"

    # 4. _record_detail 合成记录（不依赖磁盘/cv2；只验本地字符串组装契约）
    from services.cache_service import HistoryRecord
    rec = HistoryRecord(
        id="t1", ts=1700000000.0, original_name="a.png",
        original_path=None, result_path=None, thumb_path=None,
        mode="auto", engine="lama", status="success",
        params="", file_size=1234,
    )
    d = _record_detail(rec)
    assert "✅" in d and "a.png" in d and "lama" in d and "auto" in d, d

    # 5. build_disk_usage_md 桩缓存（替换模块级 _CACHE，跑完恢复）
    class _StubCache:
        def get_stats(self):
            return {
                "total_bytes": 2048, "total_count": 3,
                "by_kind": {"image": {"count": 3, "bytes": 2048}},
            }
    import sys as _sys
    _mod = _sys.modules[__name__]
    _real_cache, _mod._CACHE = _mod._CACHE, _StubCache()
    try:
        md = build_disk_usage_md()
        assert "1.00 KB" in md or "2.00 KB" in md, f"磁盘占用统计未含 KB 值：{md!r}"
    finally:
        _mod._CACHE = _real_cache


if __name__ == "__main__":
    try:
        _run_selfcheck()
        print("ui/tab_history.py 自检通过。")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"ui/tab_history.py 自检失败：{exc}")
