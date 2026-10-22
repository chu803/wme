# -*- coding: utf-8 -*-
"""阶段 314b · 异常输入的用户可读性守卫。

实测问题（异常输入审计）：
- **0 字节图片**：`cv2.imdecode` 对空数组是**抛 cv2.error**（不是返回 None），
  旧实现把 OpenCV 的 C++ 原始报错直接透给用户：
  `error: OpenCV(5.0.0) ... loadsave.cpp:1291`，与「其余损坏图都是友好中文提示」不一致。
- **损坏 PDF**：PyMuPDF 的 `FileDataError: Failed to open file ... as type pdf.`
  原样透出（英文、无处置建议）。

本守卫钉住「可读报错」：必须是项目自己的异常类型 / 含中文处置提示。
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.image_utils import load_image_with_alpha          # noqa: E402
from utils.exceptions import CorruptedImageError            # noqa: E402


def test_empty_image_raises_readable_error(tmp_path):
    p = tmp_path / "empty.png"
    p.write_bytes(b"")
    with pytest.raises(CorruptedImageError) as ei:
        load_image_with_alpha(str(p))
    msg = str(ei.value)
    assert "为空" in msg and "0 字节" in msg, f"空文件提示不可读：{msg}"
    assert "OpenCV(" not in msg, "不得把 OpenCV 原始异常透给用户"


def test_truncated_header_raises_readable_error(tmp_path):
    """头部损坏到解码器进不去（imdecode 抛错而非返回 None）也要可读。"""
    p = tmp_path / "bad.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    with pytest.raises(CorruptedImageError) as ei:
        load_image_with_alpha(str(p))
    assert "解码失败" in str(ei.value) or "损坏" in str(ei.value)


def test_normal_image_still_loads(tmp_path):
    """反例：正常图不得被新分支误伤。"""
    p = tmp_path / "ok.png"
    cv2.imwrite(str(p), np.full((64, 64, 3), 200, np.uint8))
    rgb, alpha = load_image_with_alpha(str(p))
    assert rgb.shape == (64, 64, 3) and alpha is None


def test_broken_pdf_reports_readable_error(tmp_path):
    from api import remove_watermark_pdf

    p = tmp_path / "broken.pdf"
    p.write_bytes(b"%PDF-1.4 garbage")
    res = remove_watermark_pdf(str(p), dst=str(tmp_path / "out.pdf"), engine="opencv")
    assert res.status == "failed", f"损坏 PDF 应判 failed，实得 {res.status}"
    assert "无法打开 PDF" in (res.error or ""), f"提示不可读：{res.error}"
    assert "FileDataError" not in (res.error or "").split("原因：")[0], "不得只透出英文异常名"
