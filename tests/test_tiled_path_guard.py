"""阶段 104：平铺增强通路（detect_tiled_translucent）连通域守卫的契约与反例。

背景（阶段 104 归因，探针 %TEMP%/s104_attrib.py / s104_merge.py / s104_synth.py
                      / s104_guard_iso.py / s104_iso_b.py）
--------------------------------------------------------------------------------
基础通路 detect_translucent 在阶段 82/84 加了两道**连通域级**守卫
（自然光影 `_suppress_natural_light` / 纹理稠密 `_suppress_texture_like`），
而平铺增强通路重新取了一遍候选却**没有接**这两道守卫 —— 同一批被基础通路
已经否掉的像素从这条通路重新进入并集。实测（真实照片，并集口径）：

    faint_tiled 预设  面积 9.62% → 2.61%，干净底图纯误报 3.21% → 0.87%
    ai_grid     预设  面积 10.19% → 3.20%（收紧本通路贡献了 65% 的过检削减）

**最终只接「纹理稠密」一道**（单守卫隔离实验，docs/104 §8）：真实照片削减率
只自然光影 26.0% / 只纹理稠密 **72.9%** / 两道全接 75.4% —— 纹理守卫单独拿到
95%+ 的收益；而接上自然光影守卫会把「纹理 + 极淡平铺」整类抹零、把
「渐变 + 网格」砍掉 28% 召回（合成 6 场景 IoU 合计 2.319 → 1.882）。

本文件锁定五件事：
  A. 开关真的被读到（不是扩散死代码），且是逐像素「子集」关系；
  B. 收紧显著提升纹理底图上的精确率，且**不损伤合并口径的召回**；
  C. **反例**：网格通路（detect_grid_watermark）必须**不受**本开关影响 ——
     因为它精确率已是 1.000，套守卫是纯损失（合成 0.99 → 0.68），
     若后人再把它接上，本用例会失败并强制重新推导这份取舍；
  D. **反例**：周期性（ACF 自相关峰）**不能**作为本问题的判据 ——
     真实 640px 照片上连**干净图**都 100% 过阈（实测 min 0.116 / 均值 0.268），
     干净的周期纹理同样轻松过阈。若后人想用 ACF 收紧本通路，本用例会失败。
  E. **反例**：低分辨率兜底臂（docs/67）必须**豁免**本守卫 ——
     兜底臂测的是「放大后的小图」，残差被重采样平滑与有损块效应主导，
     正是本守卫的抑制带；若后人把豁免去掉，256²/webp75/textured/grid
     会重新黑屏（`tests/test_lowres_rescue.py::test_rescue_recovers_blackout` 失败）。
     调用契约（阶段 107 修正）：**主臂传 `None`（交回 config）、兜底臂传 `False`**。
     此前主臂写死 `True`，`config.DETECT_TILED_PATH_GUARD` 被实参遮蔽 ⇒
     开关与「注入式反例」双双失效（详见用例内注释与 `docs/107`）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.detector as det  # noqa: E402
from scripts.detector_diag import BASE_KINDS, gt_mask, metrics  # noqa: E402
from scripts.make_demo_assets import CASES  # noqa: E402

_MAR = config.DETECT_MIN_AREA_RATIO
_XAR = config.DETECT_MAX_AREA_RATIO
_SENS = {"faint_tiled": 0.72, "grid": 0.60}


def _tiled(marked, sens, guarded):
    """在指定开关状态下调用平铺增强通路，返回 0/255 掩码。"""
    prev = config.DETECT_TILED_PATH_GUARD
    config.DETECT_TILED_PATH_GUARD = guarded
    try:
        m, _ = det.detect_tiled_translucent(
            marked, sens, marked.shape[0] * marked.shape[1], _MAR, _XAR
        )
    finally:
        config.DETECT_TILED_PATH_GUARD = prev
    return m


@pytest.fixture
def case_grid_textured():
    base = BASE_KINDS["textured"](7)
    marked = CASES["grid"][0](base)
    return base, marked, gt_mask(base, marked)


@pytest.fixture
def case_grid_gradient():
    base = BASE_KINDS["gradient"](7)
    marked = CASES["grid"][0](base)
    return base, marked, gt_mask(base, marked)


# ---------------------------------------------------------------------------
# A. 开关是活的 + 子集关系
# ---------------------------------------------------------------------------

def test_A_switch_is_live(case_grid_textured):
    """关/开必须产出**不同**的掩码 —— 证明开关被真正读取（防扩散死代码）。"""
    _, marked, _ = case_grid_textured
    off = _tiled(marked, _SENS["grid"], False)
    on = _tiled(marked, _SENS["grid"], True)
    assert not np.array_equal(off, on), "开关未生效（可能是扩散死代码）"
    assert np.count_nonzero(on) < np.count_nonzero(off), "开启守卫后面积应更小"


def test_A_guarded_is_pixel_subset(case_grid_textured):
    """守卫只做「剔除」，不得新增任何像素（逐像素子集）。"""
    _, marked, _ = case_grid_textured
    off = _tiled(marked, _SENS["grid"], False) > 0
    on = _tiled(marked, _SENS["grid"], True) > 0
    assert not np.any(on & ~off), "守卫新增了原候选之外的像素（越权）"


# ---------------------------------------------------------------------------
# B. 精确率提升 + 合并口径召回不损伤
# ---------------------------------------------------------------------------

def test_B_precision_rises_on_texture(case_grid_textured):
    """纹理底图：本通路精确率显著提升（实测 51.8% → 82.6%[纹理守卫] / 98.7%[两道]）。"""
    _, marked, gt = case_grid_textured
    off = metrics(_tiled(marked, _SENS["grid"], False), gt)["precision"]
    on = metrics(_tiled(marked, _SENS["grid"], True), gt)["precision"]
    assert on > off + 0.10, f"精确率未显著提升：开 {on:.3f} vs 关 {off:.3f}"


def test_B_merged_recall_kept_on_gradient(case_grid_gradient):
    """合并口径（真实调用链）在 gradient 底图上召回不得受损 —— 守卫的代价上限。

    门限 0.97 取自实测 0.9974（阶段 104 前后逐位一致），留 2.7 个点余量：
    本用例的目的是「拦住把召回打穿」，不是把当前数字钉死。
    """
    _, marked, gt = case_grid_gradient
    res = det.detect_watermark_mask(marked, preset="ai_grid", use_ocr=False)
    mm = res.mask[:, :, 0] if res.mask.ndim == 3 else res.mask
    assert metrics(mm, gt)["recall"] >= 0.97, "合并口径召回被打穿"


# ---------------------------------------------------------------------------
# C. 反例：网格通路必须**不受**本开关影响
# ---------------------------------------------------------------------------

def test_C_grid_path_untouched_by_switch():
    """开关切换不得改变 detect_grid_watermark 的任何输出（逐字节）。

    为什么必须写死这条：给网格通路的证据掩码接守卫看着「更一致」，实测却是
    **纯亏本**（合成精确率已是 1.000、再收只剩召回损失；真实图并集面积
    10.19% → 10.15% 几乎不动）。后人若把它接上，本用例失败 = 提醒重新量测。
    """
    base = BASE_KINDS["textured"](7)
    marked = CASES["grid"][0](base)
    args = (marked, _SENS["grid"], marked.shape[0] * marked.shape[1], _MAR, _XAR)
    prev = config.DETECT_TILED_PATH_GUARD
    try:
        config.DETECT_TILED_PATH_GUARD = False
        m_off, r_off = det.detect_grid_watermark(*args)
        config.DETECT_TILED_PATH_GUARD = True
        m_on, r_on = det.detect_grid_watermark(*args)
    finally:
        config.DETECT_TILED_PATH_GUARD = prev
    assert np.array_equal(m_off, m_on), "网格通路被本开关误导了"
    assert len(r_off) == len(r_on)


# ---------------------------------------------------------------------------
# D. 反例：周期性不能作为本问题的判据
# ---------------------------------------------------------------------------

def test_D_periodicity_is_not_a_valid_criterion():
    """干净的周期纹理轻松通过 ACF 判据 ⇒ 周期性本身无法区分水印与纹理。

    实测依据（阶段 104 探针 s104_acf.py，真实 640px 照片 10 张）：
        干净图 ACF 均值 0.268（min 0.116，**100% 过阈**）；加了合成水印也只 0.245~0.248。
    ⇒ 在真实照片上 DETECT_GRID_MIN_ACF(0.09) 恒被满足，它**没有区分力**；
      这也解释了为什么折叠模板会在真实图上铺满全图（`tmpl_frac` 实测达 1.000），
      以及为什么 DETECT_GRID_EVIDENCE_AND 是承重结构、绝不可关闭。
    """
    h = w = 640
    yy, xx = np.mgrid[0:h, 0:w]
    periodic = (((xx // 8) + (yy // 8)) % 2).astype(np.float32) * 180.0 + 40.0
    img = np.repeat(periodic[:, :, None], 3, axis=2).astype(np.uint8)
    signed = det._residual_fields(img)[1]
    peak, _, _ = det._periodic_strength(signed)
    assert peak >= config.DETECT_GRID_MIN_ACF, (
        f"干净周期纹理 ACF={peak:.3f} 未过阈 {config.DETECT_GRID_MIN_ACF} —— "
        "若成立说明阈值已抬高，需重新评估「ACF 不能作判据」这条结论"
    )


# ---------------------------------------------------------------------------
# E. 反例：低分辨率兜底臂必须豁免本守卫
# ---------------------------------------------------------------------------

def test_E_rescue_arm_mechanism_guard_matters():
    """机制依据：兜底臂口径（INTER_CUBIC 放大到 640 的小图）下，守卫会把整类抹零。

    这就是 `detect_watermark_mask` 必须对兜底臂传 `guard=False` 的原因 ——
    `tests/test_lowres_rescue.py::test_rescue_recovers_blackout[256-webp75-textured-grid]`
    是本条的用户可见后果。
    """
    cv2 = det._require_cv2()
    base = BASE_KINDS["textured"](7, w=256, h=256)
    marked = CASES["grid"][0](base)
    up = cv2.resize(marked, (640, 640), interpolation=cv2.INTER_CUBIC)
    area = 640 * 640
    m_on, _ = det.detect_tiled_translucent(
        up, _SENS["grid"], area, _MAR, _XAR, guard=True)
    m_off, _ = det.detect_tiled_translucent(
        up, _SENS["grid"], area, _MAR, _XAR, guard=False)
    assert np.count_nonzero(m_off) > 0, "兜底臂口径下 guard=False 竟为空，豁免无意义"
    assert not np.any((m_on > 0) & ~(m_off > 0)), "守卫不得新增像素"
    assert np.count_nonzero(m_on) <= np.count_nonzero(m_off), "守卫应更小"


def test_E_rescue_arm_passes_guard_false(monkeypatch):
    """用间谍函数锁定调用参数：**主臂传 `None`（交回 config）、兜底臂传 `False`**。

    只靠输出反推容易被「恰好也非空」蒙混；本用例直接钉住调用契约。

    ⚠ **阶段 107 修正**（本用例此前断言 `True in calls`，**恰好把缺陷钉在了原地**）：
    主臂曾经写死 `guard=(_rescue_depth == 0)` = `True`，而
    `detect_tiled_translucent` 内部是 `_guard = config.DETECT_TILED_PATH_GUARD if guard is None else bool(guard)`
    ⇒ **实参遮蔽 config** ⇒ `DETECT_TILED_PATH_GUARD` 从未被读取，
    阶段 104 宣称的「注入式反例 `WME_DETECT_TILED_PATH_GUARD=0`」自诞生起就无效
    （实测：`guard=True` + config=False 与 `guard=None` + config=True **逐位相同**）。
    故主臂必须传 `None`：这样开关由 config 决定，注入才真的可逆。
    """
    calls = []
    real = det.detect_tiled_translucent

    def spy(rgb, sens, area, mar, xar, **kw):
        calls.append(kw.get("guard"))
        return real(rgb, sens, area, mar, xar, **kw)

    monkeypatch.setattr(det, "detect_tiled_translucent", spy)
    base = BASE_KINDS["textured"](7, w=256, h=256)
    marked = CASES["grid"][0](base)
    det.detect_watermark_mask(marked, preset="ai_grid", use_ocr=False,
                              lowres_rescue=True)
    assert None in calls, (
        f"主臂未传 guard=None（= 交回 config 决定）：{calls} —— "
        "若又传了 True/False，`DETECT_TILED_PATH_GUARD` 会再次成为死开关")
    assert False in calls, f"兜底臂未传 guard=False（豁免失效）：{calls}"
    assert True not in calls, (
        f"有调用点写死了 guard=True：{calls} —— 那就是「开关被实参遮蔽」的原缺陷形态")


# ---------------------------------------------------------------------------
# F. 配置契约
# ---------------------------------------------------------------------------

def test_F_config_shape():
    """新开关存在、是布尔、且默认开启（默认关会静默回退到阶段 103 行为）。"""
    assert isinstance(config.DETECT_TILED_PATH_GUARD, bool)
    assert config.DETECT_TILED_PATH_GUARD is True
