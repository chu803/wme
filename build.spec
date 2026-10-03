# -*- mode: python ; coding: utf-8 -*-
# ==============================================================================
#  AI 智能水印擦除工作台 —— PyInstaller 打包配置（生成 Windows EXE）
#  用法（在已装好依赖的虚拟环境中执行）：
#      pyinstaller build.spec
#  产物：dist/AIWatermarkEraser/AIWatermarkEraser.exe（文件夹模式，含全部依赖）
#
#  说明：
#    1. 打包前请先在本机装好 requirements.txt（含 torch / gradio）；
#       若只需 OpenCV 降级版，可改用 requirements-min.txt 减小体积。
#    2. torch / gradio 体积较大，打包过程可能耗时数分钟，属正常现象。
#    3. hiddenimports 列出界面与核心层模块，避免打包后「缺模块」报错。
#    4. 打包后首次运行仍需下载 LaMa 模型（或放好 models/big-lama.pt），
#       模型文件不会被打进 EXE，需随程序一起分发或联网下载。
# ==============================================================================

import os

block_cipher = None

added_datas = [
    # 把 config.py 作为数据文件一并打包（部分运行路径下 import 行为较特殊）。
    ("config.py", "."),
]

a = Analysis(
    ["main.py"],
    pathex=[os.getcwd()],
    binaries=[],
    datas=added_datas,
    hiddenimports=[
        # 核心层
        "core",
        "core.device_manager",
        "core.model_loader",
        "core.inpainter",
        "core.detector",
        "core.image_utils",
        "core.mask_generator",
        # 服务层
        "services",
        "services.queue_worker",
        "services.cache_service",
        "services.file_service",
        "services.validators",
        "services.export_service",
        # 界面层
        "ui",
        "ui.app",
        "ui.tab_single",
        "ui.tab_auto",
        "ui.tab_batch",
        "ui.tab_history",
        "ui.tab_settings",
        "ui.theme",
        "ui.components",
        # 工具层
        "utils",
        "utils.logger",
        "utils.path_helper",
        "utils.exceptions",
        # 第三方关键库（确保被收集）
        "torch",
        "torchvision",
        "gradio",
        "gradio_client",
        "cv2",
        "numpy",
        "PIL",
        "skimage",
        "scipy",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 可选增强库，未安装时排除以减小体积。
        "easyocr",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AIWatermarkEraser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
