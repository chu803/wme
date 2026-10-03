# -*- coding: utf-8 -*-
"""
阶段 88 · services 层续审测试。

审计对象（按「模块行数 ÷ 测试引用次数」选点）：
  · services/report_service.py（526 行 / 3 次引用）—— 主战场
  · services/video_export.py （183 行 / 3 次引用）—— 复核后未发现缺陷，补钉正确行为

本文件钉住的缺陷（R1 家族）：
  R1a 全部未评估时，HTML 汇总渲染成「良好 0 · 告警 0 · 待复核 0」（总条数 3），
      读者只能理解为「全部都待复核」，而报告里根本没有这条信息；
      同一份数据的 PDF 却显示「本报告未包含质量评估」—— 同一数据两种结论。
  R1b 部分条目评估失败（evaluate_quality 抛错被 except 吞掉、quality 留 None）时，
      HTML 与 PDF **都**静默漏计：3 条里只算出 1 条质量，汇总只报 1 条，
      与同一文档内逐条明细的「未评估质量」自相矛盾。

两条真实来路（不是构造出来的）：
  · config.REPORT_INCLUDE_QUALITY=False（config 注释明确推荐用于「纯交付」报告）；
  · build_entries 里 evaluate_quality 异常被 log.warning 后 continue 式吞掉。
"""
import base64
import io
import os
import re
import tempfile

import cv2
import numpy as np
import pytest

import services.report_service as rs
from services.quality_service import QualityReport
from services.report_service import generate_html, generate_pdf
from services.video_export import _extract_one, export_contact_sheet, export_frame_compare


# ------------------------------------------------------------------------------
# 构造工具
# ------------------------------------------------------------------------------
def _png_b64() -> str:
    arr = np.full((60, 90, 3), 60, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _entry(name: str, quality) -> dict:
    return {
        "name": name, "engine": "opencv", "mode": "auto", "params": "力度=50",
        "time_str": "2026-09-15 10:00:00", "img_b64": _png_b64(), "img_w": 90,
        "img_h": 60, "img_is_compare": True, "quality": quality,
        "result_path": "x.png", "recommendation": None,
    }


def _q(score: int, level: str) -> QualityReport:
    return QualityReport(ssim=0.9, changed_ratio=0.1, score=score, level=level, detail="d")


def _html_summary(path: str) -> str:
    """取出 HTML 里 <div class="summary"> 的纯文本内容。"""
    t = io.open(path, encoding="utf-8").read()
    i = t.find('<div class="summary">')
    assert i >= 0, "HTML 里找不到汇总块"
    j = t.find("</div>", i)
    return re.sub(r"<[^>]+>", "", t[i + len('<div class="summary">'):j]).strip()


def _pdf_text(path: str) -> str:
    fitz = pytest.importorskip("fitz")
    doc = fitz.open(path)
    try:
        return "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()


def _render(entries):
    """渲染一份 HTML + 一份 PDF，返回 (html 汇总文本, pdf 全文)。"""
    d = tempfile.mkdtemp(prefix="s88_")
    hp = os.path.join(d, "r.html")
    pp = os.path.join(d, "r.pdf")
    generate_html(entries, title="阶段88", out_path=hp)
    generate_pdf(entries, title="阶段88", out_path=pp)
    return _html_summary(hp), _pdf_text(pp)


ALL_NONE = [_entry("a.png", None), _entry("b.png", None), _entry("c.png", None)]
PARTIAL = [_entry("a.png", _q(92, "good")), _entry("b.png", None), _entry("c.png", None)]
ALL_ASSESSED = [_entry("a.png", _q(92, "good")),
                _entry("b.png", _q(70, "warn")),
                _entry("c.png", _q(40, "review"))]


# ------------------------------------------------------------------------------
# 1. 统计层：必须区分「未评估」与「评估结果不是 good」
# ------------------------------------------------------------------------------
def test_summary_stats_exposes_unassessed():
    """_summary_stats 必须给出 assessed / unassessed，否则渲染层无从区分。"""
    s = rs._summary_stats(ALL_NONE)
    assert s["total"] == 3 and s["good"] == s["warn"] == s["review"] == 0
    assert s["assessed"] == 0 and s["unassessed"] == 3
    assert s["avg"] is None

    s2 = rs._summary_stats(PARTIAL)
    assert (s2["assessed"], s2["unassessed"]) == (1, 2)
    assert s2["avg"] == 92.0

    s3 = rs._summary_stats(ALL_ASSESSED)
    assert (s3["assessed"], s3["unassessed"]) == (3, 0)


def test_quality_summary_modes():
    """四种 mode 的判定必须稳定：empty / none / partial / full。"""
    assert rs._quality_summary(rs._summary_stats([]))["mode"] == "empty"
    assert rs._quality_summary(rs._summary_stats(ALL_NONE))["mode"] == "none"
    assert rs._quality_summary(rs._summary_stats(PARTIAL))["mode"] == "partial"
    assert rs._quality_summary(rs._summary_stats(ALL_ASSESSED))["mode"] == "full"


def test_quality_summary_none_mode_has_note_and_no_counts():
    """mode=none 时必须给整段说明、且不给任何计数（计数会误导）。"""
    qs = rs._quality_summary(rs._summary_stats(ALL_NONE))
    assert qs["counts"] == [] and qs["avg"] is None
    assert "未包含质量评估" in qs["note"]


def test_quality_summary_partial_appends_unassessed_segment():
    """partial 必须补一段「未评估 N」，否则三项之和 < 总条数。"""
    qs = rs._quality_summary(rs._summary_stats(PARTIAL))
    assert ("未评估", 2) in qs["counts"]
    assert sum(v for _, v in qs["counts"]) == 3


# ------------------------------------------------------------------------------
# 2. 缺陷 R1a / R1b 回归
# ------------------------------------------------------------------------------
def test_r1a_html_all_unassessed_does_not_look_like_all_review():
    """R1a：全部未评估时，HTML 不得出现「良好 0 · 告警 0 · 待复核 0」这种误导串。"""
    hs, _ = _render(ALL_NONE)
    assert "未包含质量评估" in hs, f"应显示未评估说明，实际：{hs!r}"
    assert "良好" not in hs and "待复核" not in hs, (
        f"未评估时不应给出任何质量分级计数（会被读成「全部都待复核」）：{hs!r}"
    )


def test_r1b_partial_renders_unassessed_in_both_html_and_pdf():
    """R1b：部分未评估时，HTML 与 PDF 都必须显式报出「未评估 N」。"""
    hs, pt = _render(PARTIAL)
    assert "未评估 2" in hs, f"HTML 漏计未评估条目：{hs!r}"
    if "质量概况" in pt:
        assert "未评估 2" in pt, f"PDF 漏计未评估条目：{pt!r}"
    else:
        pytest.skip("当前环境 PDF 中文字体不可用，跳过 PDF 文本断言")


def test_full_assessed_output_unchanged():
    """对照组：全部已评估时不得多出「未评估」字样，计数与平均分照旧。"""
    hs, _ = _render(ALL_ASSESSED)
    assert "良好 1" in hs and "告警 1" in hs and "待复核 1" in hs
    assert "未评估" not in hs
    assert "平均分 67.3" in hs


def test_empty_entries_still_renders():
    """展示路径不得有能力让功能失败：空列表也要能出报告。"""
    hs, _ = _render([])
    assert hs == "共 0 条", f"空报告汇总应为「共 0 条」，实际：{hs!r}"


# ------------------------------------------------------------------------------
# 3. 单一真值来源守卫（防止日后把计数重新内联回渲染层）
# ------------------------------------------------------------------------------
def test_html_and_pdf_consume_same_quality_summary(monkeypatch):
    """
    核心守卫：HTML 与 PDF 的汇总口径必须都来自 _quality_summary。

    做法：把 _quality_summary 换成哨兵实现。若某一侧自行判断/自行拼接
    （即阶段 88 之前的写法），它就不会消费哨兵，断言随即失败。
    """
    sentinel = {"mode": "partial", "counts": [("良好", 7), ("未评估", 9)],
                "note": "", "avg": 42.5}
    monkeypatch.setattr(rs, "_quality_summary", lambda stats: sentinel)

    d = tempfile.mkdtemp(prefix="s88_single_")
    hp = os.path.join(d, "s.html")
    pp = os.path.join(d, "s.pdf")
    generate_html(ALL_ASSESSED, title="哨兵", out_path=hp)
    generate_pdf(ALL_ASSESSED, title="哨兵", out_path=pp)

    hs = _html_summary(hp)
    # 哨兵专属数值必须原样出现在 HTML 汇总行里（_html_summary 已剥掉标签）。
    assert "良好 7" in hs and "未评估 9" in hs and "平均分 42.5" in hs, hs
    assert "共 3 条" in hs

    pt = _pdf_text(pp)
    if "质量概况" in pt:
        assert "良好 7" in pt and "未评估 9" in pt, pt
    else:
        pytest.skip("当前环境 PDF 中文字体不可用，跳过 PDF 侧哨兵断言")


def test_old_inline_implementation_would_fail():
    """
    注入式反例：把「修复前」的内联拼接原样搬回来，它必须产生误导输出。

    这条测试的作用是证明上面几条测试**有信号**——如果旧实现也能通过，
    说明测试钉的不是真正的差异。
    """
    def _old_html_stat_line(entries):
        total = len(entries)
        good = warn = review = 0
        scores = []
        for e in entries:
            q = e["quality"]
            if q is not None:
                scores.append(q.score)
                if q.level == "good":
                    good += 1
                elif q.level == "warn":
                    warn += 1
                else:
                    review += 1
        avg = round(sum(scores) / len(scores), 1) if scores else None
        return (f"共 {total} 条"
                + (f" · 良好 {good} · 告警 {warn} · 待复核 {review}" if entries else "")
                + (f" · 平均分 {avg}" if avg is not None else ""))

    old_all_none = _old_html_stat_line(ALL_NONE)
    assert "良好 0 · 告警 0 · 待复核 0" in old_all_none, old_all_none
    # 新实现绝不能再产出这一串。
    hs_new, _ = _render(ALL_NONE)
    assert "良好 0 · 告警 0 · 待复核 0" not in hs_new

    old_partial = _old_html_stat_line(PARTIAL)
    assert "未评估" not in old_partial and "共 3 条 · 良好 1" in old_partial, old_partial
    hs_p, _ = _render(PARTIAL)
    assert "未评估" in hs_p


# ------------------------------------------------------------------------------
# 4. 已核查「非缺陷」的边界：PDF/视频不参与 CLI 质量评估
# ------------------------------------------------------------------------------
def test_cli_quality_line_scope_is_images_only():
    """
    CLI 的「质量评估：需复核 x / 告警 y / 良好 z」只统计单张图片。

    PDF 是多页、视频是多帧，整份文件算 SSIM 没有可比性 —— 因此当一批里
    混有 PDF/视频时，该行计数天然小于「成功 N」，**这是设计而非漏计**。
    这里把它钉住：若日后有人给 PDF/视频也算质量，这条会失败，提示需重新评估。
    """
    import cli

    class _Args:
        evaluate = True

    d = tempfile.mkdtemp(prefix="s88_cli_")
    img = os.path.join(d, "a.png")
    cv2.imwrite(img, np.full((80, 80, 3), 200, np.uint8))
    out = os.path.join(d, "a_out.png")
    cv2.imwrite(out, np.full((80, 80, 3), 60, np.uint8))

    summary = {"success": 0, "skipped": 0, "failed": 0}
    reports: list = []
    # 图片 → 计入质量评估。
    cli._print_result({"status": "success", "kind": "image", "original_name": "a.png",
                       "input_path": img, "out_path": out}, _Args(), summary, reports)
    # PDF / 视频 → 刻意不计入。
    cli._print_result({"status": "success", "kind": "pdf", "original_name": "b.pdf",
                       "input_path": img, "out_path": out}, _Args(), summary, reports)
    cli._print_result({"status": "success", "kind": "video", "original_name": "c.mp4",
                       "input_path": img, "out_path": out}, _Args(), summary, reports)

    assert summary["success"] == 3
    assert len(reports) == 1, "只有单张图片应参与质量评估（PDF/视频不计入）"


# ------------------------------------------------------------------------------
# 5. video_export：复核后未发现缺陷，把「已验证正确」的行为钉住
# ------------------------------------------------------------------------------
def test_extract_one_clamps_out_of_range_index():
    """
    _extract_one 会把越界帧号夹到 [0, total-1]（返回最后一帧），不抛错。

    边界登记：若结果视频被 max_frames 截断（远比源视频短），而调用方拿
    源视频的帧号去抽结果帧，得到的是结果视频的最后一帧 —— 两者并非同一时刻。
    UI 路径不会这样用（帧号来自结果视频自身的抽样），故登记为边界而非缺陷。
    """
    d = tempfile.mkdtemp(prefix="s88_vid_")
    path = os.path.join(d, "v.mp4")
    w, h, n = 64, 48, 3
    wr = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (w, h))
    assert wr.isOpened(), "环境无可用视频编码器"
    for i in range(n):
        wr.write(np.full((h, w, 3), 30 + i * 40, np.uint8))
    wr.release()

    last = _extract_one(path, 9999)
    assert last is not None and last.shape[:2] == (h, w)
    assert _extract_one(os.path.join(d, "nope.mp4"), 0) is None


def test_export_frame_compare_resizes_upscaled_result_back():
    """
    结果视频经真实超分放大后，对比帧必须把结果缩回源尺寸再拼（聚焦去水印）。
    验证方式：给一个 2× 的结果视频，导出对比图的高度应与源帧一致。
    """
    d = tempfile.mkdtemp(prefix="s88_cmp_")
    src = os.path.join(d, "src.mp4")
    res = os.path.join(d, "res.mp4")
    w, h = 64, 48
    w1 = cv2.VideoWriter(src, cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (w, h))
    w2 = cv2.VideoWriter(res, cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (w * 2, h * 2))
    assert w1.isOpened() and w2.isOpened(), "环境无可用视频编码器"
    for i in range(3):
        w1.write(np.full((h, w, 3), 30 + i * 40, np.uint8))
        w2.write(np.full((h * 2, w * 2, 3), 30 + i * 40, np.uint8))
    w1.release()
    w2.release()

    out = export_frame_compare(src, res, 1, out_dir=d)
    assert out and os.path.isfile(out)
    # 落盘文件名含中文，cv2.imread 在 Windows 下读不了非 ASCII 路径，走 imdecode。
    img = cv2.imdecode(np.fromfile(out, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, f"对比帧读不出来：{out}"
    assert img.shape[0] == h, (
        f"结果被放大时应缩回源尺寸；实际对比图高 {img.shape[0]}，源帧高 {h}"
    )
    assert export_contact_sheet(os.path.join(d, "nope.mp4"), n=2, out_dir=d) is None
