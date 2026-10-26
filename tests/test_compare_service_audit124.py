# -*- coding: utf-8 -*-
"""阶段 124 真值审计守卫：services.compare_service 的 upscaled 语义缺陷。

F1（语义错）：summarize_pair 用「raw结果尺寸 != 对齐后尺寸」判定 upscaled，
    当结果比原图**小**时也会误报 True（实则未放大）。文档明写「结果是否被放大」。
修复：upscaled = (raw结果尺寸 > 原图原始尺寸)，基于两者原始尺寸比较。

判据：行为级（不依赖源码字符串），并附「子进程实跑模块自检」守卫（与阶段 122 同构，
     普通单测不会执行 __main__，而模块自检含用例 6 直接钉住 F1）。
"""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.image_utils import save_image
import services.compare_service as cs


def _pair(tmp_path, orig=(120, 160), result=(60, 80)):
    src = os.path.join(str(tmp_path), "原图.png")
    res = os.path.join(str(tmp_path), "结果.png")
    base = np.full((orig[0], orig[1], 3), 90, dtype=np.uint8)
    marked = base.copy()
    marked[20:40, 30:90] = 240
    save_image(src, marked, fmt="PNG")
    small = np.full((result[0], result[1], 3), 90, dtype=np.uint8)
    save_image(res, small, fmt="PNG")
    return src, res


def test_f1_smaller_result_not_upscaled(tmp_path):
    """结果比原图小 → upscaled 必须为 False（F1 修复核心）。"""
    src, res = _pair(tmp_path, orig=(120, 160), result=(60, 80))
    s = cs.summarize_pair(src, res)
    assert s is not None
    assert s["upscaled"] is False, "结果更小却误报放大：%s" % s
    assert s["result_width"] == 80 and s["result_height"] == 60
    assert s["width"] == 160 and s["height"] == 120


def test_f1_equal_size_not_upscaled(tmp_path):
    """结果与原图等长等宽 → upscaled False。"""
    src, res = _pair(tmp_path, orig=(120, 160), result=(120, 160))
    s = cs.summarize_pair(src, res)
    assert s["upscaled"] is False


def test_regression_bigger_result_is_upscaled(tmp_path):
    """结果比原图大 → upscaled 必须仍为 True（回归既有行为）。"""
    src, res = _pair(tmp_path, orig=(120, 160), result=(240, 320))
    s = cs.summarize_pair(src, res)
    assert s is not None
    assert s["upscaled"] is True
    assert s["result_width"] == 320 and s["result_height"] == 240


# ⚠ 阶段 191：本用例给子进程传 `timeout=300`，与 pytest.ini 的全局 `--timeout=300`
#   相等 ⇒ 存在"谁先到点"的竞态。此处显式抬高外层（不变式见 tests/test_timeout_contract.py）。
@pytest.mark.timeout(420)
def test_module_selfcheck_subprocess():
    """子进程实跑模块自检（含用例 6 钉住 F1），断言退出码 0。"""
    r = subprocess.run(
        [sys.executable, "services/compare_service.py"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    assert r.returncode == 0, (
        f"compare_service 自检失败（退出码 {r.returncode}）\n"
        f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"
    )
