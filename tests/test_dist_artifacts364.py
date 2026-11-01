# -*- coding: utf-8 -*-
"""阶段 364 · 分发产物核对（`scripts/verify_dist_artifacts.py`）与它的发布位置。

背景（为什么值得单独一条守卫）：阶段 348 的真实事故是
`pyproject.toml` 的 `py-modules` 漏了 `job_manager` ⇒ **wheel 里没有它**，
而源码目录里 `sys.path` 含仓库根 ⇒ 本地全量测试一路绿灯，装出来的包三个命令启动即崩。
教训（LESSONS 已记）：**改了打包配置要真打一次并查 zip 条目**。
本阶段把"查条目"固化成一条命令，并把它钉在 `publish.yml` 的**上传之前**。

守卫钉四件事：
  ① 产物文件名匹配规则（wheel 用点分版本 + `-` 分隔；sdist 用 `-<版本>.tar.gz`）——
     第一版按 `_{版本带下划线}-` 找 wheel，结果把**合格的产物报成不合格**（自己踩的，已修）；
  ② wheel 的每一项检查都要**真的能报出问题**（缺模块 / 入口指向包外 / 混进权重 / 混进 tests）；
  ③ sdist 必须带 `requirements-*.txt`（pyproject 刻意不声明 dependencies ⇒ 那是唯一依赖真值来源，
     README §3.3 与 docs/297 runbook 第 5 步都指着它，但首版 sdist 里没有）；
  ④ `publish.yml` 里这一步存在、在 `publish` **之前**、且守 workflow 既有纪律（id / tee logs / pipefail）。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tarfile
import zipfile
from importlib import metadata

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from scripts.verify_dist_artifacts import (  # noqa: E402
    check_sdist,
    check_wheel,
    main as verify_main,
    match_artifacts,
)

_DIST = "wme_watermark_eraser-1.0.3.dist-info"
_VERSION = "1.0.3"
_GOOD_SCRIPTS = "[console_scripts]\nwme = main:main\nwme-cli = cli:main\nwme-api = server:cli_main\n"
_GOOD_NAMES = [
    "config.py", "main.py", "cli.py", "server.py", "api.py", "job_manager.py",
    "core/__init__.py", "core/image_utils.py", "services/__init__.py",
    "ui/__init__.py", "utils/__init__.py",
    "ui/assets/sample_watermarked.png",
    f"{_DIST}/RECORD", f"{_DIST}/WHEEL",
]


def _make_wheel(path: pathlib.Path, names, scripts_text: str = _GOOD_SCRIPTS) -> str:
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x" if n.endswith(".py") else "")
        z.writestr(f"{_DIST}/METADATA",
                   "Metadata-Version: 2.1\nName: wme-watermark-eraser\nVersion: 1.0.3\n")
        z.writestr(f"{_DIST}/entry_points.txt", scripts_text)
    return str(path)


def _make_sdist(path: pathlib.Path, names) -> str:
    prefix = f"wme_watermark_eraser-{_VERSION}"
    with tarfile.open(path, "w:gz") as t:
        for n in names:
            data = b"content"
            info = tarfile.TarInfo(f"{prefix}/{n}")
            info.size = len(data)
            t.addfile(info, __import__("io").BytesIO(data))
    return str(path)


def test_match_artifacts_handles_the_real_naming():
    """① 文件名规则：wheel 里版本是**点分**且前后都是 `-`（第一版按 `_1_0_3-` 找 ⇒ 永远找不到）。"""
    files = [
        "wme_watermark_eraser-1.0.3-py3-none-any.whl",
        "wme_watermark_eraser-1.0.3.tar.gz",
        "wme_watermark_eraser-1.0.2-py3-none-any.whl",   # 旧版本混在同一个 dist/ 里
        "wme_watermark_eraser-1.0.2.tar.gz",
    ]
    wheel, sdist = match_artifacts(files, _VERSION)
    assert wheel == "wme_watermark_eraser-1.0.3-py3-none-any.whl"
    assert sdist == "wme_watermark_eraser-1.0.3.tar.gz"
    assert match_artifacts(files, "9.9.9") == (None, None)


def test_good_wheel_passes_and_each_defect_is_named(tmp_path):
    """② 合格的 wheel 必须给空清单；四类缺陷各自必须**点 named**。"""
    w = _make_wheel(tmp_path / "good.whl", _GOOD_NAMES)
    assert check_wheel(w) == [], "合格 wheel 被报出问题 ⇒ 检查式与真实打包口径不一致"

    missing = [n for n in _GOOD_NAMES if n != "job_manager.py"]
    p = check_wheel(_make_wheel(tmp_path / "m.whl", missing))
    assert any("job_manager" in x for x in p), f"漏 job_manager 没被抓到（阶段 348 的原事故）：{p}"

    dangling = _make_wheel(tmp_path / "d.whl", _GOOD_NAMES,
                           "[console_scripts]\nwme = nope:main\nwme-cli = cli:main\nwme-api = server:cli_main\n")
    p = check_wheel(dangling)
    assert any("启动即" in x and "nope" in x for x in p), f"入口指向包外模块没被抓到：{p}"

    weight = _make_wheel(tmp_path / "wt.whl", _GOOD_NAMES + ["models/big-lama.pt"])
    assert any("权重" in x for x in check_wheel(weight))

    leaked = _make_wheel(tmp_path / "lk.whl", _GOOD_NAMES + ["tests/test_x.py", "scripts/tool.py"])
    p = check_wheel(leaked)
    assert any("tests/" in x for x in p) and any("scripts/" in x for x in p), p


def test_sdist_must_carry_the_requirements(tmp_path):
    """③ sdist 里必须有依赖清单（缺一份就点名一份）；正例给空清单。"""
    good = [
        "pyproject.toml", "README.md", "LICENSE", "config.py", "main.py",
        "requirements.txt", "requirements-min.txt", "requirements-dev.txt",
        "requirements-gpu.txt", "requirements-verified.txt",
    ]
    assert check_sdist(_make_sdist(tmp_path / "good.tar.gz", good)) == []

    p = check_sdist(_make_sdist(tmp_path / "bad.tar.gz",
                                [n for n in good if n != "requirements-min.txt"]))
    assert any("requirements-min.txt" in x for x in p), f"缺清单没被抓到（阶段 364 实测到的缺口）：{p}"
    assert any("唯一" in x or "真值" in x for x in p), "报错没说清为什么这份清单重要"


def test_manifest_declares_the_requirements_inclusion():
    """③b `MANIFEST.in` 必须显式 include 依赖清单，否则 sdist 缺口会悄悄回来（setuptools 默认不收）。"""
    text = (_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for req in ("requirements.txt", "requirements-min.txt", "requirements-dev.txt"):
        assert f"include {req}" in text, f"MANIFEST.in 没 include {req} ⇒ sdist 里不会有它"


def test_verifier_returns_two_when_no_artifacts(tmp_path):
    """④ 家族口径：没有产物 = 跳过（2），不是失败（1）——CI 上没有 dist/ 时不该判红。"""
    assert verify_main(["--dist", str(tmp_path), "--version", _VERSION]) == 2


def test_publish_verifies_artifacts_before_upload():
    """④ `publish.yml` 必须在**上传之前**核对产物，并守住 workflow 既有纪律。"""
    wf = _ROOT / ".github" / "workflows" / "publish.yml"
    data = yaml.safe_load(wf.read_text(encoding="utf-8"))
    job = list(data["jobs"].values())[0]
    steps = job["steps"]
    ids = [s.get("id") for s in steps]
    assert all(ids), f"有步骤缺 id ⇒ 失败诊断状态表会漏掉它：{ids}"

    idx = {s["id"]: i for i, s in enumerate(steps)}
    assert "verify_dist" in idx, "publish.yml 没有核对分发产物这一步"
    assert idx["verify_dist"] < idx["publish"], "核对必须在上传到 PyPI 之前"
    assert idx["verify_dist"] > idx["build"], "核对必须在构建之后（否则查的是旧产物）"

    body = steps[idx["verify_dist"]]["run"]
    assert "verify_dist_artifacts.py" in body
    assert "set -eo pipefail" in body, "走管道没开 pipefail ⇒ 核对判红也会被吞成 0（阶段 329 的坑）"
    assert "logs/verify_dist.log" in body, "缺步骤日志 ⇒ 失败原因只在 runner 里（阶段 356 的坑）"


@pytest.mark.timeout(900)
def test_verifier_agrees_with_a_real_build(tmp_path, monkeypatch):
    """⑥ 端到端：真跑一次 `python -m build`（含构建隔离）+ 核对 ⇒ 必须 0。

    用 `--no-isolation`：隔离构建要去 PyPI 取 `setuptools`/`wheel`，
    而本机默认镜像（清华）取不到 ⇒ 这条会变成"网络配置决定红绿"的那种不稳定守卫
    （阶段 297 的诚实边界卡的就是这里；带镜像环境变量的**隔离构建**由 runbook 手工执行并留档）。
    这里要验的是"真 setuptools 打出来的条目"是否合口径 ⇒ 用当前环境里的 setuptools 直接打。
    """
    try:
        metadata.version("build")
    except Exception:
        pytest.skip("本机没装 build 前端（`pip install build` 后可跑）")
    # 真打一次包要 **2–4 分钟**（wheel + sdist 两次构建 + 杀软扫新文件）。
    # 发布链上这一步本来就会真跑（publish.yml 的 verify_dist 步），所以它**默认跳过**，
    # 需要时用 `set WME_RUN_REAL_BUILD=1` 显式开启；否则每次本地入口白多 4 分钟（实测就是它把
    # 整套从 7 分钟推到 11 分钟）。
    if os.environ.get("WME_RUN_REAL_BUILD") != "1":
        pytest.skip("真构建耗时 2–4 分钟；发布链已真验，本地按需设 WME_RUN_REAL_BUILD=1")
    # dist 用 tmp_path，避免污染仓库的 dist/
    r = subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(tmp_path)],
                       cwd=str(_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    assert r.returncode == 0, f"构建失败：\n{r.stdout[-800:]}\n{r.stderr[-800:]}"
    assert verify_main(["--dist", str(tmp_path), "--version", _VERSION]) == 0, \
        "真构建的产物核对不过 ⇒ 分发口径与打包配置已经漂移"
