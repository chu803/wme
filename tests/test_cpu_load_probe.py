#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 224 · CPU 负载探针的守卫（判据本身的正确性）。

★ 为何要测「判据」
------------------
阶段 224 我用 `busy = kernel + user` 读`GetSystemTimes`，得出「占用 51.8%」，
据此差点判定「有外部负载、当前基线不可信」——**这是凭空制造出来的干扰源**
（会进一步去优化不存在的瓶颈，LESSONS 233）。根因是 **Windows 的 kernel 含 idle**。
本文件把「判据正确」钉死，防止后人（或我自己）再犯同一处公式错误。
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import cpu_load as cl  # noqa: E402

#: cpu_load 探针依赖 Windows 专有 ctypes（ctypes.windll / ctypes.WinDLL）；
#: 非 Windows 平台（如 CI 的 ubuntu）这些符号不存在 ⇒ 相关用例必须跳过，
#: 否则以 AttributeError 假失败（仓库发布后首次 Linux CI 暴露，见 docs/300）。
_IS_WINDOWS = sys.platform.startswith("win")
_win_only = pytest.mark.skipif(
    not _IS_WINDOWS,
    reason="cpu_load 探针走 Windows ctypes（windll/WinDLL），非 Windows 平台跳过")


# ==============================================================================
# ① 公式：必须扣掉 kernel 里的 idle
# ==============================================================================
def test_module_doc_states_the_kernel_includes_idle():
    """文档必须写明 kernel 含 idle —— 这是 224 踩坑的根因，不该被后人重犯。"""
    doc = cl.__doc__ or ""
    assert "kernel" in doc and "idle" in doc
    assert "包含" in doc or "含idle" in doc.replace(" ", ""), (
        "必须明确写出「kernel 包含 idle」这一 Windows 语义")


@_win_only
def test_sample_is_reliable_on_this_machine():
    """在真实机器上采样必须 `reliable=True`。

    回归依据：阶段 224 我把重复计数自检的上界写成 1×N，而正确上界是 2×N
    （kernel 含 idle ⇒ idle 最多被重复计一次），结果**任何正常采样都被判
    「计时域异常」**（实测三者和 131.47核·秒 vs 阈值 24）。此处钉死「不再误报」。
    """
    s = cl.CpuLoadProbe().sample(1.0)
    assert s.reliable, (
        f"正常机器上采样被判不可靠：{s.note}"
        f"（疑似重复计数上界写错——上界应为 2×N 而非 1×N）")


@_win_only
def test_busy_pct_within_valid_range():
    """占用率必须落在 [0, 100]，且 busy% + idle% ≈ 100。"""
    s = cl.CpuLoadProbe().sample(1.0)
    if not s.reliable:
        pytest.skip(f"本次采样不可靠：{s.note}")
    assert 0.0 <= s.busy_pct <= 100.0
    assert 0.0 <= s.idle_pct <= 100.0
    assert abs(s.busy_pct + s.idle_pct - 100.0) < 1.0, (
        f"busy({s.busy_pct:.1f}) + idle({s.idle_pct:.1f}) 应≈100")


@_win_only
def test_busy_cores_within_physical_limit():
    """折算的忙核数不得超过物理核数（否则说明重复计数未扣干净）。"""
    n = os.cpu_count() or 1
    s = cl.CpuLoadProbe().sample(1.0)
    if not s.reliable:
        pytest.skip("本次采样不可靠")
    assert s.busy_cores <= n * 1.02, (
        f"忙核数 {s.busy_cores:.2f} 超过物理上限 {n} ⇒ 重复计数未扣干净")


# ==============================================================================
# ② 注入式反例：把「错误公式」显式构造出来，证明探针不会踩坑
# ==============================================================================
@_win_only
def test_wrong_formula_would_overestimate_is_documented():
    """**反向验证**：证明 `kernel + user` 确实是错的（否则本守卫只是形式主义）。

    构造一次采样，把两种公式都算出来：
        错： (kernel + user) / (kernel + user)  = 100%
        对： (kernel - idle + user) / (kernel + user)
    由于 idle > 0，两者必然不等 ⇒ 坐实「错公式恒等于 100%，毫无信息量」。
    """
    probe = cl.CpuLoadProbe()
    i0, k0, u0 = cl._get_system_times()
    import time as _t
    _t.sleep(1.0)
    i1, k1, u1 = cl._get_system_times()
    di, dk, du = i1 - i0, k1 - k0, u1 - u0
    total = dk + du
    if total <= 0 or di <= 0:
        pytest.skip("本次采样异常，跳过反向验证")
    wrong = 100.0 * (dk + du) / total          # = 100%，恒等于满载
    right = 100.0 * (dk - di + du) / total
    assert wrong == 100.0, (
        "错公式 kernel+user/kernel+user 恒等于 100%——这正是它毫无信息量的证明")
    assert right < 100.0, "正确公式应给出< 100% 的真实占用"


@_win_only
def test_idle_is_counted_twice_in_raw_sum():
    """三者和必然**超过** 1×N（因idle 被 kernel 重复包含）⇒ 判据上界须为 2×N。"""
    n = os.cpu_count() or 1
    probe = cl.CpuLoadProbe()
    i0, k0, u0 = cl._get_system_times()
    import time as _t
    _t.sleep(1.0)
    i1, k1, u1 = cl._get_system_times()
    di, dk, du = i1 - i0, k1 - k0, u1 - u0
    ratio = (di + dk + du) / (cl._TICKS_PER_SEC * n)
    assert ratio > 1.0, (
        "三者和/单核满载量 应 > 1（重复计数的证据）；"
        f"实测 {ratio:.2f}——若此断言失败说明平台语义与Windows 不同，需重审探针")


# ==============================================================================
# ③ 判定分层：给的是「能不能标定基线」，不是裸数字
# ==============================================================================
def test_verdict_distinguishes_three_levels():
    """空闲 / 轻度占用 / 明显负载 三档必须可区分（决策需要，不只是数字）。"""
    s = cl.CpuSample(busy_pct=3.0, idle_pct=97.0, busy_cores=0.7,
                     idle_cores=23.3, seconds=1.0, reliable=True)
    assert "✅" in s.verdict()
    s2 = cl.CpuSample(busy_pct=15.0, idle_pct=85.0, busy_cores=3.6,
                      idle_cores=20.4, seconds=1.0, reliable=True)
    assert "⚠" in s2.verdict()
    s3 = cl.CpuSample(busy_pct=60.0, idle_pct=40.0, busy_cores=14.4,
                      idle_cores=9.6, seconds=1.0, reliable=True)
    assert "❌" in s3.verdict()


def test_unreliable_sample_never_claims_idle():
    """`reliable=False` 时**绝不能**输出「可标定基线」（宁可拒绝、不可假装）。"""
    s = cl.CpuSample(busy_pct=0.0, idle_pct=0.0, busy_cores=0.0, idle_cores=0.0,
                     seconds=1.0, reliable=False, note="测试注入")
    v = s.verdict()
    assert "✅" not in v, "不可靠数据不得声称可标定基线"
    assert "测试注入" in v, "必须把不可靠原因带出来（便于排查）"


# ==============================================================================
# ④ 交叉验证：逐进程 vs 系统级
# ==============================================================================
@_win_only
def test_process_and_system_estimates_are_same_order():
    """两个来源必须**同量级**（各自都会偏差：逐进程低估、系统级含驱动态）。

    这是 224 用来定案「机器空闲」的关键交叉证据，故固化为守卫：
    若二者差一个数量级，说明其中一个的口径坏了。
    """
    probe = cl.CpuLoadProbe()
    # 阶段 314b：两个采样必须**并发**（各 1.5s）。旧实现串行取，两次落在不同的负载窗口里；
    # 全量跑时（本机 CPU 持续负载降频）窗口一错位，比值就假性破 10× —— 实测全量偶发红、
    # 隔离跑必绿。并发后两者看到同一段负载，判据才是在比「口径」而不是比「时间」。
    box: dict = {}

    def _system() -> None:
        box["s"] = probe.sample(1.5)

    def _process() -> None:
        box["p"] = cl.process_busy_pct(1.5)

    threads = [threading.Thread(target=_system), threading.Thread(target=_process)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s, p = box.get("s"), box.get("p")
    if s is None or not s.reliable:
        pytest.skip(f"系统级采样不可靠：{getattr(s, 'note', '无结果')}")
    if p is None or p <= 0.0:
        pytest.skip("逐进程枚举不可用（权限限制），跳过交叉验证")
    # 低负载区比值没有意义：分母趋近 0 时 1% vs 0.1% 也是 10×，但两者都只是噪声。
    # 224 定案「机器空闲」用的是**高负载**证据，低负载场景本就不该用比值判口径。
    if s.busy_pct < 5.0:
        pytest.skip(f"系统负载过低（{s.busy_pct:.1f}% < 5%），比值判据不适用")
    hi, lo = max(s.busy_pct, p), max(min(s.busy_pct, p), 0.1)
    assert hi / lo < 10.0, (
        f"系统级 {s.busy_pct:.1f}% 与逐进程 {p:.1f}% 差一个数量级"
        f"⇒ 至少一个口径已损坏")


@_win_only
def test_repeated_sampling_is_stable_enough():
    """同机连采多次，占用率不应剧烈跳动（若跳动说明时钟域有问题）。"""
    probe = cl.CpuLoadProbe()
    vals = [s.busy_pct for s in probe.sample_repeated(3, 1.0) if s.reliable]
    if len(vals) < 2:
        pytest.skip("可靠采样不足")
    spread = max(vals) - min(vals)
    assert spread < 30.0, f"同机连采占用率极差 {spread:.1f}pp 过大⇒ 计时域不稳定"
