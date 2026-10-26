# -*- coding: utf-8 -*-
"""
==============================================================================
 文件名称 : scripts/baseline_guard.py
 文件说明 : **基线刷新护栏** —— 三条门禁（`quality_gate` / `real_gate` /
            `e2e_real_probe`）共用，阶段 146 新增。

 【为什么需要它】
 三条门禁都支持「刷新基线」：把本次实测值写入快照，供下一轮做漂移比对。
 但**刷新动作本身**此前没有任何约束，实测确认两处结构性缺口：

 ① **两个主门禁的刷新是「静默通道」**
    `scripts/quality_gate.py` 与 `scripts/real_gate.py` 在写完基线后
    **直接 `return 0`**，**绕过**了绝对红线判定与数据完整性判定 ——
    用户看到「基线已刷新」+ 退出码 0，会以为一切正常。
    （对照：`scripts/e2e_real_probe.py` **已经**处理了这一点，它的注释写着
      「否则刷新基线的动作会绕过红线检查，成为一条静默通道」，刷新后继续走判定。
      两个主门禁是**漏了对齐**，不是新决策。）

 ② **刷新可以「向下」进行而无任何阻力**（三个门禁**全部**如此）
    没有任何机制阻止把某个指标**合法化到一个更坏的值**。而
    **只靠漂移层保护、没有绝对红线**的指标会被**渐进刷新**到任意低值：

        合成门禁 `recall`：基线 0.8188，`TOLERANCES["recall"] = 0.20`，
        且 **RED_LINES 里没有 `recall` 条目**（`precision` / `iou` 都有绝对红线）
           0.819 ──刷──> 0.655 ──刷──> 0.524 ──刷──> 0.419 ──> …
        每一步都在 20% 容差内 ⇒ **门禁全程绿**，而漏检率可以趋近 100%。

 ③ 本护栏**不新增任何阈值**：允许的波动范围直接复用各门禁**既有**的容差表
    （`TOLERANCES` / `REAL_TOLERANCES` / `E2E_DRIFT_TOL` + `E2E_DRIFT_ABS`），
    保证「漂移层允许的抖动」与「刷新层允许的下滑」**同口径** ——
    否则会出现「漂移层判它违规、刷新层却放它写入」的自相矛盾。

 【与技能判据的关系】
 `eval-blindspot-audit` 探针 8 的结论：修「元守卫的漏检」应当**优先加元级约束**，
 而不是逐条补漏检项 —— 逐条补只解决今天这几项，明天新增第 12 项照样漏。
 本护栏正是元级约束：它对**任何**被容差表覆盖的指标自动生效，
 未来新增指标只要进了容差表就自动受保护。
==============================================================================
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Tuple

#: 每个受保护指标的规则：(direction, rel_tol, abs_tol)
#:   direction : "min" = 越高越好（新值更低即劣化）；"max" = 越低越好（新值更高即劣化）
#:   rel_tol   : 允许的相对劣化比例（与各门禁既有的漂移容差同值）
#:   abs_tol   : 绝对劣化下限，用于**近零指标**（相对容差在零附近会乱响）；
#:               实际判定取 `max(abs_tol, |old| * rel_tol)` —— 即「两者较宽者」，
#:               与 `e2e_real_probe.E2E_DRIFT_ABS` 的既有语义一致。
Rule = Tuple[str, float, float]


def _judge(key: str, old_v: Any, new_v: Any, rule: Rule) -> "str | None":
    """判定单个指标是否为「向下刷新」；是则返回人话说明，否则返回 None。"""
    direction, rel_tol, abs_tol = rule
    if isinstance(old_v, bool) or isinstance(new_v, bool):
        # `isinstance(True, int)` 为真 —— 布尔配置项不得被当成指标比对
        # （阶段 145 家族 19 的同类陷阱）。
        return None
    if not isinstance(old_v, (int, float)) or not isinstance(new_v, (int, float)):
        return None

    # 允许幅度取「相对」与「绝对」的**较宽者**，与 `e2e_real_probe.E2E_DRIFT_ABS`
    # 的既有语义一致（近零指标单靠相对容差会乱响）。
    allowance = max(float(abs_tol), abs(float(old_v)) * float(rel_tol))
    if direction == "min":
        # 越高越好 ⇒ 新值低于「旧值 − 允许幅度」即为向下刷新。
        if float(new_v) < float(old_v) - allowance:
            return (f"{key} 向下刷新：{old_v} → {new_v}"
                    f"（劣化 {float(old_v) - float(new_v):.6g}，允许 {allowance:.6g}）")
    elif direction == "max":
        # 越低越好 ⇒ 新值高于「旧值 + 允许幅度」即为向下刷新。
        if float(new_v) > float(old_v) + allowance:
            return (f"{key} 向下刷新：{old_v} → {new_v}"
                    f"（劣化 {float(new_v) - float(old_v):.6g}，允许 {allowance:.6g}）")
    else:                                    # pragma: no cover - 规则表写错时早暴露
        raise ValueError(
            f"规则表里 {key} 的 direction 只能是 'min' / 'max'，收到 {direction!r}")
    return None


def check_downward_pairs(
    pairs: Iterable[Tuple[str, Any, Any]],
    rules: Mapping[str, Rule],
) -> list:
    """
    检查一组 (指标名, 旧值, 新值) 是否为「向下刷新」。

    **这是零结构假设的主接口** —— 三条门禁的基线结构各不相同
    （`quality_gate` 是扁平 dict、`real_gate` 是 `per_preset` / `area_only` 两层嵌套、
    `e2e_real_probe` 是按 case 的 list），因此「如何取出各指标」由**调用方**
    用它自己既有的遍历逻辑完成，本模块只负责判定。
    这样避免在本模块里再造一份「指标从哪取」的口径（那是各门禁的结构知识）。

    参数说明：
        pairs : 可迭代的 (key, old_value, new_value)。key 需在 rules 中有规则，
                否则跳过（未列入规则表的指标不受本护栏约束）。
        rules : key → (direction, rel_tol, abs_tol)。

    返回值：
        违规说明列表，空表示允许刷新。
    """
    problems: list = []
    for key, old_v, new_v in pairs:
        rule = rules.get(key)
        if rule is None:
            continue
        msg = _judge(key, old_v, new_v, rule)
        if msg:
            problems.append(msg)
    return problems


def check_downward_refresh(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
    rules: Mapping[str, Rule],
) -> list:
    """
    扁平 dict 场景的便捷封装（`quality_gate` 的 aggregate 就是扁平 dict）。

    参数说明：
        old   : **旧**基线的聚合字典（指标名 → 数值）。为空/None 视为首次建基线。
        new   : 本次将要写入的聚合字典。
        rules : 指标名 → (direction, rel_tol, abs_tol)。未列入的键不受约束。

    返回值：
        违规说明列表。**空列表表示允许刷新**；非空表示应拒绝写入。

    设计约束（刻意为之）：
        · **只检查两侧都有的键**：新基线多出的指标没有旧值可比 ⇒ 放行；
          新基线少掉的键属于**数据完整性**问题，交由各门禁自身的完整性判定报告
          （本护栏不越界，避免同一事实两处判定、口径漂移）。
        · **不做任何写盘**：纯函数，调用方自行决定拒绝还是 `--force` 写入。
    """
    if not old:
        return []                            # 首次建基线：无旧值可比，放行
    pairs = [(k, old[k], new[k]) for k in rules if k in old and k in new]
    return check_downward_pairs(pairs, rules)


def render_refusal(
    problems: Iterable[str],
    *,
    baseline_label: str = "基线",
    force_flag: str = "--force-update",
) -> list:
    """
    把护栏违规渲染成**给用户看的中文段落**（各门禁的打印风格统一）。

    参数说明：
        problems       : 上面两个检查函数的返回值。
        baseline_label : 基线的人话名（如「合成门禁基线」）。
        force_flag     : 逃生门的命令行开关名（必须**显式**提供，不会被误用）。

    返回值：
        逐行文本列表（调用方直接 `for line in ...: print(line)`）。
    """
    lines = [f"\n❌ 拒绝刷新{baseline_label}：本次结果含**向下刷新**的指标。", ""]
    for p in problems:
        lines.append(f"  · {p}")
    lines += [
        "",
        "  刷新基线会把「当前的坏值」合法化为后续比对的新基准，",
        "  且**只靠漂移层保护、没有绝对红线**的指标会被这样渐进地合法化到任意低值",
        "  （例如合成门禁的 recall 只有 20% 相对容差、没有绝对红线）。",
        "",
        f"  若确认这次劣化是**有意为之**（例如换了算法口径、改了评测集），",
        f"  请显式加 `{force_flag}` 重新运行；否则请先修代码再刷新。",
    ]
    return lines


__all__ = ["Rule", "check_downward_pairs", "check_downward_refresh", "render_refusal"]


if __name__ == "__main__":
    # 阶段 331：输出编码加固（GBK 管道下打印 ⇒/✅ 会 UnicodeEncodeError ⇒ 「通过」被读成退出码 1）。
    # 本模块**刻意**只依赖 typing；os/sys 只在这块里就地取，不污染库面。
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    # 自检：纯内存断言，无文件写入。
    _rules = {
        "recall": ("min", 0.20, 0.0),
        "clean_fpr": ("max", 1.00, 0.01),
    }
    # 首次建基线：放行。
    assert check_downward_refresh({}, {"recall": 0.5}, _rules) == []
    # 持平 / 变好：放行。
    assert check_downward_refresh({"recall": 0.80}, {"recall": 0.80}, _rules) == []
    assert check_downward_refresh({"recall": 0.80}, {"recall": 0.95}, _rules) == []
    # 容差内的小幅下滑：放行（与漂移层同口径）。
    assert check_downward_refresh({"recall": 0.80}, {"recall": 0.65}, _rules) == []
    # 超出容差：拒绝。
    _p = check_downward_refresh({"recall": 0.80}, {"recall": 0.60}, _rules)
    assert len(_p) == 1 and "recall" in _p[0], _p
    # 方向相反（越低越好）与之对称。
    assert check_downward_refresh({"clean_fpr": 0.008}, {"clean_fpr": 0.005}, _rules) == []
    assert check_downward_refresh({"clean_fpr": 0.008}, {"clean_fpr": 0.03}, _rules)
    # 近零指标用绝对容差兜底（相对容差在零附近无意义）。
    assert check_downward_refresh({"clean_fpr": 0.0001}, {"clean_fpr": 0.005}, _rules) == []
    # bool 不算数值。
    assert check_downward_refresh({"recall": True}, {"recall": 0.1}, _rules) == []
    # 新增键放行 / 缺失键不越界。
    assert check_downward_refresh({"recall": 0.8}, {"recall": 0.8, "iou": 0.1}, _rules) == []
    assert check_downward_refresh({"recall": 0.8}, {"iou": 0.1}, _rules) == []
    # 零结构假设接口：嵌套结构由调用方扁平化成路径键，**并由调用方配套规则**。
    #   （关键语义：`check_downward_pairs` 只对 `rules` 里**有规则**的键判定 ——
    #     无规则的键一律跳过。「我是嵌套结构」不是本模块的知识，是调用方的。）
    _nested_rules = {
        "per_preset.general.recall": ("min", 0.20, 0.0),
        "per_preset.general.precision": ("min", 0.25, 0.0),
    }
    _nested = [
        ("per_preset.general.recall", 1.00, 0.70),         # 超 20% ⇒ 违规
        ("per_preset.general.precision", 0.1552, 0.1552),  # 持平 ⇒ 放行
        ("area_only.ai_grid.mask_coverage_mean", 1.0, 9.0),  # 无规则 ⇒ 跳过
    ]
    _np = check_downward_pairs(_nested, _nested_rules)
    assert len(_np) == 1 and "per_preset.general.recall" in _np[0], _np
    # 规则表写错时早暴露。
    try:
        check_downward_refresh({"x": 1}, {"x": 1}, {"x": ("sideways", 0.1, 0.0)})
    except ValueError:
        pass
    else:                                     # pragma: no cover
        raise AssertionError("非法 direction 应抛 ValueError")
    print("baseline_guard.py 自检通过：向下刷新护栏的方向/容差/边界均符合预期。")
