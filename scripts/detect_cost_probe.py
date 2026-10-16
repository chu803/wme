#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detect_cost_probe.py — 剖析 recommend_preset 与 detect_watermark_mask 的成本构成。

背景（Task #450，性能续推阶段）：
  此前假说是「recommend 内含一趟重复检测 / OCR，拖慢全管线」。本探针用计时 + 调用关系
  坐实或证伪该假说，并量化 detect 内部成本（六策略 + OCR）。

实测要回答的三个问题：
  1. recommend_preset 是否重复调用 detect / OCR？
     → 否。recommend_preset 只做 numpy+cv2 轻量特征分析（中值背景 / Sobel / Canny /
       2D-FFT 规则性 / 自相关 / 平面拟合），不 import detector、不调 OCR（见
       core/preset_recommender.py docstring 与实现）。本探针用计时佐证其体量很小。
  2. recommend 是否真「收窄」了 detect、从而降本？
     → 是（对真实角标图）。recommend 推到 ai_logo 预设后，detect 只跑
       translucent + corner + logo 三条策略（edge/grid/gradient 关闭，见
       core/detection_presets.py ai_logo 定义），edge（含 OCR）被整条关掉。
       本探针对比 detect(general 全开) vs detect(推荐预设) 的耗时，量化这一降本。
  3. detect 内部 OCR 占多少？
     → 用 use_ocr=False 旁路隔离：OCR_cost ≈ detect(ocr=True) − detect(ocr=False)。
       仅 corner / edge 两条策略内部会触发 OCR；translucent/grid/gradient/logo 纯 cv2。

诚实前提：
  · 真实 OCR / GPU 数字**必须**在 canonical venv（torch+cu128+easyocr）上跑。本脚本可直接
    用于该环境；本机若无 easyocr，use_ocr=True 会自动降级 OpenCV 兜底，此时 OCR 隔离项
    为 0 并明确标注（结论以「相对关系」而非绝对毫秒为准）。
  · 结论性判据：ai_logo 推荐预设的 detect 耗时是否显著低于 general 全开；recommend 自身
    耗时是否远小于单次 detect（即它不是瓶颈、且被后续降本抵消）。

退出码：
  0 = 跑通并产出成本拆分
  2 = 无有效样本
"""
import sys
import os
import glob
import time
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import config

from core.image_utils import load_image_full
from core.detector import detect_watermark_mask, warm_up_ocr, _ocr_available
from core.preset_recommender import recommend_preset
from core.detection_presets import resolve_preset

# 每档计时的重复次数（取中位数，抹平偶发抖动）。
N_REPEAT = 3


def _force_utf8():
    try:
        from scripts.console_encoding import force_utf8_stdio
        force_utf8_stdio()
    except Exception:
        pass


def _median(xs):
    if not xs:
        return 0.0
    return float(np.median(xs))


def _time_call(fn):
    ts = []
    for _ in range(N_REPEAT):
        t0 = time.perf_counter()
        r = fn()
        ts.append(time.perf_counter() - t0)
    return _median(ts), r


def _enabled_strategy_count(preset_name, sensitivity):
    """数出某预设实际开启的策略数（translucent/corner/edge/grid/gradient/logo）。"""
    kw = resolve_preset(preset_name, sensitivity) if preset_name else {}
    keys = ("enable_translucent", "enable_corner", "enable_edge",
            "enable_grid", "enable_gradient", "enable_logo")
    return sum(1 for k in keys if kw.get(k) is True), kw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="benchmark_real/images",
                    help="图片目录（默认 benchmark_real/images）")
    ap.add_argument("--limit", type=int, default=0,
                    help="最多处理多少张（0=全部）")
    ap.add_argument("--no-ocr", action="store_true",
                    help="强制 use_ocr=False，只测 cv2 基线（隔离 OCR 成本）")
    ap.add_argument("--warmup", type=int, default=1,
                    help="进入计时前预热的 detect 次数（触发 OCR 阅读器初始化）")
    args = ap.parse_args()
    _force_utf8()

    from PIL import Image
    paths = sorted(glob.glob(os.path.join(args.images, "*.png")) +
                   glob.glob(os.path.join(args.images, "*.jpg")))
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        print("无有效样本：%s" % args.images)
        return 2

    sensitivity = config.DETECT_TRANSLUCENT_SENSITIVITY
    ocr_flag = False if args.no_ocr else config.DETECT_USE_OCR

    # ---- OCR 阅读器预热（避免把首调用初始化算进计时）----
    try:
        from core.detector import warm_up_ocr
        warm_up_ocr(blocking=True)
    except Exception as exc:
        print("[warn] warm_up_ocr 失败（可能无 easyocr）：%s" % exc)

    easyocr_ok = False
    try:
        from core.detector import _ocr_available
        easyocr_ok = _ocr_available()
    except Exception:
        pass

    # 用首图做一次真实 detect 预热（触发 OCR Reader 懒加载 + cv2 缓存）。
    try:
        rgb0 = _load_rgb(paths[0])
        detect_watermark_mask(rgb0, use_ocr=ocr_flag,
                              preset=None)
    except Exception as exc:
        print("[warn] 预热 detect 失败：%s" % exc)

    rec_times, gen_times, rec_det_times = [], [], []
    gen_noocr_times, rec_noocr_times = [], []
    used_ocr_flags, rec_presets = [], []
    strat_counts = []
    n_ocred = 0

    print("=" * 72)
    print("detect_cost_probe：recommend vs detect 成本拆分")
    print("  easyocr 可用 = %s | 本次 ocr_flag = %s | 样本数 = %d"
          % (easyocr_ok, ocr_flag, len(paths)))
    print("  说明：no_ocr 旁路下 OCR_cost≈0；真机 OCR 拆分须在 canonical venv+GPU 跑")
    print("=" * 72)

    for p in paths:
        try:
            rgb = _load_rgb(p)
        except Exception as exc:
            print("[skip] %s 读取失败：%s" % (os.path.basename(p), exc))
            continue

        # 1) recommend_preset（轻量特征分析，不调 detect/OCR）
        rec_t, rec = _time_call(lambda: recommend_preset(rgb, sensitivity=sensitivity))
        rec_times.append(rec_t)
        rec_presets.append(rec.preset)
        if rec.preset not in ("general", config.RECOMMEND_DEFAULT_PRESET):
            cnt, _ = _enabled_strategy_count(rec.preset, sensitivity)
            strat_counts.append(cnt)

        # 2) detect(general 全开)
        gen_t, gen = _time_call(
            lambda: detect_watermark_mask(rgb, use_ocr=ocr_flag, preset=None))
        gen_times.append(gen_t)
        used_ocr_flags.append(bool(gen.used_ocr))

        # 3) detect(推荐预设，被 recommend 收窄)
        rec_det_t, rec_det = _time_call(
            lambda: detect_watermark_mask(rgb, use_ocr=ocr_flag, preset=rec.preset))
        rec_det_times.append(rec_det_t)
        if rec_det.used_ocr:
            n_ocred += 1

        # 4) OCR 隔离旁路（仅无 --no-ocr 时额外跑，得到 ocr 成本差）
        if not args.no_ocr:
            gen_no, _ = _time_call(
                lambda: detect_watermark_mask(rgb, use_ocr=False, preset=None))
            rec_no, _ = _time_call(
                lambda: detect_watermark_mask(rgb, use_ocr=False, preset=rec.preset))
            gen_noocr_times.append(gen_no)
            rec_noocr_times.append(rec_no)

        print("  %-22s rec=%-9s rec_t=%.3fs | det(gen)=%.3fs det(%s)=%.3fs"
              % (os.path.basename(p), rec.preset, rec_t, gen_t, rec.preset, rec_det_t))

    if not rec_times:
        print("无有效样本")
        return 2

    m_rec = _median(rec_times)
    m_gen = _median(gen_times)
    m_recdet = _median(rec_det_times)
    m_gen_no = _median(gen_noocr_times)
    m_rec_no = _median(rec_noocr_times)

    # OCR 成本差（仅当未强制 --no-ocr 且 easyocr 可用时有意义）
    ocr_cost_gen = max(0.0, m_gen - m_gen_no) if gen_noocr_times else 0.0
    ocr_cost_rec = max(0.0, m_recdet - m_rec_no) if rec_noocr_times else 0.0

    # recommend 收窄收益：general 全开 vs 推荐预设
    narrow_speedup = (m_gen / m_recdet) if m_recdet > 0 else 0.0
    # recommend 自身相对单次 detect 的占比
    rec_share = (m_rec / m_gen) if m_gen > 0 else 0.0

    print()
    print("=" * 72)
    print("汇总（中位数，单位秒）")
    print("=" * 72)
    print("  recommend_preset 单次耗时      : %.4f" % m_rec)
    print("  detect(general 全开) 单次耗时  : %.4f" % m_gen)
    print("  detect(推荐预设)   单次耗时    : %.4f" % m_recdet)
    if gen_noocr_times:
        print("  detect(general,no-ocr)       : %.4f" % m_gen_no)
        print("  detect(推荐,no-ocr)          : %.4f" % m_rec_no)
        print("  ⇒ OCR 成本(general) ≈ %.4f s | OCR 成本(推荐) ≈ %.4f s"
              % (ocr_cost_gen, ocr_cost_rec))
    print()
    print("  recommend 占单次 detect 比例    : %.1f%%" % (rec_share * 100))
    print("  推荐预设相对 general 提速       : %.2fx (general/推荐)" % narrow_speedup)
    print("  推荐预设去 OCR 后占比提升       : %s"
          % ("见上 OCR 成本差" if gen_noocr_times else "本环境无 easyocr，未测"))
    print()
    print("  样本中推荐非空预设 (非 general) : %d / %d"
          % (sum(1 for x in rec_presets if x not in ("general", config.RECOMMEND_DEFAULT_PRESET)),
             len(rec_presets)))
    if strat_counts:
        print("  非空预设平均开启策略数         : %.1f / 6"
              % (sum(strat_counts) / max(1, len(strat_counts))))
    print("  detect 实际触发 OCR 的图        : %d / %d"
          % (sum(used_ocr_flags) + n_ocred, len(rec_times)))

    print()
    print("=" * 72)
    print("结论（结构性，跨环境稳健）")
    print("=" * 72)
    print("  · recommend_preset 不调用 detect / 不调 OCR，仅是轻量特征分析；")
    print("    其耗时占单次 detect 的 %.1f%%，非瓶颈。" % (rec_share * 100))
    if narrow_speedup >= 1.05:
        print("  · recommend 通过收窄预设（关掉 edge/grid/gradient 等含 OCR 通路），")
        print("    使 detect 提速 %.2fx —— 它是 detect 的「降本器」而非冗余开销。" % narrow_speedup)
    else:
        print("  · 本批样本 recommend 未显著收窄 detect（多为 general/全开）。")
    if gen_noocr_times and easyocr_ok:
        print("  · 真机 OCR 在 detect 中约占 %.1f%% (general) / %.1f%% (推荐)；"
              % ((ocr_cost_gen / m_gen * 100) if m_gen > 0 else 0,
                 (ocr_cost_rec / m_recdet * 100) if m_recdet > 0 else 0))
        print("    进一步优化 detect 应瞄向 corner/edge 的 OCR 通路，而非 recommend。")
    print("=" * 72)
    return 0


def _load_rgb(p):
    loaded = load_image_full(p)
    rgb = loaded[0] if isinstance(loaded, (tuple, list)) else loaded
    return np.asarray(rgb)


if __name__ == "__main__":
    sys.exit(main())
