#!/usr/bin/env python3
# ==============================================================================
# 验证入口 · run_all_checks
#
# 阶段 184：把「本项目到底该跑哪些检查」固化成**唯一入口**，本地与 CI 同源。
#
# 为什么需要它（阶段 183 的根因）
# ------------------------------
# 183 首次跑全库，暴露 6 处**跨阶段锚点腐化**（守卫常红 / 断言停在旧世界 / 新模块漏登记）。
# 根因不是「没人写测试」，而是**没有任何一个命令要求『全都跑一遍』**：
#   · `.github/workflows/ci.yml`（阶段 116 新增）声明了机器门禁，但**仓库根本不是 git 仓库**
#     （无 `.git`）⇒ 工作流从未执行过 —— 门禁「存在但从未运行」= 没有门禁；
#   · 本地也没有等价入口 ⇒ 每阶段只跑「本阶段相关」子集，红灯被隔离在视野之外。
# 本脚本补上这一环：**同一套步骤**既能在本地一键跑、也能被 CI 直接调用，
# 从而消除「CI 与本地各写一遍、慢慢漂移」的老问题。
#
# 步骤（按依赖从轻到重）
# ----------------------
#   ① pytest         —— 测试套件。默认跑 **CI 等价子集**（跳过 torch_required / network
#                        标记，与 pytest.ini 的约定一致）；`--full` 跑全量。
#                        `--cov` 时追加覆盖率统计与**地板判定**（口径见 `.coveragerc`）。
#   ② undef_scan     —— 静态「引用了但从未定义」名扫描（零依赖、秒级，见该脚本说明）。
#   ②-b docker_static —— 容器静态一致性（`scripts/docker_consistency_check.py`，
#                        只读 Dockerfile* / .dockerignore / requirements*.txt，零依赖、
#                        秒级；守解释器错配 / CUDA 版本不匹配 / 依赖单一真值来源等
#                        「确定性构建失败」。本机无 docker CLI，故只做静态门禁）。
#   ②-c corner_signal_gate —— 角标路由门禁（阶段 322b 建、**325 起进默认步骤**）：
#                        真实图集 × 生产信号 `_corner_watermark_signal`，命中不得掉线、
#                        非角标必须零误命中。它是七道效果门禁里唯一能在 **CI 最小依赖**下跑的一道
#                        （实测屏蔽 torch/torchvision/easyocr 后仍 rc=0、10.2s；数据集随仓库分发），
#                        故放这里而不是 `--gates`——CI/publish/test.bat/test.sh 经**同一入口**自动获得，
#                        不在 ci.yml 里另写命令（那是 `test_entry_surfaces_...` 防的入口副本）。
#                        缺数据集/OpenCV 时该脚本自身返回 2 ⇒ 聚合层按「跳过」放行，不判失败。
#   ③ 门禁步骤       —— 仅 `--gates` 时追加（需 torch / 耗时）：
#                        clean_bench_probe（干净图误报）· quality_gate（合成红线）
#                        · real_gate --strict-scope（真实图红线 + 漂移）
#                        · perf_probe（检测阶段 P95 回归护栏）
#                        · media_gate（PDF/视频链路；阶段 315 补齐全源验证）
#                        · removal_truth_gate（修复侧像素真值；阶段 322a）
#
# 退出码（与门禁家族契约一致，四档）
# ----------------------------
#   0 = 全部通过（至少一步真通过，且无失败/环境错误）
#   1 = 有任一步骤判「质量未通过」；**或出现约定集合之外的码**（127、崩溃等一律算失败）
#   2 = 跳过（`--list` 只列步骤；或所有步骤都判「跳过」）
#   3 = 有步骤判「环境错误」（如 `real_gate` 缺关键依赖）——**不是通过**
#
#   ⚠ 阶段 328：旧实现只把 1 当失败 ⇒ 环境错误(3) 会被聚合成 0，
#     于是同一份输出里那行是 ❌、总结句却是「✅ 全部通过」，而 `publish.yml` 跑的就是这条命令。
#   ⚠ 阶段 330：上面四档是**判定码**，不是各步骤的原始码。每个步骤自己声明 2/3 的含义
#     （`Step.skip_codes` / `env_codes`）：门禁脚本 2=跳过、3=环境错误；pytest 两档都没有
#     （2=中断、3=内部错、4=用法错、5=没收集到用例 ⇒ 全是失败）。不改这一条，329 修好的
#     CI 会把「收集期 ImportError」读成 ⏭ 跳过、再聚合成 ✅ 全部通过 —— 又一种假绿。
#
# ⚠ 环境要点（阶段 183/187 教训，已为此固化）
#   · pytest 一律用**项目内** `--basetemp`：受限环境下 `%TEMP%` 的临时目录清理会触发
#     沙箱「批量删除」护栏（> 50 文件即拒），导致一整族「删除类」用例**假失败**。
#   · 覆盖率必须 `.coveragerc` 里 `parallel = False`：否则 coverage 收尾 combine 会
#     **删除**并行数据文件 ⇒ 撞护栏 ⇒ pytest INTERNALERROR、报告整体丢失（187 实测）。
#   · 步骤一律以 `sys.executable` 调用，避免依赖 PATH 上的 python。
# ==============================================================================
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 反选标记（**单一真值来源**：CI 与本地都从这里取，见 tests/test_run_all_checks.py）。
FAST_MARKER_EXPR = "not torch_required and not network"

#: pytest 的临时根目录名（项目内，规避 %TEMP% 清理护栏）。
#:
#: ⚠ **选位禁忌**（阶段 201 实测踩坑，LESSONS 207 的补充）：basetemp **不能放在
#:   `.scratch/` 下**。`tests/test_clean_artifacts.py::test_scan_finds_expected_and_skips_git_scratch`
#:   断言「所有命中路径都不含 `.scratch`」；若 basetemp 落在 `.scratch/bt*/` 下，
#:   `tmp_path` 就在 `.scratch` 里 ⇒ 每个命中路径都含该子串 ⇒ 用例**假红**。
#:   正确姿势：放在**项目根下的独立目录**（如 `.pytest_runall`），既避开 %TEMP% 护栏，
#:   又不撞任何「路径不含某目录」的断言。
_BASETEMP_NAME = ".pytest_runall"

#: 覆盖率地板（百分比，阶段 187 标定）。
#: 标定依据：**全量套件实测 75.29%**（14835 语句 / 漏 3665，2026-10-01，torch 在位）。
#: 取 ≈实测 × 0.90 并向下取整 ⇒ 67：既拦住「覆盖率明显下滑」，又不因**环境差异**误红
#: （本机 7 个 skip 由 torch / colorama 是否安装决定，换机器会改变分母与结果）。
#: ⚠ 该地板只适用于 `--full`（含 torch 测试）的口径；CI 的 `not torch_required` 子集
#:   覆盖率**尚未测量**，故 CI 暂不启用 `--cov`（避免用一个没测过的数字去判定 ⇒ 假红）。
COVERAGE_FLOOR = 67

#: 覆盖率报告的落点（`htmlcov/` 已在 .gitignore 内）。
_COV_REPORT_DIR = "htmlcov"


@dataclass
class Step:
    """一个可执行的检查步骤。

    `skip_codes` / `env_codes` 声明**这个步骤自己**的退出码约定（阶段 330）：
    门禁家族用 2=跳过、3=环境错误，但 pytest 的 2/3/4/5 全是事故（中断、内部错、
    用法错、一条用例都没收集到）。约定不同源就不能共用一张表，否则最该报警的
    收集期 ImportError 会被洗成「⏭ 跳过 ⇒ 全部通过」。
    """

    name: str
    argv: list = field(default_factory=list)
    note: str = ""
    header: str = ""          # 打印用的额外说明
    skip_codes: tuple = (2,)
    env_codes: tuple = (3,)


def build_steps(*, full: bool = False, gates: bool = False, root: str = _ROOT,
                cov: bool = False) -> list:
    """构造步骤清单（纯函数，便于测试断言编排）。

    参数说明：
        full  : True = 跑全量 pytest（含 torch_required / network）；False = CI 等价子集。
        gates : True = 追加门禁步骤（需 torch，耗时）。
        cov   : True = pytest 追加覆盖率统计与**地板判定**（`--cov-fail-under`）。
        root  : 项目根目录。

    返回值：
        Step 列表。
    """
    # 阶段 201 修：**每次运行用唯一 basetemp**，不再复用固定名。
    # 原因：pytest 启动时会**先清空** basetemp。复用固定名 ⇒ 第二次运行必然要删上一轮
    # 留下的成堆文件 ⇒ 在受限沙箱里撞「单轮批量删除」护栏（LESSONS 202/207），
    # 表现为**整片 fixture 级 ERROR**（实测 2500 个用例里 2400+ 报 E，而非真实失败）。
    # 该缺陷在 CI 里不可见（每次全新 checkout ⇒ 目录恒不存在），但**本地第二次跑必中**。
    _stamp = time.strftime("%Y%m%d_%H%M%S")
    _pid = os.getpid()
    pytest_argv = [sys.executable, "-m", "pytest",
                   "--basetemp", os.path.join(root, f"{_BASETEMP_NAME}_{_stamp}_{_pid}")]
    if not full:
        pytest_argv += ["-m", FAST_MARKER_EXPR]
    if cov:
        pytest_argv += [
            f"--cov={root}",
            f"--cov-config={os.path.join(root, '.coveragerc')}",
            "--cov-report=term:skip-covered",
            f"--cov-fail-under={COVERAGE_FLOOR}",
        ]
    steps = [
        Step("pytest", pytest_argv,
             note=("全量套件" if full else "CI 等价子集（跳过 torch/network）")
                  + (f" + 覆盖率（地板 {COVERAGE_FLOOR}%）" if cov else ""),
             # 阶段 330：pytest 没有任何「跳过/环境」码——2=中断、3=内部错、
             # 4=用法错、5=收集到 0 条用例，全是事故，一律按失败处理。
             skip_codes=(), env_codes=()),
        Step("undef_scan", [sys.executable, os.path.join(root, "scripts", "undef_scan.py")],
             note="静态未定义名扫描（零依赖）"),
        Step("docker_static",
             [sys.executable, os.path.join(root, "scripts", "docker_consistency_check.py")],
             note="容器静态一致性（解释器/CUDA/依赖单一真值来源；零依赖、秒级）"),
        # 阶段 325：这是七道 --gates 里唯一能在 CI 口径下跑的一道——
        # 只要 numpy + opencv-headless（实测把 torch/torchvision/easyocr 全部屏蔽后仍 rc=0、10.2s），
        # 数据集又随仓库分发。放进默认步骤 ⇒ CI / publish / test.bat / test.sh 经**同一入口**自动获得
        # 这道效果类守卫；**不往 ci.yml 里另写一条命令**（那正是 test_entry_surfaces_... 防的入口副本）。
        Step("corner_signal_gate",
             [sys.executable, os.path.join(root, "scripts", "corner_signal_gate.py")],
             note="角标信号门禁（真实图集 × 生产信号；min 依赖可跑、约 10 秒；缺数据集时 rc=2 放行）"),
    ]
    if gates:
        steps += [
            Step("clean_bench_probe",
                 [sys.executable, os.path.join(root, "scripts", "clean_bench_probe.py"),
                  "--root", os.path.join(root, "benchmark_real", "clean")],
                 note="干净图误报门禁（退出码 0/1/2）"),
            Step("quality_gate",
                 [sys.executable, os.path.join(root, "scripts", "quality_gate.py")],
                 note="合成图质量门禁"),
            Step("real_gate",
                 [sys.executable, os.path.join(root, "scripts", "real_gate.py"),
                  "--strict-scope"],
                 note="真实图红线 + 漂移（严格范围）"),
            Step("perf_probe",
                 [sys.executable, os.path.join(root, "scripts", "perf_probe.py")],
                 note="性能探针（检测阶段 P95 回归护栏；未标定时退出码 2）"),
            # 阶段 315：补齐「全源」承诺的验证缺口——既有三门禁全是静态图，
            # PDF/视频链路长期「有代码、有单测、无端到端验证」。
            Step("media_gate",
                 [sys.executable, os.path.join(root, "scripts", "media_gate.py")],
                 note="PDF/视频链路门禁（真实图合成样本；防静默丢页·丢帧·跑完不干活）"),
            # 阶段 322a：把「擦得干净」从诊断升成门禁。既有多媒体/静态图门禁都只测
            # **检测侧**或**产物合法性**；修复侧唯一的量化承诺（只动水印区 + 擦除有增益）
            # 此前只有探针、无人拦截 ⇒ 用像素级真值补上第四道红线。
            Step("removal_truth_gate",
                 [sys.executable, os.path.join(root, "scripts", "removal_truth_probe.py"), "--gate"],
                 note="修复侧真值门禁（真照片自带构造保证的像素真值；检回/擦除度/净伤害/误伤四条红线）"),
            # 阶段 322b 建的 `corner_signal_gate` **不在这里重复**（阶段 325 起它是默认步骤，
            # `--gates` 会连默认一起跑 ⇒ 写两遍就是跑两遍）。
        ]
    return steps


#: 总结句措辞的唯一真值来源（阶段 328：环境错误必须被说出来，不能被印成「全部通过」）。
STEP_MARKS = {0: "✅", 1: "❌", 2: "⏭", 3: "🚧"}


VERDICTS = {
    0: "✅ 全部通过",
    1: "❌ 有步骤未通过",
    2: "⏭ 全部跳过",
    3: "🚧 环境错误（缺依赖/资产），未做判定 —— **不是通过**",
}


def aggregate(rcs: list) -> int:
    """把各步骤的**判定码**（已由 `normalize_rc` 按各自约定归一）聚合成总退出码。

    语义：**失败优先**，且**默认拒绝未知码**。四档含义（与门禁家族契约一致）：

      * ``1`` 有步骤判「质量不合格」（最高优先级）
      * ``3`` 有步骤报「环境错误」（如 `real_gate` 缺关键依赖）——**绝不能当通过**
      * ``2`` 全部步骤都「跳过」（依赖/资产缺失，无事可守）
      * ``0`` 至少一步真通过且无上述情形

    阶段 328 修的正是这里：旧实现只把 ``rc == 1`` 当失败 ⇒ ``real_gate`` 用退出码 3
    表达「环境错误」时会被聚合成 **0**，同一份输出里那行是 ❌、总结句却是「✅ 全部通过」，
    而发布链跑的就是这条命令（`publish.yml`）。凡不在约定集合 {0,1,2,3} 内的码
    （127 找不到解释器、崩溃、被信号杀掉）一律按失败处理：**说不清的结果不是通过**。
    """
    if not rcs:
        return 2
    if any(rc == 1 for rc in rcs):
        return 1
    if any(rc == 3 for rc in rcs):
        return 3
    if any(rc not in (0, 2) for rc in rcs):
        return 1
    if all(rc == 2 for rc in rcs):
        return 2
    return 0


def marker_expr_in(argv: list) -> "str | None":
    """从 pytest 命令里取出 `-m <expr>` 的表达式；没有则返回 None。

    为什么需要这个助手：本入口用 `python -m pytest ...` 调用（idiomatic），
    于是 argv 里**有两个不同语义的 `-m`**：模块调用（在 `pytest` **之前**）与
    marker 选择（在 `pytest` **之后**）。朴素 `argv.index("-m")` 会取到前者 ⇒ 误判。
    解析逻辑放在这里做**单一来源**，测试只断言本函数的结果。
    """
    try:
        i = argv.index("pytest")
    except ValueError:
        return None
    rest = argv[i + 1:]
    for j, tok in enumerate(rest):
        if tok == "-m" and j + 1 < len(rest):
            return rest[j + 1]
    return None


def normalize_rc(st: Step, rc: int) -> int:
    """把「这一步自己约定的退出码」翻译成入口的统一四档（0/1/2/3）。

    为什么按步骤翻译而不是全局一张表（阶段 330）：门禁家族约定 2=跳过、3=环境错误，
    而 pytest 的 2/3/4/5 全是事故。329 之后 CI 真的开始读这个码，若沿用全局约定，
    CI 上一次**收集期 ImportError**（min 依赖缺包正是这个形态）会被判成
    「⏭ 跳过 ⇒ 全部通过」——把最该报警的情形洗成绿。
    """
    if rc == 0:
        return 0
    if rc in st.env_codes:
        return 3
    if rc in st.skip_codes:
        return 2
    return 1


def run_steps(steps: list, *, root: str = _ROOT, runner=None, echo=None):
    """依次执行步骤，返回 (总退出码, 打印行, 报告)。

    `runner` 可注入（测试用假实现），签名与 `subprocess.run` 兼容；
    `echo` 可注入打印函数（默认 print）。
    """
    import subprocess as _sp
    runner = runner or _sp.run
    echo = echo or print
    lines, report = [], []
    verdicts = []
    for st in steps:
        started = time.time()
        proc = runner(st.argv, cwd=root, capture_output=True, text=True)
        rc = getattr(proc, "returncode", 1)
        dt = time.time() - started
        verdict = normalize_rc(st, rc)
        verdicts.append(verdict)
        mark = STEP_MARKS.get(verdict, "❌")
        # 原始码始终印出来：判定码和脚本码不一致时，读日志的人要能看见是**谁**改的口径。
        shown = f"rc={rc}" if verdict == rc else f"rc={rc}→判{verdict}"
        lines.append(f"{mark} {st.name:20s} {shown:11s} {dt:6.1f}s  {st.note}")
        report.append({"name": st.name, "rc": rc, "verdict": verdict,
                       "seconds": round(dt, 1), "argv": list(st.argv)})
        out = (getattr(proc, "stdout", "") or "").strip()
        if rc != 0 and out:
            tail = out.splitlines()[-12:]
            lines.append("   └─ 末尾输出：")
            lines += [f"      {t}" for t in tail]
    total = aggregate(verdicts)
    return total, lines, {"steps": report, "exit_code": total}


def main(argv: "list | None" = None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="项目验证入口（本地与 CI 同源）")
    ap.add_argument("--full", action="store_true",
                    help="跑全量 pytest（默认只跑 CI 等价子集：跳过 torch_required / network）")
    ap.add_argument("--gates", action="store_true",
                    help="追加门禁步骤（clean_bench_probe / quality_gate / real_gate / perf_probe，需 torch、耗时）")
    ap.add_argument("--cov", action="store_true",
                    help=f"pytest 追加覆盖率统计与地板判定（地板 {COVERAGE_FLOOR}%%，口径见 .coveragerc）")
    ap.add_argument("--list", action="store_true",
                    help="只列出将要执行的步骤，不执行（退出码 2）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    steps = build_steps(full=args.full, gates=args.gates, root=_ROOT, cov=args.cov)

    print("=" * 78)
    print("验证入口 run_all_checks · 步骤清单")
    for st in steps:
        print(f"  · {st.name:20s} {st.note}")
    print("=" * 78)

    if args.list:
        print("（--list：仅列步骤，不执行）")
        return 2

    total, lines, rep = run_steps(steps, root=_ROOT)
    print()
    for ln in lines:
        print(ln)
    print()
    verdict = VERDICTS.get(total, "❌ 有步骤返回了约定外的退出码")
    print(f"{verdict}（退出码 {total}）")
    return total


if __name__ == "__main__":
    raise SystemExit(main())
