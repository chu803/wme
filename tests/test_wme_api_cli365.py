# -*- coding: utf-8 -*-
"""阶段 365 · `wme-api` 必须有「读 argv 的薄壳」，不能直接指向函数式 API。

现场（阶段 364 的安装态冒烟实测）：`pyproject` 里 `wme-api = "server:start_server"`，
而 `start_server(host=None, port=None, max_concurrency=None)` 是**给代码调用的 API**，不读 `sys.argv`。
后果有三层，一层比一层隐蔽：

1. `wme-api --help` 打的**不是用法**，而是「REST 服务已启动 … 按 Ctrl+C 停止服务」并**阻塞**
   （冒烟脚本就是在这里挂住的，第一反应还以为是依赖或网络问题）；
2. 写错的参数——`--prot 7861`——被**静默忽略**：用户以为改了端口，实际跑在默认端口上；
3. 忽略的代价不是"报错"而是**真的占用一个端口**，还会让人以为 `API_AUTH_ENABLED` 那套
   fail-closed 配置"已经生效了"。

⇒ 规则：**每个 `console_scripts` 入口都必须指向一层会解析 argv 的薄壳**（这里是 `server.cli_main`）。
`start_server` 继续作为函数式 API 供 `main.py --api` / `cli.py --serve` / 测试调用，两层各司其职。

守卫钉四件事：
  ① `--help` 打用法、退出码 0，且**没有**起服务；
  ② 拼错的参数被 argparse 拒（退出码 2），而不是静默忽略后起服务；
  ③ 三个参数确实透传给 `start_server`（不传 ⇒ None ⇒ 由 config 兜默认值）；
  ④ **全表检查**：pyproject 的每个入口函数体里都必须出现 `parse_args(`（AST 判"会不会做某事"，
     不是文本搜索——见 LESSONS #316/#337/#339）；并带反例证明这条判据不是恒绿的装饰。
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import server as S  # noqa: E402


class _Recorder:
    """替身：记下 `start_server` 被怎么调用（绝不允许真的起服务）。"""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


@pytest.fixture()
def rec(monkeypatch):
    r = _Recorder()
    monkeypatch.setattr(S, "start_server", r)
    return r


def test_help_prints_usage_and_does_not_start_serving(rec, capsys):
    """① `--help`：打用法 + 退出码 0 + 一次都没调用 start_server。"""
    with pytest.raises(SystemExit) as ei:
        S.cli_main(["--help"])
    assert ei.value.code == 0, f"--help 应该 0，实得 {ei.value.code}"
    out = capsys.readouterr().out
    assert "usage: wme-api" in out, out[:200]
    for flag in ("--host", "--port", "--max-concurrency"):
        assert flag in out, f"用法里少了 {flag}（用户无法自描述）"
    assert rec.calls == [], "--help 竟然把服务起起来了 ⇒ 阶段 365 的原事故"


def test_typo_flag_is_rejected_not_ignored(rec, capsys):
    """② 拼错参数必须**拒绝**（退出码 2），不能静默忽略后照常起服务。

    旧行为：`wme-api --prot 7861` 会把 `--prot` 当空气，直接占用默认端口——
    用户以为换了端口，实际服务跑在 7861，是「说不清的结果不是通过」（331/352）的 CLI 版。
    """
    with pytest.raises(SystemExit) as ei:
        S.cli_main(["--prot", "7861"])
    assert ei.value.code == 2, f"argparse 用法错应为 2，实得 {ei.value.code}"
    assert "unrecognized arguments" in (capsys.readouterr().err.lower()), "没告诉用户哪个参数不认识"
    assert rec.calls == [], "被忽略的参数居然还是把服务起起来了"


def test_flags_are_passed_through_to_start_server(rec):
    """③ 参数真的透传（不是解析完丢掉——「收了参数却没用」LESSONS #333 的同族）。"""
    assert S.cli_main(["--host", "127.0.0.1", "--port", "0", "--max-concurrency", "3"]) == 0
    assert rec.calls == [((), {"host": "127.0.0.1", "port": 0, "max_concurrency": 3})], rec.calls


def test_bare_invocation_defers_to_config_defaults(rec):
    """③b 不传参数 ⇒ 全 None ⇒ 由 config 兜默认值（保持旧命令的既有行为）。"""
    assert S.cli_main([]) == 0
    assert rec.calls == [((), {"host": None, "port": None, "max_concurrency": None})], rec.calls


def _parse_args_calls(source: str, func_name: str):
    """纯函数：返回该函数体里所有解析命令行参数的调用行号。

    两种写法都算：`parser.parse_args(argv)`（属性调用，cli/server 用）
    与 `parse_args()`（裸名调用，main.py 把解析抽成了同模块的函数）。
    """
    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
    assert fn is not None, f"找不到函数 {func_name}"
    hits = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Attribute) and n.func.attr == "parse_args":
            hits.append(n.lineno)
        elif isinstance(n.func, ast.Name) and n.func.id == "parse_args":
            hits.append(n.lineno)
    return hits


def _entry_targets() -> dict:
    scripts = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    return {name: value for name, value in scripts.items()}


def test_every_console_script_parses_argv():
    """④ 全表：每个入口指向的函数都必须真的解析 argv。"""
    offenders = []
    for name, ref in _entry_targets().items():
        mod_name, _, fn_name = ref.partition(":")
        mod_file = _ROOT / f"{mod_name}.py"
        assert mod_file.is_file(), f"{name} 指向不存在的模块文件：{mod_file}"
        src = mod_file.read_text(encoding="utf-8")
        if not _parse_args_calls(src, fn_name):
            offenders.append(f"{name} = {ref}（函数体里没有 parse_args ⇒ 参数会被静默忽略）")
    assert not offenders, "控制台入口缺薄 CLI 层：\n  " + "\n  ".join(offenders)


def test_the_checker_itself_is_not_a_decoration():
    """④b 反例：同一段检查逻辑必须能揪出「不读 argv 的入口」（否则④是恒绿装饰）。"""
    bad = "def start_server(host=None):\n    return host\n"
    assert _parse_args_calls(bad, "start_server") == []
    good = ('def cli_main(argv=None):\n'
            '    import argparse\n'
            '    ns = argparse.ArgumentParser().parse_args(argv)\n'
            '    return ns\n')
    assert len(_parse_args_calls(good, "cli_main")) == 1
    # 文本里出现 parse_args 字样但**不是调用**（注释/文档）也不算数 ⇒ 判据按 AST 而非文本
    text_only = 'def f(argv=None):\n    "# ns.parse_args(argv)"  # 只是文档\n    return 0\n'
    assert _parse_args_calls(text_only, "f") == []
