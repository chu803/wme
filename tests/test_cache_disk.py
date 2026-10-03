"""
阶段 47：历史结果磁盘占用统计 + 按体积保留策略 的单元测试。

全部用例零 torch 依赖，聚焦四层实现：
  1. services.cache_service —— update_result_path 回填体积 / get_stats 聚合磁盘占用 /
     prune_by_size 按体积上限从最旧记录开始清理并清理缩略图。
  2. config —— CACHE_MAX_BYTES 进入用户覆盖白名单，校验器越界回退默认值。
  3. ui 层 —— apply_cache_config 三维（天数/条数/体积）写入运行时配置；
     prune_by_size_policy 二次确认守卫。
  4. utils.i18n —— 阶段 47 新增键双语可解析。

测试隔离范式镜像 test_history_batch_delete.py：
  - 服务层用 enabled_cache fixture（重定向 INDEX_PATH 到临时文件 + 临时 CacheService）。
  - UI 层用 patched_cache fixture（monkeypatch 掉 tab_*._CACHE，避免触碰真实历史索引）。
"""

import os as _os
import sys as _sys

import pytest

# 项目根目录注入 sys.path，确保 ui/services 可被导入（测试运行目录可能为仓库根）。
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config as cfg  # noqa: E402
from services.cache_service import CacheService, HistoryRecord  # noqa: E402
import services.cache_service as cache_service  # noqa: E402
import ui.tab_history as th  # noqa: E402
import ui.tab_settings as ts  # noqa: E402
from utils.i18n import t, set_language  # noqa: E402


def _make_record(rid, name, kind="image", thumb_path=None, result_path=None,
                 status="success"):
    return HistoryRecord(
        id=rid, ts=float(abs(hash(rid)) % 1_000_000_000), original_name=name,
        kind=kind, thumb_path=thumb_path, result_path=result_path, status=status,
    )


# enabled=True 的隔离缓存：把索引重定向到临时文件，避免污染项目真实缓存。
@pytest.fixture
def enabled_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "INDEX_PATH", tmp_path / "history.json")
    cache = CacheService(enabled=True)
    cache._records = []
    return cache


# ---------------------------------------------------------------------------
# 1. CacheService.update_result_path / get_stats / prune_by_size —— 服务层
# ---------------------------------------------------------------------------

def test_update_result_path_prefers_passed_size(enabled_cache):
    rec = _make_record("a", "x.png")
    enabled_cache._records = [rec]
    # 传入 file_size 优先采用，不依赖 result_path 是否存在。
    enabled_cache.update_result_path(rec, "/no/such.png", file_size=2048)
    assert rec.file_size == 2048
    assert rec.result_path == "/no/such.png"


def test_update_result_path_computes_from_result_path(tmp_path, enabled_cache):
    f = tmp_path / "r.png"
    f.write_bytes(b"\x00" * 1234)
    rec = _make_record("a", "x.png")
    enabled_cache._records = [rec]
    # 不传 file_size 时从 result_path 现场计算体积。
    enabled_cache.update_result_path(rec, str(f))
    assert rec.file_size == 1234


def test_update_result_path_disabled_noop(enabled_cache):
    # 关闭缓存时 update_result_path 直接返回，不改写 record。
    enabled_cache.enabled = False
    rec = _make_record("a", "x.png", result_path="p.png")
    enabled_cache.update_result_path(rec, "p.png", file_size=999)
    assert rec.file_size != 999


def test_get_stats_aggregates_by_kind(tmp_path, enabled_cache):
    real = tmp_path / "img.png"
    real.write_bytes(b"\x00" * 100)

    r_no_file = _make_record("a", "no.png", kind="image")
    r_no_file.file_size = 500  # 直接带体积、无文件，验证 file_size 分支
    r_file = _make_record("b", "img.png", kind="image", result_path=str(real))
    r_pdf = _make_record("c", "doc.pdf", kind="pdf")
    r_pdf.file_size = 200

    enabled_cache._records = [r_no_file, r_file, r_pdf]
    stats = enabled_cache.get_stats()

    assert stats["total_count"] == 3
    assert stats["total_bytes"] == 500 + 100 + 200
    assert stats["by_kind"]["image"] == {"count": 2, "bytes": 500 + 100}
    assert stats["by_kind"]["pdf"] == {"count": 1, "bytes": 200}


def test_prune_by_size_converges_and_cleans_thumb(tmp_path, enabled_cache):
    # 三个结果文件各 100 字节，共 300；体积上限设 150 → 删最旧两条、留最新一条。
    files, recs = [], []
    for i in range(3):
        f = tmp_path / f"r{i}.png"
        f.write_bytes(b"\x00" * 100)
        files.append(f)
        r = _make_record(f"r{i}", f"n{i}.png", kind="image", result_path=str(f))
        r.ts = float(i + 1)  # ts 升序：r0 最旧、r2 最新
        recs.append(r)
    # 给最旧一条配缩略图，验证清理时被删除。
    thumb = tmp_path / "thumb_old.png"
    thumb.write_text("t", encoding="utf-8")
    recs[0].thumb_path = str(thumb)

    enabled_cache._records = recs
    removed = enabled_cache.prune_by_size(max_bytes=150)

    assert removed == 2
    assert len(enabled_cache.list_records()) == 1
    # 留存的是最新记录 r2。
    assert enabled_cache.list_records()[0].id == "r2"
    # 缩略图被清理（结果成品图保留）。
    assert not thumb.exists()
    for f in files:  # 结果成品图不应被删
        assert f.exists()


def test_prune_by_size_noop_when_under_cap(tmp_path, enabled_cache):
    f = tmp_path / "r.png"
    f.write_bytes(b"\x00" * 100)
    r = _make_record("a", "n.png", result_path=str(f))
    enabled_cache._records = [r]
    # 上限远大于占用 → 不删任何记录。
    assert enabled_cache.prune_by_size(max_bytes=10 * 1024 * 1024 * 1024) == 0
    assert len(enabled_cache.list_records()) == 1


def test_prune_by_size_zero_cap_returns_0(enabled_cache):
    enabled_cache._records = [_make_record("a", "x.png")]
    assert enabled_cache.prune_by_size(max_bytes=0) == 0
    assert enabled_cache.prune_by_size(max_bytes=-5) == 0


# ---------------------------------------------------------------------------
# 2. config —— CACHE_MAX_BYTES 白名单与校验器
# ---------------------------------------------------------------------------

def test_cache_max_bytes_in_whitelist():
    assert "CACHE_MAX_BYTES" in cfg._USER_OVERRIDABLE_KEYS


def test_cache_max_bytes_validator_fallback():
    validator = cfg._USER_OVERRIDE_VALIDATORS["CACHE_MAX_BYTES"]
    # 合法范围（100MB ~ 100GB）内：原值通过。
    assert validator(10 * 1024 ** 3) == 10 * 1024 ** 3
    # 越上界（200GB）回退默认 5GB。
    assert validator(200 * 1024 ** 3) == 5 * 1024 ** 3
    # 越下界（50MB）回退默认 5GB。
    assert validator(50 * 1024 ** 2) == 5 * 1024 ** 3


# ---------------------------------------------------------------------------
# 3. UI 层：apply_cache_config / prune_by_size_policy
# ---------------------------------------------------------------------------

class _FakeCache:
    """隔离的假缓存，记录调用以断言行为。"""

    def __init__(self):
        self.prune_calls = 0
        self.size_calls = []
        self._records = []

    def list_records(self, *args, **kwargs):
        return list(self._records)

    def prune(self):
        self.prune_calls += 1
        return 1

    def prune_by_size(self, max_bytes):
        self.size_calls.append(max_bytes)
        return 2


@pytest.fixture
def patched_cache(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr(th, "_CACHE", fake)
    return fake


def test_apply_cache_config_three_dims(monkeypatch):
    # 用假缓存隔离，避免触发真实历史清理；save_user_overrides 改为 no-op 防落盘。
    fake = _FakeCache()
    monkeypatch.setattr(ts, "_CACHE", fake)
    monkeypatch.setattr(cfg, "save_user_overrides", lambda patch: None)

    # 用 monkeypatch 登记当前值：用例结束时自动还原，避免污染其它用例。
    # 注意：不能用 try/finally 手动还原——finally 会在断言之前执行，导致断言读到旧值。
    monkeypatch.setattr(cfg, "CACHE_KEEP_DAYS", cfg.CACHE_KEEP_DAYS)
    monkeypatch.setattr(cfg, "CACHE_MAX_RECORDS", cfg.CACHE_MAX_RECORDS)
    monkeypatch.setattr(cfg, "CACHE_MAX_BYTES", cfg.CACHE_MAX_BYTES)

    snap, info = ts.apply_cache_config(30, 200, 10, save_default=True)

    # 三维运行时配置均被写入（max_bytes_gb 换算字节）。
    assert cfg.CACHE_KEEP_DAYS == 30
    assert cfg.CACHE_MAX_RECORDS == 200
    assert cfg.CACHE_MAX_BYTES == 10 * 1024 ** 3
    # 立即清理：先 prune() 再 prune_by_size()。
    assert fake.prune_calls == 1
    assert fake.size_calls == [10 * 1024 ** 3]
    # 信息文案含总体积上限。
    assert "10" in info or "GB" in info


def test_prune_by_size_policy_requires_confirm(patched_cache):
    msg, gallery, pair, html, summary, confirm = th.prune_by_size_policy(
        compare=False, confirm=False)
    # 未确认 → 不调用清理，返回警告文案。
    assert patched_cache.size_calls == []
    assert msg == t("history.msg.confirm_needed")
    assert confirm is not None  # gr.update(value=False) 复位确认框


def test_prune_by_size_policy_runs_with_confirm(patched_cache):
    msg, gallery, pair, html, summary, confirm = th.prune_by_size_policy(
        compare=False, confirm=True)
    # 确认 → 以 config.CACHE_MAX_BYTES 为上限调用 prune_by_size，返回删除条数。
    assert patched_cache.size_calls == [cfg.CACHE_MAX_BYTES]
    assert msg == t("history.prune_size_ok", n=2)
    assert confirm is not None


# ---------------------------------------------------------------------------
# 4. i18n 阶段 47 新键可解析
# ---------------------------------------------------------------------------

def test_i18n_stage47_keys():
    # 注意命名空间：max_bytes_* 属于 settings.cache_policy；
    # prune_size_* / disk_usage.* / detail.size 属于 history。
    set_language("zh-CN")
    for k in ("max_bytes_label", "max_bytes_info"):
        assert t(f"settings.cache_policy.{k}") != f"settings.cache_policy.{k}", \
            f"settings.cache_policy.{k} 缺失翻译（zh-CN）"
    for k in ("disk_usage.title", "disk_usage.total", "disk_usage.kind",
              "prune_size_btn", "prune_size_ok", "prune_size_none"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（zh-CN）"
    assert "MB" in t("history.detail.size", size="1.2 MB")

    set_language("en-US")
    for k in ("max_bytes_label", "max_bytes_info"):
        assert t(f"settings.cache_policy.{k}") != f"settings.cache_policy.{k}", \
            f"settings.cache_policy.{k} 缺失翻译（en-US）"
    for k in ("disk_usage.title", "disk_usage.total", "disk_usage.kind",
              "prune_size_btn", "prune_size_ok", "prune_size_none"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（en-US）"
    assert "MB" in t("history.detail.size", size="1.2 MB")
