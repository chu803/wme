# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : services/video_export.py
 文件说明 : 视频处理结果导出（阶段 37 新增）。

            把「视频去水印」的结果转成可下载的静态产物：
              · export_frame_compare —— 导出某一帧的「原图 | 结果」拼接对比图（PNG）；
              · export_contact_sheet —— 导出整段结果的「帧拼图 / 联系表」（PNG），
                便于不播放视频也能一眼浏览修复效果。

            本模块属于 services 层，仅调用 core / utils / config 与同层
            video_service / export_service，**不依赖 gradio**。
 版 本号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import cv2
import numpy as np

import config
from core.image_utils import save_image
from services.export_service import make_compare_array, make_contact_sheet
from services.video_service import extract_sampled_frames
from utils.logger import get_logger
from utils.path_helper import make_unique_path

log = get_logger("services.video_export")


def _resolve_out_dir(out_dir: "str | None") -> str:
    """落盘目录：指定用指定，否则用 config.OUTPUT_DIR。"""
    d = str(out_dir) if out_dir else str(config.OUTPUT_DIR)
    try:
        _os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _extract_one(path: str, idx: int) -> "np.ndarray | None":
    """从视频抽第 idx 帧（RGB），失败返回 None。"""
    cap = None
    try:
        if not _os.path.isfile(path):
            return None
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total > 0:
            idx = max(0, min(int(idx), total - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    except Exception as exc:  # noqa: BLE001
        log.warning("视频抽帧失败：%s", exc)
        return None
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:  # noqa: BLE001
            pass


def export_frame_compare(
    src_video: str,
    out_video: str,
    idx: int,
    out_dir: "str | None" = None,
) -> "str | None":
    """
    导出某一帧的「原图 | 结果」拼接对比图（PNG）。

    参数说明：
        src_video : 源视频路径。
        out_video : 结果视频路径。
        idx       : 帧序号（0 = 首帧）。
        out_dir   : 落盘目录；None 用 config.OUTPUT_DIR。

    返回值：
        保存的 PNG 路径；源/结果缺失或抽帧失败返回 None。
    """
    if not out_video or not _os.path.isfile(out_video) or not src_video or not _os.path.isfile(src_video):
        return None
    fidx = max(0, int(idx or 0))
    orig = _extract_one(src_video, fidx)
    proc = _extract_one(out_video, fidx)
    if orig is None or proc is None:
        return None
    # 结果可能已被真实超分放大；为对比对齐，缩回源尺寸（聚焦去水印效果，非放大）。
    if proc.shape[:2] != orig.shape[:2]:
        proc = cv2.resize(proc, (orig.shape[1], orig.shape[0]), interpolation=cv2.INTER_AREA)
    arr = make_compare_array(orig, proc)
    d = _resolve_out_dir(out_dir)
    fname = make_unique_path(d, f"视频对比帧_{fidx:06d}.png")
    try:
        saved = save_image(str(fname), arr, fmt="PNG")
        log.info("已导出视频对比帧：%s", saved)
        return str(saved)
    except Exception as exc:  # noqa: BLE001
        log.warning("导出视频对比帧失败：%s", exc)
        return None


def export_contact_sheet(
    video_path: str,
    n: "int | None" = None,
    cols: int = 4,
    out_dir: "str | None" = None,
) -> "str | None":
    """
    导出整段结果的「帧拼图 / 联系表」（PNG）。

    参数说明：
        video_path : 视频路径（通常是结果视频）。
        n          : 抽样帧数；None 取 config.VIDEO_COMPARE_FRAMES。
        cols       : 每行几格。
        out_dir    : 落盘目录；None 用 config.OUTPUT_DIR。

    返回值：
        保存的 PNG 路径；视频缺失或抽不到帧返回 None。
    """
    if not video_path or not _os.path.isfile(video_path):
        return None
    frames = extract_sampled_frames(video_path, n=n, return_indices=True)
    if not frames:
        return None
    images = [f["image"] for f in frames]
    sheet = make_contact_sheet(images, cols=cols)
    d = _resolve_out_dir(out_dir)
    fname = make_unique_path(d, "视频帧拼图.png")
    try:
        saved = save_image(str(fname), sheet, fmt="PNG")
        log.info("已导出视频帧拼图：%s", saved)
        return str(saved)
    except Exception as exc:  # noqa: BLE001
        log.warning("导出视频帧拼图失败：%s", exc)
        return None


if __name__ == "__main__":
    import shutil
    import tempfile

    from services.video_service import process_video

    tmp = tempfile.mkdtemp(prefix="video_export_selftest_")
    # 阶段 136：自检产物（2 视频 + 2 PNG）全部落在临时目录，结束必须清理——
    # 此前 mkdtemp 从不删除，每跑一次自检就泄漏一个目录。
    try:
        test_mp4 = _os.path.join(tmp, "测试.mp4")
        W, H, FPS, N = 160, 120, 10.0, 12
        writer, _ = None, None
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(test_mp4, fourcc, FPS, (W, H))
        for i in range(N):
            canvas = np.full((H, W, 3), 40 + i * 8, dtype=np.uint8)
            cv2.putText(canvas, "MARK", (100, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            writer.write(canvas)
        writer.release()

        out = _os.path.join(tmp, "已去水印.mp4")
        res = process_video(test_mp4, out, frame_step=1)
        assert res.status == "success", res.error
        f = export_frame_compare(test_mp4, res.out_path, 2, out_dir=tmp)
        s = export_contact_sheet(res.out_path, n=4, cols=2, out_dir=tmp)
        assert f and _os.path.isfile(f), "对比帧导出失败"
        assert s and _os.path.isfile(s), "帧拼图导出失败"
        print("video_export.py 自检通过：", f, s)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
