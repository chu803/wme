# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/report_service.py
 文件说明 : 修复报告生成服务（阶段 17 核心服务，服务层）。

            把一批处理结果「汇总成一份可交付的报告」：
              · HTML ：自包含单文件（Before|After 对比图以 base64 内嵌，
                       无需附带图片即可双击打开；含汇总统计与每条明细）。
              · PDF  ：用 PyMuPDF（fitz）生成真实 PDF，排版与 HTML 一致，
                       便于归档 / 打印（中文走内嵌 CJK 字体）。

            数据来源：传入的「成功记录」列表（HistoryRecord 或等价对象），
            每条取出 original_path / result_path / original_name / engine /
            mode / params / ts，复用 export_service.make_compare_array 拼对比图，
            复用 quality_service.evaluate_quality 打质量分（超大图自动降采样）。

            设计约束：
            · 服务层模块，严禁 import ui / gradio。
            · 不引入新第三方依赖（cv2 已具备；PDF 用环境已有的 PyMuPDF）。
==============================================================================
"""

from __future__ import annotations

import base64
import html as _html
import os as _os
import sys as _sys
import time as _time
from dataclasses import dataclass, field
from typing import Optional, Sequence

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import cv2  # type: ignore

import config
from core.image_utils import load_image
from services.export_service import make_compare_array
from services.quality_service import evaluate_quality, QualityReport
from utils.logger import get_logger
from utils.path_helper import make_unique_path, build_output_filename
from utils.recommend_view import reason_line, bars_html
from utils.i18n import t, get_language  # 阶段 169：报告交付物文案双语化（跟随全局语言）

log = get_logger("services.report_service")


@dataclass
class ReportSource:
    """
    一条报告数据源（与 HistoryRecord 字段对齐，便于直接传 HistoryRecord）。

    报告只读取以下属性，因此任何具备这些属性的对象都能作为来源：
        status / result_path / original_path / original_name /
        engine / mode / params / ts
    """

    original_path: Optional[str] = None
    result_path: Optional[str] = None
    original_name: str = "image"
    engine: str = "auto"
    mode: str = "auto"
    params: str = ""
    ts: float = 0.0
    status: str = "success"
    recommendation: Optional[dict] = None  # 阶段 28：智能推荐理由（Recommendation.to_dict()）


def _src_attr(src, name: str, default=None):
    """安全读取来源属性（兼容 HistoryRecord 与 ReportSource）。"""
    return getattr(src, name, default)


def _ensure_report_dir() -> _os.PathLike:
    _os.makedirs(config.EXPORT_DIR, exist_ok=True)
    return config.EXPORT_DIR


def _encode_png(rgb: np.ndarray) -> bytes:
    """把 RGB 数组编码为 PNG 字节。"""
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError(t("report.err_png_encode"))
    return buf.tobytes()


def _maybe_downscale(rgb: np.ndarray, max_side: int) -> np.ndarray:
    if max_side and max(rgb.shape[:2]) > max_side:
        scale = max_side / max(rgb.shape[:2])
        h, w = rgb.shape[:2]
        return cv2.resize(
            rgb,
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    return rgb


def _compare_png(original_path: str, result_path: str, max_side: int) -> tuple[bytes, int, int]:
    """生成 Before|After 对比图 PNG 字节，返回 (bytes, 宽, 高)。"""
    o = load_image(original_path)        # 命中阶段16解码缓存
    r = load_image(result_path)
    arr = make_compare_array(o, r)
    arr = _maybe_downscale(arr, max_side)
    return _encode_png(arr), arr.shape[1], arr.shape[0]


def _single_png(result_path: str, max_side: int) -> tuple[bytes, int, int]:
    """原图缺失时，仅用结果图生成 PNG 字节，返回 (bytes, 宽, 高)。"""
    r = load_image(result_path)
    r = _maybe_downscale(r, max_side)
    return _encode_png(r), r.shape[1], r.shape[0]


def build_entries(
    sources: Sequence,
    *,
    include_quality: Optional[bool] = None,
    max_compare_side: Optional[int] = None,
) -> list[dict]:
    """
    把来源记录组装成报告条目列表（只保留「成功且结果文件存在」的记录）。

    参数说明：
        sources          : HistoryRecord / ReportSource 列表。
        include_quality  : 是否计算质量分；缺省取 config.REPORT_INCLUDE_QUALITY。
        max_compare_side : 对比图最大边长；缺省取 config.REPORT_COMPARE_MAX_SIDE。

    返回值：
        条目字典列表，每个含：
            name, engine, mode, params, time_str,
            img_b64(对比/结果图 base64), img_w, img_h, img_is_compare,
            quality(QualityReport|None), result_path
    """
    include_quality = config.REPORT_INCLUDE_QUALITY if include_quality is None else include_quality
    max_side = config.REPORT_COMPARE_MAX_SIDE if max_compare_side is None else max_compare_side

    entries: list[dict] = []
    for src in sources:
        status = _src_attr(src, "status", "success")
        result_path = _src_attr(src, "result_path", None)
        if status != "success" or not result_path or not _os.path.exists(result_path):
            continue
        original_path = _src_attr(src, "original_path", None)
        name = _src_attr(src, "original_name", "image") or "image"
        engine = _src_attr(src, "engine", "auto") or "auto"
        mode = _src_attr(src, "mode", "auto") or "auto"
        params = _src_attr(src, "params", "") or ""
        ts = _src_attr(src, "ts", 0.0) or 0.0
        recommendation = _src_attr(src, "recommendation", None)

        try:
            if original_path and _os.path.exists(original_path):
                png, w, h = _compare_png(original_path, result_path, max_side)
                is_compare = True
            else:
                png, w, h = _single_png(result_path, max_side)
                is_compare = False
        except Exception as exc:  # noqa: BLE001
            log.warning(t("report.log_compare_fail", exc=str(exc)))
            continue

        quality: Optional[QualityReport] = None
        if include_quality and original_path and _os.path.exists(original_path):
            try:
                quality = evaluate_quality(original_path, result_path)
            except Exception as exc:  # noqa: BLE001
                log.warning(t("report.log_quality_fail", exc=str(exc)))

        entries.append({
            "name": name,
            "engine": engine,
            "mode": mode,
            "params": params,
            "time_str": _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts)) if ts else "—",
            "img_b64": base64.b64encode(png).decode("ascii"),
            "img_w": w,
            "img_h": h,
            "img_is_compare": is_compare,
            "quality": quality,
            "result_path": result_path,
            "recommendation": recommendation,
        })
    return entries


# ==============================================================================
# HTML 报告
# ==============================================================================
def _summary_stats(entries: list[dict]) -> dict:
    """
    汇总统计（HTML / PDF 共用的唯一真值来源）。

    除 good / warn / review 外，额外给出 assessed（已评估条数）与
    unassessed（未评估条数 = total - assessed）。这两项是必需的：
    「质量未评估」有两条真实来路——include_quality 关闭（纯交付报告），
    以及逐条 evaluate_quality 抛错后被 except 吞掉、quality 留 None——
    它与「评估结果一条都不是 good」完全不是一回事。不区分就会把
    「未评估」渲染成「良好 0 · 告警 0 · 待复核 0」，读者按字面理解
    只能得出「全部都待复核」的结论，而报告里根本没有这条信息。
    """
    total = len(entries)
    good = warn = review = 0
    scores = []
    for e in entries:
        q = e.get("quality")
        if q is not None:
            scores.append(q.score)
            if q.level == "good":
                good += 1
            elif q.level == "warn":
                warn += 1
            else:
                review += 1
    assessed = good + warn + review
    avg = round(sum(scores) / len(scores), 1) if scores else None
    return {
        "total": total,
        "good": good,
        "warn": warn,
        "review": review,
        "assessed": assessed,
        "unassessed": total - assessed,
        "avg": avg,
    }


def _quality_summary(stats: dict) -> dict:
    """
    把质量统计翻译成「渲染层可直接消费」的段列表——**HTML 与 PDF 的唯一口径**。

    返回：
        mode   : "empty"（无条目）/ "none"（一条都没评估）/ "partial" / "full"
        counts : [(标签, 数值)]；mode 为 "empty"/"none" 时为空列表
        note   : 非计数型的整段说明（仅 mode == "none" 有值）
        avg    : 平均质量分；无任何评估时为 None

    历史缺陷（阶段 88）：HTML 与 PDF 各自拼汇总字符串，于是
      · include_quality=False 时，HTML 显示「良好 0 · 告警 0 · 待复核 0」
        （总条数却是 3），PDF 却显示「本报告未包含质量评估」——同一份数据两种结论；
      · 部分条目评估失败时，两边**都**静默漏计：3 条里只有 1 条算出质量，
        汇总只报「良好 1 · 告警 0 · 待复核 0」，与同文档内逐条明细的
        「未评估质量」自相矛盾。
    改为由本函数统一决定「有哪些段」之后，任何一侧都无法再与另一侧不一致；
    渲染差异（HTML 加粗、PDF 纯文本）只留在渲染层。
    """
    if stats["total"] == 0:
        return {"mode": "empty", "counts": [], "note": "", "avg": None}
    if stats["assessed"] == 0:
        return {
            "mode": "none",
            "counts": [],
            "note": t("report.note_no_quality"),
            "avg": None,
        }
    counts = [
        (t("report.quality_good"), stats["good"]),
        (t("report.quality_warn"), stats["warn"]),
        (t("report.quality_review"), stats["review"]),
    ]
    # 部分未评估时必须显式列出「未评估 N」，否则三项之和 < 总条数与读者对不上。
    if stats["unassessed"]:
        counts.append((t("report.quality_unassessed"), stats["unassessed"]))
    return {
        "mode": "partial" if stats["unassessed"] else "full",
        "counts": counts,
        "note": "",
        "avg": stats["avg"],
    }


def _card_html(e: dict) -> str:
    img = f'<img src="data:image/png;base64,{e["img_b64"]}" alt="{_html.escape(t("report.alt_compare"))}"/>'
    q = e["quality"]
    if q is not None:
        qtxt = (f'<span class="lvl lvl-{_html.escape(q.level)}">'
                f'{_html.escape(t("report.quality_score", score=q.score, level=t(f"report.quality_{q.level}")))}</span>')
    else:
        qtxt = f'<span class="lvl">{t("report.unassessed_quality")}</span>'
    # 阶段 28：智能推荐理由可视化（分数条 + 单行理由）。
    rec_html = ""
    rec = e.get("recommendation")
    if rec:
        rl = reason_line(rec)
        if rl:
            bars = bars_html(rec)
            rec_html = (
                f'<div class="row rec">{_html.escape(rl)}</div>'
                + (f'<div class="recbars">{bars}</div>' if bars else "")
            )
    return f"""
    <div class="card">
      <div class="img">{img}</div>
      <div class="meta">
        <div class="name" title="{_html.escape(e['name'])}">{_html.escape(e['name'])}</div>
        <div class="row">{t('report.lbl_mode_engine')}{_html.escape(e['mode'])} / {_html.escape(e['engine'])}</div>
        <div class="row">{t('report.lbl_time')}{_html.escape(e['time_str'])}</div>
        <div class="row">{t('report.lbl_params')}{_html.escape(e['params'] or '—')}</div>
        <div class="row">{qtxt}</div>
        {rec_html}
      </div>
    </div>"""


def generate_html(entries: list[dict], *, title: Optional[str] = None, out_path: Optional[str] = None) -> str:
    """
    生成自包含 HTML 报告（图片 base64 内嵌），写入文件并返回路径。

    参数说明：
        entries : build_entries 的产物。
        title   : 报告标题；缺省取 config.APP_NAME + 版本。
        out_path: 指定输出路径；缺省落到 EXPORT_DIR，文件名含时间戳。

    返回值：写出文件的绝对路径。
    """
    _ensure_report_dir()
    lang_code = "en-US" if get_language() == "en-US" else "zh-CN"
    if out_path is None:
        out_path = make_unique_path(config.EXPORT_DIR, build_output_filename("report", "html"))

    title = title or t("report.title", app_name=config.APP_NAME)
    stats = _summary_stats(entries)
    qsum = _quality_summary(stats)
    gen_time = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())
    # 汇总行由 _quality_summary 统一给出「有哪些段」，此处只负责加粗渲染，
    # 不再自行判断「有没有质量评估」——否则会与 PDF 分叉（阶段 88 缺陷）。
    stat_line = t("report.summary_total", n=stats['total'])
    if qsum["counts"]:
        stat_line += " · " + " · ".join(
            f"{k} <b>{v}</b>" for k, v in qsum["counts"]
        )
        if qsum["avg"] is not None:
            stat_line += t("report.summary_avg", avg=qsum['avg'])
    elif qsum["note"]:
        stat_line += " · " + _html.escape(qsum["note"])
    cards = "\n".join(_card_html(e) for e in entries) or f'<p class="empty">{t("report.empty_records")}</p>'

    html_doc = f"""<!doctype html>
<html lang="{lang_code}">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f5f7fa; color: #1f2937; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 28px 20px 60px; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }}
  .sub {{ color: #6b7280; font-size: 13px; margin-bottom: 18px; }}
  .summary {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
            padding: 14px 18px; font-size: 15px; margin-bottom: 22px; }}
  .gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
             gap: 18px; }}
  .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
          overflow: hidden; display: flex; flex-direction: column; }}
  .card .img {{ background: #111; display: flex; justify-content: center; }}
  .card .img img {{ max-width: 100%; height: auto; display: block; }}
  .meta {{ padding: 12px 14px; font-size: 13px; }}
  .name {{ font-weight: 700; font-size: 14px; margin-bottom: 6px;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .row {{ color: #4b5563; line-height: 1.7; }}
  .lvl {{ display: inline-block; margin-top: 4px; padding: 2px 8px; border-radius: 999px;
          font-size: 12px; background: #eef2ff; color: #3730a3; }}
  .lvl-good {{ background: #ecfdf5; color: #047857; }}
  .lvl-warn {{ background: #fffbeb; color: #b45309; }}
  .lvl-review {{ background: #fef2f2; color: #b91c1c; }}
  .rec {{ color: #4338ca; font-weight: 600; }}
  .recbars {{ margin: 2px 0 4px; }}
  .empty {{ color: #9ca3af; }}
</style>
</head>
<body>
  <div class="wrap">
    <h1>{_html.escape(title)}</h1>
    <div class="sub">{t('report.pdf_gen_time')}{_html.escape(gen_time)} · {_html.escape(config.APP_NAME)} v{_html.escape(str(config.APP_VERSION))}</div>
    <div class="summary">{stat_line}</div>
    <div class="gallery">{cards}</div>
  </div>
</body>
</html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    log.info(t("report.log_html_done", path=out_path, n=len(entries)))
    return str(out_path)


# ==============================================================================
# PDF 报告（PyMuPDF / fitz）
# ==============================================================================
def generate_pdf(
    entries: list[dict],
    *,
    title: Optional[str] = None,
    out_path: Optional[str] = None,
    records_per_page: Optional[int] = None,
) -> str:
    """
    生成 PDF 报告（PyMuPDF），写入文件并返回路径。

    参数说明：
        entries         : build_entries 的产物。
        title           : 报告标题。
        out_path        : 指定输出路径；缺省落到 EXPORT_DIR。
        records_per_page: 每页记录数；缺省取 config.REPORT_PDF_RECORDS_PER_PAGE。

    返回值：写出文件的绝对路径。
    """
    import fitz  # 延迟导入，避免无 PyMuPDF 时整个模块不可用

    _ensure_report_dir()
    if out_path is None:
        out_path = make_unique_path(config.EXPORT_DIR, build_output_filename("report", "pdf"))
    per_page = config.REPORT_PDF_RECORDS_PER_PAGE if records_per_page is None else int(records_per_page)
    # 阶段 166：每页条数须 ≥ 1。此前 `per_page <= 0` 会让分页判据 `placed_on_page >= per_page`
    # **恒真** ⇒ 分页静默退化成「一图一页」（负值同样）；数量类参数缺下界即显式拒绝
    # （与阶段 161 queue_worker 的 retry 下界校验同族）。
    if per_page < 1:
        raise ValueError(f"records_per_page 必须 ≥ 1，得到 {per_page}")

    # 内嵌 CJK 字体（简体中文内置字体名），失败则退回 ASCII 基础字体。
    try:
        fitz.Font("china-s")
        cjk_name = "china-s"
    except Exception:  # noqa: BLE001
        cjk_name = "helv"

    doc = fitz.open()
    page_w, page_h = fitz.paper_size("a4")  # (595, 842)
    margin = 36
    avail_w = page_w - 2 * margin

    title = title or t("report.title", app_name=config.APP_NAME)
    stats = _summary_stats(entries)
    qsum = _quality_summary(stats)
    gen_time = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime())

    def _write_text(page, x, y, text, size=10, fontname="helv", color=(0, 0, 0)):
        page.insert_text((x, y), text, fontsize=size, fontname=fontname, color=color)

    # 封面/汇总页。
    page = doc.new_page(width=page_w, height=page_h)
    _write_text(page, margin, 54, title, size=20, fontname=cjk_name)
    _write_text(page, margin, 80, f"{t('report.pdf_gen_time')}{gen_time}", size=11, fontname=cjk_name)
    _write_text(page, margin, 100, t("report.pdf_total", n=stats['total']), size=13, fontname=cjk_name)
    y = 124
    # 与 HTML 共用 _quality_summary 的段列表：两边都报同一组口径，
    # 未评估时都给说明、部分未评估时都补「未评估 N」。
    if qsum["counts"]:
        lines = [t("report.pdf_quality_overview") + " · ".join(f"{k} {v}" for k, v in qsum["counts"])]
        if qsum["avg"] is not None:
            lines.append(f"{t('report.pdf_avg_score')}{qsum['avg']}")
    elif qsum["note"]:
        lines = [qsum["note"]]
    else:
        lines = []
    for ln in lines:
        _write_text(page, margin, y, ln, size=12, fontname=cjk_name)
        y += 20

    # 逐条记录。
    cursor_y = margin
    page = doc.new_page(width=page_w, height=page_h)
    placed_on_page = 0

    for e in entries:
        png = base64.b64decode(e["img_b64"])
        iw, ih = e["img_w"], e["img_h"]
        scale = avail_w / iw
        draw_w = avail_w
        draw_h = ih * scale
        # 单图高度上限，避免一张图占满整页。
        max_img_h = (page_h - 2 * margin) * 0.62
        if draw_h > max_img_h:
            draw_h = max_img_h
            draw_w = iw * (draw_h / ih)

        q = e["quality"]
        qtxt = (t("report.quality_score", score=q.score, level=t(f"report.quality_{q.level}")) if q is not None
                else t("report.unassessed_quality"))
        _sep = "　" if get_language() == "zh-CN" else " "
        text_lines = [
            f"{t('report.lbl_name')}{e['name'][:60]}",
            f"{t('report.lbl_mode_engine')}{e['mode']} / {e['engine']}{_sep}{t('report.lbl_time')}{e['time_str']}",
            f"{t('report.lbl_params')}{(e['params'] or '—')[:80]}",
            f"{qtxt}",
        ]
        # 阶段 28：推荐理由（预设 + 置信度 + 触发特征）一并写入 PDF 文本。
        rec_rl = reason_line(e.get("recommendation"))
        if rec_rl:
            text_lines.append(f"{t('report.lbl_recommend')}{rec_rl}")
        text_h = len(text_lines) * 14 + 6
        block_h = draw_h + text_h + 16

        # 换页：封面后每页最多 per_page 条；或本块放不下。
        if placed_on_page >= per_page or cursor_y + block_h > page_h - margin:
            page = doc.new_page(width=page_w, height=page_h)
            cursor_y = margin
            placed_on_page = 0

        x = margin + (avail_w - draw_w) / 2
        rect = fitz.Rect(x, cursor_y, x + draw_w, cursor_y + draw_h)
        try:
            page.insert_image(rect, stream=png)
        except Exception as exc:  # noqa: BLE001
            log.warning(t("report.log_pdf_img_fail", exc=str(exc)))

        ty = cursor_y + draw_h + 12
        for ln in text_lines:
            _write_text(page, margin, ty, ln, size=10, fontname=cjk_name)
            ty += 14
        cursor_y = ty + 16
        placed_on_page += 1

    doc.save(out_path)
    log.info(t("report.log_pdf_done", path=out_path, n=len(entries)))
    return str(out_path)


# ==============================================================================
# 统一入口
# ==============================================================================
def export_report(
    sources: Sequence,
    *,
    fmt: str = "html",
    out_path: Optional[str] = None,
    include_quality: Optional[bool] = None,
    max_compare_side: Optional[int] = None,
) -> str:
    """
    一键导出修复报告（HTML 或 PDF）。

    参数说明：
        sources    : 来源记录列表（HistoryRecord / ReportSource）。
        fmt        : "html" 或 "pdf"。
        out_path   : 指定输出路径（可选）。
        include_quality / max_compare_side : 透传给 build_entries。

    返回值：写出文件的绝对路径。

    说明：PDF 在 PyMuPDF 不可用时自动回退为 HTML，并在文件名标注。
    """
    entries = build_entries(sources, include_quality=include_quality, max_compare_side=max_compare_side)
    if not entries:
        raise FileNotFoundError(t("report.err_no_records"))

    fmt = (fmt or "html").lower()
    if fmt == "pdf":
        try:
            return generate_pdf(entries, out_path=out_path)
        except ImportError:
            log.warning(t("report.log_pdf_fallback"))
            fmt = "html"
    # 回退或默认 HTML：若原定路径是 .pdf，改写扩展名避免「HTML 内容配 PDF 名」。
    if fmt == "html" and out_path and out_path.lower().endswith(".pdf"):
        out_path = out_path[:-4] + ".html"
    return generate_html(entries, out_path=out_path)


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    import tempfile

    cv2_mod = cv2
    # 造两张合成图。
    tmp_o = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_r = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2_mod.imwrite(tmp_o.name, np.full((200, 300, 3), 235, dtype=np.uint8))
    cv2_mod.imwrite(tmp_r.name, np.full((200, 300, 3), 60, dtype=np.uint8))
    tmp_o.close()
    tmp_r.close()

    srcs = [ReportSource(original_path=tmp_o.name, result_path=tmp_r.name,
                         original_name="测试图.png", engine="opencv", mode="auto",
                         params="力度=50", ts=_time.time())]
    # 自检产物写到临时目录，避免污染项目 exports/。
    html_tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    pdf_tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    html_tmp.close()
    pdf_tmp.close()
    html_path = export_report(srcs, fmt="html", out_path=html_tmp.name)
    pdf_path = export_report(srcs, fmt="pdf", out_path=pdf_tmp.name)
    assert _os.path.exists(html_path) and html_path.lower().endswith(".html")
    assert _os.path.exists(pdf_path) and pdf_path.lower().endswith(".pdf")
    # 校验 HTML 含 base64 内嵌与标题。
    with open(html_path, "r", encoding="utf-8") as f:
        html_text = f.read()
    assert "data:image/png;base64," in html_text and "修复报告" in html_text
    print(f"用例1通过：HTML 报告 {html_path}")
    print(f"用例2通过：PDF 报告 {pdf_path}")

    for p in (tmp_o.name, tmp_r.name, html_path, pdf_path):
        try:
            _os.remove(p)
        except OSError:
            pass
    print("report_service.py 自检全部通过。")
