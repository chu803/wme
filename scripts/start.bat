@echo off
REM ==============================================================================
REM  AI 智能水印擦除工作台 —— Windows 一键启动脚本
REM  用法：双击本文件，或在项目根目录执行 scripts\start.bat
REM  说明：
REM    1. 首次运行会自动创建 venv 虚拟环境并安装依赖（较慢，请耐心等待）；
REM    2. 之后运行会复用已有环境，直接启动界面；
REM    3. 可附带参数，例如 start.bat --selftest 仅跑核心管线自测。
REM ==============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set "VENV=venv"
set "PYTHON=python"

REM 1) 创建虚拟环境（若不存在）。
if not exist "%VENV%\Scripts\activate.bat" (
    echo [启动器] 未检测到虚拟环境，正在创建 %VENV% ...
    %PYTHON% -m venv "%VENV%"
    if errorlevel 1 (
        echo [启动器] 创建虚拟环境失败，请确认已安装 Python 3.9~3.12 并加入 PATH。
        pause
        exit /b 1
    )
)

REM 2) 进入虚拟环境。
call "%VENV%\Scripts\activate.bat"

REM 3) 安装 / 升级依赖（清华镜像加速）。
echo [启动器] 正在安装依赖（首次较慢，请耐心等待）...
pip install --upgrade pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [启动器] pip 升级失败，将尝试直接安装依赖。
)
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [启动器] 依赖安装失败，请检查网络或手动执行：
    echo         pip install -r requirements.txt
    pause
    exit /b 1
)

REM 4) 启动应用（把脚本参数原样传给 main.py）。
echo [启动器] 依赖就绪，正在启动应用 ...
python main.py %*
endlocal
