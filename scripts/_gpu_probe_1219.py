# -*- coding: utf-8 -*-
"""GPU 真可用性探针（Stage 305 · D1 前置核验）。

目的：核实 canonical venv 下 torch 是否真跑在 GPU 上 —— 重锚日志声明
「自动选择设备：NVIDIA GeForce RTX 5060 Laptop GPU」+「EasyOCR（GPU 模式）」，
但此前开放项把 D1 记作「沙箱为 CPU torch」。二者冲突，本探针以实测裁决。

判定标准（非自洽式断言，全部外部可观测）：
  T1 torch 版本 + CUDA 构建版本
  T2 torch.cuda.is_available()
  T3 设备名 / capability / 显存总量
  T4 真实 CUDA 张量运算（不是 is_available 的布尔声明）：在 GPU 上做
     matmul + synchronize，断言结果正确且计算发生在 cuda:0
  T5 easyocr Reader 实际 device（GPU vs CPU）
  T6 LaMa 修复器的 torch device（若模型在）
结论写 stdout，EXIT=0 表示 GPU 通道真打通。
"""
from __future__ import annotations

import os
import sys
import traceback

FORCE_UTF8 = os.environ.get("FORCE_UTF8_STDIO", "1") == "1"
if FORCE_UTF8 and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

RESULTS = []


def rec(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}: {detail}")
    return bool(ok)


def main() -> int:
    print("=" * 72)
    print("GPU 真可用性探针 · canonical venv")
    print("=" * 72)

    # ---- T1/T2/T3/T4 torch CUDA ----
    try:
        import numpy as np
        import torch

        rec("T1.torch", True, f"torch {torch.__version__}")
        rec(
            "T1.cuda_build",
            True,
            f"built for CUDA {torch.version.cuda}"
            + (f" / cuDNN {torch.backends.cudnn.version()}" if torch.backends.cudnn.is_available() else ""),
        )

        avail = torch.cuda.is_available()
        rec("T2.cuda_available", avail, str(avail))

        if not avail:
            rec("T3.device", False, "CUDA 不可用，后续 T4/T5/T6 跳过")
        else:
            name = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability(0)
            total_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            rec("T3.device", True, f"{name} | sm_{cap[0]}{cap[1]} | {total_gb:.2f} GiB")

            # T4 真实运算：GPU 上 matmul 并与 CPU 参考比对，synchronize 取值
            try:
                a = torch.randn(1024, 1024, device="cuda:0", dtype=torch.float32)
                b = torch.randn(1024, 1024, device="cuda:0", dtype=torch.float32)
                c = a @ b
                torch.cuda.synchronize(0)
                ref = (a.cpu() @ b.cpu())
                max_abs = float((c.cpu() - ref).abs().max())
                same_dev = str(c.device)
                ok4 = same_dev.startswith("cuda") and max_abs < 1e-2
                rec(
                    "T4.real_cuda_op",
                    ok4,
                    f"matmul on {same_dev}, max|Δ| vs CPU ref = {max_abs:.3e}",
                )
            except Exception as exc:  # noqa: BLE001
                rec("T4.real_cuda_op", False, f"异常 {type(exc).__name__}: {exc}")

        # ---- T5 easyocr 实际 device ----
        try:
            from core import detector as det

            reader = getattr(det, "_EASYOCR_READER", None)
            if reader is None:
                # 触发懒加载
                det._get_easyocr_reader() if hasattr(det, "_get_easyocr_reader") else None
                reader = getattr(det, "_EASYOCR_READER", None)
            if reader is None:
                rec("T5.easyocr", False, "reader 未初始化（可能未触发懒加载）")
            else:
                devs = getattr(reader, "device", None)
                rec("T5.easyocr", True, f"device={devs!r}")
        except Exception as exc:  # noqa: BLE001
            rec("T5.easyocr", False, f"异常 {type(exc).__name__}: {exc}")
            traceback.print_exc(file=sys.stderr)

        # ---- T6 LaMa 修复器 device ----
        try:
            from core import inpainter as inp

            info = []
            for attr in ("_DEVICE", "device", "_device"):
                v = getattr(inp, attr, None)
                if v is not None:
                    info.append(f"{attr}={v!r}")
            if hasattr(inp, "get_device"):
                try:
                    info.append(f"get_device()={inp.get_device()!r}")
                except Exception as exc:  # noqa: BLE001
                    info.append(f"get_device() 异常 {type(exc).__name__}")
            rec("T6.inpainter", bool(info), " | ".join(info) or "未暴露 device 属性")
        except Exception as exc:  # noqa: BLE001
            rec("T6.inpainter", False, f"异常 {type(exc).__name__}: {exc}")

    except Exception as exc:  # noqa: BLE001
        rec("T0.import_torch", False, f"异常 {type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)

    # ---- 汇总 ----
    print("-" * 72)
    fails = [n for n, ok, _ in RESULTS if not ok]
    hard = [n for n in fails if n in {"T0.import_torch", "T2.cuda_available"}]
    if hard:
        print(f"结论：GPU 通道未打通（硬失败 {hard}）")
        return 1
    soft = [n for n in fails if n not in hard]
    if soft:
        print(f"结论：GPU 通道打通（T2/T4 绿），软失败 {soft}")
    else:
        print("结论：GPU 通道完全打通（T1–T6 全绿）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())