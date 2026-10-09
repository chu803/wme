# syntax=docker/dockerfile:1
# ==============================================================================
#  AI 智能水印擦除工作台 —— Docker 镜像构建文件
#  阶段 197（优化清单 C5）：多阶段构建 + HEALTHCHECK + 非 root 运行。
#
#  用法：
#    构建：docker build -t watermark-eraser .
#    运行：docker run -p 7860:7860 \
#            -e WME_SERVER_HOST=0.0.0.0 \
#            -v "$PWD/outputs:/app/outputs" \
#            -v "$PWD/models:/app/models" \
#            watermark-eraser
#
#  ⚠️ 构建状态（阶段 210 · 诚实披露）：本文件在开发机上**从未真正 `docker build` 过**
#     （该机无 docker CLI / Docker Desktop）。已验证的是**静态契约**
#     （tests/test_dockerfile_contract.py：多阶段/HEALTHCHECK/WME_ 前缀/EXPOSE/非 root/
#     ENTRYPOINT/依赖单一真值来源，全绿）与**构建上下文瘦身**
#     （.dockerignore 已把 3.66GB 上下文压到 4.4MB，见该文件）。
#     首次在有 Docker 的机器上构建时，请至少跑通：① `docker build` 成功；
#     ② `docker run` 后 `curl localhost:7860` 出页面、`curl localhost:7861/api/health` 返回 200；
#     ③ 权重挂载后用一张真实图端到端去水印。详见 README「3.5 Docker 部署」小节。
#  说明：
#    1. 环境变量一律带 WME_ 前缀（`config.ENV_PREFIX`）——不带前缀会被 config 忽略：
#       ✅ WME_SERVER_HOST / WME_SERVER_PORT / WME_SERVER_OPEN_BROWSER
#       ❌ SERVER_HOST（旧写法，config 读不到 ⇒ 会退回 127.0.0.1，容器外访问不了）
#    2. 多阶段：builder 阶段装依赖到独立前缀 /install，runtime 阶段**只拷已装好的包**，
#       不带编译器、wheel 缓存与构建中间产物 ⇒ 最终镜像更小。
#    3. 非 root（uid 10001）运行；卷挂载目录需对该 uid 可写（Windows Docker Desktop 通常无感）。
#    4. 默认 CPU 推理；GPU 见文末参考配方。
# ==============================================================================

ARG PYTHON_VERSION=3.11

# ------------------------------ Stage 1 · builder -----------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

# 构建期工具链：部分依赖若没有预编译 wheel，需要就地编译。
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖清单并安装，利用 Docker 层缓存加速重建。
COPY requirements.txt /tmp/requirements.txt

# 安装到独立前缀 /install（而非系统 site-packages），供 runtime 阶段整体拷贝。
# 依赖清单真值来源仍是 requirements.txt（与本地一致，不复制第二份，见 LESSONS 187/195）。
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --prefix=/install \
         -r /tmp/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# ------------------------------ Stage 2 · runtime -----------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# 运行期系统库（OpenCV / Gradio 所需）+ ffmpeg（视频处理）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户（最小权限原则）。
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

# 只拷「已安装的包」（builder 的 /install → 系统 /usr/local），不带编译工具链与 wheel 缓存。
COPY --from=builder /install /usr/local

# 拷源码并按 appuser 授权（运行期需写 outputs / cache / temp / logs / exports / models）。
COPY --chown=appuser:appuser . .
RUN mkdir -p outputs cache temp logs exports models \
    && chown -R appuser:appuser /app

# 容器内默认允许外网访问（WME_ 前缀！）；关掉 Gradio 遥测以保持离线。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    WME_SERVER_HOST=0.0.0.0 \
    WME_SERVER_PORT=7860 \
    WME_SERVER_OPEN_BROWSER=False

# 暴露端口：
#   · 7860 —— Gradio 网页界面（config.SERVER_PORT 默认）
#   · 7861 —— 零依赖 REST 服务（server.py，需用 `python main.py --api` 启动）
EXPOSE 7860
EXPOSE 7861

# HEALTHCHECK：轮询 Gradio 首页是否就绪（用容器自带 python，免装 curl）。
# start-period 给足模型加载时间；一旦监听端口即判 HEALTHY。
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WME_SERVER_PORT','7860')+'/', timeout=4)"

USER appuser

# 入口：运行主程序（main.py 内部会做目录自检、模型就绪、启动 Gradio）。
ENTRYPOINT ["python", "main.py"]

# ------------------------------------------------------------------------------
#  【GPU 镜像参考配方】
#  ⚠ 本项目**已有独立的 Dockerfile.gpu**（阶段 197建、250 修解释器错配）——要显卡加速
#    直接用它，不要照抄下面的片段（阶段 250：下列片段是「曾经可行」的历史写法，
#    其 `python3` 与 `python3.11` 的错配、以及已过时的 cu121 时代假设都可能让镜像起不来）。
#  FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04        # ← 与 requirements-gpu.txt 的 cu128 源匹配
#  RUN apt-get update && apt-get install -y --no-install-recommends \
#          python3.11 python3-pip libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 ffmpeg \
#          && rm -rf /var/lib/apt/lists/*
#  # 关键：依赖装进 3.11 后，必须让 `python3` 也指向 3.11，或在 ENTRYPOINT 里显式写
#  #      `python3.11`（Ubuntu 22.04 的 `python3` 是 3.10，找不到 3.11 的 site-packages）。
#  ... 其余步骤见 Dockerfile.gpu，但 builder 阶段把 torch 换成 CUDA 版（见 requirements-gpu.txt）...
#  运行（网页界面）：docker run --gpus all -p 7860:7860 -p 7861:7861 -e WME_SERVER_HOST=0.0.0.0 watermark-eraser-gpu
#  运行（仅 REST 服务）：docker run --gpus all -p 7861:7861 -e WME_SERVER_HOST=0.0.0.0 watermark-eraser-gpu python3.11 main.py --api
# ------------------------------------------------------------------------------
