# -*- coding: utf-8 -*-
"""
阶段 250 守卫 · 容器构建/启动的**静态一致性**（`scripts/docker_consistency_check.py`）。

背景：本机**无 docker CLI**，`docker build` 从未真跑过 ⇒ 镜像的「确定性失败」
（装依赖的解释器 ≠ 启动用的解释器、CUDA 基础镜像与 torch 源不匹配、必需文件被
.dockerignore 排除）只能等用户侧构建才暴露，代价极高（拉 2GB 基础镜像后才发现
起不来）。阶段 250 已用它抓到并修掉一个 **P0**：`Dockerfile.gpu` 把依赖装进
`python3.11`，却用 `ENTRYPOINT ["python3", ...]`（Ubuntu 22.04 的 python3 是
3.10）⇒ GPU 镜像**启动必然 ModuleNotFoundError**。

本守卫做两件事
--------------
A. **现状断言**：当前仓库的容器文件**无 ERROR**（有则说明 P0 级回退了）。
B. **反向验证（不落盘）**：用 `monkeypatch` 替换 `dcc.read` 返回**构造的**坏内容，
   确认每条 ERROR 规则都会变红——**证明守卫不是哑巴**。
   ⚠刻意**不写真实文件**：245 已立判据「守卫不得制造污染」（改文件复原失败会
   留下损坏的 Dockerfile）。
"""
from __future__ import annotations

import io
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import docker_consistency_check as dcc  # noqa: E402


def _real(name: str) -> str:
    return io.open(os.path.join(_ROOT, name), encoding="utf-8").read()


# --------------------------------------------------------------------------- #
# A. 现状：不得有 ERROR
# --------------------------------------------------------------------------- #
def test_container_files_have_no_error():
    """当前 Dockerfile / Dockerfile.gpu / .dockerignore / requirements*.txt
    **不得存在 ERROR 级问题**（构建/启动必失败）。
    """
    errs, warns, _ = dcc.audit()
    assert not errs, "容器文件存在 ERROR 级问题（构建/启动必失败）：\n  " + "\n  ".join(errs)


def test_no_hidden_p0_interpreter_mismatch():
    """专项守卫：GPU 镜像不得出现「装依赖的解释器 ≠ 启动解释器且无 symlink」。"""
    dfg = dcc.strip_comments(_real("Dockerfile.gpu"))
    vers = [v for v in __import__("re").findall(r"python3\.(\d+)", dfg)]
    if not vers:
        pytest.skip("未找到显式 python3.X（基础镜像自带，无需此项判据）")
    interp = f"python3.{max(set(vers), key=vers.count)}"
    has_sym = ("update-alternatives" in dfg) or bool(
        __import__("re").search(r"ln -sf.*python3", dfg))
    entry = __import__("re").search(r'ENTRYPOINT\s+\[\s*"([^"]+)"', dfg)
    assert entry, "Dockerfile.gpu 缺 ENTRYPOINT"
    used = entry.group(1)
    assert used == interp or has_sym, (
        f"Dockerfile.gpu 的 ENTRYPOINT 用 `{used}`，但依赖装在 `{interp}`，"
        f"且**无**把 python3 指向 {interp} 的 symlink ⇒ 启动将 "
        f"ModuleNotFoundError（这是 250 修过的 P0，不得回退）")


# --------------------------------------------------------------------------- #
# B. 反向验证：每条 ERROR 规则都能抓住对应反例（守卫非哑巴）
# --------------------------------------------------------------------------- #
def _with(monkeypatch: pytest.MonkeyPatch, **fake) -> list:
    """用假文件内容替换真实读取，跑一次 audit() 返回 ERROR 列表。"""
    real_read = dcc.read

    def fake_read(p: str) -> str:
        base = os.path.basename(p)
        if base in fake:
            return fake[base]
        return real_read(p)

    monkeypatch.setattr(dcc, "read", fake_read)
    errs, _w, _o = dcc.audit()
    return list(errs)


def _strip_symlink(dfg: str) -> str:
    """去掉 `RUN update-alternatives` 兜底块（构造「无 symlink」场景）。"""
    s = dfg.find("RUN update-alternatives")
    e = dfg.find("\n\n", s) if s != -1 else -1
    return (dfg[:s] + dfg[e + 2:]) if s != -1 else dfg


def test_rule_E1_catches_interpreter_mismatch(monkeypatch):
    """反例：GPU 镜像 ENTRYPOINT 改回 python3 且去掉 symlink ⇒ 必须报 E1。

    ⚠ 阶段 312 起镜像解释器由 3.11 升到 3.12，反例**从 Dockerfile 现读**而非硬编码
    版本号——否则版本一升级、`.replace()` 就静默空转，守卫变哑巴（本次实测踩到）。
    """
    dfg = _real("Dockerfile.gpu")
    no_sym = _strip_symlink(dfg)
    m = re.search(r'ENTRYPOINT \["(python3(?:\.\d+)?)", "main\.py"\]', no_sym)
    assert m, "Dockerfile.gpu 未找到 `ENTRYPOINT [\"python3.x\", \"main.py\"]`"
    interp = m.group(1)
    assert interp != "python3", f"当前 ENTRYPOINT 已是 python3，反例构造无意义：{dfg[-200:]}"
    bad = no_sym.replace(f'ENTRYPOINT ["{interp}", "main.py"]',
                         'ENTRYPOINT ["python3", "main.py"]')
    assert bad != no_sym, "反例构造失败：ENTRYPOINT 未被替换"
    errs = _with(monkeypatch, **{"Dockerfile.gpu": bad})
    assert [x for x in errs if x.startswith("E1[Dockerfile.gpu]")], (
        f"反例未被E1 捕获（守卫是哑巴？）；实得 ERROR={errs}")


def test_rule_E1_catches_healthcheck_interpreter(monkeypatch):
    """反例：HEALTHCHECK 的解释器改回 python3（无 symlink）⇒ 必须报 E1。"""
    dfg = _real("Dockerfile.gpu")
    no_sym = _strip_symlink(dfg)
    m = re.search(r'CMD (python3(?:\.\d+)?) -c "import os,urllib\.request;', no_sym)
    assert m, "Dockerfile.gpu 未找到 HEALTHCHECK 里的 `CMD python3.x -c \"import os,urllib.request;`"
    interp = m.group(1)
    assert interp != "python3", "HEALTHCHECK 已是 python3，反例构造无意义"
    bad = no_sym.replace(f'CMD {interp} -c "import os,urllib.request;',
                         'CMD python3 -c "import os,urllib.request;')
    assert bad != no_sym, "反例构造失败：HEALTHCHECK 未被替换"
    errs = _with(monkeypatch, **{"Dockerfile.gpu": bad})
    assert [x for x in errs if x.startswith("E1[Dockerfile.gpu]")], (
        f"反例未被 E1 捕获；实得 ERROR={errs}")


def test_rule_E2_catches_cuda_drift(monkeypatch):
    """反例：CUDA 基础镜像 12.8.1 → 12.1.1（与 cu128 源不匹配）⇒ 必须报 E2。"""
    bad = _real("Dockerfile.gpu").replace("cuda:12.8.1-runtime", "cuda:12.1.1-runtime")
    errs = _with(monkeypatch, **{"Dockerfile.gpu": bad})
    assert [x for x in errs if x.startswith("E2")], (
        f"反例未被 E2 捕获；实得 ERROR={errs}")


def test_rule_E2_catches_torch_source_drift(monkeypatch):
    """反例：torch 源 cu128 → cu121（与 FROM cuda:12.8 不匹配）⇒ 必须报 E2。"""
    bad = _real("requirements-gpu.txt").replace("cu128", "cu121")
    errs = _with(monkeypatch, **{"requirements-gpu.txt": bad})
    assert [x for x in errs if x.startswith("E2")], (
        f"反例未被 E2 捕获；实得 ERROR={errs}")


def test_rule_E3_catches_required_path_excluded(monkeypatch):
    """反例：.dockerignore 排除必需目录 core/ ⇒ 必须报 E3。"""
    bad = _real(".dockerignore") + "\ncore/\n"
    errs = _with(monkeypatch, **{".dockerignore": bad})
    assert [x for x in errs if x.startswith("E3")], (
        f"反例未被 E3 捕获；实得 ERROR={errs}")


def test_rule_W1_reports_torch_version_split(monkeypatch):
    """反例（WARN 级）：把 requirements.txt 的 torch 改回 2.3.1 ⇒ 必须报 W1。"""
    import re
    req = _real("requirements.txt")
    bad = re.sub(r"^torch==2\.9\.1", "torch==2.3.1", req, count=1, flags=re.M)
    assert bad != req, "前提失效：未找到 torch==2.9.1"
    _errs, warns, _ = dcc.audit(), None, None
    errs, warns, _oks = dcc.audit()
    monkeypatch.setattr(dcc, "read", lambda p: bad if os.path.basename(p) == "requirements.txt"
                        else io.open(p, encoding="utf-8").read())
    errs, warns, _oks = dcc.audit()
    assert [x for x in warns if x.startswith("W1")], (
        f"torch 版本分裂未被 W1 报出；实得 WARN={warns}")