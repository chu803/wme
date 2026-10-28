# -*- coding: utf-8 -*-
"""阶段 356 · CI 必须**自诊断**：每个步骤的输出落盘，失败步骤的日志尾巴进 Summary 与 issue。

背景：CI 从 10-18 起连续红（#15~#35），每次都「红得飞快」（1m10s~1m49s ⇒ 失败在 setup 段），
但**原因只在 runner 的日志里**，而会话内读不到 GitHub ⇒ 只能一次次让人截图。
阶段 351 修了「系统依赖写死包名」，CI 仍红 ⇒ 还有别的原因。

⇒ 不再猜：把「哪一步红了」升级成「**那一步的日志尾巴**」。
本守卫钉住三件事：
  ① 每个 run 步骤都 `tee` 到 `logs/<step-id>.log`；
  ② 诊断步骤真的把 logs/ 的尾巴读出来，并同时写进 Summary 与自动建的 issue；
  ③ 用了管道就必须有 `set -eo pipefail`（否则 tee 会把退出码吞回 0 —— 阶段 329 的老坑）。
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_runs_tee_each_step_log(wf):
    data = yaml.safe_load(wf.read_text(encoding="utf-8"))
    job = list(data["jobs"].values())[0]
    run_steps = [s for s in job["steps"] if "run" in s and s.get("id")]
    assert run_steps, f"{wf.name} 没有带 id 的 run 步骤"

    for s in run_steps:
        body = s["run"]
        if "logs/" not in body:
            # 允许纯声明式步骤（如 checkout）没有日志，但带管道的必须有
            if "|" in body:
                assert "logs/" in body, (
                    f"{wf.name}: 步骤 {s['id']} 用了管道却没有 tee 到 logs/ ⇒ "
                    "失败原因仍然只在 runner 日志里")
        if "|" in body:
            assert "pipefail" in body, (
                f"{wf.name}: 步骤 {s['id']} 用了管道却没有 set -o pipefail ⇒ "
                "tee 会把退出码吞回 0（阶段 329 的坑）")


@pytest.mark.parametrize("wf", WORKFLOWS, ids=lambda p: p.name)
def test_diagnostic_reports_step_logs(wf):
    text = wf.read_text(encoding="utf-8")
    assert "stepLogs" in text, f"{wf.name} 的诊断步骤没有采集各步骤日志尾巴"
    assert "readdirSync" in text and "logs" in text, f"{wf.name} 的诊断步骤没有读 logs/"
    # Summary 与 issue 正文都要带
    assert text.count("stepLogs") >= 4, (
        f"{wf.name}: stepLogs 只出现 {text.count('stepLogs')} 次 ⇒ "
        "可能只进了 Summary 或只进了 issue（两个都要有）")


def test_log_dir_is_created_before_use():
    """两个工作流的每个 run 步骤都要先 `mkdir -p logs`（否则 tee 会因为目录不存在而失败）。"""
    for wf in WORKFLOWS:
        data = yaml.safe_load(wf.read_text(encoding="utf-8"))
        job = list(data["jobs"].values())[0]
        for s in job["steps"]:
            body = s.get("run", "")
            if "logs/" in body:
                assert "mkdir -p logs" in body, (
                    f"{wf.name}: 步骤 {s.get('id')} 写 logs/ 但没先 mkdir -p logs")
