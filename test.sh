#!/usr/bin/env bash
# ============================================================================
#  AI 智能水印擦除工作台 —— 本地测试脚本（阶段 13 新增，阶段 192 改造）
#  用法：./test.sh [python3 解释器路径]
#  说明：默认离线运行，跳过需要 torch / 网络的用例。
#
#  ⚠ 阶段 192：**不再自己写一份 pytest 选择逻辑**，改为调用统一验证入口
#     `scripts/run_all_checks.py`（与 CI 同一入口）。此前本文件与 test.bat 各自
#     复制了一份 `-m "not torch_required and not network" ... --timeout=300`，
#     加上 ci.yml 就有**三处**副本 ⇒ 改一处忘一处必然漂移（经验 184/195）。
# ============================================================================
set -uo pipefail

PY="${1:-python3}"

echo "============================================"
echo "  AI 智能水印擦除工作台 · 验证入口"
echo "  模式：离线（跳过 torch_required / network）"
echo "  解释器：$PY"
echo "============================================"

echo "[1/2] 安装测试依赖（若已装则秒过）..."
"$PY" -m pip install -q -r requirements-dev.txt || echo "⚠️  依赖安装失败，尝试继续（可能已就绪）..."

echo "[2/2] 运行统一验证入口（测试 + 静态扫描）..."
# 不用 `set -e` 直接中断：入口的退出码契约是 0/1/2（2=跳过），三种都要如实透出。
if "$PY" scripts/run_all_checks.py; then
  RC=0
else
  RC=$?
fi

if [ "$RC" -eq 0 ]; then
  echo ""
  echo "✅ 全部检查通过。"
elif [ "$RC" -eq 2 ]; then
  echo ""
  echo "⏭ 全部步骤跳过（退出码 2）。"
else
  echo ""
  echo "❌ 存在未通过项，退出码=$RC"
fi
exit "$RC"
