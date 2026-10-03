# -*- coding: utf-8 -*-
"""模型管理注册表测试（阶段 12/13）。

约束（沙箱 safe-delete）：
   本环境 Python 的 os.remove/shutil.rmtree 被拦截，故本文件「不创建真实权重文件」，
   仅通过 monkeypatch 模拟文件存在性来验证 verify/rollback 的判定逻辑；
   所有注册都发生在内存 _SLOTS 字典，测试结束 pop 掉即可，无需删除任何文件。

覆盖：
- 注册模型槽 → list_models 可见。
- active 切换（set/get_active_model）。
- 缺失权重时 is_model_available / verify_model 返回 False。
- 模拟文件存在时 verify_model 返回 ok=True。
- 无备份时 rollback_model 返回 False；模拟备份存在时返回 True。
"""

import shutil

from core import model_manager as mm
import config as _config


def _register_tmp(name, **kw):
    slot = mm.register_model_slot(name, **kw)
    return slot


def test_register_and_list():
    """注册临时槽后，list_models 应包含它。"""
    _register_tmp("tm_list", filename="tm_list.pt")
    try:
        names = [s["name"] for s in mm.list_models()]
        assert "tm_list" in names
    finally:
        mm._SLOTS.pop("tm_list", None)


def test_active_switch(restore_active_model):
    """切换 active 槽并取回，且不影响 lama 的可用性。"""
    _register_tmp("tm_active", filename="tm_active.pt")
    try:
        assert mm.set_active_model("tm_active") is True
        assert mm.get_active_model() == "tm_active"
        assert mm.set_active_model("lama") is True
        assert mm.get_active_model() == "lama"
    finally:
        mm._SLOTS.pop("tm_active", None)


def test_is_model_available_missing():
    """未放权重文件时，该槽不可用。"""
    _register_tmp("tm_missing", filename="tm_missing_noexist.pt")
    try:
        assert mm.is_model_available("tm_missing") is False
    finally:
        mm._SLOTS.pop("tm_missing", None)


def test_verify_missing_returns_false():
    """缺失权重时 verify_model 返回 ok=False 且 exists=False。"""
    _register_tmp("tm_verify", filename="tm_verify_noexist.pt")
    try:
        v = mm.verify_model("tm_verify")
        assert v["ok"] is False
        assert v["exists"] is False
    finally:
        mm._SLOTS.pop("tm_verify", None)


def test_verify_ok_via_monkeypatch(monkeypatch):
    """用 monkeypatch 模拟「文件存在且体积足够」，verify_model 应判定通过。"""
    _register_tmp("tm_vok", filename="tm_vok.pt", expected_size=1000)
    try:
        monkeypatch.setattr(mm._os.path, "exists", lambda p: True)
        monkeypatch.setattr(mm._os.path, "getsize", lambda p: 1000)
        v = mm.verify_model("tm_vok")
        assert v["ok"] is True
        assert v["reason"] == "校验通过"
        assert v["size_ok"] is True
    finally:
        mm._SLOTS.pop("tm_vok", None)


def test_rollback_no_backup():
    """无备份文件时，rollback_model 应返回 False。"""
    _register_tmp("tm_rbn", filename="tm_rbn.pt")
    try:
        assert mm.rollback_model("tm_rbn") is False
    finally:
        mm._SLOTS.pop("tm_rbn", None)


def test_rollback_success_via_monkeypatch(monkeypatch):
    """用 monkeypatch 模拟「备份存在 + copy2 成功」，rollback_model 应返回 True。"""
    _register_tmp("tm_rbo", filename="tm_rbo.pt")
    try:
        slot = mm.get_model_slot("tm_rbo")
        bak = mm._backup_path(slot)
        # 仅当路径是备份文件时返回存在
        monkeypatch.setattr(mm._os.path, "exists", lambda p: p == bak)
        # 劫持 copy2，记录调用
        captured = {}
        monkeypatch.setattr(shutil, "copy2", lambda src, dst: captured.setdefault("call", (src, dst)))
        assert mm.rollback_model("tm_rbo") is True
        assert captured.get("call") == (bak, str(slot.path))
    finally:
        mm._SLOTS.pop("tm_rbo", None)


def test_autodiscover_runs_without_error():
    """自动发现阶段不抛异常（即使 models/ 下无额外权重）。"""
    slots = mm.list_models()
    assert isinstance(slots, list)
    # 至少应含内置 lama 槽
    names = [s["name"] for s in slots]
    assert "lama" in names


def test_cache_size_and_clear():
    """cache_size 返回整数，clear_cache 后归零（不依赖真实加载 / torch）。"""
    assert isinstance(mm.cache_size(), int)
    # 模拟放入一个缓存条目再清空。
    mm._CACHE["__test_key__"] = object()
    try:
        assert mm.cache_size() >= 1
    finally:
        mm.clear_cache()
    assert mm.cache_size() == 0


def test_slot_detail_missing(monkeypatch):
    """权重文件不存在时，slot_detail 应返回 exists=False 且不计算 SHA。"""
    _register_tmp("tm_dmiss", filename="tm_dmiss_noexist.pt")
    try:
        monkeypatch.setattr(mm._os.path, "exists", lambda p: False)
        d = mm.slot_detail("tm_dmiss")
        assert d["exists"] is False
        assert "不存在" in d["reason"]
        assert d["loaded"] is False
        assert d.get("sha_computed") == ""
    finally:
        mm._SLOTS.pop("tm_dmiss", None)


def test_slot_detail_no_sha(monkeypatch):
    """无 sha256 登记的槽，slot_detail 不应计算 SHA，且体积校验通过即 ok。"""
    _register_tmp("tm_dnosha", filename="tm_dnosha.pt", expected_size=0, sha256="")
    try:
        monkeypatch.setattr(mm._os.path, "exists", lambda p: True)
        monkeypatch.setattr(mm._os.path, "getsize", lambda p: 1000)
        d = mm.slot_detail("tm_dnosha")
        assert d["exists"] is True
        assert d.get("sha_computed") == ""
        assert d["ok"] is True
    finally:
        mm._SLOTS.pop("tm_dnosha", None)


def test_reload_active_mock(monkeypatch):
    """monkeypatch load_model 后，reload_active 应返回 ok 并填充内存缓存。"""
    def _fake_load(name=None, device=None, force_reload=False):
        slot = mm.get_model_slot(name)
        mm._CACHE[str(slot.path)] = object()
        return object()

    monkeypatch.setattr(mm, "load_model", _fake_load)
    try:
        r = mm.reload_active()
        assert r["ok"] is True
        assert r["loaded"] is True
        assert mm.cache_size() >= 1
    finally:
        mm.clear_cache()
