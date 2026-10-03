"""阶段 76 · API / 服务层能力同步测试。

背景（docs/76-*）：阶段 70~75 陆续交付了 ICC 保留、输入形态规范化、透明通道策略、
低分辨率兜底、双跑择优与处理偏好持久化，但**外部集成入口（REST / 库 API）没有跟进**：

- `api.remove_watermark*` / `batch_remove` 不接受 `dual_run` → 程序化调用无法启用双跑；
- `get_capabilities()` 缺阶段 70~75 的能力字段 → 客户端只能读源码猜默认值；
- `server._collect_params` 白名单只覆盖基础参数 → REST 客户端发 `dual_run`/增强参数
  会被**静默丢弃**（最隐蔽的一类缺口）。

本文件锁定：能力快照字段、四层 API 的 dual_run 透传、REST 参数白名单与布尔归一、
PDF/视频不暴露 dual_run 的边界、以及端到端可调用性。
"""
from __future__ import annotations

import base64
import inspect
import os
import sys

import cv2
import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import api  # noqa: E402
import config  # noqa: E402
import server  # noqa: E402


def _b64_png(w=80, h=60) -> str:
    img = np.full((h, w, 3), 200, np.uint8)
    img[20:40, 25:55] = 60
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


# ---------------------------------------------------------------------------
# 1. 能力快照：阶段 70~75 字段
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "mask_dilate_default",
    "mask_feather_default",
    "inpaint_auto_prefer",
    "dual_run_default",
    "dual_run_secondary",
    "output_alpha_formats",
    "output_flatten_bg_color",
    "keep_alpha_default",
    "exif_orientation_applied",
    "bit_depth_normalized",
    "icc_preserved_on_save",
    "lowres_rescue",
    "lowres_rescue_side",
    "detect_min_area_ref",
    "optional_enhance_gates",
    "user_overrides_path",
])
def test_capabilities_expose_stage70_75_fields(key):
    caps = api.get_capabilities()
    assert key in caps, f"能力快照缺字段 {key}"


def test_capabilities_values_match_config():
    """能力快照必须与进程实际配置同源（不是硬编码）。"""
    caps = api.get_capabilities()
    assert caps["dual_run_default"] == bool(config.INPAINT_DUAL_RUN)
    assert caps["dual_run_secondary"] == config.INPAINT_DUAL_SECONDARY
    assert caps["keep_alpha_default"] == bool(config.PNG_KEEP_ALPHA)
    assert caps["output_alpha_formats"] == list(config.OUTPUT_ALPHA_FORMATS)
    assert caps["icc_preserved_on_save"] is True


def test_optional_enhance_gates_are_conjunction():
    """可选增强是「默认开关 ∧ 部署放行闸」双闸（阶段 61）。"""
    caps = api.get_capabilities()
    gates = caps["optional_enhance_gates"]
    assert set(gates) == {"poisson_blend", "texture_transfer", "artifact_reduce"}
    expected = bool(config.POSTPROCESS_POISSON_BLEND) and bool(config.POSTPROCESS_POISSON_ALLOW)
    assert gates["poisson_blend"] == expected


# ---------------------------------------------------------------------------
# 1b. 能力快照：阶段 77 插件框架状态
# ---------------------------------------------------------------------------

def test_capabilities_expose_plugin_report():
    """能力快照含插件框架状态段，且字段齐备。"""
    caps = api.get_capabilities()
    assert "plugins" in caps, "能力快照缺 plugins 段（阶段 77）"
    plug = caps["plugins"]
    assert set(plug) >= {"count", "allow_override", "loaded", "failed", "skipped", "overridden"}
    assert isinstance(plug["count"], int)
    assert plug["allow_override"] == bool(config.PLUGIN_ALLOW_OVERRIDE)


def test_plugin_report_only_exposes_basenames():
    """插件状态只暴露文件名，不泄露本机目录结构。"""
    plug = api.get_capabilities()["plugins"]
    for name in plug["loaded"] + plug["skipped"]:
        assert "/" not in name and "\\" not in name, f"不应暴露完整路径：{name}"
    for item in plug["failed"]:
        assert "/" not in item["file"] and "\\" not in item["file"]


def test_engine_details_carry_source_field():
    """引擎清单（阶段 77）含 source 字段——调用方可判断引擎来自内建还是插件文件。"""
    engines = api.get_capabilities()["engines"]
    assert engines, "引擎清单不应为空"
    for e in engines:
        assert "source" in e
    lama = next(e for e in engines if e["name"] == "lama")
    assert lama["builtin"] is True and lama["source"] == ""


# ---------------------------------------------------------------------------
# 2. API 层 dual_run 透传（四层签名 + 实际转发）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn_name", [
    "remove_watermark",
    "remove_watermark_bytes",
    "remove_watermark_array",
    "batch_remove",
])
def test_api_functions_accept_dual_run(fn_name):
    fn = getattr(api, fn_name)
    assert "dual_run" in inspect.signature(fn).parameters


def test_pdf_video_do_not_expose_dual_run():
    """边界：PDF/视频不暴露 dual_run（逐页/逐帧成本翻倍，走进程配置）。"""
    assert "dual_run" not in inspect.signature(api.remove_watermark_pdf).parameters
    assert "dual_run" not in inspect.signature(api.remove_watermark_video).parameters


def test_remove_watermark_forwards_dual_run(monkeypatch):
    """remove_watermark → process_file 的 dual_run 必须真实转发。"""
    captured = {}

    def _fake_process_file(src, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(api, "process_file", _fake_process_file)
    try:
        api.remove_watermark("x.png", dual_run=True)
    except Exception:  # noqa: BLE001 - 返回值 None 后续可能报错，此处只关心转发
        pass
    assert captured.get("dual_run") is True


def test_remove_watermark_bytes_forwards_dual_run(monkeypatch):
    captured = {}

    def _fake_process_image(arr, **kwargs):
        captured.update(kwargs)
        ok, buf = cv2.imencode(".png", arr)
        return {"status": "success", "result_rgb": arr, "mask": None,
                "detect": None, "used_ocr": False, "engine": "opencv",
                "out_path": None}

    monkeypatch.setattr(api, "process_image", _fake_process_image)
    try:
        api.remove_watermark_bytes(base64.b64decode(_b64_png()), dual_run=True)
    except Exception:  # noqa: BLE001 - 假引擎返回值不完整，此处只关心转发
        pass
    assert captured.get("dual_run") is True


def test_remove_watermark_array_end_to_end_with_dual_run():
    """端到端：数组接口开 dual_run 能跑通（副引擎与主引擎相同 → 双跑自动跳过）。

    注：用合成用例（角标 LOGO）保证「有可检出的水印」——平坦色块不构成水印，
    检测器按设计会判「未检测到水印区域」。
    """
    from scripts.detector_diag import BASE_KINDS
    from scripts.make_demo_assets import CASES

    base = BASE_KINDS["gradient"](7, w=640, h=640)
    marked = CASES["corner_logo"][0](base)
    # 显式关闭超分：本机 config.user_overrides.json 可能已开真实超分（阶段 35），
    # 那会让输出尺寸翻倍而与输入不可比（阶段 60 同类坑）。
    out, _qr = api.remove_watermark_array(
        marked, engine="opencv", preset="ai_logo", dual_run=True,
        dilate=0, denoise=0, real_superres=False, superres=False)
    assert out.shape == marked.shape and out.dtype == np.uint8
    # 角标区域应被修复（与带水印输入不再逐位相同）。
    assert not np.array_equal(out, marked)


def test_batch_remove_forwards_dual_run(monkeypatch, tmp_path):
    captured = {}

    def _fake_remove_watermark(path, **kwargs):
        captured.update(kwargs)
        return api.RemoveResult(original_name="x.png", status="success")

    monkeypatch.setattr(api, "remove_watermark", _fake_remove_watermark)
    api.batch_remove(["a.png"], str(tmp_path), dual_run=True)
    assert captured.get("dual_run") is True


# ---------------------------------------------------------------------------
# 3. REST 参数白名单（server._collect_params）
# ---------------------------------------------------------------------------

def test_collect_params_accepts_dual_run():
    out = server._collect_params({"dual_run": True, "engine": "opencv"})
    assert out["dual_run"] is True and out["engine"] == "opencv"


def test_collect_params_normalizes_bool_strings():
    out = server._collect_params({"dual_run": "TRUE", "superres": "yes",
                                  "evaluate": "0", "artifact_reduce": "on"})
    assert out["dual_run"] is True
    assert out["superres"] is True
    assert out["evaluate"] is False
    assert out["artifact_reduce"] is True


def test_collect_params_accepts_enhance_keys():
    body = {"poisson_blend": False, "texture_amount": 0.3,
            "real_scale": 2, "real_backend": "auto"}
    out = server._collect_params(body)
    assert out == body


def test_collect_params_drops_unknown_keys():
    out = server._collect_params({"evil": "payload", "engine": "lama"})
    assert "evil" not in out and out["engine"] == "lama"


def test_collect_params_empty_body():
    assert server._collect_params({}) == {}


# ---------------------------------------------------------------------------
# 4. REST 响应回显
# ---------------------------------------------------------------------------

def test_response_echoes_dual_run_default(monkeypatch):
    """未传 dual_run 时响应回显进程默认值（便于客户端确认是否双跑）。"""
    monkeypatch.setattr(api, "remove_watermark_bytes",
                        lambda data, **kw: (b"x", None))
    resp = server._process_one_b64(_b64_png(), {})
    assert resp["status"] == "success"
    assert resp["dual_run"] == bool(config.INPAINT_DUAL_RUN)


def test_response_echoes_explicit_dual_run(monkeypatch):
    monkeypatch.setattr(api, "remove_watermark_bytes",
                        lambda data, **kw: (b"x", None))
    resp = server._process_one_b64(_b64_png(), {"dual_run": True})
    assert resp["dual_run"] is True
