"""阶段 188 · 打包元数据守卫测试（优化清单 C2）。

锁定三件事：
  ① `pyproject.toml` 可解析且关键字段齐备；
  ② **版本号单一真值来源**：`pyproject.toml` 的 `version` == `config.APP_VERSION`
     （刻意不用 setuptools 的 `dynamic/attr`——那会在构建时导入 config.py，
      而它在导入期会读环境变量并解析目录，属构建副作用）；
  ③ 声明的 `py-modules` / `packages` **与磁盘现状一致**：漏一个 ⇒ 装出来的包缺模块；
     多一个（写了不存在的）⇒ 构建期报错。两边都要拦。
  ④ （阶段 293）许可用 PEP 639 的 `license-files`，禁回退弃用的 `license` table
     （setuptools 2027-02-18 后构建不再支持）。
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config  # noqa: E402


def _pyproject() -> dict:
    with open(_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_pyproject_parses_and_has_required_metadata():
    data = _pyproject()
    proj = data.get("project") or {}
    for key in ("name", "version", "description", "requires-python", "license-files"):
        assert proj.get(key), f"pyproject.toml 缺字段 project.{key}"
    assert data.get("build-system", {}).get("build-backend") == "setuptools.build_meta"


def test_version_has_a_single_source_of_truth():
    """`config.APP_VERSION` 是唯一真值来源，pyproject 必须与它一致。"""
    proj_version = _pyproject()["project"]["version"]
    assert proj_version == config.APP_VERSION, (
        f"版本号两处不一致：pyproject={proj_version} vs config.APP_VERSION={config.APP_VERSION}"
        f"——请同时更新（真值来源是 config.APP_VERSION）"
    )


def test_declared_py_modules_exist_on_disk():
    declared = _pyproject()["tool"]["setuptools"]["py-modules"]
    missing = [m for m in declared if not (_ROOT / f"{m}.py").is_file()]
    assert not missing, f"pyproject 声明了不存在的顶层模块：{missing}"


#: 带 `__init__.py` 但**刻意不随分发发布**的顶层目录（各自写明理由）。
_NOT_SHIPPED = {
    "tests": "测试套件不随分发包发布",
}


def test_declared_packages_have_init_files():
    """`packages.find.include` 里每个前缀都必须解析到**可被发现**的真实包目录。

    仅供发现：没有 `__init__.py` 的目录（如 `scripts/`）`find` 根本看不见，
    若写进 include 就是「声明与实现不符」——本断言正是为此拦住。
    """
    includes = _pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
    for pat in includes:
        prefix = pat.rstrip("*")
        d = _ROOT / prefix
        assert d.is_dir(), f"pyproject 声明的包前缀不存在：{prefix}"
        assert (d / "__init__.py").is_file(), (
            f"{prefix} 没有 __init__.py，setuptools 不会发现它（声明与实现不符）"
        )


def test_all_shipped_top_level_packages_are_declared():
    """磁盘上所有「带 __init__.py 的顶层目录」都必须**要么**已声明、**要么**显式豁免。"""
    includes = _pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
    declared = {p.rstrip("*") for p in includes}
    on_disk = {
        d.name for d in _ROOT.iterdir()
        if d.is_dir() and (d / "__init__.py").is_file() and not d.name.startswith(".")
    }
    unaccounted = sorted(on_disk - declared - set(_NOT_SHIPPED))
    assert not unaccounted, f"这些顶层包未声明也未豁免：{unaccounted}"
    overlap = declared & set(_NOT_SHIPPED)
    assert not overlap, f"既是豁免又不该声明：{overlap}"


def test_requirements_pin_gradio_major_upper_bound():
    """阶段 188：`requirements.txt` 的说明明确要求「统一使用 6.x」，
    故钉版必须有上限（原先只有 `>=6.0`，会放行 7.x ⇒ 与自述意图不一致）。"""
    text = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
    line = [ln for ln in text.splitlines()
            if ln.strip().lower().startswith("gradio") and ">=" in ln]
    assert line, "requirements.txt 里没找到 gradio 的版本约束行"
    spec = line[0].strip()
    assert "<" in spec, f"gradio 约束缺少大版本上限：{spec}"
    assert "6.x" in text or "6.0" in spec


def test_license_uses_modern_pep639_form():
    """阶段 293：project.license 的 table 形式自 setuptools 77 起弃用、
    2027-02-18 后构建不再被支持（阶段 293 实跑 build 时暴露该弃用警告）。

    本作是专有许可（无 SPDX 标识），故按 PEP 639 用 license-files 随包附带
    LICENSE 文本。本守卫双向锁定：
      · 必须有 license-files（否则许可文本不入包）；
      · 不得回退到弃用的 license table（防 2027 构建中断回归）。
    """
    proj = _pyproject()["project"]
    assert proj.get("license-files"), "pyproject 缺 license-files（PEP 639 随包附带许可文本）"
    lic = proj.get("license")
    assert not isinstance(lic, dict), (
        f"project.license 又变回弃用的 table 形式 {lic} ⇒ setuptools 2027-02-18 后构建失败；"
        f"请改用 license-files = [\"LICENSE\"]"
    )
