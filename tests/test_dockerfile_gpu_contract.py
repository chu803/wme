# -*- coding: utf-8 -*-
"""Dockerfile.gpu 契约守卫（D1 产品化 · D1-Stage 3；阶段 207 修正）。

把「GPU 镜像」的关键不变量钉死，避免回归成「看起来是 GPU 镜像、实际 torch 仍是 CPU 版」：
  ① 文件存在；
  ② 基础镜像为 nvidia/cuda，且 **CUDA ≥ 12.8**（RTX 50 系 / Blackwell sm_120 的下限）；
  ③ torch 从 `requirements-gpu.txt` 安装（CUDA 版）——**单一真值来源**，
     本文件不得硬编码 torch 版本（阶段 207 起改为对账式判据）；
  ④ ENV 必须带 WME_ 前缀（不得出现裸 SERVER_HOST）；
  ⑤ EXPOSE 同时暴露 7860（Gradio）与 7861（REST）；
  ⑥ 非 root 运行；
  ⑦ ENTRYPOINT 运行 main.py；
  ⑧ 有 HEALTHCHECK；
  ⑨ 依赖仍来自 requirements.txt（单一真值来源）。

阶段 207 背景：原配方钉 `torch==2.3.1` + `cu121` + 基础镜像 `12.1.1`，而
   · cu121 的 torch 只含到 sm_90 的 kernel ⇒ 在 RTX 50 系（sm_120）上不可用；
   · torch 2.3.1 没有 cp313 wheel ⇒ 在 Python 3.13 上装不上。
  实测（RTX 5060 + Python 3.13 + torch 2.9.1+cu128）确认 `sm_120` 可用。
  同时把「版本」收敛到 `requirements-gpu.txt` 一处，避免两处清单漂移。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _ROOT / "Dockerfile.gpu"
_GPU_REQS = _ROOT / "requirements-gpu.txt"


def _text() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_gpu_exists():
    assert _DOCKERFILE.is_file(), "缺少 Dockerfile.gpu（GPU 镜像构建文件）"


def test_base_image_is_nvidia_cuda():
    """GPU 镜像必须基于 nvidia/cuda，自带 CUDA 运行库。"""
    text = _text()
    assert re.search(r"(?im)^\s*FROM\s+nvidia/cuda", text), \
        "Dockerfile.gpu 基础镜像应为 nvidia/cuda（提供 CUDA 运行库）"


def test_cuda_version_supports_blackwell():
    """CUDA 版本必须 ≥ 12.8 —— RTX 50 系（Blackwell / sm_120）的下限。

    阶段 207 修正：原为 12.1.1，只覆盖到 sm_90 ⇒ 50 系卡上缺 kernel 不可用。
    """
    versions = re.findall(r"(?im)^\s*FROM\s+nvidia/cuda:(\d+)\.(\d+)", _text())
    assert versions, "未解析出 nvidia/cuda 版本号"
    for major, minor in versions:
        v = (int(major), int(minor))
        assert v >= (12, 8), (
            f"基础镜像 CUDA {major}.{minor} < 12.8 ⇒ 不含 Blackwell(sm_120) kernel，"
            f"RTX 50 系不可用"
        )


def test_installs_cuda_torch_from_single_source():
    """torch 必须从 `requirements-gpu.txt` 安装 —— **不得在本文件硬编码版本**。

    为什么改成对账式判据（阶段 207）：原先 Dockerfile 与契约测试各硬编码一份
    `torch==2.3.1`/`cu121`，正是本项目反复警惕的「两处真值来源」；
    一旦升级要改两个地方，很容易只改一处 ⇒ 镜像与清单不一致。
    """
    text = _text()
    assert "requirements-gpu.txt" in text, \
        "Dockerfile.gpu 未从 requirements-gpu.txt 安装 CUDA 版 torch（单一真值来源）"
    assert re.search(r"-r\s+\S*requirements-gpu\.txt", text), \
        "应以 `-r .../requirements-gpu.txt` 形式安装，而非硬编码 torch 版本"
    # 负面断言：不得再出现硬编码的 torch 版本号（WME_ 前缀式的否定式检查思路）
    hardcoded = re.findall(r"torch==[\d.]+", text)
    assert not hardcoded, (
        f"Dockerfile.gpu 仍硬编码 torch 版本 {hardcoded} ⇒ 与 requirements-gpu.txt 会漂移"
    )


def test_gpu_requirements_pins_cuda128():
    """`requirements-gpu.txt` 必须指向 cu128 源（RTX 50 系所需）并钉住 torch 版本。"""
    text = _GPU_REQS.read_text(encoding="utf-8")
    assert "download.pytorch.org/whl/cu128" in text, \
        "requirements-gpu.txt 未使用 cu128 源（RTX 50 系 / sm_120 需要）"
    assert re.search(r"(?m)^torch==", text), "requirements-gpu.txt 未钉 torch 版本"


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
