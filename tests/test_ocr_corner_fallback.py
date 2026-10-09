"""
角落低置信 OCR 回退守卫测试（阶段审计新增）

背景：真实图 q3_2/q4_2/q5_2 上，EasyOCR 能「框住」角落水印文字（检测几何正确），
却因文字低对比/字体风格识别失败、给出 ~0.00 置信度，被 DETECT_OCR_MIN_CONFIDENCE
滤掉，导致整段角落水印漏检（mask_recall=0.000）。

修复（`core/detector.py:_ocr_boxes_in_patch(accept_low_conf=True)`）：角落高置信路径
零产出、但 OCR 仍框到「类文本行」候选时，接受其作定位框——把「识别失败」与「定位」
解耦。仅作用于角落 patch（由 detect_corner_logo 传 accept_low_conf=True），且受
config.DETECT_OCR_CORNER_FALLBACK 可逆杠杆控制。

本文件用伪造 reader 注入低置信结果，验证开关与配置两条守卫，不依赖真实数据集。
"""
import numpy as np
import config
import core.detector as det


def _fake_reader(boxes_with_conf):
    """返回一个 readtext 返回固定结果的伪 reader。

    boxes_with_conf: list of (polygon_4pts, text, confidence)
    """
    class _R:
        def readtext(self, bgr):
            return boxes_with_conf
    return _R()


def test_corner_fallback_accepts_lowconf_textlike_when_enabled(monkeypatch):
    """accept_low_conf=True 且配置开启时，应接住低置信类文本行框。"""
    # conf=0.000（识别乱码）、几何为 240x60 文字条（宽高比 4，类文本行）
    reader = _fake_reader([([[10, 10], [250, 10], [250, 70], [10, 70]], "u刈a", 0.000)])
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: reader)
    monkeypatch.setattr(config, "DETECT_OCR_UPSCALE", False)  # 关闭上采样，坐标直通
    patch = np.zeros((380, 380, 3), dtype=np.uint8)

    # 开关关 → 必须丢弃
    off = det._ocr_boxes_in_patch(patch, 2340, 1140, accept_low_conf=False)
    assert off == [], "accept_low_conf=False 时必须丢弃低置信框"

    # 开关开 → 必须接受
    on = det._ocr_boxes_in_patch(patch, 2340, 1140, accept_low_conf=True)
    assert len(on) == 1, f"角落回退应接住低置信类文本行框，得到 {on}"
    x, y, w, h = on[0]
    assert (x, y) == (2350, 1150), on          # 平移到原图坐标
    assert (w, h) == (240, 60), on             # 几何保持


def test_corner_fallback_rejected_when_config_off(monkeypatch):
    """DETECT_OCR_CORNER_FALLBACK=False 时，即便 accept_low_conf=True 也丢弃。"""
    reader = _fake_reader([([[10, 10], [250, 10], [250, 70], [10, 70]], "u刈a", 0.000)])
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: reader)
    monkeypatch.setattr(config, "DETECT_OCR_UPSCALE", False)
    monkeypatch.setattr(config, "DETECT_OCR_CORNER_FALLBACK", False)
    patch = np.zeros((380, 380, 3), dtype=np.uint8)
    boxes = det._ocr_boxes_in_patch(patch, 0, 0, accept_low_conf=True)
    assert boxes == [], "DETECT_OCR_CORNER_FALLBACK=False 时即便 accept_low_conf=True 也应丢弃"


def test_corner_fallback_rejects_non_textlike(monkeypatch):
    """回退只接「类文本行」几何；极端宽高比（非文字）不应被接住。"""
    # 宽高比 50 的细长条（典型非文字，如细线/边框）
    reader = _fake_reader([([[10, 10], [510, 10], [510, 20], [10, 20]], "iii", 0.000)])
    monkeypatch.setattr(det, "_get_ocr_reader", lambda: reader)
    monkeypatch.setattr(config, "DETECT_OCR_UPSCALE", False)
    patch = np.zeros((380, 380, 3), dtype=np.uint8)
    boxes = det._ocr_boxes_in_patch(patch, 0, 0, accept_low_conf=True)
    assert boxes == [], "回退不应接住极端宽高比（非文字）候选"
