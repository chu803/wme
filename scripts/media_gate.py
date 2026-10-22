#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台
 文件名称 : scripts/media_gate.py
 文件说明 : 阶段 315 · **PDF / 视频链路门禁**（补齐「全源」承诺的验证缺口）。

 为什么需要它（缺口是实测出来的，不是猜的）
 ----------------------------------------
 项目主打护城河之一是「图 / PDF / 视频全源」，但既有三个质量门禁
 （`quality_gate` 合成图 · `real_gate` 真实图 · `prompted_area_gate` 次级档面积）
 **全部只测静态图**；`benchmark_real/` 里没有任何 PDF/视频样本；
 `services/pdf_service.py` / `video_service.py` 的测试全是**服务层单测与 mock**。
 ⇒ 没有任何一条断言在说：「一份真 PDF 走完检测+修复后，水印区确实被改了、页数没丢」。
   这两条链路长期处于「有代码、有单测、无端到端验证」的状态。

 做法（不引入外部资产，样本现场合成、可复现）
 --------------------------------------------
 从 `benchmark_real/ground_truth.json` 取「有人工标注水印框且图片在位」的真实图，
 缩放收敛到 1024²（bound 运行时间），合成多页 PDF 与固定水印视频，
 跑完整链路后再比对。**比对基准是「同一份 PDF/视频的渲染回读」**，
 这样 JPEG 往返与 PDF 渲染噪声两侧同现，不会污染判据。

 红线（任一破 → 退出码 1）
   R1 页数保持        输出 PDF 页数 == 输入页数（防静默丢页）
   R2 水印区必须变化   标注 bbox（内缩 4px）内平均绝对差 ≥ MIN_BBOX_DIFF
                      （防「跑完什么也没改」的静默成功）
   R3 输出必须可解码   每一页 / 至少一帧能读回 numpy
   R4 视频帧数不塌     可解码帧数 ≥ 1 且 ≥ 输入帧数 × VIDEO_KEEP_RATIO

 诊断（打印，**不判红**，理由见下）
   D1 非水印区改动     bbox 外扩 48px 之外的平均绝对差与改动占比
   D2 全图改动占比
   D3 逐链路耗时

 诚实边界（务必读）
   为什么「非水印区必须不变」不设红线：默认链的后处理（降噪 + 锐化 + 边缘融合）是
   **全图作用**的——阶段 314b 实测一次修复改动约 79% 像素（关掉后处理为 5.05%）。
   那是既有产品决策（设置页有对应滑杆）。把它写成红线会让门禁从第一天就常红、
   进而被人关掉 —— 正是 LESSONS 里「守卫被绕过」的老路。故长期作为诊断曝光，
   等「后处理作用域收窄」真落地，再升级为红线。

 退出码三分（与门禁家族一致）：0 通过 / 1 未通过 / 2 跳过（依赖或资产缺失）。
==============================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

GT_PATH = os.path.join(_ROOT, "benchmark_real", "ground_truth.json")
IMAGES_DIR = os.path.join(_ROOT, "benchmark_real", "images")

SIDE = 1024            # 收敛尺寸（真实图为 2048²，全链路太慢）
BBOX_INSET = 4         # R2：bbox 内缩，避开羽化边缘
BBOX_DILATE = 48       # D1：外扩多少才算「用户没要求动」的区域
MIN_BBOX_DIFF = 2.0    # R2 阈值（0~255 平均绝对差）
VIDEO_KEEP_RATIO = 0.5  # R4


def _skip_reason() -> str | None:
    """依赖/资产不齐时返回跳过原因（None = 可跑）。"""
    try:
        import cv2  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"OpenCV 不可用：{exc}"
    try:
        import fitz  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"PyMuPDF 不可用，PDF 链路无法验证：{exc}"
    if not os.path.isfile(GT_PATH):
        return f"缺真值集：{GT_PATH}"
    if not os.path.isdir(IMAGES_DIR):
        return f"缺图集：{IMAGES_DIR}"
    return None


def _find_image(name: str) -> str | None:
    """image_id 可能不带扩展名；按 manifest 的常见形态逐个试。"""
    for cand in (name, name + ".jpg", name + ".jpeg", name + ".png"):
        p = os.path.join(IMAGES_DIR, cand)
        if os.path.isfile(p):
            return p
    return None


def load_cases(limit: int) -> list:
    """取「有水印 bbox 标注且图片在位」的用例：[{name, path, bbox(缩放后)}]。"""
    from PIL import Image

    gt = json.load(open(GT_PATH, encoding="utf-8"))
    out = []
    for e in gt.get("entries", []):
        wms = [w for w in (e.get("watermarks") or []) if isinstance(w.get("bbox"), list)
               and len(w["bbox"]) == 4]
        if not wms:
            continue
        path = _find_image(str(e.get("image_id") or ""))
        if not path:
            continue
        try:
            with Image.open(path) as im:
                w0, h0 = im.size
        except Exception:  # noqa: BLE001
            continue
        if w0 <= 0 or h0 <= 0:
            continue
        sx, sy = SIDE / float(w0), SIDE / float(h0)
        x, y, w, h = wms[0]["bbox"]
        bbox = (max(0, int(x * sx)), max(0, int(y * sy)),
                max(8, int(w * sx)), max(8, int(h * sy)))
        out.append({"path": path, "name": os.path.basename(path), "bbox": bbox})
        if len(out) >= max(1, limit):
            break
    return out


def _load_side(path: str) -> np.ndarray:
    """读图并收敛到 SIDE² 的 RGB。"""
    import cv2
    bgr = cv2.imread(path)
    if bgr is None:
        raise RuntimeError(f"无法读取图片：{path}")
    return cv2.cvtColor(cv2.resize(bgr, (SIDE, SIDE), interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2RGB)


def diff_stats(a_rgb: np.ndarray, b_rgb: np.ndarray, bbox: tuple) -> dict:
    """输入/输出同尺寸 RGB → 分区域度量（R2 与 D1/D2 的判据来源）。"""
    import cv2
    d = cv2.absdiff(a_rgb, b_rgb).max(axis=2).astype(np.float64)
    h, w = d.shape
    x, y, bw, bh = bbox
    ix, iy = min(w, x + BBOX_INSET), min(h, y + BBOX_INSET)
    jx, jy = max(0, min(w, x + bw - BBOX_INSET)), max(0, min(h, y + bh - BBOX_INSET))
    inner = d[iy:jy, ix:jx] if (jx > ix and jy > iy) else np.zeros((1, 1), np.float64)
    outside = d.copy()
    outside[max(0, y - BBOX_DILATE):min(h, y + bh + BBOX_DILATE),
            max(0, x - BBOX_DILATE):min(w, x + bw + BBOX_DILATE)] = 0.0
    return {
        "bbox_diff": float(inner.mean()) if inner.size else 0.0,
        "outside_diff": float(outside.mean()) if outside.size else 0.0,
        "outside_changed": float((outside > 0).mean()) if outside.size else 0.0,
        "whole_changed": float((d > 0).mean()),
    }


def build_pdf(frames: list, dst: str) -> None:
    """把 RGB 帧列表合成为每页一张图的 PDF（页尺寸 == SIDE² 点）。"""
    import cv2
    import fitz
    doc = fitz.open()
    try:
        for rgb in frames:
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                                    [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                raise RuntimeError("JPEG 编码失败")
            page = doc.new_page(width=SIDE, height=SIDE)
            page.insert_image(fitz.Rect(0, 0, SIDE, SIDE), stream=buf.tobytes())
        doc.save(dst)
    finally:
        doc.close()


def render_pdf(path: str) -> list:
    """PDF 每页渲染回 RGB（与 build_pdf 的页尺寸一致 ⇒ 与输入逐像素可比）。"""
    import fitz
    doc = fitz.open(path)
    imgs = []
    try:
        for page in doc:
            pm = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
            imgs.append(np.frombuffer(pm.samples, dtype=np.uint8)
                        .reshape(pm.height, pm.width, 3).copy())
    finally:
        doc.close()
    return imgs


def build_video(rgb: np.ndarray, dst: str, frames: int) -> None:
    import cv2
    vw = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"mp4v"), 8, (SIDE, SIDE))
    if not vw.isOpened():
        raise RuntimeError("OpenCV 无法创建视频写出器（缺编码器）")
    try:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        for _ in range(frames):
            vw.write(bgr)
    finally:
        vw.release()


def read_video(path: str, cap: int = 60) -> list:
    import cv2
    store = cv2.VideoCapture(path)
    imgs = []
    try:
        while len(imgs) < cap:
            ok, fr = store.read()
            if not ok:
                break
            imgs.append(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
    finally:
        store.release()
    return imgs


def gate_pdf(cases: list, work: str, preset: str) -> tuple:
    """PDF 链路：返回 (红线问题, 诊断行, 耗时, 度量)。"""
    import api

    src = os.path.join(work, "media_gate_in.pdf")
    out = os.path.join(work, "media_gate_out.pdf")
    build_pdf([_load_side(c["path"]) for c in cases], src)
    t0 = time.time()
    res = api.remove_watermark_pdf(src, dst=out, engine="opencv", preset=preset)
    sec = round(time.time() - t0, 1)
    problems: list = []
    diag: list = []
    metrics: list = []
    if getattr(res, "status", "") != "success" or not getattr(res, "out_path", None):
        problems.append(f"R3 PDF 链路整体失败：{getattr(res, 'error', '无错误信息')}")
        return problems, diag, sec, metrics

    in_pages = render_pdf(src)          # 基准：同一份 PDF 的渲染回读
    out_pages = render_pdf(res.out_path)
    if len(out_pages) != len(in_pages):
        problems.append(f"R1 页数丢失：输入 {len(in_pages)} 页 → 输出 {len(out_pages)} 页")
    for i, c in enumerate(cases):
        if i >= min(len(in_pages), len(out_pages)):
            problems.append(f"R3 第 {i + 1} 页无法读回（输出页数不足）")
            continue
        s = diff_stats(in_pages[i], out_pages[i], c["bbox"])
        s["page"] = i + 1
        metrics.append(s)
        if s["bbox_diff"] < MIN_BBOX_DIFF:
            problems.append(f"R2 第 {i + 1} 页水印区几乎没变（bbox_diff={s['bbox_diff']:.2f} "
                            f"< {MIN_BBOX_DIFF}）⇒ 该页可能没检出水印")
        diag.append(f"  第{i + 1}页 {c['name'][:26]:<26} 水印区差 {s['bbox_diff']:.1f} | "
                    f"非水印区差 {s['outside_diff']:.2f} 改动 {s['outside_changed'] * 100:.1f}% | "
                    f"全图改动 {s['whole_changed'] * 100:.1f}%")
    return problems, diag, sec, metrics


def gate_video(case: dict, work: str, preset: str, frames: int) -> tuple:
    """视频链路：返回 (红线问题, 诊断行, 耗时, 度量)。"""
    import api

    src = os.path.join(work, "media_gate_in.mp4")
    out = os.path.join(work, "media_gate_out.mp4")
    rgb = _load_side(case["path"])
    build_video(rgb, src, frames)
    t0 = time.time()
    res = api.remove_watermark_video(src, dst=out, engine="opencv", preset=preset,
                                     max_frames=frames)
    sec = round(time.time() - t0, 1)
    problems: list = []
    diag: list = []
    metrics: list = []
    out_path = getattr(res, "out_path", None) or getattr(res, "output_path", None)
    if getattr(res, "status", "") != "success" or not out_path:
        problems.append(f"R3 视频链路整体失败：{getattr(res, 'error', '无错误信息')}")
        return problems, diag, sec, metrics

    in_frames = read_video(src)
    out_frames = read_video(out_path)
    if not out_frames:
        problems.append("R4 输出视频读不回任何帧")
        return problems, diag, sec, metrics
    if len(out_frames) < max(1, int(len(in_frames) * VIDEO_KEEP_RATIO)):
        problems.append(f"R4 帧数塌陷：输入 {len(in_frames)} 帧 → 输出 {len(out_frames)} 帧")
    k = min(len(in_frames), len(out_frames))
    worst = None
    for j in range(k):
        s = diff_stats(in_frames[j], out_frames[j], case["bbox"])
        s["frame"] = j
        metrics.append(s)
        if worst is None or s["bbox_diff"] < worst["bbox_diff"]:
            worst = s
    if worst is not None and worst["bbox_diff"] < MIN_BBOX_DIFF:
        problems.append(f"R2 水印区几乎没变（最差帧 {worst['frame']}，bbox_diff="
                        f"{worst['bbox_diff']:.2f} < {MIN_BBOX_DIFF}）")
    if worst is not None:
        diag.append(f"  视频 {case['name'][:24]:<24} 帧 {len(in_frames)}→{len(out_frames)} | "
                    f"水印区最差差 {worst['bbox_diff']:.1f} | 非水印区改动 "
                    f"{worst['outside_changed'] * 100:.1f}% | 全图改动 {worst['whole_changed'] * 100:.1f}%")
    return problems, diag, sec, metrics


def main(argv=None) -> int:
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="PDF / 视频链路门禁（阶段 315）")
    ap.add_argument("--limit", type=int, default=3, help="PDF 页数（取自真实图标注集）")
    ap.add_argument("--frames", type=int, default=12, help="视频输入帧数")
    ap.add_argument("--preset", default="ai_logo", help="检测预设（默认 ai_logo，与标注样式一致）")
    ap.add_argument("--json", default=None, help="把度量写到该路径")
    args = ap.parse_args(argv)

    reason = _skip_reason()
    if reason:
        print(f"⏭ 跳过（依赖/资产缺失）：{reason}")
        return 2

    cases = load_cases(args.limit)
    if not cases:
        print("⏭ 跳过：真值集中没有「有水印标注且图片在位」的可用用例")
        return 2

    work = os.path.join(_ROOT, "temp", "media_gate")
    os.makedirs(work, exist_ok=True)

    print("PDF / 视频链路门禁（真实图标注 → 现场合成 → 全链路 → 判据）")
    print("-" * 78)
    print(f"样本：{len(cases)} 页 PDF（{SIDE}²）+ {args.frames} 帧视频 | 预设={args.preset} | 引擎=opencv")

    all_problems: list = []
    all_diag: list = []
    report = {"side": SIDE, "preset": args.preset, "engine": "opencv",
              "cases": [c["name"] for c in cases]}

    try:
        p, d, sec, m = gate_pdf(cases, work, args.preset)
        all_problems += [f"[PDF] {x}" for x in p]
        all_diag += d
        report["pdf"] = {"seconds": sec, "metrics": m}
        print(f"  PDF 链路耗时 {sec}s")
    except Exception as exc:  # noqa: BLE001 —— 门禁自身异常必须红，不能静默 0
        all_problems.append(f"[PDF] 执行异常：{type(exc).__name__}: {exc}")

    try:
        p, d, sec, m = gate_video(cases[0], work, args.preset, args.frames)
        all_problems += [f"[视频] {x}" for x in p]
        all_diag += d
        report["video"] = {"seconds": sec, "input_frames": args.frames, "metrics": m}
        print(f"  视频链路耗时 {sec}s")
    except Exception as exc:  # noqa: BLE001
        all_problems.append(f"[视频] 执行异常：{type(exc).__name__}: {exc}")

    for line in all_diag:
        print(line)
    print("-" * 78)
    if all_problems:
        for line in all_problems:
            print(f"  ❌ {line}")
        rc = 1
        print("❌ 未通过")
    else:
        rc = 0
        print("✅ 通过（页数保持 · 水印区确有变化 · 输出可解码 · 帧数不塌）")

    report["problems"] = all_problems
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
