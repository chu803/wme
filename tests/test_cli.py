# -*- coding: utf-8 -*-
"""CLI 模型管理子命令集成测试（阶段 12/13）。

通过子进程调用 cli.py（与用户真实使用方式一致），覆盖：
- --list-engines：列出含 demo_model 的全部引擎。
- --list-models：列出模型槽并标注当前模型。
- --verify-model：内置槽无权重时应返回「未通过」且退出码非 0。
- --set-model lama：成功切换，退出码 0。
- --set-model nope：未知槽，退出码 1。
- 批量阶段退出码：有失败⇒1、干净/跳过⇒0（阶段 316）。

约束：不使用网络（不测 --download-model）；文件只在 pytest 的 tmp_path 里创建，
不动仓库与用户目录（沙箱的 safe-delete 钩子会拦 Python 删除，故一律不删）。
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


# ---------------------------------------------------------------------------
# 阶段 316 · 批量阶段退出码（承 315 实战演练）
# 演练实测：17 个文件、2 个失败，进程仍返回 0 ⇒ 脚本/CI 只能去解析中文汇总文本
# 才能知道「这批里有东西没做成」。以下三例把退出码钉死。
# ---------------------------------------------------------------------------
def test_batch_exit_code_failures_become_1():
    import cli

    assert cli._batch_exit_code({"success": 15, "skipped": 0, "failed": 0}) == 0
    assert cli._batch_exit_code({"success": 15, "skipped": 0, "failed": 2}) == 1


def test_batch_exit_code_skip_is_not_failure():
    """「未检出水印 ⇒ 跳过」是正常裁决，不得被当成失败（否则干净图一批全红）。"""
    import cli

    assert cli._batch_exit_code({"success": 0, "skipped": 5, "failed": 0}) == 0


def test_cli_batch_reports_failure_via_exit_code(cli_run, tmp_path):
    """真实子进程：目录里只有 1 个 0 字节图片 ⇒ stdout 记失败、退出码 1。"""
    src = tmp_path / "in"
    src.mkdir()
    (src / "empty.jpg").write_bytes(b"")
    r = cli_run("-i", str(src), "-o", str(tmp_path / "out"))
    assert "失败 1" in r.stdout, f"汇总文本未如实计数：{r.stdout[-500:]}"
    assert r.returncode == 1, f"有失败仍退 0（CI 无法判败）：rc={r.returncode}"


@pytest.mark.timeout(420)   # 内层 cli_run(timeout=300) ⇒ 外层必须更宽（tests/test_timeout_contract.py 契约）
def test_cli_batch_all_clean_exits_zero(cli_run, tmp_path):
    """反方向：没有失败项时不得返回 1（含「全部跳过」与「全部成功」两种结局）。"""
    import numpy as np
    from PIL import Image

    src = tmp_path / "in"
    src.mkdir()
    g = np.repeat(np.arange(64, dtype=np.uint8)[None, :], 64, axis=0)
    Image.fromarray(g).save(src / "gradient.png")
    r = cli_run("-i", str(src), "-o", str(tmp_path / "out"), timeout=300)
    assert "失败 0" in r.stdout, f"干净图被判失败：{r.stdout[-500:]}"
    assert r.returncode == 0, f"无失败却非 0：rc={r.returncode}\n{r.stdout[-500:]}"
