# -*- coding: utf-8 -*-
"""Stage 167 守卫：detect_grid_watermark（网格状平铺水印检测）契约与不变量。

选点背景：审计选点脚本 `--unaudited` 已空（44/44 模块收口），`core/detector` 的
非角标通路（平铺/网格/半透明/边缘文字）历史上已在阶段 52–106 各有专门套件。
本文件不重复「检测质量/精度/召回」类测试，而是锁定**契约与结构不变量**——
这些是自由函数式审计探针量化后钉死的、且既有套件未显式断言的维度：

  1. 输出契约：mask ∈ {0,255}、regions 字段齐全、kind == KIND_GRID；
  2. 干净图 / 纯随机噪声图不得被误判为网格（false-positive 守门，ACF 重写的核心收益）；
  3. 真实网格（细线网格）应检出；
  4. 大图网格：阶段 58 的 min_area 上限夹紧不得把真网格滤空（召回反向失效回归）；
  5. 退化输入（1×1 / 8×8 / 常值图）不得崩溃且返回合法契约；
  6. 证据相交不变式（DETECT_GRID_EVIDENCE_AND=True 时，结果 ⊆ 单图证据门限）。
"""
import numpy as np
import config
import core.detector as det


# --------------------------------------------------------------------------- #
# 合成图构造（均先 2D 再扩成 3 通道，避免广播误差）
# --------------------------------------------------------------------------- #
def _make_line_grid(h, w, period=32, strength=70, alpha=0.6, seed=0):
    """细线网格：period×period tile 四边画暗线，重复平铺 + 平滑基底。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 128.0 + 18.0 * np.sin(xx / 41.0) + 18.0 * np.cos(yy / 37.0)
    base += rng.normal(0, 4, (h, w))
    tile = np.zeros((period, period), np.float32)
    tile[:2, :] = -strength
    tile[-2:, :] = -strength
    tile[:, :2] = -strength
    tile[:, -2:] = -strength
    wm = np.tile(tile, (h // period + 1, w // period + 1))[:h, :w]
    img = np.clip(base + alpha * wm, 0, 255).astype(np.uint8)
    return np.stack([img, img, img], axis=-1)


def _make_clean(h, w, seed=1):
    """干净图：平滑渐变 + 纹理，无周期性水印。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 128.0 + 18.0 * np.sin(xx / 41.0) + 18.0 * np.cos(yy / 37.0)
    base += rng.normal(0, 8, (h, w))
    base = np.clip(base, 0, 255).astype(np.uint8)
    return np.stack([base, base, base], axis=-1)


def _make_noise(h, w, seed=7):
    """纯随机噪声图：无周期性。"""
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3)).astype(np.uint8)


def _run(rgb, sensitivity=0.5):
    h, w = rgb.shape[:2]
    mask, regions = det.detect_grid_watermark(
        rgb, sensitivity, h * w,
        config.DETECT_MIN_AREA_RATIO, config.DETECT_MAX_AREA_RATIO,
    )
    return mask, regions


def _assert_contract(mask, regions, h, w):
    """统一契约断言：mask 尺寸/取值 + region 字段。"""
    assert mask.shape[:2] == (h, w), "mask 尺寸应与输入一致"
    uniq = set(np.unique(mask).tolist())
    assert uniq <= {0, 255}, f"mask 含非 0/255 值：{uniq}"
    for r in regions:
        assert all(hasattr(r, a) for a in ("x", "y", "w", "h", "kind"))
        assert r.kind == det.KIND_GRID, f"region kind 应为 grid，实际 {r.kind}"


# --------------------------------------------------------------------------- #
# 1) 输出契约
# --------------------------------------------------------------------------- #
def test_output_contract_on_detected_grid():
    rgb = _make_line_grid(256, 256)
    mask, regions = _run(rgb)
    _assert_contract(mask, regions, 256, 256)
    assert len(regions) > 0, "网格应被检出"


# --------------------------------------------------------------------------- #
# 2) 干净图不误报
# --------------------------------------------------------------------------- #
def test_clean_image_not_false_positive():
    rgb = _make_clean(256, 256)
    mask, regions = _run(rgb)
    _assert_contract(mask, regions, 256, 256)
    assert len(regions) == 0, "干净图不应被误判为网格"
    assert int(np.count_nonzero(mask > 0)) == 0


# --------------------------------------------------------------------------- #
# 3) 纯随机噪声不误报（ACF 重写消灭的 13~14% 误报回归）
# --------------------------------------------------------------------------- #
def test_random_noise_not_false_positive():
    rgb = _make_noise(256, 256)
    mask, regions = _run(rgb)
    _assert_contract(mask, regions, 256, 256)
    assert len(regions) == 0, "纯噪声图不应被误判为网格"
    assert int(np.count_nonzero(mask > 0)) == 0


# --------------------------------------------------------------------------- #
# 4) 大图网格：min_area 上限夹紧不得把真网格滤空（阶段 58 回归）
# --------------------------------------------------------------------------- #
def test_large_grid_detected_min_area_clamp():
    rgb = _make_line_grid(1024, 1024, period=64, strength=80)
    mask, regions = _run(rgb, sensitivity=0.6)
    _assert_contract(mask, regions, 1024, 1024)
    assert len(regions) > 0, "大图真网格不应被 min_area 上限夹紧滤空"


# --------------------------------------------------------------------------- #
# 5) 退化输入不崩 + 合法契约
# --------------------------------------------------------------------------- #
def test_degenerate_inputs_no_crash():
    for rgb, (h, w) in (
        (_make_line_grid(1, 1, period=1), (1, 1)),
        (_make_line_grid(8, 8, period=3), (8, 8)),
        (np.full((64, 64, 3), 128, np.uint8), (64, 64)),
    ):
        mask, regions = _run(rgb)  # 不应抛异常
        _assert_contract(mask, regions, h, w)


# --------------------------------------------------------------------------- #
# 6) 证据相交不变式：AND 开时结果 ⊆ 单图证据门限（相交不扩张）
# --------------------------------------------------------------------------- #
def test_evidence_and_subset_invariant(monkeypatch):
    rgb = _make_line_grid(256, 256)
    monkeypatch.setattr(config, "DETECT_GRID_EVIDENCE_AND", False)
    m_off, _ = _run(rgb)
    monkeypatch.setattr(config, "DETECT_GRID_EVIDENCE_AND", True)
    m_on, _ = _run(rgb)
    # 必须确有检出，否则子集断言是空真的。
    assert np.any(m_off > 0), "基准（AND 关）应检出网格，否则本断言无意义"
    # AND 开后任何标记像素都应在 AND 关的结果内（相交不得扩张出证据之外）。
    leaked = int(np.count_nonzero(np.bitwise_and(m_on > 0, m_off == 0)))
    assert leaked == 0, f"证据相交后外溢 {leaked} 个像素到单图证据之外"
    # 且覆盖不增。
    assert int(np.count_nonzero(m_on > 0)) <= int(np.count_nonzero(m_off > 0)) + 1
