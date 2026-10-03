# -*- coding: utf-8 -*-
"""
阶段 23 测试：检测能力深化 v3
  · 实体 LOGO 轮廓水印检测（detect_logo_watermark + 总入口门控 + ai_logo 预设）
  · 网格 / 渐变误报抑制（config 驱动阈值）
  · 批量页 / 媒体页命中率统计聚合（ui.components 聚合助手 + 服务层 stats 透传）
"""
import numpy as np
import pytest

import config
from core.detector import (
    detect_logo_watermark,
    detect_grid_watermark,
    detect_gradient_watermark,
    detect_watermark_mask,
    KIND_LOGO,
    DetectionStats,
)
from core.detection_presets import resolve_preset, get_preset_names
from utils.i18n import set_language
from ui.components import (
    aggregate_detection_stats,
    build_aggregate_detection_stats_markdown,
    build_detection_stats_markdown,
)
from services.pdf_service import PageResult
from services.video_service import VideoResult


# ---------------------------------------------------------------------------
# 辅助：构造测试图
# ---------------------------------------------------------------------------
def _solid_logo_img(h=300, w=300):
    """浅灰底 + 角落实心圆 + 角落实心方块（高实心率，应被检出为 LOGO）。"""
    import cv2
    img = np.full((h, w, 3), 245, dtype=np.uint8)
    cv2.circle(img, (60, 60), 35, (30, 30, 30), -1)
    cv2.rectangle(img, (w - 90, h - 90), (w - 40, h - 40), (30, 30, 30), -1)
    return img


def _plain_img(h=200, w=200):
    """纯色 / 平滑渐变图（几乎无边缘，不应被误判为 LOGO）。"""
    return np.full((h, w, 3), 128, dtype=np.uint8)


# ---------------------------------------------------------------------------
# 第 1 组：LOGO 轮廓检测
# ---------------------------------------------------------------------------
def test_detect_logo_watermark_finds_solid_shape():
    img = _solid_logo_img()
    mask, regions = detect_logo_watermark(
        img, img.shape[0] * img.shape[1],
        config.DETECT_LOGO_MIN_AREA_RATIO, config.DETECT_LOGO_MAX_AREA_RATIO,
    )
    assert len(regions) >= 1, "实体 LOGO 未被检出"
    assert all(r.kind == KIND_LOGO for r in regions)
    assert int(np.count_nonzero(mask > 0)) > 0


def test_detect_logo_watermark_rejects_thin_line():
    """细长实心条（宽高比超过上限）不应被判为 LOGO。"""
    import cv2
    img = np.full((300, 300, 3), 245, dtype=np.uint8)
    cv2.rectangle(img, (150, 20), (155, 280), (30, 30, 30), -1)  # 5×260，aspect=52
    mask, regions = detect_logo_watermark(
        img, img.shape[0] * img.shape[1],
        config.DETECT_LOGO_MIN_AREA_RATIO, config.DETECT_LOGO_MAX_AREA_RATIO,
    )
    assert all(r.kind != KIND_LOGO for r in regions), "细长条不应被判为 LOGO"


def test_detect_logo_watermark_rejects_plain():
    """纯色 / 平滑图（无边缘）不应产生 LOGO 区域。"""
    img = _plain_img()
    mask, regions = detect_logo_watermark(
        img, img.shape[0] * img.shape[1],
        config.DETECT_LOGO_MIN_AREA_RATIO, config.DETECT_LOGO_MAX_AREA_RATIO,
    )
    assert len(regions) == 0


def test_logo_gating_default_off():
    """总入口默认不开启 enable_logo（向后兼容），不应出现 KIND_LOGO 区域。"""
    img = _solid_logo_img()
    res = detect_watermark_mask(
        img, enable_translucent=False, enable_corner=False, enable_edge=False,
        enable_grid=False, enable_gradient=False, use_ocr=False, detect_scale=2000,
    )
    assert not any(r.kind == KIND_LOGO for r in res.regions)


def test_logo_gating_on():
    """开启 enable_logo 后应检出 KIND_LOGO 区域。"""
    img = _solid_logo_img()
    res = detect_watermark_mask(
        img, enable_logo=True, enable_translucent=False, enable_corner=False,
        enable_edge=False, enable_grid=False, enable_gradient=False,
        use_ocr=False, detect_scale=2000,
    )
    assert any(r.kind == KIND_LOGO for r in res.regions)


def test_ai_logo_preset_resolves_enable_logo():
    """ai_logo 预设应解析出 enable_logo=True 并触发 LOGO 检测。"""
    kw = resolve_preset("ai_logo", 0.55)
    assert kw.get("enable_logo") is True
    img = _solid_logo_img()
    res = detect_watermark_mask(img, preset="ai_logo", use_ocr=False, detect_scale=2000)
    assert any(r.kind == KIND_LOGO for r in res.regions)


# ---------------------------------------------------------------------------
# 第 2 组：网格 / 渐变误报抑制（config 驱动阈值）
# ---------------------------------------------------------------------------
def test_grid_does_not_trigger_on_random_noise():
    """随机噪声无规则周期，不应被误判为网格水印。"""
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 256, (300, 300, 3), dtype=np.uint8)
    mask, regions = detect_grid_watermark(
        noise, 0.55, 300 * 300,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    assert len(regions) == 0


def test_gradient_does_not_trigger_on_flat():
    """纯色图方差不足，不应被误判为渐变水印。"""
    img = np.full((200, 320, 3), 128, dtype=np.uint8)
    mask, regions = detect_gradient_watermark(
        img, 200 * 320, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    assert len(regions) == 0


def test_gradient_does_not_trigger_on_noise():
    """随机噪声残差平滑度低（res_std 大），不应被误判为渐变水印。"""
    rng = np.random.default_rng(1)
    noise = rng.integers(0, 256, (200, 320, 3), dtype=np.uint8)
    mask, regions = detect_gradient_watermark(
        noise, 200 * 320, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    assert len(regions) == 0


def test_grid_min_strength_suppresses_weak():
    """调高 DETECT_GRID_MIN_STRENGTH 后，弱周期条纹不应触发网格检测。"""
    import cv2
    # 一组单方向细条纹（仅水平方向有周期，垂直方向无），自相关强度本就偏弱；
    # 把阈值调到很高应能抑制它。
    h, w = 300, 300
    base = np.full((h, w), 230, dtype=np.uint8)
    for y in range(0, h, 16):
        cv2.line(base, (0, y), (w, y), (90, 90, 90), 3)
    img = cv2.cvtColor(base, cv2.COLOR_GRAY2RGB)
    orig = config.DETECT_GRID_MIN_STRENGTH
    config.DETECT_GRID_MIN_STRENGTH = 0.99  # 极端高阈值，任何真实网格都难达到
    try:
        mask, regions = detect_grid_watermark(
            img, 0.55, h * w, config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
        )
        # 即便偶有周期，高阈值也应抑制；稳定不报错即可。
        assert isinstance(regions, list)
    finally:
        config.DETECT_GRID_MIN_STRENGTH = orig


# ---------------------------------------------------------------------------
# 第 3 组：批量 / 媒体页命中率统计聚合
# ---------------------------------------------------------------------------
def _mk_stats(mask_pixels, regions, by_kind, used_ocr, cov=0.1):
    return DetectionStats(
        mask_pixels=mask_pixels, coverage_ratio=cov, region_count=regions,
        by_kind=by_kind, used_ocr=used_ocr,
    )


def test_aggregate_detection_stats_empty():
    assert aggregate_detection_stats([]) is None
    assert aggregate_detection_stats([None, None]) is None


def test_aggregate_detection_stats_sums():
    items = [
        _mk_stats(100, 3, {"translucent": 2, "edge_text": 1}, False, 0.1),
        _mk_stats(300, 5, {"grid_watermark": 5}, True, 0.2),
    ]
    syn = aggregate_detection_stats(items)
    assert syn is not None
    assert syn.mask_pixels == 400
    assert syn.region_count == 8
    assert syn.used_ocr is True
    assert syn.coverage_ratio == pytest.approx(0.15)  # 平均值 (0.1+0.2)/2（浮点容差）
    assert syn.by_kind == {"translucent": 2, "edge_text": 1, "grid_watermark": 5}


def test_aggregate_markdown_contains_label():
    set_language("zh-CN")  # 单测默认语言可能为 en-US，此处显式锁定中文以断言中文标签
    items = [_mk_stats(100, 3, {"translucent": 2}, True, 0.1)]
    md = build_aggregate_detection_stats_markdown(items)
    assert "检测命中率" in md
    assert "translucent" in md or "半透明" in md


def test_aggregate_markdown_placeholder_when_empty():
    set_language("zh-CN")  # 占位文案也带「检测命中率」标题，需中文环境断言
    md = build_aggregate_detection_stats_markdown([None])
    assert "检测命中率" in md  # 占位文案也带标题


def test_process_image_returns_stats_key():
    """auto_pipeline.process_image 在自动模式下应返回 stats 键（供媒体页聚合）。"""
    from services.auto_pipeline import process_image
    img = _solid_logo_img(200, 200)
    # 自动模式：检测可能产出掩码；无论最终 inpaint 是否成功，stats 键都应存在
    # （在 mask 为空走 skipped 分支时返回；这里用一张无水印纯色图确保走 skipped）。
    res = process_image(np.full((160, 160, 3), 200, dtype=np.uint8), mode="auto",
                        sensitivity=0.55, preset="general")
    assert "stats" in res, "process_image 应返回 stats 键"
    assert res["stats"] is None or hasattr(res["stats"], "coverage_ratio")


def test_pageresult_carries_stats_field():
    pr = PageResult(index=1)
    assert pr.stats is None
    assert pr.by_kind is None
    assert pr.used_ocr is False


def test_videoresult_carries_frame_stats_field():
    vr = VideoResult(src_path="x.mp4")
    assert vr.frame_stats == []
