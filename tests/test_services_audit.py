# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : tests/test_services_audit.py
 文件说明 : 阶段 87 · services 层真值审计守卫。

            覆盖两类此前**从未被测试触及**的模块（阶段 87 探查结论：
            queue_worker 429 行仅被引用 2 次、quality_service 277 行仅 1 次）：

            A. quality_service
               · P0  SSIM 值域契约：SSIM 理论值域 [-1,1]，
                     修复前因 uint8 乘法溢出实测跑到 0.21~3.92。
               · level / detail 一致性：改写 level 时必须同步 detail。
               · 抗脆化：降采样**不得**改变分级结论（阶段 87 已验证该声明成立，
                 此测试用于防止后人把「正确的东西」当成缺陷改掉）。

            B. queue_worker
               · 取消契约：run() 声明「逐个 yield 完成（或状态变更）的 Task」，
                 被取消的任务必须被 yield，否则上层收不到终态。
               · TaskCancelledError 必须按取消处理，不得被当成故障重试。

            每条守卫都配**注入式反例**（经验 11/12）：
            没有反例的守卫可能条件写反而永远绿。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import numpy as np
import pytest
import cv2  # type: ignore

import config
from services.quality_service import (
    _gaussian_window,
    _ssim_channel,
    detail_text,
    evaluate_quality_arrays,
)
from services.queue_worker import BatchQueue, TaskStatus
from utils.exceptions import TaskCancelledError


# ==============================================================================
# 辅助：复现阶段 87 修复「前」的实现，用于注入式反例
# ==============================================================================
def _old_ssim_channel_uint8(x: np.ndarray, y: np.ndarray, window: np.ndarray) -> float:
    """
    阶段 87 修复前的 `_ssim_channel`（**故意**保留的错误实现）。

    唯一区别：没有把输入规整为 float64，于是 `x * x` / `x * y` 在 uint8 上
    按 256 取模溢出（200*200 = 64 而非 40000），sigma 全错。
    保留它只为证明「值域断言抓得住这个缺陷」——若本函数与修复后实现给出
    相同结果，说明值域断言已经失效。
    """
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu_x = cv2.filter2D(x, -1, window)
    mu_y = cv2.filter2D(y, -1, window)
    mu_x2 = mu_x * mu_x
    mu_y2 = mu_y * mu_y
    mu_xy = mu_x * mu_y
    sigma_x2 = cv2.filter2D(x * x, -1, window) - mu_x2      # uint8 溢出点
    sigma_y2 = cv2.filter2D(y * y, -1, window) - mu_y2      # uint8 溢出点
    sigma_xy = cv2.filter2D(x * y, -1, window) - mu_xy      # uint8 溢出点
    num = (2 * mu_xy + c1) * (2 * sigma_xy + c2)
    den = (mu_x2 + mu_y2 + c1) * (sigma_x2 + sigma_y2 + c2)
    den = np.where(den == 0, 1e-8, den)
    return float(np.mean(num / den))


def _const(v: int, side: int = 64) -> np.ndarray:
    return np.full((side, side), v, dtype=np.uint8)


# ==============================================================================
# A1. SSIM 值域契约（P0）
# ==============================================================================
@pytest.mark.parametrize("v1,v2", [(150, 120), (200, 170), (100, 60), (240, 210)])
def test_ssim_within_theoretical_range(v1, v2):
    """SSIM 理论值域 [-1, 1]；修复前实测可达 3.92。"""
    w = _gaussian_window()
    s = _ssim_channel(_const(v1), _const(v2), w)
    assert -1.0 <= s <= 1.0, f"SSIM={s} 超出理论值域（uint8 溢出会表现为 >1）"
    assert s == pytest.approx(s, abs=1e-9)


@pytest.mark.parametrize("v", [5, 50, 128, 200, 250])
def test_self_compare_ssim_is_exactly_one(v):
    """同一张图自比，SSIM 必须恒为 1.0（含高亮度——正是溢出最严重处）。"""
    w = _gaussian_window()
    img = _const(v)
    assert _ssim_channel(img, img, w) == pytest.approx(1.0, abs=1e-6)


def test_ssim_accepts_uint8_and_float_alike():
    """入口已统一规整 dtype：uint8 与 float64 传入应给出**相同**结果。"""
    w = _gaussian_window()
    a8, b8 = _const(150), _const(120)
    s_u8 = _ssim_channel(a8, b8, w)
    s_f64 = _ssim_channel(a8.astype(np.float64), b8.astype(np.float64), w)
    assert s_u8 == pytest.approx(s_f64, abs=1e-9)


def test_old_uint8_implementation_breaks_the_range():
    """
    注入式反例（关键）：修复前的实现**确实**会产出越界值。

    若此断言开始失败（旧实现也合法），说明前面的值域断言已退化为
    「永远通过」的空守卫，必须重新设计。
    """
    w = _gaussian_window()
    a8, b8 = _const(150), _const(120)
    bad = _old_ssim_channel_uint8(a8, b8, w)
    good = _ssim_channel(a8, b8, w)
    assert not (-1.0 <= bad <= 1.0), (
        f"旧实现本应越界（实测修复前为 3.12），实际 {bad}；"
        f"若它已合法，本文件的 SSIM 值域断言就失去了意义"
    )
    assert -1.0 <= good <= 1.0
    assert bad != pytest.approx(good, abs=1e-3), "修复后实现不应与旧实现一致"


def test_ssim_is_content_dependent_not_random():
    """修复后 SSIM 应随差异单调变差（防止「只要是合法值就行」的假绿）。"""
    w = _gaussian_window()
    base = _const(128)
    scores = [_ssim_channel(base, _const(128 - d), w) for d in (0, 30, 60, 100)]
    assert scores[0] == pytest.approx(1.0, abs=1e-6)
    assert scores == sorted(scores, reverse=True), f"差异越大 SSIM 应越低：{scores}"


# ==============================================================================
# A2. level / detail 一致性
# ==============================================================================
def test_review_level_has_consistent_detail(monkeypatch):
    """
    强制触发「score 跌破复核线 → level 改写为 review」。

    修复前：level=review 但 detail 仍为 warn 措辞「可能修复过头」——
    用户按 review 去查「水印没修掉」，看到的却是相反的「修过头」。
    阶段 337 之后诊断不再是句子而是键 ⇒ 同一条语义改为断言
    「键被换成复核键 + 原诊断键被保住 + 渲染出的中文句子两者都在」。
    """
    monkeypatch.setattr(config, "QUALITY_SCORE_REVIEW", 100.0)  # 任何分都会跌破
    rng = np.random.default_rng(0)
    o = np.full((64, 64, 3), 128, dtype=np.uint8)
    junk = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    rep = evaluate_quality_arrays(o, junk)

    assert rep.level == "review", f"期望 review，实际 {rep.level}（score={rep.score}）"
    assert rep.detail_key == "quality.detail_review_low_score", rep.detail_key
    assert rep.detail_base_key, f"复核改写时原诊断键必须保住：{rep.detail_base_key!r}"
    text = detail_text(rep)
    assert "复核" in text, f"渲染出的句子未同步复核措辞：{text!r}"


def test_good_level_detail_untouched():
    """回归护栏：正常 good 场景不应被误加「复核」字样（337 后按渲染文本断言）。"""
    o = np.full((64, 64, 3), 128, dtype=np.uint8)
    f = o.copy()
    f[10:30, 10:30] = 200            # 一小块被修掉的水印
    rep = evaluate_quality_arrays(o, f)
    assert rep.level == "good", f"期望 good，实际 {rep.level} cr={rep.changed_ratio}"
    assert rep.detail_base_key == "", f"good 场景不该带复核底稿：{rep.detail_base_key!r}"
    assert "复核" not in detail_text(rep)


# ==============================================================================
# A3. 抗脆化：降采样不得改变分级结论
# ==============================================================================
def test_downscale_preserves_level_and_changed_ratio(monkeypatch):
    """
    阶段 87 探查结论：`_downscale_for_eval` 的「分级结论基本不变」**声明成立**
    （2048² 降采样到 1024²，changed_ratio 差异 <2%，level 不变）。

    这条测试的作用是钉住「已验证为非缺陷」的结论——防止后来者把它当成
    缺陷去「修复」，反而引入回归。
    """
    side, mark = 512, 60
    rng = np.random.default_rng(7)
    base = np.clip(
        np.linspace(90, 210, side, dtype=np.float32)[None, :, None]
        + np.zeros((side, 1, 3), np.float32)
        + rng.normal(0, 3, (side, side, 3)).astype(np.float32),
        0, 255,
    ).astype(np.uint8)
    o = base.copy()
    cv2.rectangle(o, (30, 30), (30 + mark, 30 + mark), (250, 250, 250), -1)
    f = base.copy()                       # 修好了

    monkeypatch.setattr(config, "QUALITY_MAX_EVAL_SIDE", 0)
    full = evaluate_quality_arrays(o, f)
    monkeypatch.setattr(config, "QUALITY_MAX_EVAL_SIDE", 256)
    dsc = evaluate_quality_arrays(o, f)

    assert full.level == dsc.level, (
        f"降采样改变了分级结论：{full.level} -> {dsc.level}"
    )
    assert abs(full.changed_ratio - dsc.changed_ratio) < 0.01, (
        f"降采样使 changed_ratio 漂移过大：{full.changed_ratio} -> {dsc.changed_ratio}"
    )


# ==============================================================================
# B1. queue_worker 取消契约
# ==============================================================================
def test_cancelled_tasks_are_yielded():
    """
    执行前取消：run() 契约要求「逐个 yield 完成（或状态变更）的 Task」。

    修复前：5 个任务全部置为 CANCELLED，但 yield 出来 **0 个**
    → 上层 UI 点了取消却收不到任何反馈。
    """
    q = BatchQueue(max_concurrency=1, max_size=50, retry=0, timeout=30)
    for i in range(5):
        q.submit(f"i{i}.png", f"/fake/{i}.png")
    q.cancel()

    out = list(q.run(lambda t, cb: {"status": "ok"}))
    assert len(out) == 5, f"应 yield 5 个，实际 {len(out)}"
    assert all(t.status == TaskStatus.CANCELLED for t in out)


def test_mid_run_cancel_yields_every_task():
    """执行途中取消：已启动与未启动的任务都必须被 yield。"""
    q = BatchQueue(max_concurrency=1, max_size=50, retry=0, timeout=30)
    for i in range(6):
        q.submit(f"m_{i}.png", f"/fake/{i}.png")

    def proc(t, cb):
        if t.name == "m_0.png":
            q.cancel()
        return {"status": "ok"}

    out = list(q.run(proc))
    assert len(out) == 6, f"应 yield 6 个，实际 {len(out)}"
    assert sum(1 for t in out if t.status == TaskStatus.CANCELLED) >= 1


def test_on_update_receives_cancelled_tasks():
    """on_update 回调同样要收到被取消的任务（UI 靠它刷新）。"""
    q = BatchQueue(max_concurrency=1, max_size=20, retry=0, timeout=30)
    for i in range(3):
        q.submit(f"u{i}.png", f"/fake/{i}.png")
    q.cancel()
    seen = []
    list(q.run(lambda t, cb: {"status": "ok"}, on_update=seen.append))
    assert len(seen) == 3, f"on_update 应被调用 3 次，实际 {len(seen)}"


# ==============================================================================
# B2. TaskCancelledError 不得被当作故障重试
# ==============================================================================
def test_task_cancelled_error_is_not_retried():
    """
    processor 抛 TaskCancelledError（业务语义：用户主动取消）时：
      · 状态应为 CANCELLED，不是 FAILED
      · **不得重试**（修复前重试 retry+1 次，processor 若有副作用会被重复执行）
      · error 不应残留「这是您主动取消的操作，无需处理」这类自相矛盾文案
    """
    q = BatchQueue(max_concurrency=1, max_size=10, retry=2, timeout=30)
    q.submit("c.png", "/fake/c.png")
    calls = []

    def proc(t, cb):
        calls.append(1)
        raise TaskCancelledError("用户主动取消")

    out = list(q.run(proc))
    assert out, "应至少 yield 一个任务"
    t = out[0]
    assert t.status == TaskStatus.CANCELLED, f"期望 cancelled，实际 {t.status.value}"
    assert len(calls) == 1, f"用户取消不应重试，processor 却被调用 {len(calls)} 次"
    assert t.retries == 0
    assert t.error is None


def test_generic_error_is_still_retried():
    """回归护栏：普通故障**仍应**重试（不要把取消修复扩大化）。"""
    q = BatchQueue(max_concurrency=1, max_size=10, retry=1, timeout=30)
    q.submit("e.png", "/fake/e.png")
    calls = []

    def proc(t, cb):
        calls.append(1)
        raise RuntimeError("偶发故障")

    out = list(q.run(proc))
    assert out[0].status == TaskStatus.FAILED
    assert len(calls) == 2, f"retry=1 应共尝试 2 次，实际 {len(calls)}"
    assert out[0].retries == 1


def test_internal_cancel_signal_still_works():
    """回归护栏：内部的 on_progress 检查点取消机制未被破坏。"""
    q = BatchQueue(max_concurrency=1, max_size=10, retry=0, timeout=30)
    q.submit("p.png", "/fake/p.png")

    def proc(t, cb):
        cb(0.1, "开始")
        q.cancel()                 # 取消后，下一个检查点必须抛出
        cb(0.5, "中途")
        return {"status": "ok"}

    out = list(q.run(proc))
    assert out[0].status == TaskStatus.CANCELLED
