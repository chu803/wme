"""阶段 109 · `core/preset_recommender` 真值审计的回归守卫。

审计入口 = 覆盖度荒漠榜（`scripts/audit_selector.py --unaudited` 榜首，ratio 14.0）。
本文件锁定四件事：

A. **P0 修复**：`_periodicity` 的判据由 **AND 改 OR**（与 `detect_grid_watermark` 对齐）。
   旧判据把**一维周期图案**（竖/横条纹）判成「非周期」⇒ 推荐 `ai_text_heavy`
   ⇒ 该预设**不启用网格通路** ⇒ 该图案 **recall 0.000**（手动选 `ai_tiled`/`ai_grid`
   均为 0.769）。这是「自动推荐把检得出的变成检不出」，最坏的一种推荐错误。
   守卫用 **内嵌旧实现** 证明它对真实存在过的缺陷有效（不是哑巴守卫）。

B. **零副作用**：修法在 30 张真实照片上的推荐分布**逐项不变**（含数据集时现场断言）。

C. **死参数入档**：`sensitivity` 自阶段 59 起不再影响结果，但三个消费方仍在传；
   旧 docstring 与实现相反 ⇒ 本阶段更正文案，并用行为断言 + 源码断言双向钉住，
   防止后人据旧文案以为它受控于 UI 滑杆。

D. **i18n 出口守卫**：`Recommendation.__str__` 含中文字面量（核心层声明「不负责翻译」），
   目前**未被任何 UI/服务消费**（都走 `.to_dict()` / `utils.recommend_view`）
   ⇒ 用源码扫描钉住「不得出现 `str(rec)` / `f"{rec}"`」，防止它日后漏进用户可见输出。
"""
import inspect
import os
import re
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
import core.preset_recommender as pr  # noqa: E402

H = W = 400


# ---------------------------------------------------------------------------
# 图案构造
# ---------------------------------------------------------------------------

def _stripe(vertical: bool = True, period: int = 32, width: int = 4) -> np.ndarray:
    img = np.full((H, W, 3), 240, np.uint8)
    if vertical:
        for x in range(0, W, period):
            img[:, x:x + width] = 170
    else:
        for y in range(0, H, period):
            img[y:y + width, :] = 170
    return img


def _dot_grid(period: int = 32, r: int = 2) -> np.ndarray:
    cv2 = pr._require_cv2()
    img = np.full((H, W, 3), 240, np.uint8)
    for y in range(0, H, period):
        for x in range(0, W, period):
            cv2.circle(img, (x, y), r, (180, 180, 180), -1)
    return img


def _marks_of(img: np.ndarray) -> np.ndarray:
    _g, residual, score, _h, _w = pr._build_residual(img)
    return pr._marks_at(score, config.DETECT_TRANSLUCENT_SENSITIVITY, residual)


# ---------------------------------------------------------------------------
# A. P0 修复：一维周期必须被接受
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vertical", [True, False])
def test_one_direction_period_is_accepted(vertical):
    """竖/横条纹只有一个方向有周期 ⇒ **必须**判为周期（与检测器的 OR 判据对齐）。"""
    img = _stripe(vertical)
    marks = _marks_of(img)
    periodic, strength = pr._periodicity(marks, H, W)
    assert periodic is True, (
        "一维周期图案被判为非周期 —— AND 判据回归（检测器只用 OR，见 detect_grid_watermark）")
    assert strength >= config.RECOMMEND_GRID_MIN_STRENGTH, (
        f"有效方向强度 {strength:.3f} 低于网格门限 {config.RECOMMEND_GRID_MIN_STRENGTH}")


def test_dot_grid_unchanged():
    """二维点阵（两方向都有周期）行为不变：仍判周期、仍推 ai_grid。"""
    img = _dot_grid()
    periodic, _s = pr._periodicity(_marks_of(img), H, W)
    assert periodic is True
    assert pr.recommend_preset(img).preset == "ai_grid"


def test_non_periodic_stays_non_periodic():
    """纯色与随机噪声不得被判成周期（防「OR 改宽了」）。"""
    rng = np.random.default_rng(0)
    for name, img in (("纯色", np.full((H, W, 3), 240, np.uint8)),
                      ("随机噪声", rng.integers(0, 255, (H, W, 3), dtype=np.uint8))):
        periodic, strength = pr._periodicity(_marks_of(img), H, W)
        assert periodic is False, f"{name} 被判成周期（OR 判据过宽）"


def _legacy_and_periodicity(marks, h, w):
    """**修复前的实现副本**（AND 判据）——用于证明守卫抓得住它（技能 5b）。"""
    row_proj = marks.sum(axis=1)
    col_proj = marks.sum(axis=0)
    period_y, sy = pr._dominant_period(row_proj)
    period_x, sx = pr._dominant_period(col_proj)

    def _ok(p, ax_len):
        return p > 0 and 6 <= p <= 0.6 * ax_len

    periodic = _ok(period_y, h) and _ok(period_x, w)
    return periodic, (min(sy, sx) if periodic else 0.0)


def test_legacy_AND_impl_fails_on_stripe():
    """旧实现（AND）在条纹上**必须**返回 False —— 否则本守卫测不到东西。"""
    marks = _marks_of(_stripe(True))
    legacy_periodic, legacy_strength = _legacy_and_periodicity(marks, H, W)
    assert legacy_periodic is False and legacy_strength == 0.0, (
        "内嵌的旧实现竟然也判周期 —— 副本没复现原缺陷，本守卫是空的")


@pytest.mark.slow
def test_stripe_recommendation_is_not_the_zero_recall_preset():
    """**用户可见后果**：条纹图案下，推荐预设必须真能检出它。

    实测对照（`%TEMP%/s109_quality.py`）：
        `ai_text_heavy`（旧推荐）recall **0.000** ／ `ai_tiled`、`ai_grid`、`ai_multi` **0.769**。
    """
    from core.detector import detect_watermark_mask
    img = _stripe(True)
    truth = np.any(img != np.full((H, W, 3), 240, np.uint8), axis=2)
    n_t = int(truth.sum())
    rec = pr.recommend_preset(img)

    def _recall(preset):
        res = detect_watermark_mask(img, preset=preset, use_ocr=False)
        pred = np.asarray(res.mask) > 0
        return int(np.count_nonzero(pred & truth)) / n_t

    assert rec.preset != "ai_text_heavy", (
        f"条纹仍被推给 ai_text_heavy（该预设 recall 0.000）；实际推了 {rec.preset}")
    got = _recall(rec.preset)
    assert got >= 0.70, f"推荐 {rec.preset} 的召回只有 {got:.3f}（应 ≥0.70）"
    # 记录旧行为的代价：这条让「为什么必须避免 ai_text_heavy」可复现
    assert _recall("ai_text_heavy") == 0.0, (
        "ai_text_heavy 现在能检出条纹了？—— 上游通路变了，请重做本阶段的取舍")


# ---------------------------------------------------------------------------
# B. 零副作用（有数据集时现场断言）
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_real_photo_distribution_unchanged():
    """30 张真实照片上的推荐分布**基线锁定**（防无意漂移）。

    基线演变：
      · 阶段 109（AND→OR 零副作用）：{ai_multi:20, ai_text_heavy:10}；
      · 阶段 450（角标模板匹配，见 #450）：右下/中右角标图改推 ai_logo（edge=False，
        跳过 edge+OCR 空跑）⇒ 前 30 张里 1.png / 2.png 由 multi/text_heavy 转 ai_logo，
        重锚为 **{ai_multi:19, ai_text_heavy:9, ai_logo:2}**。
    数据集（`benchmark_real/images/`）是 gitignored ⇒ 缺失时跳过（不制造假红）。
    """
    from core.image_utils import load_image
    from scripts.e2e_real_probe import _resize_long_side
    img_dir = os.path.join(_ROOT, "benchmark_real", "images")
    if not os.path.isdir(img_dir):
        pytest.skip("真实基准图片目录不存在")
    files = sorted(f for f in os.listdir(img_dir)
                   if f.lower().endswith((".jpg", ".jpeg", ".png")))[:30]
    if len(files) < 30:
        pytest.skip(f"图片不足 30 张（实际 {len(files)}）")
    dist = {}
    for fn in files:
        img = _resize_long_side(load_image(os.path.join(img_dir, fn)), 640)
        rec = pr.recommend_preset(img)
        dist[rec.preset] = dist.get(rec.preset, 0) + 1
    assert dist == {"ai_multi": 19, "ai_text_heavy": 9, "ai_logo": 2}, (
        f"真实照片推荐分布变了：{dist}（阶段 450 重锚基线是 ai_multi 19 / "
        f"ai_text_heavy 9 / ai_logo 2；若为有意变更请同步更新本基线与说明）")


# ---------------------------------------------------------------------------
# C. 死参数：`sensitivity` 不影响结果（行为 + 源码双向钉住）
# ---------------------------------------------------------------------------

def test_sensitivity_is_inert_in_behaviour():
    """三档 sensitivity 的推荐结果**逐项相同**（该形参自阶段 59 起未被读取）。"""
    img = _stripe(True)
    base = pr.recommend_preset(img, sensitivity=0.0)
    for s in (0.5, 1.0):
        rec = pr.recommend_preset(img, sensitivity=s)
        assert (rec.preset, rec.triggered, rec.scores) == \
               (base.preset, base.triggered, base.scores), (
            f"sensitivity={s} 改变了结果 —— 请同步更新 docstring 与 UI 说明")


def test_marks_at_does_not_read_sensitivity():
    """源码断言：`_marks_at` 函数体内**不得出现** `sensitivity`（防止有人把它接回去）。"""
    src = inspect.getsource(pr._marks_at)
    body = src.split('"""')[-1]        # 去掉 docstring，只看代码体
    assert "sensitivity" not in body, (
        "`_marks_at` 又读起了 sensitivity —— 若这是有意的，请同时更新 "
        "`recommend_preset` 的 docstring 与 UI 滑杆说明（并把本断言改成新契约）")


def test_docstring_states_sensitivity_is_inert():
    """文档必须与实现一致：`recommend_preset` 的 docstring 要写明「目前不影响结果」。

    旧声明（「仅影响……提取」）只允许出现在**更正说明**那一行里（本阶段必须保留它，
    否则后人不知道改过什么）——故逐行判定，而不是全文包含判定。
    """
    doc = pr.recommend_preset.__doc__ or ""
    assert "不影响结果" in doc, "docstring 未说明 sensitivity 目前无效（旧文案与实现相反）"
    for line in doc.splitlines():
        if "仅影响" in line:
            assert "旧 docstring" in line, (
                f"仍在以声明语气写「仅影响…」（应与实现一致）：{line.strip()}")


# ---------------------------------------------------------------------------
# D. i18n 出口守卫：核心层的中文 `__str__` 不得进入 UI/服务消费面
# ---------------------------------------------------------------------------

def test_recommendation_str_has_cjk():
    """前提：`__str__` 确实含中文字面量（这就是它不能进输出路径的原因）。"""
    rec = pr.Recommendation(preset="ai_grid", scores={"grid": 0.9},
                            triggered=["grid"], confidence=0.9)
    assert any("\u4e00" <= c <= "\u9fff" for c in str(rec))


def test_ui_and_services_never_stringify_recommendation():
    """UI / 服务层**不得**直接 `str(rec)` 或 `f"{rec}"`（会漏中文到非中文界面）。

    它们应当走 `Recommendation.to_dict()`（机器可读）再由 `utils.recommend_view` 本地化
    —— 这也是阶段 98 为 `recommend_view` 建立 i18n 出口的同一契约。
    """
    bad = []
    for layer in ("ui", "services"):
        root = os.path.join(_ROOT, layer)
        for dirpath, _dirs, files in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(dirpath, fn)
                for i, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                    if "str(rec" in line or re.search(r"[{]\s*rec\s*[}]", line):
                        bad.append(f"{layer}/{fn}:{i}: {line.strip()[:80]}")
    assert not bad, (
        "UI/服务层把 Recommendation 直接字符串化了（中文会漏到非中文界面）：\n  "
        + "\n  ".join(bad))
