# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/preset_recommender.py
 文件说明 : 检测自动预设推荐（阶段 25 新增，核心层）。

            很多用户面对 10 个检测预设（general / ai_tiled / ai_signature /
            ai_corner / ai_text_heavy / ai_faint_tiled / ai_grid / ai_gradient /
            ai_logo / ai_multi）不知道选哪个。本模块做一次**轻量图像特征分析**，自动挑出
            最可能命中的水印预设，省去手动试错。

            分析的特征（均来自 detector 各策略的成熟思路，但独立实现，避免与
            detector 私有函数耦合）：
              1. 全局渐变解释率（平面拟合）→ ai_gradient
              2. 平铺周期自相关强度 + 标记占比 → ai_grid（稀疏点阵）/ ai_tiled /
                 ai_faint_tiled（填充式平铺，极淡→faint）
              3. 角落实心轮廓实心率 → ai_logo
              4. 整图笔画密度 → ai_text_heavy；仅底部带密度高 → ai_signature
              5. 上述皆不命中 → general（综合策略兜底）

            设计约束：
            · 纯算法层，仅依赖 numpy + OpenCV + config，零 gradio / torch / OCR；
              推荐过程**不调用 OCR**（避免无 torch 环境崩溃，也避免重型依赖）。
            · 不调用 detect_watermark_mask（那是「用预设去检测」），只做特征评分；
              评分公式复用 detector 思路（中值背景残差 / 投影自相关 / 平面拟合 /
              Canny 轮廓实心率 / Sobel 笔画密度），但独立实现。
            · 返回 Recommendation（机器可读）：preset / scores / triggered /
              confidence；本地化文案由 UI / i18n 层映射，核心层不负责翻译。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

import config
from utils.logger import get_logger

log = get_logger("core.preset_recommender")


# ==============================================================================
# 数据结构
# ==============================================================================
@dataclass
class Recommendation:
    """一次预设推荐的结果（机器可读，便于 CLI / API / UI 统一消费）。"""

    preset: str                               # 推荐的内置预设名
    scores: Dict[str, float] = field(default_factory=dict)   # 各特征激活分 0~1
    triggered: List[str] = field(default_factory=list)       # 命中的特征键（优先级最高者）
    confidence: float = 0.0                   # 置信度（= 各特征激活分的最大值）

    def to_dict(self) -> Dict[str, object]:
        return {
            "preset": self.preset,
            "scores": dict(self.scores),
            "triggered": list(self.triggered),
            "confidence": float(self.confidence),
        }

    def __str__(self) -> str:
        feats = ",".join(self.triggered) if self.triggered else "无"
        return (
            f"推荐预设：{self.preset}（置信度 {self.confidence:.2f}）；"
            f"触发特征：{feats}"
        )


# ==============================================================================
# 内部工具
# ==============================================================================
def _require_cv2():
    """延迟导入 OpenCV（避免在纯单测 / 无 cv2 环境硬依赖）。"""
    import cv2
    return cv2


def _ensure_rgb(image: np.ndarray) -> np.ndarray:
    """把任意数组规整为 (H, W, 3) uint8 RGB。"""
    if image is None:
        raise ValueError("图像为空，无法推荐预设。")
    arr = np.asarray(image)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=2)
    elif arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]
    elif arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"无法识别的图像维度：ndim={arr.ndim}, shape={arr.shape}")
    return arr.astype(np.uint8)


def _build_residual(rgb: np.ndarray):
    """
    中值背景估计 + 局部归一化残差（与 detector 半透明检测同源思路）。

    返回：(gray_u8, residual, score, h, w)
      · gray_u8 : uint8 灰度图（供 Canny / 平面拟合复用）
      · residual: 残差幅度（|gray - 中值背景|），uint8 量级浮点
      · score   : 局部归一化分数 = residual / (局部标准差 + eps)
    """
    cv2 = _require_cv2()
    h, w = rgb.shape[:2]
    gray_u8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = gray_u8.astype(np.float32)

    ksize = max(15, min(h, w) // 24)
    ksize = ksize + 1 if ksize % 2 == 0 else ksize
    bg = cv2.medianBlur(gray_u8, ksize).astype(np.float32)
    residual = np.abs(gray - bg)

    block = max(15, min(h, w) // 16)
    block = block + 1 if block % 2 == 0 else block
    local_mean = cv2.boxFilter(gray, ddepth=-1, ksize=(block, block))
    local_sq = cv2.boxFilter(gray * gray, ddepth=-1, ksize=(block, block))
    local_var = np.clip(local_sq - local_mean * local_mean, 0.0, None)
    local_std = np.sqrt(local_var) + 1e-3
    score = residual / local_std
    return gray_u8, residual, score, h, w


def _marks_at(score: np.ndarray, sensitivity: float, residual: np.ndarray = None) -> np.ndarray:
    """把归一化分数阈值化成 0/255 掩码（阶段 59 重写，纹理鲁棒）。

    旧实现用百分位（q = 100 − 0.85·敏感度），在纹理/照片底图上**恒定选出 ~46.75%
    像素**——覆盖/周期/规则性特征全被背景纹理主导，grid 点阵被判为 general、极淡
    平铺被判为满图文字（与 detector 阶段 49 修掉的「分位数恒定选比例像素」同一
    反模式，见 docs/59-*）。

    新实现：稳健统计门限（与 detector 阶段 49 的绝对证据门限同思路）——
      marks = (residual ≥ RECOMMEND_MARKS_ABS_MIN) ∧
              (score > median(score) + k·1.4826·MAD(score))
    背景纹理残差被稳健统计压灭（实测干净 textured/photo 覆盖率 ~0.1%），
    只有真正的局部异常（水印点/块/文字）能留下标记。
    参数 sensitivity 保留仅为 API 兼容（不再决定固定比例）。
    """
    if residual is None:
        # 兼容旧调用：无残差输入时退回纯分数门限（仅测试/自检使用）。
        finite = score[np.isfinite(score)]
        if finite.size == 0:
            return np.zeros(score.shape[:2], dtype=np.uint8)
        med = float(np.median(finite))
        mad = float(np.median(np.abs(finite - med)))
        k = config.RECOMMEND_MARKS_SIGMA_K
        thr = med + k * (1.4826 * mad + 1e-6)
        return (score > thr).astype(np.uint8) * 255
    finite = score[np.isfinite(score)]
    if finite.size == 0:
        return np.zeros(score.shape[:2], dtype=np.uint8)
    med = float(np.median(finite))
    mad = float(np.median(np.abs(finite - med)))
    k = config.RECOMMEND_MARKS_SIGMA_K
    thr = med + k * (1.4826 * mad + 1e-6)
    abs_floor = float(config.RECOMMEND_MARKS_ABS_MIN)
    marks = (score > thr) & (residual >= abs_floor)
    return marks.astype(np.uint8) * 255


def _dominant_period(proj: np.ndarray) -> Tuple[float, float]:
    """
    一维自相关找主导周期（与 detector._dominant_period 同算法，独立实现）。

    返回 (period, strength)：period 为估计周期（像素），strength∈[0,1] 为
    自相关相对强度（越高越像规则周期）。找不到时 period=-1.0。
    """
    n = proj.size
    if n < 8 or float(np.sum(proj)) < 1.0:
        return -1.0, 0.0
    p = proj.astype(np.float32)
    p = p - p.mean()
    if p.std() < 1e-6:
        return -1.0, 0.0
    ac = np.correlate(p, p, mode="full")
    ac = ac[ac.size // 2:]
    if ac[0] <= 0:
        return -1.0, 0.0
    min_lag = max(4, n // 40)
    hi = min(n, min_lag * 20)
    if hi <= min_lag:
        return -1.0, 0.0
    ac_win = ac[min_lag:hi]
    lag = int(np.argmax(ac_win)) + min_lag
    strength = float(ac[lag] / (ac[0] + 1e-6))
    return float(lag), strength


def _periodicity(marks: np.ndarray, h: int, w: int) -> Tuple[bool, float]:
    """行列投影自相关判断是否存在规则周期。返回 (是否周期, 强度)。

    ⚠ **阶段 109 修正（P0）**：判据由 **AND 改为 OR** —— 与
    `detector.detect_grid_watermark` 对齐。检测器那边明确写着「**只要求「至少一个方向」
    有合理周期，不能要求两个方向都有**」，理由是**可分离网格图案** `g(x,y)=f(x)·f(y)`
    的自相关同样可分离（`acf(38,0)=a` 而 `acf(38,38)=a²`，因 `a<1` 轴上峰值必高于对角峰值）
    ⇒ 要求两方向同时非零会漏掉最常见的网格形态。

    推荐器此前的 AND 比它要推荐的检测器**更严**，实测后果（可复现，用户可见）：
      · **一维周期图案（竖/横条纹）**被判为「非周期」⇒ 不满足 `grid_on`/`tiled_periodic`
        ⇒ 落到文字分支 ⇒ 推荐 `ai_text_heavy`；
      · 而 `ai_text_heavy` **不启用网格通路** ⇒ 该图案检测 **recall = 0.000**
        （同一张图手动选 `ai_tiled` / `ai_grid` / `ai_multi` 均为 **0.769**）。
    ⇒ 自动推荐把「检得出的」变成「检不出」，是最坏的一种推荐错误。

    修法的副作用已量化：30 张真实照片上的推荐分布**逐项不变**
    （`ai_multi×20 + ai_text_heavy×10`，`%TEMP%/s109_fix_proto.py`）。

    强度取法：**两个方向都过 ⇒ 取较小者**（与原语义一致）；**只一个方向过 ⇒ 取该方向**。
    """
    row_proj = marks.sum(axis=1)
    col_proj = marks.sum(axis=0)
    period_y, sy = _dominant_period(row_proj)
    period_x, sx = _dominant_period(col_proj)

    def _ok(p: float, ax_len: int) -> bool:
        return p > 0 and 6 <= p <= 0.6 * ax_len

    ok_y, ok_x = _ok(period_y, h), _ok(period_x, w)
    if not (ok_y or ok_x):
        return False, 0.0
    return True, min(s for s, ok in ((sy, ok_y), (sx, ok_x)) if ok)


def _gradient_features(gray_u8: np.ndarray) -> Tuple[float, float, float]:
    """
    全局渐变平面拟合（与 detector 渐变检测同源）。

    返回 (explained, plane_mag, res_std)：
      · explained  : 平面解释的全局方差占比 0~1（越高越像「占主导的全局渐变」）
      · plane_mag  : 平面幅度 √(a²+b²)（越大渐变越明显）
      · res_std    : 残差标准差（越小说明除渐变外细节越少）
    """
    cv2 = _require_cv2()
    ds = 256
    small = cv2.resize(gray_u8, (ds, ds), interpolation=cv2.INTER_AREA).astype(np.float32)
    sh, sw = small.shape[:2]
    ys, xs = np.mgrid[0:sh, 0:sw].astype(np.float32)
    xn = xs / max(1, sw - 1)
    yn = ys / max(1, sh - 1)
    A = np.stack([np.ones_like(xn.ravel()), xn.ravel(), yn.ravel()], axis=1)
    z = small.ravel()
    try:
        coeff, *_ = np.linalg.lstsq(A, z, rcond=None)
    except Exception:
        return 0.0, 0.0, 0.0
    c, a, b = coeff[0], coeff[1], coeff[2]
    plane = c + a * xn + b * yn
    residual = small - plane
    var_gray = float(np.var(small))
    var_res = float(np.var(residual))
    if var_gray < 1e-6:
        return 0.0, 0.0, 0.0
    explained = max(0.0, min(1.0, 1.0 - var_res / var_gray))
    plane_mag = float(np.sqrt(a * a + b * b))
    res_std = float(np.sqrt(var_res))
    return explained, plane_mag, res_std


def _corner_solid_count(gray_u8: np.ndarray, h: int, w: int) -> int:
    """
    统计「落在四角区域、且高实心率（实心）」的轮廓数量（与 detector LOGO 检测同源）。

    返回计数；>=1 表示存在角落实心品牌标识 → 推荐 ai_logo。
    """
    cv2 = _require_cv2()
    patch = max(20, min(h, w) // 5)
    edges = cv2.Canny(gray_u8, 50, 150)
    ek = np.ones((3, 3), np.uint8)
    edges = cv2.dilate(edges, ek, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 角标需达到「品牌标识」尺度：过小（点阵网格的小点）不算。
    #   地板 = 图面积的 0.1%（400×400≈160px，约 14×14 实块）；测试实心圆(≈3217)达标，
    #   网格小圆点(≈28)被排除，避免与 ai_grid 误混。
    min_area = max(30, int(h * w * 0.001))
    count = 0
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        cx, cy = x + bw / 2.0, y + bh / 2.0
        in_corner = (
            (cx <= patch or cx >= w - patch) and (cy <= patch or cy >= h - patch)
        )
        if not in_corner:
            continue
        # 阶段 59：品牌 LOGO 须达到一定实体尺度（最小边 ≥ 图宽 4%）——
        # 否则角区里的「文字字形」（如底部小字首字母 ~44×18）也会满足面积/实心率
        # 判据而被误判为 LOGO（实测致底部文字水印被推给 ai_logo 预设而漏检）。
        min_side = float(min(bw, bh))
        min_side_floor = config.RECOMMEND_LOGO_MIN_SIDE_FRACTION * min(h, w)
        if min_side < min_side_floor:
            continue
        hull = cv2.convexHull(cnt)
        hull_area = float(cv2.contourArea(hull))
        if hull_area <= 1e-6:
            continue
        solidity = area / hull_area
        if solidity < config.RECOMMEND_LOGO_SOLIDITY:
            continue
        aspect = max(bw, bh) / float(max(1, min(bw, bh)))
        if aspect > config.RECOMMEND_LOGO_MAX_ASPECT:
            continue
        count += 1
    return count


def _corner_watermark_signal(gray_u8: np.ndarray) -> float:
    """
    右下/中右角标水印**模板匹配**信号（阶段 450，替代早期不可靠的边缘密度启发式）。

    背景与选型：豆包类「AI 生成」水印是固定在**右下角（部分在中右）**的平台角标板，
    但它是半透明/低对比（亮板或暗叠加），单纯「边缘密度」既抓不全真值又误命中自然图
    （全量阈值扫描最优仅 6/75 命中、漏 69）。改用**边缘图归一化互相关（NCC）模板匹配**：
    角标字形在全部图上一致，用 9 张真值图 ROI 边缘平均作模板，对亮度极性不敏感
    （同时算 +roi 与 -roi 的 NCC 取大）。全量 88 图标定：NCC>=0.5 时精确率 100%、召回 40%；
    NCC>=0.4 时召回 69%、精确 90%。正式默认 0.5（零自然图误命中、fail-closed）。

    命中即把角标图导向 ai_logo（edge=False），跳过 edge 策略的 OCR 空跑；其余维持原推荐，
    不回归。模板以 base64 PNG 内嵌，推荐器自包含、零外部资产依赖。
    """
    if not config.RECOMMEND_CORNER_WATERMARK_ENABLED:
        return 0.0
    cv2 = _require_cv2()
    tpl = _load_corner_watermark_template()
    if tpl is None:
        return 0.0
    h, w = gray_u8.shape[:2]
    g = gray_u8.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    em = np.sqrt(gx * gx + gy * gy)

    def _roi(kind):
        if kind == "br":
            sub = em[int(h * 0.80):, int(w * 0.74):]
        else:
            sub = em[int(h * 0.45):int(h * 0.72), int(w * 0.78):]
        return cv2.resize(sub, (48, 64))

    def _ncc(a, b):
        a = a - a.mean(); b = b - b.mean()
        den = np.sqrt((a * a).sum()) * np.sqrt((b * b).sum())
        return float((a * b).sum() / den) if den > 1e-6 else 0.0

    best, best_edge = 0.0, 0.0
    for kind in ("br", "mr"):
        r = _roi(kind)
        if r.mean() < config.RECOMMEND_CORNER_WATERMARK_MIN_EDGE:
            continue
        s = max(_ncc(r, tpl), _ncc(-r, tpl))
        if s > best:
            best, best_edge = s, float(r.mean())
    if best >= config.RECOMMEND_CORNER_WATERMARK_NCC_MIN and best_edge >= config.RECOMMEND_CORNER_WATERMARK_MIN_EDGE:
        return 1.0
    return 0.0


_CORNER_WATERMARK_TEMPLATE_B64 = "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"


def _load_corner_watermark_template() -> "Optional[np.ndarray]":
    """解码内嵌角标模板（边缘图，48x64 float32），结果缓存。"""
    import base64
    if _load_corner_watermark_template._cache is not None:
        return _load_corner_watermark_template._cache
    try:
        cv2 = _require_cv2()
        raw = base64.b64decode(_CORNER_WATERMARK_TEMPLATE_B64)
        arr = np.frombuffer(raw, np.uint8)
        png = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if png is None:
            return None
        tpl = cv2.resize(png.astype(np.float32), (48, 64))
        _load_corner_watermark_template._cache = tpl
        return tpl
    except Exception:
        return None
_load_corner_watermark_template._cache = None



def _spectral_regularity(marks: np.ndarray) -> float:
    """
    平铺「规则性」度量（频域）：用 2D FFT 看标记掩码的能量是否集中在少数谐波上。

    返回 0~1：越高表示标记呈「规则周期点阵/平铺」（能量集中在 FFT 的几个峰），
    越低表示标记是随机/不规则笔画（能量弥散）。用于把「填充式平铺」与「满图文字
    笔画」区分开——二者边缘密度都高，但平铺在频域有尖锐晶格峰。

    排除直流分量，专注周期结构本身。
    """
    if marks.sum() < 1:
        return 0.0
    f = np.abs(np.fft.fft2(marks.astype(np.float32)))
    f = np.fft.fftshift(f)
    cy, cx = f.shape[0] // 2, f.shape[1] // 2
    f[cy, cx] = 0.0                       # 屏蔽直流分量
    total = float(f.sum())
    if total < 1e-6:
        return 0.0
    flat = f.ravel()
    k = 24
    top = float(np.sort(flat)[-k:].sum())   # 能量最高的若干谐波峰会聚度
    return top / total


def _text_density(gray_u8: np.ndarray, h: int, w: int) -> Tuple[float, float]:
    """
    笔画（边缘）密度：整体密度 + 底部边缘带密度（与 detector 边缘小字思路同源）。

    返回 (overall, bottom_band)：均为「梯度>阈值像素占比」0~1。
    """
    cv2 = _require_cv2()
    gray = gray_u8.astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    total = max(1, mag.size)
    overall = float(np.count_nonzero(mag > 40)) / total
    band = max(1, int(h * 0.18))
    bottom = mag[h - band:h, :]
    bottom_band = float(np.count_nonzero(bottom > 40)) / max(1, bottom.size)
    return overall, bottom_band


# ==============================================================================
# 主入口
# ==============================================================================
def recommend_preset(
    rgb: np.ndarray,
    sensitivity: Optional[float] = None,
) -> Recommendation:
    """
    分析 RGB 图像特征，推荐一个内置检测预设。

    参数：
        rgb        : (H, W, 3) uint8 数组（BGR/RGB 均可，本函数只看灰度与边缘）。
        sensitivity : **目前不影响结果**（阶段 109 更正文档）。
                      阶段 59 把「标记提取」从分位数口径改成**稳健统计门限**
                      （`median + k·1.4826·MAD` ∧ 绝对下限），此后该形参在
                      `_marks_at` 内**不再被读取**——保留只为兼容三个既有调用点
                      （`ui/tab_auto.py` / `ui/tab_batch.py` / `services/auto_pipeline.py`
                      仍按 UI 滑杆传值）。
                      ⚠ 旧 docstring 写作「仅影响「平铺/半透明标记」提取」，与实现相反
                      （阶段 109 探针：同一张图 sensitivity ∈ {0.0, 0.5, 1.0} 三档
                      `scores` **逐项相同**）。**不要**据旧文案以为它受控于滑杆。

    返回：
        Recommendation(preset, scores, triggered, confidence)。
    """
    if not config.RECOMMEND_ENABLED:
        # 总开关关闭时直接回退默认预设（分数为空）。
        return Recommendation(
            preset=config.RECOMMEND_DEFAULT_PRESET,
            scores={},
            triggered=[],
            confidence=0.0,
        )

    rgb = _ensure_rgb(rgb)
    h, w = rgb.shape[:2]
    gray_u8, residual, score, h, w = _build_residual(rgb)
    sens = config.DETECT_TRANSLUCENT_SENSITIVITY if sensitivity is None else float(sensitivity)
    marks = _marks_at(score, sens, residual)
    coverage = float(np.count_nonzero(marks)) / max(1, marks.size)

    # ---- 1. 全局渐变 ----
    explained, plane_mag, res_std = _gradient_features(gray_u8)
    grad_on = (
        explained > config.RECOMMEND_GRADIENT_MIN_EXPLAINED
        and plane_mag > config.RECOMMEND_GRADIENT_MIN_PLANE_MAG
        and res_std < 32.0
    )
    grad_act = explained if grad_on else 0.0

    # ---- 2. 平铺周期 / 网格 ----
    periodic, period_strength = _periodicity(marks, h, w)
    regularity = _spectral_regularity(marks)          # 频域规则性 0~1
    regular = regularity >= config.RECOMMEND_REGULARITY_MIN
    in_grid_band = (
        config.RECOMMEND_GRID_MIN_COVERAGE
        <= coverage
        <= config.RECOMMEND_GRID_MAX_COVERAGE
    )
    grid_on = periodic and in_grid_band and period_strength >= config.RECOMMEND_GRID_MIN_STRENGTH

    # 平铺（填充式）激活：需「周期 + 规则性 + 覆盖」三者齐备；极淡与否看残差幅度。
    #   regular 是关键——填充式平铺的方块在频域有尖锐晶格峰（regular 高），
    #   而满图随机文字笔画虽边缘密度也高，但频域能量弥散（regular 低），不会误判为平铺。
    tiled_periodic = periodic and regular
    tiled_act = 0.0
    if tiled_periodic and coverage >= config.RECOMMEND_TILED_MIN_COVERAGE:
        tiled_act = max(period_strength, 0.5)
    faint = float(residual[marks > 0].mean()) if coverage > 0 else 0.0
    is_faint = faint > 0 and faint < config.RECOMMEND_FAINT_RESIDUAL

    # ---- 3. 角落实心 LOGO ----
    solid_corner = _corner_solid_count(gray_u8, h, w)
    logo_act = min(1.0, float(solid_corner))  # >=1 个实心角标即记为 1.0

    # ---- 3b. 右下/中右角标水印模板匹配信号（阶段 450）----
    # 用边缘图 NCC 模板匹配豆包平台角标（半透明/低对比，边缘密度启发式不可靠）。
    # 命中时把角标图导向 ai_logo（edge=False），跳过 edge 策略的 OCR 空跑。
    corner_watermark_act = (
        _corner_watermark_signal(gray_u8)
        if config.RECOMMEND_CORNER_WATERMARK_ENABLED else 0.0
    )

    # ---- 4. 文字密度 ----
    overall_d, bottom_d = _text_density(gray_u8, h, w)
    text_act = min(1.0, overall_d / max(1e-6, config.RECOMMEND_TEXT_DENSITY))
    sig_act = min(1.0, bottom_d / max(1e-6, config.RECOMMEND_SIGNATURE_BAND_DENSITY))

    # 通用半透明覆盖激活（兜底展示用）。
    trans_act = min(1.0, coverage / 0.15)

    scores: Dict[str, float] = {
        "gradient": round(grad_act, 3),
        "grid": round(period_strength if grid_on else 0.0, 3),
        "tiled": round(tiled_act, 3),
        "translucent": round(trans_act, 3),
        "solid_logo": round(logo_act, 3),
        "corner_watermark": round(corner_watermark_act, 3),
        "text": round(text_act, 3),
        "signature": round(sig_act, 3),
        "regularity": round(regularity, 3),
    }

    # ---- 优先级决策（最具体 → 最通用；阶段 59 调整；阶段 61 增加组合超集）----
    # 顺序：**多信号组合超集** → 角标Logo → 填充式平铺(faint/普通) → 稀疏网格 → 文字 →
    #       签名 → 全局渐变 → 兜底。
    # 旧顺序把「全局渐变」放最前：任何光滑渐变内容（天空/纯色底）都会直接吞掉所有
    # 局部信号（角标 LOGO 落在渐变底上被误判 ai_gradient → 漏检），故把局部强结构
    # （logo/平铺/网格/文字）提到渐变之前；渐变仅在没有局部信号时兜底命中（纯渐变
    # 叠加水印仍可达，见 self-check `_gradient_img`）。
    # 阶段 61：单信号预设只启用自己那一类策略——图上同时有角标 LOGO 与网格点阵时，
    # ai_logo（不启用网格）会把网格整体漏掉（实测召回 72% vs 全策略并集 97~100%）。
    # 故当**≥2 类信号族**同时触发时，改推「AI 多重水印」超集预设
    # （translucent + corner + edge + grid + logo + 平铺增强），一次覆盖全部触发信号。
    preset = config.RECOMMEND_DEFAULT_PRESET
    triggered: List[str] = []

    families: List[str] = []
    f_logo = logo_act >= 1.0
    f_periodic = bool(grid_on) or bool(
        tiled_periodic and coverage > config.RECOMMEND_GRID_MAX_COVERAGE)
    f_corner_watermark = corner_watermark_act >= 1.0
    # 文字族只在「无周期结构、且非右下角局部化角标」时独立计数：
    #  · 填充式平铺的方块边缘密度同样高（会触发 text_act），若重复计数会把单类平铺
    #    误判成组合水印；
    #  · 右下/中右角标水印（corner_watermark 模板匹配信号）是角标而非满图文字，应走
    #    ai_logo（edge=False）而非 ai_text_heavy，故从文字族剔除，避免 edge+OCR 空跑（阶段 450）。
    f_text = (
        bool(text_act >= 1.0 or sig_act >= 1.0)
        and not f_periodic
        and not f_corner_watermark
    )
    if f_logo:
        families.append("solid_logo")
    if f_periodic:
        families.append("grid" if grid_on else "tiled")
    # 注：corner_watermark 不加入 families —— 它是「单一已知角标类型」，应由 ai_logo 单类
    # 预设覆盖（corner 策略 + search_bottom），而非并入多信号组合触发 ai_multi（edge=True）
    # 造成 OCR 空跑。优先级在下方决策树中显式高于 grid（阶段 450）。
    if f_text:
        families.append("text" if text_act >= 1.0 else "signature")

    if len(families) >= 2:
        # 组合水印：一次用并集预设覆盖（极淡平铺也已含在并集的平铺增强里）。
        preset, triggered = "ai_multi", families
    elif logo_act >= 1.0:
        preset, triggered = "ai_logo", ["solid_logo"]
    elif tiled_periodic and coverage > config.RECOMMEND_GRID_MAX_COVERAGE and is_faint:
        # 填充式平铺 + 极淡 → ai_faint_tiled（更激进的平铺增强）。
        preset, triggered = "ai_faint_tiled", ["tiled"]
    elif tiled_periodic and coverage > config.RECOMMEND_GRID_MAX_COVERAGE:
        # 填充式平铺（非极淡）→ ai_tiled。
        preset, triggered = "ai_tiled", ["tiled"]
    elif f_corner_watermark:
        # 阶段 450：右下/中右角标水印（边缘图 NCC 模板匹配命中）→ 按角标处理，推 ai_logo
        # （edge=False）跳过 edge 策略的 OCR 空跑；水印由 corner 策略（含 search_bottom）
        # 兜底。优先级高于 grid：即便同时误触发周期信号，仍走 ai_logo（角标为真实类型）。
        # ai_logo 已是 quality_baseline.json 已承诺预设，红线不受影响（real_gate 用显式预设）。
        preset, triggered = "ai_logo", ["corner_watermark"]
    elif grid_on:
        # 稀疏点阵（覆盖占比低 + 强周期）→ ai_grid（仅此分支判网格）。
        preset, triggered = "ai_grid", ["grid"]
    elif text_act >= 1.0:
        # 高笔画密度（满图文字/线条）→ ai_text_heavy。
        preset, triggered = "ai_text_heavy", ["text"]
    elif sig_act >= 1.0:
        # 仅底部带高笔画密度 → ai_signature。
        preset, triggered = "ai_signature", ["signature"]
    elif grad_act >= 0.6:
        preset, triggered = "ai_gradient", ["gradient"]
    else:
        # 无强信号：general 综合策略兜底（translucent 覆盖高时记一笔，便于展示）。
        preset = config.RECOMMEND_DEFAULT_PRESET
        triggered = ["translucent"] if trans_act >= 0.3 else []

    confidence = float(max(scores.values())) if scores else 0.0
    return Recommendation(
        preset=preset, scores=scores, triggered=triggered, confidence=confidence
    )


def recommend_preset_for_path(
    image_path: str,
    sensitivity: Optional[float] = None,
) -> Recommendation:
    """从图片路径加载后推荐（供 CLI / 快速验证用）。"""
    from core.image_utils import load_image_with_alpha
    rgb, _ = load_image_with_alpha(image_path)
    return recommend_preset(rgb, sensitivity=sensitivity)


# ==============================================================================
# 模块自检
# ==============================================================================
if __name__ == "__main__":
    cv2 = _require_cv2()
    H = W = 400

    def _plain():
        return np.full((H, W, 3), 240, np.uint8)

    def _gradient_img():
        img = np.zeros((H, W, 3), np.uint8)
        for y in range(H):
            v = int(255 * y / max(1, H - 1))
            img[y, :, :] = v
        return img

    def _grid_img():
        # 稀疏点阵：背景 240，每 32px 一个小圆点（半径 2），覆盖占比低。
        img = np.full((H, W, 3), 240, np.uint8)
        step = 32
        for y in range(0, H, step):
            for x in range(0, W, step):
                cv2.circle(img, (x, y), 2, (180, 180, 180), -1)
        return img

    def _faint_tiled_img():
        # 极淡填充式平铺：背景 245，每 40px 一个 20×20、对比度仅 ~4 的浅灰方块
        # （覆盖占比明显高于稀疏点阵）。
        base = np.full((H, W, 3), 245, np.uint8).astype(np.float32)
        step = 40
        for y in range(0, H, step):
            for x in range(0, W, step):
                cv2.rectangle(base, (x + 10, y + 10), (x + 30, y + 30),
                              (241, 241, 241), -1)
        return np.clip(base, 0, 255).astype(np.uint8)

    def _tiled_img():
        # 普通填充式平铺：对比度 25 的 20×20 方块，覆盖占比高。
        base = np.full((H, W, 3), 245, np.uint8).astype(np.float32)
        step = 40
        for y in range(0, H, step):
            for x in range(0, W, step):
                cv2.rectangle(base, (x + 10, y + 10), (x + 30, y + 30),
                              (220, 220, 220), -1)
        return np.clip(base, 0, 255).astype(np.uint8)

    def _logo_img():
        # 角落实心圆（高实心率）。
        img = _plain()
        cv2.circle(img, (45, 45), 32, (20, 20, 200), -1)
        return img

    def _text_img():
        # 满图随机短笔画（高笔画密度、无规则周期）。
        img = _plain()
        rng = np.random.default_rng(0)
        for _ in range(300):
            x = int(rng.integers(10, W - 10))
            y = int(rng.integers(10, H - 10))
            ang = float(rng.integers(0, 180))
            dx = int(18 * np.cos(np.deg2rad(ang)))
            dy = int(18 * np.sin(np.deg2rad(ang)))
            cv2.line(img, (x, y), (x + dx, y + dy), (60, 60, 60), 2)
        return img

    cases = [
        ("plain", _plain(), "general"),
        ("gradient", _gradient_img(), "ai_gradient"),
        ("grid", _grid_img(), "ai_grid"),
        ("faint_tiled", _faint_tiled_img(), "ai_faint_tiled"),
        ("tiled", _tiled_img(), "ai_tiled"),
        ("logo", _logo_img(), "ai_logo"),
        ("text", _text_img(), "ai_text_heavy"),
    ]
    for name, img, expect in cases:
        rec = recommend_preset(img)
        assert rec.preset == expect, (
            f"[{name}] 期望 {expect}，实际 {rec.preset}；"
            f"scores={rec.scores}"
        )
        print(f"用例通过：{name} → {rec.preset}（{rec}）")

    # 阶段 450：内嵌角标模板必须可解码为 48×64 数组（自包含、零外部资产）。
    _tpl = _load_corner_watermark_template()
    assert _tpl is not None and _tpl.shape == (64, 48), (
        f"角标模板解码异常：{None if _tpl is None else _tpl.shape}")
    print("用例通过：内嵌角标模板解码正常（64×48）。")

    # 阶段 450：corner_watermark 信号命中时决策分支应导向 ai_logo（edge=False）。
    # 直接注入信号命中来隔离验证**决策分支**（合成图无法复刻模板的细粒度边缘结构做 NCC，
    # 早期合成实体会误走 solid_logo 分支；真实图召回/精确由 scripts/verify_corner_reco.py
    # 在 benchmark 88 图上实测：右下角标 ~24/50 → ai_logo、自然图 0 误命中）。
    _orig_sig = _corner_watermark_signal
    try:
        globals()["_corner_watermark_signal"] = lambda _g: 1.0
        rec_cw = recommend_preset(_plain())
        assert rec_cw.preset == "ai_logo" and "corner_watermark" in rec_cw.triggered, (
            f"corner_watermark 命中期望 ai_logo 且触发 corner_watermark，"
            f"实际 {rec_cw.preset}；triggered={rec_cw.triggered}")
        print(f"用例通过：corner_watermark 命中 → ai_logo（触发 {rec_cw.triggered}）")
    finally:
        globals()["_corner_watermark_signal"] = _orig_sig

    # 总开关关闭 → 回退默认。
    _orig = config.RECOMMEND_ENABLED
    config.RECOMMEND_ENABLED = False
    try:
        rec = recommend_preset(_grid_img())
        assert rec.preset == config.RECOMMEND_DEFAULT_PRESET
        print("用例通过：总开关关闭 → 回退默认预设 "
              f"{config.RECOMMEND_DEFAULT_PRESET}。")
    finally:
        config.RECOMMEND_ENABLED = _orig

    print("preset_recommender.py 自检全部通过。")
