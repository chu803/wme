#!/usr/bin/env python3
# ==============================================================================
#  阶段 212 · 性能门禁「测量可信度」闸门的守卫
# ------------------------------------------------------------------------------
#  缺陷背景（阶段 211 末段发现，登记为缺陷 C）
#  ------------------------------------------
#  `--update` 刚把 P95 标定到 0.681s，**紧接着**跑纯判定却报 P95 1.564s、判「❌ 超护栏」。
#  用同进程 A/B 对照证伪（「朴素 3.281s vs 新版 1.581s」、10/10 输出逐位相同）确认
#  **绝对值整体抬高约 1.5× 是机器负载漂移，而非代码回退**。
#
#  根因：`min-of-k` 只滤**run 内**尖峰，滤不掉**跨 run** 的环境漂移。
#
#  本阶段做了什么
#  --------------
#  在「可比性」与「回归护栏」之间插入**第 2 层：测量可信度**，
#  判据 = `spread_mean / P95`（run 内相对噪声，**完全内生、零新增采集成本**）。
#  实测标定：正常轮 0.016~0.083，受干扰轮 0.346（高 21 倍）⇒ 阈值 NOISE_MAX=0.20。
#
#  刻意的设计约束（守卫重点守护的对象）
#  ------------------------------------
#  ① 噪声高 ⇒ **判跳过(2)，绝不判通过(0)**。噪声高时我们**不知道**真实性能是多少，
#     任何断言都是编造。这不是放水，是把「红」的归因分开。
#  ② 噪声**拿不到** ⇒ 也判跳过。`spread_mean` 缺失是**没有证据**，门禁在无证据
#     时必须保守；否则「忘记采集噪声」会变成绕过护栏的后门。
#  ③ `--update` 同样受噪声闸门约束，且**没有逃生门**（连 `--force-update` 也不放过）
#     ——否则一次受干扰的慢测量会刷进基线、**永久摧毁**性能参照。
#  ④ `skip_noise_gate` 仅供纯函数单测，**生产路径 `main()` 绝不传 True**。
# ==============================================================================
from __future__ import annotations

import inspect
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import perf_probe as pp  # noqa: E402

BASELINE_PATH = os.path.join(_ROOT, "benchmark_real", "perf_baseline.json")


def _base(p95=1.0):
    return {"version": pp.BASELINE_VERSION, "p95": p95, "source": "benchmark",
            "stage": "detect", "repeat": 3, "device": "cuda", "gpu_name": "X",
            # 阶段 215：补 engine/runs 两个可比性维度（缺任一 ⇒ 判「结构过旧」）。
            "engine": None, "runs": 10}

def _meta(spread):
    m = {"source": "benchmark", "stage": "detect", "repeat": 3,
         "device": "cuda", "gpu_name": "X",
         "engine": None, "runs": 10}
    if spread is not None:
        m["spread_mean"] = spread
    # 阶段 214：默认给一个「无渐进劣化」的锚点比，让本文件专注于**噪声层**。
    # 锚点层本身由 tests/test_perf_anchor_gate.py 专项覆盖。
    # ⚠ 阶段 214 起`gate_decision` 对**缺锚点数据**判跳过（无证据须保守），
    #   故所有构造 meta 的用例都必须显式给出该字段——否则会被第 2b 层挡住、
    #   根本测不到噪声层（第 3 层），用例就变成了「假绿」。
    m.setdefault(pp.ANCHOR_KEY, 0.97)
    return m


# ==============================================================================
# ① noise_ratio 计算
# ==============================================================================
@pytest.mark.parametrize("spread,p95,expected", [
    (0.016, 1.0, 0.016),
    (0.083, 0.5, 0.166),
    (0.2, 1.0, 0.2),
])
def test_noise_ratio_is_spread_over_p95(spread, p95, expected):
    assert pp.noise_ratio({"p95": p95}, {"spread_mean": spread}) == \
        pytest.approx(expected)


@pytest.mark.parametrize("stats,meta", [
    ({"p95": 0.0}, {"spread_mean": 0.1}),   # P95 为 0
    ({"p95": 1.0}, {}),                      # 缺 spread_mean
    ({"p95": 1.0}, {"spread_mean": None}),   # 显式 None
])
def test_noise_ratio_returns_none_when_unmeasurable(stats, meta):
    """拿不到噪声必须返回 `None`（**不得**返回 0）。

    返回 0 会让调用方把「没测到噪声」当成「没有噪声」⇒ 静默放行。
    """
    assert pp.noise_ratio(stats, meta) is None


# ==============================================================================
# ② 噪声闸门：判跳过，绝不判通过
# ==============================================================================
def test_clean_measurement_passes_rail():
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 1.0}, _meta(0.016))
    assert code == 0, reasons
    assert any("在护栏内" in r for r in reasons)


def test_noisy_measurement_skips_not_passes():
    """受污染 ⇒ 退出码 2（跳过），**绝不能是 0**。"""
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 0.5}, _meta(0.346))
    assert code == 2, f"噪声测量必须判跳过，实际 {code}：{reasons}"
    assert any("测量被环境污染" in r for r in reasons)


def test_noisy_measurement_skips_even_when_p95_is_beautiful():
    """最关键的反例：P95 极漂亮**也不能**判通过。

    没有噪声数据就没有任何证据说这次测量可信——否则「噪声大 + 数字好看」
    就能绕过护栏，这正是闸门要防的漏洞。
    """
    code, _ = pp.gate_decision(_base(1.0), {"p95": 0.01}, _meta(0.9))
    assert code == 2, "噪声测量即使 P95 极小也不得判通过（否则成为绕过护栏的后门）"


def test_noisy_measurement_skips_even_when_p95_is_tiny_but_under_rail():
    """噪声大 + 未超护栏 ⇒ 仍判跳过（不能因「反正没超护栏」就放行）。"""
    code, _ = pp.gate_decision(_base(5.0), {"p95": 1.0}, _meta(0.5))
    assert code == 2


def test_missing_noise_data_skips_conservatively():
    """**拿不到**噪声 ⇒ 判跳过（保守），不是放行。"""
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 1.0}, _meta(None))
    assert code == 2, f"无噪声证据必须保守判跳过，实际 {code}：{reasons}"
    assert any("无法计算测量噪声" in r for r in reasons)


def test_repeat_one_does_not_hard_block_only_warns():
    """`repeat=1`：噪声判据失去区分力，但**不得硬拦**——它是配置问题，不是污染问题。

    阶段 212 自我修正的记录（别加回来）：本守卫最初要求 repeat=1 判跳过，
    实测发现这是**过度设计**：
      · 硬拦会把「合法的、配置不当的」测量与「被污染的测量」混为一谈；
      · 更糟的是它会**掩盖真实回归**——若代码真的慢了 20%，用 `--repeat 1` 跑
        本该报红，却因「噪声无法计算」被判跳过 ⇒ 红色消失。
    故正确处置：**不硬拦，但在输出里如实提示**（`⚠️ repeat=N 噪声判据无法工作`）。
    """
    base = {"version": pp.BASELINE_VERSION, "p95": 1.0, "source": "benchmark",
            "stage": "detect", "repeat": 1, "device": "cuda", "gpu_name": "X",
            "engine": None, "runs": 10}
    meta = {"source": "benchmark", "stage": "detect", "repeat": 1,
            "device": "cuda", "gpu_name": "X", "spread_mean": 0.0,
            "engine": None, "runs": 10,
            # 阶段 214：补锚点比，否则会被第 2b 层（缺锚点 ⇒ 跳过）挡住，
            # 根本测不到「repeat=1 不硬拦」这个本例要守的行为。
            pp.ANCHOR_KEY: 0.97}
    code, _ = pp.gate_decision(base, {"p95": 1.0}, meta)
    assert code == 0, (
        f"repeat=1 是**配置问题**不应硬拦（否则掩盖真实回归）；实际退出码 {code}")

    # 但输出**必须**有诚实提示，不得让 0.0 被读成「完美干净」
    src = inspect.getsource(pp.main)
    assert "噪声判据无法工作" in src, "repeat<2 必须有明确提示（不能静默）"
    assert "不等于" in src, "提示需澄清「极差 0 ≠ 环境干净」"


def test_steady_high_load_is_not_caught_but_is_harmless():
    """**钉死本方案的能力边界**（阶段 212 反向验证实测结论）。

    实测：24 进程真并行压满 24 核时
      · 相对噪声仍只有 ≈0.062（**低于** 阈值 0.20）⇒ 闸门放行；
      · 但 P95 仅从 0.661s 抬到 0.754s = **1.141×**，远小于 30% 护栏容差
        ⇒ **稳态高负载本来就不会让护栏误判红**。
    ⇒ 该闸门只抓「瞬态污染」，不是负载检测器。这个断言的意义：若将来有人把
    NOISE_MAX 调低去「抓稳态负载」，本例会提醒他那是徒劳且会误伤。
    """
    meta = {"source": "benchmark", "stage": "detect", "repeat": 3,
            "device": "cuda", "gpu_name": "X", "spread_mean": 0.062,
            "engine": None, "runs": 10,
            pp.ANCHOR_KEY: 0.9731}  # 阶段 214：稳态负载下锚点比实测 ≈0.97（不越界）
    code, reasons = pp.gate_decision(_base(1.0), {"p95": 1.14}, meta)
    assert code == 0, "稳态负载（噪声比 0.062）应放行——它只抬高 14%，在护栏容差内"

    # 阈值必须**高于**稳态负载的噪声比，否则会误伤正常测量
    assert pp.NOISE_MAX > 0.062, (
        f"NOISE_MAX={pp.NOISE_MAX} 低于实测稳态负载噪声比 0.062，"
        f"会把正常的高负载测量误判为污染")


def test_noise_gate_precedes_regression_rail():
    """噪声闸门必须**先于**护栏生效：受污染时即便真回退，也先归因为「测量不可信」。

    否则同一个红会被当成「代码变慢」，导致开发者回滚无辜代码。
    ⚠ 造数要「噪声比真的超标」：`spread_mean / p95 > 0.20`。
    这里 p95=5.0、spread_mean=0.8 ⇒ 比值 0.16 < 0.20，闸门**正确**放行——
    这说明判据确实在算，而不是无条件拦截。
    """
    # 故意用「真回退 + 干净测量」先证明护栏会判红（第3 层仍工作）
    code_clean, reasons_clean = pp.gate_decision(_base(0.1), {"p95": 5.0},
                                                _meta(0.1))
    assert code_clean == 1, "干净测量下的真回退必须判红（护栏层仍须有效）"

    # 再用「真回退 + 受污染测量」证明归因被改为「测量不可信」
    code, reasons = pp.gate_decision(_base(0.1), {"p95": 5.0}, _meta(1.5))
    assert code == 2
    assert any("测量被环境污染" in r for r in reasons)
    assert not any("超护栏" in r for r in reasons), \
        "受污染测量不应同时报「超护栏」（会把归因引向代码回退）"


# ==============================================================================
# ③ 生产路径不得使用 skip_noise_gate
# ==============================================================================
def test_production_main_never_passes_skip_noise_gate():
    """`main()` 是生产入口，**绝不**能关掉噪声闸门。

    守卫意义：若有人为图省事在 main() 里传 `skip_noise_gate=True`，
    整道闸门会静默失效——而这正是缺陷 C 想解决的东西。
    """
    src = inspect.getsource(pp.main)
    body = src.split("def main", 1)[-1]
    # 允许出现在注释/docstring 说明里，但不允许作为实参出现
    code_only = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    offenders = [ln.strip() for ln in code_only.splitlines()
                 if "skip_noise_gate" in ln]
    assert not offenders, f"main() 不得传skip_noise_gate（噪声闸门会失效）：{offenders}"


def test_skip_noise_gate_is_explicitly_test_only_in_docstring():
    """该参数必须自我声明「仅测试用」，避免后人误用。"""
    doc = inspect.getdoc(pp.gate_decision) or ""
    assert "仅供单元测试" in doc
    assert "生产路径" in doc, "必须明确写出生产路径不传该参数"


# ==============================================================================
# ④ --update 路径：无逃生门
# ==============================================================================
def test_update_path_also_guards_against_polluted_baseline():
    """`--update` 必须同样受噪声闸门约束（否则污染值会固化进参照）。"""
    src = inspect.getsource(pp.main)
    assert "_nr_upd" in src, "--update 路径必须独立复查噪声（不能只靠判定层）"
    assert "NOISE_MAX" in src.split("已写入基线")[0], \
        "噪声闸门必须排在写基线之前（否则污染值先落盘）"


def test_update_source_has_noise_gate_before_baseline_write():
    """`--update` 必须在写基线**之前**有噪声闸门，且**不放宽** force_update。"""
    src = inspect.getsource(pp.main)
    body = src.split("def main", 1)[-1]
    # 闸门标记应出现在「已写入基线」之前
    i_gate = body.find("测量被环境污染")
    i_write = body.find("已写入基线")
    assert i_gate != -1, "main() 的 --update 路径缺少噪声闸门"
    assert i_write != -1
    assert i_gate < i_write, "噪声闸门必须排在写基线之前（否则污染值会先落盘）"


def test_update_noise_gate_has_no_escape_hatch():
    """噪声闸门**不得**被 `--force-update` 绕过。

    注意：不能用「取固定长度窗口」来找闸门代码——窗口会跨界扫到**后面**的
    另一处 `force_update`（可比性护栏用），造成假失败（本次实测踩到）。
    正确做法是**按行结构**定位：找到打印噪声拒绝的那行，扫到该`if` 块结束。
    """
    src = inspect.getsource(pp.main)
    lines = src.splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if "测量被环境污染" in ln), None)
    assert idx is not None, "main() 的 --update 路径缺少噪声闸门"

    # 从噪声闸门行之后开始，收集到**缩进严格变浅**处（即整个 if 块结束）。
    # ⚠ 用 `<` 而非 `<=`：`return 1` 与触发行**同级**（同属该 if 块），
    #   用 `<=` 会把 `return 1` 本身当成「块结束」而截掉（实测踩到）。
    #   注释行（`#`）缩进可能更浅但仍在块内，需跳过。
    base_indent = len(lines[idx]) - len(lines[idx].lstrip())
    seg = []
    for ln in lines[idx + 1:]:
        if not ln.strip():
            seg.append(ln)
            continue
        ind = len(ln) - len(ln.lstrip())
        if ind < base_indent:
            break
        seg.append(ln)
    block = lines[idx] + "\n" + "\n".join(seg)

    assert "args.force_update" not in block, \
        "噪声闸门不得被 --force-update 绕过（否则污染值会摧毁性能参照）"
    assert "return 1" in block, "噪声闸门必须拒绝写入（返回非 0）"


# ==============================================================================
# ⑤ 阈值本身有据可依
# ==============================================================================
def test_noise_max_is_in_the_measured_gap():
    """阈值必须落在实测两组之间，不能是拍脑袋的数。

    阶段 212 实测三组数据：
      · 正常轮：0.016 ~ 0.083
      · **稳态高负载轮：0.062**（24 进程压满 24 核——刻意抓不到，见上）
      · **瞬态受扰轮：0.346**（Stage 211 复现的 2.3× 假红）
    ⇒ 阈值须高于「稳态负载」（0.083，勿误伤）、低于「瞬态污染」（0.346，勿漏放）。
    """
    assert 0.083 < pp.NOISE_MAX < 0.346, (
        f"NOISE_MAX={pp.NOISE_MAX} 落在实测数据之外"
        f"（正常/稳态≤0.083，瞬态受扰≈0.346），说明阈值被随意改动过")


def test_noise_ratio_key_is_declared():
    """噪声比字段名提为常量（守卫与刷新规则都引用它）。"""
    assert isinstance(pp.NOISE_RATIO_KEY, str) and pp.NOISE_RATIO_KEY


# ==============================================================================
# ⑥ 落盘基线可审计
# ==============================================================================
@pytest.mark.skipif(not os.path.isfile(BASELINE_PATH), reason="性能基线尚未标定")
def test_on_disk_baseline_records_noise_for_audit():
    """基线应记录当次的相对噪声（供事后审计「这个基线是在多干净的环境标定的」）。"""
    with open(BASELINE_PATH, encoding="utf-8") as f:
        b = json.load(f)
    # 阶段 212 起新写入的基线应带该字段；旧基线缺失不判失败（版本机制会兜住）
    if pp.NOISE_RATIO_KEY in b:
        assert isinstance(b[pp.NOISE_RATIO_KEY], (int, float))
        assert b[pp.NOISE_RATIO_KEY] >= 0