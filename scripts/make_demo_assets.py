"""
生成演示素材 + 客观量化擦除质量。

为什么需要这个脚本
------------------
项目此前 48 个阶段从未回答过一个最基本的问题：**水印到底擦得干不干净？**
所有测试只能验证「不报错、流程通」，无法验证「效果好」。原因是缺少 ground truth
（无水印的原图）——真实带水印的图，我们手上没有它「未加水印」的版本。

本脚本的解法：**自己合成底图**。底图由程序生成，因此天然拥有 ground truth，
于是可以客观计算修复结果与「真值」的差距，把「效果」变成一个可比较的数字。

产出
----
1. `docs/assets/demo_<类型>_<引擎>.png` —— 四联对比图
   （原图 / 带水印 / 检测掩码 / 修复结果）。
2. `docs/assets/benchmark.json` —— 各项量化指标。
3. 标准输出的 Markdown 表格，可直接贴进文档。

量化指标
--------
- **PSNR**（峰值信噪比，dB）：修复结果与真值越接近越高。
  `ΔPSNR = PSNR(修复) − PSNR(带水印)` 为**净提升**，是本脚本最核心的数字。
- **MAE**（平均绝对误差，0~255）：逐像素平均偏差，越低越好。
- **掩码覆盖率**（%）：检测命中的像素占全图比例，衡量检测器的召回范围。

用法
----
    python scripts/make_demo_assets.py                 # 用当前可用引擎跑全部用例
    python scripts/make_demo_assets.py --engine opencv # 强制传统算法
    python scripts/make_demo_assets.py --engine lama   # 强制深度学习（需 torch+权重）
    python scripts/make_demo_assets.py --compare       # 两引擎都跑并给出对比
    python scripts/make_demo_assets.py --input-dir X   # 用真实带水印图跑 LaMa vs OpenCV 对比
                                                      # （X 为含图片的目录；无 ground truth，
                                                      #   故比较两引擎在「掩码区改写量」与「彼此差异」上的表现）

说明：脚本不依赖 gradio，可在纯 CLI / CI 环境下运行。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 项目根目录注入 sys.path（允许从任意工作目录运行）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.detector import detect_watermark_mask  # noqa: E402
from core.inpainter import inpaint  # noqa: E402
from core.quality import no_reference_inpaint_score  # noqa: E402
from utils.exceptions import EmptyMaskError  # noqa: E402

OUT_DIR = os.path.join(_ROOT, "docs", "assets")

# Windows 常见字体；找不到时回退到 PIL 内置位图字体。
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """按优先级挑一个可用字体；全部缺失时回退内置字体。"""
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 1. 合成底图（ground truth）
# ---------------------------------------------------------------------------

def make_base(seed: int, w: int = 640, h: int = 640) -> np.ndarray:
    """
    合成一张干净的 ground truth 底图：四色平滑渐变，不含任何结构性内容。

    设计原则：
        · 越简单越好：无边缘、无光斑、无纹理。这样检测器的掩码只来自水印，
          不会被底图自身结构触发，保证 benchmark 的「唯一变量」是水印。
        · 渐变为修复提供真实的颜色变化（大尺度背景），两种引擎都会因此显出差距。
        · 同一 seed 产生确定性的图，保证结果可复现。

    返回值：H×W×3 uint8 RGB 数组。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = xx / max(w - 1, 1)
    v = yy / max(h - 1, 1)

    # 四个角颜色：左上、右上、左下、右下，均为中等亮度避免过曝/欠曝。
    # reshape 为 (4,1,1,3) 以便与 (h,w) 空间维度广播。
    cols = rng.uniform(60, 200, size=(4, 3)).reshape(4, 1, 1, 3)
    top = cols[0] * (1 - u[:, :, None]) + cols[1] * u[:, :, None]
    bot = cols[2] * (1 - u[:, :, None]) + cols[3] * u[:, :, None]
    img = top * (1 - v[:, :, None]) + bot * v[:, :, None]
    return np.clip(img, 0, 255).astype(np.uint8)


def _fractal_noise(h: int, w: int, rng, octaves: int = 5,
                   base_scale: int = 8) -> np.ndarray:
    """
    多倍频程值噪声（fractal/value noise），用于生成「类自然」纹理。

    原理：把多张不同分辨率的随机网格双线性放大后加权叠加，低频给大尺度明暗、
    高频给细节颗粒，整体近似自然图像的纹理统计（低频能量高、高频能量低）。

    返回值：H×W float32，值域约 [0,1]。
    """
    ys = np.linspace(0, 1, h, dtype=np.float32)
    xs = np.linspace(0, 1, w, dtype=np.float32)
    noise = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    scale = base_scale
    for _ in range(octaves):
        gh = max(2, h // scale)
        gw = max(2, w // scale)
        grid = rng.uniform(0, 1, (gh, gw)).astype(np.float32)
        gy = (ys * (gh - 1)).astype(np.int32).clip(0, gh - 2)
        gx = (xs * (gw - 1)).astype(np.int32).clip(0, gw - 2)
        fy = (ys * (gh - 1) - gy)[:, None]
        fx = (xs * (gw - 1) - gx)[None, :]
        n = (
            grid[gy][:, gx] * (1 - fy) * (1 - fx)
            + grid[gy][:, gx + 1] * (1 - fy) * fx
            + grid[gy + 1][:, gx] * fy * (1 - fx)
            + grid[gy + 1][:, gx + 1] * fy * fx
        )
        noise += amp * n
        total += amp
        amp *= 0.5
        scale *= 2
    return noise / total


def make_textured_base(seed: int, w: int = 640, h: int = 640) -> np.ndarray:
    """
    合成一张「类自然照片」的底图：四角渐变 + 多倍频程分形纹理。

    与 make_base（纯渐变）的区别与用途：
        · 纯渐变是 OpenCV 局部扩散的最舒适区（扩散填充即可近似完美），
          但也是 LaMa 生成式重建的最不舒适区（模型倾向补出纹理）。
        · 带纹理的图才接近真实照片的统计特征，是 LaMa 的优势场景
          （OpenCV 会把纹理区抹成糊，LaMa 能补出合理纹理）。
    同一 seed 确定性；两个生成器 seed 域错开，避免纹理图与渐变图强相关。
    """
    rng = np.random.default_rng(seed + 10_000)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u = xx / max(w - 1, 1)
    v = yy / max(h - 1, 1)

    # 柔和整体明暗（四角渐变）。
    cols = rng.uniform(70, 190, size=(4, 3)).reshape(4, 1, 1, 3)
    top = cols[0] * (1 - u[:, :, None]) + cols[1] * u[:, :, None]
    bot = cols[2] * (1 - u[:, :, None]) + cols[3] * u[:, :, None]
    img = top * (1 - v[:, :, None]) + bot * v[:, :, None]

    # 三通道共享亮度纹理 + 每通道独立色彩纹理，模拟真实照片的通道相关性。
    lum = _fractal_noise(h, w, rng, octaves=5, base_scale=8)
    img += (lum - 0.5)[:, :, None] * 90
    for c in range(3):
        tex = _fractal_noise(h, w, rng, octaves=5, base_scale=8 + c * 5)
        img[:, :, c] += (tex - 0.5) * 40 * (0.85 + 0.15 * c)
    return np.clip(img, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 2. 四类水印（与项目的四个 AI 检测预设一一对应）
# ---------------------------------------------------------------------------

def _rgba_text_tile(size, text, font_size, angle, alpha, gap, color=(255, 255, 255)):
    """生成 45° 平铺的半透明文字水印层（RGBA）。"""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    fnt = _font(font_size)
    # textbbox 是 ImageDraw.Draw 的方法，不是 Image 的。
    tw, th = ImageDraw.Draw(layer).textbbox((0, 0), text, font=fnt)[2:]
    step_x, step_y = tw + gap, th + gap * 2
    tile = Image.new("RGBA", (int(step_x * 2.2), int(step_y * 2.2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    for i in range(-1, 3):
        for j in range(-1, 3):
            d.text((i * step_x, j * step_y), text, font=fnt,
                   fill=color + (alpha,))
    tile = tile.rotate(angle, expand=True, resample=Image.BICUBIC)
    # 平铺铺满整图。
    W, H = size
    tw2, th2 = tile.size
    for y in range(-th2, H + th2, max(th2 // 2, 1)):
        for x in range(-tw2, W + tw2, max(tw2 // 2, 1)):
            layer.alpha_composite(tile, dest=(x, y))
    return layer


def wm_faint_tiled(base: np.ndarray) -> np.ndarray:
    """极淡平铺文字水印 —— 对应预设 ai_faint_tiled。"""
    h, w = base.shape[:2]
    im = Image.fromarray(base).convert("RGBA")
    # 调大 gap、降低 alpha：模拟「几乎看不见的全图平铺」，覆盖率应显著低于 100%。
    im.alpha_composite(_rgba_text_tile((w, h), "AI GENERATED", 26, 30, 22, 160))
    return np.array(im.convert("RGB"))


def wm_corner_logo(base: np.ndarray) -> np.ndarray:
    """右下角实体 LOGO —— 对应预设 ai_logo。"""
    h, w = base.shape[:2]
    im = Image.fromarray(base).convert("RGBA")
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    fnt = _font(34)
    text = "StudioAI"
    tw, th = d.textbbox((0, 0), text, font=fnt)[2:]
    pad = 18
    x0, y0 = w - tw - pad * 3, h - th - pad * 3
    x1, y1 = w - pad, h - pad
    d.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=(20, 20, 24, 150))
    d.text((x0 + pad, y0 + pad), text, font=fnt, fill=(255, 255, 255, 235))
    im.alpha_composite(ov)
    return np.array(im.convert("RGB"))


def wm_edge_text(base: np.ndarray) -> np.ndarray:
    """底部边缘小字 —— 对应预设 ai_text_heavy。"""
    h, w = base.shape[:2]
    im = Image.fromarray(base).convert("RGBA")
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    fnt = _font(22)
    fnt_s = _font(15)
    d.text((18, h - 62), "Created with AI Studio", font=fnt, fill=(255, 255, 255, 205))
    d.text((18, h - 36), "example.com / watermark-demo", font=fnt_s, fill=(235, 235, 235, 175))
    im.alpha_composite(ov)
    return np.array(im.convert("RGB"))


def wm_grid(base: np.ndarray) -> np.ndarray:
    """网格点阵水印 —— 对应预设 ai_grid。"""
    h, w = base.shape[:2]
    im = Image.fromarray(base).convert("RGBA")
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    step, r = 56, 3  # 加大间距，避免覆盖率过高，更接近真实点阵水印
    for y in range(0, h, step):
        for x in range(0, w, step):
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 52))
    im.alpha_composite(ov)
    return np.array(im.convert("RGB"))


# 类型 → (加水印函数, 检测预设, 中文名)
CASES = {
    "faint_tiled": (wm_faint_tiled, "ai_faint_tiled", "极淡平铺文字"),
    "corner_logo": (wm_corner_logo, "ai_logo", "角落 LOGO"),
    "edge_text": (wm_edge_text, "ai_text_heavy", "底部边缘小字"),
    "grid": (wm_grid, "ai_grid", "网格点阵"),
}


# ---------------------------------------------------------------------------
# 3. 指标计算
# ---------------------------------------------------------------------------

def psnr(a: np.ndarray, b: np.ndarray) -> float:
    """峰值信噪比（dB）；完全一致时返回 100（理论无穷大的截断值）。"""
    diff = a.astype(np.float64) - b.astype(np.float64)
    mse = float(np.mean(diff ** 2))
    if mse <= 1e-9:
        return 100.0
    return 10.0 * np.log10(255.0 ** 2 / mse)


def mae(a: np.ndarray, b: np.ndarray) -> float:
    """平均绝对误差（0~255）。"""
    return float(np.mean(np.abs(a.astype(np.float64) - b.astype(np.float64))))


# ---------------------------------------------------------------------------
# 4. 单用例执行
# ---------------------------------------------------------------------------

def run_case(name: str, engine: str, seed: int = 7,
             base_fn=make_base, data_kind: str = "gradient"):
    """
    跑完一个用例：合成 → 加水印 → 检测 → 修复 → 量化。

    参数说明：
        name      : CASES 里的用例名。
        engine    : "auto" / "opencv" / "lama"。
        seed      : 底图随机种子。
        base_fn   : 底图生成函数（make_base 纯渐变 / make_textured_base 类自然纹理）。
        data_kind : 数据种类标记（写入指标，便于区分两种底图的结果）。

    返回 (metrics_dict, 四联对比图 ndarray)；检测为空掩码时返回 (None, None)。
    """
    apply_wm, preset, cn = CASES[name]
    clean = base_fn(seed)                         # ground truth
    marked = apply_wm(clean)                      # 带水印输入

    res = detect_watermark_mask(marked, preset=preset)
    mask = res.mask
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    coverage = float((mask > 0).mean() * 100.0)
    if coverage <= 0.0:
        return None, None

    try:
        out = inpaint(marked, mask, engine=engine)
    except EmptyMaskError:
        return None, None

    m = {
        "case": name,
        "case_cn": cn,
        "preset": preset,
        "engine": engine,
        "data": data_kind,
        "coverage_pct": round(coverage, 2),
        "psnr_marked": round(psnr(marked, clean), 2),
        "psnr_repaired": round(psnr(out, clean), 2),
        "delta_psnr": round(psnr(out, clean) - psnr(marked, clean), 2),
        "mae_marked": round(mae(marked, clean), 3),
        "mae_repaired": round(mae(out, clean), 3),
    }
    return m, _compose(clean, marked, mask, out, cn, engine)


def _compose(clean, marked, mask, out, title_cn, engine) -> np.ndarray:
    """拼成四联对比图（上排：真值 / 带水印；下排：掩码 / 修复结果）。"""
    import cv2

    h, w = clean.shape[:2]
    mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

    def to_bgr(a):
        return cv2.cvtColor(a, cv2.COLOR_RGB2BGR)

    def put(img, text):
        cv2.putText(img, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.75, (255, 255, 255), 2, cv2.LINE_AA)
        return img

    tl = put(to_bgr(clean.copy()), "1. clean (ground truth)")
    tr = put(to_bgr(marked.copy()), "2. watermarked (input)")
    bl = put(mask_bgr.copy(), "3. detected mask")
    br = put(to_bgr(out.copy()), f"4. repaired [{engine}]")

    canvas = np.zeros((h * 2 + 40, w * 2 + 30, 3), np.uint8)
    canvas[0:h, 0:w] = tl
    canvas[0:h, w + 30:w * 2 + 30] = tr
    canvas[h + 40:h * 2 + 40, 0:w] = bl
    canvas[h + 40:h * 2 + 40, w + 30:w * 2 + 30] = br
    return canvas


# ---------------------------------------------------------------------------
# 5. 主流程
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 4b. 真实图用例执行（无 ground truth）
# ---------------------------------------------------------------------------

def run_real_case(path: str, engines: list) -> "tuple | None":
    """
    处理一张真实带水印图：自动检测 → 多引擎修复 → 量化（无真值对比）。

    参数说明：
        path    : 图片路径。
        engines : 要跑的引擎列表（如 ["opencv", "lama"]）。

    返回 (metrics_dict, 四联对比图 ndarray)；未检出掩码或修复全失败时返回 None。

    度量设计（无 ground truth 时的可行指标）：
        - coverage_pct              : 检测器命中像素占比（召回范围）。
        - mae_masked                : 修复结果与原图在「掩码区」的平均改写量，
                                      越低说明引擎越克制（保留原图越多）。
        - mae_full                  : 全图平均改写量。
        - lama_vs_opencv_mae        : 两引擎修复结果逐像素差异，越高说明两者分歧越大。
    """
    import cv2

    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        print(f"[跳过] 无法读取：{path}")
        return None
    img = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    res = detect_watermark_mask(img)  # preset=None → 自动检测全部策略
    mask = res.mask
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    coverage = float((mask > 0).mean() * 100.0)
    if coverage <= 0.0:
        print(f"[跳过] {os.path.basename(path)}：未检出掩码（覆盖率 0%），"
              f"可能确实无水印或水印超出检测器能力")
        return None

    out = {}
    for eng in engines:
        try:
            out[eng] = inpaint(img, mask, engine=eng)
        except Exception as exc:  # noqa: BLE001
            print(f"[警告] {os.path.basename(path)} / {eng} 修复失败：{exc}")
            out[eng] = None

    valid = {k: v for k, v in out.items() if v is not None}
    if not valid:
        return None

    in_mask = mask > 0
    # 无参考质量分：修复区与背景环衔接越自然分越高（阶段 56）。
    # 注意：属启发式，真实照片选优效力待 P0 验证；此处仅作辅助参考。
    scores = {}
    for eng, o in valid.items():
        try:
            scores[eng] = round(no_reference_inpaint_score(o, img, mask), 4)
        except Exception:  # noqa: BLE001
            scores[eng] = None

    best = None
    if scores:
        best = max((e for e, s in scores.items() if s is not None),
                   key=lambda e: scores[e], default=None)

    m = {
        "file": os.path.basename(path),
        "coverage_pct": round(coverage, 2),
        "engines": {},
        "recommended": best,
    }
    for eng, o in valid.items():
        if in_mask.any():
            mae_masked = float(np.mean(
                np.abs(o.astype(np.float64) - img.astype(np.float64))[in_mask]
            ))
        else:
            mae_masked = 0.0
        m["engines"][eng] = {
            "mae_masked": round(mae_masked, 3),
            "mae_full": round(mae(o, img), 3),
            "quality_score": scores.get(eng),
        }
    if "opencv" in valid and "lama" in valid:
        m["lama_vs_opencv_mae"] = round(mae(valid["lama"], valid["opencv"]), 3)

    canvas = _compose_real(img, mask, valid.get("opencv"), valid.get("lama"),
                           scores, best)
    return m, canvas


def _compose_real(marked, mask, opencv_out, lama_out, scores=None,
                  recommended=None) -> np.ndarray:
    """拼成四联对比图（输入 / 掩码 / opencv 修复 / lama 修复），并标注质量分与推荐。"""
    import cv2

    h, w = marked.shape[:2]
    mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    scores = scores or {}

    def to_bgr(a):
        if a is None:
            return np.zeros((h, w, 3), np.uint8)
        return cv2.cvtColor(a, cv2.COLOR_RGB2BGR)

    def put(img, text):
        cv2.putText(img, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (255, 255, 255), 2, cv2.LINE_AA)
        return img

    def tag(eng):
        s = scores.get(eng)
        if s is None:
            return eng
        mark = " ★推荐" if recommended == eng and recommended is not None else ""
        return f"{eng} 分={s:+.3f}{mark}"

    tl = put(to_bgr(marked.copy()), "1. input")
    tr = put(mask_bgr.copy(), "2. detected mask")
    bl = put(to_bgr(opencv_out), f"3. repaired [{tag('opencv')}]")
    br = put(to_bgr(lama_out), f"4. repaired [{tag('lama')}]")

    canvas = np.zeros((h * 2 + 40, w * 2 + 30, 3), np.uint8)
    canvas[0:h, 0:w] = tl
    canvas[0:h, w + 30:w * 2 + 30] = tr
    canvas[h + 40:h * 2 + 40, 0:w] = bl
    canvas[h + 40:h * 2 + 40, w + 30:w * 2 + 30] = br
    return canvas


def _run_real_dir(input_dir: str) -> int:
    """遍历目录，对每张真实图跑 run_real_case，汇总对比表。"""
    import glob

    exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
    files = sorted(
        f for f in glob.glob(os.path.join(input_dir, "*"))
        if f.lower().endswith(exts)
    )
    if not files:
        print(f"目录 {input_dir} 下未找到图片文件（支持 {exts}）。")
        return 1

    # 真实图对比始终跑两个引擎，核心产物是「两引擎分歧」。
    engines = ["opencv", "lama"]
    rows = []
    for p in files:
        r = run_real_case(p, engines)
        if r is None:
            continue
        m, canvas = r
        rows.append(m)
        base = os.path.splitext(os.path.basename(p))[0]
        out_path = os.path.join(OUT_DIR, f"real_{base}.png")
        import cv2
        cv2.imwrite(out_path, canvas)
        print(f"[生成] {os.path.basename(out_path)}  覆盖率={m['coverage_pct']}%")

    if not rows:
        print("没有任何图片检出有效掩码。")
        return 1

    print("\n真实图擦除对比汇总（无 ground truth，比较两引擎表现）：")
    print("| 文件 | 覆盖率% | OpenCV MAE | LaMa MAE | OCV分 | LaMa分 | 推荐 |")
    print("|---|---|---|---|---|---|---|")
    for m in rows:
        oc = m["engines"].get("opencv", {})
        la = m["engines"].get("lama", {})
        oc_m = oc.get("mae_masked", "-")
        la_m = la.get("mae_masked", "-")
        oc_s = oc.get("quality_score", "-")
        la_s = la.get("quality_score", "-")
        rec = m.get("recommended") or "-"
        print(f"| {m['file']} | {m['coverage_pct']} | {oc_m} | {la_m} | "
              f"{oc_s} | {la_s} | {rec} |")

    with open(os.path.join(OUT_DIR, "real_benchmark.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"\n指标已写入 docs/assets/real_benchmark.json（共 {len(rows)} 张）")
    return 0


def main() -> int:
    # 阶段 331：输出编码加固（GBK 管道下打印 ⇒/✅ 会 UnicodeEncodeError ⇒ 「通过」被读成退出码 1）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="生成演示素材并量化擦除质量")
    ap.add_argument("--engine", default="auto", choices=["auto", "opencv", "lama"],
                    help="修复引擎（默认 auto：有 LaMa 用 LaMa，否则 OpenCV）")
    ap.add_argument("--compare", action="store_true",
                    help="同时跑 opencv 与 lama 两个引擎并给出对比")
    ap.add_argument("--seed", type=int, default=7, help="底图随机种子")
    ap.add_argument("--texture", action="store_true",
                    help="使用「类自然纹理」底图（make_textured_base）；"
                         "默认用纯渐变底图。两种数据分别检验两个引擎的舒适区")
    ap.add_argument("--input-dir", default=None,
                    help="真实带水印图目录：对每张图自动检测掩码，并分别用 "
                         "opencv / lama 修复，比较两引擎差异（无 ground truth，"
                         "不计算 ΔPSNR，改用掩码区改写量与引擎间 MAE）")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    # 真实图模式：独立于合成流程，直接处理用户提供的图片。
    if args.input_dir:
        return _run_real_dir(args.input_dir)

    import cv2
    base_fn = make_textured_base if args.texture else make_base
    data_kind = "textured" if args.texture else "gradient"
    prefix = "tex_" if args.texture else ""
    engines = ["opencv", "lama"] if args.compare else [args.engine]
    all_rows, all_metrics = [], []

    for eng in engines:
        for name in CASES:
            m, canvas = run_case(name, eng, seed=args.seed,
                                 base_fn=base_fn, data_kind=data_kind)
            if m is None:
                print(f"[跳过] {name} / {eng}：未检出掩码")
                continue
            all_rows.append(m)
            all_metrics.append(m)
            path = os.path.join(OUT_DIR, f"{prefix}demo_{name}_{eng}.png")
            cv2.imwrite(path, canvas)
            print(f"[生成] {os.path.relpath(path, _ROOT)}  "
                  f"ΔPSNR={m['delta_psnr']:+.2f} dB  覆盖率={m['coverage_pct']}%")

    if not all_rows:
        print("没有任何用例产出有效结果。")
        return 1

    # 汇总表。
    print("\n" + "=" * 78)
    print("| 数据 | 用例 | 引擎 | 覆盖率% | PSNR(带水印) | PSNR(修复) | ΔPSNR | MAE(修复) |")
    print("|---|---|---|---|---|---|---|---|")
    for m in all_rows:
        print(f"| {m['data']} | {m['case_cn']} | {m['engine']} | {m['coverage_pct']} | "
              f"{m['psnr_marked']} | {m['psnr_repaired']} | "
              f"{m['delta_psnr']:+g} | {m['mae_repaired']} |")
    print("=" * 78)

    if args.compare:
        print("\n各用例净提升（ΔPSNR，越高越好）：")
        print("| 数据 | 用例 | OpenCV | LaMa | 差值 |")
        print("|---|---|---|---|---|")
        by = {(m["data"], m["case"], m["engine"]): m for m in all_rows}
        kinds = sorted({m["data"] for m in all_rows})
        for kind in kinds:
            for name, (_, _, cn) in CASES.items():
                a = by.get((kind, name, "opencv"))
                b = by.get((kind, name, "lama"))
                if a and b:
                    print(f"| {kind} | {cn} | {a['delta_psnr']:+g} | {b['delta_psnr']:+g} | "
                          f"{b['delta_psnr'] - a['delta_psnr']:+g} |")

    with open(os.path.join(OUT_DIR, "benchmark.json"), "w", encoding="utf-8") as f:
        json.dump(all_metrics, f, ensure_ascii=False, indent=2)
    print(f"\n指标已写入 docs/assets/benchmark.json（共 {len(all_metrics)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
