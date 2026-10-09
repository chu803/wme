# -*- coding: utf-8 -*-
"""
阶段 243 守卫：`config.WATERMARK_KNOWN_GAP_IMAGES`（图像级已知检测缺口）。

背景：承诺面 `ai_logo`(=真值 style `corner_logo`) 的实测样本里混着两种形态：
「小文字牌」（检测器擅长）与「大颗多面体宝石角标」（检测器几乎全漏，a1~a4）。
探针已证伪纯像素路线（整图融入背景 / 去水印原图角落特征几乎相同 ⇒ 像素域不可分），
故按用户决策把 a1~a4 登记为**图像级已知缺口**，显式排除于承诺面红线聚合。

本守卫确保这份「诚实缺口清单」不会变成「什么都往里塞」的逃生舱：
  ① 清单**非空**且每一项都在真值数据集里（否则是幽灵条目 / 拼写错误）；
  ② 清单里的图**不得**与承诺面缺真值检查冲突（缺口≠漏标，二者语义不同）；
  ③ 判定口径自洽：`run_real_benchmark` 的排除集 == `real_gate._expected_labeled_count`
     的扣除集（否则完整性层会把「有记录的排除」误判成「静默丢图」）；
  ④ **注入式反例**：把某张**健康图**（非缺口）塞进清单时，聚合口径必须真的把它排除
     ——证明这不是一个「写了不生效」的死常量。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402
import real_gate  # noqa: E402


def _truth_ids():
    import json
    with open(os.path.join(_ROOT, "benchmark_real", "ground_truth.json"), encoding="utf-8") as f:
        truth = json.load(f)
    return {e["image_id"] for e in truth.get("entries", [])}


def test_known_gap_images_is_nonempty_and_real():
    """缺口清单非空，且每一项都真实存在于真值数据集（防幽灵条目 / 拼写错误）。"""
    gap = set(config.WATERMARK_KNOWN_GAP_IMAGES)
    assert gap, ("WATERMARK_KNOWN_GAP_IMAGES 为空——若缺口已修复，请显式清空并同步"
                 "本守卫；留白会被读成「忘了」（经验 51）")
    ids = _truth_ids()
    ghost = gap - ids
    assert not ghost, f"缺口清单里的图不在真值数据集中（幽灵条目/拼写错误）：{sorted(ghost)}"


def test_known_gap_is_subset_of_committed_face_truth():
    """缺口图必须落在**承诺面**的真值 style 内——否则它本就不该进承诺面指标，
    登记为「已知缺口」就是多余的（应交给 committed_missing 机制处理）。"""
    import json
    with open(os.path.join(_ROOT, "benchmark_real", "ground_truth.json"), encoding="utf-8") as f:
        truth = json.load(f)
    by_id = {e["image_id"]: e for e in truth.get("entries", [])}
    committed_styles = set()
    for name in config.WATERMARK_TYPE_COMMITTED:
        committed_styles |= set(config.WATERMARK_TYPE_SCOPE[name]["truth_styles"])
    for iid in config.WATERMARK_KNOWN_GAP_IMAGES:
        styles = {wm.get("style") for wm in by_id.get(iid, {}).get("watermarks", [])}
        assert styles & committed_styles, (
            f"{iid} 的真值 style={styles} 不在承诺面 style 集合 {committed_styles} 内——"
            f"它本就不参与承诺面指标，无需登记为图像级缺口")


def test_expected_labeled_count_excludes_gap_consistently():
    """完整性计数必须与 run_all 的排除口径一致（否则「有记录的排除」会被当成「静默丢图」）。"""
    import json
    with open(os.path.join(_ROOT, "benchmark_real", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(_ROOT, "benchmark_real", "ground_truth.json"), encoding="utf-8") as f:
        truth = json.load(f)
    truth_ids = {e["image_id"] for e in truth.get("entries", [])}
    labeled = [im for im in manifest.get("images", []) if im.get("id") in truth_ids]
    gap = set(config.WATERMARK_KNOWN_GAP_IMAGES)
    expected = len([im for im in labeled if im["id"] not in gap])
    got = real_gate._expected_labeled_count(manifest, truth)
    assert got == expected, (
        f"_expected_labeled_count={got} 与手工口径 {expected} 不一致"
        f"（labeled={len(labeled)}, gap={sorted(gap)}）")


def test_injected_healthy_image_into_gap_actually_excluded(monkeypatch):
    """注入式反例：把一张**健康图**塞进缺口清单，聚合口径必须真的少算它一张。

    这证明该常量**不是「写了不生效」的死声明**——若实现与常量脱钩，本用例会失败。
    """
    import json
    import run_real_benchmark  # noqa: F401  确保模块可导入（config 是 run_all 内部延迟导入）

    with open(os.path.join(_ROOT, "benchmark_real", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    with open(os.path.join(_ROOT, "benchmark_real", "ground_truth.json"), encoding="utf-8") as f:
        truth = json.load(f)
    truth_ids = {e["image_id"] for e in truth.get("entries", [])}
    base_gap = set(config.WATERMARK_KNOWN_GAP_IMAGES)   # 先取原始缺口集（在patch 之前）
    healthy = next(im["id"] for im in manifest["images"]
                   if im["id"] in truth_ids
                   and im["id"] not in base_gap)

    # 只跑聚合口径（不真检测）：用空 presets 避免 3 分钟的检测开销，
    # 仅断言 n_images 与 known_gap_image_ids 的变化。
    # `run_all` 内部延迟 `import config` 并读同一模块对象 ⇒ 直接 patch config 即可生效。
    monkeypatch.setattr(config, "WATERMARK_KNOWN_GAP_IMAGES",
                        frozenset(base_gap | {healthy}))
    import run_real_benchmark as rb
    out = rb.run_all([], manifest, truth)
    assert healthy in set(out.get("known_gap_image_ids", [])), (
        "注入的健康图未出现在 known_gap_image_ids —— 说明排除机制未生效")
    assert out["n_images"] == (len(truth_ids) - len(base_gap) - 1), (
        f"注入后实评数 {out['n_images']} 未按预期少 1 张")