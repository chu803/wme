# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 35 测试：设置页暴露真实超分旋钮
 ------------------------------------------------------------------------------
 覆盖：
   1. 用户覆盖配置（config.user_overrides.json）的读写 / 白名单 / 损坏容错 /
      环境变量优先级（env > json > default）。
   2. 真实超分控件注册表（ui.components）与跨页刷新更新构造。
   3. apply_superres_config 实时改写运行时配置并返回正确输出结构。
   4. 设置页真实超分 i18n 键（zh-CN / en-US）齐全。
==============================================================================
"""
from __future__ import annotations

import json

import pytest

import config
from ui.components import (
    clear_superres_widgets,
    get_superres_widgets,
    register_superres_widget,
)
from ui.tab_settings import _superres_refresh_updates, apply_superres_config


@pytest.fixture
def overrides_path(tmp_path, monkeypatch):
    """把用户覆盖文件路径指向临时目录（全程不触碰项目根，自动清理）。"""
    p = tmp_path / "config.user_overrides.json"
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(p))
    return str(p)


def test_user_overrides_roundtrip(overrides_path):
    assert config.save_user_overrides({
        "REAL_SUPERRES_ENABLED": True,
        "REAL_SUPERRES_SCALE": 2,
        "REAL_SUPERRES_BACKEND": "esrgan",
    }) is True
    loaded = config.load_user_overrides()
    assert loaded == {
        "REAL_SUPERRES_ENABLED": True,
        "REAL_SUPERRES_SCALE": 2,
        "REAL_SUPERRES_BACKEND": "esrgan",
    }


def test_load_user_overrides_missing_file(overrides_path):
    assert config.load_user_overrides() == {}


def test_load_user_overrides_corrupt(overrides_path):
    with open(overrides_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    assert config.load_user_overrides() == {}


def test_load_user_overrides_whitelist_only(overrides_path):
    # 写入白名单外键与非法值，应被过滤 / 回退默认。
    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "REAL_SUPERRES_ENABLED": True,
                "REAL_SUPERRES_SCALE": 99,        # 非法（超 1~8）→ 回退 4
                "REAL_SUPERRES_BACKEND": "bogus", # 非法 → 回退 auto
                "SOME_OTHER_KEY": "hacked",       # 不在白名单 → 忽略
            },
            f,
        )
    loaded = config.load_user_overrides()
    assert "SOME_OTHER_KEY" not in loaded
    assert loaded["REAL_SUPERRES_SCALE"] == 4
    assert loaded["REAL_SUPERRES_BACKEND"] == "auto"
    assert loaded["REAL_SUPERRES_ENABLED"] is True


def test_apply_user_overrides_env_precedence(monkeypatch, overrides_path):
    # 环境变量显式设置时，覆盖 JSON 不生效（env > json > default）。
    monkeypatch.setenv("WME_REAL_SUPERRES_BACKEND", "realesrgan")
    original = (
        config.REAL_SUPERRES_ENABLED,
        config.REAL_SUPERRES_SCALE,
        config.REAL_SUPERRES_BACKEND,
    )
    try:
        config.REAL_SUPERRES_ENABLED = True
        config.REAL_SUPERRES_SCALE = 4
        config.REAL_SUPERRES_BACKEND = "auto"
        config.save_user_overrides({
            "REAL_SUPERRES_ENABLED": False,
            "REAL_SUPERRES_SCALE": 8,
            "REAL_SUPERRES_BACKEND": "esrgan",
        })
        config._apply_user_overrides()
        # 后端：env 设了 → json 不生效，保持非 esrgan（未被 json 覆盖）。
        assert config.REAL_SUPERRES_BACKEND != "esrgan"
        # 其余键无 env → 被 json 覆盖。
        assert config.REAL_SUPERRES_ENABLED is False
        assert config.REAL_SUPERRES_SCALE == 8
    finally:
        config.REAL_SUPERRES_ENABLED = original[0]
        config.REAL_SUPERRES_SCALE = original[1]
        config.REAL_SUPERRES_BACKEND = original[2]


def test_register_and_refresh_superres_widgets():
    clear_superres_widgets()
    # 阶段 248：本用例写 3 个 config 全局量，**必须还原**——原 `finally` 只
    #   `clear_superres_widgets()`，从不还原 config ⇒ 实测把
    #   REAL_SUPERRES_SCALE/BACKEND 泄漏给后续用例（运行期检测器抓到）。
    _orig = (config.REAL_SUPERRES_ENABLED,
             config.REAL_SUPERRES_SCALE,
             config.REAL_SUPERRES_BACKEND)
    try:
        # 用占位对象模拟功能页控件（刷新只构造 gr.update，无需真实组件）。
        class _Fake:
            pass

        register_superres_widget(_Fake(), _Fake(), _Fake())
        assert len(get_superres_widgets()) == 1

        config.REAL_SUPERRES_ENABLED = True
        config.REAL_SUPERRES_SCALE = 3
        config.REAL_SUPERRES_BACKEND = "esrgan"
        updates = _superres_refresh_updates()
        assert len(updates) == 3
        assert updates[0].get("value") is True
        assert updates[1].get("value") == 3
        assert updates[2].get("value") == "esrgan"
    finally:
        clear_superres_widgets()
        (config.REAL_SUPERRES_ENABLED,
         config.REAL_SUPERRES_SCALE,
         config.REAL_SUPERRES_BACKEND) = _orig    # 阶段 248：还原 config


def test_apply_superres_config_updates_config_and_returns():
    clear_superres_widgets()
    original = (
        config.REAL_SUPERRES_ENABLED,
        config.REAL_SUPERRES_SCALE,
        config.REAL_SUPERRES_BACKEND,
    )
    try:
        class _Fake:
            pass

        register_superres_widget(_Fake(), _Fake(), _Fake())

        result = apply_superres_config(True, 2, "esrgan", False)
        # 1 组 → 3 个控件更新 + 快照 + 信息。
        assert len(result) == 5
        assert config.REAL_SUPERRES_ENABLED is True
        assert config.REAL_SUPERRES_SCALE == 2
        assert config.REAL_SUPERRES_BACKEND == "esrgan"
        # 末尾是快照 Markdown 与信息文案（语言无关，仅校验非空结构）。
        assert isinstance(result[-2], str) and len(result[-2]) > 0
        assert isinstance(result[-1], str) and len(result[-1]) > 0
    finally:
        config.REAL_SUPERRES_ENABLED = original[0]
        config.REAL_SUPERRES_SCALE = original[1]
        config.REAL_SUPERRES_BACKEND = original[2]
        clear_superres_widgets()


def test_i18n_settings_superres_keys():
    from utils.i18n import set_language, t

    set_language("zh-CN")
    for k in ("title", "enabled_label", "scale_label", "backend_label",
              "save_label", "apply"):
        assert t(f"settings.real_superres.{k}") != f"settings.real_superres.{k}"
    assert t("settings.snap.real_superres_title") != "settings.snap.real_superres_title"
    assert t("settings.snap.msg.superres_applied") != "settings.snap.msg.superres_applied"

    set_language("en-US")
    for k in ("title", "enabled_label", "scale_label", "backend_label",
              "save_label", "apply"):
        assert t(f"settings.real_superres.{k}") != f"settings.real_superres.{k}"
    assert t("settings.snap.real_superres_title") != "settings.snap.real_superres_title"
    assert t("settings.snap.msg.superres_applied") != "settings.snap.msg.superres_applied"

    set_language("zh-CN")
