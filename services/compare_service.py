# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/compare_service.py
 文件说明 : 前后对比查看服务（阶段 38 新增）。

            阶段 31 把「拼接 / 滑块 / 热图」三种对比视图落到了单图页与自动页，
            但那套渲染逻辑是直接写在界面回调里的（tab_single.on_enhance /
            tab_auto.on_enhance / tab_media.run_video_compare 三份几乎一样的
            代码），批量页与历史页因此只能停留在「静态拼接缩略图」。

            本模块把这套能力下沉到服务层，成为全站唯一的对比渲染入口：

              1. normalize_mode        : 把任意输入规整为合法模式（非法回退 concat）。
              2. load_pair             : 按路径加载「原图 + 结果」并对齐尺寸。
              3. render_compare_html   : 由两个数组渲染对比 HTML（三模式）。
              4. render_pair_html      : 由两个「路径」渲染对比 HTML（供画廊点选）。
              5. summarize_pair        : 计算差异摘要（改动像素占比 / 平均差异 / 峰值）。
              6. export_pair_compare   : 把一对图落盘为拼接对比图（PNG）。

            设计约束：
            · 属于「服务层」，只允许调用 core / utils / config 与同层 export_service，
              **严禁 import ui 与 gradio**（与 export_service / video_export 一致）。
            · 渲染前按 config.UI_COMPARE_MAX_SIDE 等比缩图，避免超大图 base64
              内嵌把浏览器拖卡；导出落盘走原始分辨率，不受该限制影响。
            · 原图与结果尺寸不一致时（真实超分会放大结果），统一把结果缩回原图
              尺寸再比对，保证滑块揭示与热图像素级对齐。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from typing import Optional

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

import config
from core.image_utils import load_image
from services.export_service import (
    _array_to_data_uri,
    _to_rgb3,
    export_compare,
    make_before_after_html,
    make_compare_array,
    make_diff_heatmap,
)
from utils.logger import get_logger

log = get_logger("services.compare_service")

# ------------------------------------------------------------------------------
# 模式常量：与界面 Radio 的稳定 value 一一对应（界面只传这三个字符串）
# ------------------------------------------------------------------------------
MODE_CONCAT = "concat"
MODE_SLIDER = "slider"
MODE_HEATMAP = "heatmap"
COMPARE_MODES = (MODE_CONCAT, MODE_SLIDER, MODE_HEATMAP)


def _require_cv2():
    """延迟导入 cv2（缩放用），缺失时抛出可读的中文提示。"""
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - 环境缺依赖才会走到
        raise ImportError(
            "缺少 OpenCV 依赖（cv2）。请先执行：pip install -r requirements.txt"
        ) from exc
    return cv2


def normalize_mode(mode: "str | None") -> str:
    """
    把任意输入规整为合法的对比模式。

    参数说明：
        mode : 任意字符串（界面 Radio 的 value、配置项、用户输入均可）。

    返回值：
        concat / slider / heatmap 之一；空值或非法值回退为
        config.UI_COMPARE_DEFAULT_MODE，该配置本身非法时最终回退 concat。

    说明：
        界面层不需要自己做校验，直接把 Radio 的值丢进来即可。
    """
    m = str(mode or "").strip().lower()
    if m in COMPARE_MODES:
        return m
    fallback = str(getattr(config, "UI_COMPARE_DEFAULT_MODE", MODE_CONCAT)).strip().lower()
    return fallback if fallback in COMPARE_MODES else MODE_CONCAT


def shrink_for_view(arr: np.ndarray, max_side: Optional[int] = None) -> np.ndarray:
    """
    把数组等比缩到「长边 ≤ max_side」，用于网页内嵌预览。

    参数说明：
        arr      : H×W×3（或 H×W×4）uint8 数组。
        max_side : 长边上限；None 取 config.UI_COMPARE_MAX_SIDE。

    返回值：
        缩放后的 H×W×3 uint8 RGB 数组；本就不超限时原样返回（不拷贝）。

    说明：
        只影响「看」的这一路，导出落盘始终用原始分辨率。
    """
    a = _to_rgb3(arr)
    limit = int(config.UI_COMPARE_MAX_SIDE if max_side is None else max_side)
    limit = max(64, limit)
    h, w = a.shape[:2]
    longest = max(h, w)
    if longest <= limit or longest <= 0:
        return a
    cv2 = _require_cv2()
    scale = limit / float(longest)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return cv2.resize(a, (nw, nh), interpolation=cv2.INTER_AREA)


def align_pair(
    orig_arr: np.ndarray,
    result_arr: np.ndarray,
) -> "tuple[np.ndarray, np.ndarray]":
    """
    对齐一对「原图 / 结果」的尺寸（把结果缩放到原图尺寸）。

    参数说明：
        orig_arr   : 原图数组。
        result_arr : 结果数组（可能因真实超分而变大）。

    返回值：
        (原图, 对齐后的结果) 两个 H×W×3 uint8 数组。

    说明：
        滑块揭示与差异热图都要求两图像素级对齐，否则会出现「错位鬼影」，
        让人误以为修复失败。这里统一以原图尺寸为基准。
    """
    a = _to_rgb3(orig_arr)
    b = _to_rgb3(result_arr)
    if a.shape[:2] == b.shape[:2]:
        return a, b
    cv2 = _require_cv2()
    b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return a, b


def load_pair(
    orig_path: "str | None",
    result_path: "str | None",
    *,
    align: bool = True,
) -> "tuple[np.ndarray, np.ndarray] | None":
    """
    按路径加载「原图 + 结果」两张图。

    参数说明：
        orig_path   : 原图路径。
        result_path : 结果图路径。
        align       : 是否把结果缩放对齐到原图尺寸（默认是）。

    返回值：
        (原图数组, 结果数组)；任一路径为空 / 不存在 / 解码失败时返回 None
        （调用方据此展示「无法对比」提示，而不是抛异常炸掉界面）。
    """
    op, rp = str(orig_path or ""), str(result_path or "")
    if not op or not rp or not _os.path.isfile(op) or not _os.path.isfile(rp):
        return None
    try:
        a = load_image(op)
        b = load_image(rp)
    except Exception as exc:  # noqa: BLE001 - 解码失败不应中断界面
        log.warning("对比加载失败：%s / %s（%s）", op, rp, exc)
        return None
    if a is None or b is None:
        return None
    return align_pair(a, b) if align else (_to_rgb3(a), _to_rgb3(b))


def _img_html(uri: str, alt: str, caption: str = "") -> str:
    """把一张 data URI 包成带圆角边框的 <img>（可选灰色小字说明）。"""
    cap = (
        f"<p style='color:#64748b;font-size:12px;margin:4px 0 0;'>{caption}</p>"
        if caption else ""
    )
    return (
        f"<img src='{uri}' alt='{alt}' "
        f"style='max-width:100%;border:1px solid #e5e7eb;border-radius:8px;'>"
        f"{cap}"
    )


def render_compare_html(
    orig_arr,
    result_arr,
    mode: "str | None" = None,
    *,
    before_label: str = "Before",
    after_label: str = "After",
    heatmap_caption: str = "",
    max_side: Optional[int] = None,
) -> str:
    """
    由两个数组渲染对比 HTML（全站唯一实现，三种模式）。

    参数说明：
        orig_arr        : 原图数组（H×W×3/4 uint8）。
        result_arr      : 结果数组。
        mode            : concat / slider / heatmap，非法值自动回退。
        before_label    : 滑块模式左上角标签文案（已本地化的文本由界面层传入）。
        after_label     : 滑块模式右上角标签文案。
        heatmap_caption : 热图下方说明文案（界面层传本地化文本；空则不显示）。
        max_side        : 渲染缩放上限；None 取 config.UI_COMPARE_MAX_SIDE。

    返回值：
        可直接交给 gr.HTML 渲染的字符串；入参为空返回空串，渲染异常返回空串
        （由界面层决定展示什么提示，服务层不硬编码 UI 文案）。
    """
    if orig_arr is None or result_arr is None:
        return ""
    try:
        a, b = align_pair(orig_arr, result_arr)
        a = shrink_for_view(a, max_side)
        b = shrink_for_view(b, max_side)
        m = normalize_mode(mode)
        if m == MODE_SLIDER:
            return make_before_after_html(
                a, b, before_label=before_label, after_label=after_label
            )
        if m == MODE_HEATMAP:
            heat = make_diff_heatmap(a, b)
            return _img_html(_array_to_data_uri(heat), "heatmap", heatmap_caption)
        concat = make_compare_array(a, b)
        return _img_html(_array_to_data_uri(concat), "concat")
    except Exception as exc:  # noqa: BLE001 - 渲染失败不应中断界面
        log.warning("对比渲染失败（模式=%s）：%s", mode, exc)
        return ""


def render_pair_html(
    orig_path: "str | None",
    result_path: "str | None",
    mode: "str | None" = None,
    **kwargs,
) -> str:
    """
    由两个「路径」渲染对比 HTML（画廊点选场景的主入口）。

    参数说明：
        orig_path   : 原图路径。
        result_path : 结果图路径。
        mode        : 对比模式。
        kwargs      : 透传给 render_compare_html（标签文案 / max_side）。

    返回值：
        对比 HTML；任一图缺失或渲染失败返回空串。
    """
    pair = load_pair(orig_path, result_path)
    if pair is None:
        return ""
    return render_compare_html(pair[0], pair[1], mode, **kwargs)


def summarize_pair(
    orig_path: "str | None",
    result_path: "str | None",
    *,
    changed_threshold: int = 8,
) -> "dict | None":
    """
    计算一对「原图 / 结果」的差异摘要（给查看器展示客观数字，而非只看图）。

    参数说明：
        orig_path         : 原图路径。
        result_path       : 结果图路径。
        changed_threshold : 判定「该像素被改动」的灰度差阈值（0~255），默认 8，
                            低于该值视为编码噪声，不计入改动占比。

    返回值：
        字典或 None（图缺失时）：
          width / height        : 原图尺寸；
          result_width/height   : 结果原始尺寸（真实超分后会大于原图）；
          upscaled              : 结果是否被放大；
          changed_ratio         : 改动像素占比（0~1）；
          mean_diff             : 全图平均灰度差（0~255）；
          max_diff              : 最大灰度差（0~255）。

    说明：
        改动占比可直接用来判断「这次修复到底动了多少地方」——占比过高往往
        意味着掩码画大了（整图被重绘），占比接近 0 则说明水印可能没被覆盖到。
    """
    op, rp = str(orig_path or ""), str(result_path or "")
    if not op or not rp or not _os.path.isfile(op) or not _os.path.isfile(rp):
        return None
    try:
        a_raw = load_image(op)
        b_raw = load_image(rp)
    except Exception as exc:  # noqa: BLE001
        log.warning("差异摘要加载失败：%s", exc)
        return None
    if a_raw is None or b_raw is None:
        return None

    a3 = _to_rgb3(a_raw)
    b3 = _to_rgb3(b_raw)
    oh, ow = a3.shape[:2]  # 原图原始尺寸（用于判定「结果是否被放大」）
    rh, rw = b3.shape[:2]
    a, b = align_pair(a3, b3)
    h, w = a.shape[:2]
    if h == 0 or w == 0:
        return None

    diff = np.abs(a.astype(np.float32) - b.astype(np.float32))
    if diff.ndim == 3:
        diff = diff.mean(axis=2)
    thr = max(0, int(changed_threshold))
    changed = float((diff > thr).sum()) / float(h * w)
    return {
        "width": int(w),
        "height": int(h),
        "result_width": int(rw),
        "result_height": int(rh),
        # 注意：upscaled 指「结果原始尺寸 > 原图原始尺寸」，必须基于两者**原始**
        # 尺寸比较（阶段 124 修复：旧逻辑用 (raw结果) != (对齐后尺寸) 判定，当结果
        # 比原图**小**时也会误报 True，实则未放大）。
        "upscaled": bool((rw, rh) > (ow, oh)),
        "changed_ratio": round(changed, 6),
        "mean_diff": round(float(diff.mean()), 3),
        "max_diff": round(float(diff.max()), 3),
    }


def export_pair_compare(
    orig_path: "str | None",
    result_path: "str | None",
    *,
    gap: Optional[int] = None,
    label: Optional[bool] = None,
) -> "str | None":
    """
    把一对「原图 / 结果」落盘为拼接对比图（PNG，原始分辨率）。

    参数说明：
        orig_path   : 原图路径。
        result_path : 结果图路径。
        gap         : 两图间白边像素，None 取 config.EXPORT_COMPARE_GAP。
        label       : 是否标注 Before/After，None 取 config.EXPORT_COMPARE_LABEL。

    返回值：
        对比图绝对路径；任一图缺失或写盘失败返回 None（不抛异常）。

    说明：
        与查看器里的「看」不同，这里是「带走」——所以不做 max_side 缩放，
        直接复用 export_service.export_compare 落到 EXPORT_DIR。
    """
    op, rp = str(orig_path or ""), str(result_path or "")
    if not op or not rp or not _os.path.isfile(op) or not _os.path.isfile(rp):
        return None
    try:
        return export_compare(op, rp, gap=gap, label=label)
    except Exception as exc:  # noqa: BLE001
        log.warning("导出对比图失败：%s / %s（%s）", op, rp, exc)
        return None


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    import tempfile

    cv2 = _require_cv2()
    tmp = tempfile.mkdtemp(prefix="compare_service_selftest_")
    src = _os.path.join(tmp, "原图.png")
    res = _os.path.join(tmp, "结果.png")

    # 造一对图：原图带一块「水印」白斑，结果把它抹掉。
    # 注意：cv2.imwrite 不支持中文路径，这里统一走 core.image_utils.save_image。
    from core.image_utils import save_image as _save_image

    base = np.full((120, 160, 3), 90, dtype=np.uint8)
    marked = base.copy()
    marked[20:40, 30:90] = 240
    _save_image(src, marked, fmt="PNG")
    _save_image(res, base, fmt="PNG")

    # 用例 1：模式规整。
    assert normalize_mode("SLIDER") == "slider", "模式规整失败"
    assert normalize_mode("不存在的模式") in COMPARE_MODES, "非法模式未回退"
    print("用例1通过：模式规整正常")

    # 用例 2：加载配对 + 尺寸对齐。
    pair = load_pair(src, res)
    assert pair is not None and pair[0].shape == pair[1].shape, "配对加载失败"
    assert load_pair(src, _os.path.join(tmp, "不存在.png")) is None, "缺图未返回 None"
    print("用例2通过：配对加载与容错正常")

    # 用例 3：三种模式都能出 HTML。
    for _m in COMPARE_MODES:
        html = render_pair_html(src, res, _m, heatmap_caption="差异热图")
        assert html and "<" in html, f"{_m} 模式渲染为空"
    print("用例3通过：三种模式渲染正常")

    # 用例 4：差异摘要（白斑约占 20×60 / 120×160 = 6.25%）。
    s = summarize_pair(src, res)
    assert s is not None and 0.05 < s["changed_ratio"] < 0.08, f"改动占比异常：{s}"
    assert s["upscaled"] is False, "未放大却判定为放大"
    print(f"用例4通过：差异摘要 {s}")

    # 用例 5：落盘导出。
    out = export_pair_compare(src, res)
    assert out and _os.path.isfile(out), "对比图导出失败"
    print(f"用例5通过：对比图 {out}")

    # 用例 6：结果比原图小时，upscaled 必须为 False（阶段 124 修复前会误报 True）。
    res_small = _os.path.join(tmp, "小结果.png")
    small_base = np.full((60, 80, 3), 90, dtype=np.uint8)
    _save_image(res_small, small_base, fmt="PNG")
    sm = summarize_pair(src, res_small)
    assert sm is not None and sm["upscaled"] is False, f"结果更小却误报放大：{sm}"
    assert sm["result_width"] == 80 and sm["result_height"] == 60, "结果原始尺寸记录错误"
    print(f"用例6通过：结果更小时 upscaled=False（{sm['result_width']}x{sm['result_height']}）")

    print("compare_service.py 自检全部通过。")
