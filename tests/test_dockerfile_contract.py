# -*- coding: utf-8 -*-
"""阶段 197（C5）· Dockerfile 契约守卫。

为什么需要它：本轮审计发现旧 Dockerfile 有一个**静默缺陷**——它设 `SERVER_HOST=0.0.0.0`，
而 `config` 只认带前缀的 `WME_SERVER_HOST`（`config.ENV_PREFIX = "WME_"`）⇒ 容器**退回
默认 127.0.0.1，容器外访问不了**，且没有任何报错（典型的「配置名存实亡」）。
本守卫把这份契约钉死，覆盖：
  ① 存在 Dockerfile 与 .dockerignore；
  ② **多阶段构建**（≥2 个 FROM，且至少一个命名阶段）；
  ③ 有 **HEALTHCHECK**（且探测端口与 EXPOSE 一致）；
  ④ 服务端环境变量**必须带 WME_ 前缀**（尤其不得出现裸 `SERVER_HOST=`）；
  ⑤ EXPOSE 端口 == 默认 7860；
  ⑥ **非 root** 运行；
  ⑦ ENTRYPOINT 运行 main.py；
  ⑧ 依赖仍来自 requirements.txt（单一真值来源，不另起清单）。
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = _ROOT / "Dockerfile"
_DOCKERIGNORE = _ROOT / ".dockerignore"


def _text() -> str:
    return _DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_and_dockerignore_exist():
    assert _DOCKERFILE.is_file(), "缺少 Dockerfile"
    assert _DOCKERIGNORE.is_file(), "缺少 .dockerignore"


def test_is_multi_stage():
    text = _text()
    froms = re.findall(r"(?im)^\s*FROM\s+\S+", text)
    assert len(froms) >= 2, f"多阶段构建至少需要 2 个 FROM，实测 {len(froms)} 个"
    assert re.search(r"(?im)^\s*FROM\s+\S+\s+AS\s+\S+", text), \
        "缺少命名构建阶段（FROM ... AS <name>）"


def test_has_healthcheck():
    text = _text()
    assert re.search(r"(?im)^\s*HEALTHCHECK\b", text), "缺少 HEALTHCHECK 指令"
    # 探测端口须与默认端口一致（避免改端口忘了改探测）
    assert "7860" in text, "HEALTHCHECK 未引用默认端口 7860"


def test_server_env_uses_wme_prefix():
    """核心回归：config 只认 WME_ 前缀，裸 SERVER_HOST 会被静默忽略。"""
    text = _text()
    assert "WME_SERVER_HOST=0.0.0.0" in text, "缺少 WME_SERVER_HOST=0.0.0.0（容器外访问所需）"
    for bare in ("SERVER_HOST", "SERVER_PORT", "SERVER_OPEN_BROWSER"):
        assert not re.search(rf"(?<!WME_){bare}\s*=", text), (
            f"出现未加 WME_ 前缀的 {bare}=（config 读不到，属静默失效）——"
            f"请改用 WME_{bare}="
        )


def test_expose_port_matches_default():
    m = re.search(r"(?im)^\s*EXPOSE\s+(\d+)", _text())
    assert m, "缺少 EXPOSE 指令"
    assert m.group(1) == "7860", f"EXPOSE 端口应为 7860（config.SERVER_PORT 默认），实测 {m.group(1)}"


def test_expose_api_port():
    """REST 服务（server.py）监听 7861，容器必须暴露该端口才能远程调用。"""
    assert "7861" in _text(), "Dockerfile 未 EXPOSE 7861（REST 服务端口）"


def test_runs_as_non_root():
    m = re.search(r"(?im)^\s*USER\s+(\S+)", _text())
    assert m, "缺少 USER 指令（应显式切换到非 root 用户）"
    assert m.group(1).lower() not in ("root", "0"), f"不应以 root 运行，实测 USER={m.group(1)}"


def test_entrypoint_runs_main():
    m = re.search(r"(?im)^\s*ENTRYPOINT\s+(.+)$", _text())
    assert m, "缺少 ENTRYPOINT 指令"
    assert "main.py" in m.group(1), f"ENTRYPOINT 应运行 main.py，实测：{m.group(1)}"


def test_dependencies_come_from_requirements():
    """依赖单一真值来源：Dockerfile 必须从 requirements.txt 安装，不另起一份清单。"""
    assert "requirements.txt" in _text(), "Dockerfile 未从 requirements.txt 安装依赖"
