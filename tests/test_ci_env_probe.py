# -*- coding: utf-8 -*-
"""阶段 206 · `scripts/ci_env_probe.py` 的守卫（CI 环境等价性探针）。

只测**纯逻辑与零副作用路径**（毫秒级）：屏蔽判定、屏蔽器生效、
模型目录改向、pytest 参数构造、退出码语义。
**不跑**默认模式（那会真跑一遍 10 分钟的 CI 子集）。

为什么需要这个探针（背景）：`.github/workflows/ci.yml` 只装
`requirements-min.txt` + `requirements-dev.txt`（刻意不含 torch / PyMuPDF），
而开发机是完整环境 ⇒ 「本地绿、CI 红」会被环境差异掩盖；而 CI 只有推到远端才跑
⇒ 差异可能长期无人发现。本阶段即由该探针实测出「15 个用例在 CI 上会失败」。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 直接加载脚本模块（scripts/ 无 __init__.py，与 clean_artifacts 的既有做法一致）
_SPEC = importlib.util.spec_from_file_location(
    "ci_env_probe", str(_ROOT / "scripts" / "ci_env_probe.py")
)
_probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_probe)


def test_is_blocked_matches_exactly_the_declared_set():
    """屏蔽判定必须与声明清单一致（含子模块），且**不误伤**传递依赖。"""
    for m in _probe.BLOCKED_MODULES:
        assert _probe.is_blocked(m), f"{m} 应被屏蔽"
        assert _probe.is_blocked(f"{m}.sub.module"), f"{m} 的子模块也应被屏蔽"
    # 关键反例：gradio 的传递依赖**不能**被屏蔽，否则模拟错了环境（会造假红）。
    for safe in ("yaml", "pandas", "numpy", "PIL", "cv2", "gradio", "requests"):
        assert not _probe.is_blocked(safe), (
            f"{safe} 不应被屏蔽——它是已装包的（传递）依赖，CI 实际会有"
        )


@pytest.fixture
def _restore_import_state():
    """保存并还原 `sys.meta_path` 与 `sys.modules` 中被屏蔽模块的键。

    ⚠ 为什么必须有它（LESSONS 213「改全局单例必须自包含」）：
    本文件要测屏蔽器，而屏蔽器会**永久改写 `sys.meta_path`**（全局单例）。
    首版没做还原 ⇒ 同进程**后续所有测试**的 torch/fitz 都被屏蔽而集体失败
    （实测 `test_validators_media` 5 个用例报 `[CI 环境模拟] ModuleNotFoundError`）。
    """
    original_meta = list(sys.meta_path)
    saved = {m: sys.modules[m] for m in list(sys.modules)
             if _probe.is_blocked(m)}
    try:
        yield
    finally:
        sys.meta_path[:] = original_meta
        for m in [k for k in list(sys.modules) if _probe.is_blocked(k)]:
            del sys.modules[m]
        sys.modules.update(saved)          # 还原**原始**模块对象（保住身份一致）


def test_blocker_actually_raises_and_purges_loaded_modules(_restore_import_state):
    """屏蔽器必须真的让 import 失败，且把已加载模块移出 sys.modules。"""
    # 伪装「torch 已加载」，验证 install 会把它清掉（不真 import torch，避免重载开销）
    sys.modules.setdefault("torch", types.ModuleType("torch"))
    _probe.install_blocker()
    assert "torch" not in sys.modules, "已加载的被屏蔽模块应被移出 sys.modules"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("torch")


def test_blocker_installation_is_reversible(_restore_import_state):
    """装上去必须能**摘下来**（否则污染同进程后续用例）。

    只做**结构性**验证（不真 import 重型模块）：摘掉后 `sys.meta_path` 里
    不再有我们的 finder，且对 torch 的 `find_spec` 不再抛错。
    """
    _probe.install_blocker()
    finders = [f for f in sys.meta_path if isinstance(f, _probe._CiEnvBlocker)]
    assert len(finders) == 1, "应恰好装上一个屏蔽器（重复安装须幂等）"
    _probe.install_blocker()
    assert len([f for f in sys.meta_path if isinstance(f, _probe._CiEnvBlocker)]) == 1, \
        "重复安装必须幂等（不得叠加多个 finder）"

    # 摘掉后：我们的 finder 不在链上（结构性验证）
    sys.meta_path[:] = [f for f in sys.meta_path
                        if not isinstance(f, _probe._CiEnvBlocker)]
    assert not [f for f in sys.meta_path if isinstance(f, _probe._CiEnvBlocker)]
    # 且 finder 对**未屏蔽**模块返回 None（不干扰正常 import）
    # ⚠ 不能拿被屏蔽模块试：它的 `find_spec` 按设计**就是抛 ModuleNotFoundError**。
    assert finders[0].find_spec("json") is None
    with pytest.raises(ModuleNotFoundError):
        finders[0].find_spec("torch")        # 被屏蔽者应抛（这才是它的职责）


def test_point_model_dir_to_empty_creates_dir_and_sets_env(monkeypatch):
    """模型目录必须改向到一个**真实存在的空目录**（模拟全新 clone：权重不入库）。

    ⚠ 该调用会写 `WME_MODEL_DIR` 环境变量（全局状态）⇒ 用 monkeypatch 兜住，
    否则后续用例（乃至别处的 config 解析）会读到被改过的路径（LESSONS 213）。
    """
    monkeypatch.delenv("WME_MODEL_DIR", raising=False)
    d = _probe.point_model_dir_to_empty()
    assert os.path.isdir(d), "改向的目录必须存在"
    assert os.environ.get("WME_MODEL_DIR") == d
    # 空目录 ⇒ 与真实 CI（权重未入库）语义一致
    assert not [f for f in os.listdir(d) if not f.startswith(".")], "该目录应为空"


def test_build_pytest_args_pins_ci_marker_subset_and_unique_basetemp():
    """参数必须钉住 CI 的 marker 子集，且 basetemp **唯一**（LESSONS 214：不得复用固定名）。"""
    args = _probe.build_pytest_args()
    assert "-m" in args
    assert args[args.index("-m") + 1] == "not torch_required and not network"
    bt = args[args.index("--basetemp") + 1]
    assert not os.path.basename(bt).startswith(".pytest_ciprobe_") is False  # 前缀正确
    assert str(os.getpid()) in os.path.basename(bt), "basetemp 应含 pid 以保证唯一"
    # 不得落在 .scratch/ 下（LESSONS 214③：会撞 clean_artifacts 的路径断言）
    parts = {p.lower() for p in os.path.normpath(bt).split(os.sep)}
    assert ".scratch" not in parts


def test_check_mode_is_side_effect_free_and_returns_zero(_restore_import_state, capsys):
    """`--check` 必须零副作用地返回 0（供 CI/人工快速自检）。

    注意：`--check` 内部会调 `install_blocker()`（属"验证屏蔽器可用"的一部分），
    故仍须用 `_restore_import_state` 兜住 —— 否则同样污染同进程后续用例。
    """
    before = dict(os.environ)
    assert _probe.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "自检通过" in out
    # 不得改动环境变量（零副作用）
    assert dict(os.environ) == before, "--check 不应产生环境副作用"


def test_list_blocked_mode_returns_zero(capsys):
    assert _probe.main(["--list-blocked"]) == 0
    out = capsys.readouterr().out
    for m in _probe.BLOCKED_MODULES:
        assert m in out


def test_ci_requirements_match_the_actual_workflow():
    """探针声明的 CI 清单必须与 `ci.yml` 实际安装的一致（防两处真值来源漂移）。"""
    wf = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for req in _probe.CI_REQUIREMENTS:
        assert os.path.isfile(_ROOT / req), f"清单不存在：{req}"
        assert req in wf, f"ci.yml 未引用 {req}——探针的假设与真实 CI 已经漂移"
