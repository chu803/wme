# -*- coding: utf-8 -*-
"""Stage 169 独立验证脚本（不触发 pytest basetemp 清理）。

验证：
  1. i18n parity：report.* 全部键 zh-CN/en-US 双侧存在且占位符同名。
  2. en-US 生成 HTML 报告零 CJK（除显式允许的产品名/连字符等）。
  3. zh-CN 仍含中文（不被破坏）。
  4. _quality_summary 在 en-US 下返回英文标签。
"""
import os as _os
import re as _re
import sys as _sys
import tempfile as _tempfile

_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import traceback as _tb
import time as _time_mod


def _time_now():
    return _time_mod.time()


def _exchook(et, ev, tb):
    msg = "".join(_tb.format_exception(et, ev, tb))
    try:
        with open(".verify169.result.txt", "w", encoding="utf-8") as f:
            f.write("CHECKS=0 FAILS=1\nEXCEPTION:\n" + msg.encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


_sys.excepthook = _exchook

import config
import numpy as np
import cv2  # type: ignore
import utils.i18n as i18n
from utils.i18n import t, t_in, get_language, set_language, validate_i18n_parity
import services.report_service as rs

CJK = _re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")

REPORT_KEYS = [
    "report.title", "report.summary_total", "report.summary_avg",
    "report.note_no_quality", "report.quality_good", "report.quality_warn",
    "report.quality_review", "report.quality_unassessed", "report.unassessed_quality",
    "report.alt_compare", "report.quality_score", "report.lbl_mode_engine", "report.lbl_time",
    "report.lbl_params", "report.lbl_name", "report.lbl_recommend",
    "report.pdf_gen_time", "report.pdf_total", "report.pdf_quality_overview",
    "report.pdf_avg_score", "report.empty_records", "report.err_png_encode",
    "report.err_no_records", "report.log_html_done", "report.log_pdf_done",
    "report.log_compare_fail", "report.log_quality_fail", "report.log_pdf_img_fail",
    "report.log_pdf_fallback",
]

fails = []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    if not cond:
        fails.append(msg)


# ---- 1. i18n parity ----
problems = validate_i18n_parity()
report_problems = [p for p in problems if "report." in p]
check(not report_problems, f"validate_i18n_parity 含 report.* 问题：{report_problems}")
for k in REPORT_KEYS:
    zh = t_in("zh-CN", k)
    en = t_in("en-US", k)
    check(zh != k, f"键 {k} 在 zh-CN 缺失（回退为键名）")
    check(en != k, f"键 {k} 在 en-US 缺失（回退为键名）")
    # 占位符同名
    pb = set(_re.findall(r"\{([a-zA-Z_]\w*)\}", zh))
    pe = set(_re.findall(r"\{([a-zA-Z_]\w*)\}", en))
    check(pb == pe, f"键 {k} 占位符不一致 zh={sorted(pb)} en={sorted(pe)}")


# ---- 2. en-US 生成零 CJK ----
set_language("en-US")
_orig_app = config.APP_NAME
config.APP_NAME = "WME"  # 产品名为专有名词，测试中替换为 ASCII 以隔离本地化泄漏
try:
    tmp_o = _tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_r = _tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    cv2.imwrite(tmp_o.name, np.full((200, 300, 3), 235, dtype=np.uint8))
    cv2.imwrite(tmp_r.name, np.full((200, 300, 3), 60, dtype=np.uint8))
    tmp_o.close(); tmp_r.close()

    srcs = [rs.ReportSource(original_path=tmp_o.name, result_path=tmp_r.name,
                            original_name="test.png", engine="opencv", mode="auto",
                            params="strength=50", ts=_time_now())]
    html_tmp = _tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    html_tmp.close()
    html_path = rs.export_report(srcs, fmt="html", out_path=html_tmp.name)
    with open(html_path, "r", encoding="utf-8") as f:
        html_text = f.read()

    cjk_hits = CJK.findall(html_text)
    if cjk_hits:
        cps = sorted({f"U+{ord(c):04X}" for c in cjk_hits})
        idx = CJK.search(html_text)
        ctx = html_text[max(0, idx.start() - 30):idx.start() + 30].encode("ascii", "replace").decode("ascii")
        check(False, f"en-US HTML 含 CJK {cps} 上下文: ...{ctx}...")
    else:
        check(True, "")

    # _quality_summary 英文标签
    stats = {"total": 3, "good": 1, "warn": 1, "review": 1, "assessed": 3,
             "unassessed": 0, "avg": 0.8}
    qsum = rs._quality_summary(stats)
    labels = [c[0] for c in qsum["counts"]]
    check(all(not CJK.search(l) for l in labels), f"en-US 质量标签含 CJK：{labels}")
    check(labels == ["Good", "Warning", "Needs review"], f"en-US 质量标签不符预期：{labels}")

    # note 英文
    qsum_none = rs._quality_summary({"total": 3, "good": 0, "warn": 0, "review": 0,
                                     "assessed": 0, "unassessed": 3, "avg": None})
    check(not CJK.search(qsum_none["note"]), f"en-US note 含 CJK：{qsum_none['note']}")

    # 直接覆盖 _card_html 全部路径（alt + quality_score + 未评估 + 三种 level）
    from services.quality_service import QualityReport
    base_e = {"name": "x.png", "engine": "opencv", "mode": "auto", "params": "p",
              "time_str": "2026-01-01 00:00:00", "img_b64": "", "img_w": 10, "img_h": 10,
              "img_is_compare": True, "result_path": "x.png", "recommendation": None,
              "quality": None}
    for lvl in ("good", "warn", "review"):
        fe = dict(base_e)
        fe["quality"] = QualityReport(ssim=0.9, changed_ratio=0.1, score=70, level=lvl, detail="d")
        c = rs._card_html(fe)
        check(not CJK.search(c), f"en-US _card_html(level={lvl}) 含 CJK：{CJK.findall(c)[:6]}")
    check(not CJK.search(rs._card_html(base_e)),
          f"en-US _card_html(未评估) 含 CJK：{CJK.findall(rs._card_html(base_e))[:6]}")

    for p in (tmp_o.name, tmp_r.name, html_path):
        try:
            _os.remove(p)
        except OSError:
            pass
finally:
    config.APP_NAME = _orig_app


# ---- 3. zh-CN 不被破坏 ----
set_language("zh-CN")
tmp_o = _tempfile.NamedTemporaryFile(suffix=".png", delete=False)
tmp_r = _tempfile.NamedTemporaryFile(suffix=".png", delete=False)
cv2.imwrite(tmp_o.name, np.full((200, 300, 3), 235, dtype=np.uint8))
cv2.imwrite(tmp_r.name, np.full((200, 300, 3), 60, dtype=np.uint8))
tmp_o.close(); tmp_r.close()
srcs = [rs.ReportSource(original_path=tmp_o.name, result_path=tmp_r.name,
                        original_name="测试图.png", engine="opencv", mode="auto",
                        params="力度=50", ts=_time_now())]
html_tmp = _tempfile.NamedTemporaryFile(suffix=".html", delete=False)
html_tmp.close()
html_path = rs.export_report(srcs, fmt="html", out_path=html_tmp.name)
with open(html_path, "r", encoding="utf-8") as f:
    zh_text = f.read()
check("修复报告" in zh_text, "zh-CN 标题缺失「修复报告」")
check("良好" in zh_text and "告警" in zh_text and "待复核" in zh_text, "zh-CN 质量标签缺失")
for p in (tmp_o.name, tmp_r.name, html_path):
    try:
        _os.remove(p)
    except OSError:
        pass

# ---- 4. 空记录错误文案（en-US） ----
set_language("en-US")
try:
    rs.export_report([])
    check(False, "空记录未抛 FileNotFoundError")
except FileNotFoundError as exc:
    check(not CJK.search(str(exc)), f"en-US 空记录错误含 CJK：{exc}")


def _ascii(s):
    return str(s).encode("ascii", "replace").decode("ascii")


result_lines = [f"CHECKS={checks} FAILS={len(fails)}"]
if fails:
    result_lines.append("FAILURES:")
    for m in fails:
        result_lines.append("  - " + _ascii(m))
else:
    result_lines.append("ALL OK")
with open(".verify169.result.txt", "w", encoding="utf-8") as rf:
    rf.write("\n".join(result_lines) + "\n")
print(_ascii("\n".join(result_lines)))
if fails:
    _sys.exit(1)
