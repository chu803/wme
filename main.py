# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : main.py
 文件说明 : 项目主启动程序。负责「启动前的所有准备工作」与「启动入口分发」。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【合规强制声明 —— 使用前请务必阅读】
   1. 本工具专用于去除使用者本人生成的 AI 图片上的可见水印 / 署名标识。
   2. 严禁使用本工具清除他人享有著作权作品上的水印、署名或版权标识。
   3. 不建议用于去除 AI 绘图平台的第三方署名水印（如 Midjourney），
      此类行为通常违反对应平台的服务条款，本工具不鼓励该用途。
   4. 依据《中华人民共和国著作权法》第五十三条，未经权利人许可，
      故意删除或者改变作品的权利管理电子信息的，应当承担停止侵害、
      赔偿损失等民事责任；情节严重的，可由主管部门责令停止侵权行为、
      没收违法所得、处以罚款。
   5. 使用者因违规使用本工具产生的一切法律后果，由使用者本人承担，
      与本工具作者无关。
==============================================================================
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import config
from utils.logger import get_logger, print_banner

log = get_logger("main")


# ==============================================================================
# 第 1 节：启动横幅与合规声明
# ==============================================================================

def print_startup_banner() -> None:
    """
    在控制台打印启动横幅与合规声明（不写日志文件，仅给人看）。
    """
    print_banner(f"  {config.APP_NAME}  v{config.APP_VERSION}")
    # 合规声明按原样输出，确保用户启动即见。
    print(config.COMPLIANCE_NOTICE)


# ==============================================================================
# 第 2 节：环境自检
# ==============================================================================

def run_environment_check() -> bool:
    """
    执行启动前的环境与配置自检。

    返回值：
        True 表示环境基本可用；False 表示存在需用户处理的问题。

    说明：
        自检不追求「完美无缺」，而是「抓出会导致启动崩溃」的关键项：
        目录可写、配置合法、关键依赖可用。
    """
    print("\n>>> 正在执行环境与配置自检 ...")

    # 2.1 创建/校验所有目录。
    try:
        created = config.ensure_directories()
        if created:
            log.info("已自动创建目录：%s", [str(p) for p in created])
        print("  [OK] 工作目录已就绪")
    except Exception as exc:
        print(f"  [FAIL] 目录创建失败：{exc}")
        return False

    # 2.2 配置合法性自检。
    problems = config.validate_config()
    if problems:
        for p in problems:
            print(f"  [WARN] {p}")
    else:
        print("  [OK] 配置项校验通过")

    # 2.3 关键依赖探测（cv2 必需；torch 用于 LaMa）。
    deps_ok = True
    try:
        import cv2  # noqa
        print("  [OK] OpenCV 已安装")
    except ImportError:
        print("  [FAIL] 未安装 OpenCV，请执行 pip install opencv-python-headless")
        deps_ok = False

    try:
        import torch  # noqa
        print(f"  [OK] PyTorch 已安装（版本 {torch.__version__}）")
    except ImportError:
        print("  [WARN] 未安装 PyTorch，LaMa 引擎不可用（可临时使用 OpenCV 方案）")

    # 2.4 设备探测预览。
    try:
        from core.device_manager import get_device_info

        info = get_device_info()
        print(f"  [OK] 计算设备：{info.name}（{info.reason}）")
    except Exception as exc:
        print(f"  [WARN] 设备探测异常：{exc}")

    return deps_ok


# ==============================================================================
# 第 3 节：模型就绪保障
# ==============================================================================

def ensure_model_ready() -> bool:
    """
    确保 LaMa 模型就位：缺失且开启自动下载时触发下载。

    返回值：
        True 表示模型可用（或至少 OpenCV 兜底可用）；False 表示严重缺失。
    """
    from core.model_loader import is_model_available

    if is_model_available():
        print("\n>>> 当前模型已就位，无需下载。")
        return True

    if not config.MODEL_AUTO_DOWNLOAD:
        print(
            "\n>>> 未检测到当前模型，但已关闭自动下载。\n"
            f"    请手动运行：python download_model.py\n"
            f"    或把模型放到：{config.MODEL_PATH}"
        )
        return False

    # 阶段 12：自动下载「当前模型槽（active）」权重。
    #   仅当该槽是内置 lama（保证有下载源）时才自动下载；
    #   其它自定义槽若无下载源，交给用户手动放置，避免误下错权重。
    from core import model_manager as _mm

    active_slot = _mm.get_model_slot(None)
    can_auto = bool(active_slot and (active_slot.builtin or active_slot.urls))
    if not can_auto:
        print(
            "\n>>> 当前模型槽无可用下载源，已跳过自动下载。\n"
            f"    请手动把权重放到：{active_slot.path if active_slot else config.MODEL_PATH}"
        )
        return False

    print(f"\n>>> 未检测到当前模型（{_mm.get_active_model()}），开始自动下载 ...")
    try:
        _mm.download_model(None)
        return True
    except Exception as exc:
        log.error("模型下载失败：%s", exc)
        print(
            f"\n>>> 模型下载失败：{exc}\n"
            ">>> 程序仍可使用 OpenCV 方案处理细小水印；"
            "如需 LaMa 效果，请手动下载模型后重试。"
        )
        return False


# ==============================================================================
# 第 3.5 节：启动期性能报告与降级提示
# ==============================================================================

def print_performance_report() -> None:
    """
    打印性能相关配置摘要（线程 / 分块 / 并发 / 半精度），便于排查性能问题。
    """
    print("\n>>> 性能配置：")
    print(f"  计算设备模式   : {config.DEVICE_MODE}")
    print(
        f"  CPU 推理线程数 : "
        f"{config.CPU_THREADS if config.CPU_THREADS else '自动（物理核心数）'}"
    )
    print(f"  分块大小       : {config.TILE_SIZE} px（重叠 {config.TILE_OVERLAP} px）")
    print(f"  批量并发数     : {config.BATCH_CONCURRENCY}")
    print(f"  服务最大线程   : {config.SERVER_MAX_THREADS}")
    print(f"  半精度推理     : {'开启' if config.DEVICE_USE_FP16 else '关闭'}")


def report_device_and_gpu_hint() -> None:
    """
    打印当前计算设备状态，并在「装了 PyTorch 却没用上显卡」时给出开启加速的指引。
    """
    from core.device_manager import get_device_info

    try:
        info = get_device_info()
    except Exception as exc:  # noqa: BLE001 - 探测异常不应阻断启动
        print(f"  [WARN] 设备探测异常：{exc}")
        return

    if info.is_gpu:
        vram = f"（显存约 {info.vram_mb} MB）" if info.vram_mb else ""
        print(f"  [设备] ✅ 已启用 GPU 加速：{info.name}{vram}")
        return

    # CPU 模式下，根据是否装了 torch 给出不同的提示。
    print(f"  [设备] ℹ️  当前使用 CPU 模式：{info.reason}")
    if not config.STARTUP_GPU_HINT:
        return

    try:
        import torch  # 仅判断 torch 是否安装
    except ImportError:
        torch = None  # type: ignore[assignment]

    if torch is None:
        print(
            "  [提示] 未安装 PyTorch，LaMa 引擎不可用（已自动降级为 OpenCV）。\n"
            "         如需 LaMa 神经网络修复，请安装：\n"
            "         pip install torch torchvision"
        )
    elif not torch.cuda.is_available():
        print(
            "  [提示] 已安装 PyTorch 但未检测到 NVIDIA 显卡驱动。\n"
            "         如需启用显卡加速，请安装 CUDA 版 PyTorch：\n"
            "         pip uninstall -y torch torchvision\n"
            "         pip install torch torchvision "
            "--index-url https://download.pytorch.org/whl/cu121"
        )


def warm_up_model() -> None:
    """
    启动期预热模型：若模型已就位则加载一次 LaMa，给出明确的引擎就绪状态；
    若不可用则明确告知已回退 OpenCV。让用户在打开浏览器前就清楚本次用哪套引擎。
    """
    if not config.STARTUP_WARMUP_MODEL:
        return

    # 阶段 238：启动期预热 EasyOCR（后台守护线程，不阻塞界面启动）。
    #   与 LaMa 模型预热解耦：即便模型缺失也照常预热 OCR，首个检测请求不再冷启动卡顿。
    try:
        from core.detector import warm_up_ocr

        if config.DETECT_USE_OCR:
            warm_up_ocr(blocking=False)
            print("  [OCR]  ⏳ EasyOCR 正在后台预热（首次检测不再冷启动卡顿）。")
        else:
            print("  [OCR]  ℹ️  DETECT_USE_OCR 关闭，跳过 OCR 预热。")
    except Exception as exc:  # noqa: BLE001 - 预热失败绝不影响启动
        log.debug("OCR 预热触发异常（不影响启动）：%s", exc)

    from core.model_loader import is_model_available, load_lama_model
    from utils.exceptions import (
        ModelCorruptedError,
        ModelLoadError,
        ModelNotFoundError,
    )

    if not is_model_available():
        print(
            "  [模型] ℹ️  LaMa 模型缺失，将使用 OpenCV 降级引擎"
            "（仅适合细小水印，识别率低于 LaMa）。"
        )
        return
    try:
        load_lama_model()
        print("  [模型] ✅ LaMa 模型已加载，神经网络修复引擎就绪。")
    except (ModelNotFoundError, ModelLoadError, ModelCorruptedError) as exc:
        print(f"  [模型] ⚠️  LaMa 加载失败，回退 OpenCV：{exc}")
    except Exception as exc:  # noqa: BLE001 - 兜底，避免启动被模型问题卡死
        log.warning("模型预热异常：%s", exc)
        print(f"  [模型] ⚠️  LaMa 加载异常，回退 OpenCV：{exc}")


# ==============================================================================
# 第 4 节：无界面自测（核心管线冒烟测试）
# ==============================================================================

def run_selftest() -> int:
    """
    无界面模式下，用一张合成图跑通「加载 → 修复 → 保存」核心管线，
    用于验证阶段 2 的核心代码在目标机器上真实可运行。

    返回值：
        0 表示自测通过；非 0 表示失败。
    """
    print("\n>>> 运行核心管线自测（无界面）...")
    try:
        import numpy as np
        import cv2

        from core.image_utils import load_image, save_image
        from core.inpainter import inpaint
        from utils.path_helper import get_temp_dir

        # 造一张带中央水印的合成图。
        img = np.full((400, 400, 3), 220, dtype=np.uint8)
        img[160:240, 160:240] = 40  # 水印块
        mask = np.zeros((400, 400), dtype=np.uint8)
        mask[160:240, 160:240] = 255

        tmp_dir = get_temp_dir()
        src = os.path.join(tmp_dir, "selftest_input.png")
        save_image(src, img)

        loaded = load_image(src)  # 验证读写往返
        out = inpaint(loaded, mask, engine="opencv", strength=1.0, denoise=0)
        assert out[200, 200, 0] != 40, "修复未生效"

        dst = os.path.join(tmp_dir, "selftest_output.png")
        save_image(dst, out)
        print(f"  [OK] 自测通过，示例输出：{dst}")
        return 0
    except Exception as exc:
        log.exception("核心管线自测失败")
        print(f"  [FAIL] 自测异常：{exc}")
        return 1


# ==============================================================================
# 第 5 节：启动 Gradio 界面（界面在阶段 3 接入，此处做安全分发）
# ==============================================================================

def launch_ui() -> int:
    """
    尝试启动 Gradio 交互界面。界面模块（ui.app）在阶段 3 已构建完成，
    若此处仍加载失败，通常是缺少 Gradio 依赖，给出友好指引而非崩溃堆栈。

    返回值：
        进程退出码。
    """
    try:
        # 延迟导入：避免在纯自测（--selftest）场景下强制依赖 Gradio。
        from ui.app import build_app, launch_app

        app = build_app()
        return launch_app(app)
    except (ModuleNotFoundError, FileNotFoundError, ImportError) as exc:
        log.warning("图形界面模块加载失败：%s", exc)
        print(
            "\n>>> 图形界面启动失败，最常见原因是缺少 Gradio 依赖。\n"
            ">>> 请先执行：pip install -r requirements.txt\n"
            ">>> 安装完成后重新运行：python main.py\n"
        )
        # 先确认核心依赖（numpy / OpenCV）是否已安装，缺依赖时直接给安装指引。
        try:
            import numpy  # noqa: F401
            import cv2  # noqa: F401
        except ImportError:
            print(
                ">>> 核心依赖（OpenCV / NumPy）也缺失。\n"
                ">>> 请先执行：pip install -r requirements.txt\n"
                ">>> 之后再运行：python main.py --selftest 验证核心算法。"
            )
            return 1
        # 核心依赖齐全但 Gradio 缺失：明确提示，不跑自测（自测不涉及界面）。
        try:
            import gradio  # noqa: F401
        except ImportError:
            print(">>> 缺少 Gradio，无法启动网页界面，但核心算法可正常运行。")
            return 1
        return run_selftest()


# ==============================================================================
# 第 6 节：命令行参数与主入口
# ==============================================================================

def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description=f"{config.APP_NAME} 启动程序",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--selftest", action="store_true", help="仅运行核心管线自测，不启动界面"
    )
    parser.add_argument(
        "--no-download", action="store_true", help="跳过模型自动下载"
    )
    parser.add_argument(
        "--skip-check", action="store_true", help="跳过启动前环境自检"
    )
    parser.add_argument(
        "--api", action="store_true",
        help="以零依赖 REST 服务模式启动（server.py），不启动 Gradio 界面"
    )
    parser.add_argument(
        "--api-host", default=None,
        help="REST 服务监听地址（缺省取 config.API_HOST，默认 127.0.0.1）"
    )
    parser.add_argument(
        "--api-port", default=None, type=int,
        help="REST 服务端口（缺省取 config.API_PORT，默认 7861）"
    )
    return parser.parse_args()


def main() -> int:
    """
    主流程编排：横幅 → 自检 → 模型 → 启动界面/自测。

    返回值：
        进程退出码（0 正常）。
    """
    # 横幅与合规声明永远最先输出。
    print_startup_banner()

    # 阶段 11：启动时扫描并加载插件（自定义修复引擎）。
    # 失败仅告警不崩溃，保证一个烂插件不会拖垮整个程序。
    try:
        from core import plugins as _plugins

        loaded = _plugins.load_plugins()
        if loaded:
            log.info("已加载 %d 个插件，可用引擎：%s", loaded, _plugins.get_engine_choices())
    except Exception as exc:  # noqa: BLE001
        log.warning("插件加载异常（不影响主程序）：%s", exc)

    args = parse_args()

    # 临时覆盖自动下载开关（--no-download）。
    if args.no_download:
        config.MODEL_AUTO_DOWNLOAD = False  # type: ignore[attr-defined]

    # 1) 环境自检。
    if not args.skip_check:
        ok = run_environment_check()
        # 自测模式交给 run_selftest 自行给出「缺依赖」的友好提示，
        # 因此即便依赖缺失也不在此处硬失败（仅真正启动时要求依赖齐全）。
        if not ok and not args.selftest:
            print("\n>>> 环境自检存在致命问题，请按提示修复后重试。")
            return 1

    # 2) 启动分发。
    #    自测模式只需核心算法，不需要模型，故放在「确保模型」之前提前返回，
    #    避免无谓地触发模型网络下载（可能长时间超时）。
    if args.selftest:
        return run_selftest()

    # 3) 性能报告 + 设备/降级提示（让用户清楚本次运行的能力边界）。
    print_performance_report()
    report_device_and_gpu_hint()

    # 4) 模型就绪（缺失且开启自动下载时触发下载 / 否则提示）。
    ensure_model_ready()

    # 5) 预热模型：加载 LaMa 或明确回退 OpenCV，给出引擎就绪状态。
    warm_up_model()

    # 6) 启动分发：--api 走零依赖 REST 服务（server.py），否则启动 Gradio 界面。
    if args.api:
        try:
            from server import start_server
        except ImportError as exc:  # pragma: no cover - 模块缺失极少见
            log.error("REST 服务模块 server.py 无法导入：%s", exc)
            print("\n>>> 无法加载 REST 服务模块（server.py）。")
            return 1
        return start_server(host=args.api_host, port=args.api_port)

    return launch_ui()


if __name__ == "__main__":
    sys.exit(main())
