"""阶段 160 · `scripts/make_real_compare_crops.py` 取景/面板契约守卫。

背景
----
该脚本是**人眼复核通道**（docstring 自述：「数值指标会骗人……数值给方向，肉眼给结论」），
此前**零专属测试**。合成色块 + 桩 inpaint 的探针确认主路径（三格顺序 / 面板着色 / 过滤 /
退出码）by design 正确，但抓到一处静默误导：

- **取景可能切掉被修复区域**：mask 用 `--pad` 外扩、crop 用 `--view` 外扩，二者相互独立且无
  `crop ⊇ mask` 约束。当 `view < pad`（而 `--pad` 帮助文本又写着「掩码与取景同步」，正好诱导）时，
  crop 会**整幅落在 mask 内** ⇒ 对比图里既没有水印也没有上下文，肉眼复核被静默误导，退出码仍为 0。
  探针实测（pad=40, view=10）：crop 内落在 mask 的列比例 = **1.00**（正常应 < 1，两翼各留原图上下文）。

修复（阶段 160）
----------------
强制 `eff_view = max(view, pad)`（保证 `crop ⊇ mask`），并在被夹紧时**显式告警**；
订正 `--pad` / `--view` 的帮助文本（原文「掩码与取景同步」与实现不符）。

守卫口径：**从产出的 composite 尺寸黑盒反推 crop 窗口**，断言 `crop ⊇ mask`，而不是复述实现内部。
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import make_real_compare_crops as mrc  # noqa: E402

SAVED: dict = {}

#: 合成图与真值 bbox（各测试共用）
H, W = 120, 160
BX, BY, BW, BH = 40, 20, 30, 25


@pytest.fixture
def bench(tmp_path: Path, monkeypatch):
    """隔离 benchmark_real/ + 桩 core.image_utils / core.inpainter（不加载 torch）。"""
    core = types.ModuleType("core")
    core.__path__ = []
    iu = types.ModuleType("core.image_utils")

    def load_image(p):
        from PIL import Image
        return np.array(Image.open(p).convert("RGB"))

    def save_image(p, arr):
        from PIL import Image
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Image.fromarray(arr.astype(np.uint8)).save(p)
        SAVED[os.path.basename(p)] = arr.copy()

    iu.load_image = load_image
    iu.save_image = save_image

    ip = types.ModuleType("core.inpainter")

    def inpaint(img, mask, engine="lama"):
        out = img.copy()
        out[mask > 0] = (0, 255, 0) if engine == "opencv" else (0, 0, 255)
        return out

    ip.inpaint = inpaint
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.image_utils", iu)
    monkeypatch.setitem(sys.modules, "core.inpainter", ip)

    bdir = tmp_path / "benchmark_real"
    (bdir / "images").mkdir(parents=True)
    img = np.full((H, W, 3), 100, np.uint8)
    img[BY:BY + BH, BX:BX + BW] = (255, 0, 0)
    from PIL import Image
    Image.fromarray(img).save(bdir / "images" / "t1.png")
    (bdir / "manifest.json").write_text(json.dumps({"version": 1, "images": [
        {"id": "t1", "filename": "t1.png", "path": "images/t1.png", "ai_source": "other",
         "watermark_style_hint": "corner_logo", "original_size": [W, H],
         "sha256": "x", "added_at": "t", "source": "local"}]}), encoding="utf-8")
    (bdir / "ground_truth.json").write_text(json.dumps({"version": 1, "entries": [
        {"image_id": "t1", "watermarks": [{"bbox": [BX, BY, BW, BH], "style": "corner_logo"}],
         "labeled_at": "t", "labeled_by": "manual:test"}]}), encoding="utf-8")

    monkeypatch.setattr(mrc, "BENCH_DIR", str(bdir))
    monkeypatch.setattr(mrc, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(mrc, "TRUTH_PATH", str(bdir / "ground_truth.json"))
    SAVED.clear()
    return bdir


def _run(monkeypatch, bdir: Path, argv):
    out = str(bdir / "results" / "visual")
    monkeypatch.setattr(sys, "argv", ["prog", "--out-dir", out, *argv])
    return mrc.main()


def _mask_window(pad):
    mx0, my0 = max(0, BX - pad), max(0, BY - pad)
    mx1, my1 = min(W, BX + BW + pad), min(H, BY + BH + pad)
    return mx0, my0, mx1, my1


def _panel_dims(comp):
    """从 composite 反推单格 (w, h)。"""
    wp = (comp.shape[1] - 2 * mrc.GAP) // 3
    return wp, comp.shape[0]


def _px(comp, y, x):
    return tuple(int(v) for v in comp[y, x])


# --------------------------------------------------------------------------- #
# 1 · 核心：view < pad 不得切掉被修复区域（crop ⊇ mask）
# --------------------------------------------------------------------------- #
def test_view_lt_pad_never_cuts_off_mask(bench, monkeypatch, capsys):
    rc = _run(monkeypatch, bench, ["--pad", "40", "--view", "10"])
    assert rc == 0
    comp = SAVED["t1_0_corner_logo.png"]
    wp, hp = _panel_dims(comp)
    mx0, my0, mx1, my1 = _mask_window(40)
    mask_w, mask_h = mx1 - mx0, my1 - my0
    # 关键断言：crop（面板）必须不窄于 mask，否则被修复区域被切掉
    assert wp >= mask_w, f"crop 宽 {wp} < mask 宽 {mask_w}（被修复区域被切掉）"
    assert hp >= mask_h, f"crop 高 {hp} < mask 高 {mask_h}"
    # 旧实现：wp == 2*10 + 30 = 50 < 110 ⇒ 必红
    assert wp != 50, "view<pad 时未夹紧（旧行为：crop 比 mask 窄）"


def test_view_lt_pad_warns(bench, monkeypatch, capsys):
    _run(monkeypatch, bench, ["--pad", "40", "--view", "10"])
    err = capsys.readouterr().err
    assert "[warn]" in err and "夹紧" in err, "夹紧取景必须显式告警（否则是静默改尺寸）"


def test_view_ge_pad_no_warn(bench, monkeypatch, capsys):
    _run(monkeypatch, bench, ["--pad", "24", "--view", "70"])
    assert "[warn]" not in capsys.readouterr().err, "view ≥ pad 时不应告警"


# --------------------------------------------------------------------------- #
# 2 · 默认合理参数：两翼皆有原图上下文
# --------------------------------------------------------------------------- #
def test_default_crop_has_context_both_sides(bench, monkeypatch):
    _run(monkeypatch, bench, ["--pad", "24", "--view", "70"])
    comp = SAVED["t1_0_corner_logo.png"]
    wp, _ = _panel_dims(comp)
    mx0, _, mx1, _ = _mask_window(24)
    assert wp > (mx1 - mx0), "默认参数下 crop 必须比 mask 宽（两翼留上下文）"


# --------------------------------------------------------------------------- #
# 3 · 面板顺序与着色（人眼复核的核心契约）
# --------------------------------------------------------------------------- #
def test_panel_order_and_colors(bench, monkeypatch):
    _run(monkeypatch, bench, ["--pad", "24", "--view", "70"])
    comp = SAVED["t1_0_corner_logo.png"]
    wp, _ = _panel_dims(comp)
    g = mrc.GAP

    def cnt(x0, x1, rgb):
        sub = comp[:, x0:x1]
        return int(np.all(sub == np.array(rgb, np.uint8), axis=2).sum())

    # 用「精确色像素计数」而非单点采样——面板左上角有 _label 文字（抗锯齿）会盖住部分像素
    assert cnt(0, wp, (255, 0, 0)) > 0, "orig 面板应含红色水印"
    assert cnt(wp + g, 2 * wp + g, (0, 255, 0)) > 0, "opencv 面板应含绿色（被修复区）"
    assert cnt(2 * (wp + g), 3 * wp + 2 * g, (0, 0, 255)) > 0, "lama 面板应含蓝色（被修复区）"
    # 交叉验证：修好的一侧不该混入别侧的色
    assert cnt(0, wp, (0, 255, 0)) == 0, "orig 面板不应出现修复绿"
    assert cnt(2 * (wp + g), 3 * wp + 2 * g, (255, 0, 0)) == 0, "lama 面板不应出现原图红"
    # 远离水印处为灰色背景
    assert _px(comp, 5, 5) == (100, 100, 100)


# --------------------------------------------------------------------------- #
# 4 · 过滤：--ids / --limit
# --------------------------------------------------------------------------- #
def _add_second_image(bdir: Path):
    img2 = np.full((H, W, 3), 100, np.uint8)
    img2[BY:BY + BH, BX:BX + BW] = (0, 128, 0)
    from PIL import Image
    Image.fromarray(img2).save(bdir / "images" / "t2.png")
    mf = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    mf["images"].append({"id": "t2", "filename": "t2.png", "path": "images/t2.png",
                         "ai_source": "other", "watermark_style_hint": "edge_text",
                         "original_size": [W, H], "sha256": "y", "added_at": "t",
                         "source": "local"})
    (bdir / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")
    gt = json.loads((bdir / "ground_truth.json").read_text(encoding="utf-8"))
    gt["entries"].append({"image_id": "t2",
                          "watermarks": [{"bbox": [BX, BY, BW, BH], "style": "edge_text"}],
                          "labeled_at": "t", "labeled_by": "manual:test"})
    (bdir / "ground_truth.json").write_text(json.dumps(gt), encoding="utf-8")


def _ids():
    return sorted({k.split("_")[0] for k in SAVED if k.endswith(".png") and "sheet" not in k})


def test_ids_filter(bench, monkeypatch):
    _add_second_image(bench)
    SAVED.clear()
    _run(monkeypatch, bench, ["--ids", "t2"])
    assert _ids() == ["t2"]


def test_limit_filter(bench, monkeypatch):
    _add_second_image(bench)
    SAVED.clear()
    _run(monkeypatch, bench, ["--limit", "1"])
    assert _ids() == ["t1"]


# --------------------------------------------------------------------------- #
# 5 · 退出码与总览图
# --------------------------------------------------------------------------- #
def test_missing_files_returns_1(tmp_path, monkeypatch):
    bdir = tmp_path / "benchmark_real"
    bdir.mkdir(parents=True)
    monkeypatch.setattr(mrc, "BENCH_DIR", str(bdir))
    monkeypatch.setattr(mrc, "MANIFEST_PATH", str(bdir / "manifest.json"))
    monkeypatch.setattr(mrc, "TRUTH_PATH", str(bdir / "ground_truth.json"))
    assert _run(monkeypatch, bdir, []) == 1


def test_empty_truth_returns_2(bench, monkeypatch):
    (bench / "ground_truth.json").write_text(
        json.dumps({"version": 1, "entries": []}), encoding="utf-8")
    assert _run(monkeypatch, bench, []) == 2


def test_sheet_generated(bench, monkeypatch):
    SAVED.clear()
    _run(monkeypatch, bench, ["--sheet"])
    assert "contact_sheet.png" in SAVED


# --------------------------------------------------------------------------- #
# 6 · 帮助文本必须与实现一致（曾是「掩码与取景同步」的误导）
# --------------------------------------------------------------------------- #
def test_help_text_matches_implementation(bench, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        monkeypatch.setattr(sys, "argv", ["prog", "--help"])
        mrc.main()
    out = capsys.readouterr().out
    assert "掩码与取景同步" not in out, "帮助文本不得再声称 pad 与取景同步（实现已分离 view）"
    assert "--view" in out and "--pad" in out
