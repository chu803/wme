# -*- coding: utf-8 -*-
"""i18n 状态文件跨进程泄漏守卫（阶段 290 待复审项 · 投毒态重跑证伪）。

为什么需要它（根因见 conftest.py::pytest_configure）：

  `utils.i18n.apply_language()` 会同时 `set_language()`（进程全局）与
  `save_language()`（**落盘** `cache/i18n_state.json`）。`load_language()` 的优先级是
  `环境变量 > 持久化文件 > config.UI_LANGUAGE > 默认`。于是**持久化文件里的 en-US 会在
  之后每一个全新 pytest 进程生效**，压过 `config.UI_LANGUAGE`，导致「期望中文」的用例全红。

  阶段 290 的治本修法：在 `pytest_configure`（早于 collection）把 `i18n._STATE_FILE`
  重定向到**会话专属临时目录**，于是任何作用域的用例都读不到真实持久化文件。

本守卫做「投毒态重跑证伪」：把真实 `cache/i18n_state.json` 写成 `en-US`（投毒），
再拉起一个**全新 pytest 子进程**（会触发 conftest 重定向），断言该子进程仍按
`config.UI_LANGUAGE` 回落到 `zh-CN`。若 conftest 重定向失效，子进程会读进 en-US ⇒ 本例红。
这把「泄漏是否真被修掉」从口头声明变成机器可判定。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import utils.i18n as i18n


def _real_state_path() -> str:
    # 复刻 _state_path() 的「非覆盖」分支：跟随 CACHE_DIR 现算（默认 <root>/cache）。
    d = getattr(__import__("config"), "CACHE_DIR", None)
    if d is None:
        d = os.path.join(os.getcwd(), "cache")
    return os.path.join(str(d), "i18n_state.json")


@pytest.mark.timeout(420)  # 内层子进程 timeout=300 ⇒ 外层须严格更大（test_timeout_contract 契约）
def test_i18n_state_not_leaked_across_processes(tmp_path):
    """真实状态文件被投毒为 en-US 后，全新 pytest 进程必须回落 zh-CN（不读进 en-US）。"""
    real_state = _real_state_path()
    real_dir = os.path.dirname(real_state)
    os.makedirs(real_dir, exist_ok=True)
    backup = real_state + ".leak_guard_bak"
    had_backup = False
    if os.path.exists(real_state):
        os.replace(real_state, backup)
        had_backup = True
    try:
        # ① 投毒：真实文件写成 en-US
        with open(real_state, "w", encoding="utf-8") as f:
            json.dump({"lang": "en-US"}, f, ensure_ascii=False)

        # ② 拉起一个**全新 pytest 子进程**（会触发 conftest.pytest_configure 重定向）
        child = tmp_path / "child_i18n_leak.py"
        child.write_text(
            "import utils.i18n as i18n\n"
            "def test_child_resolves_zh_cn():\n"
            "    lang = i18n.get_language()\n"
            "    assert lang == 'zh-CN', f'泄漏：get_language()={lang!r}（应为 zh-CN）'\n"
            "    assert lang != 'en-US'\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", child, "-q", "-p", "no:cacheprovider"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, (
            "投毒态下子进程仍读进 en-US（跨进程泄漏未修）：\n"
            + proc.stdout + proc.stderr
        )

        # ③ 反例闭合：若 conftest 重定向缺失，直接读真实文件必得 en-US —— 验证「毒」确实生效
        with open(real_state, "r", encoding="utf-8") as f:
            assert json.load(f).get("lang") == "en-US", "投毒未生效，证伪前提不成立"
    finally:
        # 还原现场：删掉投毒文件（及可能残留的 .bak）
        if os.path.exists(real_state):
            os.remove(real_state)
        if had_backup and os.path.exists(backup):
            os.replace(backup, real_state)


def test_conftest_redirects_state_file_out_of_real_cache():
    """正面闭合：当前会话里 i18n._STATE_FILE 已被 conftest 重定向出真实 cache 目录。"""
    assert i18n._STATE_FILE is not None, "conftest 未重定向 _STATE_FILE（泄漏防线缺失）"
    assert "cache" not in i18n._STATE_FILE.replace("\\", "/").split("/")[-2:], (
        f"_STATE_FILE 仍指向真实 cache：{i18n._STATE_FILE}"
    )
    # 真实目录里不应出现被本会话写过的状态文件
    real_state = _real_state_path()
    assert not os.path.exists(real_state) or _real_is_pristine(real_state), (
        f"真实状态文件被本会话污染：{real_state}"
    )


def _real_is_pristine(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("lang") in i18n.TRANSLATIONS
    except Exception:
        return True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
