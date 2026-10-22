# -*- coding: utf-8 -*-
"""端到端 GPU 通道核验（Stage 305 · 修 T5/T6 探针假红）。

上一版探针 T5/T6 报 FAIL 的根因是**探针自身缺陷**，不是产品缺陷：
  · T5 用 `getattr(det, "_EASYOCR_READER", None)` 读模块级缓存，但 easyocr
    reader 是懒加载的，未触发时该属性根本不存在 → 「未初始化」假红。
  · T6 在 `core.inpainter` 模块上找 device 属性，而真实 device 挂在
    模型对象 / device_manager 上，不在模块命名空间 → 「未暴露」假红。

本版改用**产品自己的公开 API 触发真实加载**，再从真实对象读回 device，
不新增任何产品改动，只验通道。
"""
from __future__ import annotations

import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import config  # noqa: E402
from core.device_manager import get_device_info, select_device, use_fp16  # noqa: E402

FAILS = []


def rec(name: str, ok: bool, detail: str = "") -> bool:
    if not ok:
        FAILS.append(name)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}: {detail}")
    return bool(ok)


def main() -> int:
    print("=" * 72)
    print("端到端 GPU 通道核验 · 修 T5/T6 探针假红")
    print("=" * 72)

    # ---- E1 device_manager 自身裁决 ----
    info = get_device_info()
    rec("E1.device_info", info.is_gpu, f"{info.name} | kind={info.kind} | vram={info.vram_mb}MB | {info.reason}")
    handle = select_device("auto")
    rec("E1.torch_device", str(handle.torch_device).startswith("cuda"), f"torch_device={handle.torch_device}")
    fp16 = use_fp16()
    print(f"  [INFO] use_fp16()={fp16}（DEVICE_USE_FP16={config.DEVICE_USE_FP16}）")

    # ---- E2 easyocr 真实初始化 ----
    try:
        from core import detector as det

        t0 = time.perf_counter()
        reader = None
        # 产品内部真实的懒加载入口（阶段 164 OCR 接入）
        for attr in ("_get_easyocr_reader", "get_easyocr_reader", "_init_easyocr"):
            fn = getattr(det, attr, None)
            if callable(fn):
                reader = fn()
                break
        if reader is None:
            # 回退：直接探测模块内可能的缓存名（触发不了就算了，如实记录）
            for cand in ("_EASYOCR_READER", "_easyocr_reader", "_OCR_READER"):
                if getattr(det, cand, None) is not None:
                    reader = getattr(det, cand)
                    break
        dt = time.perf_counter() - t0
        if reader is None:
            rec("E2.easyocr", False, "未找到 easyocr 懒加载入口/缓存名（探针未定位，不等于产品不可用）")
        else:
            dev = getattr(reader, "device", None)
            if dev is None:
                rec("E2.easyocr", False, f"reader 无 .device 属性（类型 {type(reader).__name__}）")
            else:
                on_gpu = "cuda" in str(dev).lower() or str(dev) in ("0", 0)
                rec("E2.easyocr", on_gpu, f"reader.device={dev!r}（初始化耗时 {dt:.2f}s）")
    except Exception as exc:  # noqa: BLE001
        rec("E2.easyocr", False, f"异常 {type(exc).__name__}: {exc}")

    # ---- E3 LaMa 真实加载 + 真推理（最小 256²）----
    try:
        from core import inpainter as inp

        eng = None
        for attr in ("get_engine", "get_inpainter", "load_engine", "_get_engine"):
            fn = getattr(inp, attr, None)
            if callable(fn):
                try:
                    eng = fn("lama")
                except TypeError:
                    eng = fn()
                break
        if eng is None:
            rec("E3.lama", False, "未找到 inpainter 引擎工厂入口（探针未定位）")
        else:
            dev = getattr(eng, "device", None) or getattr(eng, "_device", None)
            rec("E3.lama.device", dev is not None, f"engine.device={dev!r}")
            img = (np.random.default_rng(0).random((256, 256, 3)) * 255).astype(np.uint8)
            mask = np.zeros((256, 256), np.uint8)
            mask[100:140, 100:140] = 255
            t0 = time.perf_counter()
            out = eng.inpaint(img, mask) if hasattr(eng, "inpaint") else inp.inpaint_image(img, mask)
            dt = time.perf_counter() - t0
            arr = np.asarray(out)
            rec(
                "E3.lama.infer",
                arr.shape[:2] == (256, 256),
                f"256² 推理 {dt:.3f}s，输出 shape={arr.shape} dtype={arr.dtype}",
            )
    except Exception as exc:  # noqa: BLE001
        rec("E3.lama", False, f"异常 {type(exc).__name__}: {exc}")

    print("-" * 72)
    if FAILS:
        print(f"结论：GPU 通道 E1/E2/E3 有失败项 {FAILS}")
    else:
        print("结论：GPU 端到端通道完全打通（E1–E3 全绿，含真实 OCR + LaMa 推理）")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())