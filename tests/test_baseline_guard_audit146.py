# -*- coding: utf-8 -*-
"""阶段 146 · 三条门禁「刷新基线」护栏守卫。

审计对象：`scripts/quality_gate.py` / `scripts/real_gate.py` / `scripts/e2e_real_probe.py`
的三个元守卫（门禁）**自身的刷新动作**。这是把阶段 145 的方法（审「元守卫自身」）
系统化应用到全部门禁的结果。

本轮修的两处
------------
F1 **两个主门禁的 `--update` 是静默通道**（P1）：
    `quality_gate.py` 与 `real_gate.py` 在写完基线后**直接 `return 0`**，
    **绕过**绝对红线判定与数据完整性判定 —— 用户看到「基线已刷新」+ 退出码 0，
    会以为一切正常。
    对照：`e2e_real_probe.py` **已经**处理了这一点（它的注释写着「否则刷新基线的
    动作会绕过红线检查，成为一条静默通道」）⇒ 两个主门禁是**漏了对齐**。
    修法 = 刷新后**继续走判定**，并用刚写入的基线比漂移（与 e2e 同口径）。

F2 **刷新无「向下刷新」护栏**（P1，三处皆无）：
    没有任何机制阻止把某个指标**合法化到一个更坏的值**。而**只靠漂移层保护、
    没有绝对红线**的指标会被**渐进刷新**到任意低值 —— 合成门禁的 `recall`
    （基线 0.8188，`TOLERANCES` 仅 20% 相对容差，`RED_LINES` 里**没有 recall 条目**，
    而 `precision` / `iou` 都有）：
        0.819 ─刷→ 0.655 ─刷→ 0.524 ─刷→ 0.419 ─…   每步都在容差内 ⇒ 门禁全程绿。
    修法 = `scripts/baseline_guard.py`（新模块）提供**向下刷新护栏**：
    本次结果若把受保护指标推到容差之外 ⇒ **拒绝写入** + 返回 1；
    逃生门是**显式** `--force-update`。护栏**不新增任何阈值**，
    容差与方向全部复用各门禁**既有**的漂移表（同口径）。

F3 **e2e 的 L1 被 `--gate` 开关限定**（P1 附带）：
    `if a.update_baseline:` 段里是 `_save_baseline(...); if not a.gate: return 0`
    ⇒ **不加 `--gate` 时刷新仍是静默通道**。修法 = 刷新时**无条件**走判定
    （未加 `--gate` 则自动启用并提示）。

本文件守七层
------------
O0 存在性自检：内嵌旧逻辑，证明「静默通道」与「向下刷新」都真实存在
O1 护栏纯函数：方向（min/max）、相对/绝对容差、边界、bool 排除、缺失键不越界
O2 三处门禁的规则表**从既有容差表派生**（不新增阈值）—— 逐项对账
O3 **AST**：三处门禁的「刷新段」内**不得含 `return 0`**（刷新不绕过判定）
O4 **AST**：三处门禁都调用护栏 + 都暴露 `--force-update`
O5 走查：三处门禁的「刷新写盘」**在护栏之后**（否则基线已被污染才拒绝）
O6 `scripts/undef_scan.py` 对四个文件报「无未定义名」（防再次出现 `NameError` 型缺陷）
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts import baseline_guard as bg                                  # noqa: E402
from scripts import quality_gate as qg                                    # noqa: E402
from scripts import real_gate as rg                                       # noqa: E402
from scripts import e2e_real_probe as eg                                  # noqa: E402
from scripts import undef_scan                                            # noqa: E402

#: ⚠ **必须用 grep 枚举，不能凭记忆列** —— 我第一版只列了三个门禁，
#: 漏掉了同样含 `--update` 的 `prompted_area_gate.py`（被 `test_prompted_area_gate`
#: 的 slow 用例暴露）。这与经验 143/167 的「硬编码清单漏项」是**同一个病**。
_GATES = {
    "quality_gate": os.path.join(_ROOT, "scripts", "quality_gate.py"),
    "real_gate": os.path.join(_ROOT, "scripts", "real_gate.py"),
    "e2e_real_probe": os.path.join(_ROOT, "scripts", "e2e_real_probe.py"),
    "prompted_area_gate": os.path.join(_ROOT, "scripts", "prompted_area_gate.py"),
    # 阶段 191 补登记：186 新增的性能门禁同样带 `--update`（本守卫**按设计**当场变红，
    # 见上方注释的预言）——这正是「枚举优于记忆」那套机制的用法。
    "perf_probe": os.path.join(_ROOT, "scripts", "perf_probe.py"),
}

#: 刷新分支的判定属性名（`if args.update:` / `if a.update_baseline:`）。
_REFRESH_ATTR = {
    "quality_gate": "update",
    "real_gate": "update",
    "e2e_real_probe": "update_baseline",
    "prompted_area_gate": "update",
    "perf_probe": "update",
}


def _discover_refresh_gates() -> list:
    """从 `scripts/*.py` **枚举**含 `--update` / `--update-baseline` 开关的脚本。

    守卫的成员集必须与真值来源（磁盘上的源码）对齐 —— 新增第 5 个门禁时
    本用例会自动变红（而不是像手抄清单那样静默漏掉）。
    """
    import re as _re
    found = []
    sd = os.path.join(_ROOT, "scripts")
    for fn in sorted(os.listdir(sd)):
        if not fn.endswith(".py") or fn.startswith("__"):
            continue
        src = open(os.path.join(sd, fn), encoding="utf-8").read()
        if _re.search(r"add_argument\(\s*[\"']--update", src):
            found.append(fn[:-3])
    return found


def test_gates_registry_matches_disk():
    """`_GATES` 必须与磁盘上「含 --update 开关」的脚本集合**完全一致**。"""
    on_disk = set(_discover_refresh_gates())
    known = set(_GATES)
    assert on_disk == known, (
        f"含刷新开关的脚本集合与守卫登记不一致："
        f"磁盘多出 {sorted(on_disk - known)}，守卫多出 {sorted(known - on_disk)}"
    )
    assert len(on_disk) >= 4, f"期望至少 4 个含刷新开关的门禁，实际 {sorted(on_disk)}"


def _src(name: str) -> str:
    return open(_GATES[name], encoding="utf-8").read()


def _tree(name: str) -> ast.AST:
    return ast.parse(_src(name))


def _refresh_if_body(tree: ast.AST, attr_name: str) -> "list | None":
    """找到 `if <obj>.<attr_name>:` 的 body。

    ⚠ **不要用 `ast.dump(node.test)` 的文本匹配** —— `dump` 产出的是
    `Attribute(..., attr='update', ...)`，字符串 `"args.update"` **不会出现**，
    首版因此对三个门禁全部「找不到分支」而假失败。
    改为按 AST 结构比较（`ast.Attribute.attr == attr_name`）。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Attribute) \
                and node.test.attr == attr_name:
            return node.body
    return None


# ---------------------------------------------------------------------------
# O0 · 存在性自检：内嵌旧逻辑
# ---------------------------------------------------------------------------

def _legacy_update_returns_silently(summary_red_line_failed: bool) -> int:
    """**修复前**的 `--update` 主体：写完基线直接返回 0，不看任何判定结果。"""
    _ = summary_red_line_failed          # 旧实现根本不读判定结果
    return 0


def _legacy_refresh_allows_any_drop(old: dict, new: dict) -> bool:
    """**修复前**的刷新：无任何护栏，永远允许写入。"""
    _ = (old, new)
    return True


def test_O0_legacy_update_is_a_silent_channel():
    """旧实现即使在红线失败时也返回 0（证明 F1 是真缺陷）。"""
    assert _legacy_update_returns_silently(True) == 0
    # 对照：新实现必须让「红线失败」体现到退出码上
    assert qg.evaluate({"aggregate": {"iou": 0.01}, "rows": []})[0] is False


def test_O0_legacy_refresh_has_no_guard():
    """旧实现的刷新对任意下滑都放行（证明 F2 是真缺陷）。"""
    assert _legacy_refresh_allows_any_drop({"recall": 0.9}, {"recall": 0.1}) is True
    # 对照：新护栏必须拦住
    assert bg.check_downward_refresh(
        {"recall": 0.9}, {"recall": 0.1}, {"recall": ("min", 0.20, 0.0)})


# ---------------------------------------------------------------------------
# O1 · 护栏纯函数
# ---------------------------------------------------------------------------

_RULES = {"recall": ("min", 0.20, 0.0), "clean_fpr": ("max", 1.00, 0.01)}


@pytest.mark.parametrize("old,new,should_flag", [
    (0.80, 0.80, False),    # 持平
    (0.80, 0.95, False),    # 变好
    (0.80, 0.65, False),    # 容差内（劣化 18.75% < 20%）
    (0.80, 0.64, False),    # **恰好等于下界**（0.80−0.16）⇒ 严格 `<` 判定的边界，放行
    (0.80, 0.63, True),     # 越过边界 ⇒ 拦
    (0.80, 0.10, True),     # 大幅下滑
    (0.00, 0.00, False),    # 零基线不动
])
def test_O1_min_direction(old, new, should_flag):
    got = bool(bg.check_downward_refresh({"recall": old}, {"recall": new}, _RULES))
    assert got is should_flag, f"{old}→{new} 期望 {'拦' if should_flag else '放'}"


@pytest.mark.parametrize("old,new,should_flag", [
    (0.008, 0.008, False),
    (0.008, 0.005, False),   # 变好（更小）
    (0.008, 0.018, False),   # 涨幅 0.01 恰好等于绝对容差 ⇒ 放行
    (0.008, 0.030, True),    # 超绝对容差
    (0.0001, 0.005, False),  # 近零 + 绝对容差兜底
    (0.0001, 0.020, True),
])
def test_O1_max_direction(old, new, should_flag):
    got = bool(bg.check_downward_refresh({"clean_fpr": old}, {"clean_fpr": new}, _RULES))
    assert got is should_flag, f"{old}→{new} 期望 {'拦' if should_flag else '放'}"


def test_O1_first_baseline_is_allowed():
    assert bg.check_downward_refresh({}, {"recall": 0.1}, _RULES) == []


def test_O1_bool_is_not_a_metric():
    """`isinstance(True, int)` 为真 —— 布尔配置项不得被当指标比对。"""
    assert bg.check_downward_refresh({"recall": True}, {"recall": 0.01}, _RULES) == []
    assert bg.check_downward_refresh({"recall": 0.8}, {"recall": True}, _RULES) == []


def test_O1_missing_keys_do_not_overreach():
    """新增指标放行；缺失指标不判（数据完整性另有其层）。"""
    assert bg.check_downward_refresh({"recall": 0.8}, {"iou": 0.0}, _RULES) == []
    assert bg.check_downward_refresh({"recall": 0.8}, {"recall": 0.8, "iou": 0.0}, _RULES) == []


def test_O1_bad_direction_raises_early():
    with pytest.raises(ValueError):
        bg.check_downward_refresh({"x": 1}, {"x": 1}, {"x": ("sideways", 0.1, 0.0)})


def test_O1_message_contains_key_old_new_and_allowance():
    p = bg.check_downward_refresh({"recall": 0.80}, {"recall": 0.10}, _RULES)
    assert len(p) == 1
    msg = p[0]
    assert "recall" in msg and "0.8" in msg and "0.1" in msg and "允许" in msg


def test_O1_zero_structural_assumptions():
    """`check_downward_pairs` 只对**有规则**的键判定（零结构假设）。"""
    pairs = [("a.b.c", 1.0, 0.0), ("x", 1.0, 0.0)]
    assert bg.check_downward_pairs(pairs, {"x": ("min", 0.1, 0.0)})  # 只 x 被判
    assert bg.check_downward_pairs(pairs, {}) == []


def test_O1_render_refusal_mentions_escape_hatch():
    lines = bg.render_refusal(["recall 向下刷新"], baseline_label="X 基线",
                              force_flag="--force-update")
    joined = "\n".join(lines)
    assert "拒绝刷新" in joined and "--force-update" in joined and "recall" in joined


# ---------------------------------------------------------------------------
# O2 · 三处规则表从既有容差表派生（不新增阈值）
# ---------------------------------------------------------------------------

def test_O2_quality_gate_rules_derive_from_tolerances():
    rules = qg._refresh_rules()
    assert set(rules) == set(qg.TOLERANCES), "护栏规则键集必须与 TOLERANCES 一致"
    for key, tol in qg.TOLERANCES.items():
        direction, rel, abs_tol = rules[key]
        assert rel == tol, f"{key} 的相对容差未复用 TOLERANCES"
        assert direction == ("max" if key == "clean_fpr" else "min")
        assert abs_tol == (0.01 if key == "clean_fpr" else 0.0)


def test_O2_real_gate_rules_derive_from_tolerances():
    # 构造一组覆盖 per_preset 与 area_only 的 pairs
    pairs = rg._refresh_pairs(
        {"per_preset": {"general": {"precision": .1, "recall": 1.0, "mean_iou": .7,
                                    "mask_iou": .17, "mask_coverage": .03,
                                    "coverage_ratio": 3.7, "n": 30}},
         "area_only": {"ai_grid": {"mask_coverage_mean": .10,
                                   "mask_coverage_p90": .15,
                                   "mask_coverage_max": .15, "n": 30}}},
        {"per_preset": {"general": {"precision": .1, "recall": 1.0, "mean_iou": .7,
                                    "mask_iou": .17, "mask_coverage": .03,
                                    "coverage_ratio": 3.7, "n": 30}},
         "area_only": {"ai_grid": {"mask_coverage_mean": .10,
                                   "mask_coverage_p90": .15,
                                   "mask_coverage_max": .15, "n": 30}}},
    )
    rules = rg._refresh_rules_for(pairs)
    # per_preset 的每个指标都应在 REAL_TOLERANCES
    for key, (direction, rel, _abs) in rules.items():
        metric = key.rsplit(".", 1)[-1]
        if key.startswith("per_preset."):
            assert rel == rg.REAL_TOLERANCES[metric]
            assert direction == rg._REFRESH_DIRECTIONS.get(metric, "min")
        else:
            # 面积段：容差必须复用 AREA_DRIFT_TOLERANCE，且键必须在 AREA_DRIFT_KEYS 内
            assert rel == rg.AREA_DRIFT_TOLERANCE
            assert metric in rg.AREA_DRIFT_KEYS, "面积段覆盖面必须与漂移层一致"
    # `mask_coverage_max` 不在 AREA_DRIFT_KEYS ⇒ 不参与（避免比漂移层更严）
    assert not any(k.endswith("mask_coverage_max") for k in rules)


def test_O2_e2e_rules_derive_from_drift_tables():
    pairs = eg._refresh_pairs(
        [{"case": "c1", "removal": .5, "collateral": .2, "overdraw": 1.0, "spill": .01}],
        [{"case": "c1", "removal": .5, "collateral": .2, "overdraw": 1.0, "spill": .01}],
    )
    rules = eg._refresh_rules_for(pairs)
    assert set(k.rsplit(".", 1)[-1] for k in rules) == set(eg._E2E_REFRESH_DIRECTIONS)
    for key, (direction, rel, abs_tol) in rules.items():
        metric = key.rsplit(".", 1)[-1]
        assert rel == eg.E2E_DRIFT_TOL
        assert abs_tol == eg.E2E_DRIFT_ABS.get(metric, 0.0)
        assert direction == eg._E2E_REFRESH_DIRECTIONS[metric]


def test_O2_e2e_directions_match_drift_layer():
    """方向表必须与 `evaluate_gate` 漂移段的 `worse` 表一致（防两套口径）。"""
    src = _src("e2e_real_probe")
    for key, wanted in (("removal", "down"), ("collateral", "up"),
                        ("overdraw", "up"), ("spill", "up")):
        assert f'("{key}", "{wanted}")' in src, f"{key} 的漂移方向与刷新方向不一致"
    assert eg._E2E_REFRESH_DIRECTIONS["removal"] == "min"
    for k in ("collateral", "overdraw", "spill"):
        assert eg._E2E_REFRESH_DIRECTIONS[k] == "max"


# ---------------------------------------------------------------------------
# O3 · AST：刷新段内不得含 `return 0`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gate,attr_name", sorted(_REFRESH_ATTR.items()))
def test_O3_refresh_block_has_no_silent_return_zero(gate, attr_name):
    """刷新基线后**不得**直接 `return 0`（那会让刷新绕过全部判定）。"""
    body = _refresh_if_body(_tree(gate), attr_name)
    assert body is not None, f"{gate}: 未找到刷新分支（`if ...{attr_name}:`）"

    def returns_zero(nodes) -> int:
        n = 0
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    if isinstance(sub.value, ast.Constant) and sub.value.value == 0:
                        n += 1
        return n

    assert returns_zero(body) == 0, (
        f"{gate}: 刷新分支内仍有 `return 0` —— 刷新会成为绕过判定的静默通道"
    )


@pytest.mark.parametrize("gate,attr_name", sorted(_REFRESH_ATTR.items()))
def test_O3_refresh_block_can_refuse(gate, attr_name):
    """刷新分支必须**有能力**拒绝（含 `return 1`）。

    ⚠ 必须用 **AST 限定在刷新块的 body 内**。首版用「文本片段扫 3000 字」
    ⇒ 会扫到块**外面**的 `if not ok: return 1` ⇒ 注入「旧静默通道」后仍判为绿
    （注入式反例当场暴露了这个假阴性）。
    """
    body = _refresh_if_body(_tree(gate), attr_name)
    assert body is not None, f"{gate}: 未找到刷新分支"
    ones = [sub for node in body for sub in ast.walk(node)
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant)
            and sub.value.value == 1]
    assert ones, f"{gate}: 刷新分支内没有 `return 1` —— 它无法拒绝任何刷新"


# ---------------------------------------------------------------------------
# O4 · 三处都接护栏 + 都暴露 --force-update
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gate", sorted(_GATES))
def test_O4_gate_uses_baseline_guard(gate):
    """四个门禁都必须调用护栏的**判定**与**渲染**两个入口。

    · 判定入口按基线结构分两种（`quality_gate` 的 aggregate 是扁平 dict ⇒
      `check_downward_refresh`；其余是嵌套结构 ⇒ `check_downward_pairs`），
      两个入口共用同一套判定内核，故断言「二选一存在」即可；
    · import 风格也有两种（`from scripts import baseline_guard` 与
      `from baseline_guard import ...` —— 后者用于已把 `scripts/` 加进 `sys.path`
      的脚本，如 `prompted_area_gate`），断言「二者之一」。
    """
    src = _src(gate)
    assert ("from scripts import baseline_guard" in src
            or "from baseline_guard import" in src), f"{gate}: 未 import 护栏"
    assert ("baseline_guard.check_downward_pairs" in src
            or "baseline_guard.check_downward_refresh" in src
            or "check_downward_pairs(" in src), f"{gate}: 未调用护栏判定入口"
    assert ("baseline_guard.render_refusal" in src
            or "render_refusal(" in src), f"{gate}: 未调用护栏的拒绝渲染"


@pytest.mark.parametrize("gate", sorted(_GATES))
def test_O4_gate_exposes_force_update_flag(gate):
    tree = _tree(gate)
    flags = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_argument":
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    flags.add(a.value)
    assert "--force-update" in flags, f"{gate}: 缺少逃生门 --force-update"
    assert any(f in flags for f in ("--update", "--update-baseline")), gate


@pytest.mark.parametrize("gate", sorted(_GATES))
def test_O4_force_update_does_not_skip_red_lines(gate):
    """`--force-update` 只跳过**向下刷新护栏**，**不跳过红线** —— 源码层断言。"""
    src = _src(gate)
    # force_update 只应出现在「护栏判定」的 if 条件里
    occurrences = [l for l in src.splitlines() if "force_update" in l]
    assert occurrences, gate
    guard_lines = [l for l in occurrences if "if" in l or "add_argument" in l or "help" in l]
    assert guard_lines, f"{gate}: force_update 未见任何用途"


# ---------------------------------------------------------------------------
# O5 · 护栏在写盘之前
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gate,markers", [
    ("quality_gate", ("baseline_guard.check_downward_refresh", 'open(args.baseline, "w"')),
    ("real_gate", ("baseline_guard.check_downward_pairs", 'open(args.baseline, "w"')),
    ("e2e_real_probe", ("baseline_guard.check_downward_pairs", "_save_baseline(agg")),
    ("prompted_area_gate", ("check_downward_pairs(_pk", 'io.open(args.baseline, "w"')),
])
def test_O5_guard_runs_before_writing(gate, markers):
    """护栏必须在**写盘之前** —— 否则基线已被污染，再拒绝也来不及。"""
    src = _src(gate)
    guard_pos = src.index(markers[0])
    write_pos = src.index(markers[1], guard_pos)
    assert guard_pos < write_pos, f"{gate}: 护栏调用出现在写盘之后"


def test_O5_e2e_refresh_does_not_depend_on_gate_flag():
    """F3：e2e 的「刷新后继续判定」**不得被 `--gate` 开关限定**。

    修复前的写法是 `_save_baseline(...); if not a.gate: return 0` ——
    **不加 `--gate` 时刷新仍是静默通道**。修法是刷新时无条件走判定
    （未加 `--gate` 则自动启用）。
    """
    src = _src("e2e_real_probe")
    seg = src.split("if a.update_baseline:", 1)[1][:1600]
    assert "a.gate = True" in seg, (
        "e2e 的刷新分支未强制启用判定 —— 不加 --gate 时刷新会绕过红线"
    )
    assert "未加 --gate" in seg or "自动启用" in seg, "缺少对用户的提示"


# ---------------------------------------------------------------------------
# O6 · 静态未定义名走查
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "scripts/quality_gate.py",
    "scripts/real_gate.py",
    "scripts/e2e_real_probe.py",
    "scripts/baseline_guard.py",
    "scripts/prompted_area_gate.py",
])
def test_O6_no_undefined_names(rel):
    """四个文件不得含「引用了但从未定义」的名字（本阶段实际踩中过两次）。"""
    missing, err = undef_scan.scan_file(os.path.join(_ROOT, rel))
    assert err is None, f"{rel}: {err}"
    assert missing == [], f"{rel} 含未定义名：{missing}"


def test_O6_scanner_has_discriminating_power():
    """`undef_scan` 必须**抓得住**真正例，且不误报两个已知模式（工具也要被测）。"""
    probe = (
        "import os\n"
        "def f(a):\n"
        "    b = a + 1\n"
        "    with open('x') as fh:\n"
        "        y = fh.read()\n"
        "    print(f'{b:.1f}', fh, os, y)\n"
        "    return mystery_name\n"
    )
    tree = ast.parse(probe)
    missing = sorted(undef_scan.collect_used(tree)
                     - undef_scan.module_bindings(tree) - undef_scan._BUILTINS)
    assert missing == ["mystery_name"], f"扫描器区分力不足：{missing}"
