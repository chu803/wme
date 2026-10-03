# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 20 AI 水印专项检测 —— 单元测试
 --------------------------------------------------------------------------
 覆盖点：
   1. 检测预设体系（core.detection_presets）
      · 内置预设注册 / 查询 / choices / names
      · 预设只覆盖非 None 字段（general 不覆盖、ai_tiled 覆盖 enhance_tiled）
      · 未知预设安全回退
      · 动态注册 register_preset
   2. 平铺半透明增强（core.detector.detect_tiled_translucent）
      · 合成平铺低透明水印图，验证能检出具规模区域
   3. 底部居中搜索区（core.detector._corner_patches）
      · search_bottom=True 额外返回底部中央条带；False 不返回
   4. 预设透传（core.detector.detect_watermark_mask）
      · preset="ai_tiled" 正常返回；未知预设安全回退不报错
      · 不传 preset 时行为与默认一致（向后兼容）
   5. i18n（utils.i18n）：新增检测模式 / 预设键在 zh-CN / en-US 均存在

 设计约束：
   · 纯算法层测试，仅依赖 numpy / opencv / config，不加载 gradio / torch。
   · 平铺检测与角落检测都显式 use_ocr=False，避免沙箱缺 EasyOCR 时误判。
==============================================================================
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

# 路径加固：保证从仓库根运行也能 import。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.detection_presets import (
    DetectionPreset,
    list_presets,
    get_preset,
    get_preset_choices,
    get_preset_names,
    register_preset,
    resolve_preset,
)
from core.detector import (
    detect_tiled_translucent,
    detect_watermark_mask,
    _corner_patches,
)
from utils.i18n import t, set_language


# ==============================================================================
# 第 1 组：检测预设体系（注册 / 查询 / 解析）
# ==============================================================================
def test_builtin_presets_registered():
    names = get_preset_names()
    for expected in ("general", "ai_tiled", "ai_signature", "ai_corner"):
        assert expected in names, f"缺少内置预设：{expected}"


def test_get_preset_returns_object():
    p = get_preset("ai_tiled")
    assert p is not None
    assert p.name == "ai_tiled"
    assert p.display == "AI 平铺水印"


def test_get_preset_missing_returns_none():
    assert get_preset("__none__") is None


def test_get_preset_choices_structure():
    choices = get_preset_choices()
    assert len(choices) >= 4
    for c in choices:
        assert isinstance(c, tuple) and len(c) == 2
        assert isinstance(c[0], str) and isinstance(c[1], str)
    # 包含 (展示名, 值) 形式的 general。
    assert ("通用检测", "general") in choices


def test_general_does_not_override():
    """general 全 None → resolve 仅含 sensitivity（沿用 config 默认）。"""
    g = resolve_preset("general", 0.55)
    assert set(g.keys()) == {"sensitivity"}
    assert g["sensitivity"] == 0.55


def test_ai_tiled_overrides_enhance_tiled():
    """ai_tiled 应开启平铺增强并设置其专属敏感度。"""
    kw = resolve_preset("ai_tiled", 0.55)
    assert kw.get("enhance_tiled") is True
    assert kw.get("sensitivity") == 0.62
    # 明确关闭底部搜索（与署名栏预设区分）。
    assert kw.get("search_bottom") is False


def test_ai_signature_enables_bottom_and_ocr():
    """ai_signature 应开启底部搜索 + OCR，且失败可回退到 enable_translucent。"""
    kw = resolve_preset("ai_signature", 0.5)
    assert kw.get("search_bottom") is True
    assert kw.get("use_ocr") is True
    assert kw.get("enable_translucent") is True


def test_unknown_preset_falls_back():
    """未知预设回退到 base_sensitivity，且不抛异常。"""
    u = resolve_preset("__no_such__", 0.7)
    assert u == {"sensitivity": 0.7}


def test_dynamic_register_preset():
    tmp = DetectionPreset(name="__tmp__", display="临时", description="x",
                          sensitivity=0.3)
    register_preset(tmp)
    assert get_preset("__tmp__") is not None
    assert "__tmp__" in get_preset_names()
    # 清理，避免污染其他用例（register_preset 覆盖式，需删掉）。
    from core.detection_presets import _REGISTRY
    _REGISTRY.pop("__tmp__", None)


# ==============================================================================
# 第 2 组：平铺半透明增强检测
# ==============================================================================
def _make_tiled_image(th=300, tw=300, step=40, alpha=0.25):
    """构造一张带「平铺半透明 logo」的测试图（浅灰底 + 规则平铺小圆点）。"""
    import cv2
    tiled = np.full((th, tw, 3), 240, dtype=np.uint8)
    mark = np.zeros((th, tw), dtype=np.uint8)
    for yy in range(0, th, step):
        for xx in range(0, tw, step):
            cv2.circle(mark, (xx + step // 2, yy + step // 2), 6, 255, -1)
    tiled = (tiled.astype(np.float32) * (1 - alpha)
             + mark[..., None].astype(np.float32) * alpha).astype(np.uint8)
    return tiled


def test_detect_tiled_translucent_finds_regions():
    tiled = _make_tiled_image()
    mask, regions = detect_tiled_translucent(
        tiled, 0.62,
        tiled.shape[0] * tiled.shape[1],
        # min/max area ratio 用宽松默认，避免被面积过滤掉小 tile。
        0.00002, 0.5,
    )
    assert mask.shape[:2] == tiled.shape[:2]
    assert len(regions) > 0, "平铺半透明水印未被检出（增强检测失败）"
    n_pixels = int(np.count_nonzero(mask > 0))
    assert n_pixels > 100, "平铺增强检测掩码像素过少"


def test_tiled_enhancement_outperforms_base_on_faint():
    """
    平铺增强应在「基础半透明检测可能漏掉」的低透明平铺图上仍能检出区域。

    用很低 alpha（0.12）构造极淡平铺图，验证增强版仍能产出区域，
    至少保证「增强版不劣于 0 区域」，并优先验证其能独立检出。
    """
    tiled = _make_tiled_image(alpha=0.12)
    mask, regions = detect_tiled_translucent(
        tiled, 0.62,
        tiled.shape[0] * tiled.shape[1],
        0.00002, 0.5,
    )
    assert len(regions) > 0, "增强版对极淡平铺水印也应能召回"


# ==============================================================================
# 第 3 组：底部居中搜索区
# ==============================================================================
def test_corner_patches_without_bottom():
    h, w = 400, 600
    patches = _corner_patches(h, w, 0.25, search_bottom=False)
    assert len(patches) == 5
    # 没有任何 patch 落在底部中央区域（y 接近 h 且水平居中）。
    bottom_center = (w // 2, h - 1)
    for (x, y, ww, hh) in patches:
        # 底部中央带的特征：y 接近 h-bh 且 x 接近 w/2-bw/2。
        if y >= h - int(min(h, w) * 0.25):
            # 若存在贴底 patch，只能是左下/右下角，不是居中宽块。
            assert x == 0 or x == w - int(min(h, w) * 0.25)


def test_corner_patches_with_bottom_adds_center_band():
    h, w = 400, 600
    patches = _corner_patches(h, w, 0.25, search_bottom=True)
    assert len(patches) == 6, "search_bottom=True 应多出一个底部中央条带"
    # 找出底部中央宽块：宽度明显大于角落方块（bw=side*2.2）、且水平居中。
    side = max(8, int(min(h, w) * 0.25))
    bottom = [p for p in patches if p[2] > side and p[1] >= h - side]
    assert len(bottom) >= 1
    bc = bottom[0]
    # 居中：左边界应接近 w/2 - 宽/2。
    assert abs((bc[0] + bc[2] / 2) - (w / 2)) < side


# ==============================================================================
# 第 4 组：预设透传 detect_watermark_mask
# ==============================================================================
def _make_mixed_image(h=400, w=600):
    """浅灰底 + 半透明水印字 + 角落方块 + 顶部小字。"""
    import cv2
    img = np.full((h, w, 3), 235, dtype=np.uint8)
    cv2.putText(img, "SAMPLE WATERMARK", (60, 210),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (180, 180, 180), 3, cv2.LINE_AA)
    cv2.rectangle(img, (w - 90, h - 90), (w - 30, h - 30), (40, 40, 40), -1)
    cv2.putText(img, "www.example.com", (150, 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 60), 2, cv2.LINE_AA)
    return img


def test_detect_with_ai_tiled_preset_runs():
    tiled = _make_tiled_image()
    res = detect_watermark_mask(tiled, preset="ai_tiled", use_ocr=False,
                                detect_scale=2000)
    assert res.mask.shape[:2] == tiled.shape[:2]
    assert len(res.regions) > 0


def test_detect_unknown_preset_safe_fallback():
    img = _make_mixed_image()
    res = detect_watermark_mask(img, preset="__no_such__", use_ocr=False,
                                detect_scale=2000)
    assert res.mask.shape[:2] == img.shape[:2]


def test_detect_backward_compat_default():
    """不传 preset 时，返回结构正常（向后兼容旧版行为）。"""
    img = _make_mixed_image()
    res = detect_watermark_mask(img, sensitivity=0.55, use_ocr=False,
                                detect_scale=2000)
    assert res.mask.shape[:2] == img.shape[:2]
    assert int(np.count_nonzero(res.mask > 0)) > 0


# ==============================================================================
# 第 5 组：i18n 新增键
# ==============================================================================
def test_i18n_detect_mode_keys():
    set_language("zh-CN")
    assert t("auto.detect_mode_label") == "检测模式"
    assert t("auto.detect_mode_info")
    set_language("en-US")
    assert t("auto.detect_mode_label") == "Detect Mode"
    set_language("zh-CN")


def test_i18n_preset_keys_both_langs():
    for name in ("general", "ai_tiled", "ai_signature", "ai_corner",
                 "ai_text_heavy", "ai_faint_tiled", "ai_grid", "ai_gradient",
                 "ai_logo", "ai_multi"):
        set_language("zh-CN")
        zh_title = t(f"detect.preset.{name}.title")
        zh_desc = t(f"detect.preset.{name}.desc")
        assert zh_title != f"detect.preset.{name}.title" and zh_desc != f"detect.preset.{name}.desc"
        set_language("en-US")
        en_title = t(f"detect.preset.{name}.title")
        assert en_title != f"detect.preset.{name}.title"
        # 中英文标题不应完全相同（确保两语言都翻译了）。
        assert en_title != zh_title
    set_language("zh-CN")


# ==============================================================================
# 第 6 组：阶段 21 新增预设（文字密集 / 极淡平铺）+ 四入口透传
# ==============================================================================
def test_new_presets_registered():
    """阶段 21 + 22 + 23 + 61 新增内置预设，应出现在注册表中且 choices 计数=10。"""
    names = get_preset_names()
    for expected in ("ai_text_heavy", "ai_faint_tiled", "ai_grid", "ai_gradient",
                     "ai_logo", "ai_multi"):
        assert expected in names, f"缺少新增预设：{expected}"
    assert len(get_preset_choices()) == 10


def test_ai_text_heavy_resolve():
    """ai_text_heavy：全策略 + OCR + 底部搜索。"""
    kw = resolve_preset("ai_text_heavy", 0.5)
    assert kw.get("use_ocr") is True
    assert kw.get("search_bottom") is True
    assert kw.get("enable_edge") is True
    assert kw.get("enable_translucent") is True


def test_ai_faint_tiled_resolve():
    """ai_faint_tiled：平铺增强 + 更高敏感度（0.72）。"""
    kw = resolve_preset("ai_faint_tiled", 0.5)
    assert kw.get("enhance_tiled") is True
    assert kw.get("sensitivity") == 0.72


def test_detect_with_new_presets_runs():
    """两个新预设经 detector 总入口应能正常返回有效结果（四入口透传的核心）。"""
    img = _make_mixed_image()
    for preset in ("ai_text_heavy", "ai_faint_tiled"):
        res = detect_watermark_mask(
            img, preset=preset, use_ocr=False, detect_scale=2000
        )
        assert res.mask.shape[:2] == img.shape[:2]
        assert int(np.count_nonzero(res.mask > 0)) > 0


# ==============================================================================
# 第 7 组：阶段 22 新增预设（网格 / 渐变）+ 门控透传
# ==============================================================================
def test_ai_grid_resolve():
    """ai_grid：开启网格检测 + 平铺增强 + 全策略。"""
    kw = resolve_preset("ai_grid", 0.5)
    assert kw.get("enable_grid") is True
    assert kw.get("enhance_tiled") is True
    assert kw.get("enable_translucent") is True
    assert kw.get("enable_corner") is True
    assert kw.get("enable_edge") is True


def test_ai_gradient_resolve():
    """ai_gradient：仅开启全局渐变检测，并隔离其他策略。"""
    kw = resolve_preset("ai_gradient", 0.5)
    assert kw.get("enable_gradient") is True
    assert kw.get("enable_translucent") is False
    assert kw.get("enable_corner") is False
    assert kw.get("enable_edge") is False
    assert kw.get("enhance_tiled") is False


def test_detect_with_ai_grid_preset_finds_grid():
    """ai_grid 预设经总入口应能检出网格水印（门控透传的核心）。"""
    gh, gw, gstep, gsize = 360, 360, 38, 12
    import cv2
    grid = np.full((gh, gw, 3), 235, dtype=np.uint8)
    for gy in range(gstep // 2, gh, gstep):
        for gx in range(gstep // 2, gw, gstep):
            grid[gy - gsize // 2: gy + gsize // 2,
                 gx - gsize // 2: gx + gsize // 2] = 110
    res = detect_watermark_mask(grid, preset="ai_grid", use_ocr=False, detect_scale=2000)
    assert any(r.kind == "grid_watermark" for r in res.regions), "ai_grid 预设未检出网格"


def test_detect_with_ai_gradient_preset_finds_gradient():
    """ai_gradient 预设经总入口应能整图标记渐变水印。"""
    ph, pw = 200, 320
    import cv2
    base_g = np.full((ph, pw), 200, dtype=np.uint8)
    grad = np.tile(np.linspace(0, 90, pw, dtype=np.float32), (ph, 1))
    grad_img = np.clip(base_g.astype(np.float32) + grad, 0, 255).astype(np.uint8)
    grad_img = cv2.cvtColor(grad_img, cv2.COLOR_GRAY2RGB)
    res = detect_watermark_mask(grad_img, preset="ai_gradient", use_ocr=False, detect_scale=2000)
    assert any(r.kind == "gradient_watermark" for r in res.regions), "ai_gradient 预设未检出渐变"
    assert int(np.count_nonzero(res.mask > 0)) == ph * pw, "渐变应整图标记"
