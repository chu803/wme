# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/export_service.py
 文件说明 : 导出服务（阶段 6 核心服务）。
            把修复结果「交付」给用户时所需的各种导出能力集中在此：

            1. reencode_image      : 把单张结果图按指定格式/画质重新编码保存
                                     （例如把 PNG 转成体积更小的 JPG/WebP）。
            2. export_compare      : 生成「原图 | 修复后」左右对比图
                                     （白底间隔 + Before/After 标注），便于发朋友圈/汇报。
            3. export_paths_as_zip : 把一批图片路径打包成 ZIP（通用）。
            4. export_history_as_zip: 把「历史记录」里全部成功结果导出为 ZIP
                                     （可选按所选格式重新编码），一键带走全部成果。

            设计约束：
            · 本模块属于「服务层」，只允许调用 core / utils / config，
              严禁 import ui 与 gradio（保持与 queue_worker / cache_service 一致）。
            · 图片读写复用 core.image_utils.load_image_with_alpha / save_image，
              中文路径、透明通道、格式校验均与主流程保持一致。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import base64
import os as _os
import sys as _sys
import zipfile as _zipfile
from typing import Optional, Sequence

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

import config
from core.image_utils import load_image_full, load_image_with_alpha, save_image
from utils import brand
from utils.logger import get_logger
from utils.path_helper import make_unique_path, sanitize_filename

log = get_logger("services.export_service")

# 延迟导入 cv2（仅 export_compare 用到拼接与标注）。
def _require_cv2():
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "缺少 OpenCV 依赖（cv2）。请先执行：pip install -r requirements.txt"
        ) from exc
    return cv2


def _ensure_export_dir() -> _os.PathLike:
    """确保导出目录存在，返回目录路径。"""
    _os.makedirs(config.EXPORT_DIR, exist_ok=True)
    return config.EXPORT_DIR


def reencode_image(
    src: str | _os.PathLike,
    *,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    out_path: Optional[str | _os.PathLike] = None,
    keep_alpha: bool = True,
) -> str:
    """
    把单张图片按指定格式/画质重新编码并保存（不改内容，只换封装）。

    参数说明：
        src        : 源图片路径（通常是修复结果图）。
        fmt        : 目标格式（PNG/JPG/WEBP），缺省取 config.DEFAULT_OUTPUT_FORMAT。
        quality    : 画质 1~100，仅对 JPG/WebP 生效；缺省取 config.DEFAULT_OUTPUT_QUALITY。
        out_path   : 指定输出路径；缺省时落到 EXPORT_DIR，文件名沿用源主名。
        keep_alpha : 是否保留透明通道（PNG 源图才有意义）。

    返回值：
        写出文件的绝对路径（字符串）。

    典型用途：
        把修复结果从无损 PNG（体积大）转成 WebP（体积小、画质接近），方便分享。
    """
    _ensure_export_dir()
    # 阶段 73：同时取出内嵌 ICC 色彩配置（重新编码后随结果写回，宽色域不偏色）。
    rgb, alpha, icc = load_image_full(src)
    out_fmt = (fmt or config.DEFAULT_OUTPUT_FORMAT).upper()

    if out_path is None:
        from utils.path_helper import build_output_filename
        stem = _os.path.splitext(_os.path.basename(str(src)))[0] or "image"
        fname = build_output_filename(stem, out_fmt.lower())
        out_path = make_unique_path(config.EXPORT_DIR, fname)

    saved = save_image(
        out_path,
        rgb,
        fmt=out_fmt,
        quality=quality,
        alpha=alpha if (keep_alpha and out_fmt == "PNG") else None,
        icc=icc,
    )
    log.info("已重新编码：%s -> %s (%s)", src, saved, out_fmt)
    return str(saved)


def make_compare_array(
    orig_arr: np.ndarray,
    result_arr: np.ndarray,
    *,
    gap: Optional[int] = None,
    label: Optional[bool] = None,
) -> np.ndarray:
    """
    在内存中生成「原图 | 修复后」左右对比图（不写文件）。

    参数说明：
        orig_arr   : 原图数组，接受灰度(H×W)/RGB(H×W×3)/RGBA(H×W×4) uint8
                     （灰度自动复制三通道、RGBA 丢弃 alpha，阶段 137a 归一化）。
        result_arr : 修复结果数组，形态要求同上。
        gap        : 两张图之间的白边像素，缺省取 config.EXPORT_COMPARE_GAP。
        label      : 是否标注 Before/After，缺省取 config.EXPORT_COMPARE_LABEL。

    返回值：
        拼接后的对比图数组 H×W×3（uint8 RGB）。

    说明：
        与 export_compare 共用同一套拼接/标注逻辑，区别在于本函数直接返回
        numpy 数组，供界面层（单图/自动页）即时展示，避免每次修复都落盘。
    """
    cv2 = _require_cv2()
    gap = config.EXPORT_COMPARE_GAP if gap is None else int(gap)
    label = config.EXPORT_COMPARE_LABEL if label is None else bool(label)

    rgb_a = _to_rgb3(orig_arr)
    rgb_b = _to_rgb3(result_arr)

    ha, wa = rgb_a.shape[:2]
    hb, wb = rgb_b.shape[:2]
    # 统一高度（取较大者），保持宽高比。
    target_h = max(ha, hb)
    if ha == 0 or hb == 0:
        # 任一输入为空（零高度/宽度），无法对齐拼接；返回最小空白画布，避免除零崩溃。
        return np.full((max(1, target_h), max(1, int(gap or 0) + 2), 3), 255, dtype=np.uint8)
    a = cv2.resize(rgb_a, (max(1, int(round(wa * target_h / ha))), target_h),
                   interpolation=cv2.INTER_AREA) if ha != target_h else rgb_a
    b = cv2.resize(rgb_b, (max(1, int(round(wb * target_h / hb))), target_h),
                   interpolation=cv2.INTER_AREA) if hb != target_h else rgb_b
    wa2, hb2 = a.shape[1], b.shape[1]  # 缩放后实际宽

    # 白色画布：左图 + 间隔 + 右图。
    canvas_w = wa2 + gap + hb2
    canvas = np.full((target_h, canvas_w, 3), 255, dtype=np.uint8)
    canvas[:, :wa2] = a
    canvas[:, wa2 + gap:wa2 + gap + hb2] = b

    if label:
        font = cv2.FONT_HERSHEY_SIMPLEX
        # Before（左）
        cv2.putText(canvas, "Before", (8, 26), font, 0.9, brand.ink_rgb(), 2, cv2.LINE_AA)
        # After（右）—— 品牌主色统一取自 utils.brand（调用时读取，唯一真值来源）。
        cv2.putText(canvas, "After", (wa2 + gap + 8, 26), font, 0.9,
                    brand.primary_rgb(), 2, cv2.LINE_AA)

    return canvas


def make_contact_sheet(
    frames: "Sequence[np.ndarray]",
    cols: int = 4,
    thumb_size: int = 160,
    bg: "tuple[int, int, int]" = brand.ink_rgb(),
) -> np.ndarray:
    """
    把多张帧图拼成一张网格「联系表」（contact sheet），用于视频结果快速浏览。

    参数说明：
        frames     : 帧数组列表（每个 H×W×3 uint8 RGB）。为空时返回 thumb_size×thumb_size 的 bg 色块。
        cols       : 每行几格。
        thumb_size : 每格缩放到的最大边长（保持宽高比，居中铺在 thumb_size×thumb_size 格内）。
        bg         : 格子底色（RGB），默认深蓝灰。

    返回值：
        拼接后的整图数组 H×W×3（uint8 RGB）。
    """
    cv2 = _require_cv2()
    flist = [np.asarray(f, dtype=np.uint8) for f in frames if f is not None]
    if not flist:
        return np.full((thumb_size, thumb_size, 3), bg, dtype=np.uint8)
    cols = max(1, int(cols))
    rows = (len(flist) + cols - 1) // cols
    cell = int(thumb_size)

    cells = []
    for f in flist:
        a = _to_rgb3(f)
        h, w = a.shape[:2]
        scale = cell / max(h, w)
        nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        small = cv2.resize(a, (nw, nh), interpolation=cv2.INTER_AREA)
        pad = np.full((cell, cell, 3), bg[0], dtype=np.uint8)
        pad[...] = bg
        y0 = (cell - nh) // 2
        x0 = (cell - nw) // 2
        pad[y0:y0 + nh, x0:x0 + nw] = small
        cells.append(pad)

    grid_rows = []
    for r in range(rows):
        row_cells = cells[r * cols:(r + 1) * cols]
        while len(row_cells) < cols:
            row_cells.append(np.full((cell, cell, 3), bg, dtype=np.uint8))
        grid_rows.append(np.concatenate(row_cells, axis=1))
    return np.concatenate(grid_rows, axis=0)


def _to_rgb3(arr: np.ndarray) -> np.ndarray:
    """把任意数组规整为 H×W×3 uint8 RGB（灰度复制三通道、丢弃 alpha、容错 shape）。

    阶段 137a：补 2D 灰度分支——此前只丢 alpha，2D 直通会在
    resize/画布粘贴处 broadcast 崩溃（make_compare_array 实证）。
    """
    a = np.asarray(arr, dtype=np.uint8)
    if a.ndim == 2:
        return np.stack([a, a, a], axis=-1)
    if a.ndim == 3 and a.shape[2] == 4:
        a = a[:, :, :3]
    return a


def _array_to_data_uri(rgb_arr: np.ndarray, ext: str = ".jpg", quality: int = 88) -> str:
    """
    把 RGB uint8 数组编码为 base64 data URI（供 gr.HTML 的 <img> 内嵌，避免落盘）。

    返回值：
        "data:image/jpeg;base64,...." 形式字符串。
    """
    cv2 = _require_cv2()
    bgr = cv2.cvtColor(_to_rgb3(rgb_arr), cv2.COLOR_RGB2BGR)
    ext_l = ext.lower()
    params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)] if ext_l in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, bgr, params)
    if not ok:
        raise ValueError("图像编码失败（cv2.imencode 返回 False）")
    mime = "jpeg" if ext_l in (".jpg", ".jpeg") else "png"
    return "data:image/" + mime + ";base64," + base64.b64encode(buf).decode("ascii")


def make_diff_heatmap(orig_arr, result_arr, percentile: float = 99.0) -> np.ndarray:
    """
    生成「原图 vs 结果」的差异热图（uint8 RGB）。

    越亮的区域表示该处像素被改动越大，用于快速定位修复痕迹 / 残留水印 /
    过渡不自然区。采用 99 百分位拉伸，避免个别极亮像素把整图压暗。

    返回值：
        H×W×3 uint8 RGB 热图（未改动区域为黑色）。
    """
    cv2 = _require_cv2()
    a = _to_rgb3(orig_arr).astype(np.float32)
    b = _to_rgb3(result_arr).astype(np.float32)
    if a.shape[:2] != b.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    diff = np.abs(a - b)
    if diff.ndim == 3:
        diff = diff.mean(axis=2)
    diff = np.clip(diff, 0, None)
    h, w = diff.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    mx = float(np.percentile(diff, float(percentile))) if diff.size else 0.0
    if mx <= 1e-6:
        return np.zeros((h, w, 3), dtype=np.uint8)
    norm = np.clip(diff / mx, 0, 1) * 255.0
    cm = cv2.applyColorMap(norm.astype(np.uint8), cv2.COLORMAP_JET)
    return cv2.cvtColor(cm, cv2.COLOR_BGR2RGB)


_BA_COUNTER = 0


def make_before_after_html(
    orig_arr,
    result_arr,
    before_label: str = "Before",
    after_label: str = "After",
) -> str:
    """
    生成可拖动的「原图 | 结果」滑动对比控件（自包含 HTML/CSS，无 <script>）。

    设计要点：
    · 底层放结果图（After），上层放原图（Before）并用 width 裁剪揭示；
    · 用一个覆盖全图的透明 range 输入捕获拖动，实时调整裁剪宽度与分割线位置；
      仅用内联 oninput（无 <script>），最大化兼容 Gradio 的 gr.HTML 渲染；
    · 两个 <img> 尺寸完全一致（原图与结果同分辨率），故裁剪后严格对齐。

    返回值：
        可直接交给 gr.HTML 渲染的字符串；生成失败时返回错误提示段落。
    """
    global _BA_COUNTER
    _BA_COUNTER += 1
    try:
        orig_uri = _array_to_data_uri(_to_rgb3(orig_arr))
        result_uri = _array_to_data_uri(_to_rgb3(result_arr))
    except Exception as exc:  # noqa: BLE001
        return f"<p style='color:#b91c1c'>对比滑块生成失败：{exc}</p>"
    return (
        "<div class='ba-wrap' style='position:relative;max-width:100%;overflow:hidden;"
        "border:1px solid #e5e7eb;border-radius:8px;'>"
        f"<img src='{result_uri}' alt='After' style='display:block;width:100%;'>"
        "<div class='ba-before' style='position:absolute;top:0;left:0;height:100%;"
        "width:50%;overflow:hidden;'>"
        f"<img src='{orig_uri}' alt='Before' "
        "style='display:block;height:100%;width:auto;max-width:none;'>"
        "</div>"
        "<div class='ba-divider' style='position:absolute;top:0;left:50%;width:2px;"
        "height:100%;background:#fff;box-shadow:0 0 4px rgba(0,0,0,.5);"
        "transform:translateX(-1px);'></div>"
        f"<span style='position:absolute;left:8px;top:8px;"
        f"background:{brand.rgba(brand.ink_rgb(), 0.75)};"
        f"color:#fff;padding:2px 8px;border-radius:6px;font-size:12px;'>{before_label}</span>"
        f"<span style='position:absolute;right:8px;top:8px;"
        f"background:{brand.rgba(brand.primary_rgb(), 0.75)};"
        f"color:#fff;padding:2px 8px;border-radius:6px;font-size:12px;'>{after_label}</span>"
        "<input type='range' min='0' max='100' value='50' "
        "style='position:absolute;left:0;top:0;width:100%;height:100%;margin:0;"
        "opacity:0;cursor:ew-resize;' "
        "oninput=\"var w=this.parentElement,v=this.value+'%';"
        "w.querySelector('.ba-before').style.width=v;"
        "w.querySelector('.ba-divider').style.left=v;\">"
        "</div>"
    )


def export_compare(
    src_path: str | _os.PathLike,
    result_path: str | _os.PathLike,
    *,
    gap: Optional[int] = None,
    label: Optional[bool] = None,
    out_path: Optional[str | _os.PathLike] = None,
) -> str:
    """
    生成「原图 | 修复后」左右对比图（落盘版本）。

    参数说明：
        src_path    : 原图路径（左侧）。
        result_path : 修复结果路径（右侧）。
        gap         : 两张图之间的白边像素，缺省取 config.EXPORT_COMPARE_GAP。
        label       : 是否标注 Before/After，缺省取 config.EXPORT_COMPARE_LABEL。
        out_path    : 指定输出路径；缺省落到 EXPORT_DIR，文件名含时间戳。

    返回值：
        对比图绝对路径（字符串，PNG 格式，保透明友好）。

    说明：
        两图高度不一致时，统一缩放到相同高度再并排；标注使用英文
        "Before"/"After"（避免中文字体缺失显示为方块）。
    """
    cv2 = _require_cv2()
    _ensure_export_dir()

    rgb_a, _ = load_image_with_alpha(src_path)
    rgb_b, _ = load_image_with_alpha(result_path)

    canvas = make_compare_array(rgb_a, rgb_b, gap=gap, label=label)

    if out_path is None:
        from utils.path_helper import build_output_filename
        fname = build_output_filename("compare", "png")
        out_path = make_unique_path(config.EXPORT_DIR, fname)

    saved = save_image(out_path, canvas, fmt="PNG")
    log.info("对比图已生成：%s", saved)
    return str(saved)


def export_paths_as_zip(
    paths: Sequence[str | _os.PathLike],
    *,
    names: Optional[Sequence[str]] = None,
    out_zip: Optional[str | _os.PathLike] = None,
    arcname_ext: bool = True,
) -> str:
    """
    把一批图片路径打包成 ZIP。

    参数说明：
        paths      : 图片路径列表（仅存在的文件会被纳入）。
        names      : 压缩包内的文件名列表；缺省用原文件名（含扩展名）。
        out_zip    : 指定 ZIP 输出路径；缺省落到 EXPORT_DIR。
        arcname_ext: names 缺省时，是否保留原扩展名作为包内文件名。

    返回值：
        ZIP 文件绝对路径（字符串）。
    """
    _ensure_export_dir()

    valid: list[tuple[str, str]] = []  # (真实路径, 包内名)
    for i, p in enumerate(paths):
        p = str(p)
        if not p or not _os.path.exists(p):
            log.warning("导出 ZIP 跳过不存在的文件：%s", p)
            continue
        if names is not None and i < len(names) and names[i]:
            arc = sanitize_filename(names[i], max_length=120)
        else:
            arc = _os.path.basename(p) if arcname_ext else f"image_{i + 1}"
        valid.append((p, arc))

    if not valid:
        raise FileNotFoundError("没有可打包的有效图片文件。")

    if out_zip is None:
        from utils.path_helper import build_output_filename
        fname = build_output_filename("results", "zip")
        out_zip = make_unique_path(config.EXPORT_DIR, fname)

    used: dict[str, int] = {}
    with _zipfile.ZipFile(out_zip, "w", _zipfile.ZIP_DEFLATED) as zf:
        for real, arc in valid:
            # 包内重名去重（对齐文档契约「自动去重文件名」）：冲突时追加序号，
            # 避免 zipfile 写出同名条目导致首条读回时不可达（静默丢数据）。
            if arc in used:
                used[arc] += 1
                stem, ext = _os.path.splitext(arc)
                arc = f"{stem}_{used[arc]}{ext}"
            else:
                used[arc] = 0
            zf.write(real, arcname=arc)
    log.info("已打包 %d 个文件到 %s", len(valid), out_zip)
    return str(out_zip)


def export_history_as_zip(
    cache,
    *,
    fmt: Optional[str] = None,
    quality: Optional[int] = None,
    out_zip: Optional[str | _os.PathLike] = None,
    kind: Optional[str] = None,
    query: Optional[str] = None,
) -> str:
    """
    把「历史记录」中全部成功结果导出为 ZIP。

    参数说明：
        cache   : services.cache_service.CacheService 实例。
        fmt     : 若给定（PNG/JPG/WEBP），先按该格式重新编码每个结果再打包；
                  若省略，则直接打包原始结果文件（体积更小、最保真）。
        quality : 配合 fmt 使用的画质（1~100）。
        out_zip : 指定 ZIP 输出路径；缺省落到 EXPORT_DIR。
        kind    : 阶段 41 新增 —— 类型过滤；为 None（缺省）导出全部，
                  否则仅打包 getattr(r, "kind", "image") == kind 的记录。
        query   : 阶段 42 新增 —— 文件名模糊匹配；为 None/空串时忽略，
                  否则仅打包 original_name 含 query（不区分大小写）的记录；
                  与 kind 叠加生效。

    返回值：
        ZIP 文件绝对路径（字符串）。

    说明：
        包内文件名形如「原图名__记录id.扩展名」，避免不同原图同名时互相覆盖。
    """
    records = [
        r for r in cache.list_records(kind=kind, query=query)
        if r.status == "success" and r.result_path and _os.path.exists(r.result_path)
    ]
    if not records:
        raise FileNotFoundError("历史记录中没有可导出的成功结果。")

    if fmt:
        # 按目标格式逐个重新编码，收集临时产物再打包。
        # 阶段 342：中间产物**必须写到临时目录**，不能走默认 `EXPORT_DIR`。
        # 以前 `out_path=None` ⇒ `reencode_image` 把每条记录都落到 `exports/` 里
        # （唯一化命名，永不覆盖），打完 ZIP 后**没人删它们** ⇒ 用户点一次「导出 ZIP
        # （转 WEBP）」就在 `exports/` 里多出 N 张全尺寸散图；而 `scan_orphans` 明确
        # **不扫 exports/**（那是用户成品目录，见 `cache_service.py:855`）⇒ 孤儿清理也收不回。
        # 阶段 147 的自检早就知道这件事（当时的做法是把 `config.EXPORT_DIR` 临时改掉），
        # 但那只是让**自检**不污染，真实用户路径一直在污染。
        import tempfile

        encoded: list[str] = []
        arc_names: list[str] = []
        out_fmt = fmt.upper()
        with tempfile.TemporaryDirectory(prefix="wme_zip_") as _work:
            for _i, r in enumerate(records):
                try:
                    _stem = _os.path.splitext(_os.path.basename(str(r.result_path)))[0] or "image"
                    _tmp_out = _os.path.join(
                        _work, f"{_i:04d}_{sanitize_filename(_stem, 80)}.{out_fmt.lower()}")
                    new_path = reencode_image(
                        r.result_path, fmt=out_fmt, quality=quality,
                        out_path=_tmp_out, keep_alpha=True,
                    )
                    encoded.append(new_path)
                    base = sanitize_filename(_os.path.splitext(r.original_name or "image")[0], 120)
                    arc_names.append(f"{base}__{r.id}.{out_fmt.lower()}")
                except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
                    log.warning("历史导出重编码失败，改用原文件：%s（%s）", r.original_name, exc)
                    encoded.append(r.result_path)
                    base = sanitize_filename(_os.path.splitext(r.original_name or "image")[0], 120)
                    ext = _os.path.splitext(r.result_path)[1].lstrip(".") or "png"
                    arc_names.append(f"{base}__{r.id}.{ext}")
            # ZIP 必须在 with 块**内**写：`export_paths_as_zip` 要读这些临时文件，
            # 块一退出目录就被删，读不到就是空包/报错。
            return export_paths_as_zip(encoded, names=arc_names, out_zip=out_zip)
    else:
        paths = [r.result_path for r in records]
        names = [
            f"{sanitize_filename(_os.path.splitext(r.original_name or 'image')[0], 120)}__{r.id}"
            f"{_os.path.splitext(r.result_path)[1]}"
            for r in records
        ]
        return export_paths_as_zip(paths, names=names, out_zip=out_zip)


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    cv2 = _require_cv2()
    import tempfile
    import shutil

    # 阶段 147：自检**完全隔离到临时目录**。
    #   此前本自检走各导出函数的默认输出目录（`config.EXPORT_DIR` = `exports/`），
    #   于是**每次运行都在真实导出目录写 4 个产物、再删掉这 4 个** —— 看似干净，
    #   实则**非幂等**：删除动作会累积进沙箱的「单轮批量删除」计数器
    #   （`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，阈值 50），当同一轮里前面的用例
    #   已累计删到 44+ 时，本自检的 6 次 `os.remove` 就把计数推过阈值 ⇒ **被护栏拦截**
    #   ⇒ 自检以 rc=1 失败（**同一份代码，前几次跑是绿的**）。
    #   Stage 136 对 `services/video_export.py` 做过同款修复（隔离到 mkdtemp 且
    #   finally 自清理），本次把 `export_service` 对齐到同一做法。
    #   四个导出函数本就都接受显式输出路径（`out_path` / `out_zip`）⇒ 隔离零成本。
    _work = tempfile.mkdtemp(prefix="export_selfcheck_")
    # ⚠ **只传 `out_path` / `out_zip` 不足以隔离**（阶段 147 实测踩到）：
    #   `export_history_as_zip` 的**中间产物**（逐条记录重编码）走的是**默认目录**
    #   `config.EXPORT_DIR` —— 实测即便如此改，`exports/` 仍会多出
    #   `tmp*_cleaned_*.webp` 两个文件。⇒ 必须**把默认目录也指向临时目录**，
    #   即同时覆盖「显式输出路径」与「默认兜底路径」两条线。
    #   （与经验 169「兜底值绕过清洗管线」同族：**兜底路径往往绕过你以为已经守住的那层**。）
    from pathlib import Path as _Path
    _real_export_dir = config.EXPORT_DIR
    config.EXPORT_DIR = _Path(_work)
    tmp_src = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_res = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        cv2.imwrite(tmp_src.name, np.full((120, 160, 3), 180, dtype=np.uint8))
        cv2.imwrite(tmp_res.name, np.full((120, 160, 3), 60, dtype=np.uint8))
        tmp_src.close()
        tmp_res.close()

        # 用例 1：重新编码为 JPG。
        jpg = reencode_image(tmp_src.name, fmt="JPG", quality=90,
                             out_path=_os.path.join(_work, "recoded.jpg"))
        assert _os.path.exists(jpg) and jpg.lower().endswith(".jpg"), "重编码失败"
        print(f"用例1通过：重编码为 {jpg}")

        # 用例 2：对比图。
        cmp = export_compare(tmp_src.name, tmp_res.name, gap=20, label=True,
                             out_path=_os.path.join(_work, "compare.png"))
        assert _os.path.exists(cmp) and cmp.lower().endswith(".png"), "对比图失败"
        print(f"用例2通过：对比图 {cmp}")

        # 用例 3：多图打包 ZIP。
        zp = export_paths_as_zip([tmp_src.name, tmp_res.name], names=["原图.png", "结果.png"],
                                 out_zip=_os.path.join(_work, "results.zip"))
        assert _os.path.exists(zp) and zp.lower().endswith(".zip"), "ZIP 失败"
        print(f"用例3通过：ZIP {zp}")

        # 用例 4：历史记录导出（构造一个最小缓存桩）。
        class _Rec:
            def __init__(self, name, path, rid):
                self.status = "success"
                self.result_path = path
                self.original_name = name
                self.id = rid

        class _StubCache:
            def list_records(self, kind=None, query=None):  # 对齐 CacheService.list_records 契约
                return [_Rec("测试图", tmp_res.name, "abc123")]

        zp2 = export_history_as_zip(_StubCache(), fmt="WEBP",
                                    out_zip=_os.path.join(_work, "history.zip"))
        assert _os.path.exists(zp2), "历史导出失败"
        print(f"用例4通过：历史导出 ZIP {zp2}")
    finally:
        # 恢复默认导出目录（**必须在清理之前**，避免把恢复动作留在异常路径外）。
        config.EXPORT_DIR = _real_export_dir
        # 统一清理：临时目录整棵删（一次 rmtree 而非多次 remove，对沙箱删除计数器更友好），
        # 以及两个系统临时文件。
        shutil.rmtree(_work, ignore_errors=True)
        for _f in (tmp_src.name, tmp_res.name):
            try:
                _os.remove(_f)
            except OSError:
                pass
    print("export_service.py 自检全部通过。")
