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

    背景与选型：豆包类「AI 生成」水印固定在**右下角（部分在中右）**的平台角标板，但它是
    半透明/低对比（亮板或暗叠加），单纯「边缘密度」既抓不全真值又误命中自然图（全量阈值
    扫描最优仅 6/75 命中、漏 69）。改用**边缘图归一化互相关（NCC）模板匹配**：角标字形
    一致但对亮度极性敏感，故用 **9 张真值图右下 ROI 边缘图构成模板库**（覆盖亮板/暗叠加
    等多种外观），对每个 ROI 取库内最大 NCC（+roi 与 -roi 双极性）。

    全量 88 图标定（含**留出集**：模板源不计入召回，防自匹配膨胀）：
      · 模板库 NCC>=0.30 → 精确率 100%（自然图 0 误命中）、全 GT 召回 52%（39/75）、
        留出集召回 54%（31/57）——同等零误命中下严格优于单平均模板（avg@0.30 仅 45%、
        avg@0.40 仅 48%），且救回已知漏检 c15（密集小字母低对比）。
      · NCC>=0.35 → 召回 48%、FP=0（更保守、余量更大）。
    正式默认 0.30（fail-closed：偶发误命中仅让自然图走 ai_logo，代价有界；需更稳可设 0.35）。

    命中即把角标图导向 ai_logo（edge=False），跳过 edge 策略的 OCR 空跑；其余维持原推荐，
    不回归。模板库以 base64 PNG 内嵌，推荐器自包含、零外部资产依赖。
    """
    if not config.RECOMMEND_CORNER_WATERMARK_ENABLED:
        return 0.0
    cv2 = _require_cv2()
    bank = _load_corner_watermark_templates()
    if not bank:
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
        for tpl in bank:
            s = max(_ncc(r, tpl), _ncc(-r, tpl))
            if s > best:
                best, best_edge = s, float(r.mean())
    if best >= config.RECOMMEND_CORNER_WATERMARK_NCC_MIN and best_edge >= config.RECOMMEND_CORNER_WATERMARK_MIN_EDGE:
        return 1.0
    return 0.0

_CORNER_WATERMARK_BANK_B64 = "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"


def _load_corner_watermark_templates() -> "List[Optional[np.ndarray]]":
    """解码内嵌角标模板库（9 张 64x48 边缘图，纵向堆叠于一张 PNG），结果缓存。"""
    import base64
    if _load_corner_watermark_templates._cache is not None:
        return _load_corner_watermark_templates._cache
    try:
        cv2 = _require_cv2()
        raw = base64.b64decode(_CORNER_WATERMARK_BANK_B64)
        arr = np.frombuffer(raw, np.uint8)
        png = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if png is None:
            return []
        full = png.astype(np.float32)
        th = full.shape[0] // 9
        bank = [full[i * th:(i + 1) * th, :] for i in range(9)]
        _load_corner_watermark_templates._cache = bank
        return bank
    except Exception:
        return []
_load_corner_watermark_templates._cache = None



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

    # 阶段 450：内嵌角标模板库必须可解码为 9 张 64×48 数组（自包含、零外部资产）。
    _bank = _load_corner_watermark_templates()
    assert len(_bank) == 9 and all(t.shape == (64, 48) for t in _bank), (
        f"角标模板库解码异常：{len(_bank)} 张")
    print(f"用例通过：内嵌角标模板库解码正常（{len(_bank)}×64×48）。")

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
