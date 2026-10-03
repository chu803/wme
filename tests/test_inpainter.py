# -*- coding: utf-8 -*-
"""核心修复入口端到端测试（阶段 3/11/13）。

约束：本环境无 torch，故只测不依赖 LaMa 的引擎：
- opencv 引擎（OpenCV 传统算法，CPU 可用）
- demo_model 插件（无自定义权重时降级为 cv2 TELEA，同样无需 torch）
- auto 引擎（无模型时自动降级为 opencv）

三者都走真实修复管线（掩码预处理 → 引擎修复 → 后处理），
验证「修复引擎」作为唯一可扩展入口的端到端闭环。
"""

import numpy as np
import pytest

from core.inpainter import inpaint


def test_opencv_engine_end_to_end(synthetic_image_mask):
    """OpenCV 引擎应真实擦除掩码区水印。"""
    img, mask = synthetic_image_mask
    out = inpaint(img, mask, engine="opencv", strength=1.0)
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    # 掩码区应被填充，不再是原蓝色方块
    region_before = img[20:44, 20:44]
    region_after = out[20:44, 20:44]
    assert not np.array_equal(region_before, region_after)
    # 掩码外区域应基本保持不变（背景浅灰）
    bg = out[0:5, 0:5]
    assert bg.mean() > 200


def test_demo_model_plugin_end_to_end(synthetic_image_mask):
    """示例插件（demo_model）无自定义权重时降级为 cv2，应同样修复成功。"""
    img, mask = synthetic_image_mask
    out = inpaint(img, mask, engine="demo_model", strength=1.0)
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(img[20:44, 20:44], out[20:44, 20:44])


def test_auto_falls_back_to_opencv_without_model(synthetic_image_mask):
    """无 LaMa 权重时，auto 应自动降级为 opencv 并成功。"""
    img, mask = synthetic_image_mask
    out = inpaint(img, mask, engine="auto", strength=1.0)
    assert out.shape == img.shape
    assert out.dtype == np.uint8
    assert not np.array_equal(img[20:44, 20:44], out[20:44, 20:44])


def test_empty_mask_raises(synthetic_image_mask):
    """全黑掩码应抛出 EmptyMaskError，提示用户先标注区域。"""
    img, _ = synthetic_image_mask
    empty = np.zeros((64, 64), np.uint8)
    with pytest.raises(Exception):
        inpaint(img, empty, engine="opencv")


@pytest.mark.torch_required
def test_lama_engine_end_to_end(synthetic_image_mask):
    """LaMa 引擎端到端（需要 torch，离线 CI 跳过）。"""
    pytest.importorskip("torch")
    img, mask = synthetic_image_mask
    out = inpaint(img, mask, engine="lama", strength=1.0)
    assert out.shape == img.shape


# ------------------------------------------------------------------------------
# 阶段 A 回归：LaMa 值域契约（docs/A3-LaMa与OpenCV真实图对决与决策.md）
#
# 历史缺陷：`_run_lama_tile` 先把输入 `* 2 - 1` 压到 [-1,1]，又在输出端
# `* 0.5 + 0.5` 反归一化——同一份 [0,1] 约定被变换了两次。big-lama.pt 的
# TorchScript 导出本来就吃 [0,1]、吐 [0,1]，双重变换导致掩码区饱和成纯白
# （真实图实测 87% 掩码像素 ≥250），这正是阶段 51「OpenCV 8/8 全胜」的真因。
#
# 下面两个用例分别锁「输入值域」与「输出不做二次变换」，都是能抓住该 Bug 的
# 反例测试——只断言 shape 的端到端用例永远发现不了它。
# ------------------------------------------------------------------------------


class _ValueRangeProbeModel:
    """
    伪 LaMa 模型：记录收到的输入值域，并按官方约定原样回吐图像。

    用途：把 `_run_lama_tile` 的预处理契约钉死，不依赖真实权重与 torch 推理结果。
    """

    def __init__(self):
        import torch

        self._param = torch.zeros(1)
        self.seen_img_range = None
        self.seen_mask_range = None

    def parameters(self):
        return iter([self._param])

    def __call__(self, img, mask):
        self.seen_img_range = (float(img.min()), float(img.max()))
        self.seen_mask_range = (float(mask.min()), float(mask.max()))
        return img


@pytest.mark.torch_required
def test_lama_input_range_is_unit_interval():
    """LaMa 预处理的输入值域必须是 [0,1]，而非 [-1,1]（A 阶段回归）。"""
    torch = pytest.importorskip("torch")
    from core.inpainter import _run_lama_tile

    img = np.full((64, 64, 3), 30, np.uint8)  # 深色底，值域错误时最容易暴露
    mask = np.zeros((64, 64), np.uint8)
    mask[20:44, 20:44] = 255

    probe = _ValueRangeProbeModel()
    _run_lama_tile(probe, img, mask, use_half=False)

    lo, hi = probe.seen_img_range
    assert lo >= -1e-6, f"输入下界 {lo} 越界，应为 [0,1]"
    assert hi <= 1.0 + 1e-6, f"输入上界 {hi} 越界，应为 [0,1]（[-1,1] 是历史缺陷）"
    # 掩码语义：{0,1}，非 0 表示待修复。
    assert probe.seen_mask_range == (0.0, 1.0)


@pytest.mark.torch_required
def test_lama_output_not_double_normalized():
    """
    恒等伪模型（输入 [0,1] → 原样输出）经 `_run_lama_tile` 后应保持不变。

    若输出端残留 `* 0.5 + 0.5`，深色像素会被抬亮到 ≥128，
    本用例即时报错——这正是「白板」现象的数值指纹。
    """
    pytest.importorskip("torch")
    from core.inpainter import _run_lama_tile

    img = np.full((64, 64, 3), 30, np.uint8)
    mask = np.zeros((64, 64), np.uint8)
    mask[20:44, 20:44] = 255

    out = _run_lama_tile(_ValueRangeProbeModel(), img, mask, use_half=False)

    # 恒等映射：输出应与输入一致（容许 uint8 量化误差 ±1）。
    assert np.abs(out.astype(np.int16) - img.astype(np.int16)).max() <= 1, (
        f"恒等伪模型输出被改动：out.mean={out.mean():.1f} vs in.mean={img.mean():.1f}，"
        "疑似输出端残留 * 0.5 + 0.5 二次变换"
    )


@pytest.mark.torch_required
def test_lama_real_model_does_not_saturate_white():
    """
    真实 LaMa 权重：深色底 + 亮色块，修复后掩码区不应饱和成白板。

    这是端到端的「白板指纹」闸门：历史缺陷下掩码区均值 ≈250，
    归位后应回落到背景量级（<160）。
    """
    pytest.importorskip("torch")
    from core.inpainter import inpaint
    from core.model_loader import is_model_available

    if not is_model_available():
        pytest.skip("LaMa 权重未就绪")

    img = np.full((256, 256, 3), 35, np.uint8)
    img[90:170, 60:200] = (250, 250, 250)  # 亮色"水印"，需被深色底吸收
    mask = np.zeros((256, 256), np.uint8)
    mask[80:180, 50:210] = 255

    out = inpaint(img, mask, engine="lama", strength=1.0)
    region = out[mask > 0]
    frac_white = float(np.mean(np.all(region >= 250, axis=1)))
    assert frac_white < 0.10, f"掩码区 {frac_white:.1%} 像素饱和成纯白，疑似值域缺陷复发"
    assert region.mean() < 160, f"掩码区均值 {region.mean():.1f} 过亮，疑似值域缺陷复发"
