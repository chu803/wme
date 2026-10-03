"""阶段 110 · `ui/tab_single` 真值审计的回归守卫（并推广到**全 ui 层接线契约**）。

审计入口 = 未审榜榜首（`audit_selector.py --unaudited` 给出 `ui/tab_single` 13.7）。

背景（P0，用户可见后果可复现）
------------------------------
Gradio 的回调**按位置**把 `inputs=[...]` 传给 `fn`。`process_single` 有 22 个形参、
`run_btn.click(inputs=[...])` 只有 21 个控件，而原签名把**内部开关** `record`
排在最后一个**UI 控件**形参 `dual_run` **之前** ⇒ 21 个控件整体右移一位：

    「双跑择优」复选框  →  绑到 `record`
    `dual_run`        →  无人绑定，恒吃默认值 False

实测（`%TEMP%/s110_probe.py` / `s110_add.py`）：

| 复选框 | `inpaint(dual_run=…)` | `_CACHE.add_success` 次数 |
|---|---|---|
| False（**默认**） | False | **0**（默认路径根本不登记历史） |
| True | False | 1 |

⇒ **两个功能被整体错位**：默认路径丢失历史记录；「双跑择优」在界面上完全无效。
同一个缺陷在 `ui/tab_auto.py::process_auto_repair` **重复出现**
（由 `%TEMP%/s110_wiring.py` 的全 ui 扫描一并发现）。

本文件锁定四件事
----------------
A. **全 ui 层接线契约**：`len(inputs) == len(形参) − len(内部尾部形参)`，且内部尾部必须是
   **后缀**。全部例外**显式登记**（`_INTERNAL_TAIL`），新增错位会立刻失败。
B. **行为**：`dual_run` 的控件值必须真的到 `inpaint`；默认路径必须登记历史。
C. **文案单一真值来源 + i18n**：三处硬编码的「引擎=…」摘要已抽成
   `ui.components.repair_params_text`（走 `params.hist_summary`），
   并用 AST 通解守卫禁止 ui 层再出现硬编码的参数摘要字面量。
D. **契约声明与实现一致**：`process_single` 的返回值是**五元组**（旧 docstring 写"四元组"），
   且所有分支长度一致。
"""
import ast
import io
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ui.components as comp  # noqa: E402
from utils.i18n import get_language, set_language  # noqa: E402

_UI = os.path.join(_ROOT, "ui")

#: **显式登记**的「内部尾部形参」：这些形参**不来自 UI 控件**，且必须排在最后。
#: · `record`  —— 模块内部开关（界面无控件）
#: · `progress` —— Gradio 注入的进度对象（`gr.Progress`），不是 inputs 里的一项
#: 新增一处错位（控件数 ≠ 形参数 − 内部尾部）会让 A 组失败 ⇒ 强制来这里做决策。
_INTERNAL_TAIL: dict = {
    ("tab_single.py", "process_single"): ("record",),
    ("tab_auto.py", "process_auto_repair"): ("record",),
    ("tab_batch.py", "run_batch"): ("progress",),
    ("tab_media.py", "run_pdf"): ("progress",),
    ("tab_media.py", "run_video"): ("progress",),
    ("tab_models.py", "on_download"): ("progress",),
}


def _params_of(node):
    a = node.args
    return [x.arg for x in a.args] + [x.arg for x in a.kwonlyargs]


def _wirings(module_file):
    """返回 `[(handler_name, total_params, n_inputs, param_names)]`。"""
    path = os.path.join(_UI, module_file)
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    funcs = {n.name: _params_of(n) for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute)
                and node.func.attr in ("click", "change", "submit", "input", "release")):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        fn_v, in_v = kw.get("fn"), kw.get("inputs")
        if isinstance(fn_v, ast.Name) and isinstance(in_v, ast.List) and fn_v.id in funcs:
            out.append((fn_v.id, len(funcs[fn_v.id]), len(in_v.elts), funcs[fn_v.id]))
    return out


# ---------------------------------------------------------------------------
# A. 全 ui 层接线契约
# ---------------------------------------------------------------------------

def test_every_wiring_matches_signature_contract():
    """`len(inputs)` 必须等于「形参数 − 已登记的内部尾部形参」，且内部尾部必须是**后缀**。

    这条是全 ui 层的通解守卫：**新增控件却忘了加进 inputs**、或**把内部开关插到控件中间**，
    都会在这里失败——而不是等用户发现「勾了没反应」。
    """
    assert _INTERNAL_TAIL, "登记表不得为空，否则下面的循环会空转（技能 5b）"
    used, mismatches = set(), []
    for module_file in sorted(f for f in os.listdir(_UI) if f.endswith(".py")):
        for handler, n_params, n_in, names in _wirings(module_file):
            key = (module_file, handler)
            tail = _INTERNAL_TAIL.get(key, ())
            expected = n_params - len(tail)
            if tail:
                used.add(key)
                assert names[-len(tail):] == list(tail), (
                    f"{key} 的内部尾部形参 {tail} 不是形参表的**后缀**：{names[-len(tail):]}"
                    "——它们必须排在全部 UI 控件形参之后")
            if n_in != expected:
                mismatches.append(
                    f"{module_file}::{handler}：inputs={n_in}，形参={n_params}"
                    f"（内部尾部 {tail or '无'}）⇒ 应为 {expected}")
    assert not mismatches, "接线数量不匹配（Gradio 按位置传参 ⇒ 必然错位）：\n  " + "\n  ".join(mismatches)
    assert used == set(_INTERNAL_TAIL), (
        f"登记表里有未真正命中的条目（陈旧登记）：{sorted(set(_INTERNAL_TAIL) - used)}")


@pytest.mark.parametrize("module_file,handler", [
    ("tab_single.py", "process_single"),
    ("tab_auto.py", "process_auto_repair"),
])
def test_dual_run_is_the_last_bound_widget(module_file, handler):
    """P0 的直接守卫：这两个回调的**最后一个控件形参**必须是 `dual_run`。

    修复前它们是 `record` ⇒ 复选框整体错位。这条断言钉住的就是那次错位。
    """
    for h, n_params, n_in, names in _wirings(module_file):
        if h != handler:
            continue
        tail = _INTERNAL_TAIL[(module_file, handler)]
        bound = names[:n_params - len(tail)]
        assert len(bound) == n_in
        assert bound[-1] == "dual_run", (
            f"{module_file}::{handler} 最后一个控件形参是 {bound[-1]!r}，应为 'dual_run'"
            "（否则 UI 的「双跑择优」复选框会绑到别的形参上）")
        return
    pytest.fail(f"未找到 {module_file}::{handler} 的接线")


def test_legacy_signature_order_loses_dual_run():
    """**反例自检**：内嵌「修复前的形参顺序」副本，证明它确实会丢掉 `dual_run`。

    若副本也是对的，说明本组守卫测不到东西（技能 5b：先怀疑注入锚点）。
    """
    def legacy(editor_value, engine, strength, dilate, feather, threshold, denoise,
               enable_postprocess=True, sharpen=None,
               poisson_blend=None, poisson_strength=None,
               texture_transfer=None, texture_amount=None,
               artifact_reduce=None, artifact_amount=None,
               superres=None, superres_amount=None,
               real_superres=None, real_scale=None, real_backend=None,
               record=True,                      # ← 修复前：内部开关排在控件之前
               dual_run=False):
        return {"record": record, "dual_run": dual_run}

    widget_values = [None, "lama", 0.8, 2, 5, 127, 3,
                     True, 1.0, False, 0.5, False, 0.5, False, 0.5,
                     False, 0.5, False, 2, "auto",
                     True]          # 第 21 项 = 「双跑择优」复选框（勾选）
    got = legacy(*widget_values)
    assert got["record"] is True and got["dual_run"] is False, (
        f"内嵌的旧顺序副本没有复现错位：{got}——本组守卫是空的")


# ---------------------------------------------------------------------------
# B. 行为：控件值真的到达下游
# ---------------------------------------------------------------------------

def _patch_single_deps(ts, mask, recorder):
    """把 `process_single` 的 I/O 依赖换成记录器，返回 (seen, 还原函数)。"""
    seen = {}
    orig = (ts.build_mask_from_editor, ts.count_mask_pixels, ts.inpaint,
            ts.pp_postprocess, ts.save_result, ts._CACHE, ts.config.UI_COMPARE_VIEW)
    ts.build_mask_from_editor = lambda *a, **kw: mask
    ts.count_mask_pixels = lambda m: 1
    ts.pp_postprocess = lambda r, **kw: r
    ts.save_result = lambda *a, **kw: "out.png"
    ts._CACHE = recorder
    ts.config.UI_COMPARE_VIEW = False
    ts.inpaint = lambda image, m, **kw: (seen.update(kw), np.zeros((20, 20, 3), np.uint8))[1]

    def restore():
        (ts.build_mask_from_editor, ts.count_mask_pixels, ts.inpaint,
         ts.pp_postprocess, ts.save_result, ts._CACHE,
         ts.config.UI_COMPARE_VIEW) = orig
    return seen, restore


class _Recorder:
    def __init__(self):
        self.n = 0
        self.kw = None
    def add_success(self, *a, **kw):
        self.n += 1
        self.kw = kw
        return "rec"
    def update_result_path(self, *a, **kw):
        return None


def _single_args(editor, dual_run):
    """按 `_SINGLE_WIDGET_PARAMS` 顺序构造 21 个实参（末位 = 双跑复选框）。"""
    return [editor, "lama", 0.8, 2, 5, 127, 3,
            True, 1.0, False, 0.5, False, 0.5, False, 0.5,
            False, 0.5, False, 2, "auto", dual_run]


@pytest.mark.parametrize("dual_run", [True, False])
def test_dual_run_widget_reaches_inpaint(dual_run):
    """复选框值必须**原样**到达 `inpaint(dual_run=…)`。"""
    import ui.tab_single as ts
    mask = np.full((20, 20), 255, np.uint8)
    seen, restore = _patch_single_deps(ts, mask, _Recorder())
    try:
        ts.process_single(*_single_args({"background": np.zeros((20, 20, 3), np.uint8)},
                                       dual_run))
    finally:
        restore()
    assert seen.get("dual_run") is dual_run, (
        f"inpaint 收到的 dual_run={seen.get('dual_run')!r}，控件值是 {dual_run!r}")


def test_default_path_still_records_history():
    """复选框默认 False ⇒ 修复后 `record` 保持默认 True ⇒ **历史必须被登记**。

    修复前该路径 `record=False`，历史静默丢失（本用例即 P0 的直接回归守卫）。
    """
    import ui.tab_single as ts
    mask = np.full((20, 20), 255, np.uint8)
    rec = _Recorder()
    seen, restore = _patch_single_deps(ts, mask, rec)
    try:
        ts.process_single(*_single_args({"background": np.zeros((20, 20, 3), np.uint8)},
                                       False))
    finally:
        restore()
    assert rec.n == 1, "默认路径未登记处理历史（P0 回归）"
    assert seen.get("dual_run") is False


def test_widget_params_constant_matches_signature_prefix():
    """模块内的 `_SINGLE_WIDGET_PARAMS` 必须是形参表的**前缀**（契约的自述部分）。"""
    import inspect
    import ui.tab_single as ts
    names = list(inspect.signature(ts.process_single).parameters)
    n = len(ts._SINGLE_WIDGET_PARAMS)
    assert names[:n] == list(ts._SINGLE_WIDGET_PARAMS), (
        "`_SINGLE_WIDGET_PARAMS` 与形参表前缀不一致 —— 契约失效")
    assert names[n:] == ["record"], (
        f"前缀之后应当只剩内部开关，实际 {names[n:]}")


# ---------------------------------------------------------------------------
# C. 文案：单一真值来源 + i18n
# ---------------------------------------------------------------------------

def test_repair_params_text_is_localized():
    """摘要文案必须随语言切换（此前的硬编码中文会被报告层渲染出来）。"""
    cur = get_language()
    try:
        set_language("zh-CN")
        zh = comp.repair_params_text("lama", 0.8, 2, 5, 127, 3)
        set_language("en-US")
        en = comp.repair_params_text("lama", 0.8, 2, 5, 127, 3)
    finally:
        set_language(cur)
    cjk = lambda s: next((c for c in s if "\u4e00" <= c <= "\u9fff"), None)  # noqa: E731
    assert cjk(zh) is not None, "zh-CN 摘要里没有中文？"
    assert cjk(en) is None, f"en-US 摘要仍含中文（i18n 泄漏）：{en}"
    for s in (zh, en):
        assert "lama" in s and "127" in s and "0.8" in s, f"参数未代入：{s}"


def test_no_hardcoded_param_summary_in_ui_layer():
    """**通解守卫**：ui/ 层不得再出现硬编码的「参数摘要」字面量（AST 扫常量）。

    判据是「参数摘要的特征片段」（`引擎=`/`力度=`/`降噪=`），且剔除 docstring；
    对**尚未写下的**第 4 处、第 5 处同样有效。
    """
    needles = ("引擎=", "力度=", "降噪=", "膨胀=", "平滑=", "阈值=")
    offenders = []
    for module_file in sorted(f for f in os.listdir(_UI) if f.endswith(".py")):
        path = os.path.join(_UI, module_file)
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        docs = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef)):
                body = getattr(n, "body", [])
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant):
                    docs.add(id(body[0].value))
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and id(n) not in docs:
                if any(needle in n.value for needle in needles):
                    offenders.append(f"{module_file}: {n.value[:60]!r}")
    assert not offenders, (
        "ui 层出现硬编码的参数摘要字面量（应改用 ui.components.repair_params_text）：\n  "
        + "\n  ".join(offenders))


def test_three_tabs_share_the_builder():
    """三个标签页必须**都**调用共享构造器（防止改一处漏两处）。"""
    for rel in ("tab_single.py", "tab_batch.py", "tab_auto.py"):
        src = io.open(os.path.join(_UI, rel), encoding="utf-8").read()
        assert "repair_params_text(" in src, f"{rel} 未使用共享的参数摘要构造器"


# ---------------------------------------------------------------------------
# D. 契约声明与实现一致
# ---------------------------------------------------------------------------

def test_all_returns_are_five_tuples():
    """`process_single` 的**每个** `return` 都必须是 5 元组（与 outputs 的 5 项对账）。"""
    tree = ast.parse(io.open(os.path.join(_UI, "tab_single.py"), encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_single":
            rets = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
            assert rets, "没有找到 return，判据失效"
            for r in rets:
                assert isinstance(r.value, ast.Tuple), f"非元组返回：{ast.unparse(r)[:60]}"
                assert len(r.value.elts) == 5, (
                    f"返回元组长度 {len(r.value.elts)} ≠ 5：{ast.unparse(r)[:80]}")
            return
    pytest.fail("未找到 process_single")


def test_docstring_declares_five_tuple():
    """docstring 必须与实现一致（旧文案写"四元组"且漏了 `compare_view`）。"""
    import ui.tab_single as ts
    doc = ts.process_single.__doc__ or ""
    assert "五元组" in doc, "docstring 未声明五元组"
    for line in doc.splitlines():
        if "四元组" in line:
            assert "旧 docstring" in line, f"仍以声明语气写四元组：{line.strip()}"


def test_on_enhance_degrades_gracefully():
    """`on_enhance` 的护栏：恶意输入必须优雅降级（探针已验证；此处置为不变量）。"""
    import ui.tab_single as ts
    for a, b, m in ((None, None, "concat"),
                    (np.zeros((4, 4, 3), np.uint8), np.zeros((9, 9, 3), np.uint8), "concat"),
                    (np.zeros((4, 4, 3), np.uint8), np.zeros((4, 4, 3), np.uint8), "不存在的模式")):
        out = ts.on_enhance(a, b, m)
        assert isinstance(out, str) and out, f"未优雅降级：{(a, b, m)}"
