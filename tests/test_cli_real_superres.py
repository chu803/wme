# -*- coding: utf-8 -*-
"""
CLI 真实超分配置对齐测试（阶段 39）。

覆盖：
- 帮助信息包含 --real-backend / --no-real-superres，且 --real-backend 选项受约束。
- 三个真实超分参数的默认值取自「应用 user_overrides.json 之后」的 config 运行值
  （即 CLI 自动继承设置页保存的默认值）。
- 非法 --real-backend 取值被 argparse 拒绝（退出码 2）。
- _run_one / _run_pdf / _run_video 在调用下游处理函数时确实透传了
  real_superres / real_scale / real_backend（含图片此前缺失的 real_backend）。

约束：零 torch；不做真实图像处理（下游函数以 monkeypatch 替身捕获入参）；
不创建/删除真实产物文件（输出目录用临时目录，且下游被替换，不会真正写盘）。
"""

import argparse

import cli


# ------------------------------------------------------------------------------
# 构造一个「最小可用」的 Namespace：解析空参数拿全部默认值，再补 output_dir。
# 这样测试既用上真实默认，又不依赖 main() 的输出目录推导逻辑。
# ------------------------------------------------------------------------------
def _args(tmp_path, **over):
    ns = cli._build_arg_parser().parse_args([])
    ns.output_dir = str(tmp_path)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _fake_obj():
    """返回一个对象结果（供 process_pdf / process_video 使用，按属性访问）。"""

    class _R:
        status = "success"
        out_path = "fake_out"
        error = None

        def summary(self):
            return "ok"

    return _R()


def _fake_dict():
    """返回一个 dict 结果（供 process_file 使用，按 key 访问 + setdefault）。"""
    return {"status": "success", "original_name": "x", "out_path": "fake_out",
            "error": None}


# ------------------------------------------------------------------------------
# 帮助 / 参数解析层（与用户真实使用方式一致：走 argparse）
# ------------------------------------------------------------------------------
def test_help_shows_real_backend_and_no_real_superres():
    """--help 应声明 --real-backend 与 --no-real-superres 开关。"""
    p = cli._build_arg_parser()
    # 直接 inspect 解析器动作，避免打印噪声。
    actions = {a.dest: a for a in p._actions}
    assert "real_backend" in actions
    assert actions["real_backend"].choices == ["auto", "esrgan", "realesrgan"]
    # BooleanOptionalAction 会同时注册 --real-superres 与 --no-real-superres。
    opt_strings = [s for a in p._actions for s in a.option_strings]
    assert "--real-superres" in opt_strings
    assert "--no-real-superres" in opt_strings


def test_real_backend_invalid_choice_exits_2():
    """非法 --real-backend 取值应被 argparse 拒绝（退出码 2）。"""
    import pytest

    with pytest.raises(SystemExit) as exc:
        cli._build_arg_parser().parse_args(["--real-backend", "bogus"])
    assert exc.value.code == 2


def test_arg_defaults_inherit_user_overrides():
    """三参数默认值应等于「应用 user_overrides 之后」的 config 运行值。"""
    import config

    p = cli._build_arg_parser()
    assert p.get_default("real_backend") == config.REAL_SUPERRES_BACKEND
    assert p.get_default("real_superres") == config.REAL_SUPERRES_ENABLED
    assert p.get_default("real_scale") == config.REAL_SUPERRES_SCALE


# ------------------------------------------------------------------------------
# 透传层（monkeypatch 下游函数，捕获入参）
# ------------------------------------------------------------------------------
def test_run_one_threads_real_backend(tmp_path, monkeypatch):
    """图像路径：real_backend 此前缺失，现应透传；real_superres/scale 同步。"""
    from core.image_utils import save_image
    import numpy as np

    captured = {}
    monkeypatch.setattr(
        cli, "process_file",
        lambda *a, **k: (captured.update(k) or _fake_dict()),
    )
    img = tmp_path / "src.png"
    save_image(str(img), np.zeros((10, 10, 3), dtype=np.uint8), fmt="PNG")

    ns = _args(tmp_path, real_superres=True, real_scale=4,
               real_backend="realesrgan")
    rd = cli._run_one(str(img), ns, show_progress=False)
    assert rd["status"] == "success"
    assert captured.get("real_backend") == "realesrgan"
    assert captured.get("real_superres") is True
    assert captured.get("real_scale") == 4


def test_run_pdf_threads_real_params(tmp_path, monkeypatch):
    """PDF 路径：real_superres/real_scale/real_backend 此前完全未透传，现应补全。"""
    from services import pdf_service

    captured = {}
    monkeypatch.setattr(
        pdf_service, "process_pdf",
        lambda *a, **k: (captured.update(k) or _fake_obj()),
    )
    ns = _args(tmp_path, real_superres=True, real_scale=4,
               real_backend="esrgan")
    rd = cli._run_pdf(str(tmp_path / "doc.pdf"), ns)
    assert rd["kind"] == "pdf"
    assert rd["status"] == "success"
    assert captured.get("real_backend") == "esrgan"
    assert captured.get("real_superres") is True
    assert captured.get("real_scale") == 4


def test_run_video_threads_real_params(tmp_path, monkeypatch):
    """视频路径：real_superres/real_scale/real_backend 此前完全未透传，现应补全。"""
    from services import video_service

    captured = {}
    monkeypatch.setattr(
        video_service, "process_video",
        lambda *a, **k: (captured.update(k) or _fake_obj()),
    )
    ns = _args(tmp_path, real_superres=True, real_scale=2,
               real_backend="auto")
    rd = cli._run_video(str(tmp_path / "clip.mp4"), ns)
    assert rd["kind"] == "video"
    assert rd["status"] == "success"
    assert captured.get("real_backend") == "auto"
    assert captured.get("real_superres") is True
    assert captured.get("real_scale") == 2


def test_run_one_respects_no_real_superres(tmp_path, monkeypatch):
    """--no-real-superres 时 real_superres 应为 False 并透传。"""
    from core.image_utils import save_image
    import numpy as np

    captured = {}
    monkeypatch.setattr(
        cli, "process_file",
        lambda *a, **k: (captured.update(k) or _fake_dict()),
    )
    img = tmp_path / "src2.png"
    save_image(str(img), np.zeros((8, 8, 3), dtype=np.uint8), fmt="PNG")
    ns = _args(tmp_path, real_superres=False, real_scale=2,
               real_backend="auto")
    cli._run_one(str(img), ns, show_progress=False)
    assert captured.get("real_superres") is False
    assert captured.get("real_backend") == "auto"
