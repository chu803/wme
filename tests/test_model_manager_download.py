"""阶段 165 · `core/model_manager` 下载路径守卫。

背景
----
`core/model_manager`（499 代码 / 40 引用测试）是模型注册表核心。既有覆盖在注册/切换/校验；
**下载路径的备份-回滚-缓存语义**此前无守卫。探针（`.workbuddy/scratch/probe_165.py`）抓到两处：

1. **（P1·静默装错权重）** 槽无 urls 时，`download_model` **回退到全局默认源**（= 内置 lama 的地址）
   ⇒ 把 lama 权重下进**别的槽**路径；而该槽若无 `expected_size`/`sha256`，`verify_model` 会
   **静默通过** ⇒ 用户以为下载了目标模型，实际是 lama 权重。探针实测：无 urls 的槽收到 lama 的 3 个地址。
2. **（P2·缓存不对称）** 下载成功后**不丢弃该路径的旧缓存模型**，而 `rollback_model` 会 pop
   ⇒ 已加载过旧模型时，`load_model` 继续命中旧对象，除手动热重载外**静默沿用旧权重**。

修复：① 无 urls 即**明确失败**（不再回退别人的源）；② 下载成功后 `_CACHE.pop(dest)`（与 rollback 对称）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import download_model as dm  # noqa: E402
from core import model_manager as mm  # noqa: E402
from utils.exceptions import ModelNotFoundError  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """把 MODEL_DIR 指向 tmp，并在用例前后快照/还原注册表与缓存（进程级单例）。

    注意：快照前必须**先 `_ensure_init()`**——若在惰性初始化之前快照（此时 `_SLOTS` 为空），
    还原后 `_INITED` 仍为 True，后续用例的 `_ensure_init()` 会短路返回 ⇒ 注册表被清空且
    不再重建（内置槽都拿不到）。这正是「进程级注册表单例 + 惰性初始化」组合的经典陷阱。
    """
    monkeypatch.setattr(config, "MODEL_DIR", tmp_path)
    mm._ensure_init()
    slots, cache = dict(mm._SLOTS), dict(mm._CACHE)
    yield
    mm._SLOTS.clear(); mm._SLOTS.update(slots)
    mm._CACHE.clear(); mm._CACHE.update(cache)


def _stub(monkeypatch, rec, content=b"weights-v2", write=True):
    def fake_download_to(**k):
        rec.update(k)
        if write:
            Path(k["dest"]).write_bytes(content)
        return k["dest"]
    monkeypatch.setattr(dm, "download_to", fake_download_to)


# --------------------------------------------------------------------------- #
# 1 · P1：无 urls 不得回退全局源
# --------------------------------------------------------------------------- #
def test_slot_without_urls_raises_and_never_downloads(monkeypatch):
    rec = {}
    _stub(monkeypatch, rec, write=False)
    mm.register_model_slot("no_urls_slot", filename="x.pt")   # urls 默认 ()
    with pytest.raises(ModelNotFoundError):
        mm.download_model("no_urls_slot")
    assert rec == {}, "无下载源应直接失败，绝不调用下载（更不得回退全局 lama 地址）"


def test_slot_with_urls_passes_them_verbatim(monkeypatch):
    """回归：槽自带 urls 时，原样传给下载器（不被全局源覆盖）。

    刻意**不用内置 lama 槽**——它的 path 指向真实 models/ 目录，下载会真的备份/写入用户权重。
    """
    rec = {}
    _stub(monkeypatch, rec, write=False)
    mm.register_model_slot("u_slot", filename="u.pt",
                           urls=("https://ex.com/u.pt", "https://ex2.com/u.pt"))
    try:
        mm.download_model("u_slot")
    except Exception:  # noqa: BLE001 - 桩未落盘，后续校验会失败，与本用例无关
        pass
    assert rec.get("urls") == ["https://ex.com/u.pt", "https://ex2.com/u.pt"]


# --------------------------------------------------------------------------- #
# 2 · P2：下载成功后必须丢弃旧缓存模型（与 rollback 对称）
# --------------------------------------------------------------------------- #
def test_download_success_drops_stale_cache(monkeypatch):
    rec = {}
    _stub(monkeypatch, rec)
    mm.register_model_slot("dl_slot", filename="dl.pt",
                           urls=("https://example.com/dl.pt",))
    dest = str(mm.get_model_slot("dl_slot").path)
    mm._CACHE[dest] = object()          # 模拟「旧模型已加载」
    assert mm.download_model("dl_slot") == dest
    assert dest not in mm._CACHE, "下载成功后必须丢弃旧缓存模型（否则静默沿用旧权重）"


def test_rollback_drops_cache(monkeypatch):
    """对称性回归：rollback 一直会 pop 缓存。"""
    slot = mm.register_model_slot("rb_slot", filename="rb.pt")
    dest = str(slot.path)
    Path(dest).write_bytes(b"new")
    Path(dest + config.MODEL_BACKUP_SUFFIX).write_bytes(b"bak")
    mm._CACHE[dest] = object()
    assert mm.rollback_model("rb_slot") is True
    assert dest not in mm._CACHE
    assert Path(dest).read_bytes() == b"bak", "回滚必须把备份写回目标路径"


# --------------------------------------------------------------------------- #
# 3 · 备份 / 回滚 / 失败路径
# --------------------------------------------------------------------------- #
def test_download_failure_rolls_back_and_raises(monkeypatch):
    """下载失败 ⇒ 自动回滚到备份，并**向上抛异常**（不得静默返回）。"""
    slot = mm.register_model_slot("fail_slot", filename="fail.pt",
                                  urls=("https://example.com/fail.pt",))
    dest = str(slot.path)
    Path(dest).write_bytes(b"GOOD-OLD")                     # 已有可用权重

    def boom(**k):
        raise RuntimeError("network down")
    monkeypatch.setattr(dm, "download_to", boom)

    with pytest.raises(Exception):
        mm.download_model("fail_slot")
    assert Path(dest).read_bytes() == b"GOOD-OLD", "失败后必须回滚到备份内容"


def test_download_success_removes_backup(monkeypatch):
    """成功 ⇒ 删掉备份（已无用）。"""
    rec = {}
    _stub(monkeypatch, rec)
    slot = mm.register_model_slot("ok_slot", filename="ok.pt",
                                  urls=("https://example.com/ok.pt",))
    dest = str(slot.path)
    Path(dest).write_bytes(b"OLD")
    mm.download_model("ok_slot")
    assert not Path(dest + config.MODEL_BACKUP_SUFFIX).exists(), "成功后应清理备份"


def test_corrupted_after_download_raises(monkeypatch):
    """下载后校验不通过（例如体积不符）⇒ 抛 ModelCorruptedError 且回滚。"""
    slot = mm.register_model_slot("corrupt_slot", filename="c.pt",
                                  urls=("https://example.com/c.pt",),
                                  expected_size=1_000_000)
    dest = str(slot.path)
    Path(dest).write_bytes(b"OLD")
    _stub(monkeypatch, {})
    with pytest.raises(mm.ModelCorruptedError):
        mm.download_model("corrupt_slot")
