# -*- coding: utf-8 -*-
"""
阶段 141 守卫测试：`utils/brand.hex_to_rgb` 的「静默伪装」边界。

背景（详见 docs/141-brand真值审计-非字符串静默转换与内部空白容忍.md）：
  阶段 91 已把品牌令牌收敛为单一真值来源并配了较完整的守卫（test_ui_audit91 19 条），
  但 `hex_to_rgb` 的两个边界从未被覆盖：
    D1 非字符串输入被 `str()` 静默转换 —— `hex_to_rgb(123456)` 返回 (18,52,86)，
       把调用方 bug 伪装成合法配色，且**从不报错**（报错文案只在长度不符时才出现）。
    D3 内部空白被 `int(x,16)` 静默容忍 —— `hex_to_rgb("#12 456")` 返回 (18,4,86)。
  两者都与模块 docstring 声明的「不静默兜底」原则冲突。

本文件钉住修复后的行为：任意一条回退都会在这里变红。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils import brand  # noqa: E402


# ==============================================================================
# D1. 非字符串输入必须抛 TypeError（不得被 str() 静默转换）
# ==============================================================================
@pytest.mark.parametrize(
    "bad",
    [123456, 0x2563EB, None, (37, 99, 235), [37, 99, 235], b"2563eb", 1.5, True],
    ids=["int", "int-hex-literal", "none", "tuple", "list", "bytes", "float", "bool"],
)
def test_non_string_raises_type_error(bad):
    """
    修复前：`str(123456)` → "123456" → (18,52,86)，静默产出「看起来正常」的颜色。
    """
    with pytest.raises(TypeError):
        brand.hex_to_rgb(bad)


def test_int_input_no_longer_silently_parsed():
    """最危险的具体案例：整数被当成十六进制字符串解析。"""
    with pytest.raises(TypeError):
        brand.hex_to_rgb(123456)


def test_type_error_message_names_actual_type():
    """错误信息必须指出真实类型（便于定位调用方 bug），而非误导性的「长度不对」。"""
    with pytest.raises(TypeError) as ei:
        brand.hex_to_rgb((37, 99, 235))
    msg = str(ei.value)
    assert "tuple" in msg, f"错误信息未指出真实类型：{msg!r}"


# ==============================================================================
# D3. 内部空白必须抛 ValueError（首尾空白仍容忍）
# ==============================================================================
@pytest.mark.parametrize(
    "bad",
    ["#12 456", "#1 2345", " #25 63eb", "#2563 eb", "#25\t63eb", "# 2563eb"],
    ids=["mid-space", "two-blocks", "lead+mid", "trail+mid", "tab", "after-hash"],
)
def test_internal_whitespace_raises_value_error(bad):
    """
    修复前：`int(" 4", 16)` 容忍前导空白 → "#12 456" 静默得到 (18,4,86)。
    """
    with pytest.raises(ValueError):
        brand.hex_to_rgb(bad)


@pytest.mark.parametrize(
    "good,expected",
    [
        ("  #2563eb  ", (37, 99, 235)),
        ("\n#2563eb", (37, 99, 235)),
        ("\t2563eb\t", (37, 99, 235)),
        ("#2563eb\n", (37, 99, 235)),
    ],
    ids=["spaces", "newline", "tabs", "trailing-newline"],
)
def test_outer_whitespace_still_tolerated(good, expected):
    """回归护栏：首尾空白历来被容忍，修复不得收紧这条（否则破坏既有调用方）。"""
    assert brand.hex_to_rgb(good) == expected


# ==============================================================================
# 回归护栏：原有正确行为一字不变
# ==============================================================================
@pytest.mark.parametrize(
    "good,expected",
    [
        ("#2563eb", (37, 99, 235)),
        ("2563eb", (37, 99, 235)),
        ("#FFF", (255, 255, 255)),
        ("#fff", (255, 255, 255)),
        ("#f0a", (255, 0, 170)),
        ("#2563EB", (37, 99, 235)),
    ],
    ids=["std", "no-hash", "short-upper", "short-lower", "short-mixed", "upper"],
)
def test_valid_inputs_unchanged(good, expected):
    assert brand.hex_to_rgb(good) == expected


@pytest.mark.parametrize("bad", ["#12345", "#1234567", "#gggggg", "", "#", "#12", "#1g3"])
def test_length_and_charset_errors_still_raise(bad):
    """阶段 91 既有守卫的等价复刻（防本次修复把旧错误分支弄丢）。"""
    with pytest.raises(ValueError):
        brand.hex_to_rgb(bad)


# ==============================================================================
# 令牌表与派生链（阶段 91 契约的回归护栏）
# ==============================================================================
def test_all_tokens_valid_and_derivable():
    for name, value in brand.HEX_TOKENS.items():
        assert isinstance(value, str), f"令牌 {name} 应为字符串"
        assert brand.hex_to_rgb(value) is not None


def test_token_table_matches_public_hex_names():
    """__all__ 里的 *_HEX 与 HEX_TOKENS 键必须完全一致（双向）。"""
    public = {n for n in brand.__all__ if n.endswith("_HEX")}
    assert public == set(brand.HEX_TOKENS)


def test_derived_rgb_reads_token_at_call_time(monkeypatch):
    """阶段 88 经验 23：派生值必须调用时读取，不得冻结。"""
    monkeypatch.setattr(brand, "PRIMARY_HEX", "#ff0000")
    assert brand.primary_rgb() == (255, 0, 0)


def test_hex_tokens_is_snapshot_not_live_view():
    """
    登记既有语义：HEX_TOKENS 是**构建期快照**（docstring 已注明），
    不随运行时改模块常量而更新。若要「跟随当前值」须直接读模块属性。
    """
    old = brand.PRIMARY_HEX
    try:
        brand.PRIMARY_HEX = "#ff0000"
        assert brand.HEX_TOKENS["PRIMARY_HEX"] == old, "快照语义已被改变"
        assert brand.primary_rgb() == (255, 0, 0)
    finally:
        brand.PRIMARY_HEX = old


# ==============================================================================
# 子进程自检守卫
# ==============================================================================
def test_selfcheck_runs_clean():
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "utils", "brand.py")],
        capture_output=True, text=True, cwd=_ROOT, timeout=120,
    )
    assert r.returncode == 0, f"自检失败 rc={r.returncode}\n{r.stdout}\n{r.stderr}"


def test_selfcheck_declares_new_boundaries():
    """钉住自检覆盖本阶段新增的两组断言。"""
    src = open(os.path.join(_ROOT, "utils", "brand.py"), encoding="utf-8").read()
    assert "TypeError" in src and "内部含空白" in src
