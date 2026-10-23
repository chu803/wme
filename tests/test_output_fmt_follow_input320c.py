# -*- coding: utf-8 -*-
"""阶段 320c · `OUTPUT_FMT_FOLLOW_INPUT`（输出格式跟随输入）守卫。

为什么要它：阶段 319 实测 PNG 输出是输入的 **4.65×**（JPG 1.13×、WebP 1.00×）。
但「默认要不要跟随输入」是观感/无损承诺的产品决策，故本阶段只加**能力**、默认关：
- 关着时行为与之前**逐字节一致**（`_resolve_out_fmt` 返回 None，交给 `save_format` 默认）；
- 开着时才按扩展名映射；显式 `out_fmt` 永远优先。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
import services.auto_pipeline as ap  # noqa: E402
from ui.tab_auto import SAMPLE_IMAGE_PATH  # noqa: E402


@pytest.mark.parametrize("path,want", [
    ("a.png", "PNG"), ("a.JPG", "JPG"), ("a.jpeg", "JPG"), ("a.webp", "WEBP"),
    ("a.tif", "TIFF"), ("a.tiff", "TIFF"), ("a.avif", "AVIF"),
    ("a.bmp", "PNG"),          # BMP 无对应输出格式 ⇒ 无损 PNG
    ("a.unknown", None),       # 未知 ⇒ 交给既有默认
    ("noext", None),
])
def test_mapping_when_enabled(monkeypatch, path, want):
    monkeypatch.setattr(config, "OUTPUT_FMT_FOLLOW_INPUT", True, raising=True)
    assert ap._resolve_out_fmt(path, None) == want


def test_disabled_returns_none_regardless_of_ext(monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_FMT_FOLLOW_INPUT", False, raising=True)
    for p in ("a.png", "a.jpg", "a.webp", "a.unknown"):
        assert ap._resolve_out_fmt(p, None) is None, (
            "开关关闭时必须返回 None（= 交给既有默认），否则等于偷偷改了默认行为")


def test_explicit_format_always_wins(monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_FMT_FOLLOW_INPUT", True, raising=True)
    assert ap._resolve_out_fmt("a.jpg", "PNG") == "PNG"


def test_end_to_end_output_extension_follows_input(monkeypatch, tmp_path):
    """真跑一次 process_file：JPEG 进 → JPEG 出（开关开）；PNG 出（开关关）。"""
    import cv2
    src_png = SAMPLE_IMAGE_PATH
    img = cv2.imread(src_png)
    assert img is not None
    jpg = str(tmp_path / "wm.jpg")
    cv2.imwrite(jpg, img, [cv2.IMWRITE_JPEG_QUALITY, 95])

    monkeypatch.setattr(config, "OUTPUT_FMT_FOLLOW_INPUT", False, raising=True)
    os.makedirs(tmp_path / "off", exist_ok=True)   # process_file 不建 out_dir（CLI 层负责建）
    rd_off = ap.process_file(jpg, out_dir=str(tmp_path / "off"), engine="opencv")
    assert rd_off["status"] == "success", rd_off.get("error")
    assert rd_off["out_path"].lower().endswith(".png"), "关着时应保持既有 PNG 默认"

    monkeypatch.setattr(config, "OUTPUT_FMT_FOLLOW_INPUT", True, raising=True)
    os.makedirs(tmp_path / "on", exist_ok=True)
    rd_on = ap.process_file(jpg, out_dir=str(tmp_path / "on"), engine="opencv")
    assert rd_on["status"] == "success", rd_on.get("error")
    assert rd_on["out_path"].lower().endswith(".jpg"), (
        f"开着时 JPEG 输入应输出 JPEG，实得 {rd_on['out_path']}")
    import os as _os
    assert _os.path.getsize(rd_on["out_path"]) > 0


def test_cli_format_default_is_none_so_follow_input_can_apply():
    """回归（2026-10-23 实测）：CLI 的 `-f` 默认曾是 `config.DEFAULT_OUTPUT_FORMAT`，
    被 `_resolve_out_fmt` 当成"用户显式指定"⇒ **跟随输入永远不生效**（输出仍是 PNG）。
    改为 default=None 后，不传 -f 才由「跟随输入 / 配置默认」决定。
    """
    import cli
    p = cli._build_arg_parser()
    assert p.parse_args(["-i", "x"]).fmt is None, (
        "不传 -f 时必须为 None，否则开关被当成显式指定而失效")
    assert p.parse_args(["-i", "x", "-f", "PNG"]).fmt == "PNG"


@pytest.mark.timeout(420)   # 内层 subprocess timeout=300 ⇒ 外层必须更宽（tests/test_timeout_contract.py）
def test_cli_end_to_end_follows_input(tmp_path):
    """真跑 CLI：JPEG 进 → JPEG 出（开关开）；并确认此时体积提示**不出现**（已不胖）。"""
    import subprocess
    import cv2
    env = dict(os.environ, PYTHONUTF8="1", WME_OUTPUT_FMT_FOLLOW_INPUT="true")
    jpg = str(tmp_path / "wm.jpg")
    cv2.imwrite(jpg, cv2.imread(SAMPLE_IMAGE_PATH), [cv2.IMWRITE_JPEG_QUALITY, 95])
    out_dir = tmp_path / "out"
    r = subprocess.run([sys.executable, "cli.py", "-i", jpg, "-o", str(out_dir),
                        "--engine", "opencv"],
                       cwd=_ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    outs = [ln for ln in r.stdout.splitlines() if "_cleaned" in ln and "->" in ln]
    assert outs, f"CLI 未产出结果：{r.stdout[-400:]}"
    assert outs[0].split("->")[1].strip().lower().endswith(".jpg"), (
        f"跟随输入未生效，实得：{outs[0]}")
    assert "💡" not in r.stdout, "格式已经不胖时不应再提示换格式"
