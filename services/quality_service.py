# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/quality_service.py
 文件说明 : 修复结果质量评估（阶段 8 核心服务，服务层）。

            修复完成后，给每张结果打个「综合质量分」，帮用户快速筛出：
              · 可能没修干净（水印还留着）的图  → 级别 review
              · 可能修过头（误伤原图）的图      → 级别 warn
              · 衔接良好、可放心使用            → 级别 good

            指标：
              1) SSIM（结构相似度，0~1）：自实现高斯加权版，无需 scikit-image。
                 衡量修复区域与周围原图在「结构 / 亮度 / 对比度」上的接近程度。
              2) changed_ratio（变化像素比例，0~1）：
                 结果相对原图有多少比例的像素发生了肉眼可见的变化。

            综合判定逻辑（详见 evaluate_quality 源码与 docs/08）：
              · 变化比例过低  → 水印大概率没被修掉 → review
              · 变化比例过高且 SSIM 过低 → 大面积被改写 → warn（过处理）
              · 其余          → good

            设计约束：
            · 服务层模块，严禁 import ui / gradio。
            · 不引入新第三方依赖（SSIM 自己用 numpy + cv2 实现）。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from dataclasses import dataclass

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np
import cv2  # type: ignore

import config
from core.image_utils import load_image_with_alpha
from utils.logger import get_logger

log = get_logger("services.quality_service")

# SSIM 常量（L=255 时的标准取值）。
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


@dataclass
class QualityReport:
    """单张结果的质量评估报告。"""
    ssim: float               # 结构相似度（0~1，越高越像原图结构）
    changed_ratio: float      # 变化像素比例（0~1）
    score: int                # 综合质量分（0~100，越高越好）
    level: str                # "good" / "warn" / "review"
    detail: str               # 给用户的自然语言说明


def _gaussian_window(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    """生成归一化的二维高斯核，用于 SSIM 的局部加权。"""
    ax = np.arange(-size // 2 + 1, size // 2 + 1, dtype=np.float64)
    xx, yy = np.meshgrid(ax, ax)
    kernel = np.exp(-(xx ** 2 + yy ** 2) / (2 * sigma ** 2))
    return kernel / kernel.sum()


def _ssim_channel(x: np.ndarray, y: np.ndarray, window: np.ndarray) -> float:
    """
    单通道高斯加权 SSIM。

    参数：
        x, y : 同为 H×W 的数组（同一通道的原图与结果）。**入口统一规整为 float64**，
               调用方传 uint8 / float32 均可。
        window : 归一化二维高斯核。
    返回：该通道 SSIM 均值（理论值域 -1~1，实际图像通常落在 0~1）。

    阶段 87 修复（P0）：
        本函数契约声明 x/y 为 float64，但调用方（`_ssim`）实际传入的是 **uint8**，
        而实现里的 `x * x` / `x * y` 在 uint8 上会**按 256 取模溢出**
        （例：200*200 = 64 而非 40000）→ sigma_x2 / sigma_xy 全错 →
        SSIM 跑到理论值域之外（阶段 87 实测量值域 **0.21 ~ 3.92**），
        且**偏差方向随图像内容变化、不可预测**（同一「底图 vs 噪声」场景，
        实测既有 0.31 也有 1.78）。
        之所以长期未被发现：常量图自比时溢出项恰好相互抵消，结果仍为 1.0，
        而模块自检用的正是平滑常量块图 —— 自检永远绿（经验 26：值域必须用
        探针直接断言，「不报错 + 形状对」抓不住这类错误）。
        按经验 44（dtype 决定语义，且只在单一实现里做一次），在此唯一实现处
        统一规整。
    """
    # dtype 规整：SSIM 公式含 x*x 与 x*y，整型（尤其 uint8）会溢出，必须先转 float64。
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    mu_x = cv2.filter2D(x, -1, window)
    mu_y = cv2.filter2D(y, -1, window)
    mu_x2 = mu_x * mu_x
    mu_y2 = mu_y * mu_y
    mu_xy = mu_x * mu_y
    sigma_x2 = cv2.filter2D(x * x, -1, window) - mu_x2
    sigma_y2 = cv2.filter2D(y * y, -1, window) - mu_y2
    sigma_xy = cv2.filter2D(x * y, -1, window) - mu_xy

    num = (2 * mu_xy + _C1) * (2 * sigma_xy + _C2)
    den = (mu_x2 + mu_y2 + _C1) * (sigma_x2 + sigma_y2 + _C2)
    # 分母可能为 0（两图全黑区域），用极小值兜底避免除零得到 nan。
    den = np.where(den == 0, 1e-8, den)
    ssim_map = num / den
    return float(np.mean(ssim_map))


def _ssim(img1: np.ndarray, img2: np.ndarray) -> float:
    """三通道平均 SSIM（输入 H×W×3 uint8）。"""
    window = _gaussian_window()
    scores = [_ssim_channel(img1[:, :, c], img2[:, :, c], window) for c in range(3)]
    return float(np.mean(scores))


def _changed_ratio(img1: np.ndarray, img2: np.ndarray, thresh: int = 12) -> float:
    """
    变化像素比例：两图逐像素亮度差超过 thresh 的像素占比（0~1）。
    thresh=12 表示「肉眼勉强可辨」的差距才算变化，过滤掉压缩噪声。
    """
    diff = np.abs(img1.astype(np.int32) - img2.astype(np.int32))
    changed = (diff > thresh).any(axis=2)
    return float(changed.mean())


def _downscale_for_eval(
    o_rgb: np.ndarray, r_rgb: np.ndarray
) -> "tuple[np.ndarray, np.ndarray]":
    """
    阶段 16 新增：超大图评估前的等比降采样。

    当任一图（原图或结果）任一边长超过 config.QUALITY_MAX_EVAL_SIDE 时，
    把两张图按「同一目标边长」等比缩放到该上限以内，再交给 SSIM / 变化比例
    评估。两者同步缩放保持像素一一对应，启发式分级结论基本不变，但 SSIM 的
    filter2D 开销随面积下降约 4 倍。设为 0 表示始终用原图评估。

    阶段 140 修复（P1，「声明与实现不符」）：
        原实现的 `_rsz` 对两张图**各自**按自身边长判断「是否超限」并各自计算
        `scale = max_side / max(h, w)`，因此它**不是**「同一目标边长」：
          · 原图 800×800（未超限）+ 结果图 1600×1600（超限）
            → 原图原样返回 800²，结果图缩到 1024²，两图尺寸从此不一致；
          · 宽高比不同时（如原 1600²、结果 2000×2400）
            → 缩成 1024×1024 与 1024×853，同样不一致。
        尺寸不一致会把评估推给下游那句 `cv2.resize(r_rgb, 原图尺寸)`，
        而那是**非等比**回缩：内容被拉伸/压扁，SSIM 与 changed_ratio 双双失真。
        实测（探针 C1/D2）：同一对图，正确路径 good/99，走此路径得 warn/83；
        「仅结果图超上限」场景 warn/81 → 修复后 good/99。
        危害窗口正是 `postprocess_service.evaluate_improvement`（超分放大后对比）
        与任何「结果图尺寸 ≠ 原图」的调用方，且**在默认配置下就会触发**。
        修法（经验 44：归一化只做一次、且在对齐唯一入口做）：
          先让结果图**等比回缩到原图尺寸**，再对两图施加**同一个目标尺寸**。

    注意：cv2.resize 返回新数组，绝不修改传入的（可能被解码缓存持有的）原数组。
    """
    # 阶段 140：先对齐尺寸（结果图等比回缩到原图），使后续同步缩放成立。
    if o_rgb.shape[:2] != r_rgb.shape[:2]:
        r_rgb = cv2.resize(
            r_rgb, (o_rgb.shape[1], o_rgb.shape[0]), interpolation=cv2.INTER_AREA
        )

    max_side = int(getattr(config, "QUALITY_MAX_EVAL_SIDE", 0) or 0)
    if max_side <= 0:
        return o_rgb, r_rgb
    oh, ow = o_rgb.shape[:2]
    if max(oh, ow) <= max_side:
        return o_rgb, r_rgb

    # 两图共用同一目标尺寸（此时二者已是同尺寸，宽高比必然一致）。
    scale = max_side / max(oh, ow)
    target = (max(1, int(round(ow * scale))), max(1, int(round(oh * scale))))
    return (
        cv2.resize(o_rgb, target, interpolation=cv2.INTER_AREA),
        cv2.resize(r_rgb, target, interpolation=cv2.INTER_AREA),
    )


def _as_rgb3(arr: np.ndarray) -> np.ndarray:
    """
    把输入统一规整为 H×W×3 uint8（BGR，OpenCV 排布）。

    阶段 140 新增（P1，与 135 `services/file_service._as_rgb3` 同族）：
        `_ssim` / `_changed_ratio` 都硬编码「三通道」假设
        （`img[:, :, c] for c in range(3)`、`(diff > thresh).any(axis=2)`），
        但 `evaluate_quality_arrays` 的入参从未归一化：
          · 灰度 2D  → `IndexError: too many indices for array`（_ssim 处直接崩）；
          · RGBA 4C  → 能跑完，但第 4 通道也被算进 SSIM，且
                       `changed_ratio` 恒接近 1.0（alpha 差异被当作变化）。
        返回值：H×W×3 uint8。2D 灰度复制三通道；4 通道丢 alpha；其余原样返回
                （形状非法交由下游 cv2 报错，不做过度设计）。
    """
    a = np.asarray(arr)
    if a.ndim == 2:
        a = np.stack([a, a, a], axis=-1)
    elif a.ndim == 3 and a.shape[2] == 4:
        a = a[:, :, :3]
    return a


def evaluate_quality_arrays(
    original_rgb: np.ndarray,
    result_rgb: np.ndarray,
) -> QualityReport:
    """
    数组级质量评估（不读写文件）：给定「原图数组」与「结果数组」返回 QualityReport。

    参数：
        original_rgb : 原图 H×W×3 uint8（BGR，OpenCV 排布）。
        result_rgb   : 修复结果 H×W×3 uint8。

    返回：QualityReport 数据类。供 process_image / postprocess_service 在内存中
          直接对比「后处理前 / 后处理後」结果，避免落盘再读取。
    """
    # 阶段 140：入口统一规整通道（灰度 2D / RGBA 4C → H×W×3），再进后续通路。
    original_rgb = _as_rgb3(original_rgb)
    result_rgb = _as_rgb3(result_rgb)

    # 阶段16：超大图先等比降采样，加速 SSIM / 变化比例评估（不改变分级结论）。
    o_rgb, r_rgb = _downscale_for_eval(original_rgb, result_rgb)

    # 结果图理论上与原图同尺寸；若不一致（极端情况）缩放到原图尺寸再比。
    # 阶段 140：`_downscale_for_eval` 现已保证两图同尺寸，此分支仅为防御性兜底。
    if o_rgb.shape[:2] != r_rgb.shape[:2]:
        r_rgb = cv2.resize(r_rgb, (o_rgb.shape[1], o_rgb.shape[0]),
                           interpolation=cv2.INTER_AREA)

    ssim = _ssim(o_rgb, r_rgb)
    cr = _changed_ratio(o_rgb, r_rgb)

    # ---------- 综合判定 ----------
    if cr < config.QUALITY_REVIEW_CHANGED_MIN:
        # 几乎没改动 → 水印大概率还在。
        level = "review"
        score = round(45 + 35 * ssim)
        detail = "变化像素过少，水印可能未被修掉，建议复核原图。"
    elif cr > config.QUALITY_WARN_CHANGED_MAX and ssim < config.QUALITY_SSIM_WARN:
        # 改动巨大且结构差异明显 → 过处理。
        level = "warn"
        score = round(55 + 35 * ssim)
        detail = "改动区域过大且结构变化明显，可能修复过头（误伤原图）。"
    elif ssim < config.QUALITY_SSIM_WARN:
        level = "warn"
        score = round(62 + 33 * ssim)
        detail = "与原图结构差异偏大，请留意修复区域边缘是否自然。"
    else:
        level = "good"
        score = round(72 + 28 * ssim)
        detail = "修复区域与原图衔接良好。"

    score = max(0, min(100, int(score)))
    # 分数跌破硬性阈值也强制标记为需复核。
    # 阶段 87 修复（状态与观测对齐，经验 21 同族）：
    #   原实现只改 level 不改 detail，会产出「级别=review」却配着 warn 措辞的
    #   自相矛盾报告（实测：level=review + detail="改动区域过大…可能修复过头"）。
    #   用户按 review 去查「水印没修掉」，看到的却是「修过头」——两种诊断方向相反。
    #   修法：改写 level 时同步在 detail 开头声明复核原因，并保留原有诊断信息。
    if score < config.QUALITY_SCORE_REVIEW:
        level = "review"
        detail = (
            f"综合质量分 {score} 低于复核线 {int(config.QUALITY_SCORE_REVIEW)}，"
            f"建议人工复核；{detail}"
        )

    return QualityReport(
        ssim=round(ssim, 4),
        changed_ratio=round(cr, 4),
        score=score,
        level=level,
        detail=detail,
    )


def evaluate_quality(
    original_path: str | _os.PathLike,
    result_path: str | _os.PathLike,
) -> QualityReport:
    """
    评估一张修复结果的综合质量（路径版）。

    参数：
        original_path : 原图路径。
        result_path   : 修复结果路径。

    返回：QualityReport 数据类。内部加载后转「数组级评估」，
          方便其它模块（如 postprocess_service）复用同一套评分逻辑。
    """
    o_rgb, _ = load_image_with_alpha(original_path)
    r_rgb, _ = load_image_with_alpha(result_path)
    return evaluate_quality_arrays(o_rgb, r_rgb)


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    import tempfile

    # 造一张「干净底图」和「带半透明水印的原图」。
    h, w = 160, 240
    base = np.full((h, w, 3), 235, dtype=np.uint8)
    orig = base.copy()
    cv2.rectangle(orig, (40, 60), (200, 110), (200, 200, 200), -1)  # 灰块水印

    _tmp_paths: list[str] = []

    def _save(arr):
        t = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        t.close()
        _tmp_paths.append(t.name)
        cv2.imwrite(t.name, cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
        return t.name

    # 阶段 140 修复（P2，与 136 `video_export` 自检 mkdtemp 泄漏同族）：
    #   自检用 `NamedTemporaryFile(delete=False)` 落盘 4 个 png，末尾才统一 `remove`。
    #   断言一旦失败（或 cv2.imwrite 抛错）就**跳过清理**——探针 E1 实测崩溃时残留
    #   4 个 tmp*.png。修法：路径集中登记 + try/finally 兜底清理。
    try:
        p_base = _save(base)
        p_orig = _save(orig)
        p_fixed = _save(base)        # 修得好：结果接近底图
        p_notfixed = _save(orig)     # 没修掉：结果=原图

        # 用例 1：修得好 → good。
        q1 = evaluate_quality(p_orig, p_fixed)
        assert q1.level == "good", f"用例1应为 good，实际 {q1}"
        print(f"用例1通过：修得好 → level={q1.level} score={q1.score} ssim={q1.ssim}")

        # 用例 2：没修掉 → review。
        q2 = evaluate_quality(p_orig, p_notfixed)
        assert q2.level == "review", f"用例2应为 review，实际 {q2}"
        print(f"用例2通过：没修掉 → level={q2.level} score={q2.score} changed={q2.changed_ratio}")

        # 用例 3：原图 vs 原图（自己比自己）→ changed=0 触发 review。
        #         这符合启发式意图：真实业务里"结果与原图一模一样"几乎只发生在
        #         「没检测到/没修掉」，而此类图在管线层已被 skipped，不会进入评估，
        #         所以这里标 review 是合理的软提示。
        q3 = evaluate_quality(p_orig, p_orig)
        assert q3.level == "review", f"用例3应为 review，实际 {q3}"
        print(f"用例3通过：自身对比 → level={q3.level} score={q3.score} ssim={q3.ssim}")

        # 用例 4（阶段 140）：尺寸不一致（结果被超分放大）时，降采样仍须保持
        #   两图同尺寸，且分级不得因「非等比回缩」而失真。
        big_o = np.full((64, 64, 3), 200, dtype=np.uint8)
        big_r = np.full((128, 128, 3), 200, dtype=np.uint8)  # 分辨率翻倍
        do, dr = _downscale_for_eval(big_o, big_r)
        assert do.shape[:2] == dr.shape[:2], (
            f"降采样后两图尺寸应一致，实际 {do.shape[:2]} vs {dr.shape[:2]}"
        )

        # 用例 5（阶段 140）：灰度 2D / RGBA 4C 输入应被归一化，而非崩溃。
        g = np.full((32, 32), 200, dtype=np.uint8)
        g2 = np.full((32, 32), 120, dtype=np.uint8)
        qg = evaluate_quality_arrays(g, g2)
        assert qg.level in ("good", "warn", "review"), f"灰度输入异常：{qg}"
        rgba = np.full((32, 32, 4), 200, dtype=np.uint8)
        rgba2 = np.full((32, 32, 4), 200, dtype=np.uint8)
        qa4 = evaluate_quality_arrays(rgba, rgba2)
        assert qa4.changed_ratio == 0.0, (
            f"RGBA 自身对比 changed_ratio 应为 0，实际 {qa4.changed_ratio}"
        )
        print(f"用例4/5通过：降采样尺寸一致 + 灰度/RGBA 归一化")
    finally:
        for _p in _tmp_paths:
            try:
                _os.remove(_p)
            except OSError:
                pass

    print("quality_service.py 自检通过。")
