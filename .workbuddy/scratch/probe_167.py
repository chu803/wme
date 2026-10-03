# -*- coding: utf-8 -*-
"""Stage 167 探针：量化 detect_grid_watermark 的契约与边界。

不依赖任何真实检测器；直接构造合成 RGB 数组喂给 detect_grid_watermark，
验证：① 干净图不误报（false-positive 守门）② 退化输入不崩且返回合法契约
③ 证据相交不变式（DETECT_GRID_EVIDENCE_AND 下结果 ⊆ 单图证据）
④ n_tiles<4 守门 ⑤ 输出契约（mask∈{0,255}、regions 字段齐全）。
"""
import sys, os, traceback
sys.path.insert(0, r"D:\work 111\2026-08-03-17-38-51")
import numpy as np
import config

# 确保相关开关处于「默认」状态再测（探针只观察默认行为）。
config.DETECT_GRID_EVIDENCE_AND = True
config.DETECT_SHAPE_AWARE_MASK = True

import core.detector as D


def make_grid(h, w, period=32, alpha=0.6, strength=70, seed=0, line_w=2):
    """合成一张「带周期性细线网格水印」的图：平滑基底 + 重复 tile 的细线网格。

    用真实水印常见的「细线网格」而非平滑正弦，使残差锐利、覆盖率高、ACF 峰强。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 128.0 + 18.0 * np.sin(xx / 41.0) + 18.0 * np.cos(yy / 37.0)
    base += rng.normal(0, 4, (h, w))
    base = np.clip(base, 0, 255).astype(np.float32)
    # 构造 period×period 的 tile：在 tile 上下左右边缘画 line_w 宽的暗线（网格交点）。
    tile = np.zeros((period, period), np.float32)
    tile[:line_w, :] = -strength
    tile[-line_w:, :] = -strength
    tile[:, :line_w] = -strength
    tile[:, -line_w:] = -strength
    wm = np.tile(tile, (h // period + 1, w // period + 1))[:h, :w]
    img = base + alpha * wm
    img = np.clip(img, 0, 255).astype(np.uint8)
    return np.stack([img, img, img], axis=-1)


def make_clean(h, w, seed=1):
    """干净图：平滑渐变 + 纹理，无周期性水印。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = 128.0 + 18.0 * np.sin(xx / 41.0) + 18.0 * np.cos(yy / 37.0)
    base += rng.normal(0, 8, (h, w))
    base = np.clip(base, 0, 255).astype(np.uint8)
    return np.stack([base, base, base], axis=-1)


def run(name, rgb, sensitivity=0.5):
    h, w = rgb.shape[:2]
    area = h * w
    at = D.detect_grid_watermark(
        rgb, sensitivity, area,
        min_area_ratio=getattr(config, "DETECT_GRID_MIN_AREA_RATIO", 0.0008),
        max_area_ratio=getattr(config, "DETECT_GRID_MAX_AREA_RATIO", 0.5),
    )
    mask, regions = at
    # 契约断言
    assert mask.shape[:2] == (h, w), f"{name}: mask 尺寸错 {mask.shape}"
    uniq = set(np.unique(mask).tolist())
    assert uniq <= {0, 255}, f"{name}: mask 含非 0/255 值 {uniq}"
    for r in regions:
        assert hasattr(r, "x") and hasattr(r, "y") and hasattr(r, "w") and hasattr(r, "h")
        assert r.kind == D.KIND_GRID, f"{name}: region kind={r.kind}"
    cov = float(np.count_nonzero(mask > 0)) / float(max(1, mask.size))
    return mask, regions, cov


print("== 探针开始 ==")
print("CONFIG: MIN_COV=%s MAX_COV=%s MIN_ACF=%s TMPL_K=%s EVID_AND=%s SHAPE=%s" % (
    config.DETECT_GRID_MIN_COVERAGE, config.DETECT_GRID_MAX_COVERAGE,
    config.DETECT_GRID_MIN_ACF, config.DETECT_GRID_TMPL_K_MULT,
    config.DETECT_GRID_EVIDENCE_AND, config.DETECT_SHAPE_AWARE_MASK))
results = []

# 1) 干净图：必须不误报（false-positive 守门）
try:
    m, regs, cov = run("clean_256", make_clean(256, 256))
    print(f"[clean_256]        cov={cov:.4f} regions={len(regs)} -> {'PASS(空)' if len(regs)==0 else 'FAIL(误报!)'}")
    results.append(("clean_256_no_fp", len(regs) == 0))
except Exception as e:
    print("[clean_256] CRASH:", traceback.format_exc())
    results.append(("clean_256_no_fp", False))

# 2) 真实网格图：应检测到（正向路径）
try:
    m, regs, cov = run("grid_256_p32", make_grid(256, 256, period=32))
    print(f"[grid_256_p32]     cov={cov:.4f} regions={len(regs)} -> {'PASS(检出)' if len(regs)>0 else 'WARN(未检出)'}")
    results.append(("grid_256_detect", len(regs) > 0))
except Exception as e:
    print("[grid_256_p32] CRASH:", traceback.format_exc())
    results.append(("grid_256_detect", False))

# 3) 退化：1x1 图
try:
    m, regs, cov = run("tiny_1", make_grid(1, 1, period=1))
    print(f"[tiny_1]           cov={cov:.4f} regions={len(regs)} -> {'PASS(不崩)' if True else 'x'}")
    results.append(("tiny_1_safe", True))
except Exception as e:
    print("[tiny_1] CRASH:", repr(e))
    results.append(("tiny_1_safe", False))

# 4) 退化：8x8（< exclude 周期）
try:
    m, regs, cov = run("tiny_8", make_grid(8, 8, period=3))
    print(f"[tiny_8]           cov={cov:.4f} regions={len(regs)} -> PASS(不崩, 无周期)")
    results.append(("tiny_8_safe", True))
except Exception as e:
    print("[tiny_8] CRASH:", repr(e))
    results.append(("tiny_8_safe", False))

# 5) 常值图（残差为 0）
try:
    m, regs, cov = run("const_64", np.full((64, 64, 3), 128, np.uint8))
    print(f"[const_64]         cov={cov:.4f} regions={len(regs)} -> {'PASS(空)' if len(regs)==0 else 'FAIL'}")
    results.append(("const_64_empty", len(regs) == 0))
except Exception as e:
    print("[const_64] CRASH:", repr(e))
    results.append(("const_64_empty", False))

# 6) 证据相交不变式：DETECT_GRID_EVIDENCE_AND=True 时，结果掩码 ⊆ 单图证据掩码
try:
    rgb = make_grid(256, 256, period=32)
    h, w = rgb.shape[:2]
    area = h * w
    # 复算单图证据（与函数内部同源）：取函数内的 marks 难，改测「最终 mask 支撑 ⊆ evidence」
    # evidence 在函数内定义为 marks（开运算后、相交前）。我们用一个近似：直接对比
    # 开运算后的证据门限掩码——这里改为直接验证「相交后 mask 的支撑不会超过 tile_out 自身」。
    # 更严格：重跑两次（AND 开/关），AND 开时 cov 应 <= AND 关时 cov。
    save = config.DETECT_GRID_EVIDENCE_AND
    config.DETECT_GRID_EVIDENCE_AND = False
    m_off, _, _ = run("grid_and_off", rgb)
    config.DETECT_GRID_EVIDENCE_AND = True
    m_on, _, _ = run("grid_and_on", rgb)
    cov_on = float(np.count_nonzero(m_on > 0)) / max(1, m_on.size)
    cov_off = float(np.count_nonzero(m_off > 0)) / max(1, m_off.size)
    ok = (cov_on <= cov_off + 1e-9) and (np.count_nonzero(np.bitwise_and(m_on > 0, m_off == 0)) == 0)
    print(f"[evidence_and]      cov_on={cov_on:.4f} cov_off={cov_off:.4f} -> {'PASS(结果⊆证据)' if ok else 'FAIL(结果溢出证据)'}")
    results.append(("evidence_and_subset", ok))
except Exception as e:
    print("[evidence_and] CRASH:", repr(e))
    results.append(("evidence_and_subset", False))

# 7) n_tiles<4 守门：极小周期但图太小 → 不应崩溃且返回空或合法
try:
    rgb = make_grid(40, 40, period=20)  # 仅 2x2 tiles
    m, regs, cov = run("small_tiles", rgb)
    print(f"[small_tiles]      cov={cov:.4f} regions={len(regs)} -> PASS(不崩)")
    results.append(("small_tiles_safe", True))
except Exception as e:
    print("[small_tiles] CRASH:", repr(e))
    results.append(("small_tiles_safe", False))

# 8) 随机噪声图：不应被误判为网格（ACF 重写要消灭的 13~14% 误报回归）
try:
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    m, regs, cov = run("noise_256", noise)
    print(f"[noise_256]        cov={cov:.4f} regions={len(regs)} -> {'PASS(不误报)' if len(regs)==0 else 'FAIL(误报!)'}")
    results.append(("noise_no_fp", len(regs) == 0))
except Exception as e:
    print("[noise_256] CRASH:", repr(e))
    results.append(("noise_no_fp", False))

# 9) 单轴周期（仅横线网格）：应检测到（is_grid 允许单方向 period）
try:
    h, w = 256, 256
    yy, xx = np.mgrid[0:h, 0:w]
    base = 128.0 + 18.0 * np.sin(xx / 41.0)  # 2D
    rng = np.random.default_rng(3)
    base += rng.normal(0, 4, (h, w))
    # 仅横向线（period 沿 y）：在每隔 32 行画一条横暗线
    wm = np.zeros((h, w), np.float32)
    for y0 in range(0, h, 32):
        wm[y0:y0 + 2, :] = -70
    img = np.clip(base + 0.6 * wm, 0, 255).astype(np.uint8)
    rgb = np.stack([img, img, img], -1)
    m, regs, cov = run("hline_256", rgb)
    print(f"[hline_256]        cov={cov:.4f} regions={len(regs)} -> {'PASS(单轴检出)' if len(regs)>0 else 'WARN(未检出)'}")
    results.append(("hline_detect", len(regs) > 0))
except Exception as e:
    print("[hline_256] CRASH:", repr(e))
    results.append(("hline_detect", False))

# 10) 大图网格：验证 min_area 上限夹紧不会把真网格滤空（阶段 58 修复的反向失效）
try:
    rgb = make_grid(1024, 1024, period=64, strength=80)
    m, regs, cov = run("grid_1024", rgb, sensitivity=0.6)
    print(f"[grid_1024]        cov={cov:.4f} regions={len(regs)} -> {'PASS(大图检出)' if len(regs)>0 else 'FAIL(被滤空!)'}")
    results.append(("grid_1024_detect", len(regs) > 0))
except Exception as e:
    print("[grid_1024] CRASH:", repr(e))
    results.append(("grid_1024_detect", False))


print("\n== 汇总 ==")
for k, v in results:
    print(f"  {k:22} {'OK' if v else 'XX'}")
print("ALL_PASS" if all(v for _, v in results) else "HAS_FAIL")
