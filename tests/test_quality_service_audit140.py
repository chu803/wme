# -*- coding: utf-8 -*-
"""
阶段 140 守卫测试：`services/quality_service.py` 的降采样尺寸同步 + 通道归一化 + 自检无泄漏。

背景（详见 docs/140-quality_service真值审计-降采样尺寸失同步与通道归一化.md）：
  D1 `_downscale_for_eval` 声明「两图按同一目标边长缩放」但实现是「各按自身边长」，
     宽高比不同或「仅一图超限」时返回尺寸不一致的两图 → 下游非等比回缩 →
     SSIM / changed_ratio 失真（实测 warn/81 vs 正确 good/99）。
  D2 灰度 2D 输入在 `_ssim` 的 `img[:, :, c]` 处直接 IndexError；RGBA 4C 虽不崩但
     第 4 通道混入 SSIM、changed_ratio 恒 ≈1.0。
  D3 自检用 `NamedTemporaryFile(delete=False)` 落盘 4 个 png，崩溃时全程泄漏。

这些用例钉住「已修复」状态：任意一条回退都会在这里变红。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from services.quality_service import (  # noqa: E402
    _as_rgb3,
    _changed_ratio,
    _downscale_for_eval,
    evaluate_quality_arrays,
)


@pytest.fixture
def eval_side(monkeypatch):
    """把降采样上限固定为 1024（与环境无关，遵循经验 122：断言必须显式入参）。"""
    monkeypatch.setattr(config, "QUALITY_MAX_EVAL_SIDE", 1024)
    return 1024


def _mk(h, w, fill=200):
    return np.full((h, w, 3), fill, dtype=np.uint8)


# ==============================================================================
# D1. 降采样尺寸同步（核心）
# ==============================================================================
def test_downscale_keeps_same_size_when_only_result_oversized(eval_side):
    """
    最易触发的场景：原图未超上限、结果图因超分被放大。

    修复前：原图原样返回 800²，结果图缩到 1024² → 尺寸不一致。
    """
    o = _mk(800, 800)
    r = _mk(1600, 1600)
    do, dr = _downscale_for_eval(o, r)
    assert do.shape[:2] == dr.shape[:2], (
        f"降采样后尺寸必须一致，实际 {do.shape[:2]} vs {dr.shape[:2]}"
    )


def test_downscale_keeps_same_size_when_aspect_ratio_differs(eval_side):
    """宽高比不同（原 1600²、结果 2000×2400）：修复前缩成 1024² 与 1024×853。"""
    o = _mk(1600, 1600)
    r = _mk(2000, 2400)
    do, dr = _downscale_for_eval(o, r)
    assert do.shape[:2] == dr.shape[:2], (
        f"宽高比不同时仍须同尺寸，实际 {do.shape[:2]} vs {dr.shape[:2]}"
    )


def test_downscale_keeps_same_size_when_original_larger(eval_side):
    """原图更大、结果图更小，同样不得失同步。"""
    o = _mk(2400, 2000)
    r = _mk(1600, 1600)
    do, dr = _downscale_for_eval(o, r)
    assert do.shape[:2] == dr.shape[:2]


@pytest.mark.parametrize("side", [700, 1024, 1500, 3000])
def test_downscale_never_passes_max_side(eval_side, side):
    """降采样后任一边长都不得超过上限（上限契约本身不得被修复破坏）。"""
    o = _mk(side, side)
    r = _mk(side, side)
    do, dr = _downscale_for_eval(o, r)
    assert max(do.shape[:2]) <= eval_side
    assert max(dr.shape[:2]) <= eval_side


def test_downscale_disabled_returns_originals_untouched(monkeypatch):
    """上限为 0 表示不降采样：修复不得顺手引入 resize（经验 122：显式入参）。"""
    monkeypatch.setattr(config, "QUALITY_MAX_EVAL_SIDE", 0)
    o = _mk(3000, 3000)
    r = _mk(3000, 3000)
    do, dr = _downscale_for_eval(o, r)
    assert do is o and dr is r


def test_oversized_result_classified_consistently(eval_side):
    """
    端到端：结果图被超分放大（比例不变）时，分级应与「同一图」的期望一致。

    修复前该场景走非等比回缩 → warn；修复后 good。
    """
    rng = np.random.default_rng(3)
    o = rng.integers(0, 256, (700, 700, 3), dtype=np.uint8)
    # 结果 = 原图放大（内容完全一致，只是分辨率翻倍）→ 评估应为 good。
    import cv2

    r = cv2.resize(o, (1400, 1400), interpolation=cv2.INTER_CUBIC)
    rep = evaluate_quality_arrays(o, r)
    assert rep.level == "good", (
        f"内容一致、仅分辨率翻倍，应判 good，实际 {rep.level} score={rep.score} "
        f"ssim={rep.ssim} cr={rep.changed_ratio}"
    )


# ==============================================================================
# D2. 通道归一化
# ==============================================================================
def test_as_rgb3_gray_and_rgba():
    """_as_rgb3 的三条分支：2D→3 通道复制、4C→丢 alpha、3C→原样。"""
    g = np.full((8, 8), 77, dtype=np.uint8)
    r3 = _as_rgb3(g)
    assert r3.shape == (8, 8, 3) and np.all(r3 == 77)

    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[:, :, 0] = 10
    rgba[:, :, 3] = 255
    r4 = _as_rgb3(rgba)
    assert r4.shape == (8, 8, 3) and np.all(r4[:, :, 0] == 10)

    bgr = _mk(5, 5, 42)
    assert _as_rgb3(bgr) is bgr


def test_gray_input_does_not_crash():
    """修复前：灰度 2D 在 `img[:, :, c]` 处 IndexError。"""
    g1 = np.full((32, 32), 200, dtype=np.uint8)
    g2 = np.full((32, 32), 120, dtype=np.uint8)
    rep = evaluate_quality_arrays(g1, g2)
    assert rep.level in ("good", "warn", "review")


def test_gray_self_compare_is_zero_changed():
    g = np.full((32, 32), 180, dtype=np.uint8)
    rep = evaluate_quality_arrays(g, g.copy())
    assert rep.changed_ratio == 0.0, f"灰度自比应为 0，实际 {rep.changed_ratio}"
    assert rep.ssim == pytest.approx(1.0, abs=1e-4)


def test_rgba_self_compare_is_zero_changed():
    """修复前：alpha 通道差异被算作「变化」，changed_ratio 恒 ≈1.0。"""
    a = np.zeros((24, 24, 4), dtype=np.uint8)
    a[:, :, :3] = 150
    a[:, :, 3] = 200
    rep = evaluate_quality_arrays(a, a.copy())
    assert rep.changed_ratio == 0.0, f"RGBA 自比应为 0，实际 {rep.changed_ratio}"


def test_changed_ratio_gray_still_documented_gap():
    """
    登记：`_changed_ratio` 本身仍硬依赖 3 通道（`.any(axis=2)`），
    归一化只发生在 `evaluate_quality_arrays` 入口。
    此处钉住「直接调私有函数传灰度会报 AxisError」这一现状，
    以便未来若有人改为「私有函数也自归一化」时会主动变红并复核。
    """
    g = np.full((8, 8), 10, dtype=np.uint8)
    with pytest.raises(Exception):
        _changed_ratio(g, np.full((8, 8), 200, dtype=np.uint8))


# ==============================================================================
# D3. 自检无临时文件泄漏
# ==============================================================================
# ⚠ 阶段 191：本用例给子进程传 `timeout=600`，而 pytest.ini 的全局 `--timeout=300`
#   作用于**测试函数本身** ⇒ 外层比内层小，一旦有负载/插桩就会整场中断。
#   此处显式抬高外层（不变式由 tests/test_timeout_contract.py 机械化守住）。
@pytest.mark.timeout(900)
def test_selfcheck_runs_clean_and_no_leak():
    """子进程跑模块自检：rc=0 且临时目录不留 tmp*.png。"""
    tmp = tempfile.gettempdir()
    before = set(glob.glob(os.path.join(tmp, "tmp*.png")))
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "services", "quality_service.py")],
        capture_output=True, text=True, cwd=_ROOT, timeout=600,
    )
    after = set(glob.glob(os.path.join(tmp, "tmp*.png")))
    leaked = after - before
    for p in leaked:
        try:
            os.remove(p)
        except OSError:
            pass
    assert r.returncode == 0, f"自检非零退出 rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert not leaked, f"自检泄漏临时文件：{sorted(os.path.basename(p) for p in leaked)}"


def test_selfcheck_declares_downscale_and_channel_cases():
    """自检须覆盖本阶段新增的两组断言（用例 4/5），否则修复无自证。"""
    src = open(os.path.join(_ROOT, "services", "quality_service.py"), encoding="utf-8").read()
    assert "用例4/5通过" in src, "自检缺少降采样/通道归一化用例"


def test_selfcheck_has_finally_cleanup():
    """钉住 try/finally 兜底清理（崩溃路径也不得泄漏）。"""
    src = open(os.path.join(_ROOT, "services", "quality_service.py"), encoding="utf-8").read()
    assert "_tmp_paths" in src and "finally:" in src, "自检缺少集中登记 + finally 清理"
