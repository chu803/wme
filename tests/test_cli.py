# -*- coding: utf-8 -*-
"""CLI 模型管理子命令集成测试（阶段 12/13）。

通过子进程调用 cli.py（与用户真实使用方式一致），覆盖：
- --list-engines：列出含 demo_model 的全部引擎。
- --list-models：列出模型槽并标注当前模型。
- --verify-model：内置槽无权重时应返回「未通过」且退出码非 0。
- --set-model lama：成功切换，退出码 0。
- --set-model nope：未知槽，退出码 1。

约束：不使用网络（不测 --download-model），不创建/删除文件。
"""

import pytest

from core.model_loader import is_model_available

# 环境前提：--verify-model 的「未通过」断言仅在权重未就绪时成立；
# 权重就绪后（本开发环境）应返回「通过」，该用例跳过后由对应用例覆盖。
_MODEL_PRESENT = is_model_available()


def test_list_engines(cli_run):
    """--list-engines 应列出 auto/lama/opencv/demo_model。"""
    r = cli_run("--list-engines")
    assert r.returncode == 0
    for token in ("auto", "lama", "opencv", "demo_model"):
        assert token in r.stdout


def test_list_models(cli_run):
    """--list-models 应列出模型槽并标注当前模型。"""
    r = cli_run("--list-models")
    assert r.returncode == 0
    assert "lama" in r.stdout
    assert "当前模型" in r.stdout


@pytest.mark.skipif(_MODEL_PRESENT, reason="LaMa 权重已就绪，--verify-model 应通过，跳过「未通过」断言")
def test_verify_model_missing_exit_nonzero(cli_run):
    """内置 lama 槽无权重时，--verify-model 应判定未通过且退出码非 0。"""
    r = cli_run("--verify-model", "lama")
    assert r.returncode != 0
    assert "未通过" in r.stdout or "失败" in r.stdout or "不存在" in r.stdout


def test_set_model_valid(cli_run):
    """--set-model lama 应成功并退出 0。"""
    r = cli_run("--set-model", "lama")
    assert r.returncode == 0
    assert "切换为" in r.stdout


def test_set_model_invalid_exit1(cli_run):
    """--set-model 未知槽应退出码 1。"""
    r = cli_run("--set-model", "no_such_slot_xyz")
    assert r.returncode == 1
    assert "未找到模型槽" in r.stderr


def test_set_model_invalid_does_not_corrupt_active(cli_run):
    """
    切换失败时不应破坏当前 active 槽：随后 --list-models 仍能正确显示。
    （副作用：会改写 MODEL_STATE_FILE 为失败前的 lama，属良性。）
    """
    cli_run("--set-model", "no_such_slot_xyz")  # 失败，但不影响后续
    r2 = cli_run("--list-models")
    assert r2.returncode == 0
    assert "lama" in r2.stdout
