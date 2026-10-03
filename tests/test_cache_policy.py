# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 46 测试：历史保留策略可配置化
 ------------------------------------------------------------------------------
 覆盖：
   1. config 白名单含 CACHE_KEEP_DAYS / CACHE_MAX_RECORDS，校验器回退正确。
   2. save_user_overrides 合并式写入：保存超分默认不会抹掉保留策略，反之亦然。
   3. load_user_overrides 能读回保留策略；_apply_user_overrides 在启动时应用
      （环境变量优先于 JSON）。
   4. 设置页 apply_cache_config：实时改写运行时 + 可选持久化 + 立即 prune 反馈。
   5. 历史页 prune_by_policy：未确认拒绝执行；确认后调用 prune 并刷新画廊。
   6. i18n 键（settings.cache_policy.* / history.prune_policy.*）中英齐全。
 全程零 torch；覆盖文件指向临时目录，不触碰项目根。
==============================================================================
"""
from __future__ import annotations

import json

import pytest

import config
from ui.tab_settings import apply_cache_config
from ui.tab_history import prune_by_policy


@pytest.fixture
def overrides_path(tmp_path, monkeypatch):
    """把用户覆盖文件路径指向临时目录（全程不触碰项目根，自动清理）。"""
    p = tmp_path / "config.user_overrides.json"
    monkeypatch.setattr(config, "USER_OVERRIDES_PATH", str(p))
    return str(p)


def test_whitelist_has_cache_policy_keys():
    assert "CACHE_KEEP_DAYS" in config._USER_OVERRIDABLE_KEYS
    assert "CACHE_MAX_RECORDS" in config._USER_OVERRIDABLE_KEYS


def test_validators_fallback():
    # 超范围 → 回退到各自安全默认（keep_days 30 / max_records 200）。
    assert config._USER_OVERRIDE_VALIDATORS["CACHE_KEEP_DAYS"](99999) == 30
    assert config._USER_OVERRIDE_VALIDATORS["CACHE_KEEP_DAYS"](10) == 10
    assert config._USER_OVERRIDE_VALIDATORS["CACHE_MAX_RECORDS"](99999) == 200
    assert config._USER_OVERRIDE_VALIDATORS["CACHE_MAX_RECORDS"](50) == 50


def test_merge_save_keeps_both_policies(overrides_path):
    # 先写超分默认，再写保留策略，二者应共存（合并式而非整体覆盖）。
    assert config.save_user_overrides({
        "REAL_SUPERRES_ENABLED": True,
        "REAL_SUPERRES_SCALE": 2,
        "REAL_SUPERRES_BACKEND": "esrgan",
    }) is True
    assert config.save_user_overrides({
        "CACHE_KEEP_DAYS": 90,
        "CACHE_MAX_RECORDS": 500,
    }) is True

    loaded = config.load_user_overrides()
    assert loaded["REAL_SUPERRES_BACKEND"] == "esrgan"
    assert loaded["CACHE_KEEP_DAYS"] == 90
    assert loaded["CACHE_MAX_RECORDS"] == 500


def test_merge_save_cache_does_not_wipe_superres(overrides_path):
    # 预置超分覆盖，再单独保存保留策略，超分默认值必须保留。
    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump({
            "REAL_SUPERRES_ENABLED": False,
            "REAL_SUPERRES_SCALE": 8,
            "REAL_SUPERRES_BACKEND": "realesrgan",
        }, f)
    assert config.save_user_overrides({
        "CACHE_KEEP_DAYS": 180,
        "CACHE_MAX_RECORDS": 1000,
    }) is True
    loaded = config.load_user_overrides()
    assert loaded["REAL_SUPERRES_SCALE"] == 8
    assert loaded["REAL_SUPERRES_BACKEND"] == "realesrgan"
    assert loaded["CACHE_KEEP_DAYS"] == 180
    assert loaded["CACHE_MAX_RECORDS"] == 1000


def test_apply_user_overrides_applies_cache(monkeypatch, overrides_path):
    # JSON 中写入保留策略，启动时应用生效（无对应环境变量时）。
    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump({"CACHE_KEEP_DAYS": 120, "CACHE_MAX_RECORDS": 750}, f)
    original = (config.CACHE_KEEP_DAYS, config.CACHE_MAX_RECORDS)
    try:
        config.CACHE_KEEP_DAYS = 30
        config.CACHE_MAX_RECORDS = 200
        config._apply_user_overrides()
        assert config.CACHE_KEEP_DAYS == 120
        assert config.CACHE_MAX_RECORDS == 750
    finally:
        config.CACHE_KEEP_DAYS, config.CACHE_MAX_RECORDS = original


def test_apply_user_overrides_env_precedence(monkeypatch, overrides_path):
    # 环境变量显式设置时，覆盖 JSON 不生效（env > json > default）。
    monkeypatch.setenv("WME_CACHE_KEEP_DAYS", "3650")
    with open(overrides_path, "w", encoding="utf-8") as f:
        json.dump({"CACHE_KEEP_DAYS": 120, "CACHE_MAX_RECORDS": 750}, f)
    original = (config.CACHE_KEEP_DAYS, config.CACHE_MAX_RECORDS)
    try:
        config.CACHE_KEEP_DAYS = 30
        config.CACHE_MAX_RECORDS = 200
        config._apply_user_overrides()
        # 环境变量优先 → 保持非 120。
        assert config.CACHE_KEEP_DAYS != 120
        # 无 env → 被 json 覆盖。
        assert config.CACHE_MAX_RECORDS == 750
    finally:
        config.CACHE_KEEP_DAYS, config.CACHE_MAX_RECORDS = original


def test_apply_cache_config_runtime_and_prune(monkeypatch, overrides_path):
    # 实时改写运行时 + 立即 prune 反馈 + 快照刷新 + 可选持久化。
    _cache = __import__("ui.tab_settings", fromlist=["_CACHE"])._CACHE
    monkeypatch.setattr(_cache, "prune", lambda: 7)
    # 阶段 47：apply_cache_config 追加「按体积清理」，同��打桩保持隔离
    # （返回 0，使 info 中的清理条数仍为 prune 的 7，不改动本用例原有断言语义）。
    monkeypatch.setattr(_cache, "prune_by_size", lambda max_bytes: 0)
    original = (config.CACHE_KEEP_DAYS, config.CACHE_MAX_RECORDS, config.CACHE_MAX_BYTES)
    try:
        # 预置一条超分覆盖，验证保存保留策略不会抹掉它。
        with open(overrides_path, "w", encoding="utf-8") as f:
            json.dump({"REAL_SUPERRES_SCALE": 4}, f)

        snap, info = apply_cache_config(
            keep_days=365, max_records=1234, max_bytes_gb=10, save_default=True)
        assert config.CACHE_KEEP_DAYS == 365
        assert config.CACHE_MAX_RECORDS == 1234
        # 阶段 47：体积上限（GB → 字节）。
        assert config.CACHE_MAX_BYTES == 10 * 1024 ** 3
        assert isinstance(snap, str) and len(snap) > 0
        assert "365" in info and "1234" in info  # cache_applied 含 days/max
        assert "7" in info  # 已清理条数（prune 7 + prune_by_size 0）

        loaded = config.load_user_overrides()
        assert loaded["CACHE_KEEP_DAYS"] == 365
        assert loaded["CACHE_MAX_RECORDS"] == 1234
        # 阶段 47：体积上限同样进入持久化，且不抹掉超分覆盖。
        assert loaded["CACHE_MAX_BYTES"] == 10 * 1024 ** 3
        assert loaded["REAL_SUPERRES_SCALE"] == 4  # 未被抹掉
    finally:
        config.CACHE_KEEP_DAYS, config.CACHE_MAX_RECORDS, config.CACHE_MAX_BYTES = original


def test_prune_by_policy_requires_confirm(monkeypatch):
    # 未确认 → 拒绝执行，且不调用 prune。
    called = {"prune": False}

    def _fake_prune():
        called["prune"] = True
        return 0

    monkeypatch.setattr(
        __import__("ui.tab_history", fromlist=["_CACHE"])._CACHE,
        "prune", _fake_prune,
    )
    result = prune_by_policy(compare=False, confirm=False)
    assert result[0] == __import__("utils.i18n", fromlist=["t"]).t(
        "history.msg.confirm_needed"
    )
    assert called["prune"] is False
    # 六元组结构。
    assert len(result) == 6


def test_prune_by_policy_runs_when_confirmed(monkeypatch):
    # 确认 → 调用 prune，返回清理条数文案。
    monkeypatch.setattr(
        __import__("ui.tab_history", fromlist=["_CACHE"])._CACHE,
        "prune", lambda: 4,
    )
    # 用桩替换 load_gallery，避免触碰真实缓存索引。
    monkeypatch.setattr(
        __import__("ui.tab_history", fromlist=["load_gallery"]),
        "load_gallery", lambda compare=False: ([], None),
    )
    result = prune_by_policy(compare=False, confirm=True)
    t = __import__("utils.i18n", fromlist=["t"]).t
    assert result[0] == t("history.prune_policy_ok", n=4)
    assert len(result) == 6


def test_i18n_cache_policy_keys():
    from utils.i18n import set_language, t

    set_language("zh-CN")
    for k in ("title", "keep_days_label", "keep_days_info", "max_records_label",
              "max_records_info", "save_label", "save_info", "apply"):
        assert t(f"settings.cache_policy.{k}") != f"settings.cache_policy.{k}"
    for k in ("prune_policy_btn", "prune_policy_confirm", "prune_policy_ok",
              "prune_policy_none"):
        assert t(f"history.{k}") != f"history.{k}"
    assert t("settings.snap.msg.cache_applied") != "settings.snap.msg.cache_applied"

    set_language("en-US")
    for k in ("title", "keep_days_label", "keep_days_info", "max_records_label",
              "max_records_info", "save_label", "save_info", "apply"):
        assert t(f"settings.cache_policy.{k}") != f"settings.cache_policy.{k}"
    for k in ("prune_policy_btn", "prune_policy_confirm", "prune_policy_ok",
              "prune_policy_none"):
        assert t(f"history.{k}") != f"history.{k}"

    set_language("zh-CN")
