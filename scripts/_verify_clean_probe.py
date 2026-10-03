"""阶段 180 · 独立验证 clean_bench_probe（绕过 pytest/tmp_path 的 %TEMP% 清理限制）。

复用 tests/test_clean_bench_probe.py 的四条断言语义，但用项目本地临时目录，
不触碰 %TEMP%，可在受限沙箱内本地验证。CI 仍跑正式 pytest 用例。
"""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from scripts import clean_bench_probe as cp  # noqa: E402

WORK = _ROOT / ".verify_tmp"
WORK.mkdir(parents=True, exist_ok=True)


def _write_clean(root: Path, *, with_text: bool):
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
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")


def _fresh(suffix: str) -> Path:
    d = WORK / suffix
    import shutil
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def main():
    fails = []

    # ① 跳过层
    d = _fresh("skip")
    (d / "manifest.json").write_text(json.dumps({"version": 1, "images": []}), encoding="utf-8")
    (d / "ground_truth.json").write_text(json.dumps({"version": 1, "entries": []}), encoding="utf-8")
    code, lines, rep = cp.run_clean_probe(str(d), max_fp_rate=0.0, gate_on="prior")
    if code != 2 or rep != {}:
        fails.append(f"skip层: code={code} rep={rep}")

    # ② 静默层（纯色干净图）
    d = _fresh("plain")
    _write_clean(d, with_text=False)
    code, lines, rep = cp.run_clean_probe(str(d), max_fp_rate=0.0, gate_on="prior")
    if not (code == 0 and rep["n"] == 1 and rep["prior_fp_rate"] == 0.0
            and rep["baseline_fp_rate"] == 0.0 and rep["total_on_fp_rate"] == 0.0):
        fails.append(f"静默层: {rep}")

    # ③ gate_on=total 在纯色图一致
    d = _fresh("plain2")
    _write_clean(d, with_text=False)
    code, lines, rep = cp.run_clean_probe(str(d), max_fp_rate=0.0, gate_on="total")
    if not (code == 0 and rep["total_on_fp_rate"] == 0.0):
        fails.append(f"total口径: {rep}")

    # ④ 阈值语义自洽（右下角文字图）
    d = _fresh("text")
    _write_clean(d, with_text=True)
    code, lines, rep = cp.run_clean_probe(str(d), max_fp_rate=0.0, gate_on="prior")
    rate = rep["prior_fp_rate"]
    if rate > 0.0 and code != 1:
        fails.append(f"阈值语义: rate>0 但 code={code}")
    if rate == 0.0 and code != 0:
        fails.append(f"阈值语义: rate=0 但 code={code}")

    # ⑤ 阶段 181：同进程两次测量一致（冷启动由 warmup 吸收）
    d = _fresh("det")
    _write_clean(d, with_text=True)
    _, _, r1 = cp.run_clean_probe(str(d), max_fp_rate=0.0, gate_on="prior")
    _, _, r2 = cp.run_clean_probe(str(d), max_fp_rate=0.0, gate_on="prior")
    if not (r1["prior_fp_rate"] == r2["prior_fp_rate"]
            and r1["baseline_fp_rate"] == r2["baseline_fp_rate"]):
        fails.append(f"确定性: 两次测量不一致 {r1} vs {r2}")

    if fails:
        print("VERIFY FAIL")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)
    print("VERIFY PASS · 5/5 断言通过（跳过层 / 静默层 / total口径 / 阈值语义 / 确定性）")


if __name__ == "__main__":
    main()
