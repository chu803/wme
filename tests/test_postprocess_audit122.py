# -*- coding: utf-8 -*-
"""
阶段 122 审计守卫：services/postprocess_service 的「环境无关性」与声明一致性。

审计发现（阶段 122）：
  F1（真缺陷）：模块 `__main__` 自检**在装 torch 的环境上必然失败**——
     · 早期断言 `assert improved.shape == raw.shape` 用的是**默认调用**，而
       `postprocess` 在 `real_superres=None` 时读 `config.REAL_SUPERRES_ENABLED`；
       本机 `config.user_overrides.json` 正是 `REAL_SUPERRES_ENABLED=true / scale=2`
       ⇒ 每条默认调用都把画布放大 2× ⇒ 断言崩（实测 `AssertionError` @ line 589）。
     · 用例7 硬编码「真实超分后端不可用时不应改变尺寸」，而装了 torch 时 esrgan 后端
       可用（缺权重则退化为纯双三次放大）⇒ 该断言同样必崩。
     修复：自检开头显式关闭 `config.REAL_SUPERRES_ENABLED`（环境无关）；用例7 改为
     「要么同尺寸、要么恰好 4× 放大」并打印实际走向。
  F2（声明 ≠ 实现）：docstring 写「后端不可用（缺 torch/权重）时自动回退轻量超分（同分辨率）」，
     但阶段 115 起 `esrgan` 在**缺权重**时退化为**纯双三次放大**（仍改变画布）⇒ 已勘误。
  F3（文档缺口）：`real_superres` 需**原图+掩码**才执行（设计如此，见 docs/32），
     旧 docstring 未写 ⇒ 缺二者时开关静默无效。已补注。
"""

import os
import subprocess
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.superres import SuperResBackend  # noqa: E402
from services import postprocess_service as pp  # noqa: E402

_SELFTEST = os.path.join(_ROOT, "services", "postprocess_service.py")


def _run_selftest(extra_env: "dict | None" = None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, _SELFTEST], cwd=_ROOT,
                          capture_output=True, text=True, timeout=300, env=env)


# ------------------------------------------------------------------------------
# F1 守卫：自检必须**在任何环境下**通过（这是能永久抓住 F1 的那条判据）
# ------------------------------------------------------------------------------
def test_module_selftest_passes_in_this_env():
    res = _run_selftest()
    assert res.returncode == 0, f"模块自检未通过：\n{res.stdout}\n{res.stderr}"
    assert "自检全部通过" in res.stdout


def test_module_selftest_independent_of_real_superres_env():
    """把真实超分经**环境变量**强制打开，自检仍须通过（证明它与环境解耦）。"""
    res = _run_selftest({"WME_REAL_SUPERRES_ENABLED": "1"})
    assert res.returncode == 0, (
        f"打开 WME_REAL_SUPERRES_ENABLED 后自检失败（自检未与环境解耦）：\n{res.stdout}"
    )


# ------------------------------------------------------------------------------
# F2 守卫：声明与实现一致（缺权重 = 双三次放大，不是「回退同分辨率」）
# ------------------------------------------------------------------------------
def test_docstring_documents_no_weight_bicubic_upscale():
    doc = pp.postprocess.__doc__ or ""
    assert "缺训练权重" in doc and "双三次" in doc, "docstring 未如实说明「缺权重→双三次放大」"
    # 旧错误表述必须已被勘误掉
    assert "后端不可用（缺 torch/权重）时自动回退轻量超分（同分辨率）" not in doc


def test_light_and_real_superres_are_mutually_exclusive():
    """docstring 承诺「真实超分与轻量超分互斥（不叠加）」——用假后端钉住尺寸一致性。"""
    class _Fake(SuperResBackend):
        name = "fake"

        def _infer(self, arr, scale):
            import cv2
            h, w = arr.shape[:2]
            return cv2.resize(arr, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)

    raw = np.full((40, 50, 3), 120, np.uint8)
    orig = raw.copy()
    mask = np.zeros((40, 50), np.uint8)
    mask[10:25, 10:25] = 255
    import cv2
    cv2.rectangle(mask, (10, 10), (25, 25), 255, -1)

    real_only = pp.postprocess(raw, original_rgb=orig, mask=mask,
                               real_superres=True, real_scale=2, real_backend=_Fake())
    both = pp.postprocess(raw, original_rgb=orig, mask=mask,
                          real_superres=True, real_scale=2, real_backend=_Fake(),
                          superres=True, superres_amount=0.8)
    assert real_only.shape == (80, 100, 3)
    assert both.shape == real_only.shape, "真实/轻量超分不应叠加出不同尺寸"


# ------------------------------------------------------------------------------
# F3 守卫：real_superres 需原图+掩码（设计如此，docs/32）——如实钉住
# ------------------------------------------------------------------------------
def test_real_superres_needs_geom_and_is_documented():
    class _Fake(SuperResBackend):
        name = "fake"

        def _infer(self, arr, scale):
            import cv2
            h, w = arr.shape[:2]
            return cv2.resize(arr, (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)

    raw = np.full((40, 50, 3), 120, np.uint8)
    out = pp.postprocess(raw, original_rgb=None, mask=None,
                         real_superres=True, real_scale=2, real_backend=_Fake())
    assert out.shape == raw.shape, "缺原图/掩码时 real_superres 静默无效（设计行为）"
    assert "原图" in (pp.postprocess.__doc__ or "")


def test_explicit_real_off_keeps_size_unchanged():
    """显式 real_superres=False ⇒ 无论环境如何，尺寸都不变。"""
    raw = np.full((40, 50, 3), 120, np.uint8)
    mask = np.zeros((40, 50), np.uint8)
    mask[10:25, 10:25] = 255
    out = pp.postprocess(raw, original_rgb=raw, mask=mask, real_superres=False)
    assert out.shape == raw.shape
