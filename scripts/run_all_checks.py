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
#   ③ 门禁步骤       —— 仅 `--gates` 时追加（需 torch / 耗时）：
#                        clean_bench_probe（干净图误报）· quality_gate（合成红线）
#                        · real_gate --strict-scope（真实图红线 + 漂移）
#                        · perf_probe（检测阶段 P95 回归护栏）
#
# 退出码（与门禁家族契约一致）
# ----------------------------
#   0 = 全部通过
#   1 = 有任一步骤判「未通过」
#   2 = 跳过（`--list` 只列步骤；或所有步骤都判「跳过」）
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
    """一个可执行的检查步骤。"""

    name: str
    argv: list = field(default_factory=list)
    note: str = ""
    header: str = ""          # 打印用的额外说明


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
    pytest_argv = [sys.executable, "-m", "pytest",
                   "--basetemp", os.path.join(root, _BASETEMP_NAME)]
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
                  + (f" + 覆盖率（地板 {COVERAGE_FLOOR}%）" if cov else "")),
        Step("undef_scan", [sys.executable, os.path.join(root, "scripts", "undef_scan.py")],
             note="静态未定义名扫描（零依赖）"),
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
        ]
    return steps


def aggregate(rcs: list) -> int:
    """把各步骤退出码聚合成总退出码（0 通过 / 1 有失败 / 2 全跳过）。

    语义：**失败优先**；只有「全部步骤都跳过」时才返回 2。
    """
    if not rcs:
        return 2
    if any(rc == 1 for rc in rcs):
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


def run_steps(steps: list, *, root: str = _ROOT, runner=None, echo=None):
    """依次执行步骤，返回 (总退出码, 打印行, 报告)。

    `runner` 可注入（测试用假实现），签名与 `subprocess.run` 兼容；
    `echo` 可注入打印函数（默认 print）。
    """
    import subprocess as _sp
    runner = runner or _sp.run
    echo = echo or print
    lines, report = [], []
    rcs = []
    for st in steps:
        started = time.time()
        proc = runner(st.argv, cwd=root, capture_output=True, text=True)
        rc = getattr(proc, "returncode", 1)
        dt = time.time() - started
        rcs.append(rc)
        mark = {0: "✅", 1: "❌", 2: "⏭"}.get(rc, "❌")
        lines.append(f"{mark} {st.name:20s} rc={rc}  {dt:6.1f}s  {st.note}")
        report.append({"name": st.name, "rc": rc, "seconds": round(dt, 1),
                       "argv": list(st.argv)})
        out = (getattr(proc, "stdout", "") or "").strip()
        if rc != 0 and out:
            tail = out.splitlines()[-12:]
            lines.append("   └─ 末尾输出：")
            lines += [f"      {t}" for t in tail]
    total = aggregate(rcs)
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
                    help=f"pytest 追加覆盖率统计与地板判定（地板 {COVERAGE_FLOOR}%，口径见 .coveragerc）")
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
    verdict = {0: "✅ 全部通过", 1: "❌ 有步骤未通过", 2: "⏭ 全部跳过"}[total]
    print(f"{verdict}（退出码 {total}）")
    return total


if __name__ == "__main__":
    raise SystemExit(main())
