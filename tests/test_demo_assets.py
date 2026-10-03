"""
scripts/make_demo_assets.py —— 演示素材生成与擦除质量基准 的单元测试。

覆盖四层：
  1. make_base：确定性（同 seed 必得同图）、尺寸/类型正确、跨 seed 有差异。
  2. 四类水印（faint_tiled / corner_logo / edge_text / grid）：与底图不同、
     尺寸不变、彼此互不相同（确保四种用例确实在测四种东西）。
  3. psnr / mae 数学正确性（含「完全一致 → PSNR=100」边界）。
  4. run_case 端到端：corner_logo + opencv 引擎应产出带全部键的指标字典，
     且四联对比图尺寸正确（(2h+40) × (2w+30)）。

说明：run_case 会真实调用检测器与 OpenCV 修复引擎（零 torch），
      corner_logo 在基准中覆盖率约 6%，检测稳定，适合做端到端冒烟。
"""

import os as _os
import sys as _sys

import numpy as np
import pytest

# 项目根目录注入 sys.path（保证 core / utils 可导入）。
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

# scripts 目录注入 sys.path（保证 make_demo_assets 可导入）。
_SCRIPTS = _os.path.join(_ROOT, "scripts")
if _SCRIPTS not in _sys.path:
    _sys.path.insert(0, _SCRIPTS)

import make_demo_assets as mda  # noqa: E402


# ---------------------------------------------------------------------------
# 1. make_base —— 合成底图
# ---------------------------------------------------------------------------

def test_make_base_deterministic():
    a = mda.make_base(seed=7)
    b = mda.make_base(seed=7)
    assert np.array_equal(a, b), "同一 seed 必须得到完全相同的底图（可复现）"


def test_make_base_shape_and_dtype():
    img = mda.make_base(seed=1)
    assert img.shape == (640, 640, 3)
    assert img.dtype == np.uint8
    assert img.min() >= 0 and img.max() <= 255


def test_make_base_varies_across_seeds():
    a = mda.make_base(seed=1)
    b = mda.make_base(seed=2)
    assert not np.array_equal(a, b), "不同 seed 应产生不同底图"


# ---------------------------------------------------------------------------
# 2. 四类水印 —— 确实在测四种东西
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base128():
    return mda.make_base(seed=7, w=160, h=160)


def test_each_watermark_changes_image(base128):
    """每种水印都要实际改动底图像素（否则用例没有意义）。"""
    for name, (fn, _, _) in mda.CASES.items():
        out = fn(base128)
        assert out.shape == base128.shape
        assert not np.array_equal(out, base128), f"{name} 水印未改变图像"


def test_watermarks_are_distinct(base128):
    """四种水印彼此不同，保证每个用例在测不同模式。"""
    outs = {name: fn(base128) for name, (fn, _, _) in mda.CASES.items()}
    names = list(outs)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert not np.array_equal(outs[names[i]], outs[names[j]]), \
                f"{names[i]} 与 {names[j]} 产生相同图像"


def test_cases_map_to_presets():
    """每个用例都必须绑定一个真实的检测预设。"""
    for name, (_, preset, _) in mda.CASES.items():
        assert preset.startswith("ai_"), f"{name} 的预设名异常：{preset}"


# ---------------------------------------------------------------------------
# 3. psnr / mae —— 数学正确性
# ---------------------------------------------------------------------------

def test_psnr_identical_is_100():
    a = np.zeros((8, 8, 3), np.uint8)
    assert mda.psnr(a, a) == 100.0


def test_psnr_known_value():
    # 常量差 10 → MSE=100 → PSNR = 10*log10(255^2/100) ≈ 28.13。
    a = np.zeros((10, 10, 3), np.float64)
    b = np.full((10, 10, 3), 10.0)
    expected = 10.0 * np.log10(255.0 ** 2 / 100.0)
    assert abs(mda.psnr(a, b) - expected) < 0.01


def test_mae_known_value():
    a = np.zeros((10, 10, 3), np.float64)
    b = np.full((10, 10, 3), 5.0)
    assert mda.mae(a, b) == 5.0


# ---------------------------------------------------------------------------
# 4. run_case —— 端到端冒烟（检测 + 修复 + 量化）
# ---------------------------------------------------------------------------

def test_run_case_corner_logo_opencv():
    """角标 LOGO 用 OpenCV 引擎：应产出完整指标与正确尺寸的对比图。"""
    m, canvas = mda.run_case("corner_logo", "opencv")
    assert m is not None, "corner_logo 用例应能检测出掩码"
    for key in ("case", "case_cn", "preset", "engine", "coverage_pct",
                "psnr_marked", "psnr_repaired", "delta_psnr",
                "mae_marked", "mae_repaired"):
        assert key in m, f"指标缺失键 {key}"
    # delta_psnr 与两个 PSNR 自洽（三者均四舍五入到 0.01，容差取 0.05）。
    assert abs(m["delta_psnr"] - (m["psnr_repaired"] - m["psnr_marked"])) < 0.05
    # 四联图尺寸 = (2h+40) × (2w+30)。
    assert canvas.shape[0] == 640 * 2 + 40
    assert canvas.shape[1] == 640 * 2 + 30


# ---------------------------------------------------------------------------
# 5. --texture 模式（类自然纹理底图）
# ---------------------------------------------------------------------------

def _laplacian_var(img: np.ndarray) -> float:
    """用 Laplacian 方差衡量空间高频能量（纹理越强越高）。"""
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def test_make_textured_base_shape_and_dtype():
    img = mda.make_textured_base(seed=7)
    assert img.shape == (640, 640, 3)
    assert img.dtype == np.uint8
    assert img.min() >= 0 and img.max() <= 255


def test_make_textured_base_deterministic():
    a = mda.make_textured_base(seed=7)
    b = mda.make_textured_base(seed=7)
    assert np.array_equal(a, b), "同一 seed 必须得到完全相同的纹理底图"


def test_make_textured_base_has_more_texture_than_gradient():
    """纹理底图的空间高频能量应明显高于纯渐变底图（否则两种数据无区分度）。"""
    tex = mda.make_textured_base(seed=7)
    grad = mda.make_base(seed=7)
    assert _laplacian_var(tex) > _laplacian_var(grad) * 2 + 1.0, \
        "纹理底图的 Laplacian 方差未显著高于渐变底图，两条数据区分度不足"


def test_make_textured_base_relates_to_seed():
    a = mda.make_textured_base(seed=1)
    b = mda.make_textured_base(seed=2)
    assert not np.array_equal(a, b), "不同 seed 应产生不同纹理底图"


def test_run_case_textured_corner_logo_opencv():
    """纹理底图 + 角落 LOGO：端到端仍应产出完整指标与正确尺寸四联图。"""
    m, canvas = mda.run_case(
        "corner_logo", "opencv", seed=7,
        base_fn=mda.make_textured_base, data_kind="textured",
    )
    assert m is not None, "纹理底图下 corner_logo 用例应能检测出掩码"
    assert m["data"] == "textured", "data_kind 应被写入指标"
    for key in ("case", "coverage_pct", "psnr_marked", "psnr_repaired",
                "delta_psnr", "mae_marked", "mae_repaired"):
        assert key in m, f"纹理模式指标缺失键 {key}"
    assert canvas.shape[0] == 640 * 2 + 40
    assert canvas.shape[1] == 640 * 2 + 30


def test_run_case_textured_faint_tiled_low_coverage():
    """纹理底图 + 极淡平铺：覆盖率应远低于 100%（确认未触发过度检测）。"""
    m, _ = mda.run_case(
        "faint_tiled", "opencv", seed=7,
        base_fn=mda.make_textured_base, data_kind="textured",
    )
    # 检测器修复后，纹理图上的极淡平铺不应再占满全图。
    assert m is not None, "极淡平铺在纹理底图上仍应检出（而非过度检测归零）"
    assert m["coverage_pct"] < 80.0, \
        f"纹理底图覆盖率 {m['coverage_pct']}% 过高，疑似过度检测回潮"


# ---------------------------------------------------------------------------
# 6. --input-dir 真实图模式（无 ground truth）
# ---------------------------------------------------------------------------

def test_run_real_case_detects_and_repairs(tmp_path):
    """真实图模式：把带角标水印的合成图当真实图读入，应检出并产出对比图。"""
    import cv2
    real = mda.wm_corner_logo(mda.make_base(seed=3))
    p = tmp_path / "real1.png"
    cv2.imwrite(str(p), cv2.cvtColor(real, cv2.COLOR_RGB2BGR))

    r = mda.run_real_case(str(p), ["opencv", "lama"])
    assert r is not None, "真实图模式应能检出角标水印"
    m, canvas = r
    assert m["file"] == "real1.png"
    assert m["coverage_pct"] > 0, "应检出掩码（覆盖率 > 0）"
    assert "opencv" in m["engines"], "opencv 引擎结果应被记录"
    # 至少记录一个引擎的掩码区改写量。
    assert m["engines"]["opencv"]["mae_masked"] >= 0
    assert canvas.shape[0] == 640 * 2 + 40
    assert canvas.shape[1] == 640 * 2 + 30


def test_run_real_case_clean_image_low_coverage(tmp_path):
    """无视觉水印的干净图：检测器不应大面积覆盖（残余误报应极小）。"""
    import cv2
    clean = mda.make_base(seed=11)
    p = tmp_path / "clean1.png"
    cv2.imwrite(str(p), cv2.cvtColor(clean, cv2.COLOR_RGB2BGR))
    r = mda.run_real_case(str(p), ["opencv"])
    if r is None:
        # 理想情况：干净图判定为无水印，直接跳过。
        return
    cov = r[0]["coverage_pct"]
    assert cov < 2.0, \
        f"干净图检出覆盖率 {cov}% 过高，疑似过度检测回潮（应 < 2%）"


def test_run_real_dir_writes_summary(tmp_path, monkeypatch):
    """整目录模式：应处理每张图并写出 real_benchmark.json 与对比图到输出目录。"""
    import cv2
    for i, seed in enumerate((3, 5)):
        img = mda.wm_corner_logo(mda.make_base(seed=seed))
        cv2.imwrite(str(tmp_path / f"r{i}.png"),
                    cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    # 把全局输出目录重定向到临时目录，避免污染 docs/assets。
    monkeypatch.setattr(mda, "OUT_DIR", str(tmp_path))
    code = mda._run_real_dir(str(tmp_path))
    assert code == 0, "_run_real_dir 应返回 0"
    assert (tmp_path / "real_benchmark.json").exists(), "应写出汇总 JSON"
    assert (tmp_path / "real_r0.png").exists(), "应写出对比图"
    import json
    rows = json.loads((tmp_path / "real_benchmark.json").read_text(encoding="utf-8"))
    assert len(rows) == 2, "两张图都应被处理"
