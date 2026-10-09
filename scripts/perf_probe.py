#!/usr/bin/env python3
# ==============================================================================
# 性能探针 · perf_probe（阶段 186 · 对应优化清单 A5）
#
# 为什么需要它
# ------------
# 全仓 `p95` / `latency` / `time_budget` 命中 **0 处**（见 docs/185 侦察）：四个门禁
# 都只 `time.time()` **打印**耗时，没有任何时间红线 ⇒ 战略目标「P95 < 1.5s」
# **无法判定是否达标**，性能回退（换依赖 / 加通路 / 大图退化）无人发现。
#
# 设计要点（沿用本项目门禁家族契约）
# ----------------------------------
# ① **先测量、后标定**：没有基线时本探针**判「跳过」(退出码 2)**，绝不假装通过。
#    用 `--update` 跑一次即可生成基线 `benchmark_real/perf_baseline.json`。
# ② **回归护栏 ≠ 绝对目标**（诚实分离，避免「用 CPU 数据去标 GPU 目标」）：
#      · 护栏（默认生效）：本次 P95 ≤ 基线 P95 × (1 + tol)，`tol` 默认 30%
#        —— 跨机器可移植，守的是「**不许变慢**」；
#      · 目标（默认只**报告差距**，不判红）：`TARGET_P95_SECONDS`（默认 1.5s，
#        来自路线图，前提是生产环境；CPU 上必然超）——需要时用 `--enforce-target`
#        把它升级为硬红线（适合 GPU/CI 环境）。
# ③ **工作量必须来自真实基准图**（阶段 186 实测教训）：
#    最初版本用「固定 seed 合成图」当工作量，实测 P95 只有 **0.113s**；而
#    `real_gate` 跑真实照片（`benchmark_real/images/` 的 1024² 生活照）约 **0.97s/张**
#    —— **差约 9 倍**。原因是合成底太平滑（纹理/边缘极少），检测器几乎不干活。
#    用它会**系统性低估真实成本、掩盖真实回退**（正是「量错对象」的陷阱）。
#    故默认工作量 = 真实基准图（`--source benchmark`）；合成图仅在无图可用时兜底，
#    且基线文件会记录 `source`，**不同 source 的基线互不可比**（判跳过）。
# ④ **先烧热再计时**（LESSONS 192）：首次推理有冷启动伪影，故先跑 1 次废图推理，
#    再开始计时。
#
# 退出码（门禁家族契约 0/1/2）
# ----------------------------
#   0 = 通过（未超回归护栏；若开了 --enforce-target 也未超目标）
#   1 = 未通过（超护栏，或 --enforce-target 下超目标）
#   2 = 跳过（无基线 / 检测器不可用）
#
# 用法
# ----
#   python scripts/perf_probe.py                 # 用现有基线判定（无基线 ⇒ 退出码 2）
#   python scripts/perf_probe.py --update        # 测量并写入基线
#   python scripts/perf_probe.py --runs 20 --size 2048
#   python scripts/perf_probe.py --enforce-target --target 1.5
#   python scripts/perf_probe.py --stage full --update        # 全链路基线（检测+修复，吃到 GPU）
#   python scripts/perf_probe.py --stage full --enforce-target --target 1.5
#
# ⚠ GPU 诚实边界：默认 --stage detect 只测「检测阶段」，该阶段是 CPU 绑定的 OpenCV，
#   即便装了 CUDA torch 也**不会**加速；D1 的 GPU 收益在「修复(inpaint/LaMa)」阶段。
#   因此路线图「P95<1.5s」这个**全链路**目标，必须用 --stage full 才能验证——
#   它会经 api.remove_watermark_bytes 走到 GPU/LaMa，真实反映生产环境时延。
#   ⚠ 硬件一致性：每次测量都会把计算设备指纹（device=cpu/cuda + gpu_name）写入基线，
#   基线可比性会据此拒绝「沙箱 CPU 基线」与「RTX 5060 全链路基线」被拿来互相比对
#   （跨硬件时延差一个数量级，跨比即造假）——不同硬件 ⇒ 判跳过，绝不假装通过（D1-Stage 6）。
# ==============================================================================
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402

from scripts import baseline_guard  # noqa: E402
from utils.stats import nearest_rank_percentile  # noqa: E402

BASELINE_PATH = os.path.join(_ROOT, "benchmark_real", "perf_baseline.json")

#: 路线图目标（生产环境）；默认只报告差距，不判红。改用 `--enforce-target` 才生效。
TARGET_P95_SECONDS = 1.5

#: 默认回归容差：P95 变慢超过 30% 即判红。
DEFAULT_TOL = 0.30

#: 默认工作量来源：真实基准图（`benchmark_real/images/`）；无图时兜底 synthetic。
DEFAULT_SOURCE = "benchmark"

#: 默认逐图重复次数（取 min 作能力度量；见 `measure()` 的说明）。
DEFAULT_REPEAT = 3

#: 刷新基线时的**向下刷新护栏**规则：指标 → (方向, 相对容差, 绝对容差)。
#: perf 的「更坏」是**更高**（P95 越大越差）⇒ 方向 "max"。
#: 阶段 191：本脚本此前**没有**这道护栏——`--update` 直接 `return 0` 写盘，
#: 意味着「性能变慢也能被静默刷新进基线」；由 146 阶段的门禁登记守卫当场抓出
#: （它会对每个含 `--update` 的脚本断言护栏存在）。
P95_METRIC = "p95"

#: 判定「本次测量是否被**瞬态**扰动」的**内生**噪声阈值（相对量，无量纲）。
#: 阶段 212 实测标定（24 核机器，真实图，min-of-3）：
#:   · 正常轮：spread/P95≈ 0.016~ 0.083
#:   · **瞬态**受扰轮（刚跑完多进程负载探针、系统未回落）：≈ 0.346（高 21 倍）
#:   ⇒ 阈值取 **0.20**。
#:
#: ⚠⚠ **重要边界：这个判据只抓「瞬态」，抓不到「稳态高负载」**（阶段 212 反向验证
#:   实测推翻了自己的初始假设，务必读这段）：
#:   24 进程真并行压满全部24 核时，噪声比仍只有 **0.062**（闸门放行）——
#:   因为 `min-of-k` 对**同向**负载天然免疫：负载让每次都变慢 ⇒ 三个值彼此接近
#:   ⇒ 极差反而小。而**稳态高负载的真实表现是「整体抬高但一致」**：
#:   实测 P95 干净 0.661s → 负载 0.754s，**仅抬升 1.141×（+14.1%）**，
#:   远小于 30% 回归护栏容差 ⇒ **稳态负载本来就不会让护栏误判红**。
#:   ⇒ 因此本闸门**只**用于拦截「瞬态污染」这一类真问题（Stage 211 实测的
#:   2.3× 假红即属此类），而**不是**一台「负载检测器」。
#:
#: 为什么用「run 内极差 / P95」这个**相对量**而不用绝对秒数：绝对极差随图集与机器
#: 差异变化，不可跨机器移植；比值无量纲，可移植。
#:
#: 为什么**不用**系统级 CPU 占用当噪声信号（阶段 212 逐一实测排除，见 LESSONS 232）：
#:   · `time.process_time()/perf_counter` —— OpenCV 多线程下比值 1.3~2.3（>1），
#:     累加的是**所有线程**的 CPU 时间，衡量「并行度」而非「被抢占」，方向反了；
#:   · `os.getloadavg()` —— Windows 不存在；
#:   · `psutil` —— 未装，且不愿为一个探针引入新运行时依赖；
#:   · `ctypes` + `kernel32.GetSystemTimes` —— 24 进程真并行压满 24 核仅报 1.6%
#:     ⇒ **本环境该API 不可信**；且 Windows 的 kernel 时间**已含 idle**，
#:     照抄 Linux `/proc/stat` 公式会把占用率算成 `real_busy + idle`（恒偏大）。
#: 结论：外部负载度量在本环境**要么需新依赖、要么不可信** ⇒ 改用**完全内生**的
#: 相对噪声（零新增采集成本，`measure()` 已在返回 `spread_mean`）。
NOISE_RATIO_KEY = "noise_ratio"
NOISE_MAX = 0.20

#: 判定「本轮内是否发生**渐进劣化**」的锚点比阈值（尾/首，同一张锚点图）。
#:
#: 阶段 214 实测标定（RTX 5060 Laptop · full 口径 · 5 张图 × min-of-2）：
#:   · 「干净」轮（无外部负载）：锚点尾/首 = **1.7399**（同一张图 16.4s → 28.6s）
#:     ⇒**本机在测full 口径时本身就会渐进劣化**（笔记本 GPU 持续负载下降频）；
#:   · 「12 进程外部负载」轮：锚点尾/首 = **0.9731**，绝对值 27~31s（**慢 2 倍**）
#:     ⇒ 稳态负载让**所有**图（含锚点）都慢，但**锚点比≈1**。
#: 两者方向相反 ⇒ **锚点比能专抓「渐进劣化」，且不误报「稳态负载」**。
#: 阈值取 **1.30**（1.74 与 0.97 的中点偏严侧，两侧均有充分余量）。
ANCHOR_KEY = "anchor_ratio"
ANCHOR_MAX = 1.30

#: 基线文件结构版本（结构变更时递增，旧基线会被判「需刷新」）。
#:
#: 阶段 211 修正（D1-Stage 6 遗留缺陷）：此前把 `device`/`gpu_name` 加入
#: COMPARABILITY_KEYS 时**忘了递增本版本**，于是 2026-10-01 建的基线（v1、无硬件
#: 字段）依然能通过版本检查，落到键值比较才被判「device 不一致（基线 None vs 本次
#: cuda）」⇒ **每次跑都退出码 2（跳过）**。跳过的退出码看起来「良性」，实则护栏
#: **零约束**——这正是 LESSONS 183「有守卫≠有覆盖」的最恶劣形态：守卫在，且每次都
#: 「有输出」，但从不判定。
#: ⇒铁律：**改动 COMPARABILITY_KEYS（可比性维度）必须递增本版本**，
#:   否则历史基线会静默退化为永久跳过。tests/test_perf_probe.py 有守卫钉死这一点。
#:
#: 阶段 215：2 → 3（Stage 211 已把1→2）。本次补入 `engine` 与 `runs` 两个维度（此前漏检，会让
#: 「opencv 的测量」与「lama 的基线」直接比护栏，产生假红）。
#: ⚠ 两个口径的基线文件**都**需重新标定（版本号是全局的，不分stage）。
BASELINE_VERSION = 3

#: 判定基线与本次测量是否可比时，**必须逐项相等**的字段。
#:
#: · source/stage/repeat —— 工作量口径（合成图 vs 真实图、仅检测 vs 全链路、min-of-k）。
#: · device/gpu_name    —— 计算硬件（阶段 211 提取为常量，便于守卫检查其与基线的一致性）。
#: · engine             —— 修复引擎（阶段 215 补入）。**full 口径下不同引擎耗时差一个
#:   数量级**（实测 lama 13s vs opencv 约 1~2s），而 `stage=full` 的 meta 本就带
#:   `engine` 字段——此前它**不在**比对维度里，于是「opencv 的测量」会与
#:   「lama 的基线」直接比护栏，产生**假红**（把换引擎误报成性能回归）。
#: · runs               —— 实际计时张数（阶段 215 补入）。P95 是**分位数**，样本量
#:   不同则分布形状不同（3 张的 P95 必然是最大值，10 张才是真分位）⇒ 不可直接比。
#:
#: ⚠ 增删这里的键**必须**同步递增 BASELINE_VERSION，并重新 `--update` 标定，
#:   否则旧基线会缺该字段 ⇒ 永久判「跳过」⇒ 护栏失效（LESSONS 230 的原样重演）。
#: 阶段 263：full 阶段标定口径必须含「真实超分」状态（阶段 262：开启令 full 耗时 +2.6s）。
#: 但这三项**不进全局 COMPARABILITY_KEYS**——否则会让现有 v3 detect 基线（无此键）
#: 被判「结构过旧」而失效。改用 **full 阶段专属**可比性检查（见 `baseline_comparable`），
#: 既守住「超分开/关不可比」，又不连累 detect 基线（LESSONS 230 的本意是「不让旧基线
#: 被误判为可比或误判为失效」，stage-scoped 恰好满足，且无需递增版本号重标定 detect）。
REAL_SUPERRES_KEYS = ("real_superres_enabled", "real_superres_scale", "real_superres_backend")

COMPARABILITY_KEYS = ("source", "stage", "repeat", "device", "gpu_name",
                      "engine", "runs")


def build_workload(size: int = 1024, seed: int = 20261001) -> np.ndarray:
    """构造**确定性**合成工作负载（**兜底用**；默认工作量是真实基准图，见模块头 ③）。

    为什么仍然保留它：① 无基准图的干净 checkout（如刚 clone）仍能跑一次冒烟；
    ② 单测需要一个不依赖外部素材、逐位可复现的输入。
    ⚠ 它**低估真实成本**（合成底太平滑），故基线里会记 `source=synthetic`，
    与 `source=benchmark` 的基线**互不可比**。
    """
    rng = np.random.default_rng(seed)
    acc = np.zeros((size, size), np.float32)
    amp, scale = 1.0, 4
    for _ in range(5):
        small = rng.random((max(2, size // scale), max(2, size // scale))).astype(np.float32)
        acc += amp * np.repeat(np.repeat(small, scale, axis=0), scale, axis=1)[:size, :size]
        amp *= 0.55
        scale = max(2, scale // 2)
    acc -= acc.min()
    acc /= max(1e-6, acc.max())
    base = np.stack([acc * 200 + 30, acc * 190 + 35, acc * 170 + 45], axis=-1)
    img = base.astype(np.uint8)

    # 右下角亮牌（340×128 @2048² 同比例）+ 暗色墨迹
    fw = max(8, int(round(340 * size / 2048.0)))
    fh = max(8, int(round(128 * size / 2048.0)))
    bx, by = size - fw, size - fh
    img[by:by + fh, bx:bx + fw] = 236
    ink_h = max(2, fh // 5)
    img[by + fh // 2:by + fh // 2 + ink_h, bx + fw // 12:bx + fw // 2] = 40

    # 底边小字（浅色条纹块）
    ty = max(0, size - 18)
    for x in range(0, size, 9):
        img[ty:ty + 8, x:x + 5] = 220

    # 全局淡格点
    step = max(16, size // 12)
    img[::step, ::step] = np.clip(img[::step, ::step].astype(np.int16) + 26, 0, 255)
    return img


def iter_workload(*, source: str = DEFAULT_SOURCE, limit: int = 10, size: int = 1024,
                  seed: int = 20261001, images_dir: "str | None" = None):
    """产出 `(显示名, RGB 数组)` 列表 —— 性能门禁的**工作量**。

    参数说明：
        source     : `"benchmark"`（默认，真实基准图）或 `"synthetic"`（兜底合成图）。
        limit      : 采样张数上限（默认 10）。
        size/seed  : 仅 `synthetic` 用。
        images_dir : 基准图目录，默认 `benchmark_real/images`。

    返回值：
        `(items, source_used)`；`source="benchmark"` 但**无图可用**时会自动降级到
        `synthetic` 并在 `source_used` 里如实回报（调用方据此写入基线、拒绝跨源比较）。
    """
    from PIL import Image
    d = images_dir or os.path.join(_ROOT, "benchmark_real", "images")
    if source == "benchmark" and os.path.isdir(d):
        names = sorted(f for f in os.listdir(d)
                       if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")))
        # 优先真实豆包角标图（有角标 ⇒ 角标通路真的跑起来）
        names.sort(key=lambda f: (not f.startswith("ai_logo"), f))
        picked = names[:max(1, limit)]
        items = []
        for f in picked:
            try:
                items.append((f, np.asarray(Image.open(os.path.join(d, f)).convert("RGB"))))
            except Exception:
                continue
        if items:
            return items, "benchmark"
    return [(f"synthetic:{size}:{seed}", build_workload(size=size, seed=seed))], "synthetic"


def summarize(runs: list) -> dict:
    """把逐次耗时汇总为分位数（最近秩口径，见 utils.stats）。"""
    if not runs:
        return {}
    return {
        "n": len(runs),
        "min": round(min(runs), 4),
        "p50": round(nearest_rank_percentile(runs, 0.50), 4),
        "p95": round(nearest_rank_percentile(runs, 0.95), 4),
        "max": round(max(runs), 4),
        "mean": round(sum(runs) / len(runs), 4),
    }


def device_fingerprint() -> dict:
    """记录本次测量的**计算设备指纹**，用于阻止跨硬件基线被错误比对（D1-Stage 6）。

    为什么必须校验硬件（LESSONS 209「基础设施一致性守卫」）：
    · `--stage detect` 是 CPU 绑定的 OpenCV，**永远走 cpu**，即便装了 CUDA torch 也不加速；
    · `--stage full` 在 `WME_DEVICE_MODE=auto` 下可吃到 CUDA/LaMa 收益，
      同一张图在 RTX 5060 上的 P95 与沙箱 CPU 上**差一个数量级**。
    ⇒ 若基线可比性只校验 `source/stage/repeat`，用户用沙箱 CPU 基线去判 RTX 5060 的
    绝对目标（--enforce-target 1.5），会要么「CPU 基线被误当 GPU 标杆」、要么把
    GPU 真实成绩与 CPU 数字比出虚假回退。必须把 `device`（cpu/cuda）与
    `gpu_name` 纳入可比性裁决——不同硬件 ⇒ 不可比 ⇒ 判跳过，绝不假装通过。

    采集策略：懒加载 torch（torch 很重，只在有明确需要时 import）；CUDA 不可用时
    `device="cpu"` 且 `gpu_name=None`，与「无 torch 环境」保持同一指纹，保证可读。
    """
    fp = {"device": "cpu", "gpu_name": None}
    try:
        import torch  # 懒加载：仅在采集指纹时 import，避免拖慢纯检测路径
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            fp["device"] = "cuda"
            try:
                fp["gpu_name"] = torch.cuda.get_device_name(0)
            except Exception:  # noqa: BLE001 - 名字拿不到也至少记 cuda
                fp["gpu_name"] = "unknown"
    except Exception:  # noqa: BLE001 - 无 torch（如沙箱）⇒ 明确记为 cpu
        fp["device"] = "cpu"
    return fp


def baseline_comparable(baseline, meta) -> "tuple[bool, str]":
    """基线与本次测量是否**可比**（同工作量 / 同结构版本 / **同硬件**）。

    不可比的组合必须判**跳过**，绝不允许「拿 2048 的基线判 1024」、
    「拿合成图基线判真实照片」或「拿沙箱 CPU 基线判 RTX 5060 全链路」——
    那会让护栏看起来通过而实际毫无约束力。
    """
    if not baseline or baseline.get("version") != BASELINE_VERSION:
        return False, "无基线（或结构过旧）"
    # 阶段 211：把「基线**缺**该字段（结构过旧，需重新标定）」与
    #「字段**有**但值不同（真不可比，如跨硬件）」分开诊断。
    # 原因：两者都返回「跳过」，但处置完全相反——前者只需 `--update` 重新标定，
    # 若是跨硬件则**永远不该**拿旧基线比。把两者混为一谈，会让「护栏静默失效」
    # 被误读成「本机硬件不同，不方便比对」而长期无人发现（正是本缺陷的成因）。
    missing = [k for k in COMPARABILITY_KEYS if k not in baseline]
    if missing:
        return False, (
            f"基线结构过旧（缺字段 {', '.join(missing)}）"
            f"⇒ 需重新标定：python scripts/perf_probe.py --update"
        )
    for k in COMPARABILITY_KEYS:
        if baseline.get(k) != meta.get(k):
            return False, f"{k} 不一致（基线 {baseline.get(k)} vs 本次 {meta.get(k)}）"
    # 阶段 263：full 阶段专属口径维度（真实超分）。不进全局 COMPARABILITY_KEYS，
    # 以免让 v3 detect 基线（无此键）被判「结构过旧」失效；仅在双方都是 full 时单独比对：
    # 超分开启(+2.6s, 阶段 262) 与关闭的 full 测量必须判不可比，绝不假装同口径对比。
    if baseline.get("stage") == "full" and meta.get("stage") == "full":
        for k in REAL_SUPERRES_KEYS:
            if baseline.get(k) != meta.get(k):
                return False, f"{k} 不一致（基线 {baseline.get(k)} vs 本次 {meta.get(k)}）"
    return True, ""


def _refresh_rules() -> dict:
    """刷新基线时所守的**向下刷新护栏**规则（指标 → (方向, 相对容差, 绝对容差)）。

    性能指标的「更坏」是**更高** ⇒ 方向 `"max"`；容差与门禁的回归护栏同口径
    （`DEFAULT_TOL` = 30%）。含义：**本次测得的 P95 若比旧基线差出 30% 以上，
    拒绝把它刷进基线**（否则「变慢」会被一次 `--update` 静默合法化）。
    逃生门是显式的 `--force-update`。
    """
    return {P95_METRIC: ("max", DEFAULT_TOL, 0.0)}


def noise_ratio(stats: dict, meta: dict) -> "float | None":
    """本次测量的**内生相对噪声** = run 内极差均值 / P95。

    返回 `None` 表示拿不到（缺 `spread_mean` 或 P95 为 0）⇒ 调用方须按「无法判定」
    处理，**不得**当作 0（否则会静默地把「没测到噪声」当成「没有噪声」）。

    为什么用这个量（阶段 212）：`min-of-k` 已经把 **run 内**尖峰压掉大半，剩下的
    主要噪声源是**跨 run 的环境漂移**（别的进程占 CPU）。而漂移会同时抬高
    「每次重复的离散度」——实测受干扰轮该比值 0.346，正常轮 0.016~0.083，
    相差 21 倍，足以当判据。
    """
    spread = (meta or {}).get("spread_mean")
    p95 = (stats or {}).get("p95")
    if spread is None or not p95:
        return None
    try:
        return float(spread) / float(p95)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def gate_decision(baseline, measured, meta, *, tol: float = DEFAULT_TOL,
                  enforce_target: bool = False, target: float = TARGET_P95_SECONDS,
                  noise_max: float = NOISE_MAX, anchor_max: float = ANCHOR_MAX,
                  skip_noise_gate: bool = False
                  ) -> "tuple[int, list]":
    """纯函数：给基线/实测决定退出码与理由列表（可比性由 baseline_comparable 统一裁决）。

    判定顺序（**顺序本身就是安全性**）：
      1. **可比性**：基线缺失/ 结构过旧 / 工作量不可比 ⇒ 2（跳过，绝不假装通过）；
      2a. **瞬态污染**（阶段 212，`spread_mean / P95`）：超阈值 ⇒ 2；
      2b. **渐进劣化**（阶段 214，**锚点图尾/首比**）：超阈值或缺数据 ⇒ 2。
          2a 与 2b **正交**——2a 抓「单张图重复之间」的瞬态离散，
          2b 抓「同一张图随时间退化」。Stage 213 实测：渐进劣化组的
          `spread/P95` 仅 0.063（判可信）却把 P95 从 13.6s 推到 27.7s，
          **对2a 完全隐形**。
      3. **回归护栏 / 绝对目标**：P95 超护栏或（启用时）超目标 ⇒ 1。

    ⚠ 第2 层**只降级为「跳过」，绝不升级为「通过」**。这是刻意的：
    噪声高时我们**不知道**真实性能是多少（可能很快也可能很慢），
    任何断言都是编造。判「跳过 + 要求重测」是唯一诚实的处置。

    参数 `skip_noise_gate`（**仅供单元测试**，生产路径 `main()` 绝不传 True）
    ------------------------------------------------------------------------
    纯函数单测只关心「护栏/目标怎么判」，但它们构造的 meta 不含 `spread_mean`
    ⇒ 会被第 2 层挡住而测不到第 3 层。本参数让测试能**显式声明**「本例噪声层由
    专项测试覆盖，此处只测护栏」，**而不是**把严格性从生产路径上拿掉。
    `tests/test_perf_gate_staleness.py` 有守卫断言 `main()` 走的是严格路径。
    """
    ok, why = baseline_comparable(baseline, meta or {})
    if not ok:
        return 2, [f"{why} → 跳过判定（用 --update 先标定 / 或对齐工作量后重测）"]

    # ---- 第 2 层：测量可信度 ----
    # ⚠ `spread_mean` 缺失 ⇒ **无法证明测量干净** ⇒ 判跳过，而不是「按 0 噪声放行」。
    #这是刻意的：min-of-k 的离散度是唯一能自证「本次测量没被环境污染」的信号；
    # 拿不到它就等于没有证据，而门禁在无证据时**必须保守**（否则「忘记采集噪声」
    # 会变成一条绕过护栏的后门）。`repeat<=1` 时min-of-k 天然无意义，也归入此类。
    if not skip_noise_gate:
        nr = noise_ratio(measured, meta or {})
        if nr is None:
            return 2, [
                "无法计算测量噪声（缺少 run内极差数据，通常是 repeat<=1）"
                f"→ 本次 P95 {float(measured.get('p95') or 0.0):.3f}s 不可信，"
                f"**不判通过也不判回退**。请用 --repeat >= 3 在机器空闲时重测。"
            ]
        if float(noise_max) > 0 and nr > float(noise_max):
            return 2, [
                f"测量被环境污染（run内相对噪声 {nr:.3f} > {float(noise_max):.2f}）"
                f"→ 本次 P95 {float(measured.get('p95') or 0.0):.3f}s 不可信，"
                f"**不判通过也不判回退**。请在机器空闲时重测（关闭其他占用 CPU 的任务）。"
            ]
        # ---- 第 2b 层：渐进劣化（锚点法，阶段 214）----
        # 与上面的 `spread/P95` **正交**：那个抓「单张图重复之间」的瞬态离散，
        # 这个抓「同一张图随时间退化」。Stage 213 实测：渐进劣化组 spread/P95
        # 仅 0.063（判可信）却把 P95 从 13.6s 推到 27.7s——完全隐形。
        ar = (meta or {}).get(ANCHOR_KEY)
        if ar is None or float(ar) <= 0:
            # 拿不到 / 异常值（首轮耗时为 0 等）⇒ **无证据** ⇒ 保守判跳过。
            # 刻意与 `repeat<2` 相反（那是「有证据但无区分力」→ 只提示）：
            # 锚点是**新采集**的信号，拿不到说明采集没跑到。
            return 2, [
                "缺少锚点测量数据（anchor_ratio缺失或异常）"
                f"→ 无法排除「本轮渐进劣化」，本次 P95 "
                f"{float(measured.get('p95') or 0.0):.3f}s 不可信，"
                f"**不判通过也不判回退**。请重测（若反复出现，说明锚点采集逻辑失效）。"
            ]
        if float(anchor_max) > 0 and ar > float(anchor_max):
            return 2, [
                f"本轮发生**渐进劣化**（锚点图尾/首 = {ar:.3f} > {float(anchor_max):.2f}，"
                f"同一张图在轮内变慢 {(float(ar) - 1) * 100:.0f}%）"
                f"→ 本次 P95 {float(measured.get('p95') or 0.0):.3f}s 不可信，"
                f"**不判通过也不判回退**。这是 GPU/热节流类渐变（如笔记本持续负载降频），"
                f"请让机器冷却后重测。"
            ]

    reasons = []
    base_p95 = float(baseline.get("p95") or 0.0)
    me_p95 = float(measured.get("p95") or 0.0)
    rail = base_p95 * (1.0 + float(tol))
    over_rail = me_p95 > rail
    reasons.append(
        f"回归护栏：P95 {me_p95:.3f}s vs 基线 {base_p95:.3f}s ×(1+{tol:.0%}) = {rail:.3f}s "
        f"→ {'❌ 超护栏' if over_rail else '✅ 在护栏内'}"
    )
    over_target = False
    if enforce_target:
        over_target = me_p95 > float(target)
        reasons.append(
            f"绝对目标（--enforce-target）：P95 {me_p95:.3f}s vs {target:.2f}s "
            f"→ {'❌ 超目标' if over_target else '✅ 达目标'}"
        )
    else:
        reasons.append(
            f"绝对目标（仅报告）：P95 {me_p95:.3f}s vs 路线图 {target:.2f}s "
            f"→ {'超出' if me_p95 > float(target) else '达标'}（未启用 --enforce-target，不判红）"
        )
    return (1 if (over_rail or over_target) else 0), reasons


def measure(*, source: str = DEFAULT_SOURCE, limit: int = 10, size: int = 1024,
            seed: int = 20261001, warmup: bool = True, images_dir: "str | None" = None,
            repeat: int = DEFAULT_REPEAT, progress=None,
            stage: str = "detect", engine: "str | None" = None
            ) -> "tuple[list, dict]":
    """对工作量逐项、每项重复 `repeat` 次，取**最小值**作为该图耗时；返回 (耗时, 元信息)。

    为什么取 min 而不是均值 / 单次（阶段 186 实测教训）
    --------------------------------------------------
    首版用「每图跑一次」，连续两次同批测量的 P95 是 **1.558s → 4.037s**（同图
    1.558s → 4.036s）——**机器负载噪声约 150%**，远超 30% 的回归护栏。若照此设门禁，
    它会**常红**（钉在噪声上，等同失效）。
    ⇒ 改用「**逐图 min-of-k**」：min 是**能力度量**（该图在这台机器上「至少能做到多快」），
    天然滤掉外部负载尖峰；重复次数 `repeat` 写入基线并参与可比性检查。

    先烧热（LESSONS 192：首次推理有冷启动伪影），再逐项计时。

    stage 参数（D1 真机验证扩展）
    ----------------------------
    · "detect"（默认）：仅测**检测阶段**（CPU 绑定的 OpenCV，不触及 GPU）。
      这是历史基线口径，反映「检测器本身」成本；GPU 对它**没有加速**。
    · "full"：测**检测+修复全链路**，调用 `api.remove_watermark_bytes`——
      会在 `WME_DEVICE_MODE=auto` 下吃到 GPU/LaMa 收益。路线图「P95<1.5s」
      目标针对的是**全链路**在生产环境的表现，必须用 --stage full 才能验证。
    """
    import numpy as np

    from core.detector import detect_watermark_mask
    items, source_used = iter_workload(source=source, limit=limit, size=size,
                                       seed=seed, images_dir=images_dir)
    # D1-Stage 6：采集计算设备指纹，写入 meta（基线可比性会据此拒绝跨硬件比对）。
    fp = device_fingerprint()

    if stage == "full":
        # 懒加载：避免 detect-only 默认路径也拉起完整 api 与模型。
        import cv2 as _cv2  # noqa: F401
        import api as _api
        _engine = engine or "lama"
        if warmup:
            _wimg = build_workload(size=64, seed=1)
            _wb = _cv2.imencode(".png", _wimg)[1].tobytes()
            try:
                _api.remove_watermark_bytes(_wb, engine=_engine, auto_preset=True, allow_auto_erase=True)
            except Exception:  # noqa: BLE001 - 烧热失败不影响正式计时
                pass
        k = max(1, int(repeat))
        times, spreads = [], []
        skipped = 0
        # ---- 阶段 214 · 锚点测量：轮首/轮尾测**同一张图** ----
        # 目的：抓「本轮内的渐进劣化」（如笔记本 GPU 持续负载降频）。
        # 该形态对 `spread/P95`（单图重复间离散度）**完全隐形**，却会污染 P95
        # （分位数专挑最慢样本，而最慢的恰是劣化末期）。
        # 锚点图用 items[0]（与轮内其它图不同，**图没变**⇒ 耗时变化只来自环境）。
        _anchor_name, _anchor_arr = items[0]
        _anchor_png = _cv2.imencode(".png", _anchor_arr)[1].tobytes()
        _anchor_head = None
        try:
            _t0 = time.perf_counter()
            _api.remove_watermark_bytes(_anchor_png, engine=_engine, auto_preset=True, allow_auto_erase=True)
            _anchor_head = time.perf_counter() - _t0
        except Exception:  # noqa: BLE001 - 锚点测量失败不影响正式计时
            pass
        for name, arr in items:
            _ab = _cv2.imencode(".png", arr)[1].tobytes()
            runs = []
            try:
                for _ in range(k):
                    t0 = time.perf_counter()
                    _api.remove_watermark_bytes(_ab, engine=_engine, auto_preset=True, allow_auto_erase=True)
                    runs.append(time.perf_counter() - t0)
            except Exception as exc:  # noqa: BLE001 - 该图未检出水印/修复失败 ⇒ 跳过，不中断整批
                skipped += 1
                if progress:
                    progress(len(times) + skipped, len(items), 0.0, name, 0.0)
                print(f"  [跳过单图] {name}: {exc}")
                continue
            times.append(min(runs))
            spreads.append(max(runs) - min(runs))
            if progress:
                progress(len(times) + skipped, len(items), min(runs), name, max(runs))
        if not times:
            raise RuntimeError(
                "全链路工作量中无一图检出有效水印掩码，无法计时。\n"
                "  --stage full 需要**含水印的真实图**：请放置若干张到你本机 AI 图到 "
                "benchmark_real/images/（或 --images-dir 指向含水印图目录），再重测。"
            )
        # 轮尾再测**同一张锚点图**（图没变 ⇒ 耗时变化只来自环境）。
        _anchor_ratio = None
        _anchor_tail = None
        try:
            _t0 = time.perf_counter()
            _api.remove_watermark_bytes(_anchor_png, engine=_engine, auto_preset=True, allow_auto_erase=True)
            _anchor_tail = time.perf_counter() - _t0
            if _anchor_head:
                _anchor_ratio = round(_anchor_tail / _anchor_head, 4)
        except Exception:  # noqa: BLE001
            pass
        # 阶段 263：记录 full 阶段超分口径（决定是否可比）。读 config（懒加载，full 分支
        # 本就拉起 api/torch，无额外代价；detect 分支不读，保持热路径零依赖）。
        try:
            import config as _cfg
            _rs_enabled = bool(getattr(_cfg, "REAL_SUPERRES_ENABLED", False))
            _rs_scale = int(getattr(_cfg, "REAL_SUPERRES_SCALE", 1))
            _rs_backend = str(getattr(_cfg, "REAL_SUPERRES_BACKEND", "auto"))
        except Exception:  # noqa: BLE001 - config 不可用时保守记为 None
            _rs_enabled, _rs_scale, _rs_backend = None, None, None
        return times, {"source": source_used, "stage": "full", "engine": _engine,
                       "runs": len(items), "skipped": skipped, "repeat": k,
                       "device": fp["device"], "gpu_name": fp["gpu_name"],
                       "size": size if source_used == "synthetic" else None,
                       "items": [n for n, _ in items],
                       "anchor_name": _anchor_name,
                       "anchor_head": round(_anchor_head, 4) if _anchor_head else None,
                       "anchor_tail": round(_anchor_tail, 4) if _anchor_tail else None,
                       ANCHOR_KEY: _anchor_ratio,
                       "spread_mean": round(sum(spreads) / len(spreads), 4) if spreads else None,
                       "real_superres_enabled": _rs_enabled,
                       "real_superres_scale": _rs_scale,
                       "real_superres_backend": _rs_backend}

    # ---- 默认：仅检测阶段（CPU 绑定，不触及 GPU）----
    if warmup:
        detect_watermark_mask(np.zeros((1, 1, 3), dtype=np.uint8), preset="general",
                              use_ocr=False)
    k = max(1, int(repeat))
    times, spreads = [], []
    # 阶段 214：detect 口径同样加锚点（CPU 负载/热节流也会渐进劣化）。
    _anchor_name, _anchor_arr = items[0]
    _anchor_head = None
    try:
        t0 = time.perf_counter()
        detect_watermark_mask(_anchor_arr, preset="general", use_ocr=False)
        _anchor_head = time.perf_counter() - t0
    except Exception:  # noqa: BLE001
        pass
    for i, (name, arr) in enumerate(items):
        runs = []
        for _ in range(k):
            t0 = time.perf_counter()
            detect_watermark_mask(arr, preset="general", use_ocr=False)
            runs.append(time.perf_counter() - t0)
        times.append(min(runs))
        spreads.append(max(runs) - min(runs))
        if progress:
            progress(i + 1, len(items), min(runs), name, max(runs))
    # 轮尾复测锚点图（同一张 ⇒ 耗时变化只来自环境）。
    _anchor_ratio = None
    _anchor_tail = None
    try:
        t0 = time.perf_counter()
        detect_watermark_mask(_anchor_arr, preset="general", use_ocr=False)
        _anchor_tail = time.perf_counter() - t0
        if _anchor_head:
            _anchor_ratio = round(_anchor_tail / _anchor_head, 4)
    except Exception:  # noqa: BLE001
        pass
    return times, {"source": source_used, "stage": "detect", "runs": len(items),
                   "repeat": k, "device": fp["device"], "gpu_name": fp["gpu_name"],
                   # 阶段 215：detect 口径**不涉及修复引擎**，但仍显式写入 `engine=None`，
                   # 而不是让该键缺席——缺席会走「基线缺字段 ⇒ 结构过旧」的路径，
                   # 语义上把「本来就无此维度」误报成「基线过期」。
                   "engine": None,
                   "size": size if source_used == "synthetic" else None,
                   "items": [n for n, _ in items],
                   "anchor_name": _anchor_name,
                   "anchor_head": round(_anchor_head, 4) if _anchor_head else None,
                   "anchor_tail": round(_anchor_tail, 4) if _anchor_tail else None,
                   ANCHOR_KEY: _anchor_ratio,
                   "spread_mean": round(sum(spreads) / len(spreads), 4) if spreads else None,
                   # 阶段 263：detect 口径不涉及超分，但显式写入 None 保持 meta 结构统一，
                   # 避免「键缺席 ⇒ 结构过旧」误判（与 engine=None 同理）。
                   "real_superres_enabled": None,
                   "real_superres_scale": None,
                   "real_superres_backend": None}


def main(argv: "list | None" = None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="性能探针（检测阶段延迟 / 时间预算门禁）")
    ap.add_argument("--source", choices=("benchmark", "synthetic"), default=DEFAULT_SOURCE,
                    help="工作量来源：benchmark=真实基准图（默认）；synthetic=兜底合成图")
    ap.add_argument("--limit", type=int, default=10, help="采样张数上限（默认 10）")
    ap.add_argument("--repeat", type=int, default=DEFAULT_REPEAT,
                    help=f"逐图重复次数，取 min 作能力度量（默认 {DEFAULT_REPEAT}）")
    ap.add_argument("--size", type=int, default=1024, help="合成图边长（仅 --source synthetic）")
    ap.add_argument("--seed", type=int, default=20261001, help="合成图随机种子（仅 synthetic）")
    ap.add_argument("--tol", type=float, default=DEFAULT_TOL, help="回归容差（默认 0.30）")
    ap.add_argument("--target", type=float, default=TARGET_P95_SECONDS,
                    help="绝对目标 P95 秒数（默认 1.5）")
    ap.add_argument("--enforce-target", action="store_true",
                    help="把绝对目标升级为硬红线（GPU/CI 环境用；CPU 上必然超）")
    ap.add_argument("--images-dir", default=None, help="基准图目录（默认 benchmark_real/images）")
    ap.add_argument("--stage", choices=("detect", "full"), default="detect",
                    help="测量阶段：detect=仅检测（CPU，默认，不触及 GPU）；"
                         "full=检测+修复全链路（吃到 GPU/LaMa 收益，用于验证 P95<1.5s 目标）")
    ap.add_argument("--engine", default=None,
                    help="full 阶段的修复引擎（默认 lama，以验证 GPU 收益；CPU 上也可用）")
    ap.add_argument("--update", action="store_true",
                    help="测量并写入基线（受下方护栏约束；写完**继续**按护栏/红线判定）")
    ap.add_argument("--force-update", action="store_true",
                    help="逃生门：跳过「向下刷新护栏」强制写入基线（**不**跳过红线）")
    ap.add_argument("--baseline", default=BASELINE_PATH, help="基线文件路径")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    print("=" * 78)
    print(f"性能探针 · 阶段={args.stage} · source={args.source} limit={args.limit} "
          f"repeat={args.repeat}" + (f" engine={args.engine}" if args.stage == "full" else ""))
    print("=" * 78)

    try:
        times, meta = measure(source=args.source, limit=args.limit, size=args.size,
                              seed=args.seed, images_dir=args.images_dir,
                              repeat=args.repeat, stage=args.stage, engine=args.engine,
                              progress=lambda i, n, dt, name, mx: print(
                                  f"  第 {i:>2}/{n} 张：min={dt:.3f}s  max={mx:.3f}s  {name}"))
    except Exception as exc:  # 检测器不可用（如无 cv2/np）→ 跳过，不制造假红
        print(f"[跳过] 检测器不可用：{exc}")
        return 2

    stats = summarize(times)
    print()
    print(f"工作量：source={meta['source']}  张数={meta['runs']}  逐图重复={meta['repeat']}"
          f"（取 min）")
    print(f"统计：min={stats['min']:.3f}s  P50={stats['p50']:.3f}s  "
          f"P95={stats['p95']:.3f}s  max={stats['max']:.3f}s  均值={stats['mean']:.3f}s")
    # 阶段 212：把run 内极差换算成**无量纲相对噪声**并直接对照阈值 ——
    # 这比「打印一个秒数让读者自己判断」有效得多（实测能以 21 倍差异区分
    # 正常轮 0.016~0.083 与受干扰轮 0.346）。
    _nr = noise_ratio(stats, meta)
    if _nr is not None:
        _verdict = "✅ 可信" if _nr <= NOISE_MAX else f"❌ 超过 {NOISE_MAX:.2f}（测量受污染）"
        print(f"逐图重复内的极差均值 = {meta['spread_mean']:.3f}s"
              f"（机器负载噪声参考；远大于统计量差异时本次测量不可信）")
        print(f"  → 相对噪声 = 极差/P95 = **{_nr:.3f}**（阈值 {NOISE_MAX:.2f}）→ {_verdict}")
    elif meta.get("spread_mean") is not None:
        print(f"逐图重复内的极差均值 = {meta['spread_mean']:.3f}s（P95 为 0，噪声比无法计算）")

    #阶段 212：`repeat<=1` 时极差恒 0，噪声判据**失去区分力**（不是"环境干净"，
    #   而是"没机会产生离散度"）。此时**不硬拦**（那是配置问题、不是污染问题，
    #   硬拦会掩盖真实回归），但必须**如实提示**，否则读者会把 0.0误读成"完美干净"。
    if int(meta.get("repeat") or 0) < 2:
        print(f"  ⚠️  repeat={meta.get('repeat')}：min-of-k 不足 2 次，"
              f"极差恒 0，噪声判据无法工作（**不等于**环境干净）。建议 --repeat >= 3。")

    # 阶段 214：锚点法（抓「本轮内渐进劣化」，与单图离散度正交）。
    if meta.get(ANCHOR_KEY) is not None:
        _ar = meta[ANCHOR_KEY]
        _ah = meta.get("anchor_head")
        _at = meta.get("anchor_tail")
        _averdict = ("✅ 无渐变" if _ar <= ANCHOR_MAX
                     else f"❌ 超过 {ANCHOR_MAX:.2f}（本轮渐进劣化）")
        print(f"  → 锚点图「{meta.get('anchor_name', '?')}」"
              f"轮首 {_ah:.3f}s → 轮尾 {_at:.3f}s"
              f"（尾/首 = **{_ar:.3f}**）→ {_averdict}" if (_ah and _at) else
              f"  → 锚点比 = {_ar}（缺首/尾原始值）")

    if meta["source"] != args.source:
        print(f"[注意] 请求 source={args.source}，实际使用 source={meta['source']}"
              f"（基准图不可用 ⇒ 已降级为合成图；其成本被低估，仅作冒烟）")

    baseline = None
    if os.path.isfile(args.baseline):
        try:
            with open(args.baseline, encoding="utf-8") as f:
                baseline = json.load(f)
        except Exception as exc:
            print(f"[警告] 基线读取失败（按无基线处理）：{exc}")

    if args.update:
        # 阶段 191（对齐 146 的门禁家族规范）：刷新基线是**受护栏约束的动作**，
        # 不是静默通道。
        #⓿ **测量可信度闸门**（阶段 212 新增，必须排在可比性之前）：
        #   一次受环境干扰的慢测量若被刷进基线，性能参照即被**永久摧毁**——
        #   之后真正的回退会拿「污染后的高基线」当标准，判不出红灯；而`--force-update`
        #   本是给「确实变慢」用的逃生门，不该被用来合法化噪声。故噪声超阈值时
        #   **直接拒绝刷新并要求重测**，不给逃生门（`--force-update` 也不放过）。
        _nr_upd = noise_ratio(stats, meta)
        if _nr_upd is not None and _nr_upd > NOISE_MAX:
            print(f"\n[拒绝刷新] 本次测量被环境污染（相对噪声 {_nr_upd:.3f} > "
                  f"{NOISE_MAX:.2f}）⇒ P95 {stats.get('p95')}s 不可信，"
                  f"**拒绝写入基线**（否则会摧毁性能参照）。")
            print("  → 请关闭其他占用 CPU 的任务后重测；此闸门**没有逃生门**"
                  "（`--force-update` 也不放过噪声值）。")
            return 1
        #   ⓪ **可比性护栏**：旧基线存在时，若本次**工作量口径**与它不同
        #      （source / stage / repeat / 结构版本），不得直接覆盖——否则一次
        #      `--update --source synthetic` 就能把「真实图基线」冲掉，可比性尽失。
        if baseline and not args.force_update:
            _ok, _why = baseline_comparable(baseline, meta)
            if not _ok:
                # 阶段 211 修正：区分「基线**结构过旧**」与「工作量口径真的不同」。
                #   · 结构过旧（版本不匹配 / 缺可比性字段）⇒ 该基线本就永远判跳过、
                #     对任何比较都无约束力，**直接重新标定**是正确的修复动作；
                #     若也要求 `--force-update`，就等于「递增版本号逼人重标定，
                #     却又不让人重标定」——自相矛盾的死胡同（且真实存在：本阶段实测）。
                #   · 口径不同（source/stage/repeat/device真的不一样）⇒ 必须挡住，
                #     否则一次 `--update --source synthetic` 就能把真实图基线冲掉。
                _stale = ("结构过旧" in _why) or ("缺字段" in _why)
                if not _stale:
                    print(f"\n[拒绝刷新] 本次测量与既有基线不可比：{_why}")
                    print("  → 如确要替换该基线，请显式加 `--force-update`。")
                    return 1
                print(f"\n[重新标定] 既有基线{_why}——该基线已无法用于任何比较，"
                      f"将用本次测量直接替换。")
        #   ① **向下刷新护栏**：本次 P95 若比旧基线差出容差以上 ⇒ 拒绝写入并返回 1；
        #      逃生门是显式的 `--force-update`。
        #   ② **刷新后继续走判定**：否则刷新会绕过绝对目标 ⇒ 用户看到「基线已刷新」
        #      + 退出码 0，会以为一切正常。
        if not args.force_update:
            _down = baseline_guard.check_downward_refresh(
                baseline or {},
                {P95_METRIC: stats.get(P95_METRIC)},
                _refresh_rules(),
            )
            if _down:
                for _line in baseline_guard.render_refusal(
                        _down, baseline_label="性能基线", force_flag="--force-update"):
                    print(_line)
                return 1
        payload = dict(meta)
        payload.update(stats)
        payload["version"] = BASELINE_VERSION
        # 阶段 212：把当次相对噪声写进基线，供事后审计「这份基线是在多干净的
        # 环境标定的」。**不参与可比性比较**（那是逐项相等的硬维度），只作记录。
        _nr_payload = noise_ratio(stats, meta)
        if _nr_payload is not None:
            payload[NOISE_RATIO_KEY] = round(_nr_payload, 4)
        payload["measured_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        os.makedirs(os.path.dirname(args.baseline), exist_ok=True)
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 已写入基线：{args.baseline}")
        print("（刷新后继续按护栏与绝对目标判定：刷新动作不得成为绕过判定的通道）")
        # 用**刚写入的**基线做比对（差值恒为 0），本次仍回答「新基线是否满足绝对目标」。
        baseline = payload

    code, reasons = gate_decision(baseline, stats, meta, tol=args.tol,
                                 enforce_target=args.enforce_target, target=args.target)
    print()
    for r in reasons:
        print("  ·", r)
    verdict = {0: "✅ 性能门禁通过", 1: "❌ 性能门禁未通过", 2: "⏭ 跳过（未标定 / 不可比）"}[code]
    print(f"\n{verdict}（退出码 {code}）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
