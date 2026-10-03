"""阶段 193 · D2 批量 / 媒体页耗时展示（承接 190 单图页 _compose_status 范式）。

覆盖范围：
  ① **共享接缝** `ui.components.format_elapsed`：单一真值来源，三页（单图 / 批量 / 媒体）
     共用同一段 `{seconds:.2f}` 格式化口径；单图页的 `_compose_status` 已改为委托它。
  ② **i18n 双键**：`batch.msg.elapsed` / `media.msg.elapsed` 必须 zh/en 双语言、
     占位符一致（与 `test_i18n_coverage` 的 parity 守卫同口径，这里做针对性钉死）。
  ③ **集成**：`run_pdf` / `run_video` / `run_batch` 三个处理入口在成功路径下，
     必须在返回的多行日志里拼上「本次处理耗时」行（用 monkeypatch 把服务层替换成桩，
     避免拉起 torch / 模型 / 真文件）。

设计取舍：只验证「耗时行是否被拼进产物」，不验证修复质量（那是 core 层的事）；
服务层 / 缓存 / 聚合 Markdown 一律用桩隔离，使断言聚焦、确定性、不依赖外部环境。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config  # noqa: E402
import ui.tab_batch as tb  # noqa: E402
import ui.tab_media as tm  # noqa: E402
import ui.tab_single as ts  # noqa: E402
from ui.components import format_elapsed  # noqa: E402
from utils.i18n import t, t_in  # noqa: E402


def _has_cjk(text: str) -> bool:
    return any(
        "\u3000" <= c <= "\u303f"
        or "\u3400" <= c <= "\u4dbf"
        or "\u4e00" <= c <= "\u9fff"
        or "\uff00" <= c <= "\uffef"
        for c in text
    )


def _noop(*_a, **_k):
    return None


# ---------------------------------------------------------------------------
# ① 共享接缝
# ---------------------------------------------------------------------------
def test_format_elapsed_default_key_is_single():
    assert format_elapsed(1.5).startswith("\n⏱️")
    assert format_elapsed(2.0) == t("single.msg.elapsed", seconds=2.0)


def test_format_elapsed_respects_caller_key():
    s = format_elapsed(3.25, key="batch.msg.elapsed")
    assert "3.25" in s and "批量" in s
    assert "3.25" in format_elapsed(3.25, key="media.msg.elapsed")


def test_format_elapsed_english_has_no_cjk():
    en = t_in("en-US", "single.msg.elapsed")
    assert not _has_cjk(en)
    assert "1.23" in format_elapsed(1.23, key="single.msg.elapsed")


def test_tab_single_compose_status_still_delegates_to_format_elapsed(monkeypatch):
    # 190 的 _compose_status 改为委托 format_elapsed；这里钉死该契约不被悄然改回硬编码。
    seen = {}
    monkeypatch.setattr(
        ts, "format_elapsed",
        lambda secs, **kw: seen.setdefault("v", f"<ELAPSED {secs}>"),
    )
    out = ts._compose_status("lama", "/tmp/out.png", 4.0, upscaled_note="")
    assert "<ELAPSED 4.0>" in out


# ---------------------------------------------------------------------------
# ② i18n 双键
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["batch.msg.elapsed", "media.msg.elapsed", "single.msg.elapsed"])
def test_elapsed_key_parity_zh_en_and_placeholder(key):
    zh = t_in("zh-CN", key)
    en = t_in("en-US", key)
    assert zh != key, f"{key} 缺中文"
    assert en != key, f"{key} 缺英文"
    assert not _has_cjk(en), f"{key} 英文含 CJK：{en!r}"
    # 占位符必须一致（{seconds:.2f}），否则中英文格式错位。
    assert "{seconds:" in zh and "{seconds:" in en


# ---------------------------------------------------------------------------
# ③ 集成：服务层用桩隔离
# ---------------------------------------------------------------------------
def _fake_page(**kw):
    base = dict(status="success", error=None, mask_pixels=10, recommended_preset=None,
                recommendation=None, index=1, seconds=0.4, stats={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_run_pdf_includes_elapsed(tmp_path, monkeypatch):
    out = tmp_path / "out.pdf"
    out.write_bytes(b"%PDF-1.4 fake")
    res = SimpleNamespace(
        status="success", out_path=str(out), pages=[_fake_page()],
        summary=lambda: "统计：1 页",
    )
    monkeypatch.setattr("services.pdf_service.is_pdf_available", lambda: True)
    monkeypatch.setattr("services.pdf_service.process_pdf", lambda *a, **k: res)
    monkeypatch.setattr(tm, "validate_pdf_file", lambda *a, **k: None)
    monkeypatch.setattr(tm._CACHE, "add_pdf_record", lambda *a, **k: None)
    monkeypatch.setattr(tm, "build_aggregate_detection_stats_markdown", lambda *a, **k: "")

    log, _upd, _stats = tm.run_pdf(
        file_value=str(tmp_path / "in.pdf"),
        pages_spec="", dpi=150, engine="lama",
        strength=1.0, dilate=1, feather=1, threshold=1, denoise=1,
        sensitivity=0.0,
        auto_preset=False, preset="ai_logo", progress=_noop,
    )
    assert "耗时" in log and "秒" in log


def test_run_video_includes_elapsed(tmp_path, monkeypatch):
    out = tmp_path / "out.mp4"
    out.write_bytes(b"fake")
    res = SimpleNamespace(
        status="success", out_path=str(out),
        summary=lambda: "统计", width=1280, height=720, fps=30.0, codec="h264",
        upscaled=False, warnings=[], preset_counts={}, recommend_conf_avg=0.0,
        frame_stats=[],
    )
    monkeypatch.setattr("services.video_service.process_video", lambda *a, **k: res)
    monkeypatch.setattr("services.video_service.build_audio_merge_command", lambda *a, **k: "ffmpeg ...")
    monkeypatch.setattr(tm, "validate_video_file", lambda *a, **k: None)
    monkeypatch.setattr(tm._CACHE, "add_video_record", lambda *a, **k: None)
    monkeypatch.setattr(tm, "build_aggregate_detection_stats_markdown", lambda *a, **k: "")
    # 输出文件不存在 ⇒ 浏览抽样返回 []，避免真实抽帧；这里直接钉死桩。
    monkeypatch.setattr(tm, "_build_video_browse_gallery", lambda *a, **k: [])

    log, _upd, _cmd, _stats, _gallery, _exp = tm.run_video(
        file_value=str(tmp_path / "in.mp4"),
        frame_step=3, max_frames=10, reuse_mask=False, engine="lama",
        strength=1.0, dilate=1, feather=1, threshold=1, denoise=1,
        sensitivity=0.0,
        real_superres=False, real_scale=1, real_backend="auto",
        auto_preset=False, preset="ai_logo", progress=_noop,
    )
    assert "耗时" in log and "秒" in log


def test_run_batch_includes_elapsed(tmp_path, monkeypatch):
    img = tmp_path / "a.png"
    img.write_bytes(b"x")

    class _FakeQueue:
        def __init__(self):
            self._tasks = []

        def submit(self, name, path):
            self._tasks.append((name, path))

        def summary(self):
            n = len(self._tasks)
            return {"current": "x", "done": n, "failed": 0, "cancelled": 0,
                    "total": n, "progress": 1.0, "finished": n}

        def run(self, processor, on_update=None):
            for name, path in self._tasks:
                task = SimpleNamespace(
                    status=tb.TaskStatus.DONE, name=name, path=path, error="",
                    result={"result_path": str(path), "status": "ok", "stats": None,
                            "recommendation": None, "recommended_preset": None},
                )
                if on_update:
                    on_update(task)
                yield task

    monkeypatch.setattr(tb, "BatchQueue", _FakeQueue)
    monkeypatch.setattr(tb, "build_detection_stats_markdown", lambda *a, **k: "")
    monkeypatch.setattr(tb, "build_aggregate_detection_stats_markdown", lambda *a, **k: "")

    gen = tb.run_batch(
        folder_value=str(img), files_value=None, mask_folder_value=None,
        mode="auto", engine="lama",
        strength=1.0, dilate=1, feather=1, threshold=1, denoise=1,
        sensitivity=0.0,
        poisson_blend=False, poisson_strength=0.0,
        texture_transfer=False, texture_amount=0.0,
        artifact_reduce=False, artifact_amount=0.0,
        superres=False, superres_amount=0.0,
        real_superres=False, real_scale=1, real_backend="auto",
        evaluate=False, compare=False, preset="ai_logo",
        auto_preset=False, dual_run=None, progress=_noop,
    )
    logs = [log for (_frac, log, _g, _s, _m) in gen]
    assert logs, "run_batch 未产出任何 yield"
    final = logs[-1]
    assert "耗时" in final and "秒" in final
