# -*- coding: utf-8 -*-
"""配置模块测试（阶段 13）。

覆盖：
- validate_config() 不抛异常（离线环境无 torch 也应通过）。
- get_config_snapshot() 含阶段 11/12 新增字段。
- 环境变量辅助函数 _env_bool / _env_list / _env_json 的解析逻辑。
"""

import config


def test_validate_config_runs_offline():
    """离线（无 torch）下配置合法性自检不应抛异常。"""
    errors = config.validate_config()
    assert isinstance(errors, list)


def test_snapshot_contains_expected_fields():
    """配置快照应含阶段 11（插件）与阶段 12（模型管理）新增字段。"""
    snap = config.get_config_snapshot()
    for key in ("当前模型槽", "额外模型槽数", "自动发现模型", "插件目录", "已启用插件"):
        assert key in snap, f"配置快照缺少字段：{key}"


def test_env_bool(monkeypatch):
    """_env_bool 应正确解析真/假/缺省。"""
    monkeypatch.setenv("WME_TEST_BOOL", "true")
    assert config._env_bool("TEST_BOOL", False) is True
    monkeypatch.setenv("WME_TEST_BOOL", "0")
    assert config._env_bool("TEST_BOOL", True) is False
    monkeypatch.delenv("WME_TEST_BOOL", raising=False)
    assert config._env_bool("TEST_BOOL", True) is True


def test_env_list(monkeypatch):
    """_env_list 应正确按逗号拆分并去除空白。"""
    monkeypatch.setenv("WME_TEST_LIST", "a, b ,c")
    assert config._env_list("TEST_LIST", ["x"]) == ["a", "b", "c"]
    monkeypatch.delenv("WME_TEST_LIST", raising=False)
    assert config._env_list("TEST_LIST", ["x", "y"]) == ["x", "y"]


def test_env_json(monkeypatch):
    """_env_json 应正确解析 JSON，失败时回退默认值。"""
    monkeypatch.setenv("WME_TEST_JSON", '{"k": 1, "v": [2, 3]}')
    assert config._env_json("TEST_JSON", {}) == {"k": 1, "v": [2, 3]}
    monkeypatch.setenv("WME_TEST_JSON", "不是合法json")
    assert config._env_json("TEST_JSON", {"default": True}) == {"default": True}
