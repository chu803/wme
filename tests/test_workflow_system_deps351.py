# -*- coding: utf-8 -*-
"""阶段 351 · CI 的系统依赖**不许写死包名**（runner 镜像自己会变）。

事故：Ubuntu 24.04（noble）起 glib 的运行时包改名为 `libglib2.0-0t64`，
**过渡包 `libglib2.0-0` 被删除**（launchpad #2063221「Drop libglib2.0-0 transitional package」）。
而 `ci.yml` / `publish.yml` 里写死了 `apt-get install … libglib2.0-0 …`
⇒ 镜像一滚到 noble，`apt` 直接 "Unable to locate package" ⇒ **整个 job 一分钟红**，
连门禁都跑不到。

这和「依赖版本钉死」是**两个独立原因**（阶段 347 是 numpy/scipy 没有 cp311 轮子；
本条是系统包名随镜像变），两个都修好 CI 才可能绿。

本守卫把「系统依赖必须**按镜像实际有什么来挑**」变成机器判定：
安装命令里必须用变量（`"$GLIB"`），不许再出现写死的旧包名。
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = [ROOT / ".github" / "workflows" / "ci.yml",
              ROOT / ".github" / "workflows" / "publish.yml"]


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_system_deps_are_not_hardcoded_to_one_image_generation(wf):
    text = wf.read_text(encoding="utf-8")

    # ① 必须**探测**镜像里有没有 t64 变体
    assert "apt-cache show libglib2.0-0t64" in text, (
        f"{wf.name} 没有探测 libglib2.0-0t64 ⇒ Ubuntu 24.04+ 上会直接 "
        "\"Unable to locate package\"（launchpad #2063221）")

    # ② 安装命令里必须用变量，而不是写死某个代际的包名
    install_lines = [ln for ln in text.splitlines()
                     if "apt-get install" in ln or re.search(r'"\$GLIB"', ln)]
    assert any('"$GLIB"' in ln for ln in install_lines), (
        f"{wf.name} 的 apt 安装命令仍写死系统包名，应改为 \"$GLIB\"（按镜像探测结果挑）")

    # ③ 不许在安装参数里出现写死的旧包名
    for ln in text.splitlines():
        if "apt-get install" in ln or ln.strip().startswith("libglib2.0-0 "):
            assert not re.search(r"(?<![\w\"])(libglib2\.0-0)(?![\w-])", ln), (
                f"{wf.name} 仍在安装命令里写死 {ln.strip()!r}（新镜像上会失败）")


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_yaml_still_parses(wf):
    import yaml

    data = yaml.safe_load(wf.read_text(encoding="utf-8"))
    job = list(data["jobs"].values())[0]
    names = [s.get("name", "") for s in job["steps"]]
    assert any("系统依赖" in n for n in names), names
    # 每个步骤都要有 id（阶段 332：诊断靠 toJSON(steps) 拿「哪一步红了」）
    ids = [s.get("id") for s in job["steps"]]
    assert all(ids), f"{wf.name} 有步骤没写 id ⇒ 诊断状态表会漏掉它：{ids}"
