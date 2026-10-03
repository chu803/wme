# -*- coding: utf-8 -*-
"""
==============================================================================
 阶段 138 守卫测试：ui/components 自检真实性 + param_values 契约
------------------------------------------------------------------------------
 背景（docs/138）：
   D1  __main__ 自检 catch-all 把任何构造异常吞成「注意」且退出码 0——本机
       gradio 下组件构造**实际一直失败**（'Slider' object has no attribute
       'page'），自检却始终显示「通过」（哑巴自检活案例，经验 133）。
       修复：自检体包 gr.Blocks() 上下文 + 失败 SystemExit 非零。
   D2  补 param_values 键集 ↔ RepairParamHandles 字段集的一致性契约断言
       （防两头各改各的）。
 注入验证：param_values 删键后，子进程自检必须失败（本文件 + legacy 守卫红）。
==============================================================================
"""

from __future__ import annotations

import os
import subprocess
import sys

import gradio as gr

from ui.components import RepairParamHandles, build_repair_params, param_values, repair_params_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_param_values_keys_match_dataclass():
    with gr.Blocks():
        h = build_repair_params(accordion=False)
        vals = param_values(h)
    expected = {f.name for f in RepairParamHandles.__dataclass_fields__.values()}
    assert set(vals.keys()) == expected, (
        f"param_values 键集与句柄字段不一致：缺 {expected - set(vals)} 多 {set(vals) - expected}"
    )
    assert len(expected) == 20


def test_selfcheck_subprocess_green():
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "ui", "components.py")],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert r.returncode == 0, f"自检失败：{(r.stderr or r.stdout or '')[-400:]}"
    assert "自检通过" in r.stdout


def test_repair_params_text_localized():
    zh = repair_params_text("lama", 0.9, 0, 2, 127, 30)
    assert "引擎=lama" in zh and "力度=0.9" in zh, f"zh 摘要异常：{zh!r}"
    # en-US 模板存在且占位符同名（阶段 110 契约：摘要唯一真值走 i18n）
    en = repair_params_text("lama", 0.9, 0, 2, 127, 30)
    assert "None" not in en, f"摘要出现未填充占位符：{en!r}"


def test_build_detection_stats_markdown_placeholders():
    from ui.components import build_detection_stats_markdown

    md_none = build_detection_stats_markdown(None)
    assert "检测命中率" in md_none and "运行检测后" in md_none
    zero = type("S", (), {"region_count": 0})()
    assert "未检测到水印区域" in build_detection_stats_markdown(zero)
    # 未知 kind：回退为原常量名，不泄漏 detect.kind. 键名（_kind_label 契约）
    st = type("S", (), {
        "region_count": 1, "coverage_ratio": 0.1, "mask_pixels": 100,
        "by_kind": {"mystery_kind": 1}, "used_ocr": False,
    })()
    md = build_detection_stats_markdown(st)
    assert "detect.kind." not in md and "mystery_kind" in md
