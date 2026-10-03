"""
阶段 48：孤儿文件扫描与清理（磁盘治理闭环）的单元测试。

全部用例零 torch 依赖，聚焦四层实现：
  1. services.cache_service —— referenced_paths 收集引用集（规范化去重）/
     scan_orphans 与引用集做差集并按来源统计 / purge_orphans 按来源删除并
     返回 (文件数, 释放字节)，且具备「重新扫描」与「空来源不删」两道安全性。
  2. utils.format_utils.format_bytes —— 字节格式化（含容错）；
     ui.tab_history._fmt_bytes 已改为委托它，验证两处输出一致。
  3. ui 层 —— scan_orphans_md 只读渲染清单 / purge_orphans 二次确认与
     来源选择守卫。
  4. utils.i18n —— 阶段 48 新增键双语可解析。

测试隔离范式：
  orphan_env fixture 把 THUMB_DIR / UPLOAD_DIR / INDEX_PATH 全部重定向到
  tmp_path，服务层与 UI 层均不会触碰项目真实缓存目录。
"""

import os as _os
import sys as _sys

import pytest

# 项目根目录注入 sys.path，确保 ui/services 可被导入（测试运行目录可能为仓库根）。
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from services.cache_service import CacheService, HistoryRecord  # noqa: E402
import services.cache_service as cache_service  # noqa: E402
import ui.tab_history as th  # noqa: E402
import ui.tab_settings as ts  # noqa: E402
from utils.format_utils import format_bytes  # noqa: E402
from utils.i18n import t, set_language  # noqa: E402


def _make_record(rid, name, kind="image", **paths):
    """构造记录；paths 可含 original_path / thumb_path / result_path / video_path。"""
    return HistoryRecord(
        id=rid, ts=float(abs(hash(rid)) % 1_000_000_000), original_name=name,
        kind=kind, **paths,
    )


def _w(path, nbytes):
    """写出指定大小的占位文件，返回其字符串路径。"""
    path.write_bytes(b"\x00" * nbytes)
    return str(path)


@pytest.fixture
def orphan_env(tmp_path, monkeypatch):
    """
    完全隔离的孤儿文件环境：缓存目录与历史索引都重定向到临时目录。

    THUMB_DIR / UPLOAD_DIR 是 cache_service 的模块级名称（自 file_service 导入），
    直接 monkeypatch 即可让 scan_orphans / purge_orphans 在临时目录内工作。
    """
    thumbs = tmp_path / "thumbs"
    uploads = tmp_path / "uploads"
    thumbs.mkdir()
    uploads.mkdir()
    monkeypatch.setattr(cache_service, "THUMB_DIR", thumbs)
    monkeypatch.setattr(cache_service, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(cache_service, "INDEX_PATH", tmp_path / "history.json")
    cache = CacheService(enabled=True)
    cache._records = []
    return {"cache": cache, "thumbs": thumbs, "uploads": uploads}


# ---------------------------------------------------------------------------
# 1. 服务层：referenced_paths / scan_orphans / purge_orphans
# ---------------------------------------------------------------------------

def test_referenced_paths_collects_all_fields(orphan_env):
    ca = orphan_env["cache"]
    p1 = _w(orphan_env["thumbs"] / "t1.png", 10)
    p2 = _w(orphan_env["uploads"] / "u1.png", 20)
    p3 = _w(orphan_env["uploads"] / "v1.mp4", 30)
    ca._records = [_make_record(
        "a", "x.png", thumb_path=p1, original_path=p2, video_path=p3)]
    refs = ca.referenced_paths()
    # 三类路径均被收集（result_path 为 None，不产生条目）。
    for p in (p1, p2, p3):
        assert _os.path.normcase(_os.path.abspath(p)) in refs


def test_referenced_paths_dedupes_and_ignores_none(orphan_env):
    ca = orphan_env["cache"]
    p1 = _w(orphan_env["thumbs"] / "t1.png", 10)
    # 同一路径被两条记录引用 → 去重后只算一次。
    ca._records = [
        _make_record("a", "x.png", thumb_path=p1),
        _make_record("b", "y.png", thumb_path=p1, result_path=None),
    ]
    assert len(ca.referenced_paths()) == 1


def test_scan_orphans_excludes_referenced(orphan_env):
    ca = orphan_env["cache"]
    kept = _w(orphan_env["thumbs"] / "kept.png", 10)   # 被引用
    _w(orphan_env["thumbs"] / "orphan.png", 20)        # 孤儿
    ca._records = [_make_record("a", "x.png", thumb_path=kept)]

    scan = ca.scan_orphans()
    files = scan["thumbs"]["files"]
    assert len(files) == 1
    assert _os.path.basename(files[0]) == "orphan.png"
    assert scan["thumbs"]["count"] == 1
    assert scan["thumbs"]["bytes"] == 20
    assert scan["__total__"] == {"count": 1, "bytes": 20}


def test_scan_orphans_groups_by_origin(orphan_env):
    ca = orphan_env["cache"]
    _w(orphan_env["thumbs"] / "t1.png", 100)
    _w(orphan_env["thumbs"] / "t2.png", 100)
    _w(orphan_env["uploads"] / "u1.png", 200)
    scan = ca.scan_orphans()
    assert scan["thumbs"] == {"count": 2, "bytes": 200,
                              "files": scan["thumbs"]["files"]}
    assert scan["uploads"]["count"] == 1
    assert scan["uploads"]["bytes"] == 200
    assert scan["__total__"]["count"] == 3
    assert scan["__total__"]["bytes"] == 400


def test_scan_orphans_skips_subdirectories(orphan_env):
    ca = orphan_env["cache"]
    sub = orphan_env["thumbs"] / "nested"
    sub.mkdir()
    (sub / "inner.png").write_bytes(b"\x00" * 5)
    scan = ca.scan_orphans()
    # 缓存目录应保持扁平：子目录不参与扫描（不计入、也不会被删）。
    assert scan["__total__"]["count"] == 0


def test_scan_orphans_clean_when_empty(orphan_env):
    scan = orphan_env["cache"].scan_orphans()
    assert scan["__total__"] == {"count": 0, "bytes": 0}


def test_purge_orphans_none_deletes_both(orphan_env):
    ca = orphan_env["cache"]
    f1 = orphan_env["thumbs"] / "t1.png"
    f2 = orphan_env["uploads"] / "u1.png"
    _w(f1, 100)
    _w(f2, 200)
    removed, freed = ca.purge_orphans(None)
    assert (removed, freed) == (2, 300)
    assert not f1.exists() and not f2.exists()


def test_purge_orphans_single_origin(orphan_env):
    ca = orphan_env["cache"]
    f1 = orphan_env["thumbs"] / "t1.png"
    f2 = orphan_env["uploads"] / "u1.png"
    _w(f1, 100)
    _w(f2, 200)
    removed, freed = ca.purge_orphans(["thumbs"])
    assert (removed, freed) == (1, 100)
    assert not f1.exists()
    # uploads 未被选中 → 保留。
    assert f2.exists()


def test_purge_orphans_empty_list_deletes_nothing(orphan_env):
    """关键安全性：空列表语义为「未选择 → 不删」，绝不退化成全删。"""
    ca = orphan_env["cache"]
    f1 = orphan_env["thumbs"] / "t1.png"
    f2 = orphan_env["uploads"] / "u1.png"
    _w(f1, 100)
    _w(f2, 200)
    assert ca.purge_orphans([]) == (0, 0)
    assert f1.exists() and f2.exists()


def test_purge_orphans_keeps_referenced(orphan_env):
    ca = orphan_env["cache"]
    kept_path = orphan_env["thumbs"] / "kept.png"
    kept = _w(kept_path, 100)          # _w 返回 str 路径，供记录引用
    orphan = orphan_env["thumbs"] / "orphan.png"
    _w(orphan, 50)
    ca._records = [_make_record("a", "x.png", thumb_path=kept)]
    assert ca.purge_orphans(None) == (1, 50)
    # 被引用的文件必须留存（用 Path 断言存在性）。
    assert kept_path.exists()
    assert not orphan.exists()


def test_purge_orphans_rescans_before_delete(orphan_env):
    """
    安全性：清理前重新扫描——预览之后才被新记录引用的文件不得被删。
    """
    ca = orphan_env["cache"]
    f1 = orphan_env["thumbs"] / "t1.png"
    f2 = orphan_env["thumbs"] / "t2.png"
    _w(f1, 100)
    _w(f2, 100)

    # 第一步：预览（两个都是孤儿）。
    assert ca.scan_orphans()["__total__"]["count"] == 2
    # 第二步：预览之后新增一条记录，引用了 t1。
    ca._records = [_make_record("a", "x.png", thumb_path=str(f1))]
    # 第三步：清理——t1 应被重新判定为「被引用」而保留。
    removed, freed = ca.purge_orphans(None)
    assert (removed, freed) == (1, 100)
    assert f1.exists()          # 新引用的文件未被误删
    assert not f2.exists()      # 仍是孤儿，被清理


def test_purge_orphans_disabled_cache_noop(orphan_env):
    ca = orphan_env["cache"]
    _w(orphan_env["thumbs"] / "t1.png", 100)
    ca.enabled = False
    assert ca.purge_orphans(None) == (0, 0)
    assert (orphan_env["thumbs"] / "t1.png").exists()


# ---------------------------------------------------------------------------
# 2. utils.format_utils —— 字节格式化与历史页委托
# ---------------------------------------------------------------------------

def test_format_bytes_units_and_fallback():
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1536) == "1.50 KB"
    assert format_bytes(1024 ** 2) == "1.00 MB"
    assert format_bytes(5 * 1024 ** 3) == "5.00 GB"
    # 容错：None / 负数 / 非法值一律按 0，不抛异常。
    assert format_bytes(None) == "0 B"
    assert format_bytes(-1) == "0 B"
    assert format_bytes("bad") == "0 B"


def test_history_fmt_bytes_delegates_to_util():
    # 阶段 48：tab_history._fmt_bytes 已委托 utils.format_utils，输出须一致。
    for n in (0, 512, 1024, 1536, 1024 ** 2, 5 * 1024 ** 3):
        assert th._fmt_bytes(n) == format_bytes(n)


# ---------------------------------------------------------------------------
# 3. UI 层：scan_orphans_md / purge_orphans 守卫
# ---------------------------------------------------------------------------

def test_scan_orphans_md_renders_list(orphan_env, monkeypatch):
    monkeypatch.setattr(ts, "_CACHE", orphan_env["cache"])
    _w(orphan_env["thumbs"] / "t1.png", 1024 ** 2)
    _w(orphan_env["uploads"] / "u1.png", 2 * 1024 ** 2)
    md = ts.scan_orphans_md()
    # 两个来源各 1 个文件，合计 2 个；体积以 MB 呈现。
    assert "1" in md and "2" in md
    assert "MB" in md


def test_scan_orphans_md_clean_message(orphan_env, monkeypatch):
    monkeypatch.setattr(ts, "_CACHE", orphan_env["cache"])
    # 无任何文件时返回「很干净」提示（语言无关：与 i18n none 文案一致）。
    assert ts.scan_orphans_md() == t("settings.disk_clean.none")


def test_purge_orphans_requires_confirm(orphan_env, monkeypatch):
    monkeypatch.setattr(ts, "_CACHE", orphan_env["cache"])
    f1 = orphan_env["thumbs"] / "t1.png"
    _w(f1, 100)
    md, info, upd = ts.purge_orphans(True, True, False)
    # 未确认 → 不删除，返回提示并复位确认框。
    assert f1.exists()
    assert info == t("settings.disk_clean.confirm_needed")
    assert upd is not None


def test_purge_orphans_requires_source(orphan_env, monkeypatch):
    monkeypatch.setattr(ts, "_CACHE", orphan_env["cache"])
    f1 = orphan_env["thumbs"] / "t1.png"
    _w(f1, 100)
    md, info, upd = ts.purge_orphans(False, False, True)
    # 已确认但未选来源 → 不删除。
    assert f1.exists()
    assert info == t("settings.disk_clean.need_select")


def test_purge_orphans_runs_when_confirmed(orphan_env, monkeypatch):
    monkeypatch.setattr(ts, "_CACHE", orphan_env["cache"])
    f1 = orphan_env["thumbs"] / "t1.png"
    f2 = orphan_env["uploads"] / "u1.png"
    _w(f1, 1024 ** 2)
    _w(f2, 2 * 1024 ** 2)
    md, info, upd = ts.purge_orphans(True, True, True)
    assert not f1.exists() and not f2.exists()
    assert "2" in info          # 清理条数
    assert "MB" in info         # 释放空间
    # 清理后重新扫描 → 清单应回到「很干净」。
    assert md == t("settings.disk_clean.none")


def test_purge_orphans_partial_message(orphan_env, monkeypatch):
    """
    部分文件因被占用/权限失败时，返回 partial 文案（含跳过数量）。

    阶段 129 修正：此前 `removed=0` 会被 `removed and` 短路进 ok 分支；现已改为
    纯数量比较 `removed < want` ⇒ **全失败（0<want）也报 partial**。本用例仍用
    「预期 2 个、实际 1 个」覆盖常规部分成功路径；全失败路径见
    `test_tab_settings_audit129.py::test_d1_all_failed_reports_partial_not_ok`。
    """
    monkeypatch.setattr(ts, "_CACHE", orphan_env["cache"])
    f1 = orphan_env["thumbs"] / "t1.png"
    f2 = orphan_env["thumbs"] / "t2.png"
    _w(f1, 100)
    _w(f2, 100)
    # 只删 thumbs（预期 2 个），但先让 t2 被引用 → 实际只删 1 个。
    monkeypatch.setattr(
        ts._CACHE, "scan_orphans",
        lambda: {"thumbs": {"count": 2, "bytes": 200, "files": [str(f1)]},
                 "uploads": {"count": 0, "bytes": 0, "files": []},
                 "__total__": {"count": 2, "bytes": 200}},
    )
    md, info, upd = ts.purge_orphans(True, False, True)
    assert not f1.exists()
    assert f2.exists()          # 未被清理（模拟跳过）
    assert "1" in info


# ---------------------------------------------------------------------------
# 4. i18n 阶段 48 新键可解析
# ---------------------------------------------------------------------------

def test_i18n_stage48_keys():
    keys = ("title", "hint", "scan_btn", "src_thumbs", "src_uploads", "clean_btn",
            "confirm_label", "line", "total", "none", "need_scan", "need_select",
            "confirm_needed", "ok", "partial", "fail")
    for lang in ("zh-CN", "en-US"):
        set_language(lang)
        for k in keys:
            assert t(f"settings.disk_clean.{k}") != f"settings.disk_clean.{k}", \
                f"settings.disk_clean.{k} 缺失翻译（{lang}）"
    set_language("zh-CN")
    assert t("settings.disk_clean.title").startswith("### 🧹 磁盘清理")
    set_language("en-US")
    assert t("settings.disk_clean.title").startswith("### 🧹 Disk Cleanup")
