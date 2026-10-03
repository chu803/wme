"""阶段 180 · 干净图误报探针（clean_bench_probe）守卫测试。

锁定四层：
  ① 跳过层：无 clean:true 条目 → 退出码 2（不污染其它门禁）；
  ② 静默层：干净图（无右下角信号）上杠杆 ON/OFF 均不误发 corner_logo → prior_fp_rate=0、退出码 0；
  ③ 归因隔离层：run_clean_probe 同时测 ON/OFF，prior_fp_rate = (ON−OFF) 误发占比；
  ④ 门禁口径层：gate_on='prior' 与 'total' 分别按 179 归因 / 总误报率判定。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import clean_bench_probe as cp  # noqa: E402


def _write_clean(root: Path, *, with_text: bool) -> None:
    (root / "images").mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1024, 1024), (200, 200, 200))
    if with_text:
        dr = ImageDraw.Draw(img, "RGBA")
        dr.rectangle([700, 880, 1000, 1000], fill=(20, 20, 20, 255))
        dr.text((710, 890), "SAMPLE", fill=(235, 235, 235, 255),
                font=ImageFont.load_default())
    img.save(root / "images" / "c.png")
    manifest = {"version": 1, "images": [
        {"id": "c", "filename": "c.png", "path": "images/c.png",
         "ai_source": "synthetic", "watermark_style_hint": "none", "clean": True,
         "original_size": [1024, 1024], "sha256": "", "source": "synthetic"}]}
    truth = {"version": 1, "entries": [
        {"image_id": "c", "watermarks": [], "labeled_at": "x", "labeled_by": "t"}]}
    json.dump(manifest, open(root / "manifest.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump(truth, open(root / "ground_truth.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def test_skip_when_no_clean_images(tmp_path):
    """无 clean:true 条目 → 退出码 2、不崩溃。"""
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "images": []}),
                                            encoding="utf-8")
    (tmp_path / "ground_truth.json").write_text(json.dumps({"version": 1, "entries": []}),
                                                encoding="utf-8")
    code, lines, rep = cp.run_clean_probe(str(tmp_path), max_fp_rate=0.0, gate_on="prior")
    assert code == 2, lines
    assert rep == {}


def test_plain_clean_image_no_spurious_plate(tmp_path):
    """纯色干净图：杠杆 ON/OFF 均不误发 corner_logo → prior_fp_rate=0、退出 0。"""
    _write_clean(tmp_path, with_text=False)
    code, lines, rep = cp.run_clean_probe(str(tmp_path), max_fp_rate=0.0, gate_on="prior")
    assert code == 0, "\n".join(lines)
    assert rep["n"] == 1
    assert rep["baseline_fp_rate"] == 0.0
    assert rep["prior_fp_rate"] == 0.0
    assert rep["total_on_fp_rate"] == 0.0
    assert rep["passed"] is True


def test_gate_on_total_matches_prior_on_plain(tmp_path):
    """纯色干净图上 total 与 prior 口径一致（均为 0）。"""
    _write_clean(tmp_path, with_text=False)
    code, lines, rep = cp.run_clean_probe(str(tmp_path), max_fp_rate=0.0, gate_on="total")
    assert code == 0
    assert rep["total_on_fp_rate"] == 0.0


def test_threshold_semantics_on_text_image(tmp_path):
    """右下角文字干净图：先验口径只验证「rate>0 ⇔ 未通过」的语义一致性，不绑定环境。

    右下角文字是否触发 edge_text 信号与运行环境相关；本测试只保证门禁阈值语义自洽，
    不把环境差异当回归。
    """
    _write_clean(tmp_path, with_text=True)
    code, lines, rep = cp.run_clean_probe(str(tmp_path), max_fp_rate=0.0, gate_on="prior")
    rate = rep["prior_fp_rate"]
    assert rate >= 0.0
    if rate > 0.0:
        assert code == 1          # 超阈值 → 未通过
    else:
        assert code == 0          # 阈值内 → 通过


def test_probe_deterministic_within_process(tmp_path):
    """阶段 181：同一干净图集在同进程内两次测量应一致（冷启动由 warmup 吸收）。

    防止「检测器冷启动伪影」或未来引入的真实非确定回归。只断言两次结果相等，
    不绑定具体数值（环境相关）。
    """
    _write_clean(tmp_path, with_text=True)
    _, _, rep1 = cp.run_clean_probe(str(tmp_path), max_fp_rate=0.0, gate_on="prior")
    _, _, rep2 = cp.run_clean_probe(str(tmp_path), max_fp_rate=0.0, gate_on="prior")
    assert rep1["prior_fp_rate"] == rep2["prior_fp_rate"]
    assert rep1["baseline_fp_rate"] == rep2["baseline_fp_rate"]
    assert rep1["total_on_fp_rate"] == rep2["total_on_fp_rate"]
