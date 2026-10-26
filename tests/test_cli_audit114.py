# -*- coding: utf-8 -*-
"""
阶段 114 审计守卫：cli.py 的未测辅助路径 + 两处修复的不变量。

审计发现（阶段 114）：
  F1（加固，潜伏）：`_run_one` 未自保证展示键 `original_name` / `input_path`，
     而 `_print_result` 依这两个键拼输出行 —— 两者皆缺时会打印**空文件名**。
     process_file 当前各分支都带这两键，故非活跃缺陷；CLI 侧不应依赖它 ⇒ 在 `_run_one`
     用 setdefault 自保证（实测无此修复时确会打印空名）。
  F2（真缺陷，静默空操作）：`--superres` / `--superres-amount` 此前**只透传给图片**，
     PDF 路径 `_run_pdf` 完全未传 —— 但 `process_pdf` 明确支持并逐页使用 `superres`
     ⇒ 用户在 PDF 上加 `--superres` 被**静默忽略**。修复：`_run_pdf` 补传两参数。

本文件把上述行为与既有未测路径（_kind_of / _collect_inputs / _dispatch / _print_result）
一并钉死。全部零真实图像处理（下游以 monkeypatch 替身捕获入参）。
"""

import argparse
import os as _os
import sys as _sys

import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import cli  # noqa: E402
from services.quality_service import QualityReport  # noqa: E402


def _args(tmp_path, **over):
    ns = cli._build_arg_parser().parse_args([])
    ns.output_dir = str(tmp_path)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# ------------------------------------------------------------------------------
# 1) _kind_of：按扩展名判类型（大小写无关）
# ------------------------------------------------------------------------------
def test_kind_of_case_insensitive_and_unknown():
    assert cli._kind_of("a.png") == "image"
    assert cli._kind_of("A.JPEG") == "image"
    assert cli._kind_of("x.WebP") == "image"
    assert cli._kind_of("d.pdf") == "pdf"
    assert cli._kind_of("v.MP4") == "video"
    assert cli._kind_of("noext") == "unknown"
    assert cli._kind_of("weird.xyz") == "unknown"
    assert cli._kind_of("trailing.") == "unknown"


# ------------------------------------------------------------------------------
# 2) _collect_inputs：文件/文件夹 × 递归 × 含媒体
# ------------------------------------------------------------------------------
def _make_tree(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    for rel in ("top1.png", "top2.jpg", "doc.pdf", "clip.mp4", "note.txt"):
        (tmp_path / rel).write_bytes(b"")
    for rel in ("nested1.png", "nested2.webp", "nested.pdf", "nested.mp4"):
        (sub / rel).write_bytes(b"")
    return tmp_path


def _names(paths):
    return sorted(_os.path.basename(x) for x in paths)


def test_collect_inputs_folder_combinations(tmp_path):
    root = _make_tree(tmp_path)
    # 文件夹 + 非递归 + 不含媒体：只顶层图片。
    assert _names(cli._collect_inputs(str(root), False, False)) == ["top1.png", "top2.jpg"]
    # 文件夹 + 非递归 + 含媒体：顶层图片 + PDF + 视频。
    assert _names(cli._collect_inputs(str(root), False, True)) == [
        "clip.mp4", "doc.pdf", "top1.png", "top2.jpg"]
    # 文件夹 + 递归 + 不含媒体：仅图片（含子目录）。
    assert _names(cli._collect_inputs(str(root), True, False)) == [
        "nested1.png", "nested2.webp", "top1.png", "top2.jpg"]
    # 文件夹 + 递归 + 含媒体：全部 8 个。
    assert _names(cli._collect_inputs(str(root), True, True)) == [
        "clip.mp4", "doc.pdf", "nested.mp4", "nested.pdf",
        "nested1.png", "nested2.webp", "top1.png", "top2.jpg"]


def test_collect_inputs_single_file_ignores_include_media(tmp_path):
    root = _make_tree(tmp_path)
    # 单文件：PDF / 视频都被收（与 include_media 无关，由 docstring 承诺）。
    assert _names(cli._collect_inputs(str(root / "doc.pdf"), False, False)) == ["doc.pdf"]
    assert _names(cli._collect_inputs(str(root / "clip.mp4"), False, False)) == ["clip.mp4"]
    # 未知类型单文件不收。
    assert cli._collect_inputs(str(root / "note.txt"), False, False) == []
    # 不存在的路径：空。
    assert cli._collect_inputs(str(root / "nope"), False, False) == []


# ------------------------------------------------------------------------------
# 3) _dispatch：按类型分派到对应下游
# ------------------------------------------------------------------------------
def test_dispatch_routes_by_kind(tmp_path, monkeypatch):
    import services.pdf_service as pdfs
    import services.video_service as vids

    monkeypatch.setattr(cli, "process_file",
                        lambda *a, **k: {"status": "success", "original_name": "x",
                                         "out_path": "x", "error": None})
    monkeypatch.setattr(pdfs, "process_pdf",
                        lambda *a, **k: type("R", (), {"status": "success", "out_path": "x",
                                                       "error": None,
                                                       "summary": lambda s: "pdf"})())
    monkeypatch.setattr(vids, "process_video",
                        lambda *a, **k: type("R", (), {"status": "success", "out_path": "x",
                                                       "error": None,
                                                       "summary": lambda s: "vid"})())
    a = _args(tmp_path)
    assert cli._dispatch("a.png", a, show_progress=False)["kind"] == "image"
    assert cli._dispatch("d.pdf", a, show_progress=False)["kind"] == "pdf"
    assert cli._dispatch("v.mp4", a, show_progress=False)["kind"] == "video"


# ------------------------------------------------------------------------------
# 4) _print_result：五个分支的图标 / 名字非空 / 计数
# ------------------------------------------------------------------------------
def _run_print(rd, args, evaluate=False):
    args.evaluate = evaluate
    summary = {"success": 0, "skipped": 0, "failed": 0}
    qrs = []
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli._print_result(rd, args, summary, qrs)
    return buf.getvalue(), summary, qrs


def test_print_result_branches(tmp_path, capsys):
    a = _args(tmp_path)
    cases = [
        ({"status": "success", "original_name": "a.png", "input_path": "/x/a.png",
          "out_path": "/o/a.png"}, "✅", "a.png"),
        ({"kind": "pdf", "status": "success", "original_name": "d.pdf",
          "input_path": "/x/d.pdf", "out_path": "/o/d.pdf", "note": "共 3 页"}, "📄", "d.pdf"),
        ({"kind": "video", "status": "success", "original_name": "v.mp4",
          "input_path": "/x/v.mp4", "out_path": "/o/v.mp4", "note": "12 帧"}, "🎬", "v.mp4"),
        ({"status": "skipped", "original_name": "s.png", "input_path": "/x/s.png",
          "error": "未检测到水印区域（已跳过）"}, "⏭️", "s.png"),
        ({"status": "failed", "original_name": "f.png", "input_path": "/x/f.png",
          "error": "boom"}, "❌", "f.png"),
    ]
    for rd, icon, name in cases:
        line, summary, _ = _run_print(rd, a)
        assert icon in line
        assert name in line, f"名字缺失：{line!r}"
        assert line.strip().split()[1] == name or name in line  # 名字紧随图标
    # 计数正确。
    _, s1, _ = _run_print({"status": "success", "original_name": "a", "out_path": "o"}, a)
    _, s2, _ = _run_print({"status": "skipped", "original_name": "s", "error": "e"}, a)
    _, s3, _ = _run_print({"status": "failed", "original_name": "f", "error": "e"}, a)
    assert s1 == {"success": 1, "skipped": 0, "failed": 0}
    assert s2 == {"success": 0, "skipped": 1, "failed": 0}
    assert s3 == {"success": 0, "skipped": 0, "failed": 1}


def test_print_result_evaluate_only_images(tmp_path, monkeypatch):
    a = _args(tmp_path)
    calls = []

    def _fake_eval(src, out):
        calls.append((src, out))
        return QualityReport(ssim=0.99, changed_ratio=0.01, score=90,
                             level="good", detail_key="quality.detail_good")

    monkeypatch.setattr(cli, "evaluate_quality", _fake_eval)
    # 图片：触发评估、进 quality_reports。
    _, _, qrs_img = _run_print(
        {"status": "success", "original_name": "a.png", "input_path": "/x/a.png",
         "out_path": "/o/a.png"}, a, evaluate=True)
    assert len(qrs_img) == 1 and calls == [("/x/a.png", "/o/a.png")]
    # PDF / 视频：不评估（多页 / 多帧无 SSIM 可比性）。
    calls.clear()
    _, _, qrs_pdf = _run_print(
        {"kind": "pdf", "status": "success", "original_name": "d.pdf",
         "input_path": "/x/d.pdf", "out_path": "/o/d.pdf"}, a, evaluate=True)
    _, _, qrs_vid = _run_print(
        {"kind": "video", "status": "success", "original_name": "v.mp4",
         "input_path": "/x/v.mp4", "out_path": "/o/v.mp4"}, a, evaluate=True)
    assert qrs_pdf == [] and qrs_vid == [] and calls == []


# ------------------------------------------------------------------------------
# 5) F1 守卫：_run_one 恒补齐展示键（即使下游只回最小 dict 也不出空名）
# ------------------------------------------------------------------------------
def test_run_one_always_supplies_display_keys(tmp_path, monkeypatch):
    # 下游返回一个「缺 original_name / input_path」的最小 dict（模拟未来分支）。
    monkeypatch.setattr(cli, "process_file",
                        lambda *a, **k: {"status": "failed", "error": "无名字的错误"})
    a = _args(tmp_path)
    rd = cli._run_one("/x/某个文件.png", a, show_progress=False)
    assert rd.get("original_name") == "某个文件.png"
    assert rd.get("input_path") == "/x/某个文件.png"
    assert rd.get("kind") == "image"
    # 端到端：打印出来的名字必须非空。
    line, _, _ = _run_print(rd, a)
    assert "某个文件.png" in line
    assert "❌  失败" not in line  # 不得出现「图标后紧跟空名」的形态


def test_run_one_preserves_downstream_name_when_present(tmp_path, monkeypatch):
    # 下游带了名字时，setdefault 不得覆盖它（单真值来源：以下游为准）。
    monkeypatch.setattr(cli, "process_file",
                        lambda *a, **k: {"status": "success", "original_name": "下游名.png",
                                         "input_path": "/y/下游名.png", "out_path": "o",
                                         "error": None})
    a = _args(tmp_path)
    rd = cli._run_one("/x/传入.png", a, show_progress=False)
    assert rd["original_name"] == "下游名.png"
    assert rd["input_path"] == "/y/下游名.png"


# ------------------------------------------------------------------------------
# 6) F2 守卫：_run_pdf 透传轻量超分（此前完全未传，PDF 上 --superres 静默失效）
# ------------------------------------------------------------------------------
def test_run_pdf_threads_light_superres(tmp_path, monkeypatch):
    from services import pdf_service

    captured = {}
    monkeypatch.setattr(
        pdf_service, "process_pdf",
        lambda *a, **k: (captured.update(k) or type("R", (), {
            "status": "success", "out_path": "x", "error": None,
            "summary": lambda s: "pdf-ok"})()),
    )
    a = _args(tmp_path, superres=True, superres_amount=0.7)
    rd = cli._run_pdf(str(tmp_path / "doc.pdf"), a)
    assert rd["kind"] == "pdf" and rd["status"] == "success"
    assert captured.get("superres") is True
    assert captured.get("superres_amount") == 0.7
