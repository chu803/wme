# -*- coding: utf-8 -*-
"""阶段 353 · api 字节入口的**通道顺序**与 job 的**失败状态**必须是真的。

三条来自第三轮审计，其中两条我**重新实测过**、结论与审计的方向相反：

1. `_encode_bytes` 的 docstring 说入参是 RGB，而 `cv2.imencode` 按 BGR 解释数组
   ⇒ PNG/JPG/WEBP 的输出**红蓝互换**；TIFF/AVIF 走 `save_image`（内部 RGB2BGR）反而正确。
   审计的隔离实验喂的是 BGR 数组，所以它的结论方向是反的 —— **实测为准**。
2. api 的三个字节入口 `imdecode` 出 **BGR** 直接喂给只认 RGB 的核心
   ⇒ REST 入口的检测结果与文件/界面路径**不是同一件事**（与阶段 338 修的 PDF/视频同类）。
3. `job_manager` 的 worker 不看结果对象的 status ⇒ PDF/视频失败（服务**不抛异常**）时
   作业仍被标成 success ⇒ 客户端拿到 {"status":"success"}，随后取文件 404。
"""
from __future__ import annotations

import base64
import time

import cv2
import numpy as np
import pytest


def _red_rgb(size: int = 48) -> np.ndarray:
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[..., 0] = 200          # 纯红（RGB 语义）
    return img


def _png_bytes(rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    assert ok
    return buf.tobytes()


# ── ① 编码侧：红必须是红 ────────────────────────────────────────────────────
@pytest.mark.parametrize("fmt", ["PNG", "JPG"])
def test_encode_keeps_colors(fmt):
    import api

    data = api._encode_bytes(_red_rgb(), fmt, 95)
    back = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert back is not None
    b, g, r = (int(v) for v in back[0, 0])
    assert r > 120 and b < 60, f"{fmt} 红蓝互换：解码 BGR=[{b},{g},{r}]"


# ── ② 解码侧：REST 入口的检测结果必须与文件路径一致 ──────────────────────────
def test_detect_bytes_matches_file_path(tmp_path):
    """`api.detect_watermark_bytes` 与「读文件 → detect_watermark_mask」必须给出**同一个掩码**。

    这就是「同一张图，不同入口给出不同结果」的可判定形式（不是比颜色，是比结果）。
    """
    from api import detect_watermark_bytes
    from core.detector import detect_watermark_mask
    from core.image_utils import load_image

    rgb = _red_rgb(96)
    p = tmp_path / "red.png"
    p.write_bytes(_png_bytes(rgb))

    via_api = np.asarray(
        cv2.imdecode(np.frombuffer(detect_watermark_bytes(_png_bytes(rgb))[0], np.uint8),
                     cv2.IMREAD_GRAYSCALE)) > 0
    via_file = np.asarray(detect_watermark_mask(load_image(str(p))).mask) > 0

    m1 = np.asarray(via_api) > 0
    m2 = np.asarray(via_file) > 0
    assert m1.shape == m2.shape, (m1.shape, m2.shape)
    assert np.array_equal(m1, m2), (
        f"REST 入口与文件路径给出不同掩码：像素不同 {int((m1 ^ m2).sum())} 个"
        "（原因通常是 BGR/RGB 排布不一致）")


# ── ③ job：失败不许被标成 success ────────────────────────────────────────────
def test_pdf_job_with_garbage_is_failed_not_success(tmp_path):
    import job_manager

    jm = job_manager.JobManager(job_dir=str(tmp_path))
    jid = jm.submit("pdf", {"file": base64.b64encode(b"this is not a pdf").decode()})
    assert jid, "作业没提交成功"

    job = None
    for _ in range(200):                      # 最多等 10 秒
        job = jm.get(jid)
        if job is not None and job.status in ("success", "failed"):
            break
        time.sleep(0.05)
    assert job is not None
    assert job.status == "failed", f"坏输入的 PDF 作业被标成 {job.status!r}（应 failed）"
    assert not job.out_path, job.out_path
