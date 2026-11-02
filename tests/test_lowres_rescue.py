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

本测试锁定：① 默认关闭（精度契约不被破坏）+ **黑屏现象现场测量**（阶段 369：原来把
「某条组合掩码必须为 0」钉成常量，换字体就会假报回归 ⇒ 改成网格现场测量「仍存在完全漏检
且放行后都能救回」）；② 放行后黑屏被救回；③ 开关关闭时行为与
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
#: 阶段 67 标定的「默认路径完全黑屏」快照，用于 ②③（放行后能救回 / 并集不更差）。
#: ⚠ 阶段 369 重锚：这些组合落在**检出门槛的混沌区**（同一字体下 224 漏检、256 检出、
#: 288 检出、320 又漏检），旧写法把它当常量钉 `== 0`，于是「换字体」这类环境变化会
#: 变成产品回归的假信号。现在 ① 改为**当场测量**（见
#: `test_silent_miss_phenomenon_measured_not_pinned`），本表只作为 ②③ 的输入样本。
#: 字体固定成仓库内那份之后实测（seed=7，textured，默认路径掩码像素 / 放行后召回）：
#:   (256 png corner_logo) 2813 px / 1.0000 —— 已**不是**黑屏，仍留在表里喂 ②③
#:   (256 jpeg75 grid)         0 px / 0.6225
#:   (256 webp75 grid)         0 px / 0.5196
#:   (320 jpeg75 corner_logo)  0 px / 1.0000
_BLACKOUTS = (
    (256, "png", "textured", "corner_logo"),
    (256, "jpeg75", "textured", "grid"),
    (256, "webp75", "textured", "grid"),
    (320, "jpeg75", "textured", "corner_logo"),
)
#: ① 用来**现场测量**「是否还存在完全漏检的组合」的小网格（刻意小：CI 里也要跑）。
_MISS_GRID_SIZES = (160, 224, 256)
_MISS_GRID_CODECS = ("png", "jpeg75")
_MISS_GRID_CASES = ("corner_logo", "grid")
#: `test_rescue_side_zero_disables` 的样本：必须是**真黑屏**（否则「SIDE=0 不兜底 ⇒ 0 px」
#: 会被主路径自己检出而假红）。按值取，不靠下标——下标一改就静默换样本。
_SIDE_ZERO_SAMPLE = next(c for c in _BLACKOUTS if c[1] == "jpeg75" and c[3] == "grid")


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


def test_silent_miss_phenomenon_measured_not_pinned():
    """① 现象判据改成**当场测量**，不再钉「某条组合必须掩码 0 px」。

    为什么改（阶段 369 实测）：这些组合落在检出门槛的混沌区——同一张图、同一 seed，
    字体从 arialbd 换成 Linux 的 DejaVu，`256 png corner_logo` 就从 0 px 变成 2813 px，
    于是「本机装了哪个字体」会伪装成一次产品回归（CI 等价环境强制 DejaVu 跑整套 ⇒
    恰好只有这一条翻红，其余 3398 条绿）。

    本阶段真正要钉的是两件事，都与快照无关：
      · 默认路径**仍然存在**完全漏检的组合（兜底的前提没消失）；
      · 每一个这样的组合，放行兜底后召回必须 > 0（放行闸确实救得回来）。
    """
    misses = []
    for size in _MISS_GRID_SIZES:
        for codec in _MISS_GRID_CODECS:
            for case in _MISS_GRID_CASES:
                img, g, preset = _downscaled_case(size, codec, "textured", case)
                if np.count_nonzero(_det(img, preset, rescue=None)) == 0:
                    misses.append(((size, codec, case),
                                   metrics(_det(img, preset, rescue=True), g)["recall"]))
    assert misses, (
        "网格 %s×%s×%s 里默认路径**已无**完全漏检的组合 ⇒ 阶段 67 的兜底前提变了，"
        "要重估这个开关还有没有必要默认关闭"
        % (_MISS_GRID_SIZES, _MISS_GRID_CODECS, _MISS_GRID_CASES))
    unrecovered = [m for m in misses if m[1] <= 0.0]
    assert not unrecovered, (
        "这些组合默认漏检、放行兜底后仍漏检（兜底没救回来）：%s" % (unrecovered,))


@pytest.mark.parametrize("size,codec,kind,case", _BLACKOUTS)
def test_snapshot_samples_are_measurable(size, codec, kind, case):
    """② 快照样本仍能算出真值与掩码（不判黑屏与否——黑屏由 ① 现场测量负责）。"""
    img, g, preset = _downscaled_case(size, codec, kind, case)
    assert np.count_nonzero(g) > 0, "真值掩码为空 ⇒ 该用例失去意义（配对几何变了）"
    assert _det(img, preset, rescue=None).shape == g.shape



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
    """放行兜底后，快照用例必须检出（召回 > 0）。

    ⚠ 阶段 369：快照里 `256 png corner_logo` 在字体固定后已**不再黑屏**（默认路径就能检出
    2813 px），但它仍是合法的兜底输入样本，故保留；「哪些组合黑屏」由
    `test_silent_miss_phenomenon_measured_not_pinned` 现场测量，不靠这张表。
    """
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
    img, g, preset = _downscaled_case(*_SIDE_ZERO_SAMPLE)
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
