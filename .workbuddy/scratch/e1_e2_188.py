# -*- coding: utf-8 -*-
"""阶段 188 · E1 归档 + E2 小目标清理（一次性执行脚本）。

用户已确认：
  · E1：把根目录的阶段级点文件归档到 `.scratch/`（探针脚本 / 结果 / 预览图），日志直接删；
  · E2：只清「小目标」（build/、*.egg-info/、covdata、_dbg_bench/、.verify_tmp/、.pytest_iso/），
        大目录（.pytest_* / cache / logs / temp / exports / outputs）由用户自行清空。

安全设计：
  · **移动用 `os.rename`**（不触发沙箱批量删除护栏）；
  · **删除逐目标 try/except SystemExit**：护栏拒绝时会抛 SystemExit，必须逐个兜住，
    否则脚本会被中途打断（护栏是「单次 >50 项即拒」，是本环境既有边界）；
  · 只处理**白名单内**的文件类别，绝不碰源码、docs、tests、scripts 与合法配置。
"""
import os
import shutil
import sys
from collections import Counter

ROOT = r"D:/work 111/2026-08-03-17-38-51"
SCRATCH = os.path.join(ROOT, ".scratch")

# 合法配置文件与数据文件：一律不动
KEEP = {".gitignore", ".dockerignore", ".gitattributes", ".coveragerc", ".coverage"}

moved = Counter()
deleted, refused, failed = [], [], []


def _try_delete_file(p):
    try:
        os.remove(p)
        return True
    except SystemExit:
        return False
    except Exception as e:
        failed.append((p, repr(e)))
        return False


def _try_delete_dir(p):
    """递归删除目录；被护栏拒绝/出错都如实回报（不抛）。"""
    try:
        shutil.rmtree(p)
        return True
    except SystemExit:
        return False
    except Exception as e:
        failed.append((p, repr(e)))
        return False


def phase_e1():
    for sub in ("probes", "results", "previews"):
        os.makedirs(os.path.join(SCRATCH, sub), exist_ok=True)
    for name in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, name)
        if not (name.startswith(".") and os.path.isfile(p)) or name in KEEP:
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext == ".py":
            dst = os.path.join(SCRATCH, "probes", name)
        elif ext == ".txt":
            dst = os.path.join(SCRATCH, "results", name)
        elif ext == ".png":
            dst = os.path.join(SCRATCH, "previews", name)
        elif ext in (".log", ".out"):
            # 用户确认：日志直接删（可再生）
            if _try_delete_file(p):
                deleted.append(name)
            else:
                refused.append(name)
            continue
        else:
            continue
        if os.path.exists(dst):
            failed.append((name, "目标已存在，未覆盖"))
            continue
        os.rename(p, dst)
        moved[os.path.relpath(dst, SCRATCH).split(os.sep)[0]] += 1


def phase_e2_small():
    # ① 本次新增的构建/覆盖遗留
    for name in ("build", "wme_watermark_eraser.egg-info"):
        p = os.path.join(ROOT, name)
        if os.path.isdir(p):
            (deleted if _try_delete_dir(p) else refused).append(name + "/")
    # ② 本次隔离的 coverage 并行数据（孤儿）
    p = os.path.join(ROOT, "covdata_parallel_quarantine.bin")
    if os.path.isfile(p):
        (deleted if _try_delete_file(p) else refused).append(os.path.basename(p))
    # ③ 历史调试残留与本地隔离目录（都是极少量文件）
    for name in ("_dbg_bench", ".verify_tmp", ".pytest_iso"):
        p = os.path.join(ROOT, name)
        if os.path.isdir(p):
            (deleted if _try_delete_dir(p) else refused).append(name + "/")


def main():
    phase_e1()
    phase_e2_small()
    print("=== E1 归档结果（rename，不触发删除护栏）===")
    for k, v in sorted(moved.items()):
        print(f"  .scratch/{k}: {v} 个")
    print(f"  E1 日志删除成功: {len(deleted)}  -> {deleted}")
    print("=== E2 小目标清理 ===")
    print(f"  已删除: {[x for x in deleted if x.endswith('/') or x.startswith('covdata')]}")
    print(f"  被护栏拒绝: {refused}")
    if failed:
        print(f"  异常: {failed}")
    left = sorted(f for f in os.listdir(ROOT) if f.startswith('.') and os.path.isfile(os.path.join(ROOT, f)))
    print(f"=== 归档后根目录点文件: {len(left)} 个 -> {left} ===")


if __name__ == "__main__":
    main()
