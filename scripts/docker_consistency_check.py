# -*- coding: utf-8 -*-
"""
阶段 250 · 容器构建/启动**静态一致性审计**（无需 docker CLI）。

为什么需要它
------------
本机（开发机）**没有 docker CLI**，`docker build` 从未真跑过 ⇒ 镜像问题只能等
用户侧构建才暴露，而那时的失败**成本很高**（拉 2GB 基础镜像后才发现起不来）。
本脚本把「**确定性的构建/启动失败**」与「**版本/依赖不一致**」前移为
**静态可判定**，只读 Dockerfile / .dockerignore / requirements*.txt。
退出码：0=通过，1=有 ERROR，2=只有 WARN。

审计项
------
E1 **解释器一致性（P0 类）**：装依赖的 python 与 ENTRYPOINT/CMD 用的解释器必须同一个。
   典型反例：基础镜像 ubuntu22.04 自带 `python3`=3.10，但装了 `python3.11` 并把包装进
   `/usr/local/lib/python3.11/site-packages` ⇒ 启动即 `ModuleNotFoundError`。
E2 **CUDA 版本一致性**：`FROM nvidia/cuda:<X.Y>` 必须与 requirements-gpu.txt 的
   `cuXXX` 对应（cu128↔12.8、cu121↔12.1）。
E3 必需文件未被 .dockerignore 排除（否则镜像缺文件）。
E4 大目录已被排除（否则上下文膨胀）。
W1 torch 版本单一真值来源；W2 死依赖（零 import 却钉版本）；W3 上下文体积估算。

⚠ **解析铁律（250 首版踩坑）**：Dockerfile 注释里出现的 `python3.11` / `CMD` 等字样
   会被误当指令 ⇒ **任何匹配前必须先剥离注释**（整行 + 行尾）。首版因缺这一步产生
   4 处假阳性（见 docs/250）。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(ROOT, "Dockerfile")
DOCKERFILE_GPU = os.path.join(ROOT, "Dockerfile.gpu")
DOCKERIGNORE = os.path.join(ROOT, ".dockerignore")
REQ = os.path.join(ROOT, "requirements.txt")
REQ_GPU = os.path.join(ROOT, "requirements-gpu.txt")

REQUIRED_PATHS = ["main.py", "config.py", "core", "ui", "services", "utils",
                  "requirements.txt"]
BIG_PATHS = ["models", "benchmark_real", "benchmark_prompted", ".workbuddy",
             ".scratch", ".git", "outputs", "cache", "logs"]


def read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def strip_comments(text: str) -> str:
    """剥离 Dockerfile 注释：整行 `#...` 与行尾 ` #...`（`#` 需不在引号内）。

    保留换行以维持行号语义（本脚本不依赖行号，但便于调试）。
    """
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            out.append("")
            continue
        # 简易引号感知：不在引号内的 # 之后为注释
        res, quote = [], None
        for ch in line:
            if quote:
                res.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
                res.append(ch)
                continue
            if ch == "#":
                break# 行尾注释开始
            res.append(ch)
        out.append("".join(res))
    return "\n".join(out)


def dockerignore_rules() -> list:
    out = []
    for line in read(DOCKERIGNORE).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s.rstrip("/"))
    return out


def ignored(rel: str, rules: list) -> bool:
    parts = rel.replace("\\", "/").split("/")
    for r in rules:
        rc = r.replace("\\", "/").rstrip("/")
        if not rc:
            continue
        if parts[0] == rc or rc in parts:
            return True
        if rc.endswith("*") and rel.replace("\\", "/").startswith(rc[:-1]):
            return True
    return False


def cu_tag_to_version(tag: str) -> str:
    """cu128 -> 12.8；cu121 -> 12.1（cu 后三位 = 主两位.次一位）。"""
    s = tag[-3:]
    return f"{s[:-1]}.{s[-1]}"


def audit() -> tuple:
    errs, warns, oks = [], [], []
    df_raw, dfg_raw = read(DOCKERFILE), read(DOCKERFILE_GPU)
    df = strip_comments(df_raw)
    dfg = strip_comments(dfg_raw)
    req, req_gpu = read(REQ), read(REQ_GPU)
    rules = dockerignore_rules()

    # ---- E1 解释器一致性 ----
    # CPU 镜像：`FROM python:${PYTHON_VERSION}-slim` 官方镜像自带同版本 python3 ⇒ 自洽。
    if re.search(r"FROM\s+python:", df):
        m = re.search(r"ARG\s+PYTHON_VERSION=([\d.]+)", df)
        v = m.group(1) if m else "?"
        entry = re.search(r'ENTRYPOINT\s+\[\s*"([^"]+)"', df)
        used = entry.group(1) if entry else "?"
        if used in ("python", "python3"):
            oks.append(f"E1[Dockerfile] 官方 python:{v}-slim 自带 python3={v}，"
                       f"ENTRYPOINT 用 `{used}` ⇒ 自洽")
        else:
            errs.append(f"E1[Dockerfile] ENTRYPOINT 用 `{used}`，"
                        f"但基础镜像是 python:{v}-slim（自带 {v}）⇒ 版本可能错配")
    else:
        errs.append("E1[Dockerfile] 未识别到 `FROM python:${PYTHON_VERSION}-slim`，"
                    "解析规则需更新")

    # GPU 镜像：显式装 python3.X ⇒ ENTRYPOINT/HEALTHCHECK 必须用它，或有 symlink。
    vers = re.findall(r"python3\.(\d+)", dfg)
    if vers:
        interp = f"python3.{max(set(vers), key=vers.count)}"
        has_sym = ("update-alternatives" in dfg) or bool(re.search(r"ln -sf.*python3", dfg))
        entry = re.search(r'ENTRYPOINT\s+\[\s*"([^"]+)"', dfg)
        entry_i = entry.group(1) if entry else None
        health = re.findall(r"HEALTHCHECK[\s\S]*?\n\s*CMD\s+(\S+)", dfg)
        health_i = health[0] if health else None
        for name, val in (("ENTRYPOINT", entry_i), ("HEALTHCHECK", health_i)):
            if not val:
                continue
            if val == interp:
                continue
            if val in ("python3", "python") and has_sym:
                continue
            errs.append(f"E1[Dockerfile.gpu] {name} 用 `{val}`，但依赖装在 `{interp}`"
                        f" ⇒ 系统 python3 看不到 {interp} 的 site-packages，"
                        f"启动将 ModuleNotFoundError")
        if not errs:
            oks.append(f"E1[Dockerfile.gpu] 解释器一致（{interp}"
                       f"{' + symlink 兜底' if has_sym else ''}）")
    else:
        warns.append("E1[Dockerfile.gpu] 未找到显式 python3.X，需人工确认解释器")

    # ---- E2 CUDA 版本一致性 ----
    m = re.search(r"FROM nvidia/cuda:(\d+)\.(\d+)", dfg)
    cu = re.search(r"index-url\s+\S*?cu(\d+)", req_gpu)
    if m and cu:
        have = f"{m.group(1)}.{m.group(2)}"
        want = cu_tag_to_version(cu.group(1))
        if have == want:
            oks.append(f"E2 CUDA 基础镜像 {have} 与 torch cu{cu.group(1)} 源匹配")
        else:
            errs.append(f"E2[Dockerfile.gpu] FROM cuda:{have} 与 "
                        f"requirements-gpu.txt 的 cu{cu.group(1)}（期望 {want}）不匹配")
    else:
        warns.append("E2 无法解析 CUDA 版本（格式变了？需人工确认）")

    # ---- E3/E4 .dockerignore 覆盖 ----
    miss = [p for p in REQUIRED_PATHS if ignored(p, rules)]
    if miss:
        errs.append(f"E3 必需路径被 .dockerignore 排除 ⇒ 镜像缺文件：{miss}")
    else:
        oks.append(f"E3 必需路径均未被排除（{len(REQUIRED_PATHS)} 项）")
    notign = [p for p in BIG_PATHS if not ignored(p, rules) and os.path.exists(p)]
    if notign:
        warns.append(f"E4 以下存在却未被排除 ⇒ 上下文膨胀：{notign}")
    else:
        oks.append("E4 大目录均已排除")

    # ---- W1 torch 版本单一真值 ----
    t1 = re.search(r"^torch==([\d.]+)", req, re.M)
    t2 = re.search(r"^torch==([\d.]+)", req_gpu, re.M)
    if t1 and t2:
        if t1.group(1) != t2.group(1):
            claim = ("单一真值来源" in req_gpu) or ("与 requirements.txt 同版本" in req_gpu)
            warns.append(f"W1 torch 版本分裂：requirements.txt={t1.group(1)} vs "
                         f"requirements-gpu.txt={t2.group(1)}"
                         + ("；而 gpu 清单自称「与 requirements.txt 同版本」⇒ 声明与事实不符"
                            if claim else ""))
        else:
            oks.append(f"W1 torch 版本一致（{t1.group(1)}）")

    # ---- W2 死依赖（torchvision 零 import）----
    if re.search(r"^torchvision==", req, re.M):
        used = False
        for d, subdirs, fs in os.walk(ROOT):
            subdirs[:] = [x for x in subdirs
                          if x not in (".git", ".workbuddy", ".scratch", "venv",
                                       ".venv", "__pycache__", "models", "docs",
                                       "benchmark_real", "benchmark_prompted",
                                       ".pytest_cache")]
            for f in fs:
                if f.endswith(".py"):
                    try:
                        t = read(os.path.join(d, f))
                    except OSError:
                        continue
                    if re.search(r"^\s*(import|from)\s+torchvision", t, re.M):
                        used = True
                        break
            if used:
                break
        if not used:
            warns.append("W2 requirements.txt 钉死 torchvision，但代码零 import"
                         "（纯版本耦合负担，建议移除）")

    # ---- W3 构建上下文体积估算 ----
    total = cnt = 0
    for d, subdirs, fs in os.walk(ROOT):
        rel_d = os.path.relpath(d, ROOT).replace("\\", "/")
        if ignored(rel_d if rel_d != "." else "", rules):
            subdirs[:] = []
            continue
        subdirs[:] = [x for x in subdirs if not ignored(f"{rel_d}/{x}", rules)]
        for f in fs:
            rel = f"{rel_d}/{f}" if rel_d != "." else f
            if ignored(rel, rules):
                continue
            try:
                total += os.path.getsize(os.path.join(d, f))
                cnt += 1
            except OSError:
                pass
    mb = total / 1024 / 1024
    if mb > 200:
        warns.append(f"W3 构建上下文估算 {mb:.1f}MB /{cnt} 文件（>200MB 偏大）")
    else:
        oks.append(f"W3 构建上下文估算 {mb:.1f}MB /{cnt} 文件")

    return errs, warns, oks


def main() -> int:
    # 阶段 331：输出编码加固（GBK 管道下打印 ⇒/✅ 会 UnicodeEncodeError ⇒ 「通过」被读成退出码 1）。
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)     # 本脚本刻意零依赖，这一步只为拿到编码共用件
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    errs, warns, oks = audit()
    for l in oks:
        print(f"  [OK]   {l}")
    for l in warns:
        print(f"  [WARN] {l}")
    for l in errs:
        print(f"  [ERR]  {l}")
    print()
    if errs:
        print(f"结论：{len(errs)} 个 ERROR（构建/启动必失败）、{len(warns)} 个 WARN")
        return 1
    if warns:
        print(f"结论：无 ERROR，{len(warns)} 个 WARN（不阻断构建，但需知悉/决策）")
        return 2
    print("结论：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())