"""阶段 67 · 低分辨率兜底重检（部署级放行闸）的回归测试。

背景（docs/67-*）：输入被平台缩小 + 有损再编码后，水印的**绝对像素尺寸**缩水，而各
策略的判据都按标定尺度（640）设计——实测 320² JPEG 的角标 / 256² 的网格出现
**完全空掩码**（召回 0），界面只会显示「未检测到水印」，属静默漏检。逐策略归因确认
失败发生在策略内部（同图 PNG 下 logo 策略仍能返回 3390px/1 区域），不是共享面积门限。
修复：`DETECT_LOWRES_RESCUE` 开启时，把长边 < `DETECT_LOWRES_RESCUE_SIDE` 的输入提升到
该尺度再完整检测一次，与主结果**取并集**（并集恒 ≥ 两条通路）。

**为什么默认关闭**：兜底会把原生小图的纹理噪点一并放大，实测干净图四预设平均误报
256² textured 0.80% → 7.98%（默认预设 general 亦 0.0% → 4.66%），破坏阶段 57
「小图误报 ≤ 2%」的保证。按项目铁律「宁可漏检，不可滥杀」，该能力作为显式 opt-in 放行。

本测试锁定：① 默认关闭（精度契约不被破坏）；② 放行后黑屏被救回；③ 开关关闭时行为与
旧版逐位一致；④ 长边 ≥ 锚点的输入**逐位不变**（零回归）；⑤ 递归深度锁 1（性能护栏）；
⑥ 区域坐标映射正确；⑦ 目标尺度 0 = 关闭。
"""
import os
import sys

import cv2
import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
from scripts.detector_diag import BASE_KINDS, gt_mask, metrics  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402

_CLEAN_PRESETS = ("general", "ai_faint_tiled", "ai_grid", "ai_logo")
#: 实测（默认路径）会完全黑屏的「640 下采样 + 有损编码」用例，兜底必须全部救回。
_BLACKOUTS = (
    (256, "png", "textured", "corner_logo"),
    (256, "jpeg75", "textured", "grid"),
    (256, "webp75", "textured", "grid"),
    (320, "jpeg75", "textured", "corner_logo"),
)


def _encode(img: np.ndarray, codec: str) -> np.ndarray:
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    if codec == "png":
        ok, buf = cv2.imencode(".png", bgr)
    elif codec.startswith("jpeg"):
        ok, buf = cv2.imencode(".jpg", bgr,
                               [int(cv2.IMWRITE_JPEG_QUALITY), int(codec[4:])])
    else:
        ok, buf = cv2.imencode(".webp", bgr,
                               [int(cv2.IMWRITE_WEBP_QUALITY), int(codec[4:])])
    assert ok
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def _downscaled_case(size, codec, kind, case, seed=7):
    """640 原生带水印图 → 缩放到 size → 有损编码；真值取无损配对后同几何变换。"""
    base = BASE_KINDS[kind](seed, w=640, h=640)
    marked = CASES[case][0](base)
    gt = gt_mask(base, marked)
    img = _encode(cv2.resize(marked, (size, size), interpolation=cv2.INTER_AREA), codec)
    g = cv2.resize(gt, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
    return img, g, CASES[case][1]


def _det(img, preset, rescue=None):
    m = det.detect_watermark_mask(img, preset=preset, use_ocr=False,
                                 lowres_rescue=rescue).mask
    return m[:, :, 0] if m.ndim == 3 else m


# ---------------------------------------------------------------------------
# 1. 默认关闭：精度契约（阶段 57 的 2% 保证）不得被破坏
# ---------------------------------------------------------------------------

def test_rescue_is_disabled_by_default():
    """兜底默认关闭——它是放行闸，不是默认行为。"""
    assert config.DETECT_LOWRES_RESCUE is False
    assert config.DETECT_LOWRES_RESCUE_SIDE == 640


@pytest.mark.parametrize("size,codec,kind,case", _BLACKOUTS)
def test_default_path_keeps_blackout(size, codec, kind, case):
    """默认路径下这些用例确实空掩码——这是本阶段要暴露的静默漏检，不是测试缺陷。"""
    img, g, preset = _downscaled_case(size, codec, kind, case)
    mask = _det(img, preset, rescue=None)          # 走 config 默认（False）
    assert np.count_nonzero(mask) == 0, "默认路径行为变了（应为黑屏，由放行闸承接）"


@pytest.mark.slow
def test_default_path_small_image_fpr_within_red_line():
    """默认路径：原生小图干净误报必须回到阶段 57 的水平（≤2%，四预设平均）。"""
    for size in (256, 320):
        for kind in ("textured", "photo"):
            base = BASE_KINDS[kind](7, w=size, h=size)
            blank = np.zeros(base.shape[:2], np.uint8)
            fprs = [metrics(_det(base, p, rescue=None), blank)["fpr"]
                    for p in _CLEAN_PRESETS]
            assert float(np.mean(fprs)) <= 0.02, (
                f"{size}²/{kind} 默认路径干净误报 {np.mean(fprs)*100:.2f}% 超过 2%")


# ---------------------------------------------------------------------------
# 2. 放行后：黑屏被救回（阶段 67 核心收益）
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("size,codec,kind,case", _BLACKOUTS)
def test_rescue_recovers_blackout(size, codec, kind, case):
    """放行兜底后，此前完全空掩码的用例必须检出（召回 > 0）。"""
    img, g, preset = _downscaled_case(size, codec, kind, case)
    assert metrics(_det(img, preset, rescue=True), g)["recall"] > 0.0


@pytest.mark.slow
def test_rescue_is_union_of_both_arms():
    """放行结果 = 主检测 ∪ 兜底检测：召回恒 ≥ 两条通路各自的结果。"""
    for size, codec, kind, case in _BLACKOUTS:
        img, g, preset = _downscaled_case(size, codec, kind, case)
        r_off = metrics(_det(img, preset, rescue=False), g)["recall"]
        r_on = metrics(_det(img, preset, rescue=True), g)["recall"]
        assert r_on >= r_off - 1e-9, f"{size}²/{codec}/{case} 并集反而更差"


# ---------------------------------------------------------------------------
# 3. 零回归：≥ 锚点的输入逐位不变
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [640, 960])
def test_large_inputs_are_bit_identical(size):
    """长边 ≥ 锚点的输入不触发兜底，掩码必须与关闭时逐位相同。"""
    base = BASE_KINDS["textured"](7, w=size, h=size)
    marked = CASES["grid"][0](base)
    a = _det(marked, "ai_grid", rescue=False)
    b = _det(marked, "ai_grid", rescue=True)
    assert np.array_equal(a, b)


# ---------------------------------------------------------------------------
# 4. 递归深度锁 1（性能护栏）与开关语义
# ---------------------------------------------------------------------------

def test_rescue_depth_is_one(monkeypatch):
    """兜底只允许一层：小图恰好调用 2 次（主 + 1 次兜底），不得递归放大。"""
    calls = {"n": 0}
    real = det.detect_watermark_mask

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(det, "detect_watermark_mask", counting)
    img = BASE_KINDS["textured"](7, w=200, h=200)
    counting(img, preset="general", use_ocr=False, lowres_rescue=True)
    assert calls["n"] == 2, f"兜底递归层数异常，调用 {calls['n']} 次"


def test_rescue_side_zero_disables(monkeypatch):
    """目标尺度设为 0 时，即使开关为真也不兜底。"""
    monkeypatch.setattr(config, "DETECT_LOWRES_RESCUE_SIDE", 0)
    img, g, preset = _downscaled_case(*_BLACKOUTS[1])
    assert np.count_nonzero(_det(img, preset, rescue=True)) == 0


# ---------------------------------------------------------------------------
# 5. 区域坐标映射
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_rescued_regions_within_bounds():
    """兜底区域坐标需缩回原图尺度，必须落在图像范围内且面积为正。"""
    base = BASE_KINDS["textured"](7, w=640, h=640)
    marked = CASES["corner_logo"][0](base)
    small = cv2.resize(marked, (320, 320), interpolation=cv2.INTER_AREA)
    res = det.detect_watermark_mask(small, preset="ai_logo", use_ocr=False,
                                    lowres_rescue=True)
    assert res.regions, "兜底后应有区域"
    h, w = small.shape[:2]
    for r in res.regions:
        assert 0 <= r.x < w and 0 <= r.y < h, f"区域坐标越界：{r}"
        assert r.w >= 1 and r.h >= 1, f"区域尺寸非法：{r}"
