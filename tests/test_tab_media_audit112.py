# -*- coding: utf-8 -*-
"""
Stage-112 真值审计守卫：ui/tab_media.py 的 Gradio 事件接线契约。

背景（阶段 110 P0 教训）：控件数 ≠ 形参数 = 静默错位（位置绑定错乱）。
本模块在 stage 112 审计中确认 run_pdf / run_video / run_video_compare /
export_frame_file / export_sheet_file / on_frame_gallery_select 的全部接线
**均正确**（控件数 == 形参数 == 输出组件数）。以下静态守卫把这一结论钉死，
任何「增删控件却不同步函数形参 / 改了返回却不同步 outputs」都会让测试失败。

Gradio 自动注入的参数（不计入接线控件数）：`progress`（.click/.select 回调末位）、
`evt`（.select 回调首位的 gr.SelectData）。

注意：本守卫为*静态*（AST 解析源码），不导入 ui/tab_media（避免拉起 Gradio 全栈）。
若以后回调契约发生合理变更，需同步更新下方的 EXPECTED 契约表。
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ui" / "tab_media.py"


def _parse():
    return ast.parse(SRC.read_text(encoding="utf-8"))


def _wired_param_count(fdef):
    """除 Gradio 自动注入的 progress / evt 外的形参数。"""
    return sum(1 for a in fdef.args.args if a.arg not in ("progress", "evt"))


def _return_tuple_lengths(fdef):
    lens = []
    for n in ast.walk(fdef):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple):
            lens.append(len(n.value.elts))
    return lens


def _collect_wirings(tree):
    """返回 {fn_name: (n_inputs, n_outputs)}，覆盖 .click/.select/.change。"""
    out = {}
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("click", "select", "change")):
            continue
        fn_name = inputs = outputs = None
        for kw in n.keywords:
            if kw.arg == "fn" and isinstance(kw.value, ast.Name):
                fn_name = kw.value.id
            elif kw.arg == "inputs" and isinstance(kw.value, (ast.List, ast.Tuple)):
                inputs = len(kw.value.elts)
            elif kw.arg == "outputs" and isinstance(kw.value, (ast.List, ast.Tuple)):
                outputs = len(kw.value.elts)
        if fn_name:
            out[fn_name] = (inputs, outputs)
    return out


def _arity_claim(doc):
    import re
    m = re.search(r"（(.?)元组", doc or "")
    return m.group(1) if m else None


def test_wiring_contract():
    tree = _parse()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    wirings = _collect_wirings(tree)

    # (fn, 期望接线 inputs, 期望 outputs)
    EXPECTED = {
        "run_pdf": (12, 3),
        "run_video": (16, 6),
        "run_video_compare": (4, 1),
        "export_frame_file": (4, 1),
        "export_sheet_file": (1, 1),
        "on_frame_gallery_select": (3, 2),  # evt 由 Gradio 注入，不计入 inputs
    }
    for fn, (exp_in, exp_out) in EXPECTED.items():
        fdef = funcs[fn]
        wired = _wired_param_count(fdef)
        got_in, got_out = wirings[fn]
        assert wired == exp_in, f"{fn}: 形参数={wired} 但接线 inputs={got_in}（应={exp_in}）"
        assert got_in == exp_in, f"{fn}: 接线 inputs={got_in} 应={exp_in}"
        assert got_out == exp_out, f"{fn}: 接线 outputs={got_out} 应={exp_out}"


def test_return_arity_matches_outputs():
    tree = _parse()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    # run_pdf 返回三元组（所有 return 都是 3 元组）
    pdf_rets = _return_tuple_lengths(funcs["run_pdf"])
    assert pdf_rets and set(pdf_rets) == {3}, f"run_pdf 应恒返回 3 元组，实得 {pdf_rets}"

    # run_video 返回六元组（所有 return 都是 6 元组）—— stage-112 修复点
    vid_rets = _return_tuple_lengths(funcs["run_video"])
    assert vid_rets and set(vid_rets) == {6}, f"run_video 应恒返回 6 元组，实得 {vid_rets}"


def test_docstring_arity_claims():
    tree = _parse()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    pdf_doc = ast.get_docstring(funcs["run_pdf"]) or ""
    assert "三元组" in pdf_doc, "run_pdf 文档应声明「三元组」"

    vid_doc = ast.get_docstring(funcs["run_video"]) or ""
    assert "四元组" not in vid_doc, "run_video 文档不得再写「四元组」（历史错误，stage-112 已修）"
    assert "六元组" in vid_doc, "run_video 文档应声明「六元组」"
