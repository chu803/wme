# -*- coding: utf-8 -*-
"""286 · core/model_manager 续审守卫。

首轮（阶段 89 真值审计 + 阶段 165 下载路径守卫）已修 P0：
  · url 无源不得回退全局源（静默装错权重）
  · 下载/回滚必须对称丢弃旧缓存（静默沿用旧权重）
  · 惰性初始化置位顺序（半初始化固化）
  · verify_model / slot_detail 分支键集合一致（避免调用方 KeyError）

本轮补**首轮未钉的分支不变量**（探针 .scratch/probe_model_manager_reaudit.py 实跑 27 项全 OK）：
  · verify_model 的 sha256 不匹配 / sha 计算失败（不可读）两条分支的 reason 文案与字段
  · 体积 0.9*expected 边界（恰好达标 True / 差 1 字节 False），is_model_available 同边界
  · set_active_model 的「不存在槽→False」、「清空缓存」副作用、「persist=True 写状态文件」、
    「persist 写失败仍返回 True（切换已生效）」
  · get_model_slot(None) 返 active；register 无 filename 默认 key.pt；register 覆盖语义
  · _sha256_of_file 缺路径抛异常（不静默返回）
  · rollback_model 的 copy2 抛异常分支 → False
  · blank_verify_result 键集 == _VERIFY_KEYS（声明与实现一致）
  · slot_detail 带/不带 sha 时的 sha_computed 与 sha_ok 计算
"""
from __future__ import annotations

import sys
import json
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import core.model_manager as mm  # noqa: E402


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """隔离进程级单例 + 把 MODEL_DIR / MODEL_STATE_FILE 都指向 tmp，避免写真实权重目录。

    关键：set_active_model(persist=True) 会写 config.MODEL_STATE_FILE；该字段在 import 时已
    固定为真实 models/ 路径，不会随 MODEL_DIR 一起变，故必须显式一并重定向到 tmp。
    """
    monkeypatch.setattr(config, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(config, "MODEL_STATE_FILE", tmp_path / "state.json")
    mm._ensure_init()
    slots, cache, active, inited = (
        dict(mm._SLOTS), dict(mm._CACHE), mm._ACTIVE, mm._INITED,
    )
    yield
    mm._SLOTS.clear(); mm._SLOTS.update(slots)
    mm._CACHE.clear(); mm._CACHE.update(cache)
    mm._ACTIVE = active
    mm._INITED = inited


def _reg(name, **kw):
    slot = mm.register_model_slot(name, **kw)
    return slot


def _write(slot, content: bytes = b"x"):
    p = Path(str(slot.path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# --------------------------------------------------------------------------- #
# 1 · verify_model 的 sha256 分支
# --------------------------------------------------------------------------- #
def test_verify_sha256_mismatch():
    """登记了 sha256 但文件内容不符 → sha_ok=False，reason 指向「可能被篡改/损坏」。"""
    slot = _reg("sha_mismatch", filename="sha_mismatch.pt",
                expected_size=0, sha256="deadbeef")
    _write(slot, b"real weights content")
    v = mm.verify_model("sha_mismatch")
    assert v["sha_ok"] is False
    assert "篡改" in v["reason"], f"reason 应指向篡改/损坏：{v['reason']!r}"


def test_verify_sha_unreadable(monkeypatch):
    """sha 登记但文件算不出 SHA（不可读）→ sha_ok=False、sha_error 非空、reason 据此归因。"""
    slot = _reg("sha_unreadable", filename="sha_unreadable.pt",
                expected_size=0, sha256="abc123")
    _write(slot, b"content")

    def _boom(path, chunk=1):
        raise OSError("Permission denied")
    monkeypatch.setattr(mm, "_sha256_of_file", _boom)

    v = mm.verify_model("sha_unreadable")
    assert v["sha_ok"] is False
    assert bool(v["sha_error"]), "sha_error 应记录原始异常"
    assert "无法读取文件" in v["reason"], f"reason 应归因「无法读取」而非「篡改」：{v['reason']!r}"


def test_verify_none_name_uses_active():
    """verify_model(None) 以当前 active 槽为对象，不抛异常。"""
    v = mm.verify_model(None)
    assert v["name"] == mm.get_active_model()


# --------------------------------------------------------------------------- #
# 2 · 体积 0.9*expected 边界（verify_model 与 is_model_available 同口径）
# --------------------------------------------------------------------------- #
def test_verify_size_boundary():
    slot = _reg("sz", filename="sz.pt", expected_size=1000)
    p = _write(slot, b"x" * 900)  # 恰好 0.9 * 1000
    assert mm.verify_model("sz")["size_ok"] is True
    p.write_bytes(b"x" * 899)      # 差 1 字节
    assert mm.verify_model("sz")["size_ok"] is False


def test_is_available_size_boundary():
    slot = _reg("sz_av", filename="sz_av.pt", expected_size=1000)
    _write(slot, b"x" * 900)
    assert mm.is_model_available("sz_av") is True
    Path(str(slot.path)).write_bytes(b"x" * 899)
    assert mm.is_model_available("sz_av") is False


def test_is_available_none_name():
    assert mm.is_model_available(None) in (True, False)


# --------------------------------------------------------------------------- #
# 3 · set_active_model 的语义与副作用
# --------------------------------------------------------------------------- #
def test_set_active_unknown_slot_returns_false():
    before = mm.get_active_model()
    assert mm.set_active_model("__not_real__") is False
    assert mm.get_active_model() == before


def test_set_active_clears_cache():
    mm._CACHE["__cached__"] = object()
    assert mm.cache_size() >= 1
    assert mm.set_active_model("lama") is True
    assert mm.cache_size() == 0, "切换 active 必须清空权重缓存，防误用旧权重"


def test_set_active_persist_writes_state_file():
    _reg("p_slot", filename="p_slot.pt")
    assert mm.set_active_model("p_slot", persist=True) is True
    state = json.loads(config.MODEL_STATE_FILE.read_text(encoding="utf-8"))
    assert state.get("active") == "p_slot"
    # 还原，避免影响后续用例
    mm.set_active_model("lama", persist=True)


def test_set_active_persist_write_failure_still_true(monkeypatch):
    """persist 写盘失败属非致命：切换仍返回 True 且内存 active 已生效。"""
    class _BoomPath:
        def write_text(self, *a, **k):
            raise OSError("disk full")
    monkeypatch.setattr(config, "MODEL_STATE_FILE", _BoomPath())
    _reg("p2", filename="p2.pt")
    ok = mm.set_active_model("p2", persist=True)
    assert ok is True, "persist 写失败不得被误判为切换失败"
    assert mm.get_active_model() == "p2"
    mm.set_active_model("lama", persist=True)


# --------------------------------------------------------------------------- #
# 4 · register_model_slot 语义
# --------------------------------------------------------------------------- #
def test_register_default_filename():
    slot = _reg("nofname")
    assert slot.filename == "nofname.pt"
    assert str(slot.path).startswith(str(config.MODEL_DIR))


def test_register_overwrites_existing():
    _reg("ov", filename="ov_a.pt")
    _reg("ov", filename="ov_b.pt")
    assert mm.get_model_slot("ov").filename == "ov_b.pt"


def test_get_model_slot_none_returns_active():
    g = mm.get_model_slot(None)
    assert g is not None and g.name == mm.get_active_model()


# --------------------------------------------------------------------------- #
# 5 · 边界与失败分支
# --------------------------------------------------------------------------- #
def test_sha256_of_file_missing_raises():
    with pytest.raises(Exception):
        mm._sha256_of_file(str(config.MODEL_DIR / "__no_such__.bin"))


def test_rollback_copy_failure_returns_false(monkeypatch):
    """rollback 的 copy2 抛异常 → 返回 False（不冒泡）。"""
    import shutil
    slot = _reg("rb_fail", filename="rb_fail.pt")
    _write(slot, b"ORIG")
    Path(mm._backup_path(slot)).write_bytes(b"BACKUP")
    monkeypatch.setattr(shutil, "copy2", lambda s, d: (_ for _ in ()).throw(OSError("copy boom")))
    assert mm.rollback_model("rb_fail") is False


def test_blank_verify_result_keys_consistent():
    """blank_verify_result 的键集必须与 _VERIFY_KEYS 完全一致（声明与实现一致）。"""
    b = mm.blank_verify_result("x")
    assert set(b.keys()) == set(mm._VERIFY_KEYS), (
        f"缺失 {set(mm._VERIFY_KEYS) - set(b.keys())}，多余 {set(b.keys()) - set(mm._VERIFY_KEYS)}"
    )


# --------------------------------------------------------------------------- #
# 6 · slot_detail 的 sha 计算
# --------------------------------------------------------------------------- #
def test_slot_detail_sha_computed_and_ok():
    content = b"deterministic-content-for-sha"
    exp_sha = hashlib.sha256(content).hexdigest()
    slot = _reg("sd_sha", filename="sd_sha.pt", sha256=exp_sha)
    _write(slot, content)
    d = mm.slot_detail("sd_sha")
    assert d["sha_computed"] == exp_sha
    assert d["sha_ok"] is True


def test_slot_detail_no_sha_skips_computation():
    slot = _reg("sd_nosha", filename="sd_nosha.pt", sha256="")
    _write(slot, b"whatever")
    d = mm.slot_detail("sd_nosha")
    assert d["sha_computed"] == ""
    assert d["sha_ok"] is True, "未登记 sha 视为「无需校验 → 通过」"
