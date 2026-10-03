"""阶段 194（A4 准备）· ai_gradient 证伪脚本的确定性自测。

不依赖真实图：用合成图验证脚本逻辑——
  ① 强线性渐变图 → 渐变通道（force_gradient=True）应命中 KIND_GRADIENT；
  ② 纯噪声图     → 不应被误判为渐变；
  ③ 同一张渐变图，force_gradient=False（即默认关）会**假阴性零触发** ——
     这正是脚本文档强调的陷阱，必须用 importlib 强制开启。

设计取舍：用 importlib 直接加载脚本文件，规避 scripts/ 是否含 __init__.py 的包结构差异。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 直接加载脚本模块（不依赖包结构）
_SPEC = importlib.util.spec_from_file_location(
    "a4_gradient_proof", str(_ROOT / "scripts" / "a4_gradient_proof.py")
)
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)
prove_image = _mod.prove_image


def _make_linear_gradient(path: str, size: int = 256) -> str:
    """每列从 0→255 的灰度梯度（R 升、B 降，制造青→品红的低频平面）。"""
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    cols = np.linspace(0, 255, size, dtype=np.uint8)
    arr[:, :, 0] = cols[None, :]
    arr[:, :, 2] = (255 - cols)[None, :]
    Image.fromarray(arr).save(path)
    return path


def _make_noise(path: str, size: int = 256, seed: int = 0) -> str:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path)
    return path


def test_linear_gradient_is_detected_as_gradient(tmp_path):
    p = _make_linear_gradient(str(tmp_path / "grad.png"))
    r = prove_image(p, force_gradient=True)
    assert r["gradient_triggered"] is True
    assert r["gradient_region_count"] >= 1
    assert r["coverage_ratio"] > 0.9  # 渐变覆盖整图


def test_noise_is_not_detected_as_gradient(tmp_path):
    p = _make_noise(str(tmp_path / "noise.png"))
    r = prove_image(p, force_gradient=True)
    assert r["gradient_triggered"] is False
    assert r["gradient_region_count"] == 0


def test_default_off_would_falsely_report_zero(tmp_path):
    """文档铁律的反向钉死：默认 DETECT_GRADIENT_ENABLED=False，
    若不强制开启，连合成强渐变都会假阴性零触发 —— 正是脚本要规避的陷阱。"""
    p = _make_linear_gradient(str(tmp_path / "grad.png"))
    on = prove_image(p, force_gradient=True)
    off = prove_image(p, force_gradient=False)
    assert on["gradient_triggered"] is True
    assert off["gradient_triggered"] is False
