"""
方向 A · 真实 AI 图采集入口
============================

职责
----
1. 把真实带水印 AI 图登记到 `benchmark_real/manifest.json`（含元数据）
2. 不复制图——只记录路径与哈希，由调用方提供 images/ 目录

CLI 用法
--------
    # 扫描 images/ 下所有图片，自动登记（默认 ai_source=other）
    python scripts/collect_real_assets.py scan

    # 手工添加单张图
    python scripts/collect_real_assets.py add images/foo.png \\
        --ai-source midjourney --style corner_logo

    # 公网下载（依赖 urllib，无需第三方；失败时不中断）
    python scripts/collect_real_assets.py download --url https://example.com/foo.png \\
        --ai-source dalle --style grid_watermark

约束
----
- 不修改 images/ 已有文件；
- manifest.json 是**合并语义**（相同 id 不覆盖手工编辑的元数据）；
- 公网下载尽量短超时（默认 10s），失败给出原因但不抛错；
- 校验：路径存在 / 哈希一致 / 必填字段不缺。

manifest.json schema
--------------------
    {
      "version": 1,
      "images": [
        {
          "id": "mj_001",                # 自动从文件名推断；冲突时追加 _N
          "filename": "foo.png",
          "path": "images/foo.png",       # 相对 benchmark_real/
          "ai_source": "midjourney",      # 枚举见下
          "watermark_style_hint": "corner_logo",  # 提示值，真值以 ground_truth 为准
          "original_size": [W, H],
          "sha256": "...",
          "added_at": "2026-09-13T14:30:00Z",
          "source": "local"               # local / url
        }
      ]
    }

ai_source 枚举：midjourney / stable_diffusion / dalle / firefly / lepton / v0 / other
watermark_style_hint 枚举：corner_logo / edge_text / grid_watermark / logo_watermark /
                        gradient_watermark / translucent / overlay / mixed / unknown
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 允许测试夹具用 WME_BENCH_DIR 指向临时目录（默认仍为项目内 benchmark_real/）。
BENCH_DIR = os.environ.get("WME_BENCH_DIR") or os.path.join(_ROOT, "benchmark_real")
IMAGES_DIR = os.path.join(BENCH_DIR, "images")
MANIFEST_PATH = os.path.join(BENCH_DIR, "manifest.json")

AI_SOURCES = {
    "midjourney", "stable_diffusion", "dalle", "firefly",
    "lepton", "v0", "other",
}
STYLE_HINTS = {
    "corner_logo", "edge_text", "grid_watermark", "logo_watermark",
    "gradient_watermark", "translucent", "overlay", "mixed", "unknown",
}
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


# -----------------------------------------------------------------------------#
# 工具函数
# -----------------------------------------------------------------------------#
def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _img_size(path: str) -> Optional[List[int]]:
    """读图尺寸（仅元数据，不解码像素；用 PIL）。失败返回 None。"""
    try:
        from PIL import Image  # 局部依赖：collect 步骤可能不是每次都需要 PIL
        with Image.open(path) as im:
            return [int(im.width), int(im.height)]
    except Exception:
        return None


def _infer_id_from_filename(name: str, existing_ids: set) -> str:
    """从文件名推断 id：去扩展名 → 仅保留 [a-z0-9_] → 前 24 字符 → 冲突追加 _N。"""
    base = os.path.splitext(os.path.basename(name))[0].lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_") or "img"
    base = base[:24]
    cand = base
    n = 1
    while cand in existing_ids:
        n += 1
        cand = f"{base}_{n}"
    return cand


def _load_manifest() -> Dict[str, Any]:
    if not os.path.exists(MANIFEST_PATH):
        return {"version": 1, "images": []}
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "images" not in data:
            raise ValueError("manifest 结构异常")
        return data
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[warn] manifest.json 损坏或结构异常：{e}。将以空清单重启。", file=sys.stderr)
        return {"version": 1, "images": []}


def _save_manifest(manifest: Dict[str, Any]) -> None:
    os.makedirs(BENCH_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -----------------------------------------------------------------------------#
# 子命令实现
# -----------------------------------------------------------------------------#
def _build_entry(rel_path: str, ai_source: str, style: str, source: str,
                 abs_src: Optional[str] = None) -> Dict[str, Any]:
    """构造一条清单条目。

    `abs_src`：可选，**实际读取（算 sha256 / 取尺寸）的文件**。
    旧实现固定读 `BENCH_DIR/rel_path`，于是 `--copy` 必须**先复制**才能算哈希，
    把「覆盖写」排在了守卫判定之前——守卫随后拒绝登记，数据集文件却已被静默换掉
    （manifest 的 sha256 从此与磁盘文件不一致）。给了 `abs_src` 后即可
    **先判定、通过后再落盘**（阶段 152 修复）。
    """
    abs_path = abs_src or os.path.join(BENCH_DIR, rel_path)
    return {
        "id": "",  # 占位，由 _upsert 填
        "filename": os.path.basename(rel_path),
        "path": rel_path,
        "ai_source": ai_source,
        "watermark_style_hint": style,
        "original_size": _img_size(abs_path),
        "sha256": _sha256_of(abs_path),
        "added_at": _now_iso(),
        "source": source,
    }


def _upsert(manifest: Dict[str, Any], entry: Dict[str, Any]) -> str:
    """合并语义：同 sha256 视为同一张图，跳过；同 path 但 sha256 变 → 拒绝。

    返回状态字符串（阶段 158 由 bool 升级，使调用方能区分「已存在跳过」与「冲突拒绝」）：
      "accepted"  — 新登记，已 append；
      "skipped"   — 哈希已存在（同图），未改动；
      "rejected"  — 路径已存在但哈希变化，未改动（拒绝登记）。
    """
    images: List[Dict[str, Any]] = manifest["images"]
    by_sha = {im.get("sha256"): im for im in images if im.get("sha256")}
    by_path = {im.get("path"): im for im in images if im.get("path")}

    if entry["sha256"] in by_sha:
        print(f"[skip] 哈希已存在：{entry['path']}（与 {by_sha[entry['sha256']]['path']} 同图）")
        return "skipped"
    if entry["path"] in by_path:
        prev = by_path[entry["path"]]
        if prev.get("sha256") and prev["sha256"] != entry["sha256"]:
            print(f"[error] 路径已存在但哈希变化：{entry['path']}（文件被覆盖？）拒绝登记。", file=sys.stderr)
            return "rejected"

    existing_ids = {im.get("id") for im in images if im.get("id")}
    entry["id"] = _infer_id_from_filename(entry["filename"], existing_ids)
    images.append(entry)
    return "accepted"


def cmd_scan(args: argparse.Namespace) -> int:
    if not os.path.isdir(IMAGES_DIR):
        print(f"[error] 目录不存在：{IMAGES_DIR}（先创建并放入真实图）", file=sys.stderr)
        return 1
    manifest = _load_manifest()
    added = 0
    for fn in sorted(os.listdir(IMAGES_DIR)):
        if not fn.lower().endswith(IMG_EXTS):
            continue
        rel = f"images/{fn}"
        entry = _build_entry(rel, ai_source=args.ai_source, style=args.style, source="local")
        if _upsert(manifest, entry) == "accepted":
            print(f"[add] {entry['id']}  {rel}  sha256={entry['sha256'][:8]}")
            added += 1
    _save_manifest(manifest)
    print(f"\n扫描完成。新增 {added} 条；manifest 总计 {len(manifest['images'])} 条。")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    src = args.path
    if not os.path.isabs(src):
        src = os.path.join(_ROOT, src)
    if not os.path.isfile(src):
        print(f"[error] 文件不存在：{src}", file=sys.stderr)
        return 1
    manifest = _load_manifest()
    if args.copy:
        # 阶段 152：**先判定、后落盘**。
        # 旧实现先 shutil.copy2 覆盖 images/ 下的同名文件、再让 _upsert 判定；
        # 于是一条「路径已存在但哈希变化 ⇒ 拒绝登记」的守卫，会在**拒绝的同时**
        # 把数据集里那张图悄悄换掉——提示说没登记，磁盘却已经变了。
        # 现在哈希/尺寸取自源文件，只有 _upsert 接受了才真正复制（阶段 158：接受判定改用状态字符串）。
        rel = f"images/{os.path.basename(src)}"
        entry = _build_entry(rel, ai_source=args.ai_source, style=args.style,
                             source="local", abs_src=src)
        entry["path"] = rel
        if _upsert(manifest, entry) == "accepted":
            os.makedirs(IMAGES_DIR, exist_ok=True)
            shutil.copy2(src, os.path.join(IMAGES_DIR, os.path.basename(src)))
            _save_manifest(manifest)
            print(f"[add] {entry['id']}  {entry['path']}")
        return 0
    # 非 copy：允许引用 images/ 外的路径（不复制，只登记）
    entry = _build_entry(src, ai_source=args.ai_source, style=args.style, source="local")
    try:
        entry["path"] = os.path.relpath(src, BENCH_DIR)
    except ValueError:
        # Windows 跨盘符无法计算相对路径（"path is on mount 'D:', start on mount 'C:'"）。
        # 旧实现让它直接抛 traceback；退化成绝对路径并提示即可（阶段 152 修复）。
        print(f"[warn] 源与数据集不在同一盘符，path 记为绝对路径：{src}", file=sys.stderr)
        entry["path"] = src
    if _upsert(manifest, entry) == "accepted":
        _save_manifest(manifest)
        print(f"[add] {entry['id']}  {entry['path']}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    url = args.url
    if not url.lower().startswith(("http://", "https://")):
        print(f"[error] 仅支持 http(s) URL：{url}", file=sys.stderr)
        return 1
    # 推断文件名
    fn = args.output or os.path.basename(url.split("?")[0]) or "downloaded.png"
    if not fn.lower().endswith(IMG_EXTS):
        fn += ".png"
    rel = f"images/{fn}"
    # 阶段 158：**先判定、后落盘**（与 cmd_add --copy 同构，lesson 189/190）。
    # 旧实现直接 urllib 下载覆盖 images/<fn>，再算 sha 交给 _upsert；
    # 若目标路径已被「不同内容」的已登记图占用，会先把磁盘文件覆盖成新内容、
    # 再被 _upsert 拒绝（sha 不一致），于是「manifest 的 sha256 与磁盘文件静默失同步 +
    # 退出码仍为 0（成功）」—— 与阶段 152 的同一种缺陷（打印契约 ≠ 强制契约）。
    # 现改为先下到临时文件 → 算 sha256 → 让 _upsert 判定 → 接受后才 move 进 images/。
    manifest = _load_manifest()
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(fn)[1])
        tmp_path = tmp.name
        tmp.close()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=args.timeout) as r:
            with open(tmp_path, "wb") as f:
                f.write(r.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f"[error] 下载失败：{e}", file=sys.stderr)
        return 2
    entry = _build_entry(rel, ai_source=args.ai_source, style=args.style,
                         source="url", abs_src=tmp_path)
    status = _upsert(manifest, entry)
    if status == "accepted":
        os.makedirs(IMAGES_DIR, exist_ok=True)
        shutil.move(tmp_path, os.path.join(IMAGES_DIR, fn))
        _save_manifest(manifest)
        print(f"[add] {entry['id']}  {rel}  ← {url}")
        return 0
    # 未接受：绝不覆盖磁盘上已登记的图。
    if tmp_path and os.path.exists(tmp_path):
        os.remove(tmp_path)
    # skipped=同图已存在（幂等，视为成功）；rejected=路径冲突（视为失败，非 0）。
    return 0 if status == "skipped" else 3


def cmd_list(args: argparse.Namespace) -> int:
    manifest = _load_manifest()
    if not manifest["images"]:
        print("(manifest 为空)")
        return 0
    print(f"{'id':<24} {'source':<16} {'style_hint':<20} {'size':<12} {'sha256':<10} path")
    print("-" * 100)
    for im in manifest["images"]:
        size = im.get("original_size") or [-1, -1]
        size_s = f"{size[0]}x{size[1]}" if size and size[0] > 0 else "?"
        sha = (im.get("sha256") or "")[:8]
        print(f"{im.get('id', '?'):<24} {im.get('ai_source', '?'):<16} "
              f"{im.get('watermark_style_hint', '?'):<20} {size_s:<12} {sha:<10} {im.get('path', '?')}")
    return 0


# -----------------------------------------------------------------------------#
# 入口
# -----------------------------------------------------------------------------#
def main() -> int:
    ap = argparse.ArgumentParser(description="真实 AI 图采集入口")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="扫描 images/ 自动登记")
    p_scan.add_argument("--ai-source", default="other", choices=sorted(AI_SOURCES))
    p_scan.add_argument("--style", default="unknown", choices=sorted(STYLE_HINTS))
    p_scan.set_defaults(func=cmd_scan)

    p_add = sub.add_parser("add", help="手工添加单张图")
    p_add.add_argument("path", help="文件路径（绝对或相对项目根）")
    p_add.add_argument("--ai-source", default="other", choices=sorted(AI_SOURCES))
    p_add.add_argument("--style", default="unknown", choices=sorted(STYLE_HINTS))
    p_add.add_argument("--copy", action="store_true", help="复制到 images/ 而非引用原路径")
    p_add.set_defaults(func=cmd_add)

    p_dl = sub.add_parser("download", help="公网下载（urllib；不依赖第三方）")
    p_dl.add_argument("--url", required=True)
    p_dl.add_argument("--output", help="保存文件名（默认从 URL 推断）")
    p_dl.add_argument("--ai-source", default="other", choices=sorted(AI_SOURCES))
    p_dl.add_argument("--style", default="unknown", choices=sorted(STYLE_HINTS))
    p_dl.add_argument("--timeout", type=float, default=10.0, help="秒")
    p_dl.set_defaults(func=cmd_download)

    p_list = sub.add_parser("list", help="列出已登记的图")
    p_list.set_defaults(func=cmd_list)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())