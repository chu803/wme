# -*- coding: utf-8 -*-
"""阶段 379：运行时数据根 DATA_ROOT（pip 安装场景不写 site-packages）。

背景（1.0.4 发布物实测）：
  · wheel 安装后 config.PROJECT_ROOT = site-packages；
  · outputs / models / cache / logs 全部写进安装目录（权限脆弱、升级即丢）；
  · Gradio 只服务 cwd 与临时目录，修复结果回传时抛 InvalidPathError。

本守卫在临时目录里复制 config.py 模拟三种布局：
  · 源码树（含 pyproject.toml）→ DATA_ROOT = 源码根
  · 普通安装（无 pyproject.toml）→ DATA_ROOT = 用户数据目录（WME_DATA_DIR 可覆盖）
  · PyInstaller（sys.frozen）→ DATA_ROOT = exe 所在目录
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_PROBE_BODY = textwrap.dedent(
    """
    import json
    import config

    print(json.dumps({
        "PROJECT_ROOT": str(config.PROJECT_ROOT),
        "DATA_ROOT": str(config.DATA_ROOT),
        "OUTPUT_DIR": str(config.OUTPUT_DIR),
        "MODEL_DIR": str(config.MODEL_DIR),
        "LOG_DIR": str(config.LOG_DIR),
        "ASSETS_DIR": str(config.ASSETS_DIR),
    }, ensure_ascii=False))
    """
)

_FROZEN_PRELUDE = textwrap.dedent(
    """
    import sys
    sys.frozen = True
    sys.executable = {exe!r}
    """
)


def _clean_env(**overrides) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("WME_")}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def _run_probe(
    pkg: Path,
    cwd: Path,
    env: dict[str, str],
    prelude: str = "",
    script_dir: Path | None = None,
) -> dict:
    script = (script_dir or pkg) / "_probe379.py"
    script.write_text(prelude + _PROBE_BODY, encoding="utf-8")
    env = dict(env)
    env["PYTHONPATH"] = str(pkg)
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    assert r.returncode == 0, f"probe 失败：\n{r.stdout}\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _installed_pkg(tmp_path: Path) -> tuple[Path, Path, Path]:
    pkg = tmp_path / "site-packages"
    pkg.mkdir()
    shutil.copy(ROOT / "config.py", pkg / "config.py")
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    return pkg, (tmp_path / "data"), cwd


def _user_data_root_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    home = tmp_path / "home"
    local = tmp_path / "localappdata"
    xdg = tmp_path / "xdg"
    for d in (home, local, xdg):
        d.mkdir(parents=True, exist_ok=True)
    env = _clean_env(HOME=home, LOCALAPPDATA=local, XDG_DATA_HOME=xdg)
    if os.name == "nt":
        expected = local / "wme-watermark-eraser"
    elif sys.platform == "darwin":
        expected = home / "Library" / "Application Support" / "wme-watermark-eraser"
    else:
        expected = xdg / "wme-watermark-eraser"
    return env, expected


def test_installed_layout_uses_explicit_data_root(tmp_path):
    pkg, data, cwd = _installed_pkg(tmp_path)
    out = _run_probe(pkg, cwd, _clean_env(WME_DATA_DIR=data))

    assert Path(out["PROJECT_ROOT"]) == pkg
    assert Path(out["DATA_ROOT"]) == data
    for key in ("OUTPUT_DIR", "MODEL_DIR", "LOG_DIR"):
        assert Path(out[key]).is_relative_to(data), f"{key} 没落在数据根下：{out[key]}"
    assert Path(out["ASSETS_DIR"]).is_relative_to(pkg), "静态资源应随包（只读）"
    assert not (pkg / "outputs").exists(), "安装目录里仍出现了 outputs"


def test_installed_layout_defaults_to_user_data_dir(tmp_path):
    pkg, _data, cwd = _installed_pkg(tmp_path)
    env, expected = _user_data_root_env(tmp_path)
    out = _run_probe(pkg, cwd, env)

    assert Path(out["DATA_ROOT"]) == expected
    assert not Path(out["DATA_ROOT"]).is_relative_to(pkg), "默认数据根不得落在安装目录"
    assert Path(out["OUTPUT_DIR"]).is_relative_to(expected)


def test_source_checkout_keeps_data_in_repo(tmp_path):
    # 探针脚本写到临时目录，避免在仓库根留下未跟踪文件（repo_hygiene 守卫会红）。
    out = _run_probe(ROOT, ROOT, _clean_env(), script_dir=tmp_path)
    assert Path(out["PROJECT_ROOT"]) == ROOT
    assert Path(out["DATA_ROOT"]) == ROOT
    assert Path(out["OUTPUT_DIR"]) == ROOT / "outputs"


def test_frozen_layout_uses_exe_dir(tmp_path):
    pkg, _data, cwd = _installed_pkg(tmp_path)
    exe_dir = tmp_path / "app"
    prelude = _FROZEN_PRELUDE.format(exe=str(exe_dir / "wme.exe"))
    out = _run_probe(pkg, cwd, _clean_env(), prelude=prelude)
    assert Path(out["DATA_ROOT"]) == exe_dir


def test_ensure_directories_never_touches_installed_package(tmp_path):
    pkg, data, cwd = _installed_pkg(tmp_path)
    script = pkg / "_ensure379.py"
    script.write_text(
        textwrap.dedent(
            """
            import config
            print("|".join(str(p) for p in config.ensure_directories()))
            """
        ),
        encoding="utf-8",
    )
    env = _clean_env(WME_DATA_DIR=data)
    env["PYTHONPATH"] = str(pkg)
    r = subprocess.run(
        [sys.executable, str(script)], cwd=str(cwd), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60, env=env,
    )
    assert r.returncode == 0, r.stderr

    assert data.is_dir()
    for created in ("outputs", "exports", "cache", "temp", "logs", "models"):
        assert not (pkg / created).exists(), f"安装目录被写入了 {created}"


def test_plugin_dirs_resolve_against_data_root(monkeypatch, tmp_path):
    import config
    import core.plugins as plugins

    monkeypatch.setattr(config, "PLUGIN_DIRS", ["plugins"])
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data", raising=False)
    dirs = plugins._resolve_plugin_dirs()
    assert dirs == [tmp_path / "data" / "plugins"]
