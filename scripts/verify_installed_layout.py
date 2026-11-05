#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安装态布局自检：把即将发布的 wheel 装进干净 venv，在仓库外验证运行时路径。

为什么需要（阶段 379 的真实教训）：
  1.0.4 的 wheel 在源码态、页面静态检查里都正常，但 pip 安装后
  PROJECT_ROOT 变成 site-packages，输出/模型/缓存/日志全写进安装目录；
  同时这些目录在 cwd 之外，Gradio 回传修复结果时抛 InvalidPathError。
  这个缺陷只在「安装包 + 仓库外 cwd + 真有产物」时暴露，所以发布前必须补这一枪。

检查项：
  1. 安装后的 config.APP_VERSION 与 wheel 文件名版本一致；
  2. DATA_ROOT 由 WME_DATA_DIR 决定，且不在包目录内；
  3. OUTPUT_DIR / MODEL_DIR 都落在 DATA_ROOT 下；
  4. ensure_directories 只在数据目录建目录，安装目录不出现 outputs/models 等。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

_PROBE = (
    "import json\n"
    "import config\n"
    "def _get(name):\n"
    "    return str(getattr(config, name, '') or '')\n"
    "print(json.dumps({\n"
    "    'version': _get('APP_VERSION'),\n"
    "    'project_root': _get('PROJECT_ROOT'),\n"
    "    'data_root': _get('DATA_ROOT'),\n"
    "    'output_dir': _get('OUTPUT_DIR'),\n"
    "    'model_dir': _get('MODEL_DIR'),\n"
    "}, ensure_ascii=False))\n"
)

_RUNTIME_NAMES = ("outputs", "exports", "cache", "temp", "logs", "models")


def _pick_wheel(pattern: str) -> Path:
    from glob import glob

    matches = sorted(glob(pattern))
    if not matches:
        raise SystemExit(f"未找到 wheel：{pattern}")
    if len(matches) > 1:
        raise SystemExit(f"匹配到多个 wheel，请显式指定一个：{matches}")
    return Path(matches[0])


def _wheel_version(wheel: Path) -> str:
    parts = wheel.name.split("-")
    if len(parts) < 3:
        raise SystemExit(f"wheel 文件名不符合规范：{wheel.name}")
    return parts[1]


def problems_for(info: dict, expected_data_root: Path, expected_version: str) -> list[str]:
    """纯函数：按探针输出判定布局问题（便于单测做反例）。"""
    problems: list[str] = []
    package_root = Path(info["project_root"]).resolve()
    data_root = Path(info["data_root"]).resolve()

    if info["version"] != expected_version:
        problems.append(f"版本不一致：安装后 {info['version']}，wheel 文件名 {expected_version}")
    if not info["data_root"]:
        problems.append("安装后 config 没有 DATA_ROOT：这是 1.0.4 的旧布局，运行时目录会写进安装目录")
        return problems
    if data_root != expected_data_root.resolve():
        problems.append(f"DATA_ROOT 未按 WME_DATA_DIR 生效：{data_root}")
    if data_root == package_root or data_root.is_relative_to(package_root):
        problems.append(f"数据根落在安装目录内：{data_root}")
    for key in ("output_dir", "model_dir"):
        path = Path(info[key]).resolve()
        if not path.is_relative_to(data_root):
            problems.append(f"{key} 未落在数据根下：{path}")
    return problems


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, **kwargs)


def _smoke_launch(interp: Path, script: Path, extra_path: Path, tmp: Path,
                  data_root: Path, timeout: float = 150.0) -> tuple[bool, str]:
    """在**装了依赖的解释器**里真正启动一次 wheel 里的程序，确认能起来并响应 HTTP。

    为什么必须有这一步：1.0.8 的发布物"装得上、导得进"，但启动即抛
    `TypeError: Blocks.launch() got an unexpected keyword argument 'analytics_enabled'`
    —— 只做 import/布局检查是发现不了的。

    为什么用宿主解释器 + PYTHONPATH：新 venv 里没有依赖（OpenCV/Gradio），启动自检会
    直接因"缺依赖"退出，测不到"界面能不能起来"。于是用**宿主解释器**（CI 与本地都已有
    最小依赖），把 PYTHONPATH 指向 venv 的 site-packages ⇒ 跑的是 **wheel 里的代码**，
    依赖来自宿主，贴近真实用户环境。
    """
    import socket
    import time
    import urllib.error
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONPATH"] = str(extra_path)
    env.update({
        "WME_DATA_DIR": str(data_root),
        "WME_SERVER_PORT": str(port),
        "WME_SERVER_OPEN_BROWSER": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })

    log_path = tmp / "smoke_launch.log"
    ok = False
    reason = ""
    with open(log_path, "wb") as log_file:
        proc = subprocess.Popen(
            [str(interp), str(script), "--no-download"], cwd=str(tmp), env=env,
            stdout=log_file, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if proc.poll() is not None:
                    reason = f"进程已退出（返回码 {proc.returncode}）"
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
                        if resp.status == 200:
                            ok = True
                            reason = f"界面已启动并响应 HTTP 200（端口 {port}）"
                            break
                except urllib.error.HTTPError as exc:
                    ok = True
                    reason = f"界面已启动（端口 {port}，根路径返回 HTTP {exc.code}）"
                    break
                except Exception:
                    time.sleep(1.0)
            else:
                reason = f"{timeout:.0f}s 内界面没有响应"
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    if ok:
        return True, reason
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-1200:]
    except OSError:
        tail = "(读不到启动日志)"
    return False, f"{reason}\n--- 启动日志尾部 ---\n{tail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 wheel 安装态的运行时目录布局")
    parser.add_argument("--wheel", required=True, help="dist/*.whl 或具体 wheel 文件")
    args = parser.parse_args(argv)

    wheel = _pick_wheel(args.wheel).resolve()
    expected_version = _wheel_version(wheel)

    with tempfile.TemporaryDirectory(prefix="wme_install_check_") as raw_tmp:
        tmp = Path(raw_tmp)
        venv_dir = tmp / "venv"
        data_root = tmp / "data"
        cwd = tmp / "cwd"
        cwd.mkdir()

        # system_site_packages=True：让这个 venv 看到**当前环境已装好的最小依赖**
        # （CI / 发布流水线本来就装了 requirements-min）。否则启动自检会因
        # "未安装 OpenCV" 直接退出——那就测不到"界面能不能起来"这件事了。
        venv.EnvBuilder(with_pip=True, clear=True, system_site_packages=True).create(venv_dir)
        py = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        installed = _run([str(py), "-m", "pip", "install", "--no-deps", "--quiet", str(wheel)])
        if installed.returncode != 0:
            print(installed.stdout)
            print(installed.stderr)
            print(f"[FAIL] wheel 安装失败：{wheel.name}")
            return 1

        env = dict(os.environ)
        for key in list(env):
            if key.startswith("WME_") and key != "WME_DATA_DIR":
                env.pop(key)
        env.pop("PYTHONPATH", None)
        env["WME_DATA_DIR"] = str(data_root)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        probe = _run([str(py), "-c", _PROBE], cwd=str(cwd), env=env)
        if probe.returncode != 0:
            print(probe.stdout)
            print(probe.stderr)
            print("[FAIL] 安装后无法导入 config")
            return 1
        info = json.loads(probe.stdout.strip().splitlines()[-1])
        problems = problems_for(info, data_root, expected_version)

        ensure = _run([str(py), "-c", "import config; config.ensure_directories()"],
                      cwd=str(cwd), env=env)
        if ensure.returncode != 0:
            problems.append(f"ensure_directories 失败：{ensure.stderr.strip()[-300:]}")

        package_root = Path(info["project_root"])
        for name in _RUNTIME_NAMES:
            if (package_root / name).exists():
                problems.append(f"安装目录被写入运行时目录：{package_root / name}")
            if not (data_root / name).is_dir():
                problems.append(f"数据目录缺少运行时目录：{data_root / name}")

        # 阶段 384：布局合格还不够——**真的启动一次**才算数。
        # 1.0.8 的发布物能装、能导，但启动即 TypeError（参数传错了地方）⇒ 必须起一次。
        purelib = _run([str(py), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"])
        venv_site = Path(purelib.stdout.strip())
        entry = venv_site / "main.py"
        if not entry.exists():
            problems.append(f"wheel 里找不到 main.py（入口模块缺失）：{entry}")
        else:
            launched, detail = _smoke_launch(
                Path(sys.executable), entry, venv_site, tmp, data_root
            )
            if launched:
                print(f"[OK] 启动冒烟通过：{detail}")
            else:
                problems.append(f"安装后界面起不来：{detail}")

    if problems:
        print("[FAIL] 安装态布局不合格：")
        for item in problems:
            print(f"  · {item}")
        return 1

    print(f"[OK] 安装态布局合格：{wheel.name}")
    print(f"     包目录：{package_root}")
    print(f"     数据根：{data_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
