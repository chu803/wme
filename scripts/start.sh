#!/usr/bin/env bash
# ==============================================================================
#  AI 智能水印擦除工作台 —— Linux / macOS 一键启动脚本
#  用法：chmod +x scripts/start.sh && ./scripts/start.sh
#  说明：
#    1. 首次运行自动创建 venv 虚拟环境并安装依赖（较慢）；
#    2. 之后运行复用已有环境，直接启动界面；
#    3. 可附带参数，例如 ./start.sh --selftest 仅跑核心管线自测。
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

VENV="venv"
PYTHON="${PYTHON:-python3}"

# 1) 创建虚拟环境（若不存在）。
if [ ! -d "$VENV" ]; then
  echo "[启动器] 未检测到虚拟环境，正在创建 $VENV ..."
  "$PYTHON" -m venv "$VENV"
fi

# 2) 进入虚拟环境。
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# 3) 安装 / 升级依赖（清华镜像加速）。
echo "[启动器] 正在安装依赖（首次较慢，请耐心等待）..."
pip install --upgrade pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple || true
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4) 启动应用（把脚本参数原样传给 main.py）。
echo "[启动器] 依赖就绪，正在启动应用 ..."
exec python main.py "$@"
