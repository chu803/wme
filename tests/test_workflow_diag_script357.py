# -*- coding: utf-8 -*-
"""阶段 357 · 诊断脚本本身必须**能跑通**（否则"自诊断"只是纸面功能）。

阶段 356 给 CI 加了「各步骤日志尾巴进 Summary 与 issue」，但那段 JS 从没被执行过
（会话内读不到 GitHub）⇒ 万一它自己抛异常，issue 根本不会生成，我们又会退回"让人截图"。

本守卫把 `ci.yml` 里那段脚本**原样抽出来**，在临时目录里放一份假的 `logs/*.log`，
用 node 真跑一遍，断言：
  ① 不抛异常；
  ② Summary 与 issue 正文里都出现**日志里的那行错误**（= 失败原因真的被带出去了）。
GitHub 的 `${{ toJSON(steps) }}` 在本地是字面量 ⇒ 探针里替换成等价的 JSON（它会被 GitHub 替换）。
node 不在环境里就跳过（不因缺工具而红）。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="没装 node，跳过诊断脚本实跑")


def _diagnostic_script() -> str:
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    job = list(wf["jobs"].values())[0]
    step = next(s for s in job["steps"] if s.get("id") == "diagnose")
    return step["with"]["script"]


def test_diagnostic_script_reports_the_failing_step_log(tmp_path):
    script = _diagnostic_script()
    # GitHub 会在执行前把 `${{ toJSON(steps) }}` 换成真 JSON；本地替换成等价对象。
    script = script.replace("${{ toJSON(steps) }}", json.dumps({
        "checkout": {"outcome": "success"}, "deps": {"outcome": "failure"}}))

    work = tmp_path / "w"
    (work / "logs").mkdir(parents=True)
    marker = "ERROR: No matching distribution found for numpy==2.5.1"
    (work / "logs" / "deps_min.log").write_text(
        "\n".join([f"line {i}" for i in range(60)] + [marker]), encoding="utf-8")
    (work / "gate_output.txt").write_text("no failures here\n", encoding="utf-8")

    runner = """
const core = {
  summary: {
    addHeading: (t) => { console.log('H:' + t); return core.summary; },
    addRaw: (r) => { console.log('RAW:' + String(r)); return core.summary; },
    write: () => { console.log('SUMMARY_DONE'); },
  },
  warning: (m) => console.log('WARN:' + m),
};
const github = { rest: { issues: { create: (a) => {
  console.log('ISSUE:' + a.body); return Promise.resolve();
} } } };
const context = { repo: { owner: 'o', repo: 'r' }, sha: 'abcdef0123456789' };
(async () => {
__SCRIPT__
})().catch(e => { console.log('THREW:' + e.constructor.name + ':' + e.message); process.exitCode = 1; });
"""
    (work / "runner.js").write_text(runner.replace("__SCRIPT__", script), encoding="utf-8")

    res = subprocess.run(["node", "runner.js"], cwd=str(work), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    out = res.stdout
    assert res.returncode == 0, f"诊断脚本自己抛异常了（issue 就不会生成）：\n{out}\n{res.stderr[-800:]}"
    assert "THREW:" not in out, out
    assert marker in out, f"失败步骤的日志尾巴没有出现在输出里：\n{out[-1500:]}"
    assert "SUMMARY_DONE" in out, "Summary 没写出来"
    assert "ISSUE:" in out, "issue 正文没构造出来"
