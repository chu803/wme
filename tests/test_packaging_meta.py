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


#: 根级 `.py` 里**刻意不随分发发布**的（测试基础设施，不是产品代码）。
_NOT_SHIPPED_MODULES = {
    "conftest.py": "pytest 根级夹具，不随分发发布",
}


def test_every_root_module_is_declared_or_exempted():
    """**镜像方向**的守卫：磁盘上每个根级 `.py` 都必须**已声明**或**显式豁免**。

    阶段 348：`job_manager.py` 一直**没进 `py-modules`** ⇒ 装出来的 wheel 里没有它
    ⇒ `server.py` 的 `import job_manager` 在安装态必然 `ModuleNotFoundError`
    ⇒ `wme` / `wme-cli` / `wme-api` 三个命令**一启动就崩**。
    源码目录里跑是好的（能 import 到仓库根），所以本地自测全绿也发现不了；
    而既有守卫只查了「声明的都在磁盘上」，**没查「磁盘上的都在声明里」**——正是这个缺口。

    ⇒ 与 `test_all_shipped_top_level_packages_are_declared`（顶层**包**的方向）配成一对。
    """
    declared = set(_pyproject()["tool"]["setuptools"]["py-modules"])
    on_disk = {p.name for p in _ROOT.glob("*.py") if not p.name.startswith(".")}
    unaccounted = sorted(on_disk - {f"{m}.py" for m in declared} - set(_NOT_SHIPPED_MODULES))
    assert not unaccounted, (
        f"这些根级模块未声明也未豁免 ⇒ 它们不会进 wheel，"
        f"而别的模块 import 它们时在安装态必然 ModuleNotFoundError：{unaccounted}")
    overlap = {f"{m}.py" for m in declared} & set(_NOT_SHIPPED_MODULES)
    assert not overlap, f"既是豁免又不该声明：{overlap}"


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


def test_console_scripts_declared_and_resolvable():
    """阶段 296：补齐 `[project.scripts]` 控制台入口，服务「发布 v1.0」。

    三件套 wme / wme-cli / wme-api 必须：
      · 都声明在 `[project.scripts]` 里（缺一个 ⇒ 装出的 wheel 缺该命令）；
      · 指向 `module:func` 形式，且 module 是磁盘真实存在的顶层模块；
      · func 是该模块顶层定义的可调用（AST 校验，避免 import 重型依赖）。
    这样 wheel 安装后 `wme` / `wme-cli` / `wme-api` 才是可用命令，
    而非「元数据写了对、实际跑不了」的假绿。
    """
    import ast

    scripts = _pyproject()["project"].get("scripts") or {}
    expected = {
        "wme": ("main", "main"),
        "wme-cli": ("cli", "main"),
        "wme-api": ("server", "start_server"),
    }
    # 1) 三个命令都声明
    missing = [name for name in expected if name not in scripts]
    assert not missing, f"pyproject 缺控制台入口：{missing}"
    # 2) 指向的模块:函数 与约定一致
    for name, (mod, fn) in expected.items():
        spec = scripts[name]
        assert spec == f"{mod}:{fn}", (
            f"控制台入口 {name} 指向 {spec!r}，期望 {mod}:{fn}（命名属发布范围决策，改名请同步此处）"
        )
        # 3) 模块文件存在
        mod_file = _ROOT / f"{mod}.py"
        assert mod_file.is_file(), f"控制台入口 {name} 指向的模块 {mod}.py 不存在"
        # 4) 顶层函数存在且是可调用（def / async def）
        tree = ast.parse(mod_file.read_text(encoding="utf-8"))
        top_funcs = {
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert fn in top_funcs, (
            f"控制台入口 {name} 指向的 {mod}:{fn} 不是 {mod}.py 顶层函数（top_funcs={sorted(top_funcs)}）"
        )


def test_manifest_prunes_non_package_dirs_from_sdist():
    """2026-10-17 打包口径对齐：MANIFEST.in 把 sdist 收窄到与 wheel 一致。

    wheel 由 pyproject 的 packages.find 限定（只含 core/services/ui/utils + 顶层模块），
    但 setuptools 的 sdist **默认**会把 tests/、scripts/、benchmark_real/、docs/ 等一并
    打进 .tar.gz（实测打进去 222 个 tests 条目、1.43MB）——与「测试套件不随分发发布」
    的意图不符（虽无害：纯源码、用户装的是 wheel）。MANIFEST.in 用 prune 把这些目录挡在
    sdist 外（实测 sdist 296→75 条目、1.43MB→0.84MB、tests_entries=0，且仍能重建 wheel）。

    静态守卫（不触发真实构建，避免在测试里跑 setuptools）：MANIFEST.in 必须存在，且对每个
    「不该进 sdist」的顶层目录都有 `prune <dir>`。若 MANIFEST.in 被删/回退 ⇒ 本测试失败，
    防 tests/数据悄悄回流源码包。
    """
    manifest = _ROOT / "MANIFEST.in"
    assert manifest.is_file(), (
        "缺 MANIFEST.in：sdist 默认会把 tests/scripts/benchmark_real 等一并收进 .tar.gz，"
        "与 pyproject「测试套件不随分发发布」意图不符。请补回并 prune 这些目录。"
    )
    prune_dirs = {
        ln.split()[1]
        for ln in manifest.read_text(encoding="utf-8").splitlines()
        if ln.strip().startswith("prune ") and len(ln.split()) == 2
    }
    must_prune = {"tests", "scripts", "benchmark_real", "docs", ".github", ".workbuddy"}
    missing = sorted(must_prune - prune_dirs)
    assert not missing, f"MANIFEST.in 未 prune 这些目录（会漏进 sdist）：{missing}"

