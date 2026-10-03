"""Stage 158 探针：量化 collect_real_assets 未测子命令的静默损坏/契约风险。
仅做只读探测 + 隔离 tmp，不改源码。
"""
import argparse
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path("D:/work 111/2026-08-03-17-38-51")
sys.path.insert(0, str(ROOT))
import scripts.collect_real_assets as cra  # noqa: E402


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _setup(tmp: Path):
    bdir = tmp / "benchmark_real"
    (bdir / "images").mkdir(parents=True)
    cra.BENCH_DIR = str(bdir)
    cra.IMAGES_DIR = str(bdir / "images")
    cra.MANIFEST_PATH = str(bdir / "manifest.json")
    return bdir


def test_download_desync():
    """下载覆盖已登记的不同内容文件后，manifest 与磁盘是否静默失同步 + 退出码误报成功。"""
    tmp = Path(tempfile.mkdtemp())
    bdir = _setup(tmp)
    C1 = b"\x89PNG\r\n\x1a\n" + b"C1-content-AAAA"
    C2 = b"\x89PNG\r\n\x1a\n" + b"C2-different-content-BBBB"
    (bdir / "images" / "existing.png").write_bytes(C1)
    S1 = _sha(C1)
    S2 = _sha(C2)
    (bdir / "manifest.json").write_text(json.dumps({
        "version": 1,
        "images": [{
            "id": "mj_001", "filename": "existing.png",
            "path": "images/existing.png", "ai_source": "midjourney",
            "watermark_style_hint": "corner_logo",
            "original_size": [64, 64], "sha256": S1,
            "added_at": "2026-09-13T00:00:00Z", "source": "local",
        }]
    }), encoding="utf-8")
    print(f"[probe] S1={S1[:8]} S2={S2[:8]}")

    # 让 urlopen 返回 C2（与目标路径不同内容）
    fake_resp = io.BytesIO(C2)

    def fake_urlopen(req, timeout=10):
        return io.BytesIO(C2)

    with mock.patch.object(cra.urllib.request, "urlopen", fake_urlopen):
        rc = cra.cmd_download(argparse.Namespace(
            url="https://example.com/existing.png", output="existing.png",
            ai_source="dalle", style="grid_watermark", timeout=10.0))

    disk = (bdir / "images" / "existing.png").read_bytes()
    mf = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    print(f"[probe] download rc={rc}")
    print(f"[probe] 磁盘文件 sha={_sha(disk)[:8]} (期望保持 S1={S1[:8]})")
    print(f"[probe] manifest sha={mf['images'][0]['sha256'][:8]} (期望 S1)")
    print(f"[probe] 磁盘被覆盖? {_sha(disk) == S2}  退出码误报成功? {rc == 0}")
    print(">>> 判定：若 磁盘被覆盖=True 且 退出码误报成功=True ⇒ P1 静默数据损坏（manifest/磁盘失同步）")
    print()


def test_download_failure_no_stale_file():
    tmp = Path(tempfile.mkdtemp())
    bdir = _setup(tmp)

    def boom(req, timeout=10):
        raise urllib_error_URLError("boom")  # noqa

    # 用真实异常类
    import urllib.error as _ue
    def boom2(req, timeout=10):
        raise _ue.URLError("boom")

    with mock.patch.object(cra.urllib.request, "urlopen", boom2):
        rc = cra.cmd_download(argparse.Namespace(
            url="https://example.com/foo.png", output="foo.png",
            ai_source="other", style="unknown", timeout=10.0))
    leftover = (bdir / "images" / "foo.png").exists()
    print(f"[probe] 下载失败 rc={rc}  残留空文件? {leftover}")
    print(">>> 判定：rc 应=2 且无残留文件（by design 正确）")
    print()


def test_scan_dedup_and_reject():
    tmp = Path(tempfile.mkdtemp())
    bdir = _setup(tmp)
    # 两张内容相同、文件名不同的图
    C = b"\x89PNG\r\n\x1a\n" + b"same-bytes-for-dedup"
    (bdir / "images" / "a.png").write_bytes(C)
    (bdir / "images" / "b.png").write_bytes(C)
    rc = cra.cmd_scan(argparse.Namespace(ai_source="other", style="unknown"))
    mf = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    print(f"[probe] scan rc={rc} 条目数={len(mf['images'])} (期望 1：b.png 因同 sha 被跳过)")
    print(">>> 判定：dedup-by-sha 是否正确（by design）")
    print()


def test_upsert_status_semantics():
    """当前 _upsert 返回 bool；记录三种场景的返回，以决定是否需要 richer status。"""
    m = {"version": 1, "images": []}
    e1 = {"sha256": "AAA", "path": "images/x.png", "filename": "x.png"}
    r1 = cra._upsert(m, e1)           # accept
    e2 = {"sha256": "AAA", "path": "images/y.png", "filename": "y.png"}
    r2 = cra._upsert(m, e2)           # skip (same sha)
    e3 = {"sha256": "BBB", "path": "images/x.png", "filename": "x.png"}
    r3 = cra._upsert(m, e3)           # reject (same path, diff sha)
    print(f"[probe] accept={r1} skip={r2} reject={r3}  (当前均为 bool；skip/reject 无法区分)")
    print(">>> 判定：下载修复若需区分 skip(幂等) vs reject(冲突)，需 richer status")
    print()


def test_infer_id():
    print("[probe] _infer_id_from_filename:")
    for name in ["Foo Bar.png", "foo-bar.png", "MyImage!!.JPG", "a" * 40 + ".png", ""]:
        cand = cra._infer_id_from_filename(name, set())
        print(f"   {name!r:30} -> {cand!r}")
    # 冲突
    ids = {"foo_bar"}
    print("   冲突 foo bar.png ->", cra._infer_id_from_filename("foo bar.png", ids))
    print()


if __name__ == "__main__":
    import urllib.error as urllib_error_URLError  # for closure name
    test_download_desync()
    test_download_failure_no_stale_file()
    test_scan_dedup_and_reject()
    test_upsert_status_semantics()
    test_infer_id()
    print("PROBE DONE")
