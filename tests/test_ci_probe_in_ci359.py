# -*- coding: utf-8 -*-
"""阶段 359 · CI 等价探针**接进 CI**（收集检查），并证明这条步骤真的有牙齿。

背景：阶段 358 查到 CI 连红的直接原因是「测试在模块顶层 import 了 CI 没有的可选依赖」
⇒ 收集期 ImportError ⇒ pytest 退出码 2 ⇒ 入口判失败。仓库里**早就有**能发现它的探针
（`scripts/ci_env_probe.py`），但它只是个"没人跑的手动脚本" ⇒ 缺陷潜伏到 CI 红才被反推出来。

⇒ 本阶段把它变成 CI 的**常驻步骤**。代价必须算清楚：
完整探针要 ~10 分钟（等于把套件跑两遍），而这类问题**只看收集期**就能发现
⇒ CI 里跑 `--collect-only`（实测本机 21 秒），完整执行仍留在本地手动跑。

守卫钉四件事：
  ① ci.yml 真的有这一步，且走管道时开了 `shell: bash` + `set -eo pipefail`（阶段 329 的坑）；
  ② 该步骤的输出 tee 到 `logs/<step-id>.log`（阶段 356：失败原因要能自己说话）；
  ③ `--collect-only` 与 `--extra` 的合成逻辑（纯函数）正确且不重复；
  ④ **反例**：注入一份顶层 `import fitz` 的临时用例 ⇒ 这条步骤必须判红并点名该文件
     （没有反例 = 「有牙齿」只是说说）。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from scripts.ci_env_probe import resolve_extra  # noqa: E402

CI_YML = _ROOT / ".github" / "workflows" / "ci.yml"


def _step_block(name_hint: str) -> str:
    """从 ci.yml 文本里切出某个步骤的整块（按 6 空格缩进的 `- ` 边界）。"""
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("- name:") and name_hint in ln:
            start = i
            break
    assert start is not None, f"ci.yml 里找不到含 {name_hint!r} 的步骤"
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.startswith("      - ") or not ln.startswith("        "):
            break
        block.append(ln)
    return "\n".join(block)


def test_ci_runs_the_collect_probe():
    """① CI 必须有「依赖缺失下的收集检查」这一步，且跑的是探针而不是自己抄一份参数。"""
    block = _step_block("收集检查")
    assert "ci_env_probe.py" in block, "这一步没调用探针 ⇒ 又变成第二份真值来源"
    assert "--collect-only" in block, (
        "CI 里应跑收集检查（秒级）；完整执行留在本地，见本文件开头"
    )


def test_collect_probe_step_keeps_the_pipe_exit_code():
    """② 走管道必须 `shell: bash` + `set -eo pipefail`，否则退出码被 tee 吞成 0（阶段 329）。"""
    block = _step_block("收集检查")
    assert "|" in block, "这一步应当把输出 tee 出来"
    assert "shell: bash" in CI_YML.read_text(encoding="utf-8")
    assert "set -eo pipefail" in block, f"缺 pipefail ⇒ 探针判红也照样绿：{block}"
    assert "logs/probe_collect.log" in block, (
        "缺 logs/<step-id>.log ⇒ 失败原因只在 runner 日志里（阶段 356 的坑）"
    )


def test_resolve_extra_is_idempotent_and_passthrough():
    """③ 合成逻辑：标志加上即生效；已显式透传过就不重复；不传标志时原样透传。"""
    assert resolve_extra(True) == ["--collect-only"]
    assert resolve_extra(True, ["--collect-only"]) == ["--collect-only"]
    assert resolve_extra(True, ["tests/test_x.py"]) == ["tests/test_x.py", "--collect-only"]
    assert resolve_extra(False, ["tests/test_x.py"]) == ["tests/test_x.py"]
    assert resolve_extra(False, None) == []


@pytest.fixture()
def _cf_dir():
    """反例用的临时目录：放在 `.scratch/`（已 gitignore）里，**不进 tests/**
    ——否则会和阶段 358 那条「AST 扫 tests/ 顶层 import」的守卫互相看见。"""
    d = _ROOT / ".scratch" / "ci_probe_counterfactual359"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _run_probe(args):
    """跑探针（子进程），按跨进程编码契约读回（阶段 331：父进程按 GBK 解 UTF-8 ⇒ stdout=None）。"""
    proc = subprocess.run(
        [sys.executable, "scripts/ci_env_probe.py", *args],
        cwd=str(_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_counterfactual_the_step_catches_the_real_failure(_cf_dir):
    """④ 注入「顶层 import 了 CI 没有的可选依赖」⇒ 这条步骤必须**判红并点名该文件**。"""
    offender = _cf_dir / "test_zz_offender.py"
    offender.write_text(
        "# -*- coding: utf-8 -*-\nimport fitz\n\n\ndef test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )
    rc, out = _run_probe(["--collect-only", "--extra", str(offender)])
    assert rc != 0, (
        "注入的收集期 ImportError 没被判红 ⇒ CI 那一步是装饰品（探针没有牙齿）"
    )
    assert "test_zz_offender" in out, f"判红了但没点名是哪个文件 ⇒ 诊断没用：{out[-800:]}"
    assert "CI 环境模拟" in out, "输出里没有探针自己的模拟层说明，读日志的人分不清真缺失与模拟"


def test_clean_file_under_the_same_probe_passes(_cf_dir):
    """同一条命令、同一种注入方式下，**没有**顶层可选依赖 import 的文件必须判绿（排除恒红）。"""
    clean = _cf_dir / "test_zz_clean.py"
    clean.write_text("# -*- coding: utf-8 -*-\ndef test_ok():\n    assert True\n", encoding="utf-8")
    rc, out = _run_probe(["--collect-only", "--extra", str(clean)])
    assert rc == 0, f"干净文件也被判红 ⇒ 上一条「判红」不含信息：{out[-800:]}"
