"""阶段 184 · 验证入口 run_all_checks 守卫测试。

锁定四层：
  ① 编排层：步骤清单随 `--full` / `--gates` 变化，且 pytest 的反选标记与
     `pytest.ini` 的约定一致（torch_required / network）；
  ② 环境层：pytest 的 `--basetemp` **必须落在项目内**，不得落在系统 %TEMP%
     （阶段 183：%TEMP% 清理会触发沙箱批量删除护栏 ⇒ 一整族「删除类」用例假失败）；
  ③ 聚合层：退出码语义 = 失败优先，只有「全部跳过」才返回 2；
  ④ 同源层：CI 工作流**必须调用同一入口**，且不得再自己复制一份 marker 选择逻辑
     （防「CI 与本地各写一遍、慢慢漂移」）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import run_all_checks as rac  # noqa: E402


class _Proc:
    """假的 CompletedProcess。"""

    def __init__(self, rc, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _runner(rc_map):
    """构造一个按步骤名返回预设退出码的假 runner。"""

    def _run(argv, cwd=None, capture_output=True, text=True, **_kw):
        name = os.path.basename(argv[1] if argv[1] != "-m" else argv[2])
        name = {"pytest": "pytest", "undef_scan.py": "undef_scan",
                "clean_bench_probe.py": "clean_bench_probe",
                "quality_gate.py": "quality_gate",
                "real_gate.py": "real_gate"}.get(name, name)
        return _Proc(rc_map.get(name, 0), out="")
    return _run


# ---------------------------------------------------------------------------
# ① 编排层
# ---------------------------------------------------------------------------

def test_default_steps_are_pytest_undef_scan_docker_static():
    """默认步骤 = pytest + undef_scan + docker_static + corner_signal_gate。

    阶段 325 把 `corner_signal_gate` 从 `--gates` 提进默认：它是七道效果门禁里唯一在
    **CI 最小依赖**（无 torch/easyocr）下实测 rc=0、≈10s 的一道，且数据集随仓库分发 ⇒
    放默认就等于 CI/publish/test.bat/test.sh 经**同一入口**自动获得这道守卫。
    ⚠ 反过来不做的另一条路是「往 ci.yml 里另写一条命令」——那是 192 阶段打掉的入口副本，
    `test_entry_surfaces_call_the_same_unified_entry` 正防它。
    """
    names = [s.name for s in rac.build_steps()]
    assert names == ["pytest", "undef_scan", "docker_static", "corner_signal_gate"]


def test_fast_mode_uses_ci_equivalent_marker_selection():
    steps = rac.build_steps(full=False)
    # 解析走脚本提供的单一来源助手（argv 里 `-m` 有两种语义：模块调用 / marker）。
    assert rac.marker_expr_in(steps[0].argv) == rac.FAST_MARKER_EXPR
    # 与 pytest.ini 声明的标记名一致（单一真值来源：pytest.ini）
    ini = (_ROOT / "pytest.ini").read_text(encoding="utf-8")
    for flag in ("torch_required", "network"):
        assert flag in ini, f"pytest.ini 未声明标记 {flag}"
        assert flag in rac.FAST_MARKER_EXPR


def test_full_mode_has_no_marker_filter():
    assert rac.marker_expr_in(rac.build_steps(full=True)[0].argv) is None


def test_gates_flag_appends_gate_steps():
    names = [s.name for s in rac.build_steps(gates=True)]
    assert names == ["pytest", "undef_scan", "docker_static", "corner_signal_gate",
                     "clean_bench_probe", "quality_gate", "real_gate", "perf_probe",
                     "media_gate", "removal_truth_gate"]
    # 325：corner_signal_gate 只在默认段出现一次——`--gates` 连默认一起跑，写两遍就是跑两遍。
    assert names.count("corner_signal_gate") == 1


def test_real_gate_step_uses_strict_scope():
    step = [s for s in rac.build_steps(gates=True) if s.name == "real_gate"][0]
    assert "--strict-scope" in step.argv


def test_all_argv_use_current_interpreter():
    """不得依赖 PATH 上的 python（本环境 PATH 常不可靠）。"""
    for st in rac.build_steps(gates=True):
        assert st.argv[0] == sys.executable, st.argv


# ---------------------------------------------------------------------------
# ①' 覆盖率模式（阶段 187）
# ---------------------------------------------------------------------------

def test_cov_off_by_default():
    argv = rac.build_steps(full=True)[0].argv
    assert not any(a.startswith("--cov") for a in argv), (
        "覆盖率默认关闭：它更慢，且地板只按 --full 口径标定"
    )


def test_cov_adds_coverage_args_and_floor():
    argv = rac.build_steps(full=True, cov=True)[0].argv
    assert any(a.startswith("--cov=") for a in argv)
    assert any(a.startswith("--cov-config=") for a in argv)
    assert f"--cov-fail-under={rac.COVERAGE_FLOOR}" in argv, "地板必须由单一常量提供"


def test_cov_config_points_to_repo_root():
    """`--cov` 的路径必须是**绝对**的：本项目的测试命令常以绝对路径调用，
    相对 `source=.` 会按 cwd 解析 ⇒ 统计到空集或错目录。"""
    argv = rac.build_steps(cov=True)[0].argv
    cov_root = [a for a in argv if a.startswith("--cov=")][0].split("=", 1)[1]
    assert os.path.isabs(cov_root)
    # normcase：Windows 盘符大小写（d: vs D:）不影响路径等同性，避免环境相关误红。
    assert os.path.normcase(os.path.abspath(cov_root)) == os.path.normcase(os.path.abspath(str(_ROOT)))
    cfg = [a for a in argv if a.startswith("--cov-config=")][0].split("=", 1)[1]
    assert os.path.isabs(cfg) and os.path.isfile(cfg), f"覆盖率配置不存在：{cfg}"


def test_coverage_floor_is_sane():
    assert 0 < rac.COVERAGE_FLOOR < 100, rac.COVERAGE_FLOOR
    # 地板必须**低于实测**（否则一上线就红）。实测值在 .coveragerc 与 docs/187 登记。
    assert rac.COVERAGE_FLOOR <= 75, "地板不得高于 187 实测的 75.29%"


def test_coveragerc_disables_parallel_data():
    """阶段 187 实测：parallel 数据会让 coverage 收尾 combine 去**删除**并行文件，
    撞沙箱批量删除护栏 ⇒ INTERNALERROR、报告丢失。故必须显式关闭。"""
    text = (_ROOT / ".coveragerc").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in text.splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))
    assert "parallel = False" in body, ".coveragerc 必须显式 parallel = False"


# ---------------------------------------------------------------------------
# ② 环境层（阶段 183 教训机械化）
# ---------------------------------------------------------------------------

def test_pytest_basetemp_is_project_local_not_system_temp(monkeypatch):
    steps = rac.build_steps()
    argv = steps[0].argv
    assert "--basetemp" in argv
    base = argv[argv.index("--basetemp") + 1]
    # normcase：Windows 盘符大小写（d: vs D:）不影响前缀判定，避免环境相关误红。
    assert os.path.normcase(os.path.abspath(base)).startswith(
        os.path.normcase(os.path.abspath(str(_ROOT)) + os.sep)
    ), base
    tmp = os.path.abspath(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")
    assert not os.path.abspath(base).startswith(tmp + os.sep), (
        f"--basetemp 落在系统临时目录 {tmp} 下——会触发沙箱批量删除护栏（阶段 183）"
    )


# ---------------------------------------------------------------------------
# ③ 聚合层
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rcs,expected", [
    ([], 2),                    # 没有步骤 ⇒ 跳过
    ([0, 0], 0),                # 全通过
    ([0, 2], 0),                # 有通过 + 有跳过 ⇒ 通过（跳过不算失败）
    ([2, 2], 2),                # 全跳过 ⇒ 跳过
    ([0, 1], 1),                # 任一失败 ⇒ 失败
    ([1, 0], 1),
    ([2, 1], 1),                # 失败优先于跳过
    # ---- 阶段 328：环境错误（3）与「非约定退出码」不得被聚合成 0 ----
    ([0, 3], 3),                # real_gate 缺依赖 ⇒ 环境错误，绝不能算「全部通过」
    ([3, 0], 3),
    ([1, 3], 1),                # 质量失败优先于环境错误
    ([2, 3], 3),                # 环境错误优先于跳过
    ([0, 127], 1),              # 崩溃 / 解释器缺失 ⇒ 失败（默认拒绝未知码）
    ([0, 4], 1),                # 其它非约定码同样算失败
])
def test_aggregate_semantics(rcs, expected):
    assert rac.aggregate(rcs) == expected


def test_environment_error_is_never_reported_as_pass():
    """阶段 328 的真 bug：`real_gate` 用退出码 3 表达「缺依赖」，旧聚合层把它算成 0。

    后果不是「红一次」，而是**同一份输出自相矛盾**——那一步印 ❌/🚧，总结句却印
    「✅ 全部通过（退出码 0）」，而 `publish.yml` 跑的就是这条命令 ⇒ 环境坏了也能发版。
    这里同时钉三件事：总码不是 0、总结措辞不含「全部通过」、未知码默认失败。
    """
    code, lines, _rep = rac.run_steps(
        rac.build_steps(gates=True),
        runner=_runner({"real_gate": 3}), echo=lambda *a, **k: None)
    assert code == 3, f"环境错误必须聚合成 3，实得 {code}"
    assert not any("全部通过" in ln for ln in lines), lines

    code2, _, _ = rac.run_steps(
        rac.build_steps(), runner=_runner({"undef_scan": 127}), echo=lambda *a, **k: None)
    assert code2 == 1, f"约定外的码（127）默认按失败处理，实得 {code2}"


def test_verdict_and_mark_tables_cover_all_documented_codes():
    """退出码四档都有措辞：漏一档就是 KeyError 或印成「✅ 全部通过」。"""
    assert set(rac.VERDICTS) == {0, 1, 2, 3}, rac.VERDICTS
    assert set(rac.STEP_MARKS) == {0, 1, 2, 3}, rac.STEP_MARKS
    assert "全部通过" not in rac.VERDICTS[3]
    assert "全部通过" not in rac.VERDICTS[1]


def test_run_steps_aggregates_and_reports():
    steps = rac.build_steps(gates=True)
    code, lines, rep = rac.run_steps(
        steps, runner=_runner({"quality_gate": 1}), echo=lambda *a, **k: None)
    assert code == 1
    assert len(rep["steps"]) == len(steps)
    assert {"name", "rc", "seconds", "argv"} <= set(rep["steps"][0])


def test_run_steps_all_skipped_returns_two():
    steps = [st for st in rac.build_steps() if st.name != "pytest"]
    # 用「一律返回 2」的 runner：对默认步骤集合的未来增减免疫（不再逐个点名步骤）。
    # ⚠ pytest 必须排除在外（阶段 330）：它的 2 是「中断」不是「跳过」，
    #   留在这里就会把这条用例本该证明的事（全跳过 ⇒ 2）改成失败 ⇒ 那是**正确的**失败，
    #   但测不到想测的东西。pytest 自己的码由 test_pytest_nonzero_codes_... 单独钉。
    code, _lines, _rep = rac.run_steps(
        steps, runner=lambda *a, **k: _Proc(2), echo=lambda *a, **k: None)
    assert code == 2


def test_failure_prints_tail_output():
    steps = [rac.build_steps()[0]]
    code, lines, _rep = rac.run_steps(
        steps, runner=lambda argv, cwd=None, capture_output=True, text=True, **_kw: _Proc(1, "boom\n"),
        echo=lambda *a, **k: None)
    assert code == 1
    assert any("末尾输出" in ln for ln in lines)
    assert any("boom" in ln for ln in lines)


def test_success_still_prints_a_line_of_evidence():
    """阶段 331：绿也要说得出「绿在哪」。

    旧实现只有非零码才附打末尾输出 ⇒ 入口全绿时日志里只有「✅ pytest rc=0」＋零上下文，
    连**跑了多少条用例**都看不到（331 真跑一次实测到了这个空洞）。判定不变，只多一行证据。
    """
    steps = [rac.build_steps()[0]]
    out = "collected 3361 items\n\n...........\n3353 passed, 8 skipped in 661.81s (0:11:01)\n"
    code, lines, _rep = rac.run_steps(
        steps, runner=lambda argv, cwd=None, capture_output=True, text=True, **_kw: _Proc(0, out),
        echo=lambda *a, **k: None)
    assert code == 0
    assert any("passed" in ln for ln in lines), lines
    assert not any("末尾输出" in ln for ln in lines), "通过时不该按失败格式打印"




def _only(name):
    """只取某一步，便于单点断言退出码语义。"""
    return [st for st in rac.build_steps() if st.name == name]


def test_pytest_nonzero_codes_are_failures_not_skips():
    """pytest 的 2/3/4/5 语义是「中断 / 内部错 / 用法错 / 一条都没收集到」，**不是跳过**。

    为什么必须收口：329 之后 CI 真的会读这个退出码了。若沿用「2=跳过」的全局约定，
    CI 上一次**收集期 ImportError**（min 依赖缺包正是这种形态）会被入口判成
    「⏭ 跳过 ⇒ 全部通过」，等于把最该报警的情形洗成绿。
    """
    for code in (2, 3, 4, 5):
        got, lines, rep = rac.run_steps(
            _only("pytest"), runner=_runner({"pytest": code}), echo=lambda *a, **k: None)
        assert got == 1, f"pytest 返回 {code} 时入口应判失败，实得 {got}（{rep}）"
        # 判定改口径时**原始码要留在日志里**：只印「判 1」会让人以为 pytest 自己报了 1。
        assert any("❌" in ln and f"rc={code}" in ln for ln in lines), lines
        assert rep["steps"][0]["verdict"] == 1


def test_gate_step_skip_and_env_codes_are_preserved():
    """反向：门禁脚本的 2/3 仍按「跳过 / 环境错误」解释，不得被拖成质量失败。

    缺数据集或 OpenCV 的机器应当看得见「⏭ 没跑成」，而不是被误判为产品退化。
    """
    code2, _, _ = rac.run_steps(_only("corner_signal_gate"),
                                runner=_runner({"corner_signal_gate.py": 2}),
                                echo=lambda *a, **k: None)
    assert code2 == 2, code2
    code3, _, _ = rac.run_steps(_only("corner_signal_gate"),
                                runner=_runner({"corner_signal_gate.py": 3}),
                                echo=lambda *a, **k: None)
    assert code3 == 3, code3


def test_run_steps_pins_the_cross_process_encoding():
    """阶段 331：入口必须**同时**钉住子进程的写侧与父进程的读侧，缺一边就崩另一边。

    实测（本机中文 Windows，不设 `PYTHONUTF8`）：第 3 步 `docker_static` 打印 `audit()` 构造的
    `⇒` ⇒ 子进程按 GBK 编码失败 ⇒ rc=1「通过被读成失败」；反过来子进程已加固（UTF-8）而父进程
    按 locale(GBK) 读 ⇒ 读线程 `UnicodeDecodeError` ⇒ `stdout=None`。
    这条守卫防的是「以后有人加新步骤时把这两个参数删掉」——它们看着像可有可无的噪音。
    """
    seen = []

    def _spy(argv, cwd=None, capture_output=True, text=True, **kw):
        seen.append(dict(kw, text=text))
        return _Proc(0, "ok\n")

    steps = rac.build_steps(gates=True)
    rac.run_steps(steps, runner=_spy, echo=lambda *a, **k: None)
    assert len(seen) == len(steps), "每一步都要走同一份编码契约，不能只对某步生效"
    for kw in seen:
        assert kw.get("encoding") == "utf-8", kw
        assert kw.get("errors") == "replace", kw
        assert kw.get("text") is True, kw
        env = kw.get("env") or {}
        assert env.get("PYTHONIOENCODING") == "utf-8", (
            f"写侧没钉住：{sorted(env)[:6]}…")

# ---------------------------------------------------------------------------
# ③-b 工作流里的「管道吞退出码」守卫（阶段 329）
# ---------------------------------------------------------------------------

#: 用 `| tee` 收集门禁输出的工作流文件。
GATED_PIPE_FILES = [".github/workflows/ci.yml", ".github/workflows/publish.yml"]


@pytest.mark.parametrize("rel", GATED_PIPE_FILES)
def test_piped_gate_step_cannot_swallow_the_exit_code(rel):
    """**门禁步骤走管道时必须开 pipefail**，否则 `run_all_checks` 的退出码被 `tee` 吞成 0。

    实测（本机复现，与上游报告一致）：GitHub 未声明 `shell:` 时默认是 `sh -e`，
    **不带 pipefail** ⇒ `sh -e -c 'false | tee /dev/null'` 整步退出码 **0**，
    `bash -eo pipefail` 才是 1。也就是说「不过门禁不发布」这句注释，
    在补 pipefail 之前**并不成立**。判据取「就近上下文」：管道行的 ±6 行内
    必须同时出现 `shell: bash` 与 `pipefail`——两者都留（双保险，不依赖 runner 怎么拼 shell）。
    """
    lines = (_ROOT / rel).read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, ln in enumerate(lines):
        if ln.strip().startswith("run:") and "|" in ln and "tee" in ln:
            ctx = " ".join(lines[max(0, i - 6):i + 6])
            if "pipefail" not in ctx or "shell: bash" not in ctx:
                offenders.append(f"第 {i + 1} 行：{ln.strip()}")
    assert not offenders, (
        f"{rel} 有门禁步骤走管道却没开 pipefail ⇒ 退出码会被吞成 0，CI/发布形同装饰：{offenders}")

# ---------------------------------------------------------------------------
# ④ 同源层：CI 必须调用同一入口
# ---------------------------------------------------------------------------

def _ci_text() -> str:
    return (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


#: 三个「验证入口表面」：CI 工作流 + 两个本地脚本（阶段 192 全部收口到统一入口）。
ENTRY_SURFACES = [".github/workflows/ci.yml", "test.bat", "test.sh"]


def _executable_text(rel: str) -> str:
    """入口文件的**可执行部分**（去掉注释与空行）。

    注释里解释标记名是允许的；本守卫只拦「真的又写了一份 marker 逻辑」。
    注释语法按文件类型区分：YAML/shell 用 `#`，Windows 批处理用 `REM`。
    """
    raw = (_ROOT / rel).read_text(encoding="utf-8")
    out = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.upper().startswith("REM"):
            continue
        out.append(ln)
    return "\n".join(out)


@pytest.mark.parametrize("rel", ENTRY_SURFACES)
def test_entry_surfaces_call_the_same_unified_entry(rel):
    """**每一处**入口表面都必须调用 `scripts/run_all_checks.py`。

    阶段 192：此前 `test.bat` / `test.sh` 各自复制了一份 pytest + marker 选择逻辑，
    加上 `ci.yml` 就是**三处副本** ⇒ 与 184 修掉的问题同源（改一处忘一处）。
    """
    body = _executable_text(rel)
    assert "run_all_checks" in body, (
        f"{rel} 未调用统一验证入口 run_all_checks —— 本地脚本与 CI 会各自演化"
    )


@pytest.mark.parametrize("rel", ENTRY_SURFACES)
def test_entry_surfaces_do_not_duplicate_marker_selection(rel):
    """marker 反选逻辑必须只有一处（`run_all_checks.FAST_MARKER_EXPR`）。

    判据收窄为「**同一行**同时出现 `-m` 与标记名」——那才是**过滤逻辑**；
    横幅/说明里出现标记名只是文案（首版守卫因此误报，属「守卫把说明当逻辑」的老坑，
    与 191 的注释干扰同族）。

    可执行部分里若再写一遍 `-m "not torch_required and not network"`，
    就有了第二份真值来源 ⇒ 本地绿、CI 红（经验 184/195）。
    """
    body = _executable_text(rel)
    bad = [ln.strip() for ln in body.splitlines()
           if "torch_required" in ln and "-m" in ln]
    assert not bad, (
        f"{rel} 的可执行部分复制了 marker 选择逻辑（应只由统一入口持有）：{bad}"
    )
    assert "pytest -m" not in body and "-m pytest" not in body, (
        f"{rel} 里仍有直接调 pytest 的行——选择逻辑应只存在于统一入口"
    )


def test_list_mode_returns_skip_code():
    assert rac.main(["--list"]) == 2


# ---------------------------------------------------------------------------
# 阶段 201：basetemp 必须「每次唯一」（LESSONS 202/207 的延伸）
# ---------------------------------------------------------------------------

def test_basetemp_is_not_a_fixed_reusable_name():
    """统一入口的 `--basetemp` **不得**是固定名。

    为什么这是硬要求：pytest 启动时会**先清空** basetemp。固定名 ⇒ 第二次运行必然
    要删上一轮留下的成堆文件 ⇒ 在受限沙箱里撞「单轮批量删除」护栏，表现为
    **整片 fixture 级 ERROR**（实测 2500 个用例里 2400+ 报 E，而非真实失败），
    且极具误导性——看起来像「代码大面积崩坏」，实则是环境操作被拒。

    该缺陷在 CI 里**不可见**（每次全新 checkout ⇒ 目录恒不存在），
    但**本地第二次跑必中** ⇒ 必须在入口处根治，不能靠调用方自觉。
    """
    argv = rac.build_steps()[0].argv
    bt = argv[argv.index("--basetemp") + 1]
    base = os.path.basename(bt)
    assert base != rac._BASETEMP_NAME, (
        f"basetemp 仍是固定名 {base!r} ⇒ 第二次运行会清空旧目录并撞删除护栏"
    )
    # 必须带唯一成分（时间戳 + pid 任一即可）
    assert str(os.getpid()) in base or any(ch.isdigit() for ch in base), (
        f"basetemp 缺少唯一成分（时间戳/pid）：{base!r}"
    )


def test_basetemp_is_never_under_scratch():
    """basetemp **不得**放在 `.scratch/` 下。

    实测踩坑：`tests/test_clean_artifacts.py::test_scan_finds_expected_and_skips_git_scratch`
    断言「所有命中路径都不含 `.scratch`」；若 basetemp 落在 `.scratch/bt*/` 下，
    `tmp_path` 就在 `.scratch` 里 ⇒ 每个命中路径都含该子串 ⇒ 用例**假红**。
    正确姿势：项目根下的独立目录（既避开 %TEMP% 护栏，又不撞路径断言）。
    """
    argv = rac.build_steps()[0].argv
    bt = argv[argv.index("--basetemp") + 1]
    parts = {p.lower() for p in os.path.normpath(bt).split(os.sep)}
    assert ".scratch" not in parts, (
        f"basetemp 落在 .scratch/ 下 ⇒ 会撞 clean_artifacts 的路径断言：{bt}"
    )
