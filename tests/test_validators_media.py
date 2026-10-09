# -*- coding: utf-8 -*-
"""
阶段 44 测试：媒体输入预校验深化（PDF / 视频校验层）。

覆盖（全部零 torch，不依赖真实超分权重）：
  · path_helper 媒体格式白名单（is_supported_pdf_input / is_supported_video_input）
  · exceptions MediaError 子树继承关系与默认文案
  · validators.validate_pdf_file：合法 PDF / 不支持格式 / 缺失文件 / 损坏文件 / 体积超限
  · validators.validate_video_file：合法视频 / 不支持格式 / 缺失文件 / 损坏文件 / 体积超限
  · i18n 关键键 media.pdf.invalid / media.video.invalid 双语存在
  · tab_media 预校验闸门接线：run_pdf / run_video 在非法输入时返回 invalid 上屏文案
全部使用临时目录，不污染项目缓存。
"""

import os as _os
import sys as _sys

import numpy as np
import pytest

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config  # noqa: E402
from services.validators import (  # noqa: E402
    PdfMeta,
    VideoMeta,
    validate_pdf_file,
    validate_video_file,
)
from utils.exceptions import (  # noqa: E402
    WatermarkEraserError,
    MediaError,
    UnsupportedMediaError,
    CorruptedMediaError,
    MediaTooLargeError,
)
from utils.path_helper import (  # noqa: E402
    is_supported_pdf_input,
    is_supported_video_input,
)
from utils.i18n import t, set_language  # noqa: E402

@pytest.fixture(autouse=True)
def _lang_zh():
    """阶段 249：语言设置从**模块级**移入夹具。

    模块级 set_language() 发生在**收集期**（先于一切夹具），conftest 的
    `_isolate_i18n_language` 只能保存已污染的值再还原，挡不住它；移入夹具后
    收集期零副作用，每个用例仍拿到 zh-CN（本夹具 setup 时设置、conftest 的
    teardown 还原）。
    """
    set_language("zh-CN")


# ------------------------------------------------------------------------------
# path_helper 媒体格式白名单
# ------------------------------------------------------------------------------
def test_is_supported_pdf_input_whitelist():
    assert is_supported_pdf_input("报告.PDF") is True
    assert is_supported_pdf_input("报告.pdf") is True
    assert is_supported_pdf_input("报告.doc") is False
    assert is_supported_pdf_input("报告.txt") is False


def test_is_supported_video_input_whitelist():
    assert is_supported_video_input("clip.MP4") is True
    assert is_supported_video_input("clip.avi") is True
    assert is_supported_video_input("clip.mkv") is True
    assert is_supported_video_input("clip.mp3") is False
    assert is_supported_video_input("clip.exe") is False


# ------------------------------------------------------------------------------
# exceptions MediaError 子树
# ------------------------------------------------------------------------------
def test_media_error_hierarchy():
    for cls in (UnsupportedMediaError, CorruptedMediaError, MediaTooLargeError):
        assert issubclass(cls, MediaError)
        assert issubclass(cls, WatermarkEraserError)
        assert getattr(cls, "default_title", "")


def test_media_error_fields():
    err = CorruptedMediaError("坏文件", title="自定义标题")
    assert err.title == "自定义标题"
    assert "坏文件" in err.reason
    # 渲染为 Markdown 不应抛错，且包含标题与建议。
    md = err.to_markdown()
    assert "自定义标题" in md
    assert "建议" in md


# ------------------------------------------------------------------------------
# validate_pdf_file
# ------------------------------------------------------------------------------
@pytest.mark.skipif(not __import__("services.pdf_service", fromlist=["is_pdf_available"])
                    .is_pdf_available(), reason="PyMuPDF 未安装")
def test_validate_pdf_file_happy(tmp_path):
    fitz = __import__("fitz")
    src = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=200, height=300)
    doc.save(src)
    doc.close()

    meta = validate_pdf_file(src)
    assert isinstance(meta, PdfMeta)
    assert meta.pages == 3
    assert meta.size_mb >= 0
    assert meta.path == src


def test_validate_pdf_file_unsupported_format(tmp_path):
    pp = tmp_path / "note.doc"
    pp.write_text("hello")
    with pytest.raises(UnsupportedMediaError):
        validate_pdf_file(str(pp))


def test_validate_pdf_file_missing():
    with pytest.raises(FileNotFoundError):
        validate_pdf_file("__not_exist__.pdf")


@pytest.mark.skipif(not __import__("services.pdf_service", fromlist=["is_pdf_available"])
                    .is_pdf_available(), reason="PyMuPDF 未安装")
def test_validate_pdf_file_corrupted(tmp_path):
    # 阶段 206 修：`validate_pdf_file` 在缺 fitz 时抛的是 ImportError 而非
    # CorruptedMediaError ⇒ 本用例需要 PyMuPDF 在位才有意义（原缺 skipif，
    # CI 的 min 环境上直接失败）。
    pp = tmp_path / "broken.pdf"
    pp.write_bytes(b"%PDF-1.4 this is not a real pdf")
    with pytest.raises(CorruptedMediaError):
        validate_pdf_file(str(pp))


@pytest.mark.skipif(not __import__("services.pdf_service", fromlist=["is_pdf_available"])
                    .is_pdf_available(), reason="PyMuPDF 未安装")
def test_validate_pdf_file_too_large(tmp_path, monkeypatch):
    """把体积上限压到 -1，任何（体积>=0）的 PDF 都应触发 MediaTooLargeError。"""
    monkeypatch.setattr(config, "MAX_PDF_FILE_MB", -1)
    # 用一个真实小 PDF，确保先过了「能打开」这一关。
    fitz = __import__("fitz")
    src = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    doc.new_page(width=100, height=100)
    doc.save(src)
    doc.close()
    with pytest.raises(MediaTooLargeError):
        validate_pdf_file(src)


# ------------------------------------------------------------------------------
# validate_video_file
# ------------------------------------------------------------------------------
def _make_small_video(path: str, frames: int = 10, fps: float = 25.0,
                      w: int = 64, h: int = 48) -> None:
    cv2 = __import__("cv2")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, fps, (w, h))
    for i in range(frames):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:, :, 0] = (i * 20) % 255
        writer.write(frame)
    writer.release()


def test_validate_video_file_happy(tmp_path):
    src = str(tmp_path / "clip.mp4")
    _make_small_video(src)
    meta = validate_video_file(src)
    assert isinstance(meta, VideoMeta)
    assert meta.frame_count >= 1
    assert meta.width == 64 and meta.height == 48
    assert meta.fps >= 0
    assert meta.size_mb >= 0


def test_validate_video_file_unsupported_format(tmp_path):
    pp = tmp_path / "clip.mp3"
    pp.write_bytes(b"\x00\x01\x02")
    with pytest.raises(UnsupportedMediaError):
        validate_video_file(str(pp))


def test_validate_video_file_missing():
    with pytest.raises(FileNotFoundError):
        validate_video_file("__not_exist__.mp4")


def test_validate_video_file_corrupted(tmp_path):
    pp = tmp_path / "broken.mp4"
    pp.write_bytes(b"not a video file at all")
    with pytest.raises(CorruptedMediaError):
        validate_video_file(str(pp))


def test_validate_video_file_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_VIDEO_FILE_MB", -1)
    src = str(tmp_path / "clip.mp4")
    _make_small_video(src)
    with pytest.raises(MediaTooLargeError):
        validate_video_file(src)


# ------------------------------------------------------------------------------
# i18n 关键键
# ------------------------------------------------------------------------------
def test_i18n_media_invalid_keys():
    for lang in ("zh-CN", "en-US"):
        set_language(lang)
        assert t("media.pdf.invalid", exc="x") != "media.pdf.invalid"
        assert t("media.video.invalid", exc="x") != "media.video.invalid"


# ------------------------------------------------------------------------------
# tab_media 预校验闸门接线
# ------------------------------------------------------------------------------
@pytest.mark.skipif(not __import__("services.pdf_service", fromlist=["is_pdf_available"])
                    .is_pdf_available(), reason="PyMuPDF 未安装")
def test_tab_media_run_pdf_gate(monkeypatch, tmp_path):
    import ui.tab_media as tm

    # 让预校验闸门抛 MediaError（模拟「非法 PDF」），且不进入真实处理。
    def _boom(path, **kw):
        raise UnsupportedMediaError("模拟不支持的文档格式：.doc")
    monkeypatch.setattr(tm, "validate_pdf_file", _boom)

    fitz = __import__("fitz")
    src = str(tmp_path / "doc.pdf")
    doc = fitz.open()
    doc.new_page(width=100, height=100)
    doc.save(src)
    doc.close()

    msg, upd, stats = tm.run_pdf(
        src, "", 150, "lama", 1.0, 0, 0, 0, 0, 0.5,
        auto_preset=True, preset=config.DETECT_PRESET_DEFAULT, progress=None,
    )
    # 闸门应命中并返回 invalid 上屏文案（异常标题固定在中文 MediaError 内，
    # 不随 i18n 运行时语言变化，可跨语言断言）。
    assert "不支持的媒体格式" in msg
    # 返回形状应是三元组且结果组件不可见。
    assert isinstance(upd, dict) and upd.get("visible") is False
    assert stats is not None


def test_tab_media_run_video_gate(monkeypatch, tmp_path):
    import ui.tab_media as tm

    def _boom(path, **kw):
        raise UnsupportedMediaError("模拟不支持的视频格式：.mp3")
    monkeypatch.setattr(tm, "validate_video_file", _boom)

    src = str(tmp_path / "clip.mp4")
    _make_small_video(src)

    msg, upd, cmd, stats, gallery, _ = tm.run_video(
        src, 1, 300, True, "lama", 1.0, 0, 0, 0, 0, 0.5,
        real_superres=False, real_scale=config.REAL_SUPERRES_SCALE,
        real_backend=config.REAL_SUPERRES_BACKEND,
        auto_preset=True, preset=config.DETECT_PRESET_DEFAULT, progress=None,
    )
    assert "不支持的媒体格式" in msg
    assert isinstance(upd, dict) and upd.get("visible") is False
    assert gallery == []
