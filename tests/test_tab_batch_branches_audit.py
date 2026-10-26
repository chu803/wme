# -*- coding: utf-8 -*-
"""
阶段 281 · `ui/tab_batch` 深度续审（5j 复审链延续，接 276–280）。

首轮审计（阶段 26/97/110/164/191/193）已覆盖：
  · 纯助手 `_normalize_file_value` / `_mask_path_for` / `_resolve_batch_preset` /
    `_preset_display` / `_build_editor_value` 的契约（26/164/191）；
  · run_batch 早退路径 yield 5 值（阶段 97 I1）、render_pair 异常兜底（I2）；
  · 全 ui 接线契约（110）与耗时行（193）、任务级 i18n 双语（164）。

本轮按「契约输入类别枚举」钉死**首轮遗漏的成功/边界分支不变量**，并含**一个真缺陷修复**：

【真缺陷 · 已修复】画廊点选空序列索引崩溃
  `on_gallery_select` / `on_gallery_select_with_mode` / `tab_history._on_gallery_select`
  归整 `(row, col)` 索引时写作 `if isinstance(idx, (tuple, list)) and idx:`——
  **空**序列 `()` / `[]` 能过 `isinstance` 但过不了 `and idx`（空序列为 falsy），
  于是未被规整就落到 `int(())` ⇒ **TypeError** 穿透到 Gradio 回调，违反函数自身
  文档承诺「索引越界…置 None 并给出提示文案」。探针实证（`.scratch/probe_tab_batch_reaudit.py`）：
  `idx=()` → `TypeError: int() argument must be... not 'tuple'`。
  修复：三处统一改为 `if isinstance(idx, (tuple, list)): idx = idx[0] if idx else None`
  （LESSON 265：同一根因往往不止一处，三处一并修）。

【本轮钉死的其余遗漏边界】
  · `export_report_from_batch`：空产出 / 成功 / fmt 缺省 / FileNotFoundError / 通用异常 四分支；
  · run_batch `_on_update` DONE 子分支：画廊缩略图（对比数组 vs 结果路径）、
    配对表与报告源登记、质量 caption、未识别 level → 空图标、推荐理由 vs 预设展示行；
  · FAILED / CANCELLED 日志分支。

守卫均用可控桩（FakeQueue / 桩 export_report / 桩 evaluate_quality / 桩 make_compare_array）驱动
**真实执行路径**，不依赖 GPU / 真实模型 / 网络。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ui.tab_batch as tb  # noqa: E402
import ui.tab_history as th  # noqa: E402
from utils.i18n import t as _t  # noqa: E402


def _noop(*a, **k):
    return None


def _png(path: Path, val=200) -> str:
    cv2.imwrite(str(path), np.full((6, 8, 3), val, np.uint8))
    return str(path)


class _Evt:
    """最小 SelectData 替身：仅暴露 index。"""

    def __init__(self, index):
        self.index = index


# --------------------------------------------------------------------------- #
# 1 · 真缺陷回归：空序列索引必须降级为「未选中」而非崩溃
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_idx", [(), [], None, 99, -1])
def test_batch_gallery_select_empty_seq_degrades(bad_idx, tmp_path, monkeypatch):
    """空序列索引 `()` / `[]` 不得抛 TypeError，须与 None/越界同路径降级。"""
    orig, res = _png(tmp_path / "o.png"), _png(tmp_path / "r.png", 60)
    monkeypatch.setattr(tb, "_batch_pairs", [(orig, res, "x.png")])
    monkeypatch.setattr(tb, "render_pair", lambda pair, mode: ("H", "S"))
    pair, html, summary = tb.on_gallery_select(_Evt(bad_idx))
    assert pair is None and html == ""
    assert summary == _t("compare.viewer.no_select"), f"idx={bad_idx!r} 未按契约降级"
    # 同一修复覆盖 with_mode 变体。
    pair2, html2, summary2 = tb.on_gallery_select_with_mode(_Evt(bad_idx), "concat")
    assert pair2 is None and html2 == ""
    assert summary2 == _t("compare.viewer.no_select")


def test_history_gallery_select_empty_seq_degrades(tmp_path, monkeypatch):
    """`tab_history.on_select` 同型点选也须降级（同一根因第三处）。

    返回六元组 (详情 md, 结果路径, 视频路径, 对比 pair, HTML, 摘要 md)；
    空序列索引须走「未选中」分支：pair/结果/视频均为 None，且不抛 TypeError。
    """
    monkeypatch.setattr(th, "_HISTORY_ID_MAP", [("rec1",)])
    for bad in ((), []):
        detail, res_path, video_path, pair, html, summary = th.on_select(_Evt(bad), "concat")
        assert pair is None and res_path is None and video_path is None
        assert html == "" and summary == _t("compare.viewer.no_select")
        assert detail == _t("history.msg.no_select"), f"idx={bad!r} 未按契约降级"


@pytest.mark.parametrize("idx, expect_set", [
    ((0, 1), True),   # (row, col) 规整
    ([0], True),
    (0, True),
])
def test_batch_gallery_select_nonempty_seq_still_resolves(idx, expect_set, tmp_path, monkeypatch):
    """非空序列索引仍须正常解析（修复不得破坏既有成功路径）。"""
    orig, res = _png(tmp_path / "o.png"), _png(tmp_path / "r.png", 60)
    monkeypatch.setattr(tb, "_batch_pairs", [(orig, res, "x.png")])
    monkeypatch.setattr(tb, "render_pair", lambda pair, mode: (f"H[{mode}]", "S"))
    pair, html, _ = tb.on_gallery_select(_Evt(idx))
    assert (pair is not None) is expect_set
    assert html == "H[concat]"


# --------------------------------------------------------------------------- #
# 2 · export_report_from_batch：四分支
# --------------------------------------------------------------------------- #
def test_export_from_batch_empty_returns_hint(monkeypatch):
    monkeypatch.setattr(tb, "_batch_run_products", [])
    path, msg = tb.export_report_from_batch("html")
    assert path is None and msg == _t("batch.msg.report_empty")


def test_export_from_batch_success_returns_path(monkeypatch):
    monkeypatch.setattr(tb, "_batch_run_products", [tb.ReportSource(original_path="a", result_path="b")])
    monkeypatch.setattr(tb, "export_report", lambda srcs, fmt="html": "/tmp/r.html")
    path, msg = tb.export_report_from_batch("html")
    assert path == "/tmp/r.html" and _t("batch.report_ok", path="/tmp/r.html") == msg


def test_export_from_batch_fmt_none_defaults_to_html(monkeypatch):
    """fmt 缺省/None ⇒ 传给 export_report 的必须是 'html'（不得是 None）。"""
    seen = {}
    def _cap(srcs, fmt="html"):
        seen["fmt"] = fmt
        return "/tmp/r.html"
    monkeypatch.setattr(tb, "_batch_run_products", [tb.ReportSource(original_path="a", result_path="b")])
    monkeypatch.setattr(tb, "export_report", _cap)
    tb.export_report_from_batch(None)
    assert seen["fmt"] == "html", f"fmt=None 未归一为 html，而是 {seen['fmt']!r}"


def test_export_from_batch_filenotfound_is_handled(monkeypatch):
    def _fnf(srcs, fmt="html"):
        raise FileNotFoundError("no records")
    monkeypatch.setattr(tb, "_batch_run_products", [tb.ReportSource(original_path="a", result_path="b")])
    monkeypatch.setattr(tb, "export_report", _fnf)
    path, msg = tb.export_report_from_batch("html")
    assert path is None and "no records" in msg, "FileNotFoundError 须转成可读提示而非上抛"


def test_export_from_batch_generic_exception_is_handled(monkeypatch):
    def _boom(srcs, fmt="html"):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(tb, "_batch_run_products", [tb.ReportSource(original_path="a", result_path="b")])
    monkeypatch.setattr(tb, "export_report", _boom)
    path, msg = tb.export_report_from_batch("html")
    assert path is None and "kaboom" in msg, "通用异常须被兜住（log + 可读提示），不得上抛"


# --------------------------------------------------------------------------- #
# 3 · run_batch `_on_update` DONE 子分支（FakeQueue 驱动真实生成器）
# --------------------------------------------------------------------------- #
class _FakeQueue:
    """受控队列：submit 无副作用，run 逐个调用 on_update 后 yield。"""

    def __init__(self, tasks):
        self._tasks = tasks

    def submit(self, name, path):
        pass

    def summary(self):
        n = len(self._tasks)
        return {"current": "x", "done": n, "failed": 0, "cancelled": 0,
                "total": n, "progress": 1.0, "finished": n}

    def run(self, processor, on_update=None):
        for t in self._tasks:
            if on_update:
                on_update(t)
            yield t


def _done(name, path, result):
    return SimpleNamespace(status=tb.TaskStatus.DONE, name=name, path=path,
                           error="", result=result)


def _q(level, score, detail_key="quality.detail_good"):
    """质量报告替身：字段必须与 `services.quality_service.QualityReport` 同源（阶段 337）。

    旧替身带的是 `detail="d"`（一个中文句子字段），而真类已改成「键 + 数字」。
    替身与真类不同源时，`ui/tab_batch` 里的 `detail_text()` 会抛 AttributeError，
    而那一行外层原本写着宽口子 `except Exception: log.warning(...)` ⇒ 测试全绿、真实界面少一行。
    337 已把那处改为「编程错误一律上抛」，替身也必须跟着同步。
    """
    return SimpleNamespace(level=level, score=score, detail_key=detail_key,
                           detail_base_key="", review_floor=0)


def _run(tasks, *, evaluate=False, compare=False, auto_preset=False,
         quality=None, tmp_path=None, tag="x"):
    """以受控桩驱动 run_batch 到结束，返回最后一个 yield 的 5 元组。"""
    img = _png(tmp_path / f"in_{tag}.png")
    res = _png(tmp_path / f"res_{tag}.png", 60)
    mq = lambda: _FakeQueue(tasks(img, res))
    monkey = pytest.MonkeyPatch()
    monkey.setattr(tb, "BatchQueue", mq)
    monkey.setattr(tb, "build_detection_stats_markdown", lambda *a, **k: "")
    monkey.setattr(tb, "build_aggregate_detection_stats_markdown", lambda *a, **k: "")
    monkey.setattr(tb, "evaluate_quality", lambda a, b: quality if quality else _q("good", 88))
    monkey.setattr(tb, "make_compare_array", lambda o, rr: np.full((6, 16, 3), 7, np.uint8))
    try:
        gen = tb.run_batch(
            folder_value=img, files_value=None, mask_folder_value=None,
            mode="auto", engine="lama", strength=1.0, dilate=1, feather=1,
            threshold=1, denoise=1, sensitivity=0.0, poisson_blend=False,
            poisson_strength=0.0, texture_transfer=False, texture_amount=0.0,
            artifact_reduce=False, artifact_amount=0.0, superres=False,
            superres_amount=0.0, real_superres=False, real_scale=1,
            real_backend="auto", evaluate=evaluate, compare=compare,
            preset="ai_logo", auto_preset=auto_preset, dual_run=None, progress=_noop,
        )
        yields = list(gen)
        return yields[-1]
    finally:
        monkey.undo()


def test_coding_error_in_quality_render_is_not_swallowed(tmp_path):
    """宽口子只该吞「数据/环境」类异常；**缺字段这种编程错误必须冒出来**（阶段 337）。

    现场：337 把 `QualityReport.detail` 换成键之后，替身还带旧字段 ⇒ `detail_text()` 抛
    AttributeError，而调用点外面原本写着 `except Exception: log.warning("质量评估失败…")`
    ⇒ 用例全绿、批次照跑，只有界面**悄悄少了一行诊断**。这类静默降级比崩掉更难发现，
    所以这里用「旧形状的替身」把那条路钉住：必须炸，不许被吞。
    """
    def tasks(img, res):
        return [_done("a.png", img, {"result_path": res, "status": "ok",
                                     "stats": None, "recommendation": None,
                                     "recommended_preset": None})]

    bad = SimpleNamespace(level="good", score=88, detail="337 后已不存在的旧字段")
    with pytest.raises(AttributeError):
        _run(tasks, evaluate=True, quality=bad, tmp_path=tmp_path, tag="loud")


def test_on_update_compare_thumbnail_is_array(tmp_path):
    """compare=True ⇒ 画廊项是 Before|After 内存数组（非路径字符串）。"""
    def tasks(img, res):
        return [_done("a.png", img, {"result_path": res, "status": "ok",
                 "stats": None, "recommendation": None, "recommended_preset": None})]
    last = _run(tasks, compare=True, tmp_path=tmp_path, tag="cmp")
    thumb = last[2][0][0]
    assert isinstance(thumb, np.ndarray), "compare=True 画廊缩略图应是拼接数组"


def test_on_update_no_compare_thumbnail_is_path(tmp_path):
    """compare=False ⇒ 画廊项退回结果路径字符串。"""
    def tasks(img, res):
        return [_done("a.png", img, {"result_path": res, "status": "ok",
                 "stats": None, "recommendation": None, "recommended_preset": None})]
    last = _run(tasks, compare=False, tmp_path=tmp_path, tag="nocmp")
    thumb = last[2][0][0]
    assert isinstance(thumb, str) and thumb, "compare=False 画廊缩略图应是结果路径"


def test_on_update_registers_pairs_and_report_sources(tmp_path):
    """每个成功条目须同序登记：画廊项 / 配对表 / 报告源三者计数一致。"""
    def tasks(img, res):
        return [_done(f"{i}.png", img, {"result_path": res, "status": "ok",
                 "stats": None, "recommendation": None, "recommended_preset": None})
                for i in range(3)]
    last = _run(tasks, tmp_path=tmp_path, tag="reg")
    assert len(last[2]) == 3, "画廊应有 3 项"
    assert len(tb._batch_pairs) == 3, "配对表须与画廊同序同长"
    assert len(tb._batch_run_products) == 3, "报告源须与画廊同序同长"


@pytest.mark.parametrize("level, expect_icon", [
    ("good", "✅"), ("warn", "⚠️"), ("review", "🔍"), ("unknown_level", ""),
])
def test_on_update_quality_caption_icons(level, expect_icon, tmp_path):
    """质量 caption：good/warn/review 各有图标；**未识别 level ⇒ 空图标**（不崩）。"""
    def tasks(img, res):
        return [_done("a.png", img, {"result_path": res, "status": "ok",
                 "stats": None, "recommendation": None, "recommended_preset": None})]
    last = _run(tasks, evaluate=True, quality=_q(level, 77), tmp_path=tmp_path, tag=f"q_{level}")
    caption = last[2][0][1]
    assert "77" in caption, f"caption 缺质量分：{caption}"
    if expect_icon:
        assert expect_icon in caption, f"level={level} 缺图标 {expect_icon}：{caption}"
    else:
        assert caption.rstrip().endswith("分"), f"未识别 level 应无图标尾巴：{caption}"


def test_on_update_failed_and_cancelled_log_branches(tmp_path):
    """FAILED / CANCELLED 各写一条日志，且不进画廊。"""
    def tasks(img, res):
        return [
            SimpleNamespace(status=tb.TaskStatus.FAILED, name="f.png", path=img,
                            error="boom", result=None),
            SimpleNamespace(status=tb.TaskStatus.CANCELLED, name="c.png", path=img,
                            error="", result=None),
        ]
    last = _run(tasks, tmp_path=tmp_path, tag="fc")
    log = last[1]
    assert "f.png" in log and "boom" in log, "FAILED 须写失败日志"
    assert "c.png" in log, "CANCELLED 须写取消日志"
    assert last[2] == [], "失败/取消条目不得进画廊"


def test_on_update_recommendation_adds_reason_line(tmp_path):
    """推荐结果存在 ⇒ 追加「推荐理由」行（含置信度百分比）。"""
    def tasks(img, res):
        return [_done("a.png", img, {"result_path": res, "status": "ok", "stats": None,
                 "recommendation": {"preset": "ai_tiled", "confidence": 0.9,
                   "triggered": ["tiled"], "scores": {"tiled": 0.9}},
                 "recommended_preset": "ai_tiled"})]
    last = _run(tasks, auto_preset=True, tmp_path=tmp_path, tag="rec")
    log = last[1]
    assert "90%" in log, f"推荐理由行应含置信度：\n{log}"


def test_on_update_recommend_preset_distribution_logged(tmp_path):
    """开启智能推荐且有分布 ⇒ 末尾追加预设分布行（`标签×N`）。"""
    def tasks(img, res):
        return [_done(f"{i}.png", img, {"result_path": res, "status": "ok", "stats": None,
                 "recommendation": {"preset": "ai_tiled", "confidence": 0.9,
                   "triggered": ["tiled"], "scores": {"tiled": 0.9}},
                 "recommended_preset": "ai_tiled"}) for i in range(2)]
    last = _run(tasks, auto_preset=True, tmp_path=tmp_path, tag="dist")
    log = last[1]
    assert "×2" in log, f"预设分布行应含计数 ×2：\n{log}"
