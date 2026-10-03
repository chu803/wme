# -*- coding: utf-8 -*-
"""
阶段 33 测试：视频真实超分接入 + 抽帧对比查看。

覆盖点：
  1. 无后端（沙箱无 torch）时真实超分优雅回退，输出尺寸不变；
  2. 假后端放大 2× 时整段视频输出尺寸整体放大、所有帧同尺寸；
  3. 显式关闭真实超分时输出等于源尺寸；
  4. VideoResult 新增 upscaled / real_scale_used 字段默认与回填正确；
  5. 抽帧对比查看（concat/slider/heatmap）能渲染，缺资源给提示；
  6. 直接传入已构造后端实例（real_backend=实例）被复用（视频整段复用路径）；
  7. 开启真实超分但后端不可用（返回 None）时回退并给出警告。
"""

from __future__ import annotations

import os as _os

import cv2
import numpy as np
import pytest

from core.superres import SuperResBackend
from services import video_service


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


# 环境前提：真实超分「无后端 → 回退」仅当 torch 缺失时成立；
# torch 就绪后 esrgan 后端存在（会真实放大），该断言反转，须跳过。
_TORCH_PRESENT = _torch_available()


def _make_test_mp4(path: str, w: int = 160, h: int = 120, n: int = 8) -> None:
    """合成一段带「右下角固定水印」的测试视频。"""
    writer, _codec = video_service._open_writer(path, 10.0, w, h)
    assert writer is not None, "测试视频无法创建（编码器不可用）"
    for i in range(n):
        canvas = np.full((h, w, 3), 40 + i * 6, dtype=np.uint8)
        cv2.circle(canvas, (40 + i * 8, 60), 18, (200, 120, 60), -1)
        cv2.putText(canvas, "MARK", (w - 50, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        writer.write(canvas)
    writer.release()


class _FakeBackend(SuperResBackend):
    """测试用假后端：把画面放大 2×，并统计被调用次数。"""

    name = "fake"

    def __init__(self, *a, **k):
        super().__init__()
        self.used_real = True
        self.note = "fake 2x"
        self.calls = 0

    def upscale(self, arr, scale):
        self.calls += 1
        hh, ww = arr.shape[:2]
        return cv2.resize(arr, (ww * 2, hh * 2)), True, "fake 2x"


@pytest.fixture
def fake_backend_cls():
    return _FakeBackend


@pytest.mark.skipif(_TORCH_PRESENT, reason="torch 已安装，真实超分后端存在，跳过「无后端回退」验证")
def test_process_video_real_superres_fallback_no_backend(tmp_path):
    """无 torch 环境：真实超分开启但后端不可用，应回退且输出尺寸不变。"""
    src = str(tmp_path / "in.mp4")
    _make_test_mp4(src, 160, 120, 6)
    out = str(tmp_path / "out.mp4")
    res = video_service.process_video(
        src, out, frame_step=1, real_superres=True, max_frames=6,
    )
    assert res.status == "success"
    assert res.upscaled is False, "无后端时不应标记已放大"
    info = video_service.get_video_info(res.out_path)
    assert info["width"] == 160 and info["height"] == 120, "输出尺寸应与原尺寸一致"


def test_process_video_real_superres_upscales_fake_backend(tmp_path, monkeypatch):
    """假后端放大 2×：整段输出尺寸整体放大，所有帧同尺寸、不丢帧。"""
    src = str(tmp_path / "in.mp4")
    _make_test_mp4(src, 160, 120, 6)
    out = str(tmp_path / "out.mp4")
    monkeypatch.setattr(
        video_service, "get_superres_backend",
        lambda *a, **k: _FakeBackend(),
    )
    res = video_service.process_video(
        src, out, frame_step=1, real_superres=True, real_scale=2, max_frames=6,
    )
    assert res.status == "success"
    assert res.upscaled is True, "应标记已放大"
    assert res.real_scale_used == 2
    info = video_service.get_video_info(res.out_path)
    assert info["width"] == 320 and info["height"] == 240, "输出应为 2× 尺寸"
    assert res.written_frames == 6, "不应丢帧"


def test_process_video_real_superres_disabled_equals_source(tmp_path):
    """显式关闭真实超分：输出尺寸等于源尺寸。"""
    src = str(tmp_path / "in.mp4")
    _make_test_mp4(src, 160, 120, 4)
    out = str(tmp_path / "out.mp4")
    res = video_service.process_video(
        src, out, frame_step=1, real_superres=False, max_frames=4,
    )
    assert res.status == "success"
    assert res.upscaled is False
    info = video_service.get_video_info(res.out_path)
    assert info["width"] == 160 and info["height"] == 120


def test_video_result_upscaled_default_and_summary():
    """VideoResult 新增字段默认值与 summary 不崩溃。"""
    from services.video_service import VideoResult

    r0 = VideoResult(src_path="x", status="success")
    assert r0.upscaled is False
    assert r0.real_scale_used == 0
    assert "耗时" in r0.summary()

    r1 = VideoResult(src_path="y", status="success", upscaled=True, real_scale_used=4)
    assert "4×" in r1.summary()


def test_run_video_compare_modes(tmp_path):
    """抽帧对比查看：concat/slider/heatmap 均能渲染；缺结果给提示。"""
    from ui.tab_media import run_video_compare

    src = str(tmp_path / "in.mp4")
    out = str(tmp_path / "out.mp4")
    _make_test_mp4(src, 160, 120, 6)
    _make_test_mp4(out, 320, 240, 6)  # 模拟已被真实超分放大的结果

    html_concat = run_video_compare(src, out, 0, "concat")
    assert "<img" in html_concat, "拼接模式应返回内嵌图片 HTML"

    html_slider = run_video_compare(src, out, 0, "slider")
    assert "ba-wrap" in html_slider, "滑块模式应返回滑动对比控件"

    html_heat = run_video_compare(src, out, 0, "heatmap")
    assert "<img" in html_heat, "热图模式应返回内嵌图片 HTML"

    # 缺结果视频（提示语言随默认语言变化，用 ⚠ 做语言无关断言）
    no_res = run_video_compare(src, "", 0, "concat")
    assert "⚠" in no_res, "缺结果应给出提示"


def test_process_video_real_backend_instance_reused(tmp_path, fake_backend_cls):
    """直接传入已构造后端实例：被整段复用，输出 2×。"""
    src = str(tmp_path / "in.mp4")
    _make_test_mp4(src, 160, 120, 6)
    out = str(tmp_path / "out.mp4")
    inst = fake_backend_cls()
    res = video_service.process_video(
        src, out, frame_step=2, reuse_mask=False,
        real_superres=True, real_scale=2, real_backend=inst, max_frames=6,
    )
    assert res.status == "success"
    assert res.upscaled is True
    info = video_service.get_video_info(res.out_path)
    assert info["width"] == 320 and info["height"] == 240
    # 6 帧逐帧检测（步长 2 + reuse_mask=False 会被自动拉回逐帧）→ 实例被调用 6 次。
    assert inst.calls == 6, f"后端实例应被复用 6 次，实际 {inst.calls}"


def test_process_video_real_backend_string_kind_passed(tmp_path, monkeypatch):
    """阶段 34：传入字符串后端类型，透传给工厂的 kind 参数。"""
    src = str(tmp_path / "in.mp4")
    _make_test_mp4(src, 160, 120, 4)
    out = str(tmp_path / "out.mp4")
    captured = {}
    def fake_get_backend(kind, model_path="", model_dir=""):
        captured["kind"] = kind
        return _FakeBackend()
    monkeypatch.setattr(video_service, "get_superres_backend", fake_get_backend)
    res = video_service.process_video(
        src, out, frame_step=1, real_superres=True, real_scale=2,
        real_backend="esrgan", max_frames=4,
    )
    assert res.status == "success"
    assert captured.get("kind") == "esrgan", f"后端类型应透传为 esrgan，实际 {captured}"


def test_process_video_real_superres_enabled_but_unavailable_warns(tmp_path, monkeypatch):
    """开启真实超分但后端返回 None：回退轻量超分、尺寸不变，并给出警告。"""
    src = str(tmp_path / "in.mp4")
    _make_test_mp4(src, 160, 120, 4)
    out = str(tmp_path / "out.mp4")
    monkeypatch.setattr(
        video_service, "get_superres_backend", lambda *a, **k: None,
    )
    res = video_service.process_video(
        src, out, frame_step=1, real_superres=True, real_scale=4, max_frames=4,
    )
    assert res.status == "success"
    assert res.upscaled is False
    info = video_service.get_video_info(res.out_path)
    assert info["width"] == 160 and info["height"] == 120
    assert any("回退" in w for w in res.warnings), "应提示已回退轻量超分"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
