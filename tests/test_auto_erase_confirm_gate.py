#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 233 · 「全自动擦除确认门」的守卫。

背景（阶段 232 审计→233 实施）
------------------------------
检测器是**召回优先**设计（红线 precision ≥ 0.050 ⇒ 有意容忍 ≥95% 误检）。
在**文字密集的插画/封面**上会大面积误检（实测某用户封面 21 区域中 15 个
`translucent` **全部是误检**，压在标题笔画/标签/衣物上）。
三条「改进检测器」的路径先后被数据否决：
  ① 像素判据不可分（残差 p90 / lap_mean 两组大幅重叠）；
  ② 「区域数上限」不是信号（基准图中位 26 / p90 875 / max 1568，问题图仅 21）；
  ③ 收紧检测器会撞 recall 红线。
⇒ 定性为**产品预期不符**，修法落在**产品层**：让「系统替用户选预设 + 直接擦除」
这条**静默**路径不再静默——默认拒绝并抛 `AutoEraseNotConfirmed`。

本文件守什么（每条都对应一个**具体的失效形态**）
------------------------------------------------
① 默认组合必须拒绝（否则功能等于没加）；
② 三条出路各自都能放行（否则门变成「一律拒绝」= 功能损坏）；
③ 配置开关能恢复旧行为（可逆杠杆）；
④ **`batch_remove` 必须转发标志**——不转发时异常会被单张容错吞掉，
   表现为「**整批静默全失败**」，正是本门要消灭的静默形态；
⑤ **REST 白名单必须包含该参数**——否则 REST 客户端**永远无法**表态，
   等于把功能从 REST 上摘掉（而 REST 恰好是最需要它的入口）；
⑥ REST 必须把该异常映射为**可程序化区分**的状态（不能混进通用 failed）；
⑦ 异常消息必须**给出可执行的出路**（否则用户只知失败、不知怎么办）；
⑧ **源码级**：api 模块 docstring 的示例不得停留在旧签名（文档撒谎 = 二次伤害）。
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import api  # noqa: E402
import config  # noqa: E402


# ==============================================================================
# ① 默认必须拒绝
# ==============================================================================
def test_default_combo_is_rejected():
    """`preset=None` + `auto_preset=True` + 未显式允许 ⇒ 必须拒绝。"""
    with pytest.raises(api.AutoEraseNotConfirmed):
        api._guard_auto_erase(entry="t", preset=None, auto_preset=True,
                              allow_auto_erase=False)


def test_rejection_is_an_exception_not_a_silent_return():
    """必须是**异常**——返回值/状态位都可以被旧调用方静默忽略。"""
    assert issubclass(api.AutoEraseNotConfirmed, Exception)
    # 且必须是 RuntimeError 家族（调用方能一眼看出是「运行期策略拒绝」）
    assert issubclass(api.AutoEraseNotConfirmed, RuntimeError)


# ==============================================================================
# ② 三条出路各自放行
# ==============================================================================
def test_explicit_preset_passes():
    api._guard_auto_erase(entry="t", preset="ai_logo", auto_preset=True,
                          allow_auto_erase=False)


def test_explicit_allow_passes():
    api._guard_auto_erase(entry="t", preset=None, auto_preset=True,
                          allow_auto_erase=True)


def test_auto_preset_off_passes():
    """未启用 auto_preset ⇒ 系统不替调用方选预设 ⇒ 不构成「静默」。"""
    api._guard_auto_erase(entry="t", preset=None, auto_preset=False,
                          allow_auto_erase=False)


# ==============================================================================
# ③ 配置开关可逆
# ==============================================================================
def test_config_lever_restores_old_behavior(monkeypatch):
    monkeypatch.setattr(config, "API_REQUIRE_AUTO_ERASE_CONFIRM", False)
    api._guard_auto_erase(entry="t", preset=None, auto_preset=True,
                          allow_auto_erase=False)   # 不应抛


def test_config_default_is_confirm_required():
    """默认必须是「要求确认」——否则本阶段改动形同未生效。"""
    assert config.API_REQUIRE_AUTO_ERASE_CONFIRM is True


def test_config_lever_is_env_overridable():
    """项目铁律：可逆杠杆走 env，不改代码。"""
    with open(os.path.join(_ROOT, "config.py"), encoding="utf-8") as f:
        src = f.read()
    line = next((ln for ln in src.splitlines()
                 if ln.strip().startswith("API_REQUIRE_AUTO_ERASE_CONFIRM")), "")
    assert "_env_bool" in line, f"必须用 _env_bool 支持环境变量覆盖，实际：{line}"


# ==============================================================================
# ④ batch_remove 必须转发（否则「整批静默全失败」）
# ==============================================================================
def test_batch_remove_forwards_allow_auto_erase():
    """**反例守卫**：`batch_remove` 必须把标志转发给 `remove_watermark`。

    不转发时：每张都抛 `AutoEraseNotConfirmed`，而 `batch_remove` 的单张容错
    会把它变成 `status="failed"` ⇒ **整批静默全失败**，用户只看到「全失败」
    却不知道是策略拒绝。这条正是本门要消灭的静默形态。
    """
    sig = inspect.signature(api.batch_remove)
    assert "allow_auto_erase" in sig.parameters, "batch_remove 缺 allow_auto_erase 参数"
    src = inspect.getsource(api.batch_remove)
    assert "allow_auto_erase=allow_auto_erase" in src, (
        "batch_remove 声明了参数却**没有转发**给 remove_watermark ⇒ 会静默全失败")


def test_batch_remove_reports_rejection_as_failed_not_silent():
    """转发后，未允许时整批应如实失败（而非误报成功）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "not_an_image.png")
        with open(src, "wb") as f:
            f.write(b"not an image")
        res = api.batch_remove([src], os.path.join(td, "out"),
                               engine="opencv", auto_preset=True)
    assert len(res) == 1
    assert res[0].status == "failed", "策略拒绝应如实报 failed"
    # 失败原因须能指向确认门（而不是含混的「无法解码」）
    assert "AutoErase" in (res[0].error or "") or "自动" in (res[0].error or ""), (
        f"失败原因应指向确认门，实际：{res[0].error}")


# ==============================================================================
# ⑤⑥ REST：白名单 + 可区分状态
# ==============================================================================
def test_server_whitelist_includes_allow_auto_erase():
    """REST 白名单不含该键 ⇒ 客户端**永远无法**表态 ⇒ 功能从 REST 被摘掉。"""
    with open(os.path.join(_ROOT, "server.py"), encoding="utf-8") as f:
        src = f.read()
    assert '"allow_auto_erase"' in src, "server.py 参数白名单缺 allow_auto_erase"


def test_server_maps_rejection_to_distinct_status():
    """REST 必须把确认拒绝映射为**独立状态**，不能混进通用 failed。"""
    with open(os.path.join(_ROOT, "server.py"), encoding="utf-8") as f:
        src = f.read()
    assert "AutoEraseNotConfirmed" in src, "server.py 未专门捕获该异常"
    assert "needs_confirmation" in src, "server.py 未给出可程序化区分的状态"


def test_collect_params_passes_allow_auto_erase_through():
    import server

    got = server._collect_params({"allow_auto_erase": "true", "preset": None})
    assert got.get("allow_auto_erase") is True, (
        "字符串 'true' 应被归一为 True（与其它布尔键一致）")
    got2 = server._collect_params({"allow_auto_erase": "no"})
    assert got2.get("allow_auto_erase") is False


# ==============================================================================
# ⑦ 异常消息必须可执行
# ==============================================================================
def test_error_message_lists_actionable_options():
    exc = api.AutoEraseNotConfirmed(entry="remove_watermark_bytes")
    msg = str(exc)
    for token in ("preset", "allow_auto_erase", "detect_watermark_bytes",
                  "API_REQUIRE_AUTO_ERASE_CONFIRM"):
        assert token in msg, f"异常消息缺少可执行信息：{token}"


def test_error_message_names_the_entry():
    exc = api.AutoEraseNotConfirmed(entry="remove_watermark")
    assert "remove_watermark" in str(exc), "应指明是哪个入口被拒（便于定位）"


# ==============================================================================
# ⑧ 源码级：文档不得停留旧签名
# ==============================================================================
def test_api_docstring_examples_are_not_stale():
    """模块 docstring 的示例若停留旧签名，会**教用户写出必抛异常的代码**。

    ⚠ 示例**跨行书写**（第一版按「单行」检查，于是第一个守卫自己假失败——
    `allow_auto_erase` 落在下一行）。故这里按**括号配平**把一条调用拼完整再判。
    """
    doc = api.__doc__ or ""
    lines = doc.splitlines()
    checked = 0
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("res = remove_watermark(") or \
           s.startswith("out_bytes, qr = remove_watermark_bytes("):
            stmt = s
            # 拼接到括号配平（含跨行参数）
            while stmt.count("(") > stmt.count(")") and i + 1 < len(lines):
                i += 1
                stmt += " " + lines[i].strip()
            checked += 1
            assert "allow_auto_erase" in stmt or "preset=" in stmt, (
                f"docstring 示例已过期（会抛异常）：{stmt}")
        i += 1
    assert checked >= 2, f"未检查到两条示例（实际 {checked}）——docstring 结构变了？"


def test_probe_helper_never_raises():
    """`detect_auto_erase_required` 只查询不抛——供 CLI/REST 提前提示。"""
    assert api.detect_auto_erase_required(preset=None, auto_preset=True,
                                          allow_auto_erase=False) is True
    assert api.detect_auto_erase_required(preset="ai_logo", auto_preset=True,
                                          allow_auto_erase=False) is False
    assert api.detect_auto_erase_required(preset=None, auto_preset=True,
                                          allow_auto_erase=True) is False
