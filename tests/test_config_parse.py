# -*- coding: utf-8 -*-
"""配置解析与用户覆盖链路回归测试（阶段 80）。

背景：布尔解析存在**两条入口**，此前各有一套实现：

  · 环境变量（`WME_*`）→ `_env_bool()`，规则严谨（识别 1/true/yes/on/是/开 与 0/false/no/off/否/关）；
  · 用户覆盖文件 → `_USER_OVERRIDE_VALIDATORS` 里的 `lambda v: bool(v)`。

而 Python 中 `bool("false") is True` —— 于是同一份配置、两条入口、**两种结果**：

| 输入 | 环境变量路径 | 覆盖文件路径（修复前） |
|---|---|---|
| `"false"` / `"0"` / `"no"` / `"off"` / `"关"` | False ✅ | **True** ❌ |

后果真实且明确：用户手工把 `{"INPAINT_DUAL_RUN": "false"}` 写入
`config.user_overrides.json` 以为关掉了双跑，实际**意外开启**（耗时翻倍）；
`{"REAL_SUPERRES_ENABLED": "0"}` 会意外开启真实超分。

本文件的核心是 **两入口一致性守卫**：对同一输入，环境变量路径与覆盖文件路径
必须给出相同结果——把「同一语义只有一套实现」变成可执行的断言。

同类不一致（一并修复）：`REAL_SUPERRES_BACKEND` 未做大小写归一化，
用户写 `"ESRGAN"` 被静默丢弃退回 `auto`，而同表的 `INPAINT_DUAL_SECONDARY` 却做了 `.lower()`。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

import config

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 全部布尔型可覆盖键，及其在 `_env_bool` 定义处的默认值（由下方源码守卫交叉验证）。
BOOL_KEYS = {
    "REAL_SUPERRES_ENABLED": False,
    "INPAINT_DUAL_RUN": False,
    "PNG_KEEP_ALPHA": True,
    "IMAGE_APPLY_EXIF_ORIENTATION": True,
    "IMAGE_NORMALIZE_BIT_DEPTH": True,
}

_TRUE_LITERALS = ["true", "TRUE", "True", "1", "yes", "y", "on", "是", "开", "  true  "]
_FALSE_LITERALS = ["false", "FALSE", "False", "0", "no", "n", "off", "否", "关", "  false  "]


# ==============================================================================
# 核心守卫 · 两条入口必须一致
# ==============================================================================
@pytest.mark.parametrize("raw", _TRUE_LITERALS + _FALSE_LITERALS)
def test_bool_entries_agree_on_strings(raw):
    """**守卫**：环境变量路径与覆盖文件路径对同一字符串输入必须给出相同结果。"""
    for key, default in BOOL_KEYS.items():
        env_side = config.parse_bool(raw, default)               # _env_bool 用的同一实现
        file_side = config._USER_OVERRIDE_VALIDATORS[key](raw)   # 覆盖文件路径
        assert env_side == file_side, (
            f"{key} 在输入 {raw!r} 上两条入口不一致：环境变量={env_side} 覆盖文件={file_side}"
        )


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "关", "FALSE"])
def test_false_literals_really_mean_false(raw):
    """直接锁定本次缺陷：这些字符串此前被 `bool()` 解释为 True（意外开启开关）。"""
    assert config.parse_bool(raw, True) is False
    for key in BOOL_KEYS:
        assert config._USER_OVERRIDE_VALIDATORS[key](raw) is False, key


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "是", "开"])
def test_true_literals_really_mean_true(raw):
    """真值字面量同样必须被正确识别（双向都要守住）。"""
    assert config.parse_bool(raw, False) is True
    for key in BOOL_KEYS:
        assert config._USER_OVERRIDE_VALIDATORS[key](raw) is True, key


def test_bool_validator_defaults_match_env_defaults():
    """**守卫**：布尔验证器的回退默认，必须与 `_env_bool` 定义处的默认一致。

    两份默认值若分叉，就会产生「非法值回退到与代码默认不同的值」的隐蔽偏差。
    """
    src = (ROOT / "config.py").read_text(encoding="utf-8")
    found = {
        m.group(1): (m.group(2) == "True")
        for m in re.finditer(r'_env_bool\(\s*"([A-Z0-9_]+)"\s*,\s*(True|False)', src, re.S)
    }
    for key, expected in BOOL_KEYS.items():
        assert key in found, f"未在 config.py 中找到 _env_bool(\"{key}\", ...) 的定义"
        assert found[key] is expected, f"{key} 的 _env_bool 默认值已变化，需同步验证器"
        got = config._USER_OVERRIDE_VALIDATORS[key]("<无法识别>")
        assert got is expected, (
            f"{key} 验证器对无法识别的值返回 {got}，但 _env_bool 默认是 {expected}"
        )


def test_override_lists_are_consistent():
    """白名单与验证器两份清单必须一一对应（分叉会导致某键永久静默失效）。"""
    keys = set(config._USER_OVERRIDABLE_KEYS)
    validators = set(config._USER_OVERRIDE_VALIDATORS)
    assert keys == validators, (
        f"仅白名单有：{sorted(keys - validators)}；仅验证器有：{sorted(validators - keys)}"
    )


# ==============================================================================
# parse_bool 单元语义
# ==============================================================================
def test_parse_bool_passes_through_real_bools():
    """原生 JSON 布尔必须原样通过（设置页写入的正是这种）。"""
    assert config.parse_bool(True, False) is True
    assert config.parse_bool(False, True) is False


@pytest.mark.parametrize("value,expected", [(1, True), (0, False)])
def test_parse_bool_accepts_0_and_1_numbers(value, expected):
    """数字 0/1 是明确信号，应被接受。"""
    assert config.parse_bool(value, not expected) is expected


@pytest.mark.parametrize("value", [2, 3, 0.5, -1, float("nan"), float("inf")])
def test_parse_bool_rejects_ambiguous_numbers(value):
    """其他数字一律回退默认——不做隐式「非零即真」转换（那正是 bool() 的老毛病）。"""
    assert config.parse_bool(value, True) is True
    assert config.parse_bool(value, False) is False


@pytest.mark.parametrize("value", ["abc", "", "   ", "maybe", "enable", None, [], {}, object()])
def test_parse_bool_falls_back_for_unknown(value):
    """无法识别一律返回默认值，且**不反转**。"""
    assert config.parse_bool(value, True) is True
    assert config.parse_bool(value, False) is False


def test_parse_bool_is_whitespace_and_case_insensitive():
    """大小写与首尾空白都应被容忍（手工编辑文件时的常见差异）。"""
    assert config.parse_bool("  FALSE  ", True) is False
    assert config.parse_bool("Yes", False) is True


def test_env_bool_delegates_to_parse_bool(monkeypatch):
    """`_env_bool` 必须与 `parse_bool` 同源（而不是各留一份实现）。"""
    monkeypatch.setenv("WME_INPAINT_DUAL_RUN", "false")
    assert config._env_bool("INPAINT_DUAL_RUN", True) is False
    monkeypatch.setenv("WME_INPAINT_DUAL_RUN", "关")
    assert config._env_bool("INPAINT_DUAL_RUN", True) is False
    monkeypatch.setenv("WME_INPAINT_DUAL_RUN", "  true ")
    assert config._env_bool("INPAINT_DUAL_RUN", False) is True


# ==============================================================================
# 端到端 · 覆盖文件链路
# ==============================================================================
def test_load_user_overrides_parses_string_bools_as_written(tmp_path, monkeypatch):
    """端到端：覆盖文件里的字符串布尔必须按用户意图解析。"""
    f = tmp_path / "ovr.json"
    f.write_text(json.dumps({
        "INPAINT_DUAL_RUN": "false",
        "REAL_SUPERRES_ENABLED": "0",
        "PNG_KEEP_ALPHA": "no",
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(f))

    got = config.load_user_overrides()
    assert got["INPAINT_DUAL_RUN"] is False
    assert got["REAL_SUPERRES_ENABLED"] is False
    assert got["PNG_KEEP_ALPHA"] is False


def test_load_user_overrides_keeps_native_bools(tmp_path, monkeypatch):
    """原生 JSON 布尔（设置页写入形态）行为不变。"""
    f = tmp_path / "ovr.json"
    f.write_text(json.dumps({"INPAINT_DUAL_RUN": True, "PNG_KEEP_ALPHA": False}), encoding="utf-8")
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(f))

    got = config.load_user_overrides()
    assert got["INPAINT_DUAL_RUN"] is True
    assert got["PNG_KEEP_ALPHA"] is False


def test_load_user_overrides_reports_invalid_value(tmp_path, monkeypatch, capsys):
    """非法值应被忽略**且留下可观测痕迹**（此前是完全静默）。"""
    f = tmp_path / "ovr.json"
    f.write_text(json.dumps({"CACHE_KEEP_DAYS": "abc"}), encoding="utf-8")
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(f))

    got = config.load_user_overrides()
    assert "CACHE_KEEP_DAYS" not in got
    err = capsys.readouterr().err
    assert "CACHE_KEEP_DAYS" in err, "非法配置被静默丢弃，用户无法自查"


def test_load_user_overrides_ignores_unknown_and_missing(tmp_path, monkeypatch):
    """白名单外的键与不可解析的文件都不影响其他项。"""
    f = tmp_path / "ovr.json"
    f.write_text(json.dumps({"NOT_A_KEY": 1, "INPAINT_DUAL_RUN": "true"}), encoding="utf-8")
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(f))
    got = config.load_user_overrides()
    assert "NOT_A_KEY" not in got
    assert got["INPAINT_DUAL_RUN"] is True

    missing = tmp_path / "nope.json"
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(missing))
    assert config.load_user_overrides() == {}


def test_save_user_overrides_writes_validated_values(tmp_path, monkeypatch):
    """保存链路：字符串 "false" 必须被规范化为布尔 False 后再落盘（不能写成 True）。"""
    f = tmp_path / "ovr.json"
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(f))

    ok = config.save_user_overrides({"INPAINT_DUAL_RUN": "false", "NOT_A_KEY": 1})
    assert ok is True
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["INPAINT_DUAL_RUN"] is False, "字符串 'false' 被错误地固化为 True"
    assert "NOT_A_KEY" not in data


# ==============================================================================
# 同类不一致 · 超分后端归一化
# ==============================================================================
@pytest.mark.parametrize("raw,expected", [
    ("ESRGAN", "esrgan"),
    ("esrgan", "esrgan"),
    ("  realesrgan  ", "realesrgan"),
    ("AUTO", "auto"),
    ("bogus", "auto"),
    (None, "auto"),
])
def test_superres_backend_normalized(raw, expected):
    """后端名应大小写无关、去空白；与同表的 INPAINT_DUAL_SECONDARY 风格一致。"""
    assert config._normalize_superres_backend(raw) == expected


@pytest.mark.parametrize("raw,expected", [("LAMA", "lama"), (" OpenCV ", "opencv"), ("x", "lama")])
def test_dual_secondary_normalized(raw, expected):
    """副引擎名同样大小写无关、去空白。"""
    assert config._USER_OVERRIDE_VALIDATORS["INPAINT_DUAL_SECONDARY"](raw) == expected


# ==============================================================================
# 数值验证器边界
# ==============================================================================
@pytest.mark.parametrize("raw,expected", [
    (2, 2), (4, 4), (1, 1), (8, 8),
    (0, 4), (9, 4), (999, 4),          # 越界 → 回退 4
])
def test_superres_scale_range(raw, expected):
    assert config._USER_OVERRIDE_VALIDATORS["REAL_SUPERRES_SCALE"](raw) == expected


def test_invalid_values_fall_back_to_defaults():
    """非法值回退到各自默认（与 _env_* 定义处一致）。"""
    v = config._USER_OVERRIDE_VALIDATORS
    assert v["CACHE_KEEP_DAYS"](-1) == 30
    assert v["CACHE_MAX_RECORDS"](-1) == 200
    assert v["CACHE_MAX_BYTES"](1) == 5 * 1024 * 1024 * 1024
