# -*- coding: utf-8 -*-
"""阶段 379：发布前「安装态布局」自检器的判定与接线守卫。

真缺陷（1.0.4）：源码态与静态页面全绿，装出来的 wheel 却把 site-packages
当数据目录，Gradio 回传结果失败。本守卫确保：
  · 判定函数对旧布局 / 版本不符 / 数据根错位都会红（反例齐全）；
  · wheel 选择与版本解析有明确语义；
  · publish.yml 真的在发布前调用它（否则又是一条永不生效的闸）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "verify_installed_layout379", ROOT / "scripts" / "verify_installed_layout.py"
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def _info(tmp_path: Path, **over) -> dict:
    data = tmp_path / "data"
    info = {
        "version": "1.0.5",
        "project_root": str(tmp_path / "pkg"),
        "data_root": str(data),
        "output_dir": str(data / "outputs"),
        "model_dir": str(data / "models"),
    }
    info.update(over)
    return info


def test_installed_layout_passes(tmp_path):
    assert mod.problems_for(_info(tmp_path), tmp_path / "data", "1.0.5") == []


def test_legacy_layout_without_data_root_is_flagged(tmp_path):
    problems = mod.problems_for(_info(tmp_path, data_root=""), tmp_path / "data", "1.0.5")
    assert any("DATA_ROOT" in p for p in problems), problems


def test_version_mismatch_is_flagged(tmp_path):
    problems = mod.problems_for(_info(tmp_path, version="1.0.4"), tmp_path / "data", "1.0.5")
    assert any("版本不一致" in p for p in problems), problems


def test_data_root_inside_package_is_flagged(tmp_path):
    pkg = tmp_path / "pkg"
    problems = mod.problems_for(
        _info(tmp_path, data_root=str(pkg / "data")), pkg / "data", "1.0.5"
    )
    assert any("安装目录内" in p for p in problems), problems


def test_output_outside_data_root_is_flagged(tmp_path):
    problems = mod.problems_for(
        _info(tmp_path, output_dir=str(tmp_path / "elsewhere")), tmp_path / "data", "1.0.5"
    )
    assert any("output_dir" in p for p in problems), problems


def test_wheel_version_is_parsed_from_filename():
    wheel = Path("wme_watermark_eraser-1.0.5-py3-none-any.whl")
    assert mod._wheel_version(wheel) == "1.0.5"


def test_pick_wheel_requires_exactly_one(tmp_path):
    (tmp_path / "a-1.0.5.whl").write_bytes(b"x")
    (tmp_path / "b-1.0.5.whl").write_bytes(b"x")
    try:
        mod._pick_wheel(str(tmp_path / "*.whl"))
    except SystemExit as exc:
        assert "多个" in str(exc)
    else:
        raise AssertionError("多个 wheel 时必须拒绝自动选择")

    picked = mod._pick_wheel(str(tmp_path / "a-*.whl"))
    assert picked.name == "a-1.0.5.whl"


def test_publish_workflow_runs_installed_layout_guard():
    text = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "python scripts/verify_installed_layout.py --wheel" in text, (
        "发布链没有跑安装态布局自检，1.0.4 的缺陷可以再次上线"
    )
    assert "id: installed_layout" in text, "该步骤需要 id，失败诊断才读得到"
