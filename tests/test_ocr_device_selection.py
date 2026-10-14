#!/usr/bin/env python3
# ==============================================================================
#  阶段 220 · OCR 设备选择的守卫（「跟随 DEVICE_MODE」+ 三重安全回退）
# ------------------------------------------------------------------------------
#  背景
#  ----
#  `core/detector._get_ocr_reader()` 曾把 `easyocr.Reader(..., gpu=False)` **硬编码**
#  为 CPU，注释理由「与本项目默认 CPU 一致」。**该前提早已不成立**：`DEVICE_MODE=auto`
#  下 LaMa 已在 cuda:0 上跑 ⇒ 同一条流水线里「LaMa 吃 GPU、OCR 纯 CPU」，
#  实测 OCR 段占全链路 13.9s（阶段 217/218 归因）。
#  阶段 219 实测 GPU 化：加速 11~13×，**包围盒逐位相同**（掩码不受影响）。
#
#  本文件守什么
#  ------------
#  ① **不硬编码设备**：必须走 `core.device_manager.select_device()`（`auto` 语义），
#     而不是再写一次 `gpu=True/False`（否则「一处不一致」会变成「两处」）。
#  ② **三重安全回退**：配置关 → CPU；探测抛异常 → CPU；GPU 初始化失败 → CPU。
#     **任何失败都只降级、绝不中断检测**（OCR 是可选增强通路）。
#  ③ **不用 select_device("cuda")**：那是「强制 CUDA」语义，无卡时**抛 RuntimeError**，
#     会让整条检测挂掉。
#  ④ **有配置开关**且默认开启跟随（`DETECT_OCR_USE_GPU`，env 可强制关闭）。
#  ⑤ 旧的无 GPU 环境行为**保持不变**（回归保护）。
# ==============================================================================
from __future__ import annotations

import inspect
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from core import detector as det  # noqa: E402
from tests._src_assert import function_body_code  # noqa: E402

_DETECTOR_PY = os.path.join(_ROOT, "core", "detector.py")
_CONFIG_PY = os.path.join(_ROOT, "config.py")


# ==============================================================================
# ① 不硬编码设备
# ==============================================================================
def test_reader_factory_does_not_hardcode_a_single_device():
    """`_get_ocr_reader` 不得无条件写死 `gpu=False`（那正是阶段 220 修掉的缺陷）。"""
    src = inspect.getsource(det._get_ocr_reader)
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    # 允许出现 gpu=True（GPU 分支）与 gpu=False（CPU 分支），但必须**由条件驱动**
    assert "if want_gpu:" in code, (
        "设备选择必须由条件驱动（有 GPU 分支 / CPU 分支），而非单一硬编码")
    assert "DETECT_OCR_USE_GPU" in src, "必须读配置开关 DETECT_OCR_USE_GPU"


def test_reader_factory_uses_project_device_manager():
    """必须复用 `core.device_manager.select_device()`，不自己再造一套探测逻辑。"""
    src = inspect.getsource(det._get_ocr_reader)
    assert "select_device" in src, (
        "应复用 core.device_manager.select_device()（它已含显存检查与 auto 语义）")
    assert "from core.device_manager import select_device" in src, \
        "必须走公共设备选择入口（避免出现第二套不一致的探测逻辑）"


def test_never_uses_forced_cuda_mode():
    """**不得**用 `select_device("cuda")`：那是强制语义，无卡时会抛 RuntimeError。

    ⚠ 两个踩坑点（本例都撞过）：
    ① 断言必须作用在**剥离 docstring 后的代码**上——docstring 里正是**引用**
       `select_device("cuda")` 来解释「为什么不用它」，朴素 `in src` 会假失败。
    ② 判据须精确到「`select_device` **带了 cuda 实参**」，不能写成「代码里不出现
       `cuda`」——因为 `kind == "cuda"` 是**合法且必要**的设备判定。
    """
    body = function_body_code(det._get_ocr_reader)
    # 精确判据：select_device 的调用不得带 "cuda"/'cuda' 实参
    bad = re.findall(r"select_device\(\s*[\"']cuda[\"']", body)
    assert not bad, (
        "OCR 是可选增强通路，绝不能用 select_device('cuda') 强制语义"
        f"——无卡时会抛异常并中断检测。实际命中：{bad}")


# ==============================================================================
# ② 三重安全回退
# ==============================================================================
def test_has_three_fallback_levels():
    """三处降级点必须都在：① 配置关 ② 探测异常 ③ GPU 初始化失败。"""
    src = inspect.getsource(det._get_ocr_reader)
    # ① 配置开关
    assert "DETECT_OCR_USE_GPU" in src, "缺降级①：配置关闭"
    # ② 探测异常 → 降级
    assert src.count("except") >= 3, (
        "降级②（探测失败）与 ③（GPU 初始化失败）需各自的 except，"
        f"当前 except 数={src.count('except')}")
    assert "回退 CPU" in src or "回退CPU" in src, "降级路径须有明确日志（便于排查）"


def test_gpu_failure_still_yields_a_usable_reader(monkeypatch):
    """**注入式反例**：GPU 初始化抛异常时，必须回退出可用的 CPU reader（不返回 None）。

    ⚠ 反例要**用单一 fake 类**同时「记录 + 按需抛错」。此前用两层 patch
    （先替掉 `Reader` 类、再替掉其 `__init__`）会互相覆盖，导致调用序列记录失真
    （实测记录到 `[False]` 而非 `[True, False]`）——**探针 bug 会被误读成产品缺陷**
    （LESSONS 233）。
    """
    pytest.importorskip("easyocr")  # easyocr 是可选依赖：CI(requirements-min) 不含，缺失则跳过本用例
    import easyocr

    calls = []

    class _FakeReader:
        """gpu=True 时抛错（模拟显存不足/算子不兼容），gpu=False 时正常构造。"""

        def __init__(self, langs, gpu=False, verbose=False):
            calls.append(bool(gpu))
            if gpu:
                raise RuntimeError("模拟显存不足 / 算子不兼容")
            self.device = "cpu"

        def readtext(self, img):
            return []

    monkeypatch.setattr(easyocr, "Reader", _FakeReader)
    monkeypatch.setattr(det, "_OCR_READER", None, raising=False)
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(config, "DETECT_OCR_USE_GPU", True)

    class _H:
        class info:
            kind = "cuda"
    import core.device_manager as dm
    monkeypatch.setattr(dm, "select_device", lambda *a, **k: _H())

    reader = det._get_ocr_reader()
    assert reader is not None, "GPU 初始化失败时必须回退出 CPU reader，而不是 None"
    assert getattr(reader, "device", "?") == "cpu", "应已回退到 CPU"
    assert calls == [True, False], (
        f"应先试 GPU 再退 CPU（顺序即语义），实际调用序列={calls}")


def test_no_device_env_never_breaks_detection(monkeypatch):
    """**注入式反例**：设备探测抛异常时必须走 CPU，且不返回 None。"""
    pytest.importorskip("easyocr")  # easyocr 是可选依赖：CI(requirements-min) 不含，缺失则跳过本用例
    import easyocr

    class _FakeReader:
        def __init__(self, langs, gpu=False, verbose=False):
            self.device = "cuda" if gpu else "cpu"

        def readtext(self, img):
            return []

    monkeypatch.setattr(easyocr, "Reader", _FakeReader)
    monkeypatch.setattr(det, "_OCR_READER", None, raising=False)
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(config, "DETECT_OCR_USE_GPU", True)
    import core.device_manager as dm

    def _boom(*a, **k):
        raise RuntimeError("模拟设备探测失败")

    monkeypatch.setattr(dm, "select_device", _boom)
    reader = det._get_ocr_reader()
    assert reader is not None, "探测失败必须降级到 CPU reader，而不是中断检测"
    assert getattr(reader, "device", "?") == "cpu"


# ==============================================================================
# ③④ 配置开关
# ==============================================================================
def test_config_switch_exists_and_defaults_to_follow_device():
    assert hasattr(config, "DETECT_OCR_USE_GPU"), "缺少 DETECT_OCR_USE_GPU 配置项"
    assert config.DETECT_OCR_USE_GPU is True, (
        "默认应开启跟随（OCR 占全链路 13.9s，GPU 化是唯一不牺牲召回的提速路径）")


def test_config_switch_is_env_overridable():
    """必须走 `_env_bool`（项目铁律：可逆杠杆用 env，不改代码）。"""
    with open(_CONFIG_PY, encoding="utf-8") as f:
        src = f.read()
    line = next((ln for ln in src.splitlines()
                 if ln.strip().startswith("DETECT_OCR_USE_GPU")), "")
    assert "_env_bool" in line, f"必须用 _env_bool 以支持 WME_ 环境变量覆盖，实际：{line}"
    assert '"DETECT_OCR_USE_GPU"' in line, "env 变量名须与配置名一致（WME_DETECT_OCR_USE_GPU）"


def test_config_false_forces_cpu(monkeypatch):
    """配置为 False 时**不得**尝试 GPU（显式声明，不受设备探测影响）。"""
    pytest.importorskip("easyocr")  # easyocr 是可选依赖：CI(requirements-min) 不含，缺失则跳过本用例
    import easyocr
    seen = []

    class _FakeReader:
        def __init__(self, langs, gpu=False, verbose=False):
            seen.append(bool(gpu))
            self.device = "cuda" if gpu else "cpu"

        def readtext(self, img):
            return []

    monkeypatch.setattr(easyocr, "Reader", _FakeReader)
    monkeypatch.setattr(det, "_OCR_READER", None, raising=False)
    monkeypatch.setattr(det, "_ocr_available", lambda: True)
    monkeypatch.setattr(config, "DETECT_OCR_USE_GPU", False)
    reader = det._get_ocr_reader()
    assert seen == [False], f"配置为 False 时只应构造 CPU reader，实际={seen}"
    assert getattr(reader, "device", "?") == "cpu"


# ==============================================================================
# ⑤ 源码级防复发
# ==============================================================================
def test_detector_source_has_bare_hardcoded_gpu_false():
    """源码级守卫：设备选择必须**由条件驱动**，不得退回「无条件构造 CPU reader」。

    ⚠ 判定改为**正面判据**（必须存在 `gpu=True` 的条件分支），而不是
    「文本里不能出现 `gpu=False`」——因为 `gpu=False` 在**回退分支里是合法的**
    （GPU 初始化失败后正是要退回 CPU），负面判据会误伤正确实现（实测踩到）。

    纯功能测试看不出「设备选错」——它在有 GPU 的机器上照样能跑、结果照样对，
    只是慢。凡**性能**回退，功能守卫天然无感（183 有守卫≠有覆盖），故须源码级断言。
    """
    body = function_body_code(det._get_ocr_reader)

    # 正面判据：存在「条件分支下构造 gpu=True 的 reader」
    assert "gpu=True" in body, (
        "必须存在「探测到 GPU 就用 GPU」的条件分支——否则 OCR 又退回纯 CPU"
        f"（性能回退，功能测试无感）。实际代码：{body}")

    # 条件必须真的驱动分支，而不是恒真
    assert "if want_gpu:" in body, (
        "GPU 分支必须由 `if want_gpu:` 这类条件驱动（want_gpu 来自配置 + 设备探测）")

    # 回退分支的 gpu=False 只能出现在 except / else 之下
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        if "gpu=False" not in ln:
            continue
        ctx = [x for x in lines[:i] if x.startswith(("if ", "else", "except"))]
        assert ctx, f"出现无条件的 gpu=False 构造（性能回退旧写法）：{ln}"
        assert ctx[-1].startswith("else") or ctx[-1].startswith("except"), (
            f"gpu=False 只能作为回退分支（else / except 之下），实际上下文={ctx[-1]}")
