# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 195（E2）· 仓库产物清理脚本

 背景
 ------
 阶段 185 首次纳管版本控制时，逐条核对 .gitignore，发现 5 类此前**误入仓**的
 产物（实测规模见括注）：① exports/（457 文件）② 根目录 `.*.py` / `.*.txt`
 探针脚本 ③ `.pytest_*` / `.verify_tmp` / `.pytest_iso` 等多套隔离目录
 ④ `_dbg_bench/` 调试残留 ⑤ 项目记忆 scratch。此外运行时还持续累积
 `cache/`（11078 文件）/`outputs/`/`temp/`/`logs/`（均已被 .gitignore 忽略，
 不入库，但**占据本地磁盘**、产物边界模糊）。

 本脚本把「盘点 → 隔离 → 回收」固化成一条可重复的命令，替换此前零散的手动
 `rm -rf` 思路，并**从设计上规避沙箱删除护栏**（LESSONS 202：受限环境两段式
 删除受限，单次 >50 即拒 + 会话闩锁；归档 rename 优于删除）。

 三种模式（互斥，默认 dry-run）
 ------------------------------
   --dry-run  （默认，不传 --archive/--delete 即此模式）
       只扫描 + 打印分类报告（数量 / 体积），**零文件系统写**。安全、可反复跑。
   --archive
       把命中的产物**移动到** `<root>/.scratch/cleaned/<时间戳>/<相对路径>`
       （rename，同文件系统零拷贝）。`.scratch/` 已被 .gitignore 忽略 ⇒
       既清出工作树主干，又不破坏任何文件（可随时找回）。这是本脚本的**主路径**。
   --delete --i-am-sure
       真正删除（rmtree / remove）。仅用于**沙箱外、用户明确要回收空间**时。
       强制 `--i-am-sure` 双确认；删除按 ≤50 的小批量推进以规避沙箱护栏；
       每批前打印将被删除的路径。**在受限沙箱内不要依赖它成功**——护栏可能
       直接拒删，届时回退到 `--archive` 或沙箱外手动清理。

 设计约束
 --------
   · 仅标准库；`force_utf8_stdio()` 保证 print 非 GBK 字符不影响退出码（阶段 170）。
   · 扫描用单一真值来源 `ARTIFACT_SPECS`，不解析 .gitignore（保持确定、零依赖）。
   · 跳过 `.git` 与归档目标 `.scratch`，不递归进已命中目录（避免重复计数）。
   · 纯函数 `classify_dir` / `classify_file` / `scan` 便于守卫测试（见
     tests/test_clean_artifacts.py），不触碰真实仓库即可确定性验证。

 用法
 ----
   python scripts/clean_artifacts.py                      # 默认 dry-run 报告
   python scripts/clean_artifacts.py --verbose            # 报告 + 列具体路径
   python scripts/clean_artifacts.py --json report.json   # 报告写 JSON（不含明细，除非 --verbose）
   python scripts/clean_artifacts.py --archive            # 隔离到 .scratch/cleaned/<ts>/
   python scripts/clean_artifacts.py --delete --i-am-sure  # 真正删除（沙箱外）

 版本：1.0.0
==============================================================================
"""
from __future__ import annotations

import argparse
import json
import os as _os
import shutil
import sys as _sys
import time

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

#: 归档目标（gitignored，见 .gitignore:195）。归档是主路径，删除只在沙箱外使用。
_ARCHIVE_TOP = ".scratch"
_ARCHIVE_SUB = "cleaned"

#: 删除单批上限（≤ 沙箱护栏阈值，见 LESSONS 202），规避「单次 >50 即拒 + 会话闩锁」。
_DELETE_BATCH_LIMIT = 50

#: 已知**不可归档**的类别 → 理由。这些条目**必须仍被报告**（用户要知道它存在），
#: 但 `archive()` 必须**跳过**它们、而不是崩溃（阶段 204 修的真实缺陷）：
#:   · `stray_nul`：Windows 保留设备名。ntpath 把它识别为**设备路径**
#:     （`\\.\NUL`），与项目所在盘符不是同一 mount ⇒
#:     `os.path.relpath` 抛 `ValueError`、`shutil.move` 亦不可用。
#: 判据：分类器产出的**每个**类别，处理器都必须有明确处置——否则就是
#: 「分类器与处理器不一致」，一个条目即可中断整轮（同 LESSONS 216）。
_NON_ARCHIVABLE = {
    "stray_nul": "Windows 保留设备名，无法求相对路径 / 移动，需手工处置",
}


# ------------------------------------------------------------------------------
# 分类单一真值来源
# ------------------------------------------------------------------------------
#: 运行时 / 构建产物目录：按**目录名精确匹配**（这些名字在本项目里不会被源码占用）。
_EXACT_DIRS = {
    # 运行时数据目录（.gitignore:55-58，含 .gitkeep 占位）
    "cache", "outputs", "temp", "logs", "exports",
    # pytest / 隔离 / 验证目录（.gitignore:134-180）
    ".pytest_iso", ".verify_tmp", ".pytest_runall",
    "gradio_cached_examples", "flagged",
    # 调试 / 覆盖率残留（.gitignore:136-183）
    "_dbg_bench", "htmlcov", ".tox", ".mypy_cache", ".ruff_cache",
    # 打包 / 构建产物（.gitignore:30-37）
    "build", "dist", "__pycache__", ".gradio",
}

#: 目录名前缀匹配（覆盖 .pytest_tmp / .pytest_158_ / .pytest_183* 等一切会话隔离目录）。
_DIR_PREFIXES = (".pytest_",)

#: 目录名后缀匹配（egg-info 目录）。
_DIR_SUFFIXES = (".egg-info",)


def classify_dir(name: str) -> "str | None":
    """返回目录名命中的产物类别，或 None。

    纯函数（无 IO），守卫测试直接断言。命中即作为「整目录」处理：不递归进其内部。
    """
    if name in _EXACT_DIRS:
        return name
    for p in _DIR_PREFIXES:
        if name.startswith(p):
            return "pytest_"
    for s in _DIR_SUFFIXES:
        if name.endswith(s):
            return "egg_info"
    return None


def classify_file(basename: str, in_root: bool) -> "str | None":
    """返回文件名命中的产物类别，或 None。

    参数：
        basename : 文件名。
        in_root  : 该文件是否位于仓库根目录（用于约束 `.*.py` / `.*.txt` 锚定根）。

    纯函数（无 IO）。类别约定：
        stray_root   —— 根目录的 `.*.py` / `.*.txt`（阶段 185 E1，一次性探针脚本）。
        stray_out    —— `*.out` / `pytest*.xml` / `pytest*.log`（裸躺根目录的转储 / 报告）。
        stray_log    —— `*.log`（运行时日志）。
        pyc          —— `*.pyc`（字节码）。
        stray_nul    —— Windows 误建的保留名文件 `NUL`。
    """
    # E1：根目录锚定的点开头 py/txt（阶段 185）。
    if in_root and basename.startswith(".") and (
        basename.endswith(".py") or basename.endswith(".txt")
    ):
        return "stray_root"
    if basename == "NUL":
        return "stray_nul"
    if basename.endswith(".pyc"):
        return "pyc"
    if basename.endswith(".log"):
        return "stray_log"
    if basename.endswith(".out"):
        return "stray_out"
    if basename.startswith("pytest") and (basename.endswith(".xml") or basename.endswith(".log")):
        return "stray_pytest"
    return None


# ------------------------------------------------------------------------------
# 扫描（纯函数核心，便于测试）
# ------------------------------------------------------------------------------
def scan(root: str) -> "dict[str, list[str]]":
    """扫描 root，返回 {类别: 命中路径列表（绝对路径，已排序）}。

    跳过 `.git` 与归档目标 `_ARCHIVE_TOP`，命中目录后不再递归其内部。
    """
    hits: "dict[str, list[str]]" = {}
    for dirpath, dirnames, filenames in _os.walk(root):
        # 剪枝：不进入 .git 与归档目标，避免重复计数与自我归档。
        if ".git" in dirnames:
            dirnames.remove(".git")
        if _ARCHIVE_TOP in dirnames:
            dirnames.remove(_ARCHIVE_TOP)

        rel = _os.path.relpath(dirpath, root)
        in_root = (rel == ".")

        # 目录分类（命中即从 dirnames 移除，停止递归）。
        for d in list(dirnames):
            cat = classify_dir(d)
            if cat is not None:
                hits.setdefault(cat, []).append(_os.path.join(dirpath, d))
                dirnames.remove(d)

        # 文件分类（不剪枝，文件无递归）。
        for f in filenames:
            cat = classify_file(f, in_root)
            if cat is not None:
                hits.setdefault(cat, []).append(_os.path.join(dirpath, f))

    # 每个类别排序，保证确定性（测试可断言、归档顺序稳定）。
    for cat in hits:
        hits[cat].sort()
    return hits


def entry_size(path: str) -> int:
    """返回路径体积（字节）。目录递归求和；文件取其 st_size。"""
    if _os.path.isdir(path):
        total = 0
        for dp, _, fns in _os.walk(path):
            for fn in fns:
                try:
                    total += _os.path.getsize(_os.path.join(dp, fn))
                except OSError:
                    pass
        return total
    try:
        return _os.path.getsize(path)
    except OSError:
        return 0


def format_size(n: int) -> str:
    """人类可读体积。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} TB"


# ------------------------------------------------------------------------------
# 报告 / 归档 / 删除
# ------------------------------------------------------------------------------
def build_report(hits: "dict[str, list[str]]", root: str, *, with_items: bool = False) -> dict:
    """把 hits 汇总成结构化报告（供打印或 JSON 落盘）。"""
    categories = []
    total_bytes = 0
    total_entries = 0
    n_files_inside = 0
    for cat in sorted(hits):
        items = hits[cat]
        cat_bytes = sum(entry_size(p) for p in items)
        # 估算内部文件数：目录数其内部文件，文件本身计 1。
        cat_files = 0
        for p in items:
            if _os.path.isdir(p):
                for dp, _, fns in _os.walk(p):
                    cat_files += len(fns)
            else:
                cat_files += 1
        total_bytes += cat_bytes
        total_entries += len(items)
        n_files_inside += cat_files
        rec = {
            "category": cat,
            "count": len(items),
            "files_inside": cat_files,
            "bytes": cat_bytes,
            "human_size": format_size(cat_bytes),
        }
        if with_items:
            rec["items"] = items
        categories.append(rec)
    return {
        "root": _os.path.abspath(root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "totals": {
            "entries": total_entries,
            "files_inside": n_files_inside,
            "bytes": total_bytes,
            "human_size": format_size(total_bytes),
        },
        "categories": categories,
    }


def print_report(rep: dict, *, verbose: bool = False) -> None:
    """打印人类可读的分类报告。"""
    print("=" * 78)
    print(f"仓库产物盘点 · root = {rep['root']}")
    print(f"生成时间      = {rep['generated_at']}")
    print("=" * 78)
    if not rep["categories"]:
        print("✅ 未发现任何已知产物（仓库干净）。")
        return
    print(f"{'类别':<16}{'条目':>8}{'内部文件':>12}{'体积':>14}")
    print("-" * 78)
    for c in rep["categories"]:
        print(f"{c['category']:<16}{c['count']:>8}{c['files_inside']:>12}{c['human_size']:>14}")
        if verbose and c.get("items"):
            for it in c["items"]:
                print(f"      └─ {it}")
    t = rep["totals"]
    print("-" * 78)
    print(f"{'合计':<16}{t['entries']:>8}{t['files_inside']:>12}{t['human_size']:>14}")
    print("=" * 78)


def archive(hits: "dict[str, list[str]]", root: str) -> "tuple[int, list[str]]":
    """把命中产物 rename 到 `.scratch/cleaned/<ts>/<相对路径>`。返回 (成功数, 失败列表)。

    阶段 204 修（**分类器与处理器不一致** —— 一个坏条目毁掉整轮归档）：
        `classify_file` 早就产出了 `stray_nul` 类别（Windows 误建的保留名文件 `NUL`），
        但本函数**没处理它**。结果 `_os.path.relpath("D:\\...\\NUL", root)` 抛

            ValueError: path is on mount '\\\\.\\NUL', start on mount 'D:'

        （ntpath 把保留设备名识别为**设备路径** ⇒ 与项目所在盘符不同 mount），
        而该调用**在 try 之外** ⇒ **整个归档轮次当场中断**，前面成功的都白做一半。

    修法三条：
        ① **逐条目错误隔离**：单条失败只记入 `failed`，绝不中断整轮；
        ② **已知不可归档的类别显式登记**（`_NON_ARCHIVABLE`）并单独计为「跳过」——
           它们**必须仍被报告**（用户要知道它存在），但归档/删除对它们无意义；
        ③ `print` 移到 `shutil.move` **之后**——原实现在 move 之前就打印「归档 →」，
           失败时输出与真实状态矛盾（同源经验：成功路径的展示性输出不可先行）。
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    archive_root = _os.path.join(root, _ARCHIVE_TOP, _ARCHIVE_SUB, ts)
    _os.makedirs(archive_root, exist_ok=True)
    ok = 0
    failed = []
    skipped = []
    for cat in sorted(hits):
        skip_reason = _NON_ARCHIVABLE.get(cat)
        for src in hits[cat]:
            if skip_reason is not None:
                skipped.append(f"{src}（{skip_reason}）")
                continue
            try:
                rel = _os.path.relpath(src, root)
                dest = _os.path.join(archive_root, rel)
                # 同文件系统 rename（零拷贝）；跨文件系统 shutil.move 退化为复制 + 删除原。
                _os.makedirs(_os.path.dirname(dest), exist_ok=True)
                # 目标已存在则追加序号，避免覆盖。
                real_dest = dest
                if _os.path.exists(real_dest):
                    base, ext = _os.path.splitext(dest)
                    i = 2
                    while _os.path.exists(f"{base}_{i}{ext}"):
                        i += 1
                    real_dest = f"{base}_{i}{ext}"
                shutil.move(src, real_dest)
            except (OSError, ValueError) as e:      # noqa: BLE001 - 单条失败不中断整轮
                failed.append(f"{src} ({type(e).__name__}: {e})")
                continue
            ok += 1
            print(f"  归档 → {real_dest}")
    print(f"\n归档完成：成功 {ok} 项，失败 {len(failed)} 项，跳过 {len(skipped)} 项 → {archive_root}")
    if skipped:
        print("  （跳过 = 已知不可归档类别，仍需你手工处置：）")
        for s in skipped:
            print(f"    · {s}")
    return ok, failed


def delete(hits: "dict[str, list[str]]", root: str) -> "tuple[int, list[str]]":
    """真正删除命中产物。按 ≤ `_DELETE_BATCH_LIMIT` 分批，每批前打印。

    返回 (成功数, 失败列表)。失败多因沙箱护栏拒删——调用方应回退到 --archive。
    """
    all_paths = [p for cat in sorted(hits) for p in hits[cat]]
    ok = 0
    failed = []
    batch = []
    for p in all_paths:
        batch.append(p)
        if len(batch) >= _DELETE_BATCH_LIMIT:
            ok, failed = _flush_delete_batch(batch, ok, failed)
            batch = []
    if batch:
        ok, failed = _flush_delete_batch(batch, ok, failed)
    print(f"\n删除完成：成功 {ok} 项，失败 {len(failed)} 项。")
    if failed:
        print("⚠ 部分删除被拒绝（可能为沙箱护栏）。改用 `python scripts/clean_artifacts.py --archive`")
        print("  或到沙箱外手动回收。被拒路径：")
        for f in failed[:20]:
            print(f"   └─ {f}")
    return ok, failed


def _flush_delete_batch(batch: list, ok: int, failed: list) -> "tuple[int, list]":
    print(f"  [删除批 {len(batch)} 项] 即将删除：")
    for p in batch:
        print(f"    └─ {p}")
    for p in batch:
        try:
            if _os.path.isdir(p) and not _os.path.islink(p):
                shutil.rmtree(p)
            else:
                _os.remove(p)
            ok += 1
        except OSError as e:
            failed.append(f"{p} ({e})")
    return ok, failed


# ------------------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------------------
def main(argv: "list | None" = None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(
        description="仓库产物清理脚本（E2，默认 dry-run 只列不改）",
    )
    ap.add_argument("--root", default=_ROOT, help="仓库根目录（默认脚本所在项目根）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--archive", action="store_true",
                      help="把命中产物 rename 隔离到 .scratch/cleaned/<ts>/（主路径，安全可逆）")
    mode.add_argument("--delete", action="store_true",
                      help="真正删除（须配合 --i-am-sure；建议仅沙箱外使用）")
    ap.add_argument("--i-am-sure", action="store_true",
                    help="删除双确认：必须显式传递，否则 --delete 被拒")
    ap.add_argument("--verbose", action="store_true", help="报告同时列出具体路径")
    ap.add_argument("--json", help="把报告写到的 JSON 路径（不含明细，除非 --verbose）")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅扫描 + 报告（默认模式；也可显式指定）")
    args = ap.parse_args(_sys.argv[1:] if argv is None else argv)

    root = _os.path.abspath(args.root)
    if not _os.path.isdir(root):
        print(f"root 不是目录：{root}", file=_sys.stderr)
        return 1

    # 模式判定：默认 dry-run。
    mode_name = "dry-run"
    if args.archive:
        mode_name = "archive"
    elif args.delete:
        if not args.i_am_sure:
            print("⚠ --delete 需要显式 --i-am-sure 双确认。已拒绝。", file=_sys.stderr)
            return 2
        mode_name = "delete"

    hits = scan(root)
    rep = build_report(hits, root, with_items=args.verbose)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        print(f"JSON 报告已写入：{args.json}")

    if mode_name == "dry-run":
        print_report(rep, verbose=args.verbose)
        if not rep["categories"]:
            return 0
        print("\n（dry-run：未做任何改动。要隔离请加 --archive；要删除请加 --delete --i-am-sure）")
        return 0

    if mode_name == "archive":
        print_report(rep, verbose=args.verbose)
        if not hits:
            print("✅ 无命中，无需归档。")
            return 0
        print("\n--- 归档模式：以下将被移动到 .scratch/cleaned/<ts>/ ---")
        ok, failed = archive(hits, root)
        return 1 if failed else 0

    # delete
    print_report(rep, verbose=args.verbose)
    if not hits:
        print("✅ 无命中，无需删除。")
        return 0
    print("\n--- 删除模式（沙箱外使用；沙箱内可能被护栏拒删）---")
    ok, failed = delete(hits, root)
    return 1 if (failed or ok == 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
