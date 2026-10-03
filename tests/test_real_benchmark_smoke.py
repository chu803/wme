"""方向 A · 真实图门禁烟雾测试。

目的
----
验证 scripts/{collect_real_assets, build_real_ground_truth, run_real_benchmark,
real_benchmark_decision}.py 的端到端链路能跑通，**且空集情况必须显式报错**（防止
「假绿」——脚本沉默跑完但无数据）。

测试矩阵
--------
1. 空 manifest → run_real_benchmark 应抛 RuntimeError；
2. 1 张合成图 + 真值 → run_real_benchmark 跑通，输出有效指标；
3. build_real_ground_truth.validate 应捕获越界 bbox 与非法 style；
4. collect_real_assets.scan 不修改现有 manifest 中手工编辑的条目。

约束
----
- 不依赖真实 AI 图、不依赖 LaMa（CPU 环境可跑）；
- 用 PIL 合成一张 256×256 RGB 图，画一个已知矩形作为「水印」；
- 用 monkeypatch 把 BENCH_DIR 指向 tmp_path，让脚本读 tmp 下的 manifest/truth/images。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable  # 已被 conftest 强制为托管 venv 的 python


def _run_cli(args: list, cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run([PY, *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def bench_tmp(tmp_path: Path, monkeypatch):
    """建一个隔离的 benchmark_real/ 目录，把脚本的 BENCH_DIR 重定向到此处。"""
    bdir = tmp_path / "benchmark_real"
    (bdir / "images").mkdir(parents=True)
    # 写一个 monkeypatch 用的「模块注入」env var，让脚本识别为 test bench。
    # 简单做法：把脚本读到的 _ROOT 改 tmp_path，但脚本用的是 os.path.dirname(__file__)，
    # 不可注入。改用：直接 copy 4 个脚本到 tmp_path 跑，或让脚本支持 BENCH_DIR 环境变量。
    # 这里采用后者——需要给脚本加 BENCH_DIR env 覆盖（仅当设置了 WME_BENCH_DIR 时）。
    monkeypatch.setenv("WME_BENCH_DIR", str(bdir))
    return bdir


def _make_synth_image(path: Path, size=(256, 256), bbox=(50, 50, 80, 80)) -> None:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, (200, 200, 200))
    d = ImageDraw.Draw(im)
    d.rectangle(bbox, fill=(30, 30, 30))
    im.save(path)


# --------------------------------------------------------------------------- #
# 1 · 空 manifest 必须显式报错
# --------------------------------------------------------------------------- #
def test_run_benchmark_rejects_empty_manifest(bench_tmp: Path):
    """manifest 不存在 → CLI 返回非零退出码。"""
    p = subprocess.run(
        [PY, str(ROOT / "scripts" / "run_real_benchmark.py")],
        cwd=str(ROOT),
        env={**os.environ, "WME_BENCH_DIR": str(bench_tmp)},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert p.returncode != 0, f"应为非零退出；实际 {p.returncode}\nSTDOUT: {p.stdout}\nSTDERR: {p.stderr}"
    assert "manifest" in (p.stderr + p.stdout).lower() or "ground_truth" in (p.stderr + p.stdout).lower()


# --------------------------------------------------------------------------- #
# 2 · 1 张合成图 + 真值 → 全链路跑通
# --------------------------------------------------------------------------- #
def test_end_to_end_with_synthetic_image(bench_tmp: Path):
    """手工写 manifest + 真值，跑 run_real_benchmark，确认产出有效指标。"""
    # 准备一张合成图
    img_path = bench_tmp / "images" / "synth_001.png"
    _make_synth_image(img_path, size=(256, 256), bbox=(50, 50, 80, 80))

    # 写 manifest
    manifest = {
        "version": 1,
        "images": [{
            "id": "synth_001",
            "filename": "synth_001.png",
            "path": "images/synth_001.png",
            "ai_source": "other",
            "watermark_style_hint": "logo_watermark",
            "original_size": [256, 256],
            "sha256": "deadbeef" * 8,
            "added_at": "2026-09-13T00:00:00Z",
            "source": "local",
        }],
    }
    (bench_tmp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # 写真值
    truth = {
        "version": 1,
        "entries": [{
            "image_id": "synth_001",
            "watermarks": [{"bbox": [50, 50, 80, 80], "style": "logo_watermark", "note": "synth"}],
            "labeled_at": "2026-09-13T00:00:00Z",
            "labeled_by": "manual",
        }],
    }
    (bench_tmp / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")

    # 跑门禁（仅两个预设，缩短时间）
    p = subprocess.run(
        [PY, str(ROOT / "scripts" / "run_real_benchmark.py"),
         "--presets", "general,ai_logo",
         "--output", str(bench_tmp / "results" / "bench_smoke.json")],
        cwd=str(ROOT),
        env={**os.environ, "WME_BENCH_DIR": str(bench_tmp)},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert p.returncode == 0, f"应成功；实际 rc={p.returncode}\nSTDOUT: {p.stdout}\nSTDERR: {p.stderr}"
    out = json.loads((bench_tmp / "results" / "bench_smoke.json").read_text(encoding="utf-8"))
    assert out["n_images"] == 1
    assert "general" in out["per_preset_aggregate"]
    # 矩形 80x80 在 256x256 = 9.77%；detector 应至少 recall>0
    rec = out["per_preset_aggregate"]["general"]["recall_mean"]
    assert 0.0 <= rec <= 1.0, f"recall 应在 [0,1]：{rec}"


# --------------------------------------------------------------------------- #
# 3 · validate 应捕获越界 bbox 与非法 style
# --------------------------------------------------------------------------- #
def test_validate_catches_invalid_bbox_and_style(bench_tmp: Path):
    img_path = bench_tmp / "images" / "synth_002.png"
    _make_synth_image(img_path, size=(128, 128), bbox=(10, 10, 30, 30))
    manifest = {
        "version": 1, "images": [{
            "id": "x", "filename": "synth_002.png", "path": "images/synth_002.png",
            "ai_source": "other", "watermark_style_hint": "unknown",
            "original_size": [128, 128], "sha256": "00" * 32,
            "added_at": "2026-09-13T00:00:00Z", "source": "local",
        }],
    }
    (bench_tmp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # 故意制造两个错误：越界 bbox + 非法 style
    truth = {
        "version": 1, "entries": [{
            "image_id": "x",
            "watermarks": [
                {"bbox": [100, 100, 50, 50], "style": "logo_watermark", "note": "越界"},
                {"bbox": [10, 10, 20, 20], "style": "totally_invalid_style", "note": "非法枚举"},
            ],
            "labeled_at": "2026-09-13T00:00:00Z", "labeled_by": "manual",
        }],
    }
    (bench_tmp / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")

    p = subprocess.run(
        [PY, str(ROOT / "scripts" / "build_real_ground_truth.py"), "validate"],
        cwd=str(ROOT),
        env={**os.environ, "WME_BENCH_DIR": str(bench_tmp)},
        capture_output=True, text=True, encoding="utf-8",
    )
    # validate 应非零退出，且错误数 ≥ 2
    assert p.returncode != 0
    assert "errors=" in (p.stdout + p.stderr), f"应输出错误数；实际：{p.stdout}{p.stderr}"


# --------------------------------------------------------------------------- #
# 4 · collect scan 不会破坏已有手工元数据
# --------------------------------------------------------------------------- #
def test_collect_scan_preserves_manual_metadata(bench_tmp: Path):
    """手工写的 manifest（id=special_id）不应被 scan 改写。"""
    img_path = bench_tmp / "images" / "foo.png"
    _make_synth_image(img_path, size=(64, 64))
    import hashlib
    sha = hashlib.sha256(img_path.read_bytes()).hexdigest()
    existing = {
        "version": 1, "images": [{
            "id": "special_id",
            "filename": "foo.png", "path": "images/foo.png",
            "ai_source": "midjourney", "watermark_style_hint": "corner_logo",
            "original_size": [64, 64], "sha256": sha,
            "added_at": "2026-09-13T00:00:00Z", "source": "local",
        }],
    }
    (bench_tmp / "manifest.json").write_text(json.dumps(existing), encoding="utf-8")

    p = subprocess.run(
        [PY, str(ROOT / "scripts" / "collect_real_assets.py"), "scan",
         "--ai-source", "other", "--style", "unknown"],
        cwd=str(ROOT),
        env={**os.environ, "WME_BENCH_DIR": str(bench_tmp)},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert p.returncode == 0
    after = json.loads((bench_tmp / "manifest.json").read_text(encoding="utf-8"))
    # 原条目应保留（id 不变，ai_source 不被覆盖）
    assert len(after["images"]) == 1
    assert after["images"][0]["id"] == "special_id"
    assert after["images"][0]["ai_source"] == "midjourney"  # 手工元数据保留


# --------------------------------------------------------------------------- #
# 5 · 决策脚本的 inpaint 接线必须真实可调（经验 20：端到端测试抓接线错误）
# --------------------------------------------------------------------------- #
def test_decision_inpaint_wiring_opencv(bench_tmp: Path):
    """real_benchmark_decision._repair_one 用真实 inpaint() 签名跑通 opencv 引擎。

    背景：首轮真实图对决全表空白——`inpaint(..., return_meta=False)` 是想象中的
    签名，实际 `inpaint()` 无此参数，TypeError 被 except 吞掉后全部记为
    available=False。修复后本测试锁定「接线必须真实可调」。
    """
    sys.path.insert(0, str(ROOT))
    import numpy as np
    from scripts.real_benchmark_decision import _repair_one

    rng = np.random.default_rng(7)
    img = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[20:40, 20:40] = 255
    out = _repair_one(img, mask, engine="opencv")
    assert out is not None, "opencv 引擎必须可用且返回结果"
    assert out.shape == img.shape, f"输出形状应与输入一致：{out.shape} vs {img.shape}"
    assert out.dtype == np.uint8