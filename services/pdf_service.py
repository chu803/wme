# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/pdf_service.py
 文件说明 : PDF 文档水印擦除服务（阶段 10 新增）。

            核心思路（三步走，零基础也能看懂）：
                第 1 步「拆」：把 PDF 的每一页按指定精度(DPI)渲染成一张图片；
                第 2 步「修」：把这张图片交给 auto_pipeline.process_image 去水印；
                第 3 步「拼」：把修好的图片按原页面尺寸放回新 PDF 的对应页。

            为什么是「渲染成图片」而不是「直接删 PDF 里的水印对象」？
                因为绝大多数水印是「画」在页面上的（图片型水印、扫描件水印），
                PDF 里根本没有可删除的独立对象；即便有，删除后也会留下白洞。
                走「渲染 → AI 修复 → 回写」这条路，对任何 PDF 都通用。

            代价与提醒：
                · 输出 PDF 的文字将不可再被选中/搜索（因为整页变成了图片）；
                · 输出体积通常会变大，可通过 config.PDF_PAGE_IMAGE_FMT=JPEG
                  与 PDF_JPEG_QUALITY 控制。

            分层约束：本模块属于 services 层，只调用 core / utils / config
            与同层的 auto_pipeline，**不依赖 gradio**，因此可被 GUI / CLI /
            程序库 / REST 四套入口共同复用。
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
# 路径加固：保证无论从哪个目录启动，都能 import 到项目根下的模块。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import cv2

import config
from core.device_manager import select_device
from services.auto_pipeline import process_image
from utils.path_helper import make_unique_path, ensure_parent_dir
from utils.logger import get_logger

log = get_logger("services.pdf")

# 进度回调类型：接收 (进度 0~1, 中文提示语)。
ProgressCallback = Callable[[float, str], None]


def _noop_progress(_p: float, _msg: str) -> None:
    """空进度回调：调用方没传时用它兜底，省去到处写 if。"""
    return None


# ==============================================================================
# 第 1 节：依赖探测（PyMuPDF 是可选依赖，缺失时给出人话提示而非崩溃）
# ==============================================================================
def _import_fitz():
    """
    导入 PyMuPDF（模块名叫 fitz）。

    返回值：
        fitz 模块对象。

    异常：
        RuntimeError —— 未安装时抛出，附带一句可直接复制的安装命令。
    """
    try:
        import fitz  # PyMuPDF
        return fitz
    except ImportError as exc:
        raise RuntimeError(
            "PDF 功能需要 PyMuPDF 库，但当前环境未安装。\n"
            "请在命令行执行：pip install PyMuPDF\n"
            f"（原始错误：{exc}）"
        ) from exc


def is_pdf_available() -> bool:
    """
    探测当前环境是否具备 PDF 处理能力。

    返回值：
        True 表示 PyMuPDF 可用；False 表示未安装。

    用途：
        界面层用它决定「PDF 区域」是否置灰；api.get_capabilities() 也会调用。
    """
    try:
        _import_fitz()
        return True
    except RuntimeError:
        return False


def get_pdf_version() -> str:
    """返回 PyMuPDF 版本号字符串；不可用时返回 "未安装"。

    阶段 131 订正：此前返回 `fitz.__doc__`（含 Python 版本与平台的多行文档串，
    如 "PyMuPDF 1.28.0: Python bindings…\\nPython 3.13 running on…"），
    与「版本号字符串」的声明不符；现优先取 `fitz.__version__`。
    """
    try:
        fitz = _import_fitz()
        ver = getattr(fitz, "__version__", None)
        if ver:
            return str(ver)
        # 回退：fitz.version 是 (PyMuPDF 版本, MuPDF 版本, 构建日期) 的元组。
        vtuple = getattr(fitz, "version", None)
        if vtuple:
            return str(vtuple[0])
        return "已安装"
    except RuntimeError:
        return "未安装"


def is_pdf_file(path: str | _os.PathLike) -> bool:
    """按扩展名判断是不是 PDF 文件（不读取文件内容，纯字符串判断）。"""
    return _os.path.splitext(str(path))[1].lower() in config.SUPPORTED_DOC_FORMATS


# ==============================================================================
# 第 2 节：结果数据结构
# ==============================================================================
class PdfAbortOnFailedPage(RuntimeError):
    """**专用**中止信号：只在 `config.PDF_KEEP_FAILED_PAGE=False` 且某页真的失败时才抛。

    为什么不能复用裸 `RuntimeError`（阶段 341）：逐页 `try` 外面写的是
    `except RuntimeError: raise  # 由 PDF_KEEP_FAILED_PAGE=False 主动抛出的中断`，
    可这一层包住的是**整段**逐页处理，而 PyMuPDF 的渲染错误、
    `_encode_page_image` 的「编码彻底失败」（`:316`）也都是 `RuntimeError`
    ⇒ 一页坏就把整份文档中止，恰好违反同一函数里「单页异常不该毁掉整份文档」那句设计说明，
    也无视了 `PDF_KEEP_FAILED_PAGE=True`（默认值）。
    判据：**「我要主动中止」和「事情出错了」不能共用一个异常类型**（同 #319 的宽口子族）。
    """


@dataclass
class PageResult:
    """单页的处理结果。"""

    index: int = 0                   # 页码（从 1 开始，方便普通用户理解）
    status: str = "skipped"          # success / skipped / failed
    mask_pixels: int = 0             # 该页检测到的水印像素数
    regions: int = 0                 # 该页检测到的水印区域数
    coverage_ratio: float = 0.0      # 该页掩码覆盖率（阶段 23）
    by_kind: "dict" = None           # 该页按类别命中统计（阶段 23）
    used_ocr: bool = False           # 该页是否启用 OCR（阶段 23）
    stats: "object" = None           # 阶段 23：DetectionStats（整页统计）
    error: Optional[str] = None      # 失败/跳过原因
    seconds: float = 0.0             # 该页耗时（秒）
    recommended_preset: Optional[str] = None  # 阶段 27：逐页智能推荐的预设名
    recommendation: Optional[dict] = None     # 阶段 28：逐页智能推荐理由（to_dict）

    def __str__(self) -> str:
        tail = f" ({self.error})" if self.error else ""
        return f"第{self.index}页 [{self.status}] 像素={self.mask_pixels}{tail}"


@dataclass
class PdfResult:
    """整份 PDF 的处理结果汇总。"""

    src_path: str = ""                                   # 源文件路径
    out_path: Optional[str] = None                       # 结果 PDF 路径
    total_pages: int = 0                                 # 源文档总页数
    processed_pages: int = 0                             # 实际尝试处理的页数
    success_pages: int = 0                               # 成功修复的页数
    skipped_pages: int = 0                               # 未检出水印而跳过的页数
    failed_pages: int = 0                                # 处理失败的页数
    seconds: float = 0.0                                 # 总耗时
    status: str = "failed"                               # success / failed
    error: Optional[str] = None                          # 整体失败原因
    pages: List[PageResult] = field(default_factory=list)  # 逐页明细

    def summary(self) -> str:
        """生成一句人话总结，界面和命令行都能直接打印。"""
        if self.status != "success":
            return f"PDF 处理失败：{self.error}"
        return (
            f"共 {self.total_pages} 页，处理 {self.processed_pages} 页 → "
            f"修复 {self.success_pages} 页、无水印跳过 {self.skipped_pages} 页、"
            f"失败 {self.failed_pages} 页，耗时 {self.seconds:.1f} 秒。"
        )


# ==============================================================================
# 第 3 节：内部工具函数
# ==============================================================================
def _parse_pages(spec: Optional[str], total: int) -> List[int]:
    """
    把用户写的页码范围字符串解析成「从 0 开始」的页索引列表。

    参数说明：
        spec  : 形如 "1-5"、"1,3,7"、"2-4,9"、"all" 或 None（表示全部）。
        total : 文档实际总页数。

    返回值：
        去重且升序的页索引列表（内部用 0 基）。

    零基础提示：
        用户输入的页码是从 1 开始数的（第 1 页），而程序内部从 0 开始数，
        所以这里统一做了减 1 的换算。
    """
    if not spec or str(spec).strip().lower() in ("all", "全部", "*"):
        return list(range(total))

    picked: set[int] = set()
    for chunk in str(spec).replace("，", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            # 范围写法：3-8
            left, _, right = chunk.partition("-")
            try:
                start = int(left.strip())
                end = int(right.strip())
            except ValueError:
                log.warning("忽略无法识别的页码片段：%s", chunk)
                continue
            if start > end:
                start, end = end, start
            # 阶段 131：先把范围与 [1, total] 求交，再遍历。此前直接
            # `for p in range(start, end+1): if 1<=p<=total`——钳制发生在循环**内部**，
            # 于是用户误输超大范围（如 "1-99999999"）会执行近亿次空转、界面卡住。
            start = max(1, start)
            end = min(end, total)
            for p in range(start, end + 1):
                if 1 <= p <= total:
                    picked.add(p - 1)
        else:
            # 单页写法：5
            try:
                p = int(chunk)
            except ValueError:
                log.warning("忽略无法识别的页码片段：%s", chunk)
                continue
            if 1 <= p <= total:
                picked.add(p - 1)

    return sorted(picked)


def _page_to_array(page, dpi: int) -> np.ndarray:
    """
    把 PDF 的一页渲染成 **RGB** 数组（阶段 338 修正通道顺序）。

    参数说明：
        page : PyMuPDF 的 Page 对象。
        dpi  : 渲染精度，数值越大越清晰也越慢。

    返回值：
        H×W×3 的 uint8 数组（**RGB 排布**）。

    ⚠ 为什么这里是 RGB 而不是 BGR（旧版返回 BGR，是个真缺陷）：
    下游 `services/auto_pipeline.process_image` 的契约是 RGB——`core/image_utils.load_image`
    把文件读成 `rgb` 交给单图/自动/批量三个页签，`core/detector` 内部按
    `cv2.cvtColor(rgb, COLOR_RGB2GRAY)` 取灰度（R/G/B 权重 0.299/0.587/0.114）。
    PDF 页以前直接喂 BGR ⇒ 同一张图在两个入口下检测到的像素不一样
    （实测 `ui/assets/sample_watermarked.png`：RGB 4292 像素 / 覆盖率 1.05%，
    BGR 10328 像素 / 2.52%，区域数 6→7），用户表现为「PDF 页擦不干净或擦过头」。
    编码回字节时再翻成 BGR（`_encode_page_image`），那是 OpenCV 的输入约定，不是管线约定。
    """
    fitz = _import_fitz()
    # zoom = dpi / 72，因为 PDF 的原生坐标单位是 1/72 英寸（即 72 DPI）。
    zoom = float(dpi) / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    # alpha=False：不要透明通道，直接给不透明的三通道图，省内存也省麻烦。
    pix = page.get_pixmap(matrix=matrix, alpha=False)

    # pix.samples 是原始像素字节流，按 高×宽×通道 重组成数组。
    buf = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = buf.reshape(pix.height, pix.width, pix.n)

    if pix.n == 1:
        # 灰度页：补成三通道，后续流程统一按彩色处理。
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    if pix.n == 4:
        # 带透明通道：丢弃 alpha。
        arr = arr[:, :, :3]
    # PyMuPDF 给的本来就是 RGB 顺序，管线也按 RGB 走 ⇒ **不再翻转**（阶段 338）。
    return arr


def _encode_page_image(rgb: np.ndarray) -> tuple[bytes, str]:
    """
    把修好的页面图像编码成字节，准备塞回 PDF。

    参数说明：
        rgb : **RGB** 数组（管线内部一律 RGB，见 `_page_to_array`）。

    返回值：
        (图片字节, 扩展名说明)。编码失败时自动回退 PNG，绝不抛异常中断整份文档。

    说明：阶段 338 在这里补上唯一一次 `RGB2BGR`——`cv2.imencode` 按 BGR 解释数组，
    而管线交给我们的是 RGB。以前是「喂进管线的是 BGR、编码时也当 BGR」，两处错互相抵消，
    颜色看起来正常，但检测侧一直用着反的通道权重。
    """
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    fmt = (config.PDF_PAGE_IMAGE_FMT or "JPEG").upper()
    try:
        if fmt in ("JPEG", "JPG"):
            params = [int(cv2.IMWRITE_JPEG_QUALITY), int(config.PDF_JPEG_QUALITY)]
            ok, buf = cv2.imencode(".jpg", bgr, params)
            if ok:
                return buf.tobytes(), "jpg"
            log.warning("JPEG 编码失败，回退 PNG。")
        params = [int(cv2.IMWRITE_PNG_COMPRESSION), int(config.PNG_COMPRESS_LEVEL)]
        ok, buf = cv2.imencode(".png", bgr, params)
        if ok:
            return buf.tobytes(), "png"
    except Exception as exc:  # noqa: BLE001 —— 编码层任何异常都要兜住
        log.warning("页面图像编码异常：%s，改用无参数 PNG。", exc)

    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError("页面图像编码彻底失败（cv2.imencode 返回 False）。")
    return buf.tobytes(), "png"


def _default_out_path(src_path: str, out_path: Optional[str]) -> str:
    """
    计算输出 PDF 的落地路径。

    规则：
        · 调用方指定了 out_path 就用它（自动补 .pdf、自动建父目录）；
        · 没指定则放到 config.OUTPUT_DIR，文件名加 "_已去水印" 后缀并自动避重名。
    """
    if out_path:
        dst = str(out_path)
        if not dst.lower().endswith(".pdf"):
            dst += ".pdf"
        ensure_parent_dir(dst)
        return dst

    stem = _os.path.splitext(_os.path.basename(src_path))[0]
    return str(make_unique_path(config.OUTPUT_DIR, f"{stem}_已去水印.pdf"))


# ==============================================================================
# 第 4 节：主流程 —— 处理整份 PDF
# ==============================================================================
def process_pdf(
    src_path: str | _os.PathLike,
    out_path: Optional[str | _os.PathLike] = None,
    *,
    pages: Optional[str] = None,
    dpi: Optional[int] = None,
    mode: str = "auto",
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
    superres: Optional[bool] = None,
    superres_amount: Optional[float] = None,
    real_superres: Optional[bool] = None,
    real_scale: Optional[int] = None,
    real_backend: Optional[str] = None,
    auto_preset: bool = True,
    on_progress: Optional[ProgressCallback] = None,
    preserve_untouched: Optional[bool] = None,
) -> PdfResult:
    """
    对一份 PDF 逐页去水印，输出一份新的 PDF。

    参数说明：
        src_path    : 源 PDF 路径。
        out_path    : 结果 PDF 路径；None 表示自动放到 outputs 目录。
        pages       : 页码范围字符串，如 "1-5"、"1,3,7"；None/"all" 表示全部页。
        dpi         : 渲染精度；None 取 config.PDF_RENDER_DPI（默认 150）。
        mode        : 固定用 "auto"（PDF 场景无法手动涂抹，保留参数便于扩展）。
        engine 等   : 与单图处理完全一致的修复参数，None 表示取配置默认值。
        on_progress : 进度回调 (0~1, 中文提示)。

    返回值：
        PdfResult 对象。整体失败时 status="failed" 且 error 有值，
        不会抛异常（除非环境缺少 PyMuPDF —— 那种情况必须让用户知道）。

    行为约定：
        · 未被选中的页：原样复制，内容不变；
        · 处理失败的页：按 config.PDF_KEEP_FAILED_PAGE 决定保留原页还是整体失败；
        · 未检出水印的页（skipped）：也保留原页，避免无谓的画质损失。
    """
    on_progress = on_progress or _noop_progress
    started = time.time()
    src = str(src_path)
    result = PdfResult(src_path=src)

    # 阶段 14：批量/PDF 场景预先选定一次设备并复用，省去逐页重复探测设备的小开销。
    device = select_device() if config.PERF_REUSE_DEVICE else None

    # ---------- 前置校验 ----------
    if not _os.path.isfile(src):
        result.error = f"找不到文件：{src}"
        log.error(result.error)
        return result

    if not is_pdf_file(src):
        result.error = f"不是 PDF 文件（扩展名不在 {config.SUPPORTED_DOC_FORMATS} 内）：{src}"
        log.error(result.error)
        return result

    try:
        fitz = _import_fitz()
    except RuntimeError as exc:
        result.error = str(exc)
        log.error("PDF 依赖缺失：%s", exc)
        return result

    dpi = int(config.PDF_RENDER_DPI if dpi is None else dpi)
    dpi = max(36, min(dpi, 600))  # 收敛到安全区间，避免用户填出天文数字

    doc = None
    out_doc = None
    try:
        # ---------- 打开源文档 ----------
        on_progress(0.02, "正在打开 PDF……")
        try:
            doc = fitz.open(src)
        except Exception as exc:  # noqa: BLE001
            # 阶段 314b：损坏/非 PDF 文件时 PyMuPDF 抛英文 FileDataError，
            # 直接透出会让用户看到 `Failed to open file ... as type pdf.`（实测）。
            result.error = (
                f"无法打开 PDF（文件可能已损坏，或根本不是 PDF）：{src}\n原因：{exc}"
            )
            return result
        total = doc.page_count
        result.total_pages = total
        if total <= 0:
            result.error = "这份 PDF 没有任何页面。"
            return result

        # ---------- 解析要处理的页 ----------
        targets = _parse_pages(pages, total)
        if not targets:
            result.error = f"页码范围 '{pages}' 在本文档（共 {total} 页）中没有匹配到任何页。"
            return result

        if len(targets) > config.PDF_MAX_PAGES:
            log.warning(
                "选中 %d 页超过上限 %d，仅处理前 %d 页，其余页原样保留。",
                len(targets), config.PDF_MAX_PAGES, config.PDF_MAX_PAGES,
            )
            targets = targets[: config.PDF_MAX_PAGES]

        target_set = set(targets)
        result.processed_pages = len(targets)
        log.info(
            "开始处理 PDF：%s（共 %d 页，本次处理 %d 页，DPI=%d）",
            _os.path.basename(src), total, len(targets), dpi,
        )

        # ---------- 新建输出文档 ----------
        out_doc = fitz.open()

        for page_no in range(total):
            page = doc.load_page(page_no)
            rect = page.rect  # 原页面尺寸（单位 1/72 英寸），用于保持版面一致

            # —— 情况 A：这一页不在处理范围内，原样搬过去 ——
            if page_no not in target_set:
                out_doc.insert_pdf(doc, from_page=page_no, to_page=page_no)
                continue

            page_started = time.time()
            pr = PageResult(index=page_no + 1)

            try:
                # 第 1 步：渲染成图片
                rgb = _page_to_array(page, dpi)

                # 第 2 步：交给统一核心去水印
                #   把单页内部进度压缩到整体进度条的一小段，避免进度条来回横跳。
                done_before = len([p for p in result.pages])
                base = 0.05 + 0.90 * (done_before / max(1, len(targets)))
                span = 0.90 / max(1, len(targets))

                def _page_progress(p: float, msg: str, _b=base, _s=span, _n=pr.index) -> None:
                    on_progress(_b + _s * max(0.0, min(1.0, p)), f"第 {_n} 页：{msg}")

                core = process_image(
                    rgb,
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
                    superres=superres,
                    superres_amount=superres_amount,
                    real_superres=real_superres,
                    real_scale=real_scale,
                    real_backend=real_backend,
                    device=device,
                    on_progress=_page_progress,
                    preserve_untouched=preserve_untouched,
                )

                pr.status = core.get("status", "failed")
                pr.mask_pixels = int(core.get("mask_pixels", 0) or 0)
                pr.regions = int(core.get("regions", 0) or 0)
                # 阶段 27：记录逐页智能推荐的预设名（开启推荐且有值时非空）。
                pr.recommended_preset = core.get("recommended_preset")
                # 阶段 28：记录逐页智能推荐理由（含 scores/triggered/confidence），
                #          供媒体页聚合展示「为什么选这个预设」。
                pr.recommendation = core.get("recommendation")
                # 阶段 23：记录整页检测命中率统计，供媒体页聚合展示。
                _st = core.get("stats")
                pr.stats = _st
                if _st is not None:
                    pr.coverage_ratio = float(getattr(_st, "coverage_ratio", 0.0) or 0.0)
                    pr.by_kind = dict(getattr(_st, "by_kind", {}) or {})
                    pr.used_ocr = bool(getattr(_st, "used_ocr", False))

                if pr.status == "success":
                    # 第 3 步：把修好的图按原页面尺寸插回新文档
                    img_bytes, _ext = _encode_page_image(core["result_rgb"])
                    new_page = out_doc.new_page(width=rect.width, height=rect.height)
                    new_page.insert_image(new_page.rect, stream=img_bytes)
                    result.success_pages += 1
                else:
                    # 没检出水印 / 失败：保留原页，画质零损失
                    pr.error = core.get("error") or "未检测到水印"
                    out_doc.insert_pdf(doc, from_page=page_no, to_page=page_no)
                    if pr.status == "skipped":
                        result.skipped_pages += 1
                    else:
                        result.failed_pages += 1
                        if not config.PDF_KEEP_FAILED_PAGE:
                            raise PdfAbortOnFailedPage(f"第 {pr.index} 页处理失败：{pr.error}")

            except PdfAbortOnFailedPage:
                raise   # 只有「用户设了 keep=False」这一种情况才允许中止整份文档
            except Exception as exc:  # noqa: BLE001 —— 单页异常不该毁掉整份文档
                log.exception("第 %d 页处理异常", pr.index)
                pr.status = "failed"
                pr.error = f"{type(exc).__name__}: {exc}"
                result.failed_pages += 1
                try:
                    out_doc.insert_pdf(doc, from_page=page_no, to_page=page_no)
                except Exception:  # noqa: BLE001
                    log.error("第 %d 页连原样复制都失败，该页将缺失。", pr.index)
                # 阶段 341：`PDF_KEEP_FAILED_PAGE=False` 的语义必须与失败**方式**无关——
                # 管线返回 failed 与本页抛异常是同一件事（都算「这一页没做成」），
                # 不能一个中止、一个静默保留，否则同一个开关给出两种解释。
                if not config.PDF_KEEP_FAILED_PAGE:
                    raise PdfAbortOnFailedPage(f"第 {pr.index} 页处理失败：{pr.error}") from exc

            pr.seconds = time.time() - page_started
            result.pages.append(pr)
            log.info("%s，耗时 %.2fs", pr, pr.seconds)

        # ---------- 保存输出 ----------
        on_progress(0.96, "正在保存 PDF……")
        dst = _default_out_path(src, str(out_path) if out_path else None)
        # garbage=4 做最大程度的对象合并去重，deflate 压缩流，减小体积。
        out_doc.save(dst, garbage=4, deflate=True)
        result.out_path = dst
        result.status = "success"
        result.seconds = time.time() - started
        on_progress(1.0, "PDF 处理完成")
        log.info("PDF 处理完成：%s（%s）", dst, result.summary())
        return result

    except Exception as exc:  # noqa: BLE001 —— 顶层兜底，转成结果对象返回
        log.exception("处理 PDF 时发生异常")
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        result.seconds = time.time() - started
        return result

    finally:
        # 无论成功失败都要释放文件句柄，否则 Windows 上文件会被锁住删不掉。
        for handle in (out_doc, doc):
            try:
                if handle is not None:
                    handle.close()
            except Exception:  # noqa: BLE001
                pass


# ==============================================================================
# 第 5 节：辅助能力 —— 读取 PDF 基本信息（界面用于预览）
# ==============================================================================
def get_pdf_info(src_path: str | _os.PathLike) -> Dict[str, Any]:
    """
    读取 PDF 的基本信息，供界面展示（不做任何修改）。

    返回值字典：
        ok         : 是否读取成功
        pages      : 总页数
        title      : 文档标题（可能为空）
        size_mb    : 文件大小（MB）
        page_size  : 首页尺寸描述，如 "595 × 842 点"（阶段 131 订正：不含「约 A4」后缀）
        error      : 失败原因（ok=False 时有值）
    """
    src = str(src_path)
    info: Dict[str, Any] = {"ok": False, "pages": 0, "title": "", "size_mb": 0.0,
                            "page_size": "", "error": None}
    if not _os.path.isfile(src):
        info["error"] = f"找不到文件：{src}"
        return info

    doc = None
    try:
        fitz = _import_fitz()
        doc = fitz.open(src)
        info["pages"] = doc.page_count
        meta = doc.metadata or {}
        info["title"] = str(meta.get("title") or "")
        info["size_mb"] = round(_os.path.getsize(src) / 1024 / 1024, 2)
        if doc.page_count > 0:
            rect = doc.load_page(0).rect
            info["page_size"] = f"{rect.width:.0f} × {rect.height:.0f} 点"
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if doc is not None:
                doc.close()
        except Exception:  # noqa: BLE001
            pass
    return info


def render_pdf_first_page(pdf_path: str | _os.PathLike, *, dpi: int | None = None) -> "Optional[np.ndarray]":
    """
    渲染 PDF 首页为 OpenCV 可处理的 BGR 数组（供历史页做缩略图使用）。

    参数说明：
        pdf_path : 源 PDF 路径（可以是处理后的结果 PDF，也可以是原始 PDF）。
        dpi      : 渲染精度；None 取 config.PDF_RENDER_DPI（默认 150）。

    返回值：
        H×W×3 的 uint8 数组（BGR 排布）；以下情况返回 None（绝不抛异常）：
            · PyMuPDF 未安装；
            · 文件不存在 / 不是 PDF / 文档没有任何页面；
            · 渲染或解码过程中出现异常。
        调用方拿到 None 时应自行回退到「占位缩略图」。

    设计意图：
        阶段 40 需要把 PDF 处理结果登记进历史缓存，并像视频记录那样给出
        一张首页缩略图。本函数复用了既有的 ``_page_to_array``，把「取第 0 页
        → 渲染」这一步封装成独立公共接口，与 process_pdf 内部逻辑保持一致。
    """
    src = str(pdf_path)
    if not _os.path.isfile(src) or not is_pdf_file(src):
        log.warning("render_pdf_first_page 跳过：文件不存在或非 PDF（%s）", src)
        return None

    try:
        fitz = _import_fitz()
    except RuntimeError as exc:
        log.warning("render_pdf_first_page 跳过：PyMuPDF 不可用（%s）", exc)
        return None

    _dpi = int(config.PDF_RENDER_DPI if dpi is None else dpi)
    _dpi = max(36, min(_dpi, 600))

    doc = None
    try:
        doc = fitz.open(src)
        if doc.page_count <= 0:
            log.warning("render_pdf_first_page 跳过：文档没有任何页面（%s）", src)
            return None
        page = doc.load_page(0)
        return _page_to_array(page, _dpi)
    except Exception as exc:  # noqa: BLE001 —— 渲染层任何异常都兜住，绝不向上抛
        log.warning("render_pdf_first_page 渲染异常（%s）：%s", src, exc)
        return None
    finally:
        try:
            if doc is not None:
                doc.close()
        except Exception:  # noqa: BLE001
            pass


# ==============================================================================
# 第 6 节：模块自测入口
# ------------------------------------------------------------------------------
# 直接运行 python services/pdf_service.py 会生成一份带水印的测试 PDF，
# 走完整流程处理一遍，并打印每一步的结论。这是验证「PDF 功能是否可用」最快的方式。
# ==============================================================================
if __name__ == "__main__":
    from utils.console_encoding import force_utf8_stdio
    force_utf8_stdio()   # 阶段 331：自检打印 ✅/⇒，GBK 管道下会把「通过」读成退出码 1
    print("=" * 70)
    print("PDF 服务自检")
    print("=" * 70)

    print(f"[1] PyMuPDF 可用性：{is_pdf_available()}")
    if not is_pdf_available():
        print("    未安装 PyMuPDF，请执行：pip install PyMuPDF")
        raise SystemExit(1)

    import tempfile

    _fitz = _import_fitz()
    tmp_dir = tempfile.mkdtemp(prefix="pdf_selftest_")
    test_pdf = _os.path.join(tmp_dir, "测试文档.pdf")

    # ---- 造一份 3 页的测试 PDF，每页画一段深色文字当作「水印」 ----
    _doc = _fitz.open()
    for i in range(3):
        pg = _doc.new_page(width=595, height=842)  # A4
        pg.insert_text((60, 100), f"Normal content page {i + 1}", fontsize=18)
        # 右下角画一块高对比色块 + 文字，模拟水印
        pg.draw_rect(_fitz.Rect(380, 720, 545, 770), color=(0, 0, 0), fill=(0.1, 0.1, 0.1))
        pg.insert_text((392, 752), "WATERMARK", fontsize=16, color=(1, 1, 1))
    _doc.save(test_pdf)
    _doc.close()
    print(f"[2] 已生成测试 PDF：{test_pdf}")

    info = get_pdf_info(test_pdf)
    print(f"[3] 文档信息：{info}")
    assert info["ok"] and info["pages"] == 3, "PDF 信息读取异常"

    # ---- 页码解析用例 ----
    assert _parse_pages(None, 3) == [0, 1, 2]
    assert _parse_pages("1-2", 3) == [0, 1]
    assert _parse_pages("1,3", 3) == [0, 2]
    assert _parse_pages("3-1", 3) == [0, 1, 2]
    assert _parse_pages("99", 3) == []
    print("[4] 页码范围解析用例全部通过")

    # ---- 完整流程 ----
    out_file = _os.path.join(tmp_dir, "结果.pdf")
    steps: list[str] = []
    res = process_pdf(
        test_pdf,
        out_file,
        pages="1-2",
        dpi=100,
        on_progress=lambda p, m: steps.append(f"{p:.0%} {m}"),
    )
    print(f"[5] 处理结果：{res.status}")
    print(f"    {res.summary()}")
    for pr in res.pages:
        print(f"      · {pr}")
    print(f"[6] 进度回调触发 {len(steps)} 次，末条：{steps[-1] if steps else '(无)'}")

    assert res.status == "success", f"处理失败：{res.error}"
    assert res.out_path and _os.path.isfile(res.out_path), "输出文件不存在"
    assert res.total_pages == 3 and res.processed_pages == 2, "页数统计不对"

    # ---- 校验输出 PDF 页数与源一致（未选中的第 3 页应被原样保留）----
    _chk = _fitz.open(res.out_path)
    print(f"[7] 输出 PDF 页数：{_chk.page_count}（应为 3）")
    assert _chk.page_count == 3, "输出页数与源文档不一致"
    _chk.close()

    # ---- 异常路径 ----
    bad = process_pdf(_os.path.join(tmp_dir, "不存在.pdf"))
    assert bad.status == "failed" and bad.error, "不存在的文件应返回失败"
    print(f"[8] 异常路径校验通过：{bad.error}")

    print("=" * 70)
    print("PDF 服务自检全部通过 ✅")
    print(f"输出文件：{res.out_path}")
    print("=" * 70)
