# -*- coding: utf-8 -*-
"""阶段 318b ·「只动水印区」在**四个门面**上的真实转发守卫。

为什么不能只测签名（LESSONS #298 / #304）：参数写进签名但忘了往下传，接口照样"存在"、
单元测试照样绿，而能力对调用方不可达。这里的每一例都**替换下游函数并捕获实参**，
断言 `preserve_untouched` 真的走到了下一层；再加一条端到端，证明它是**有效**的（不只是被转发）。

门面清单（与 315 的对称性守卫同一思路）：
    api.remove_watermark → process_file
    api.remove_watermark_bytes / array → process_image
    api.batch_remove → remove_watermark
    api.remove_watermark_pdf / video → services.process_pdf / process_video
    server._collect_params（REST 白名单）
    cli --preserve-untouched（argparse 三态）+ → process_file / process_pdf / process_video
"""
import base64
import inspect
import os
import sys

import numpy as np
import pytest
import importlib.util

# 阶段 358：CI 只装 requirements-min/dev（**不含 PyMuPDF**）⇒ PDF 相关用例必须**跳过**，
# 而不是让 `process_pdf` 抛「PDF 依赖缺失」把用例打红（这条正是 358 抓到 CI 连红的第三因）。
_HAS_FITZ = importlib.util.find_spec("fitz") is not None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import api  # noqa: E402
import cli  # noqa: E402
import config  # noqa: E402
import server  # noqa: E402


def _png_bytes(w=64, h=64) -> bytes:
    import cv2
    arr = np.zeros((h, w, 3), np.uint8)
    arr[:, :, 0] = np.arange(w, dtype=np.uint8)[None, :]
    ok, buf = cv2.imencode(".png", arr)
    assert ok
    return buf.tobytes()


def _fake_core(arr):
    return {"status": "success", "result_rgb": arr,
            "mask": np.zeros(arr.shape[:2], np.uint8),
            "mask_pixels": 0, "regions": 0, "used_ocr": False,
            "engine": "opencv", "recommended_preset": "ai_logo"}


# ---------------------------------------------------------------------------
# 1. 逐门面转发
# ---------------------------------------------------------------------------
def test_remove_watermark_forwards(monkeypatch):
    cap = {}
    monkeypatch.setattr(api, "process_file",
                        lambda src, **kw: (cap.update(kw), None)[1])
    try:
        api.remove_watermark("x.png", preserve_untouched=True, allow_auto_erase=True)
    except Exception:  # noqa: BLE001 - 假返回值不完整，只关心转发
        pass
    assert cap.get("preserve_untouched") is True


def test_remove_watermark_bytes_forwards(monkeypatch):
    cap = {}
    monkeypatch.setattr(api, "process_image",
                        lambda arr, **kw: (cap.update(kw), _fake_core(arr))[1])
    api.remove_watermark_bytes(_png_bytes(), preserve_untouched=True,
                              allow_auto_erase=True)
    assert cap.get("preserve_untouched") is True


def test_remove_watermark_array_forwards(monkeypatch):
    cap = {}
    monkeypatch.setattr(api, "process_image",
                        lambda arr, **kw: (cap.update(kw), _fake_core(arr))[1])
    api.remove_watermark_array(np.zeros((32, 32, 3), np.uint8),
                               preserve_untouched=False)
    assert cap.get("preserve_untouched") is False, "显式 False 必须原样下传（不得被 None 吞掉）"


def test_batch_remove_forwards(monkeypatch, tmp_path):
    cap = {}
    monkeypatch.setattr(api, "remove_watermark",
                        lambda p, **kw: (cap.update(kw),
                                         api.RemoveResult(original_name="x.png",
                                                          status="success"))[1])
    api.batch_remove(["a.png"], str(tmp_path), preserve_untouched=True)
    assert cap.get("preserve_untouched") is True


def test_pdf_api_forwards(monkeypatch):
    import services.pdf_service as pdfs
    cap = {}
    monkeypatch.setattr(pdfs, "process_pdf",
                        lambda src, dst=None, **kw: (cap.update(kw), object())[1])
    api.remove_watermark_pdf("x.pdf", dst="y.pdf", preserve_untouched=True)
    assert cap.get("preserve_untouched") is True


def test_video_api_forwards(monkeypatch):
    import services.video_service as vids
    cap = {}
    monkeypatch.setattr(vids, "process_video",
                        lambda src, dst=None, **kw: (cap.update(kw), object())[1])
    api.remove_watermark_video("x.mp4", dst="y.mp4", preserve_untouched=True)
    assert cap.get("preserve_untouched") is True


# ---------------------------------------------------------------------------
# 2. 服务层再往下一层（pdf/video → process_image）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _HAS_FITZ, reason="需要 PyMuPDF（CI 最小依赖不含）")
def test_process_pdf_reaches_process_image(monkeypatch, tmp_path):
    import fitz
    import services.pdf_service as pdfs

    src = str(tmp_path / "t.pdf")
    doc = fitz.open()
    page = doc.new_page(width=64, height=64)
    page.insert_image(fitz.Rect(0, 0, 64, 64), stream=_png_bytes())
    doc.save(src)
    doc.close()

    cap = {}

    def _fake(arr, **kw):
        cap.update(kw)
        return _fake_core(arr)

    monkeypatch.setattr(pdfs, "process_image", _fake)
    pdfs.process_pdf(src, str(tmp_path / "o.pdf"), preserve_untouched=True,
                     engine="opencv")
    assert cap.get("preserve_untouched") is True, "PDF 逐页修复未把开关传下去"


def test_process_video_reaches_process_image(monkeypatch, tmp_path):
    import cv2
    import services.video_service as vids

    src = str(tmp_path / "t.mp4")
    vw = cv2.VideoWriter(src, cv2.VideoWriter_fourcc(*"mp4v"), 5, (64, 64))
    assert vw.isOpened(), "本机无 mp4v 编码器，无法构造视频样本"
    for _ in range(3):
        vw.write(np.zeros((64, 64, 3), np.uint8))
    vw.release()

    cap = {}

    def _fake(arr, **kw):
        cap.update(kw)
        return _fake_core(arr)

    monkeypatch.setattr(vids, "process_image", _fake)
    vids.process_video(src, str(tmp_path / "o.mp4"), max_frames=3,
                       preserve_untouched=True, engine="opencv")
    assert cap.get("preserve_untouched") is True, "视频逐帧修复未把开关传下去"


# ---------------------------------------------------------------------------
# 3. REST 白名单
# ---------------------------------------------------------------------------
def test_collect_params_accepts_and_normalizes():
    out = server._collect_params({"preserve_untouched": "true"})
    assert out.get("preserve_untouched") is True
    assert server._collect_params({"preserve_untouched": False})["preserve_untouched"] is False
    assert "preserve_untouched" not in server._collect_params({"engine": "lama"})


# ---------------------------------------------------------------------------
# 4. CLI：三态 + 逐层转发
# ---------------------------------------------------------------------------
def test_cli_flag_is_tristate():
    p = cli._build_arg_parser()
    assert p.parse_args(["-i", "x"]).preserve_untouched is None
    assert p.parse_args(["-i", "x", "--preserve-untouched"]).preserve_untouched is True
    assert p.parse_args(["-i", "x", "--no-preserve-untouched"]).preserve_untouched is False


def test_cli_forwards_to_engine(monkeypatch):
    import services.auto_pipeline as ap
    cap = {}

    def _fake(*a, **kw):
        cap.update(kw)
        return {"status": "skipped", "original_name": "x.png",
                "input_path": "x.png", "kind": "image", "error": "无"}

    monkeypatch.setattr(cli, "process_file", _fake)
    args = cli._build_arg_parser().parse_args(
        ["-i", "x.png", "-o", "out", "--preserve-untouched"])
    args.output_dir = "out"
    cli._run_one("x.png", args, show_progress=False)
    assert cap.get("preserve_untouched") is True


# ---------------------------------------------------------------------------
# 5. 端到端有效性：不是"被转发"，而是"真的只动水印区"
# ---------------------------------------------------------------------------
def test_array_facade_is_actually_effective(monkeypatch):
    """带外像素必须与输入**逐字节相同**，且关闭时**确实不同**（否则断言是空的）。"""
    from scripts.detector_diag import BASE_KINDS
    from scripts.make_demo_assets import CASES

    base = BASE_KINDS["gradient"](7, w=640, h=640)
    marked = CASES["corner_logo"][0](base)          # 角标在右下
    far = (slice(0, 200), slice(0, 200))           # 左上角，离角标足够远

    out_on, _ = api.remove_watermark_array(
        marked, engine="opencv", preset="ai_logo", dilate=0, denoise=0,
        real_superres=False, superres=False, preserve_untouched=True)
    out_off, _ = api.remove_watermark_array(
        marked, engine="opencv", preset="ai_logo", dilate=0, denoise=0,
        real_superres=False, superres=False, preserve_untouched=False)

    assert not np.array_equal(out_off[far], marked[far]), (
        "反例构造失败：关闭时带外本来就没变，开启后的『相同』不能说明任何问题")
    assert np.array_equal(out_on[far], marked[far]), "开启后带外仍被改动，门面链路没生效"
    assert not np.array_equal(out_on, out_off), "两个开关值给出完全相同的结果，参数可能被吞"
