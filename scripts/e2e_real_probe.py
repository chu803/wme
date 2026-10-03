"""真实底图 × 合成水印 —— 端到端真值量测（修复侧）。

文件说明
--------
本脚本回答一个此前**从未被量测过**的问题：

    「输入一张带水印的图，它返回的图**离干净原图有多远**？」

即产品对外承诺的「去痕并保住本质」这条链路，在**真实照片底图**上到底做得怎样。

与既有两条门禁的关系（三者互补，不重叠）
----------------------------------------
| 脚本 | 底图 | 量测侧 | 真值 |
|---|---|---|---|
| `scripts/quality_gate.py` | 程序化合成（make_base/textured） | 检测 + 修复 + 交付链 | 有 |
| `scripts/real_gate.py` | 真实照片（平台真水印） | **仅检测** | 30 图 60 标注（全角标） |
| **本脚本** | **真实照片** | **仅修复（端到端）** | 逐像素（由差值导出） |

也就是说：`real_gate` 覆盖了「真实底图 × 检测」，`quality_gate` 覆盖了「合成底图 × 检测+修复」，
**「真实底图 × 修复」这块此前是空白**——而它恰好是用户唯一能直接看到的东西。

为什么必须把「去痕」与「保真」拆成两个区域级指标
------------------------------------------------
全图 PSNR / ΔPSNR 是**面积加权**的，会被大片未改动区域稀释：
水印区通常只占全图 0.2%~2%，即使去得很干净，全图 PSNR 也涨不了多少；
反之，非水印区只要被大面积轻改，全图 PSNR 就会掉。
⇒ 全图 ΔPSNR **在本任务上会给出与体验相反的结论**（与 bbox-IoU 对长条带目标失真是同类问题）。
故本脚本只报两个互不稀释的区域级指标：

    removal    = 1 - MAE(修复后, 干净) / MAE(加印后, 干净)   仅统计**真值水印像素**
                 1.0 = 完全去干净；0.0 = 一点没去掉；<0 = 反而更糟
    collateral = MAE(修复后, 干净)                            仅统计**非水印像素**（真值膨胀 4px 后取补）
                 0 = 完全没动到无水印区域（理想）；越大 = 越伤「本质」
    overdraw   = 检测掩码像素 / 真值水印像素                    多擦了几倍面积（1.0 = 刚好）
    spill      = 改动且落在**给定掩码外**的像素 / 全部改动像素     修复自身的外溢（阶段 105 接入）
                 机制上 = support(proc_mask) 减去掩码，与引擎无关：
                 `inpaint()` 的融合是 `image*(1-m3*s) + result*(m3*s)`，m3=0 处逐字节等于原图，
                 故 改动区 ⊆ support(proc_mask)；而 `proc_mask` 由 dilate+feather 生成
                 ⇒ spill 的下界 ≈ D·周长/面积（D=膨胀像素）。这就是「外溢 ≤5%」与 D≥1
                 不可兼得的原因（`docs/105-*`）。
    gain_db    = PSNR(修复后, 干净) - PSNR(加印后, 干净)        全图净提升，**仅供参考，勿作判据**

用法
----
    python scripts/e2e_real_probe.py                 # 默认 4 张底图 × 4 类水印，LaMa
    python scripts/e2e_real_probe.py --limit 8 --engine opencv
    python scripts/e2e_real_probe.py --json out.json

边界（诚实声明）
----------------
1. **合成水印 ≠ 平台真水印**。字形/透明度/混合方式由平台决定，本脚本只能证明「在这一族合成
   水印上、在真实底图上」的表现，**不能**替代平台真实水印图。
2. 真值掩码由 `|加印 - 干净| > 0` 导出，因此**完全不可见的像素**（例如极淡白点在纯白区域）
   不计入真值——这与「那里本来就没有可去掉的东西」一致，但意味着真值略偏小。
3. 底图统一缩到长边 640，与 `quality_gate.py` 的 `size` 默认值一致，**以便两组数字的差异
   只来自「底图真不真」这一个变量**。
4. 本脚本默认**只量测**；加 `--gate` 后按下方红线判定，**并**与基线快照比对漂移。
   红线的标定口径：`--gate` 使用固定的 `--limit 10`（4 张样本不足以支撑红线），
   红线值取自 10 张底图实测基线并留出固定余量（removal ×0.75、collateral ×1.30、
   overdraw ×1.30），**不随本次运行结果自算**——门禁若用"当前值"当阈值，等于没有门禁。

红线与「优秀线」是两层，不要混淆
--------------------------------
· **红线（本脚本 `--gate`）**：不许变坏。只守「已达成的事实」，方向单侧
  （removal 只守下界、collateral/overdraw 只守上界），因此任何**改进都不会被判失败**。
· **优秀线（`ASPIRATIONAL`，仅打印不判定）**：removal ≥90% / collateral ≤0.20 /
  overdraw ∈[0.9,1.5]。当前**无一类达标**，故不能写成红线——写成红线会让门禁恒红，
  而恒红的门禁与恒绿的门禁一样是哑巴（阶段 95 教训）。待阶段 103 达标后再升格。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.detector import detect_watermark_mask          # noqa: E402
from core.image_utils import load_image                   # noqa: E402
from core.inpainter import inpaint                        # noqa: E402
from scripts.make_demo_assets import (                    # noqa: E402
    CASES,
    mae,
    psnr,
    wm_corner_logo,
    wm_edge_text,
    wm_faint_tiled,
    wm_grid,
)
from scripts import baseline_guard                         # noqa: E402  # 阶段 146：刷新基线护栏

ROOT = _ROOT
REAL_IMG_DIR = os.path.join(ROOT, "benchmark_real", "images")
SIDE = 640                      # 与 quality_gate 默认 size 对齐
BOUNDARY_PAD = 4                # 计算 collateral 时把真值膨胀这么多像素后取补

# ---------------------------------------------------------------------------
# 门禁参数（阶段 103 标定）
# ---------------------------------------------------------------------------
E2E_GATE_LIMIT = 10             # 门禁固定用 10 张底图；4 张样本量不足以支撑红线
E2E_GATE_ENGINE = "lama"        # 红线在 LaMa 上标定；换引擎需重新标定
BASELINE_PATH = os.path.join(ROOT, "benchmark_real", "e2e_baseline.json")

# 红线：单侧，方向 = 「不许变坏」。因此任何**改进**都不会被判失败。
#   removal   : 下界（低了变坏）
#   collateral: 上界（高了变坏）
#   overdraw  : 上界（高了变坏）
#
# 标定口径：10 张底图实测基线，留固定余量 —— removal ×0.75、collateral ×1.30、
# overdraw ×1.30，再向**宽松侧**取到 0.05 的整数倍（保证红线不会因四舍五入变严）。
#
# 标定历史（务必保留，否则后人无法判断红线为何是这些数）：
#   · 阶段 103 首次标定，基线取自**改动前**实测：
#       faint_tiled  removal -0.142  collateral 4.513  overdraw  2.61
#       corner_logo  removal  0.358  collateral 1.226  overdraw  0.89
#       edge_text    removal  0.228  collateral 1.657  overdraw  6.87
#       grid         removal  0.475  collateral 4.784  overdraw 11.74
#   · 阶段 103 落地「框内证据二次确认」后**重标**（基线刷新 + 红线收紧），
#     依据：改进若不刷新基线，日后静默回退时基线比对为「无变化」⇒ 门禁全绿（阶段 75 教训）。
#     实测：edge_text overdraw 6.87→5.00、collateral 1.657→1.423；grid 11.74→11.60。
#   · 阶段 104 再次标定（平铺增强通路接上 82/84 两道连通域守卫后）：
#       faint_tiled  removal -0.139 → **-0.055**、collateral 4.530 → **2.478**、
#                    overdraw  2.581 → **1.382**（首次进入「合格 ≤1.5」区间）
#       grid         removal  0.478 →  0.480、collateral 4.801 → **2.233**、
#                    overdraw 11.604 → **5.954**
#       corner_logo / edge_text **逐项不变**（两个预设不启用平铺通路 ⇒ 改动无外溢）
#     收紧口径：只收紧本次被直接改善的 collateral / overdraw（×1.30 再向宽松侧取到
#     0.05 整数倍）；**removal 的红线保持不动**——removal 依赖修复模型行为、噪声更大，
#     细粒度守备交给刷新后的基线漂移层（15% 相对容差）即可，避免「过紧的门禁偶发红灯」。
#   · 阶段 104 **再标定**（把「两道守卫」收敛为「只接纹理稠密守卫 + 兜底臂豁免」后）：
#       faint_tiled  removal -0.055 → **-0.037**、collateral 2.478 → 2.512、
#                    overdraw  1.382 → 1.445
#       grid         removal  0.480 →  0.494、collateral 2.233 → 2.257、
#                    overdraw  5.954 → 6.206
#     为什么会有这次收敛：单守卫隔离实验（docs/104 §8）显示自然光影守卫在真实照片上
#     只贡献 26% 的削减（纹理守卫 72.9%），却会把「纹理 + 极淡平铺」整类抹零、
#     把「渐变 + 网格」砍掉 28% 召回 ⇒ 去掉它：**removal 变好**（-0.055 → -0.037，
#     多留住了真水印像素），collateral / overdraw 只回退 1~4%（红线仍在 ±31% 余量内）。
#     按「只收紧被直接改善的项」原则，两类红线同步按 ×1.30 重标（collateral 3.25→3.30 /
#     2.90→2.95，overdraw 1.80→1.90 / 7.75→8.10）。
#   · 阶段 105 **新增第 4 条量并标定**（`spill` = 改动落在给定掩码外的比例）：
#     把 `MASK_DILATE_DEFAULT` 由 2 归零、并让羽化只在外侧加软坡（`_prepare_mask` 第 4 步
#     `max(soft, binary)`）后，10 图实测：
#       spill 29.3% → 0.7%、removal 24.3% → 25.9%、collateral 2.198 → 1.877、
#       ΔPSNR −7.22 → −6.84 dB（四项同时改善，故无取舍争议）
#     spill 红线取 **5%**：这是本项目唯一一条以「目标」而非「实测×余量」为口径的红线 ——
#     因为 spill 是可解析的机制量（下界 ≈ D·周长/面积），5% 与「不膨胀」在数学上完全兼容，
#     而按实测 0.7%×1.3≈0.9% 标线会让门禁在浮点噪声上乱响（与阶段 95「恒红=哑巴」同理）。
#   · 阶段 106 **掩码—实体对齐收尾**（`overdraw` 双向失准收敛），三条落地改动：
#       ① 网格折叠模板门限乘 1.3（`DETECT_GRID_TMPL_K_MULT`）：模板上残留的
#          「纹理经 N 个 tile 平均后未压干净的伪结构」被滤掉 ⇒ 网格通路 FP −76%、TP 不变；
#       ② 框内证据外形膨胀 `DETECT_BOX_REFINE_PAD` 2 → **1**（pad=2 时证据像素变 5×5 块，
#          对 1~2px 笔画是约 5× 面积放大；角落框证据块够大，三档逐位相同）；
#       ③ 框内相对门限 `DETECT_BOX_REFINE_K_SCALE` 0.5 → **0.65**（pad 收敛后
#          「1.0 会伤 faint_tiled」的旧耦合消失，0.65 在新几何下**单侧占优**）。
#     10 图实测（每类：overdraw / removal / collateral）：
#       faint_tiled 1.445→**1.187** / −0.0174→**+0.0041** / 2.129→**1.815**
#       corner_logo **逐项不变**（0.886 / 0.3555 / 1.053）——唯一承诺类型零扰动
#       edge_text   4.996→**3.799** / 0.2407→**0.2415** / 1.193→**1.024**
#       grid        6.206→**5.015** / 0.5026→**0.5063** / 1.727→**1.515**
#     ⇒ 四类**每一项都不退化**（唯一上升的是近零的 spill：0.006~0.009 → 0.007~0.012，仍 ≪5%）。
#     红线重标采用**双约束**（阶段 106 新增的第二条，见下）：`red ≈ min(新值×1.15, 旧值×0.95)`，
#     再向宽松侧取到 0.05 的整数倍 ——
#       overdraw  1.90→1.40 / 6.50→4.40 / 8.10→5.80（corner 1.16 不变，本次未被改善）
#       collateral 2.80→2.05 / 1.60→1.15 / 2.25→1.65（corner 1.40 不变）
#     removal / spill 红线**保持不动**（removal 依赖修复模型行为、噪声更大，细粒度守备交给
#     刷新后的基线漂移层；spill 已是机制量口径）。
#
# ⚠ **为什么阶段 106 起用「双约束」而不是单纯的 ×1.30**（方法论升级，务必保留）：
#   旧规则「新值 × 1.30」只满足约束①「不能因噪声误判」（宽松）。它在**改进幅度 ≥30%** 时
#   自动满足约束②「改动回退必须被判红」；但本阶段的改进幅度只有 20~24%，×1.30 会让
#   **回退后的数值落在红线之内**（例：grid overdraw 旧值 6.206 < 1.30×5.015=6.52）
#   ⇒ 红线形同虚设，回退只能靠 15% 的漂移层兜（而 grid collateral 回退仅 +14.0%，
#   连漂移层也抓不住）——这正是经验 75「收敛必须重标红线，否则改进可静默回退」要防的事。
#   故新增约束②：**红线必须低于「改动前实测值」**（留 5% 余量），即
#   `red ≤ 旧值 × 0.95`；在两条约束的交集里取最宽松者（都按 0.05 整数倍向上取整）。
#   校验：本阶段六个被重标项的回退值分别为 1.445 / 4.996 / 6.206 / 2.129 / 1.193 / 1.727，
#   全部 > 对应红线 1.40 / 4.40 / 5.80 / 2.05 / 1.15 / 1.65 ⇒ 回退必红。
#   （未被改善的项不重标：corner_logo 两项保持 1.40 / 1.16，其基线漂移层同样有效。）
E2E_LIMITS = {
    "faint_tiled": {"removal": -0.15, "collateral": 2.05, "overdraw": 1.40, "spill": 0.05},
    "corner_logo": {"removal": 0.25, "collateral": 1.40, "overdraw": 1.16, "spill": 0.05},
    "edge_text": {"removal": 0.15, "collateral": 1.15, "overdraw": 4.40, "spill": 0.05},
    "grid": {"removal": 0.35, "collateral": 1.65, "overdraw": 5.80, "spill": 0.05},
}

# 阶段 106 **被数据否决的方案**（登记，防止后人重犯）：把合并阶段收口膨胀
# （`DETECT_MERGE_DILATE`）设为 0 能再降 overdraw（corner 0.886→0.669），
# 但**把 corner_logo 的 removal 从 0.3555 打到 0.2264（−36%，越过本表红线 0.25）**，
# 同时 edge/grid/faint 的 removal 也各掉 8~10% ⇒ 该 1px 覆盖的是水印抗锯齿/羽化边缘，
# 是**承重结构，不可关闭**（探针 `%TEMP%/s106_mergeG.py`）。
# 阶段 105 收紧依据（×1.30 再向宽松侧取到 0.05 的整数倍）：四类 collateral 全部被本次改动
# 直接改善 —— faint_tiled 2.512→2.129、corner_logo 1.226→1.053、edge_text 1.423→1.193、
# grid 2.257→1.727。removal / overdraw 的红线**保持不动**（前者依赖修复模型行为、噪声更大，
# 细粒度守备交给刷新后的基线漂移层；后者本次实测与基线几乎相同）。

# overdraw 只守上界、不设下界，理由：下界方向（掩码偏小）必然压低 removal，而 removal
# 已有下界红线，属**传递性守备**；若同时设下界，则「把 11.60 降到 1.2」这类真正想要的
# 改进反而会被判失败。
E2E_DRIFT_TOL = 0.15            # 相对漂移容差，只查「朝红线劣化方向」的漂移
#: 近零指标的**绝对**漂移容差下限：spill 基线 ≈0.007，相对容差会在浮点噪声上乱响。
#: 取 max(相对容差, 本表值) 作为该指标的有效容差（2pp）。
E2E_DRIFT_ABS = {"spill": 0.02}

# ---------------------------------------------------------------------------
# 阶段 146 · 刷新基线的**向下刷新护栏**
# ---------------------------------------------------------------------------
# 本脚本的 `--update-baseline` **已经**做了「刷新后继续走门禁判定」（见 main 里的
# 注释：「否则刷新基线的动作会绕过红线检查，成为一条静默通道」）—— 这一层是
# `quality_gate` / `real_gate` 漏掉、本阶段向它们对齐的部分。
#
# 但**向下刷新护栏**三个门禁此前都没有：没有任何机制阻止把某个指标合法化到一个
# 更坏的值（每次刷新都在容差内 ⇒ 渐进刷新可把指标推到任意低值而门禁全程绿）。
#
# 规则表**完全复用漂移层的键、方向与容差**（`E2E_DRIFT_TOL` + `E2E_DRIFT_ABS`，
# 方向与 `evaluate_gate` 漂移段的 `worse` 表一一对应）⇒ 与漂移层**同口径**，
# 不会出现「漂移层判它违规、刷新层却放它写入」的自相矛盾。
_E2E_REFRESH_DIRECTIONS: dict = {
    "removal": "min",        # 越高越好
    "collateral": "max",     # 越低越好
    "overdraw": "max",
    "spill": "max",
}


def _refresh_rules_for(pairs) -> dict:
    """按 pairs 里的 `<case>.<key>` 生成规则表（容差/方向取自静态表）。"""
    rules: dict = {}
    for key, _o, _n in pairs:
        metric = key.rsplit(".", 1)[-1]
        if metric in _E2E_REFRESH_DIRECTIONS:
            rules[key] = (_E2E_REFRESH_DIRECTIONS[metric],
                          E2E_DRIFT_TOL, E2E_DRIFT_ABS.get(metric, 0.0))
    return rules


def _refresh_pairs(old_agg, new_agg) -> list:
    """把两侧**按 case 的 list** 提取成 `(case.key, 旧值, 新值)` 序列。"""
    def _index(rows):
        return {b["case"]: b for b in (rows or [])
                if isinstance(b, dict) and "case" in b}
    old, new = _index(old_agg), _index(new_agg)
    pairs: list = []
    for case in sorted(set(old) & set(new)):
        for metric in sorted(_E2E_REFRESH_DIRECTIONS):
            if metric in old[case] and metric in new[case]:
                pairs.append((f"{case}.{metric}", old[case][metric], new[case][metric]))
    return pairs


# 优秀线：只打印、不判定（当前无一类达标）。写成红线会让门禁恒红。
ASPIRATIONAL = {"removal": 0.90, "collateral": 0.20,
                "overdraw_lo": 0.9, "overdraw_hi": 1.5}

_WM = {
    "faint_tiled": wm_faint_tiled,
    "corner_logo": wm_corner_logo,
    "edge_text": wm_edge_text,
    "grid": wm_grid,
}


def _resize_long_side(rgb: np.ndarray, side: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    k = side / float(max(h, w))
    if k == 1.0:
        return rgb
    return cv2.resize(rgb, (max(1, int(round(w * k))), max(1, int(round(h * k)))),
                      interpolation=cv2.INTER_AREA)


def _dilate(bin_mask: np.ndarray, k: int) -> np.ndarray:
    ker = np.ones((2 * k + 1, 2 * k + 1), np.uint8)
    return cv2.dilate(bin_mask.astype(np.uint8), ker, iterations=1) > 0


def _mae_region(a: np.ndarray, b: np.ndarray, sel: np.ndarray) -> "float | None":
    """只在 sel（bool）内统计平均绝对误差；空选区返回 None。"""
    if sel is None or not np.any(sel):
        return None
    d = np.abs(a.astype(np.float64) - b.astype(np.float64)).mean(axis=2)
    return float(d[sel].mean())


def run_case(clean: np.ndarray, case: str, engine: str) -> dict:
    """单个用例：加印 → 检测 → 修复 → 与干净原图逐像素比对。"""
    marked = _WM[case](clean)
    truth = np.any(marked != clean, axis=2)                 # 真值 = 真正被改动的像素
    if not np.any(truth):
        return {"case": case, "status": "no_truth"}

    t0 = time.time()
    res = detect_watermark_mask(marked, preset=CASES[case][1], use_ocr=False)
    t_det = time.time() - t0
    pred = np.asarray(res.mask) > 0

    row = {
        "case": case, "preset": CASES[case][1], "engine": engine,
        "truth_cov": float(truth.sum()) / truth.size,
        "pred_cov": float(pred.sum()) / pred.size,
        "overdraw": (float(pred.sum()) / float(truth.sum())) if truth.sum() else 0.0,
        "t_detect_s": round(t_det, 2),
        "gain_db": None, "removal": None, "collateral": None, "spill": None,
    }

    if not np.any(pred):
        row["status"] = "empty_mask"
        return row

    t0 = time.time()
    try:
        repaired = inpaint(marked, res.mask, engine=engine)
    except Exception as exc:                                # noqa: BLE001
        row["status"] = f"inpaint_error:{type(exc).__name__}"
        return row
    row["t_inpaint_s"] = round(time.time() - t0, 2)

    before = _mae_region(marked, clean, truth)
    after = _mae_region(repaired, clean, truth)
    row["removal"] = None if not before else round(1.0 - after / before, 4)
    row["collateral"] = _mae_region(repaired, clean, ~_dilate(truth, BOUNDARY_PAD))
    row["gain_db"] = round(psnr(repaired, clean) - psnr(marked, clean), 3)
    # 阶段 105：修复自身的外溢（改动落在**给定掩码之外**的比例，与引擎无关）。
    changed = np.any(repaired != marked, axis=2)
    n_chg = int(changed.sum())
    row["changed_px"] = n_chg
    # n_chg == 0 ⇒ 一个像素都没改 ⇒ 外溢必然是 0（而不是「未知」），用 0.0 参与聚合。
    row["spill"] = (float(np.count_nonzero(changed & ~pred)) / n_chg) if n_chg else 0.0
    row["mae_truth_before"] = round(before, 3)
    row["mae_truth_after"] = round(after, 3)
    row["status"] = "ok"
    return row


def aggregate(rows: list) -> list:
    """按水印类型聚合。只对 status == ok 的行取均值。"""
    agg = []
    for case in CASES:
        rs = [r for r in rows if r["case"] == case and r.get("status") == "ok"]
        base = {
            "case": case, "preset": CASES[case][1], "n": len(rs),
            "n_total": len([r for r in rows if r["case"] == case]),
        }
        if rs:
            mean = lambda k: round(sum(r[k] for r in rs) / len(rs), 4)   # noqa: E731
            base.update({
                "truth_cov": round(sum(r["truth_cov"] for r in rs) / len(rs), 5),
                "pred_cov": round(sum(r["pred_cov"] for r in rs) / len(rs), 5),
                "overdraw": mean("overdraw"),
                "removal": mean("removal"),
                "collateral": mean("collateral"),
                "spill": mean("spill"),
                "gain_db": mean("gain_db"),
            })
        agg.append(base)
    return agg


def to_markdown(agg: list) -> str:
    lines = [
        "| 水印类型 | 预设 | n | 真值覆盖% | 检出覆盖% | 多擦× | 去痕率 | 非水印区改动 | 外溢率 | 全图ΔPSNR(参考) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in agg:
        if not a.get("n"):
            lines.append(f"| {a['case']} | {a['preset']} | 0/{a['n_total']} | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {a['case']} | {a['preset']} | {a['n']} "
            f"| {a['truth_cov'] * 100:.3f} | {a['pred_cov'] * 100:.3f} "
            f"| {a['overdraw']:.2f} | {a['removal'] * 100:.1f}% "
            f"| {a['collateral']:.3f} | {a['spill'] * 100:.1f}% "
            f"| {a['gain_db']:+.2f} dB |"
        )
    return "\n".join(lines)


def _load_baseline(path: str = BASELINE_PATH) -> "dict | None":
    """读取基线快照；不存在时返回 None（首次运行允许无基线）。"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:                                    # noqa: BLE001
        print(f"[warn] 基线快照不可读（{exc}），本次只判定红线、跳过漂移层。")
        return None


def _save_baseline(agg: list, engine: str, limit: int,
                   path: str = BASELINE_PATH) -> None:
    """写入基线快照（供下一轮漂移比对）。"""
    payload = {
        "engine": engine, "side": SIDE, "limit": limit,
        "aggregate": agg,
        "note": ("e2e 修复侧基线；红线 E2E_LIMITS 由本快照标定。"
                 "用 --update-baseline 刷新（刷新前必须确认新值是有意为之）。"),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"基线快照已写入 {path}")


def evaluate_gate(agg: list, baseline: "dict | None") -> "tuple[list, list, list]":
    """三层判定：① 数据完整性 ② 绝对红线 ③ 基线漂移。

    返回 (fails, notes, skipped)。
    · fails   : 必须让门禁变红的条目。
    · notes   : 朝红线**反侧**的显著变化（即改进），仅提示，**不判定**——
                否则「把 overdraw 从 11.74 降到 1.2」这类目标改进会被判失败。
    · skipped : 因环境/数据原因无法判定的条目（计入退出码 2 的依据）。
    """
    fails: list = []
    notes: list = []
    skipped: list = []

    base = {b["case"]: b for b in ((baseline or {}).get("aggregate") or [])}
    if baseline is None:
        skipped.append("无基线快照：只判定红线，漂移层跳过")
    else:
        if baseline.get("engine") != E2E_GATE_ENGINE:
            fails.append(f"基线引擎为 {baseline.get('engine')}，"
                         f"当前 {E2E_GATE_ENGINE} —— 引擎不同则数字不可比，请重标基线")
        if baseline.get("side") != SIDE:
            fails.append(f"基线 side={baseline.get('side')}，当前 {SIDE} —— 尺度不同不可比")

    for a in agg:
        case = a["case"]
        lim = E2E_LIMITS.get(case)
        if lim is None:
            fails.append(f"{case}: 未登记红线（新增水印类型必须同时标定红线）")
            continue
        if not a.get("n"):
            fails.append(f"{case}: 0/{a['n_total']} 用例可评（空掩码或修复失败）")
            continue
        # ---- 绝对红线（单侧，方向 = 不许变坏）----
        for key, worse in (("removal", "down"), ("collateral", "up"),
                           ("overdraw", "up"), ("spill", "up")):
            v, t = a.get(key), lim.get(key)
            if v is None or t is None:
                continue
            if (worse == "down" and v < t) or (worse == "up" and v > t):
                cmp_s = "<" if worse == "down" else ">"
                fails.append(f"[红线] {case}.{key} = {v:g} {cmp_s} {t:g}")
        # ---- 基线漂移（只查朝劣化方向）----
        b = base.get(case)
        if b and b.get("n"):
            for key, worse in (("removal", "down"), ("collateral", "up"),
                               ("overdraw", "up"), ("spill", "up")):
                v, bv = a.get(key), b.get(key)
                if v is None or bv is None or bv == 0:
                    continue
                tol = max(E2E_DRIFT_TOL * abs(bv), E2E_DRIFT_ABS.get(key, 0.0))
                rel = (v - bv) / abs(bv)
                if (worse == "down" and v < bv - tol) or \
                   (worse == "up" and v > bv + tol):
                    fails.append(f"[漂移] {case}.{key} = {v:g}（基线 {bv:g}，"
                                 f"相对 {rel:+.1%} 超容差 {tol:g}）")
                elif abs(rel) > E2E_DRIFT_TOL and abs(v - bv) > tol:
                    notes.append(f"{case}.{key} = {v:g}（基线 {bv:g}，{rel:+.1%}，"
                                 f"朝红线反侧 ⇒ 改进）")
    return fails, notes, skipped


def gate_exit_code(fails: list, skipped: list) -> int:
    """把 (fails, skipped) 映射到门禁退出码：有失败→1，有跳过→2，否则→0。

    阶段 171（门禁退出码契约审计）：此前 `main` 只把 `fails` 变成 1、其余一律 0
    —— 于是「基线缺失 ⇒ 漂移层未判」这一**跳过**情形被读成 exit 0（绿），
    与本脚本 `--gate` 帮助文本「退出码 0 通过 / 1 失败 / 2 跳过」及
    `evaluate_gate` 文档「skipped 计入退出码 2 的依据」**自相矛盾**，
    也与同族 `prompted_area_gate`（基线缺失返回 2）**不对称**
    （经验 185：判据不对称即 bug；经验「静默通过是缺陷温床」）。

    失败优先于跳过：有失败时返回 1（跳过信息仍会打印）。
    """
    if fails:
        return 1
    if skipped:
        return 2
    return 0


def aspirational_table(agg: list) -> str:
    """优秀线差距表（只展示，不判定）。"""
    lines = ["| 水印类型 | 去痕率 → 90% | 非水印区改动 → ≤0.20 | 多擦× → 0.9~1.5 |",
             "|---|---|---|---|"]
    for a in agg:
        if not a.get("n"):
            continue
        r = a.get("removal")
        c = a.get("collateral")
        o = a.get("overdraw")
        gap_r = f"{r * 100:.1f}%" + ("" if r >= ASPIRATIONAL["removal"]
                                    else f"（差 {ASPIRATIONAL['removal'] - r:.3f}）")
        gap_c = f"{c:.3f}" + ("" if c <= ASPIRATIONAL["collateral"]
                              else f"（{c / ASPIRATIONAL['collateral']:.1f}×）")
        gap_o = f"{o:.2f}" + ("" if ASPIRATIONAL["overdraw_lo"] <= o
                              <= ASPIRATIONAL["overdraw_hi"] else "（区间外）")
        lines.append(f"| {a['case']} | {gap_r} | {gap_c} | {gap_o} |")
    return "\n".join(lines)


def main() -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="真实底图 × 合成水印端到端真值量测")
    ap.add_argument("--limit", type=int, default=4, help="用几张真实底图（默认 4）")
    ap.add_argument("--engine", default="lama", choices=["lama", "opencv", "auto"])
    ap.add_argument("--json", default=None, help="结果 JSON 落盘路径（可选）")
    ap.add_argument("--gate", action="store_true",
                    help=f"门禁模式：强制 limit={E2E_GATE_LIMIT} / engine={E2E_GATE_ENGINE}，"
                         "按红线 + 漂移判定，退出码 0 通过 / 1 失败 / 2 跳过")
    ap.add_argument("--update-baseline", action="store_true",
                    help=f"把本次结果写成基线快照 {BASELINE_PATH}（需人工确认后使用）")
    ap.add_argument("--force-update", action="store_true",
                    help="与 --update-baseline 同用：跳过「向下刷新护栏」显式覆盖基线（阶段 146）")
    a = ap.parse_args()

    limit, engine = a.limit, a.engine
    if a.gate:
        # 门禁必须可复现：口径由常量钉死，不允许被命令行改动（否则是拿苹果比橘子）。
        if limit != E2E_GATE_LIMIT:
            print(f"[warn] 门禁模式忽略 --limit {limit}，强制用 {E2E_GATE_LIMIT}"
                  f"（红线即在此口径下标定）。")
            limit = E2E_GATE_LIMIT
        if engine != E2E_GATE_ENGINE:
            print(f"[warn] 门禁模式忽略 --engine {engine}，强制用 {E2E_GATE_ENGINE}。")
            engine = E2E_GATE_ENGINE

    files = sorted(f for f in os.listdir(REAL_IMG_DIR)
                   if f.lower().endswith((".jpg", ".jpeg", ".png")))[:limit]
    if not files:
        print(f"[error] 真实底图目录为空：{REAL_IMG_DIR}")
        return 2
    if a.gate and len(files) < E2E_GATE_LIMIT:
        print(f"[error] 门禁需 {E2E_GATE_LIMIT} 张底图，实际只有 {len(files)} 张 ⇒ 跳过判定。")
        return 2

    rows = []
    for fn in files:
        clean = _resize_long_side(load_image(os.path.join(REAL_IMG_DIR, fn)), SIDE)
        for case in CASES:
            r = run_case(clean, case, engine)
            r["base"] = fn
            rows.append(r)
            print(f"  {fn[:22]:<24}{case:<13}{r.get('status'):<16}"
                  f"removal={r.get('removal')} overdraw={round(r.get('overdraw', 0), 2)}")

    agg = aggregate(rows)
    md = to_markdown(agg)
    print()
    print(f"真实底图 {len(files)} 张 × {len(CASES)} 类水印，引擎 {engine}，长边 {SIDE}")
    print(md)
    print()
    print("判读（只看 removal / collateral / overdraw / spill，不要看全图 ΔPSNR）：")
    print("  removal    >= 90%  -> 去痕合格；< 50% -> 基本没去掉；< 0 -> 反而更糟")
    print("  collateral <= 0.20 -> 本质保真良好；> 0.50 -> 肉眼可见地擦到了不该擦的地方")
    print("  overdraw   <= 1.5  -> 面积控制合格；> 3 -> 大面积误擦")
    print("  spill      <= 5%   -> 修复外溢合格（红线；≈膨胀像素×周长/掩码面积）")
    print()
    print("距「优秀线」的量化差距（展示用，不判定）：")
    print(aspirational_table(agg))

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"engine": engine, "side": SIDE, "limit": limit,
                       "rows": rows, "aggregate": agg}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nJSON 已写入 {a.json}")

    if a.update_baseline:
        # 阶段 146：刷新基线是**受护栏约束的动作**。
        #   ① **向下刷新护栏**（新增；必须在写盘**之前**判定，否则基线已被污染）：
        #      本次结果若把某指标合法化到更坏的值（超出漂移层容差）⇒ 拒绝写入。
        #   ② **无条件继续走判定** —— 此前这里是 `_save_baseline(...)`
        #      然后 `if not a.gate: return 0`：**不加 `--gate` 时刷新仍是静默通道**
        #      （本脚本对「刷新绕过红线」的处理是对的，但被 `--gate` 开关限定了范围）。
        if not a.force_update:
            _old = _load_baseline()
            _pk = _refresh_pairs((_old or {}).get("aggregate"), agg)
            _down = baseline_guard.check_downward_pairs(_pk, _refresh_rules_for(_pk))
            if _down:
                for _line in baseline_guard.render_refusal(
                        _down, baseline_label="e2e 修复侧基线"):
                    print(_line)
                return 1
        _save_baseline(agg, engine, limit)
        if not a.gate:
            print("[提示] 本次未加 --gate，但**刷新基线必须经判定** —— 已自动启用门禁判定。")
            a.gate = True
        # 刷新基线后继续走门禁判定：用刚写入的基线比漂移（恒为 0），
        # 从而同一次运行同时回答「新基线是否仍满足红线」与「基线是否已锁定」。
        # 否则刷新基线的动作会绕过红线检查，成为一条静默通道。
        print("（基线已刷新，继续按红线判定）")

    if not a.gate:
        return 0

    baseline = _load_baseline()
    fails, notes, skipped = evaluate_gate(agg, baseline)
    print()
    print("=" * 72)
    print(f"e2e 修复侧门禁 · {len(files)} 图 × {len(CASES)} 类 · 引擎 {engine}")
    if baseline:
        print(f"基线：{baseline.get('limit')} 图 / 引擎 {baseline.get('engine')}"
              f" / 长边 {baseline.get('side')}")
    for n in skipped:
        print(f"  ⚠ {n}")
    for n in notes:
        print(f"  ↑ {n}")
    if fails:
        print(f"\n✗ 门禁未通过：{len(fails)} 项")
        for f_ in fails:
            print(f"  · {f_}")
    elif skipped:
        # 阶段 171：跳过项必须进退出码（见 gate_exit_code 的说明）。
        print("\n⏭ e2e 修复侧门禁**跳过**（未完成全部判定层）：")
        for n in skipped:
            print(f"  · {n}")
    else:
        print("\n✅ e2e 修复侧门禁通过（红线 + 漂移均在容差内）")
    return gate_exit_code(fails, skipped)


if __name__ == "__main__":
    sys.exit(main())
