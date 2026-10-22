# -*- coding: utf-8 -*-
"""阶段 314 · 入口编码加固 + 非 ASCII 路径告警 守卫。

两个真实失效模式（均在本机复现）：

① **入口不加固 ⇒ 输出编码决定退出码**
   `main.py / cli.py / server.py` 会 print `✅/❌/⚠️/📄/🎬`（GBK 不可编码）。
   阶段 146/170 只加固了 `scripts/` 下的门禁脚本，产品入口一直漏着：
   `python cli.py ... > 日志.txt`（或任何管道）会在**干完活之后**因汇总行崩溃，
   退出码 1 ⇒ 「成功」被读成「失败」。实测 `print('✅ 完成')` 重定向即 EXIT=1。

② **非 ASCII 安装路径 ⇒ 模型加载报「文件不存在」**
   Windows 上 libtorch 用 ANSI 代码页打开文件，`D:\\工具\\...\\models\\big-lama.pt`
   会让 `torch.jit.load` 报 `WinError 127 ... No such file or directory`（文件明明在）。
   本机实测一次拖垮 46 个用例，症状与「权重缺失」无法区分。
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.console_encoding import force_utf8_stdio          # noqa: E402
from utils.path_helper import non_ascii_path_warning          # noqa: E402


# ==============================================================================
# ① 编码加固
# ==============================================================================
def test_force_utf8_stdio_switches_gbk_stream(monkeypatch):
    """把 gbk 的 stdout 换进来，加固后编码必须是 utf-8 且能打印 ✅。"""
    import io

    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stream)
    force_utf8_stdio()
    assert stream.encoding.lower().replace("-", "") == "utf8"
    print("✅ 完成")          # 加固后必须可打印（未加固时抛 UnicodeEncodeError）
    stream.flush()


def test_force_utf8_stdio_is_idempotent(monkeypatch):
    """重复调用不得抛（main/cli/server 可能各自调用一次）。"""
    force_utf8_stdio()
    force_utf8_stdio()


@pytest.mark.parametrize("entry", ["main.py", "cli.py", "server.py"])
def test_entry_point_hardens_encoding(entry):
    """三个产品入口都必须在打印之前调用 `force_utf8_stdio()`。"""
    src = open(os.path.join(_ROOT, entry), encoding="utf-8").read()
    assert "from utils.console_encoding import force_utf8_stdio" in src, (
        f"{entry} 未从 utils.console_encoding 导入加固件（分发包不含 scripts/，不能从 scripts 导入）"
    )
    assert "force_utf8_stdio()" in src, f"{entry} 导入了但没有调用"


@pytest.mark.parametrize("entry", ["main.py", "cli.py", "server.py"])
def test_entry_hardens_before_business_imports(entry):
    """加固必须早于业务模块导入——导入期日志也要走 UTF-8。

    实测教训：最初只把调用放在 `main()` 里，重定向文件仍混着两种编码——
    插件注册日志发生在**模块导入期**，早于 `main()` ⇒ 那几行仍是 GBK。
    """
    src = open(os.path.join(_ROOT, entry), encoding="utf-8").read()
    call = src.find("force_utf8_stdio()")
    assert call != -1, f"{entry} 未调用加固"
    for marker in ("import config", "from services", "from core", "import api"):
        idx = src.find(marker)
        if idx != -1:
            assert call < idx, (
                f"{entry} 的编码加固晚于 `{marker}`（第 {idx} 字符）——"
                f"导入期日志会按 GBK 输出，重定向文件将混合两种编码"
            )


def test_cli_survives_gbk_redirect(tmp_path):
    """行为级反例：GBK 管道下 `cli.py --list-models` 必须 EXIT=0 且无 UnicodeEncodeError。

    不加加固时：`avail = "✅"/"❌"` 这一行会抛 UnicodeEncodeError，进程 EXIT=1。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "gbk"
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run(
        [sys.executable, os.path.join(_ROOT, "cli.py"), "--list-models"],
        cwd=_ROOT, env=env, capture_output=True, timeout=180,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    assert "UnicodeEncodeError" not in err, f"GBK 管道下编码崩溃：\n{err[-800:]}"
    assert proc.returncode == 0, f"--list-models 在 GBK 下 EXIT={proc.returncode}\n{err[-800:]}"
    assert out.strip(), "没有任何输出"


# ==============================================================================
# ② 非 ASCII 路径告警
# ==============================================================================
def test_non_ascii_path_warning_ascii_is_silent():
    assert non_ascii_path_warning(r"D:\watermark-workbench\models\big-lama.pt") is None
    assert non_ascii_path_warning("/opt/wme/models") is None


@pytest.mark.parametrize("path", [
    r"D:\工具\watermark\models\big-lama.pt",
    r"D:\qoder\归档整理\01-我的项目\models\big-lama.pt",
    "/home/用户/wme/models",
])
def test_non_ascii_path_warning_flags_cjk(path):
    msg = non_ascii_path_warning(path)
    assert msg and "非 ASCII" in msg
    assert "WinError 127" in msg, "告警必须点出真实症状，否则用户会去重下权重"
    assert "纯英文路径" in msg, "告警必须给出可执行的处置建议"


def test_environment_check_uses_path_guard():
    """启动自检必须做路径编码检查（否则用户装到中文目录后无从排查）。"""
    src = open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
    assert "non_ascii_path_warning(config.PROJECT_ROOT)" in src, (
        "run_environment_check 未检查 PROJECT_ROOT 的路径编码"
    )


def test_model_load_error_includes_path_hint():
    """模型加载失败时，若路径含非 ASCII，异常文本必须带上排查提示。"""
    src = open(os.path.join(_ROOT, "core", "model_manager.py"), encoding="utf-8").read()
    assert "non_ascii_path_warning(model_path)" in src, (
        "model_manager 的 ModelLoadError 未附带路径编码提示"
    )


def test_warning_text_is_printable_under_utf8():
    """告警文本本身必须能安全打印（不得再引入编码问题）。"""
    msg = non_ascii_path_warning(r"D:\工具\models")
    for line in textwrap.wrap(msg or "", 200):
        line.encode("utf-8")
