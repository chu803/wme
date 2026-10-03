"""阶段 153 · `validate` 与 `preflight` 必须对同一真值文件给出**一致**判定。

背景（阶段 153 真值审计发现）
-----------------------------
`build_real_ground_truth.py validate` 与 `real_asset_preflight.py` 都在「校验
`ground_truth.json`」，但**各自实现了一套检查**：

  · `validate`：孤儿条目只 `[warn]`（不判错）、不查 `labeled_by`、不查空
    `watermarks`、不识别 `auto:` 预填、`bbox` 是否在图内是它独有的检查。
  · `preflight`：以上全查（且 `auto:` 视为硬失败）。

于是同一个真值文件会出现 **`validate` 绿（`errors=0`）而 `preflight` 红（rc=1）**
的工具矛盾——而 `preflight` 才是承诺面门禁，用户会被 `validate` 的绿灯误导
（以为 P0 已闭合，其实没有）。

修法：把「bbox 非负 / 在图内」上收到 `real_asset_preflight.audit_truth`，
并抽出 `structural_problems()` 作为**单一实现**，两个工具都调它。

本文件的守卫，就是钉住这条**同判契约**：对同一份畸形真值，两个工具**要么都红、
要么都绿**；干净真值则两者都绿。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PY = sys.executable  # 已被 conftest 强制为托管 venv 的 python

from scripts import real_asset_preflight as pf  # noqa: E402


# --------------------------------------------------------------------------- #
# 夹具与工具
# --------------------------------------------------------------------------- #
_MANIFEST = {"version": 1, "images": [{
    "id": "img1", "filename": "img1.png", "path": "images/img1.png",
    "ai_source": "other", "watermark_style_hint": "unknown",
    "original_size": [512, 512], "sha256": "a" * 64,
    "added_at": "2026-09-28T00:00:00Z", "source": "local",
}]}

_VALID = {"image_id": "img1",
          "watermarks": [{"bbox": [10, 10, 20, 20], "style": "corner_logo", "note": ""}],
          "labeled_at": "2026-09-28T00:00:00Z", "labeled_by": "manual:measured"}


def _variant(**over) -> dict:
    e = json.loads(json.dumps(_VALID))
    e.update(over)
    return e


def _write(tmp: Path, entries) -> None:
    (tmp / "images").mkdir(parents=True, exist_ok=True)
    (tmp / "manifest.json").write_text(json.dumps(_MANIFEST), encoding="utf-8")
    (tmp / "ground_truth.json").write_text(
        json.dumps({"version": 1, "entries": entries}), encoding="utf-8")


def _rc(tmp: Path, script: str, *args) -> tuple:
    env = {**os.environ, "WME_BENCH_DIR": str(tmp)}
    r = subprocess.run([PY, str(ROOT / "scripts" / script), *args],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout + r.stderr


# --------------------------------------------------------------------------- #
# 1 · 单一实现层：`audit_truth` 必须查出「bbox 越界」
# --------------------------------------------------------------------------- #
def test_audit_truth_detects_bbox_out_of_bounds():
    """「bbox 在图内」原先只有 `validate` 查；阶段 153 上收到 `audit_truth`。"""
    m = {"images": [{"id": "img1", "filename": "a.png", "path": "images/a.png",
                     "sha256": "x", "original_size": [512, 512]}]}
    t = {"entries": [{"image_id": "img1",
                      "watermarks": [{"bbox": [500, 500, 50, 50], "style": "corner_logo"}],
                      "labeled_by": "manual:measured"}]}
    problems = pf.audit_truth(m, t)
    assert any("超出图像" in p for p in problems), problems


# --------------------------------------------------------------------------- #
# 2 · 同判契约：畸形真值 → 两工具都必须红
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,entries", [
    ("孤儿条目", [dict(_VALID, image_id="ghost")]),
    ("缺 labeled_by", [{k: v for k, v in _VALID.items() if k != "labeled_by"}]),
    ("空 watermarks", [_variant(watermarks=[])]),
    ("auto 预填", [_variant(labeled_by="auto:general")]),
    ("bbox 越界", [_variant(watermarks=[{"bbox": [500, 500, 50, 50], "style": "corner_logo"}])]),
])
def test_validate_and_preflight_agree_on_malformed_truth(tmp_path: Path, name, entries):
    _write(tmp_path, entries)
    vrc, vout = _rc(tmp_path, "build_real_ground_truth.py", "validate")
    prc, pout = _rc(tmp_path, "real_asset_preflight.py")
    assert prc != 0, f"[{name}] preflight 应拒绝：\n{pout}"
    assert vrc != 0, (
        f"[{name}] validate 必须与 preflight 同判（不得绿灯）——"
        f"否则用户被 validate 误导。\nvalidate 输出：\n{vout}")


# --------------------------------------------------------------------------- #
# 3 · 反面对照：干净真值 → 两工具都必须绿（防止「一律判红」的假修复）
# --------------------------------------------------------------------------- #
def test_validate_and_preflight_both_green_on_clean_truth(tmp_path: Path):
    _write(tmp_path, [_VALID])
    vrc, vout = _rc(tmp_path, "build_real_ground_truth.py", "validate")
    prc, pout = _rc(tmp_path, "real_asset_preflight.py")
    assert vrc == 0, f"干净真值 validate 应绿：\n{vout}"
    assert prc == 0, f"干净真值 preflight 应绿：\n{pout}"
