"""阶段 158 · 收编入口 `scripts/collect_real_assets.py` 其余未测子命令守卫。

为什么单独建这个文件
--------------------
阶段 152 只审了 `cmd_add`（P0 静默数据损坏），`cmd_scan` / `cmd_download` /
`cmd_list` 与辅助 `_upsert` / `_infer_id_from_filename` 仍零专属测试。本轮：
1. 探针量化（见 `probe_158.py`，隔离 tmp）确认 `cmd_download` 存在 **P1 静默数据损坏**——
   下载直接覆盖已登记的「不同内容」文件、再被 `_upsert` 拒绝，导致
   「manifest 的 sha256 与磁盘文件静默失同步 + 退出码误报成功（0）」，与阶段 152 同类。
2. 最小修复：`_upsert` 由 bool 升级为状态字符串（"accepted"/"skipped"/"rejected"），
   使调用方能区分「同图跳过（幂等）」与「路径冲突（失败）」；`cmd_download` 改为
   **先下到临时文件 → 算 sha → 让 _upsert 判定 → 接受后才 move 进 images/**（先判定后落盘）。
3. 守卫口径：断言**磁盘字节**与 **manifest 的 sha256** 一致（缺陷本质正是「打印成功、磁盘却是另一回事」）。

除下载修复外，本文件还把此前零测试的 scan/list 与辅助函数钉成回归护栏。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import collect_real_assets as cra  # noqa: E402


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _png_bytes(w: int = 32, h: int = 32, color=(10, 20, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _png(path: Path, color=(10, 20, 30)) -> bytes:
    data = _png_bytes(color=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


@pytest.fixture
def bench(tmp_path: Path, monkeypatch):
    """隔离的 benchmark_real/：把脚本的三个路径全局量重定向到 tmp_path。"""
    bdir = tmp_path / "benchmark_real"
    (bdir / "images").mkdir(parents=True)
    monkeypatch.setattr(cra, "BENCH_DIR", str(bdir))
    monkeypatch.setattr(cra, "IMAGES_DIR", str(bdir / "images"))
    monkeypatch.setattr(cra, "MANIFEST_PATH", str(bdir / "manifest.json"))
    return bdir


def _manifest(bdir: Path) -> dict:
    return json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))


def _args_scan(style: str = "unknown", ai_source: str = "other") -> argparse.Namespace:
    return argparse.Namespace(ai_source=ai_source, style=style)


def _args_dl(url: str, output: str = None, style: str = "unknown",
             ai_source: str = "other", timeout: float = 10.0) -> argparse.Namespace:
    return argparse.Namespace(url=url, output=output, ai_source=ai_source,
                              style=style, timeout=timeout)


def _fake_urlopen(content: bytes):
    def _impl(req, timeout=10):
        return io.BytesIO(content)
    return _impl


# --------------------------------------------------------------------------- #
# 1 · cmd_scan
# --------------------------------------------------------------------------- #
def test_scan_adds_new_image_and_saves_manifest(bench: Path, capsys):
    data = _png(bench / "images" / "new.png", (1, 2, 3))
    assert cra.cmd_scan(_args_scan()) == 0
    capsys.readouterr()
    mf = _manifest(bench)
    assert len(mf["images"]) == 1
    assert mf["images"][0]["sha256"] == _sha(data), "manifest 的 sha 必须等于磁盘文件"


def test_scan_skips_duplicate_hash(bench: Path, capsys):
    """两张内容相同、文件名不同的图 → 只登记一条（dedup by content）。"""
    _png(bench / "images" / "a.png", (4, 5, 6))
    _png(bench / "images" / "b.png", (4, 5, 6))  # 同内容
    assert cra.cmd_scan(_args_scan()) == 0
    out = capsys.readouterr().out
    assert "skip" in out, "第二张同 sha 应被跳过"
    assert len(_manifest(bench)["images"]) == 1


def test_scan_rejects_path_collision_with_diff_sha(bench: Path, capsys):
    """磁盘文件内容已与被登记条目 sha 不一致时，scan 不得改写已登记条目的 sha。"""
    C1 = _png_bytes(color=(255, 0, 0))
    S1 = _sha(C1)
    # 被登记条目声明 sha=S1
    (bench / "manifest.json").write_text(json.dumps({
        "version": 1,
        "images": [{
            "id": "dup", "filename": "dup.png", "path": "images/dup.png",
            "ai_source": "midjourney", "watermark_style_hint": "corner_logo",
            "original_size": [32, 32], "sha256": S1,
            "added_at": "2026-09-13T00:00:00Z", "source": "local",
        }]
    }), encoding="utf-8")
    # 但磁盘上是**不同内容** C2
    C2 = _png(bench / "images" / "dup.png", (0, 0, 255))
    assert C2 != C1
    assert cra.cmd_scan(_args_scan()) == 0
    err = capsys.readouterr().err
    assert "拒绝登记" in err, "路径冲突（sha 变化）必须显式拒绝"
    mf = _manifest(bench)
    assert len(mf["images"]) == 1, "不得因冲突产生第二条同路径条目"
    assert mf["images"][0]["sha256"] == S1, "已登记条目的 sha 不得被改写"


def test_scan_preserves_hand_metadata_on_rescan(bench: Path, capsys):
    """对同名同内容图重扫，不得抹掉手工编辑的 ai_source/style_hint。"""
    data = _png(bench / "images" / "mj.png", (7, 8, 9))
    (bench / "manifest.json").write_text(json.dumps({
        "version": 1,
        "images": [{
            "id": "mj_001", "filename": "mj.png", "path": "images/mj.png",
            "ai_source": "midjourney", "watermark_style_hint": "corner_logo",
            "original_size": [32, 32], "sha256": _sha(data),
            "added_at": "2026-09-13T00:00:00Z", "source": "local",
        }]
    }), encoding="utf-8")
    assert cra.cmd_scan(_args_scan()) == 0
    capsys.readouterr()
    mf = _manifest(bench)
    assert len(mf["images"]) == 1
    im = mf["images"][0]
    assert im["ai_source"] == "midjourney", "重扫不得抹掉手工 ai_source"
    assert im["watermark_style_hint"] == "corner_logo", "重扫不得抹掉手工 style_hint"


def test_scan_missing_images_dir_returns_1(bench: Path, monkeypatch):
    # 删掉 images/，模拟目录不存在
    import shutil as _sh
    _sh.rmtree(bench / "images")
    assert cra.cmd_scan(_args_scan()) == 1


# --------------------------------------------------------------------------- #
# 2 · _upsert 状态语义（阶段 158 由 bool 升级）
# --------------------------------------------------------------------------- #
def test_upsert_returns_status_strings():
    m = {"version": 1, "images": []}
    e1 = {"sha256": "AAA", "path": "images/x.png", "filename": "x.png"}
    assert cra._upsert(m, e1) == "accepted"
    e2 = {"sha256": "AAA", "path": "images/y.png", "filename": "y.png"}  # 同 sha
    assert cra._upsert(m, e2) == "skipped"
    e3 = {"sha256": "BBB", "path": "images/x.png", "filename": "x.png"}  # 同 path 异 sha
    assert cra._upsert(m, e3) == "rejected"
    # 只有 e1 真正进入 manifest
    assert len(m["images"]) == 1


# --------------------------------------------------------------------------- #
# 3 · _infer_id_from_filename
# --------------------------------------------------------------------------- #
def test_infer_id_sanitizes_and_collides():
    assert cra._infer_id_from_filename("Foo Bar.png", set()) == "foo_bar"
    assert cra._infer_id_from_filename("MyImage!!.JPG", set()) == "myimage"
    assert cra._infer_id_from_filename("", set()) == "img"
    long = "a" * 40 + ".png"
    assert cra._infer_id_from_filename(long, set()) == "a" * 24
    assert cra._infer_id_from_filename("foo bar.png", {"foo_bar"}) == "foo_bar_2"


# --------------------------------------------------------------------------- #
# 4 · cmd_download
# --------------------------------------------------------------------------- #
def test_download_bad_scheme_returns_1(bench: Path, capsys):
    assert cra.cmd_download(_args_dl("ftp://example.com/x.png")) == 1
    err = capsys.readouterr().err
    assert "http" in err
    assert not (bench / "manifest.json").exists() or len(_manifest(bench)["images"]) == 0


def test_download_failure_returns_2_no_stale_file(bench: Path):
    def _boom(req, timeout=10):
        raise cra.urllib.error.URLError("boom")

    with mock.patch.object(cra.urllib.request, "urlopen", _boom):
        rc = cra.cmd_download(_args_dl("https://example.com/foo.png", output="foo.png"))
    assert rc == 2, "下载失败必须返回 2"
    assert not (bench / "images" / "foo.png").exists(), "失败不得留下残留文件"


def test_download_success_registers_and_saves(bench: Path, capsys):
    data = _png_bytes(color=(11, 22, 33))
    with mock.patch.object(cra.urllib.request, "urlopen", _fake_urlopen(data)):
        rc = cra.cmd_download(_args_dl("https://example.com/ok.png", output="ok.png"))
    assert rc == 0
    capsys.readouterr()
    mf = _manifest(bench)
    assert len(mf["images"]) == 1
    assert mf["images"][0]["sha256"] == _sha(data)
    assert (bench / "images" / "ok.png").read_bytes() == data, "文件必须落到 images/"


def test_download_path_collision_rejected_not_overwritten(bench: Path, capsys):
    """【核心守卫 · 阶段 158 P1 修复】已登记的不同内容文件，下载冲突时：
       ① 退出码必须非 0（不得误报成功）；
       ② 磁盘文件不得被覆盖成新内容；
       ③ manifest 的 sha 必须与磁盘保持一致。"""
    C1 = _png(bench / "images" / "existing.png", (255, 0, 0))  # 已登记内容
    S1 = _sha(C1)
    (bench / "manifest.json").write_text(json.dumps({
        "version": 1,
        "images": [{
            "id": "mj_001", "filename": "existing.png", "path": "images/existing.png",
            "ai_source": "midjourney", "watermark_style_hint": "corner_logo",
            "original_size": [32, 32], "sha256": S1,
            "added_at": "2026-09-13T00:00:00Z", "source": "local",
        }]
    }), encoding="utf-8")
    C2 = _png_bytes(color=(0, 0, 255))  # 下载到的「不同内容」
    assert C2 != C1
    with mock.patch.object(cra.urllib.request, "urlopen", _fake_urlopen(C2)):
        rc = cra.cmd_download(_args_dl("https://example.com/existing.png",
                                       output="existing.png"))
    # ① 退出码必须非 0（旧实现误报 0）
    assert rc != 0, "路径冲突下载不得误报成功"
    # ② 磁盘文件未被覆盖
    assert (bench / "images" / "existing.png").read_bytes() == C1, \
        "拒绝登记的同时，不得覆盖磁盘上已登记的图（阶段 158 P1）"
    # ③ manifest 与磁盘一致
    mf = _manifest(bench)
    assert len(mf["images"]) == 1, "冲突不得产生第二条条目"
    assert mf["images"][0]["sha256"] == S1 == _sha(C1), "manifest 的 sha 必须与磁盘一致"
    assert "拒绝登记" in capsys.readouterr().err


def test_download_duplicate_sha_is_idempotent(bench: Path):
    """下载到与已登记图「内容完全相同」的图 → 视为同图跳过，幂等、不新增条目。"""
    C1 = _png(bench / "images" / "existing.png", (1, 2, 3))
    S1 = _sha(C1)
    (bench / "manifest.json").write_text(json.dumps({
        "version": 1,
        "images": [{
            "id": "mj_001", "filename": "existing.png", "path": "images/existing.png",
            "ai_source": "midjourney", "watermark_style_hint": "corner_logo",
            "original_size": [32, 32], "sha256": S1,
            "added_at": "2026-09-13T00:00:00Z", "source": "local",
        }]
    }), encoding="utf-8")
    with mock.patch.object(cra.urllib.request, "urlopen", _fake_urlopen(C1)):
        rc = cra.cmd_download(_args_dl("https://example.com/existing.png",
                                       output="existing.png"))
    assert rc == 0, "同图重复下载应视为幂等成功"
    assert len(_manifest(bench)["images"]) == 1, "不得因同图重复下载产生重复条目"


# --------------------------------------------------------------------------- #
# 5 · cmd_list
# --------------------------------------------------------------------------- #
def test_list_empty_prints_placeholder(bench: Path, capsys):
    assert cra.cmd_list(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "为空" in out, "空 manifest 必须给出占位提示"


def test_list_prints_rows(bench: Path, capsys):
    _png(bench / "images" / "a.png", (1, 1, 1))
    cra.cmd_scan(_args_scan())
    capsys.readouterr()
    assert cra.cmd_list(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "a" in out, "必须打印已登记条目的信息"
