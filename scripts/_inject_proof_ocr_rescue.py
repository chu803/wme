"""P0-4 注入式反证：证明守卫测试能捕获「救援逻辑被回退」的缺陷。

做法：把 `_ocr_box_rescue_mask` 就地 neutralized 成「恒返空掩码」（模拟救援被删除的
回退态），复刻守卫测试 `test_ocr_box_trust_rescues_empty_refine` 的 same 输入条件，
断言 OCR 框被丢弃（即缺陷被复现）。若此脚本断言成立 ⇒ 真实守卫测试在回退态必 FAIL
⇒ 守卫确实能捕获声称缺陷。
"""
import sys
import numpy as np
import core.detector as det
import config

from scripts.console_encoding import force_utf8_stdio

force_utf8_stdio()  # 本脚本 print 中文/⇒，GBK 管道下会 UnicodeEncodeError（test_scripts_console_encoding 守卫）

# 1) 模拟「救援被删除」的回退态：rescue 恒返空
det._ocr_box_rescue_mask = lambda regions, evidence, h, w, pad: np.zeros((h, w), np.uint8)

# 2) 复刻守卫测试输入条件
img = np.full((128, 128, 3), 128, dtype=np.uint8)
det._ocr_available = lambda: True
det._ocr_boxes_in_patch = lambda *a, **k: [(80, 80, 30, 16)]
det._refine_regions_to_mask = lambda regions, ev, h, w, pad: (np.zeros((h, w), np.uint8), len(regions))
det._evidence_is_degenerate = lambda ev: False
config.DETECT_OCR_BOX_TRUST = True

res = det.detect_watermark_mask(img, use_ocr=True)
kept = [r for r in res.regions if r.kind == det.KIND_OCR_TEXT]
assert not kept, (
    f"[INJECT] 回退态下守卫应复现缺陷（OCR 框被丢），但竟保留了 {kept} —— 守卫无效"
)
assert int(np.asarray(res.mask).max()) == 0, "[INJECT] 回退态下掩码应仍为空"
print("[INJECT] 反例复现成功：救援被删除时 OCR 框静默漏检 ⇒ 守卫测试将 FAIL（捕获缺陷）")
sys.exit(0)
