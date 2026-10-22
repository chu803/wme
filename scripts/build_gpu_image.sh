#!/usr/bin/env bash
# ==============================================================================
#  AI 智能水印擦除工作台 —— GPU 镜像「构建 + 启动 + 健康检查」一键脚本
#
#  运行环境：装好 Docker Desktop（WSL2 后端）+ NVIDIA 驱动 + nvidia-container-toolkit
#            的 Windows 宿主机，在本仓库根目录用 Git Bash 执行：
#                bash scripts/build_gpu_image.sh
#
#  说明：
#    · 本脚本在「你的本机」跑，不在 AI 侧执行环境（AI 侧的沙箱没有 docker daemon）。
#    · 镜像基于 Dockerfile.gpu（CUDA 12.8 + torch 2.9.1+cu128，匹配 RTX 50 系 Blackwell）。
#    · 默认走 Gradio 网页模式（端口 7860）；要 REST 服务（7861）见文末。
#    · 权重默认首次启动自动下载，不进镜像（.dockerignore 已排除 models/）。
#    · 国内网络：Docker Hub 拉基础镜像若慢/失败，需在 Docker Desktop
#      Settings → Docker Engine 加 registry mirror（如 https://docker.xuanyuan.me）。
# ==============================================================================
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

IMAGE="watermark-eraser-gpu"
CONTAINER="wme-gpu"

echo "==> [1/4] 构建 GPU 镜像（CUDA 12.8 / torch 2.9.1+cu128）"
docker build -f Dockerfile.gpu -t "$IMAGE" .

echo "==> [2/4] 启动容器（GPU + 端口 7860）"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" --gpus all \
  -p 7860:7860 \
  -e WME_SERVER_HOST=0.0.0.0 \
  "$IMAGE"

echo "==> [3/4] 等待 HEALTHCHECK 转 healthy（最多 ~150s）"
for i in $(seq 1 30); do
  STATUS="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)"
  echo "   [$i] health=$STATUS"
  if [ "$STATUS" = "healthy" ]; then break; fi
  sleep 5
done

echo "==> [4/4] 验证 Gradio 网页端点"
echo -n "   Gradio 首页 (http://localhost:7860/): "
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:7860/ || echo "FAIL"

echo ""
echo "✅ 若上面返回 200，镜像构建与启动成功。"
echo "   浏览器打开 http://localhost:7860 即可使用工作台。"
echo "   停止容器：docker stop $CONTAINER"
echo "   查看日志：docker logs -f $CONTAINER"
echo ""
echo "   （可选）仅跑 REST 服务（端口 7861，覆盖默认命令）："
echo "   docker run --gpus all -p 7861:7861 -e WME_SERVER_HOST=0.0.0.0 \\"
echo "       $IMAGE python3.11 main.py --api"
echo "   然后：curl http://localhost:7861/api/health"
