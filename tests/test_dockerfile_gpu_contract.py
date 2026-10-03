# -*- coding: utf-8 -*-
"""Dockerfile.gpu 契约守卫（D1 产品化 · D1-Stage 3）。

把「GPU 镜像」的关键不变量钉死，避免回归成「看起来是 GPU 镜像、实际 torch 仍是 CPU 版」：
  ① 文件存在；
  ② 基础镜像为 nvidia/cuda（带 CUDA 运行库）；
  ③ torch / torchvision 必须从 PyTorch **cu121** 源安装（CUDA 版），而非默认 CPU 版；
  ④ ENV 必须带 WME_ 前缀（不得出现裸 SERVER_HOST）；
  ⑤ EXPOSE 同时暴露 7860（Gradio）与 7861（REST）；
  ⑥ 非 root 运行；
  ⑦ ENTRYPOINT 运行 main.py；
  ⑧ 有 HEALTHCHECK；
  ⑨ 依赖仍来自 requirements.txt（单一真值来源）。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _ROOT / "Dockerfile.gpu"


def _text() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_gpu_exists():
    assert _DOCKERFILE.is_file(), "缺少 Dockerfile.gpu（GPU 镜像构建文件）"


def test_base_image_is_nvidia_cuda():
    """GPU 镜像必须基于 nvidia/cuda，自带 CUDA 运行库。"""
    text = _text()
    assert re.search(r"(?im)^\s*FROM\s+nvidia/cuda", text), \
        "Dockerfile.gpu 基础镜像应为 nvidia/cuda（提供 CUDA 运行库）"


def test_installs_cuda_torch():
    """torch 必须从 PyTorch cu121 源安装（CUDA 版），否则 DEVICE_MODE=auto 检测不到 CUDA。"""
    text = _text()
    assert "torch==2.3.1" in text, "Dockerfile.gpu 未安装 CUDA 版 torch"
    assert "torchvision==0.18.1" in text, "Dockerfile.gpu 未安装 CUDA 版 torchvision"
    assert "download.pytorch.org/whl/cu121" in text, \
        "torch 必须来自 PyTorch cu121 源（CUDA 版），而非默认 CPU 版 PyPI"


def test_server_env_uses_wme_prefix():
    """与默认 Dockerfile 同一铁律：配置名必须带 WME_ 前缀，裸 SERVER_HOST 会静默失效。"""
    text = _text()
    assert "WME_SERVER_HOST=0.0.0.0" in text, "缺少 WME_SERVER_HOST=0.0.0.0"
    for bare in ("SERVER_HOST", "SERVER_PORT", "SERVER_OPEN_BROWSER"):
        assert not re.search(rf"(?<!WME_){bare}\s*=", text), \
            f"出现未加 WME_ 前缀的 {bare}=（config 读不到，属静默失效）"


def test_expose_both_ports():
    """同时暴露网页 7860 与 REST 7861。"""
    text = _text()
    assert "7860" in text, "未 EXPOSE 7860（Gradio 网页端口）"
    assert "7861" in text, "未 EXPOSE 7861（REST 服务端口）"


def test_runs_as_non_root():
    m = re.search(r"(?im)^\s*USER\s+(\S+)", _text())
    assert m, "缺少 USER 指令（应切换到非 root 用户）"
    assert m.group(1).lower() not in ("root", "0"), f"不应以 root 运行，实测 USER={m.group(1)}"


def test_entrypoint_runs_main():
    m = re.search(r"(?im)^\s*ENTRYPOINT\s+(.+)$", _text())
    assert m, "缺少 ENTRYPOINT 指令"
    assert "main.py" in m.group(1), f"ENTRYPOINT 应运行 main.py，实测：{m.group(1)}"


def test_has_healthcheck():
    assert re.search(r"(?im)^\s*HEALTHCHECK\b", _text()), "缺少 HEALTHCHECK 指令"


def test_dependencies_come_from_requirements():
    """依赖单一真值来源：Dockerfile 必须从 requirements.txt 安装，不另起一份清单。"""
    assert "requirements.txt" in _text(), "Dockerfile.gpu 未从 requirements.txt 安装依赖"
