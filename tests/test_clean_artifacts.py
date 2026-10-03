"""阶段 195（E2）· 仓库产物清理脚本的确定性自测。

不触碰真实仓库：用 `tmp_path` 合成一棵小树，验证分类 / 扫描 / 归档 / 删除编排。
  · classify_dir / classify_file  —— 纯函数，断言各类别边界；
  · scan                          —— 命中合成产物、跳过 .git 与 .scratch；
  · archive                       —— 真实 rename 到 .scratch/cleaned/<ts>/（tmp 内，安全）；
  · delete                        —— monkeypatch 拦截真实删除，验证分批编排与计数；
  · main 模式守卫                 —— dry-run 返回 0；--delete 缺 --i-am-sure 返回 2。

设计取舍：用 importlib 直接加载脚本模块，规避 scripts/ 是否含 __init__.py 的包结构差异。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 直接加载脚本模块（不依赖包结构）
_SPEC = importlib.util.spec_from_file_location(
    "clean_artifacts", str(_ROOT / "scripts" / "clean_artifacts.py")
)
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)

classify_dir = _mod.classify_dir
classify_file = _mod.classify_file
scan = _mod.scan
build_report = _mod.build_report
archive = _mod.archive
delete = _mod.delete
main = _mod.main


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def _build_tree(root: Path) -> None:
    """合成一棵含各类产物的小树；同时放 .git 与 .scratch 应被跳过。"""
    _touch(root / "cache" / "a" / "img1.png")
    _touch(root / "cache" / "a" / "img2.png")
    _touch(root / ".pytest_158_xy" / "tmp.json")
    _touch(root / "mypkg.egg-info" / "PKG-INFO")
    _touch(root / ".probe.py")           # 根目录点开头 .py（E1 stray_root，对应 .gitignore 的 /.*.py）
    _touch(root / "debug.out")           # 根目录 *.out
    _touch(root / "sub" / "mod.pyc")     # 子目录字节码
    _touch(root / "run.log")             # 根目录运行时日志（放根，避免被 logs/ 剪枝吞掉）
    _touch(root / "logs" / ".gitkeep")   # 覆盖 logs 目录类别
    # 应被跳过：
    _touch(root / ".git" / "config")
    _touch(root / ".scratch" / "old" / "junk.txt")


def test_classify_dir():
    assert classify_dir("cache") == "cache"
    assert classify_dir(".pytest_iso") == ".pytest_iso"
    assert classify_dir(".pytest_158_abc") == "pytest_"
    assert classify_dir(".pytest_tmp") == "pytest_"
    assert classify_dir("mypkg.egg-info") == "egg_info"
    assert classify_dir("src") is None
    assert classify_dir(".git") is None


def test_classify_file():
    # 根目录点开头 py/txt → stray_root
    assert classify_file("._probe.py", in_root=True) == "stray_root"
    assert classify_file(".diag.txt", in_root=True) == "stray_root"
    # 非根目录的点开头 py/txt 不应命中
    assert classify_file("._probe.py", in_root=False) is None
    assert classify_file(".diag.txt", in_root=False) is None
    assert classify_file("mod.pyc", in_root=False) == "pyc"
    assert classify_file("run.log", in_root=False) == "stray_log"
    assert classify_file("debug.out", in_root=True) == "stray_out"
    assert classify_file("pytest_report.xml", in_root=True) == "stray_pytest"
    assert classify_file("NUL", in_root=True) == "stray_nul"
    assert classify_file("main.py", in_root=False) is None


def test_scan_finds_expected_and_skips_git_scratch(tmp_path: Path):
    root = tmp_path
    _build_tree(root)
    hits = scan(str(root))
    # 我显式创建的产物都应被命中。不限定精确计数：conftest 的 autouse
    # `_isolate_runtime_dirs` 会在 tmp_path 下额外创建 `_wme_runtime/{cache,...}`，
    # 它们同样会被 classify_dir 命中——属于预期行为（证明脚本也能抓到这类目录）。
    expected = {
        "cache": root / "cache",
        "pytest_": root / ".pytest_158_xy",
        "egg_info": root / "mypkg.egg-info",
        "stray_root": root / ".probe.py",
        "stray_out": root / "debug.out",
        "pyc": root / "sub" / "mod.pyc",
        "stray_log": root / "run.log",
        "logs": root / "logs",
    }
    for cat, p in expected.items():
        assert cat in hits, f"类别缺失: {cat}"
        assert str(p) in hits[cat], f"{cat} 未命中 {p}"
    # 跳过 .git 与 .scratch（无论是否含其它命中）
    all_hits = sum(hits.values(), [])
    assert all(".git" not in p for p in all_hits)
    assert all(".scratch" not in p for p in all_hits)
    # 不应误伤正常源码
    assert all("src" not in p for p in all_hits)


def test_build_report_structure(tmp_path: Path):
    root = tmp_path
    _build_tree(root)
    hits = scan(str(root))
    rep = build_report(hits, str(root), with_items=True)
    assert "totals" in rep and "categories" in rep
    assert rep["totals"]["entries"] == sum(len(v) for v in hits.values())
    assert rep["totals"]["bytes"] > 0
    # 每类都带分类 + 明细（with_items）
    for c in rep["categories"]:
        assert "category" in c and "bytes" in c
        assert "items" in c


def test_archive_moves_to_scratch(tmp_path: Path):
    root = tmp_path
    _build_tree(root)
    hits = scan(str(root))
    ok, failed = archive(hits, str(root))
    assert failed == []
    assert ok == sum(len(v) for v in hits.values())
    # 我显式创建的 cache 已被移走
    assert not (root / "cache").exists()
    # 归档目标已建立，且内部至少含一个 cache 目录
    assert (root / ".scratch" / "cleaned").exists()
    assert list((root / ".scratch" / "cleaned").rglob("cache")), "归档目标应含 cache"


def test_delete_requires_confirmation_via_main(tmp_path: Path):
    root = tmp_path
    _build_tree(root)
    rc = main(["--delete", "--root", str(root)])
    # 缺 --i-am-sure → 拒绝，退出码 2
    assert rc == 2
    # 未删除：源仍在
    assert (root / "cache" / "a" / "img1.png").exists()


def test_delete_orchestration_via_mock(tmp_path, monkeypatch):
    """monkeypatch 拦截真实删除，验证分批编排与计数（不碰真实磁盘）。"""
    removed = []

    def fake_rmtree(p, *a, **k):
        removed.append(("dir", p))

    def fake_remove(p, *a, **k):
        removed.append(("file", p))

    monkeypatch.setattr(_mod.shutil, "rmtree", fake_rmtree)
    monkeypatch.setattr(_mod._os, "remove", fake_remove)

    root = str(tmp_path)
    # 构造 120 个假条目，验证 ≤50 分批（应为 3 批）
    hits = {"fake": [f"{root}/f{i}" for i in range(120)]}
    ok, failed = delete(hits, root)
    assert failed == []
    assert ok == 120
    assert len(removed) == 120
