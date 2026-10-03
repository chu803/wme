"""阶段 152 · 收编入口 `scripts/collect_real_assets.py` 的守卫。

为什么单独建这个文件
--------------------
这个模块是**闭合 P0（`ai_logo` 红灯）的唯一入口**（阶段 151 已端到端验证
「丢图 → 预检红 → 标注 → 预检绿」），但此前**只有 `scan` 有测试、`cmd_add` 零测试**。
阶段 152 的真值审计因此在这里一次性抓到两个缺陷：

1. **（P0 · 静默数据损坏）`add --copy` 先覆盖、后拒绝**：
   旧实现先 `shutil.copy2` 覆盖 `images/` 下的同名文件，**之后**才让 `_upsert` 判定；
   于是一条「路径已存在但哈希变化 ⇒ 拒绝登记」的守卫，会在**拒绝的同时把数据集里
   那张图悄悄换掉**。提示说「没登记」，磁盘却已经变了，而且没有任何后续信号——
   manifest 里的 `sha256` 从此与磁盘文件不一致，整个基准的可信度静默归零。

2. **（P1 · 未捕获崩溃）跨盘符 `add`（无 `--copy`）抛 traceback**：
   `os.path.relpath(src, BENCH_DIR)` 在 Windows 跨盘符时抛
   `ValueError: path is on mount 'D:', start on mount 'C:'`，旧实现未捕获。

修法（最小）：`_build_entry` 增加 `abs_src` 参数让**哈希取自源文件**，
从而把顺序改成**先判定、后落盘**；`relpath` 包 try/except 退化成绝对路径并告警。

守卫口径：这里断言的是**磁盘上的字节**，不是函数返回值——
因为缺陷的本质正是「返回值说成功/拒绝，磁盘却是另一回事」。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import collect_real_assets as cra  # noqa: E402


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _png(path: Path, color: tuple) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch):
    """隔离的 benchmark_real/：把脚本的三个路径全局量重定向到 tmp_path。"""
    bdir = tmp_path / "benchmark_real"
    (bdir / "images").mkdir(parents=True)
    monkeypatch.setattr(cra, "BENCH_DIR", str(bdir))
    monkeypatch.setattr(cra, "IMAGES_DIR", str(bdir / "images"))
    monkeypatch.setattr(cra, "MANIFEST_PATH", str(bdir / "manifest.json"))
    return bdir


def _args(path: Path, style: str = "unknown", copy: bool = True) -> argparse.Namespace:
    return argparse.Namespace(path=str(path), ai_source="other", style=style, copy=copy)


def _manifest(bdir: Path) -> dict:
    return json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1 · P0 守卫：拒绝登记时**不得**改动数据集文件
# --------------------------------------------------------------------------- #
def test_add_copy_rejects_without_overwriting_dataset_file(bench: Path, capsys):
    """同名、不同内容的图被拒绝时，已收编的那张图必须**字节级原封不动**。

    这是阶段 152 的核心缺陷。旧实现会先把 `images/dup.png` 覆盖成新内容，
    然后才打印「拒绝登记」——拒绝的是**登记**，不是**复制**。
    """
    red = bench / "sA" / "dup.png"
    blue = bench / "sB" / "dup.png"
    _png(red, (255, 0, 0))
    _png(blue, (0, 0, 255))

    assert cra.cmd_add(_args(red)) == 0
    stored = bench / "images" / "dup.png"
    sha_before = _sha(stored)
    assert sha_before == _sha(red), "第一次收编应把红图复制进 images/"

    capsys.readouterr()
    assert cra.cmd_add(_args(blue)) == 0, "拒绝登记也应正常返回，不得崩溃"
    assert "拒绝登记" in capsys.readouterr().err, "必须显式告知被拒绝"

    # ---- 关键断言：磁盘上的字节没变 ----
    assert _sha(stored) == sha_before, (
        "守卫拒绝登记的同时，不得覆盖数据集里已收编的图片（阶段 152 P0）")

    # ---- manifest 与磁盘保持一致 ----
    mf = _manifest(bench)
    assert len(mf["images"]) == 1, "被拒绝的条目不得写进 manifest"
    assert mf["images"][0]["sha256"] == _sha(stored), "manifest 的 sha256 必须与实际文件一致"


# --------------------------------------------------------------------------- #
# 2 · 同内容重复添加：幂等跳过，且不产生多余写入
# --------------------------------------------------------------------------- #
def test_add_copy_same_content_is_idempotent(bench: Path, capsys):
    src = bench / "same.png"
    _png(src, (10, 20, 30))

    assert cra.cmd_add(_args(src)) == 0
    capsys.readouterr()
    assert cra.cmd_add(_args(src)) == 0
    assert "skip" in capsys.readouterr().out, "同一张图重复收编应跳过"

    mf = _manifest(bench)
    assert len(mf["images"]) == 1, "不得因重复收编产生重复条目"


# --------------------------------------------------------------------------- #
# 3 · 契约：`abs_src` 决定哈希来源（「先判定后落盘」的前提）
# --------------------------------------------------------------------------- #
def test_build_entry_hashes_abs_src_not_destination(bench: Path):
    """`abs_src` 必须决定哈希/尺寸的读取来源，`rel_path` 只决定 filename/path。

    没有这条，`--copy` 就只能靠「先复制再算哈希」，也就必然把覆盖写排在判定之前。
    """
    src = bench / "src.png"
    _png(src, (200, 100, 50))

    e = cra._build_entry("images/whatever.png", "other", "unknown", "local",
                         abs_src=str(src))
    assert e["sha256"] == _sha(src), "哈希必须取自源文件"
    assert e["filename"] == "whatever.png", "filename 仍由 rel_path 决定"
    assert e["path"] == "images/whatever.png"


# --------------------------------------------------------------------------- #
# 4 · P1 守卫：跨盘符不得崩溃，退化为绝对路径
# --------------------------------------------------------------------------- #
def test_add_cross_drive_falls_back_to_absolute_path(bench: Path, monkeypatch, capsys):
    """跨盘符时 `os.path.relpath` 抛 ValueError；旧实现让它直接 traceback 崩掉。

    用 monkeypatch 强制 relpath 抛错来**确定性**覆盖这个分支，
    不依赖测试机是否真的有多个盘符。
    """
    src = bench / "cross.png"
    _png(src, (1, 2, 3))

    def _boom(path, start):                                   # noqa: ARG001
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", _boom)

    assert cra.cmd_add(_args(src, copy=False)) == 0, "跨盘符不得崩溃"
    err = capsys.readouterr().err
    assert "盘符" in err, "必须显式告警说明 path 记成了绝对路径"

    mf = _manifest(bench)
    assert len(mf["images"]) == 1
    assert os.path.isabs(mf["images"][0]["path"]), "跨盘符应退化为绝对路径"
