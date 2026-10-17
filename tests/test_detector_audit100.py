# -*- coding: utf-8 -*-
"""阶段 100 · `core/detector` 独立审计 —— 角标 MSER 降级通路（过检主体）。

背景（docs/100）
----------------
阶段 99 把过检按 kind 归因后留下一个未解之谜：`corner_logo` 贡献了掩码的
**53%**，而它的区域级平均 IoU 只有 **0.0183**（2021 个区域里仅 43 个命中）。
阶段 100 逐子通路拆开后真相很干净：

    白牌圆角板块判据（阶段 86）  ：  32 框 / 30 命中 / 平均 IoU 0.79
    MSER 降级通路               ：2569 框 / 15 命中 / **误报率 99.4%**

即：**不是白牌判据不准，是 MSER 在淹它**。消融（双基准）结果：

    30 张真实图：召回 1.0000 → 1.0000（**零变化**）
                 精确率 0.0392 → 0.1382（3.5×）
                 覆盖率 0.1266 → 0.0745（−41%）
                 mask_iou 0.0699 → 0.1163（+66%）
                 区域数 84.8/图 → 18.9/图（−78%）
    12 个合成用例：召回不变，唯 `photo/corner_logo` 精确率 0.620 → 0.914

且**判据层面不可分**：15 个真命中的实心度仅 0.25~0.58（`solidity ≥ 0.7` 会杀掉
100% 真命中、只削 93.8% 误报），边缘锐度 P90 ≥ 100 只削 6.5% 误报。MSER 找的是
「稳定极值区」，而自然照片本身就由大量稳定极值区构成。

处置：新增 `config.DETECT_CORNER_MSER_ENABLED`（**默认 False**），沿用
`DETECT_LOWRES_RESCUE`（阶段 67）确立的「按项目铁律『宁可漏检，不可滥杀』
把净增量为负的能力作为显式 opt-in」先例。

本文件锁五层：
  ① 默认值与展示面：开关默认关闭、且出现在配置快照里；
  ② 源码层：MSER 调用**必须**被该开关包裹（含「守卫自检」——去掉开关必须变红）；
  ③ 行为层：默认配置下 MSER 通路**一次都不被调用**（哨兵计数），
     打开开关则被调用（正向对照，证明哨兵不是哑巴）；
  ④ 不误伤层：关掉 MSER 后白牌通路仍然工作（合成白牌图实测命中）；
  ⑤ 门禁层：把 MSER 开回去后的真实指标会被**红线与漂移双重**拦下。
"""

from __future__ import annotations

import ast
import copy
import io
import json
import os
import sys

import cv2
import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config                          # noqa: E402
from scripts import real_gate as rg    # noqa: E402
import core.detector as det            # noqa: E402

_DETECTOR_SRC = os.path.join(_ROOT, "core", "detector.py")
_REAL_BASELINE = os.path.join(_ROOT, "benchmark_real", "quality_baseline.json")

#: 当前基线实测（`benchmark_real/quality_baseline.json` 锚点）——MSER 关闭后。
#: 阶段 100 记录为 0.1382 / 0.1163 / 0.0745 / 7.65；
#: **阶段 106 随「框内证据几何收敛」进一步改善为下列值**（0.1552 / 0.1705 / 0.0365 / 3.75，
#: recall 1.0000 与区域 IoU 0.7518 逐位不变）。
#: ⚠ **阶段 183 重锚**：上述两个阶段的值**全部标定于伪造真值期**（149 判定原 60 条
#:   corner_logo GT 系模板复制；172 收编 10 张真实豆包图并逐图实测真值 → 177 全面重算
#:   并重建基线文件）。177 漏改了本表 ⇒ `test_real_baseline_reflects_the_mser_removal`
#:   长期常红（守卫失效）。183 按当前基线重锚，并已用
#:   `real_gate.py --presets general --no-area`（杠杆 OFF 默认）复现：逐位一致。
#: ⚠ **阶段 240（Option A）再锚**：2026-10-05 导入 q 系列 11 张（数据集 10→29 图，
#:   豆包角标更小更靠角）使过检天然更低；当时 29 图实测 precision 0.0761 /
#:   mask_iou 0.2714 / mask_coverage 0.0107 / coverage_ratio 1.92，本表随之同步
#:   （与基线文件逐位一致）。
#: ⚠ **阶段 243 再锚（承诺面口径变更）**：`a1`~`a4`（大颗多面体宝石角标）已登记为
#:   `config.WATERMARK_KNOWN_GAP_IMAGES` 并**显式排除于承诺面红线聚合**（报告原样列出）。
#:   承诺面实评 29→**25 图**，实测变为 precision 0.0789 / mask_iou 0.313 /
#:   mask_coverage 0.012 / coverage_ratio 2.21（本表随之同步，与基线文件逐位一致）。
#:   ⚠ 下表 `MSER_ON` 是**注入反例**基准（开回 MSER 的历史实测），与承诺面口径无关，
#:   **保持不变**——它守的是「MSER 回退会被抓住」，不随数据集演进而重标。
#: ⚠ **阶段 300（c/s 系列导入）再锚**：2026-10-13 由 `D:\照片存储` 导入 28 张
#:   （c1–c11/c13–c15/s1–s14，2048×2048，右下「豆包 AI 生成」固定文字水印，
#:   bbox 由跨图平均高通 + 逐图足迹约束量取；c12 与 c11 字节重复已按 hash 去重）。
#:   承诺面实评 25→**53 图**，实测变为 precision **0.0801** / mask_iou **0.3217** /
#:   mask_coverage **0.0129** / coverage_ratio **2.30**（本表随之同步，与基线文件逐位一致）。
#:   mask_recall 同步 0.5111→**0.5960**（红线 0.45 不动，夹逼不变式仍满足）。
#: ⚠ **阶段 S2（2026-10-17）再锚**：`DETECT_TRANSLUCENT_SENSITIVITY` 0.55→0.50 保守收紧过检，
#:   重锚后 precision/mask_iou/mask_coverage/coverage_ratio 如上（与基线文件逐位一致）；
#:   本表为「快照==当前默认配置实测」，随基线刷新同步（经验 81 合法连带损伤）。
#: 本表语义是「快照 == 当前默认配置的实测」，故每次刷新基线都要同步（经验 81 的合法连带损伤）。
MSER_OFF = {"precision": 0.0801, "mask_iou": 0.3217,
            "mask_coverage": 0.0129, "coverage_ratio": 2.30}
#: 把开关开回去后的实测（真实图跑 `WME_DETECT_CORNER_MSER_ENABLED=1` 得到；阶段 100 口径）。
MSER_ON = {"precision": 0.0392, "mask_iou": 0.0699,
           "mask_coverage": 0.1266, "coverage_ratio": 13.00}


# ---------------------------------------------------------------------------
# 合成底图（不依赖 benchmark_real/images —— 那目录是 gitignored）
# ---------------------------------------------------------------------------
_SIDE = 512
_PLATE_WH = (32, 25)
_PLATE_XY = (18, 20)


def _fractal_noise(h: int, w: int, seed: int, octaves: int = 5) -> np.ndarray:
    """确定性分形噪声（与 detector_diag 的纹理底图同族），无第三方依赖。"""
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, scale = 1.0, 4
    for _ in range(octaves):
        small = rng.random((max(2, h // scale), max(2, w // scale))).astype(np.float32)
        acc += amp * cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        amp *= 0.55
        scale = max(2, scale // 2)
    acc -= acc.min()
    return acc / max(1e-6, acc.max())


def _textured_image(side: int = _SIDE, seed: int = 11) -> np.ndarray:
    """自然纹理型底图：MSER 会在其上大量产出「稳定极值区」。"""
    img = np.zeros((side, side, 3), np.float32)
    for c in range(3):
        img[:, :, c] = _fractal_noise(side, side, seed * 10 + c) * 210 + 20
    return np.clip(img, 0, 255).astype(np.uint8)


def _plate_image(side: int = _SIDE, seed: int = 7) -> np.ndarray:
    """白底圆角牌 + 深色描边 + 深色字形（与 test_corner_plate 同构）。"""
    rng = np.random.default_rng(seed)
    img = np.full((side, side, 3), (48, 52, 60), np.uint8)
    grain = rng.integers(-18, 19, size=(side, side, 1), dtype=np.int16)
    img = np.clip(img.astype(np.int16) + grain, 0, 255).astype(np.uint8)
    x, y = _PLATE_XY
    w, h = _PLATE_WH
    cv2.rectangle(img, (x, y), (x + w, y + h), (58, 58, 62), 1)
    cv2.rectangle(img, (x + 1, y + 1), (x + w - 1, y + h - 1), (242, 240, 236), -1)
    cv2.putText(img, "AI", (x + 4, y + h - 7), cv2.FONT_HERSHEY_SIMPLEX,
                0.52, (28, 28, 32), 2, cv2.LINE_AA)
    return img, (x, y, w, h)


def _corner_candidates(img: np.ndarray):
    side = img.shape[0]
    return det.detect_corner_logo(img, config.DETECT_CORNER_RATIO,
                                  side * side, use_ocr=False)


# ===========================================================================
# ① 默认值与展示面
# ===========================================================================

def test_mser_switch_defaults_to_off():
    """开关必须默认关闭——打开它就是 30 图 +1977 个误报框、召回零收益。"""
    assert config.DETECT_CORNER_MSER_ENABLED is False, (
        "DETECT_CORNER_MSER_ENABLED 被改回 True——角标 MSER 洪水已回归。"
        "改写理由必须同时更新 docs/100、config 注释、real_gate 红线与基线快照。"
    )


def test_mser_switch_is_visible_in_config_snapshot():
    """配置快照必须暴露该开关（否则设置页/诊断看不到它，属「隐形配置」）。"""
    snap = config.get_config_snapshot()
    assert "角落 MSER 降级通路" in snap, "配置快照缺少角落 MSER 开关"
    assert snap["角落 MSER 降级通路"] == "False"


# ===========================================================================
# ② 源码层：MSER 调用必须在开关之下（含守卫自检）
# ===========================================================================

def _mser_calls_all_gated(src: str) -> bool:
    """`detect_corner_logo` 里每个 `_mser_boxes_in_patch` 调用是否都在开关下。

    判据：该调用的 AST 节点必须落在某个 `if <含 DETECT_CORNER_MSER_ENABLED 的表达式>`
    的 body/orelse 子树里。刻意用 AST 而非文本匹配——文本匹配会被注释淹没。
    """
    tree = ast.parse(src)
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "detect_corner_logo"),
              None)
    if fn is None:
        raise AssertionError("core/detector.py 里找不到 detect_corner_logo")

    guarded_ids: set = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and "DETECT_CORNER_MSER_ENABLED" in ast.unparse(node.test):
            for sub in list(node.body) + list(node.orelse):
                for inner in ast.walk(sub):
                    guarded_ids.add(id(inner))

    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "_mser_boxes_in_patch"]
    if not calls:
        raise AssertionError("detect_corner_logo 里找不到 _mser_boxes_in_patch 调用")
    return all(id(c) in guarded_ids for c in calls)


def test_mser_call_is_behind_the_switch_in_source():
    """MSER 调用必须被 `if config.DETECT_CORNER_MSER_ENABLED:` 包裹。

    这条守的是「行为层的默认值」之外的第二种回退方式：把开关删掉、让 MSER
    无条件运行（此时 config 仍是 False，但代码已不看它）。
    """
    src = io.open(_DETECTOR_SRC, encoding="utf-8").read()
    assert _mser_calls_all_gated(src), (
        "detect_corner_logo 里的 MSER 调用没有被 config.DETECT_CORNER_MSER_ENABLED 包裹"
    )


def test_gate_checker_is_not_a_dummy():
    """**守卫自检**：把开关换成恒真的 `if True:`，判据必须变红。

    没有这一步，「AST 判据写错（比如找错函数 / 找错常量名）」会让上面那条断言
    永久为绿——而它守卫的正是本阶段最核心的改动。
    """
    src = io.open(_DETECTOR_SRC, encoding="utf-8").read()
    broken = src.replace("if config.DETECT_CORNER_MSER_ENABLED:",
                         "if True:  # 注入：去掉开关")
    assert broken != src, "注入锚点未命中——源码结构已变化，请更新本测试"
    assert not _mser_calls_all_gated(broken), (
        "去掉开关后判据仍为绿——守卫是哑巴"
    )


# ===========================================================================
# ③ 行为层：MSER 通路一次都不被调用（哨兵计数）+ 正向对照
# ===========================================================================

def test_mser_path_is_never_called_by_default(monkeypatch):
    """默认配置下，检测流程**不得**调用 MSER 降级通路（哨兵计数）。"""
    calls = []
    monkeypatch.setattr(det, "_mser_boxes_in_patch",
                        lambda *a, **k: calls.append(1) or [])
    _corner_candidates(_textured_image())
    assert calls == [], (
        f"默认配置下调用了 MSER 降级通路 {len(calls)} 次——角标洪水已回归"
    )


def test_mser_path_is_called_when_switch_is_on(monkeypatch):
    """**正向对照**：打开开关后哨兵必须被触发（证明上一条不是哑巴断言）。"""
    calls = []
    monkeypatch.setattr(det, "_mser_boxes_in_patch",
                        lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(config, "DETECT_CORNER_MSER_ENABLED", True)
    _corner_candidates(_textured_image())
    assert calls, "打开开关后 MSER 通路仍未被调用——开关是装饰品"


def test_mser_flood_on_natural_texture_is_gated(monkeypatch):
    """同一张自然纹理图上：关 MSER 候选极少，开 MSER 候选暴涨。

    这是「过检主体来自 MSER」的自足证据（不依赖 benchmark_real/images）。
    """
    img = _textured_image()

    monkeypatch.setattr(config, "DETECT_CORNER_MSER_ENABLED", False)
    off = len(_corner_candidates(img))

    monkeypatch.setattr(config, "DETECT_CORNER_MSER_ENABLED", True)
    on = len(_corner_candidates(img))

    assert on > off, f"开 MSER 后候选数未增加（off={off}, on={on}）——构造图不具纹理性"
    assert off <= 2, f"关 MSER 后仍有 {off} 个角标候选，白牌通路在纹理图上过检"
    assert on >= 10, f"开 MSER 后只有 {on} 个候选，构造图的纹理不足以复现洪水"


# ===========================================================================
# ④ 不误伤层：关掉 MSER 后白牌通路仍然工作
# ===========================================================================

def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix, iy = max(ax, bx), max(ay, by)
    iw = max(0, min(ax + aw, bx + bw) - ix)
    ih = max(0, min(ay + ah, by + bh) - iy)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def test_plate_path_still_hits_with_mser_off():
    """关掉 MSER 后，白牌圆角板块仍必须被检出（这是修复不能牺牲的那部分召回）。

    真实基准上白牌通路 30/30 命中、平均 IoU 0.79，是角标召回的唯一支柱；
    本测试用合成白牌图在默认配置（MSER 关）下把它钉住。
    """
    assert config.DETECT_CORNER_MSER_ENABLED is False
    img, truth = _plate_image()
    cands = _corner_candidates(img)
    best = max((_iou((r.x, r.y, r.w, r.h), truth) for r in cands), default=0.0)
    assert best >= 0.3, (
        f"关掉 MSER 后白牌通路也检不出了：最佳 IoU={best:.3f}，"
        f"候选={[(r.x, r.y, r.w, r.h) for r in cands]}"
    )


# ===========================================================================
# ⑤ 门禁层：把 MSER 开回去会被红线与漂移双重拦下
# ===========================================================================

def _real_baseline() -> dict:
    if not os.path.exists(_REAL_BASELINE):
        pytest.skip("真实图基线快照不存在（未跑过 real_gate.py --update）")
    with io.open(_REAL_BASELINE, encoding="utf-8") as f:
        return json.load(f)


def test_real_baseline_reflects_the_mser_removal():
    """基线必须已刷新到 MSER 关闭后的值——否则漂移是在跟旧世界比。"""
    gen = _real_baseline()["aggregate"]["per_preset"]["general"]
    for key, expected in MSER_OFF.items():
        assert gen[key] == pytest.approx(expected, abs=1e-4), (
            f"基线 {key}={gen[key]} 与 MSER 关闭后实测 {expected} 不一致——"
            f"未跑 `python scripts/real_gate.py --update`"
        )


def test_injection_reenabling_mser_is_caught_by_red_line_and_drift():
    """**核心注入反例**（真实数值）：把 MSER 开回去，必须被**红线与漂移双重**拦下。

    注入值取自真实运行：`WME_DETECT_CORNER_MSER_ENABLED=1 python scripts/real_gate.py`
    → precision 0.1382→0.0392、mask_iou 0.1163→0.0699、coverage 0.0745→0.1266、
    ratio 7.65→13.00，实测输出 4 条红线 + 4 条漂移。

    本测试把那次真实运行**固化**成纯数据断言，使它在 CI 上零成本复现。
    """
    base = _real_baseline()
    now = copy.deepcopy(base)
    gen = now["aggregate"]["per_preset"]["general"]
    gen.update(MSER_ON)

    ok, problems = rg.evaluate_real(now, base)
    assert not ok, "把 MSER 开回去后门禁仍判通过——本阶段的修复没有护栏"

    for key in MSER_ON:
        hits = [p for p in problems if key in p]
        assert any("红线" in p for p in hits), (
            f"{key} 回退未被红线拦下（problems={problems}）"
        )
        assert any("漂移" in p for p in hits), (
            f"{key} 回退未被漂移拦下（problems={problems}）"
        )
    # 过检两项是「越高越坏」，措辞必须是「上升」而非「下滑」（经验 76 同族）。
    assert any("mask_coverage" in p and "上升" in p for p in problems)
    assert any("coverage_ratio" in p and "上升" in p for p in problems)
