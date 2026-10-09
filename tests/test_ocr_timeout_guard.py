"""OCR 单图识别超时护栏的回归守卫测试（阶段审计新增）。

验证点（均针对生产函数 core.detector._ocr_readtext）：
  1. 快路径（timeout <= 0）：关闭护栏，直接走原生 reader.readtext，返回真实结果。
  2. 超时路径（timeout > 0 且 OCR 卡死）：daemon 线程 join(timeout) 按时返回，
     降级为 []，不挂死、不抛异常。
护栏覆盖的真实风险：某真实图触发 EasyOCR CPU 极慢路径，单 patch 可卡数分钟，
令整批门禁/检测挂起（实测 general 全量 918.8s）。超时后仅丢弃该 patch 的 OCR
结果、降级走板块/图标通路。
"""
import time

import numpy as np

from core import detector as D


class _FastReader:
    def readtext(self, bgr):
        return [([[0, 0], [10, 0], [10, 10], [0, 10]], "ok", 0.9)]


class _SlowReader:
    def readtext(self, bgr):
        time.sleep(5.0)  # 远超超时阈值，模拟极慢 OCR
        return [([[0, 0], [1, 0], [1, 1], [0, 1]], "x", 0.9)]


def test_ocr_readtext_fastpath_returns_results():
    """timeout <= 0 时关闭护栏，直接返回阅读器结果（原行为不变）。"""
    out = D._ocr_readtext(_FastReader(), np.zeros((10, 10, 3), np.uint8), 0.0)
    assert len(out) == 1
    assert out[0][1] == "ok"


def test_ocr_readtext_thread_timeout_returns_empty():
    """慢 reader 卡死 + 小超时：daemon 线程 join(timeout) 必须按时降级为 []。

    这是护栏成立的前提——join(timeout) 在 GIL 释放型调用（torch/cv2）上可靠返回，
    避免整批挂死。回归证明：不会卡满 5s，也不会抛异常。
    """
    start = time.time()
    out = D._ocr_readtext(_SlowReader(), np.zeros((10, 10, 3), np.uint8), 0.05)
    dt = time.time() - start
    assert out == [], "超时分支应降级为 []"
    assert dt < 2.0, "线程超时护栏失效：join(timeout) 未按时返回（耗时 %.1fs）" % dt
