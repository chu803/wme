# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/real_gate.py
 文件说明 : 真实图质量门禁（阶段 83 · 真实图门禁分册）。

 为什么需要这个门禁（分册）
 --------------------------
 阶段 57 起，`scripts/quality_gate.py` 用**合成图**（640²、程序生成底图）把检测 + 修复
 的端到端结果固化成机器可判定的契约。但方向 A（阶段 A1~A6）与阶段 82 共同证明：

   · 合成图标定的阈值在真实 AI 平台图上**系统性失效**（阶段 A4：general 精确率
     1.29% vs 合成 51.3%，≈40 倍崩塌）；
   · 阶段 82 的全部收益（召回 71.7→93.3%、精确率 1.29→3.08%）**只由手工脚本
     `run_real_benchmark.py` 与文档维系**——没有任何自动判定。

 也就是说：任何一次改动一旦回退掉碎片聚合或自然光影守卫，**合成门禁全绿、全量测试
 全绿**，真实图收益会被静默丢弃。这正是阶段 57 在合成侧已经解决过的问题，在真实侧
 重演。本脚本把真实图指标固化成**第二条基线契约**（「双线契约」，与阶段 72 同范式）：
 合成线管「算法常数不被改坏」，真实线管「真实分布上的结论不被回退」。

 与合成门禁的分工
 ----------------
   · 合成门禁（quality_gate.py）：口径 640² 程序底图，含修复引擎与交付链；红线 + 漂移。
   · 真实门禁（本脚本）     ：口径 30 张真实 1024² AI 图，**只评估检测侧**（真实图没有
     「干净原图」可对齐，ΔPSNR 无意义）；绝对红线 + 基线漂移 + 数据完整性。

 三层判定
 --------
   0) 数据完整性（新增，防「容错成为错误的藏身处」）：
      `run_all` 对读图失败是**静默 skip** 的，若 30 张里有几张读不出来，聚合均值会照常
      算出来、只看数字完全看不出来。故强制断言「实际评估图数 == manifest ∩ truth 的
      标注图数」，不等即失败。
   0b) 标注覆盖面与承诺面（阶段 93 建立，阶段 95 扩写）：真值只标了 `corner_logo`
      一类，未标注的水印不计入漏检，于是 recall 被结构性抬到 1.0000。阶段 93 把这个
      「沉默」显式化；阶段 95 用户决定**只承诺角标 LOGO 类图片**，于是
      `config.WATERMARK_TYPE_COMMITTED` 成为承诺面单一真值来源，判定分三档：
        · 数据矛盾 / 枚举外 style        → 始终硬失败（标注错误）
        · **承诺类型缺真值证据**          → 始终硬失败（承诺比证据多；可执行）
        · 清单只有 catch-all 声明         → 只告警，`--strict-scope` 可升级
      阶段 95 之后 `--strict-scope` 首次可以**长期为绿**（承诺面 = 角标，角标有真值）。
   1) 绝对红线（REAL_PRESET_CONTRACT）：只对**已登记契约的预设**（当前仅 general）生效。
      未登记预设（如 ai_gradient 设计上恒为零输出）只做监控与漂移，不设绝对红线。
      红线直接编码各阶段的工程结论：
        · recall  ≥ 0.98  ← 低于此说明**碎片聚合（82）或白底圆角板块判据（86）失效**
                             （82 前 0.717 / 82 后 0.9333 / 84 后 0.9500 / 86 后 1.0000）
        · precision ≥ 0.092 ← 低于此说明**自然光影守卫（82）、纹理稠密守卫（84）、
                             或角标候选来源（100：MSER 降级通路默认关闭）失效**
                             （82 前 0.0129 / 82 后 0.0308 / 84 后 0.0388 / 86 后 0.0385
                              / **100 后 0.1382**）
        · iou     ≥ 0.55  ← 崩溃护栏（几何匹配整体崩坏；86 实测 0.7573，100 后 0.7518）
        · mask_coverage ≤ 0.020 · coverage_ratio ≤ 3.50× · mask_iou ≥ 0.15
                            （阶段 106 起；100 时为 0.11 / 11.5× / 0.077，
                              99 时为 0.19 / 19.5× / 0.047，86~93 时为 0.25 / 25× / 0.042；
                              mask_iou 阶段 183 由 0.10 抬到 0.15——177 真实真值重算后
                              实测 0.2975，旧 0.10 松于实测的一半，违反防哑巴不变式）
                          ← **过检面积三线**（阶段 93 新增；99 / 100 / 106 三次按收敛结果重标）。
                            此前本门禁**只罚「漏」（recall）不罚「多」**，于是掩码一路
                            膨胀到均值 16.18%、预测/真值面积倍率 16.62×，而门禁永远是绿的
                            （docs/92 S2）。阶段 99 把 `DETECT_MAX_COVERAGE_RATIO` 由
                            0.30 收敛为 0.15，实测降到 0.1266 / 13.00× / mask_iou 0.0699；
                            **阶段 100 把角标 MSER 降级通路默认关闭**（docs/100），
                            再降到 0.0745 / 7.65× / mask_iou 0.1163，recall 仍 1.0000；
                            **阶段 106 收敛「框内证据的几何膨胀」**（pad 2→1、k_scale 0.5→0.65），
                            再降到 **0.0365 / 3.75× / mask_iou 0.1705、precision 0.1552**，
                            recall 1.0000 与区域 IoU 0.7518 逐位不变。
                            **「收敛」的完整含义是两件事**：改行为 + 把新结论写进红线。
                            只改行为不重标，改进就能静默回退——旧 0.25 之下，
                            退回 0.1618 也不会红；重标到 0.045 后**红线与漂移都能拦**。
                            （注意：`coverage_ratio` 是**逐图倍率的均值**；用「总预测面积
                             ÷ 总真值面积」会得到 ≈47× 的错误口径，详见下方
                             REAL_PRESET_CONTRACT 标定注释。红线值以实现为准，
                             勿在此处另写一份。）
                            ✅ **阶段 93 登记的目标（coverage ≤ 0.05 / ratio ≤ 5）已达成**
                            （0.0365 / 3.75×）—— 且 0.045 这条红线本身就把它编码住了：
                            退回阶段 105 的 0.0525 会同时触发红线与漂移。
                            （ratio ≤5 未单独写进红线：0.045 与 4.35 是同一次收敛的两个刻度，
                             4.35 已隐含 ratio 已进入「个位数倍率」。）
                            残余过检（阶段 106 归因）：`general` 的残余已不是框填充型
                            （`edge_text` 框型通路被 103 的框内二次确认 + 106 的 pad 收敛
                            压过两轮），而是**半透明通路在真实照片上的固有误报**；
                            它受 82/84 两道守卫保护，且其真阳性正是唯一承诺类型 `ai_logo`
                            的召回来源 ⇒ **不宜再砍**（砍它会击穿 precision/recall 契约）。
                            这三条仍是**崩溃护栏**（守「不再整体崩坏」，不编码某个实验值），
                            故登记在 REAL_CRASH_GUARD_METRICS 中；日常回归由漂移容差先拦。
   1b) 基线漂移的**方向**从红线方向派生（阶段 93）：`mask_coverage` 是「越高越坏」，
       旧的「只查下滑」实现会让掩码膨胀到 30% 都不报警——同一指标出现两个相反方向
       语义属于「同一语义两套实现」，故方向不再在 REAL_TOLERANCES 里重复声明。
   2) 基线漂移（REAL_TOLERANCES）：与 `benchmark_real/quality_baseline.json` 比对，
      只约束「变坏」方向。真实图数据集是**固定文件**，判定完全确定性（同码同数据同结果），
      故漂移即真实回退，容差只用来吸收依赖库版本的浮点扰动。
   3) **未承诺预设的「掩码面积」监控**（阶段 107，REAL_AREA_CONTRACT）：
      对 `config.WATERMARK_TYPE_SCOPE` 里 `committed=False` 的 4 类预设
      （`ai_faint_tiled` / `ai_text_heavy` / `ai_grid` / `ai_gradient`）只量**面积**——
      面积不需要真值（`掩码像素 / 图面像素`），于是「平铺增强 / 网格两条通路在真实照片上
      会不会涂满全图」第一次成为机器可判定的（此前它们在本门禁里**结构性失明**，
      见阶段 104/106 登记）。判据只守上界 + 15% 漂移，**不参与任何承诺结论**。

 判定逻辑刻意做成纯函数 `evaluate_real()`，不依赖文件系统，便于单测直接喂数据校验。

 用法
 ----
   python scripts/real_gate.py                          # 跑 general 预设并与基线比对
   python scripts/real_gate.py --update                 # 刷新基线快照
   python scripts/real_gate.py --presets general,ai_corner   # 换预设（默认 general）
   python scripts/real_gate.py --baseline <path>        # 换基线路径
   python scripts/real_gate.py --strict-scope           # 覆盖面不足也判失败
                                                        # （阶段 95 起承诺面完整 → 绿）
   python scripts/real_gate.py --no-area                # 跳过面积监控（提速）

 退出码
 ------
    0 = 通过
    1 = 未通过（红线越界 / 漂移超容差 / 数据不完整 / 承诺类型缺真值证据）
    2 = 跳过：manifest 或 ground_truth 缺失（数据集未就位）。**独立于 0**，
        以便 CI 区分「真的通过了」与「根本没跑」——静默通过是缺陷温床。
        （阶段 171 勘误：此处原文误写「0 = 通过（含「数据集缺失 → 跳过」）」，
          与下一行「2 = 跳过：…缺失」及实现自相矛盾——「数据集缺失」返回的是 2。）

 依赖：numpy / PIL / config / core.detector / scripts.run_real_benchmark（零 gradio）。
==============================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

# 项目根目录注入 sys.path（允许从任意工作目录运行）。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scripts.run_real_benchmark import (  # noqa: E402
    MANIFEST_PATH,
    TRUTH_PATH,
    run_all,
)
from utils.stats import nearest_rank_percentile  # noqa: E402
from utils.path_helper import display_path  # noqa: E402
import config  # noqa: E402  （阶段 95：承诺面 WATERMARK_TYPE_COMMITTED 的唯一真值来源）
from scripts import baseline_guard  # noqa: E402  # 阶段 146：刷新基线护栏（三条门禁共用）

BASELINE_PATH = os.path.join(_ROOT, "benchmark_real", "quality_baseline.json")

#: 默认只跑 general：真实图上唯一有明确契约语义的预设（阶段 A4/A5/82 的全部数字都指它）。
#: 10 个预设全跑约 300 次检测（CPU 数分钟），需要时用 --presets 显式指定。
DEFAULT_REAL_PRESETS = ("general",)

# ---------------------------------------------------------------------------
# ① 绝对红线（按预设登记；未登记预设 → 只监控/漂移，不判红线）
# ---------------------------------------------------------------------------

#: 预设 → {指标: (方向, 阈值)}。"min" 越低越坏，"max" 越高越坏。
#: 只登记「有真实图语义」的预设；避免给设计上恒零的预设（ai_gradient）加无意义红线。
REAL_PRESET_CONTRACT: dict = {
    "general": {
        # ⚠ **阶段 177 重标**：旧上限（recall 0.98 / precision 0.092 / mean_iou 0.55）
        #   全部标定于**伪造真值期**（阶段 149 判定原 60 条 corner_logo GT 系模板复制；
        #   172 收编 10 张真实豆包图重建真值后，实测全面重算，见 docs/172/177）。
        #   按「红线值以实现为准 + 收敛必须重标」（经验 75）重标为**回归地板**：
        #   实测 × 约 0.8（向宽松侧取整），既拦住回退、又不会一上线就红灯。
        #   真实实测（阶段 183：10 图 · general）：mask_recall 0.5619 / recall 0.8000 /
        #   precision 0.0690 / mean_iou 0.5347 / mask_iou 0.2975 /
        #   mask_coverage 0.0291 / coverage_ratio 2.80×。
        #   ⚠ **阶段 240（Option A）再锚**：2026-10-05 导入 q 系列 11 张（数据集 10→29 图，
        #   豆包角标更小更靠角），过检天然更低。当前 29 图实测：precision 0.0761 /
        #   mask_coverage 0.0107 / coverage_ratio 1.92× / mask_iou 0.2714；红线随之下移：
        #   mask_coverage 0.045→**0.020**、coverage_ratio 4.35→**3.50**、mask_iou 0.15 不变
        #   （三者均满足「红线夹逼实测」不变式：max 型 实测<红线≤实测×2；min 型 实测/2≤红线<实测）。
        # ---- 头条：掩码级召回（阶段 175 起为对外主口径；区域级对碎片与框大小失真） ----
        #   注入实测（关板块通路 WME_DETECT_PLATE_ENABLED=false）：0.5619 → **0.3535**
        #   ⇒ 红线 0.45（= 实测×0.8）**高于** 0.3535 ⇒ 该通路被回退时必然报警。
        "mask_recall": ("min", 0.45),
        # 区域级召回：实测 0.8000 × 0.80 = 0.64。
        "recall": ("min", 0.64),
        # 精确率：实测 0.0690 × 0.72 = 0.050。仍**高于**三个守卫失效实测值
        #   （关自然光影 0.0224 / 关纹理稠密 0.0308 / 开回 MSER 0.0392，
        #   系伪造真值期测得但方向性仍成立）⇒ 守卫被回退仍会报警。
        "precision": ("min", 0.050),
        # 崩溃护栏（几何匹配整体崩坏）：实测 0.5347，取 0.35（≈ 实测×0.65）。
        "mean_iou": ("min", 0.35),
        # ---- 阶段 93 新增 · **阶段 99 收敛后重标**：过检面积（此前完全无红线，docs/92 S2）----
        #   阶段 86 基线（收敛前）：coverage **0.1618**（中位 0.1545、最大 0.2900）、
        #   ratio **16.62×**（= 逐图「预测掩码面积 ÷ 真值水印面积」再取均值，理想 1.0）。
        #   阶段 99 收敛后（`DETECT_MAX_COVERAGE_RATIO` 0.30 → 0.15）：
        #   coverage **0.1266**（−21.8%）、ratio **13.00×**（−21.8%）、
        #   mask_iou **0.0699**（+10.1%）；recall 1.0000 与 mean_iou 0.7573 逐位不变。
        #   **阶段 100 二次收敛**（角标 MSER 降级通路默认关闭，docs/100）：
        #   coverage **0.0745**（较 99 −41%、较 86 −54%）、ratio **7.65×**、
        #   mask_iou **0.1163**（+66%）；recall 1.0000 不变、
        #   mean_iou 0.7573 → **0.7518**（−0.7%：MSER 框不再「抢先」匹配真值，
        #   少数 GT 改由 IoU 略低的区域命中；容差 15%，量级可忽略但如实登记）。
        #   本阶段红线再压到实测的约 1/1.5（0.11 / 11.5 / 0.077），基线同步刷新。
        #   分层设计：**漂移容差（15%）守「变化」，红线守「崩溃」**，故红线统一留
        #   约 1.5× 余量，日常回归由漂移先拦住。
        #   ✅ 阶段 100 后，「过检退回上一级（0.1266 / 13.00）」已被**红线与漂移双重**
        #     拦下（0.1266 > 0.11、0.1266 > 0.0745×1.15=0.0857）——这比阶段 99 时
        #     「只有漂移抓得住」更强，也说明红线收紧没有制造哑巴。
        #   🎯 **阶段 106：阶段 93 的「过检面积 ≤5%」目标首次达成**（0.0525 → **0.0365**）。
        #     直接原因：`DETECT_BOX_REFINE_PAD` 2→1 与 `K_SCALE` 0.5→0.65（框内证据的
        #     5×5 膨胀是「框型策略」掩码的主要放大源，而 `general` 预设正是靠 corner/edge
        #     两条框型通路）。同时 ratio 5.39×→**3.75×**、mask_iou 0.1426→**0.1705**、
        #     precision 0.1478→**0.1552**，**recall 1.0000 与区域 IoU 0.7518 逐位不变**
        #     ⇒ 面积更小、却与真值更重合（与阶段 103 同向的严格改进）。
        #   红线按**双约束**重标（阶段 106 新增，见 e2e_real_probe 同名说明）：
        #     `red ∈ [实测×(1±15%), 改动前实测)`，即既不被噪声误判、又能拦住回退：
        #       mask_coverage 0.11 → **0.045**（< 改动前 0.0525，且 < 0.05 ⇒ 红线本身
        #                                    即编码了「阶段 93 目标已达成」）
        #       coverage_ratio 11.5 → **4.35**（< 改动前 5.39）
        #     `mask_iou` **不重标**（保持 0.077 = 崩溃护栏）：本阶段它 +19.6%，回退 −16.4%
        #     **已超过 15% 的漂移容差** ⇒ 由漂移层负责拦截，无需把红线钉到 0.1435 那种
        #     只剩 0.6% 余量的位置（经验 77：不得为了过测试去拟合阈值）。
        #     `precision` 同理不重标：本阶段只 +5.0%，**既低于红线 15% 的分辨率、
        #     也低于其 25% 的漂移容差 ⇒ 该改进目前无法被任何自动判定守住**（如实登记，
        #     不硬抓——把容差压到 <5% 等于把守卫钉在噪声上）。
        #   ⚠ 标定注意：`coverage_ratio` 是**逐图倍率的均值**，不等于「均值之比」
        #     （0.1618 / 0.00346 ≈ 47× 是错的算法——每张图真值框大小不同，
        #     先算比值再平均才是正确口径）。实测值 7.65× 以本门禁输出为准。
        #   ⚠ 残余过检的下一步（阶段 106 归因更新）：`general` 残余已不再是「框填充」
        #     （`edge_text` 框型通路被 103 的框内二次确认 + 106 的 pad 收敛压过两轮），
        #     而是**半透明通路在真实照片上的固有误报**（受 82/84 两道守卫保护，
        #     其真阳性正是唯一承诺类型 `ai_logo` 的召回来源 ⇒ 不宜再砍）。
        #   ⚠ `core/detector` 内部的面积上限（阶段 100 起 0.15）与本节红线是**两道**
        #     独立的闸：前者在检测器内按「小区域优先」裁剪，后者是门禁的崩溃护栏。
        "mask_coverage": ("max", 0.020),
        #   相对量判据：对「角标只看几千像素、平铺看整幅」这两类水印**可比**，
        #   故补 4 类新样式时这条红线不会被"合法的大面积水印"顶掉（经验 1 绝对量优先、
        #   相对量兜底；此处两者并存且各有分工）。
        "coverage_ratio": ("max", 3.50),
        # 崩溃护栏：整幅掩码级 IoU（阶段 86 实测 0.0635 → 99 收敛后 0.0699
        #   → 100 后 0.1163 → **阶段 106 后 0.1705**，两次剔误报后累计 +169%）。
        #   与区域级 IoU 正交，两者背离即诊断「不是框偏了，是框太多了」。
        #   阶段 106 由 0.077 提到 **0.10**：不是为了拦回退（回退 −16.4% 已超 15% 漂移容差，
        #   由漂移层负责），而是满足「红线不得松于实测的一半」这条防哑巴不变式
        #   （`tests/test_watermark_scope.py`）——0.077 在实测 0.1705 下只剩 0.45×，
        #   等于容忍 55% 的整幅塌陷。
        #   ⚠ **阶段 183 抬到 0.15**：177 用真实真值重算后实测抬到 **0.2975**
        #   （实测/2 = 0.14875），而红线仍停在被**伪造真值期**实测 0.1705 校准的 0.10
        #   ⇒ 0.10/0.2975 = 0.34×，松于实测的一半，**违反上面这条不变式**
        #   （单调性：min 型护栏抬高 = 放宽，不会制造新红灯，真实回退仍由 15% 漂移层拦截）。
        #   这是「阶段 177 重标只改了实现侧、没同步标定表」的遗留，随 183 一并收口。
        #   ⚠ **阶段 243 再锚（承诺面口径变更，a1~a4 登记为图像级已知缺口后）**：
        #     承诺面实评 29→25 图后实测 mask_iou 抬到 **0.313**（剔除检测器几乎全漏的
        #     缺口图，微平均分母里的「几乎不命中」样本消失 ⇒ 均值上抬，非检测器改动）。
        #     0.15 在新实测下只剩 0.15/0.313 = **0.48×**，已**松于实测的一半**
        #     （=容忍 52% 的整幅塌陷）⇒ 违反上面那条防哑巴不变式（守卫实测抓到了）。
        #     按不变式重锚到 **0.20**（0.313/2=0.157 ≤ 0.20 < 0.313，仍留约 1.6× 余量），
        #     恢复「红线守崩溃」的本意。min 型抬高=放宽，不会制造新红灯。
        "mask_iou": ("min", 0.20),
    },
}

#: 红线与「实测值」的标定对照（docs/84 起集中登记）。测试断言红线**严格夹在**
#: 「上一阶段实测值」与「本阶段实测值」之间——既防被放宽到无意义（门禁变哑巴），
#: 也防被收紧到一上线就红灯。**新增阶段时同步更新本表**（经验 37）。
REAL_REDLINE_CALIBRATION: dict = {
    # ⚠ **阶段 177 重标**：旧区间均标定于**伪造真值期**（如 recall 的 hi=1.0000 直接
    #   来自被判定为模板复制的 60 条 GT），已作废。新区间统一取
    #   「(失败注入实测, 当前真实实测)」——红线须严格夹在两者之间：
    #     mask_recall 0.3535（关板块通路 WME_DETECT_PLATE_ENABLED=false）→ 0.5619（当前）
    "mask_recall": (0.3535, 0.5619),
    #     recall：无有效「上一阶段」值（旧值系伪造）⇒ 下界记 0.0 并说明，上限=当前实测
    "recall": (0.0, 0.8000),
    #     precision：下界取「开回 MSER 通路」实测 0.0392（方向性仍成立）→ 当前 0.0761
    #     （阶段 240（Option A）随 q 系列扩图后 29 图实测同步刷新）
    "precision": (0.0392, 0.0761),
}

#: 属于「崩溃护栏」而非「结论编码」的红线指标：它们不放宽/收紧到某个实验值之间，
#: 只保证「系统没有整体崩坏」，故不参与上下界夹逼断言。**必须显式登记**——
#: 目的是让「新增一条红线」强制成为一个决策点：它到底编码了哪个结论？
REAL_CRASH_GUARD_METRICS: frozenset = frozenset(
    {"mean_iou", "mask_iou", "mask_coverage", "coverage_ratio"}
)

#: ② 基线漂移容差（相对变坏比例；只约束变坏方向）。
#:
#: **方向不在本表重复声明**，而是从 `REAL_PRESET_CONTRACT` 的红线方向派生
#: （阶段 93 修正）：`mask_coverage` 是「越高越坏」，若仍按旧的「只查下滑」逻辑，
#: 掩码膨胀到 30% 反而不会被发现——红线与漂移对同一个指标给出相反的方向语义，
#: 是典型的「同一语义两套实现」（经验 24/77）。
REAL_TOLERANCES: dict = {
    "precision": 0.25,
    "recall": 0.10,        # 召回是 82 的核心收益，收紧
    # 阶段 175：**头条口径**掩码级召回（越高越好）。与 `recall` 同容差（10%）。
    #   基线快照里若还没有该键，漂移层会自动跳过（不会因缺键而误报）。
    "mask_recall": 0.10,
    "mean_iou": 0.15,
    # 阶段 99 由 0.20 统一为 0.15：与区域级 IoU 正交，仅作**交叉校验**，
    #   不单独收紧。理由（实测，勿凭直觉改）：阶段 99 收敛前后 mask_iou 只从
    #   0.0699 走到 0.0635（−9.2%）——要让它单独抓住「回退」，容差必须 ≤9.2%，
    #   余量不足 0.2%，等于把测试钉在噪声上。当时过检回退由 mask_coverage /
    #   coverage_ratio 两条主信号拦下（tests/test_overdetection99.py）。
    #   **阶段 100 起这条边界解除**：剔掉 MSER 误报后 mask_iou 走上 0.0699 → 0.1163
    #   （+66%），故「回退到 0.0699」是一次 −40% 的下滑，15% 容差抓得住
    #   （下限 0.1163×0.85 = 0.0989，余量 29%）——mask_iou 自此可以承担漂移主信号。
    "mask_iou": 0.15,
    "mask_coverage": 0.15, # 过检面积：越高越坏（方向由契约派生）
    # 阶段 99 新增：**此前 `coverage_ratio` 没有任何漂移容差**——它是过检的
    #   倍率形式，与 mask_coverage 同向，却只由崩溃护栏（19.5×）兜着。
    #   于是「13.00× 回退到 16.62×」既不过红线也不报漂移，静默通过。
    #   该缺口由 `tests/test_overdetection99.py` 的注入反例发现（回退三项中
    #   只有 mask_coverage 被拦下），故在此补上同值容差。
    "coverage_ratio": 0.15,
}

# ---------------------------------------------------------------------------
# 阶段 146 · 刷新基线的护栏参数
# ---------------------------------------------------------------------------
# 背景：本门禁的 `--update` 此前与 `quality_gate` 一样是**静默通道** ——
#   写完基线**直接 return 0**，绕过绝对红线、数据完整性与漂移判定；
#   且没有任何机制阻止把指标**合法化到更坏的值**（渐进刷新 ⇒ 门禁全程绿）。
#   `recall` 在本门禁有 10% 容差（阶段 82 收紧过），但**仍无绝对红线**，
#   故同样是「可被逐次刷新到任意低值」的候选。
_REFRESH_DIRECTIONS: dict = {
    # 「越低越好」的两个面积类指标（方向由 `REAL_TOLERANCES` 注释里的契约派生）。
    "mask_coverage": "max",
    "coverage_ratio": "max",
    # 面积监控（`area_only` 段）的三个派生量，同样「越低越好」。
    "mask_coverage_mean": "max",
    "mask_coverage_p90": "max",
    "mask_coverage_max": "max",
}

#: 面积监控段的容差来源：**复用**阶段 107 既有的 `AREA_DRIFT_TOLERANCE`
#: 与其漂移键表 `AREA_DRIFT_KEYS`（不新增阈值）。
#: ⚠ 这两者定义在本文件**更下方**（阶段 107 小节），因此**不能在模块级**用它们构造
#: 常量（会 `NameError: name 'AREA_DRIFT_KEYS' is not defined` —— 首版就是这么错的）。
#: 故改为在 `_refresh_rules_for()` **函数体内**引用（运行期求值，无顺序依赖）。


def _refresh_pairs(old_agg, new_agg) -> list:
    """把两侧的**嵌套** aggregate 提取成 `(路径键, 旧值, 新值)` 序列（阶段 146）。

    键形如 `per_preset.general.recall` / `area_only.ai_grid.mask_coverage_mean`。
    只取两侧**都存在**的 (段, 名, 指标) 三元组 —— 新增项无旧值可比，交漂移层处理。
    """
    pairs: list = []
    for section in ("per_preset", "area_only"):
        old_sec = (old_agg or {}).get(section) or {}
        new_sec = (new_agg or {}).get(section) or {}
        if not isinstance(old_sec, dict) or not isinstance(new_sec, dict):
            continue
        for name in sorted(set(old_sec) & set(new_sec)):
            o, n = old_sec[name] or {}, new_sec[name] or {}
            if not isinstance(o, dict) or not isinstance(n, dict):
                continue
            for metric in sorted(set(o) & set(n)):
                pairs.append((f"{section}.{name}.{metric}", o[metric], n[metric]))
    return pairs


def _refresh_rules_for(pairs) -> dict:
    """按 `pairs` 里出现的指标基名生成规则表。

    容差与方向**全部来自静态表**（`REAL_TOLERANCES` / `AREA_DRIFT_TOLERANCE` /
    `_REFRESH_DIRECTIONS`，单一真值来源）；这里只做「指标基名 → 带路径键」的映射。

    面积段只覆盖 `AREA_DRIFT_KEYS`（mean / p90）——与漂移层**同覆盖面**；
    `mask_coverage_max` 不在该表里，故不参与刷新护栏（避免「刷新层比漂移层更严」
    这种两套口径）。这两个名字定义在本文件更下方，因此在**函数体内**引用。
    """
    rules: dict = {}
    area_keys = set(AREA_DRIFT_KEYS)          # 运行期求值，避免模块级顺序依赖
    for key, _o, _n in pairs:
        metric = key.rsplit(".", 1)[-1]
        if metric in REAL_TOLERANCES:
            rules[key] = (_REFRESH_DIRECTIONS.get(metric, "min"),
                          REAL_TOLERANCES[metric], 0.0)
        elif metric in area_keys:
            rules[key] = (_REFRESH_DIRECTIONS.get(metric, "min"),
                          AREA_DRIFT_TOLERANCE, 0.0)
    return rules


# ---------------------------------------------------------------------------
# 阶段 107 · 未承诺预设的「掩码面积」监控（**不需要真值**）
# ---------------------------------------------------------------------------
# 缺口（阶段 104/106 两次登记）：本门禁此前只跑 `general`，而 `general` 的
# `enhance_tiled=None`、`enable_grid=None` ⇒ **平铺增强与网格两条通路在真实图上结构性失明**
# （阶段 106 把网格通路假阳性砍掉 76%，真实图侧却一点都看不见）。
#
# 补法：对**未承诺的 4 类预设**（`config.WATERMARK_TYPE_SCOPE` 里 committed=False 的那 4 类）
# 只量「掩码面积」——它**不需要真值**（`mask_coverage = 掩码像素 / 图面像素`），
# 于是「这条通路在真实照片上会不会突然涂满全图」这类劣化第一次变成**机器可判定**的。
# 判据只有上界（面积越大越坏），与红线层同向；漂移层用同样的 15% 容差。
#
# 为什么是这 4 个而不是全部 10 个：① 另外 4 个（`ai_tiled` / `ai_signature` / `ai_corner` /
# `ai_multi`）是 UI 变体或其它预设的并集，监控它们只会重复计数；② `general` 走红线层
# （有真值）；③ `ai_logo` 是**承诺类型**，已在红线层内。
#: 被面积监控的预设（顺序即输出顺序）。
AREA_ONLY_PRESETS = ("ai_faint_tiled", "ai_text_heavy", "ai_grid", "ai_gradient")

#: 面积红线：`{预设: {指标: (方向, 上界)}}`。标定口径 = 实测均值 ×1.15 再向 0.5pp 取宽松侧；
#: 只守上界（面积越大越坏）。**不需要**「双约束」的第二条——面积监控是新增维度，
#: 没有「改动前实测值」可比，故只保留「不因噪声误判」这一条。
#:
#: 实测（2026-09-23，30 张真实图，`%TEMP%/s107_area_probe.py`）——
#:   | 预设 | 覆盖率均值 | P90 | 最大 | 有掩码图数 | 平均区域数 |
#:   | ai_faint_tiled | 6.12% | 11.52% | 14.42% | 30/30 | 38.1 |
#:   | ai_text_heavy  | 3.43% |  6.57% |  9.17% | 30/30 | 16.3 |
#:   | ai_grid        | 10.46% | 14.94% | 14.97% | 30/30 | **547.7** |
#:   | ai_gradient    | **0.00%** | 0.00% | 0.00% | **0/30** | 0.0 |
#:
#: ⚠ **两条必须同时说的读数**：
#: ① `ai_grid` 的「最大 14.97%」正贴着检测器内置的面积闸（`DETECT_MAX_COVERAGE_RATIO=0.15`）
#:    ⇒ 该通路的面积**在 fattest 图上被闸压住**，均值的上界因此也被压（这是为什么红线取
#:    12.5% 而不是「均值的 1.15 倍再留大余量」）。**任何面积红线都必须 < 0.15**，
#:    否则它永远不会触发（= 哑巴红线）。本文件的 `tests/test_real_area_monitor.py` 钉住这条。
#: ② `ai_gradient` 在 30 张真实图上**一次都没触发**（0/30）——「设计上恒为零输出」这条
#:    自述第一次被量测确认。故对它不设「均值上界」（0 的 1.15 倍仍是 0，会恒红），
#:    改设**静默守卫**：`n_with_mask ≤ 0`（一旦渐变通路开始在普通照片上触发，必须有人来
#:    更新这份契约，而不是让它静默发生）。
REAL_AREA_CONTRACT: dict = {
    "ai_faint_tiled": {"mask_coverage_mean": ("max", 0.075)},
    "ai_text_heavy": {"mask_coverage_mean": ("max", 0.0425)},
    "ai_grid": {"mask_coverage_mean": ("max", 0.125)},
    "ai_gradient": {"mask_coverage_mean": ("max", 0.05),
                    "n_with_mask": ("max", 0)},
}

#: 面积漂移容差（与 REAL_TOLERANCES 同值起步）。
AREA_DRIFT_TOLERANCE = 0.15
#: 参与漂移比对的面积指标（全部是「越高越坏」，故方向可写死，不必查契约）。
AREA_DRIFT_KEYS = ("mask_coverage_mean", "mask_coverage_p90")


def _area_stats(rows: "list | None", preset: str) -> "dict | None":
    """从逐图结果统计某预设的**掩码面积画像**（纯面积，不碰真值）。

    返回键：
        n                参与统计的图数（读图/检测失败的图被剔除）
        mask_coverage_mean / p90 / max   掩码覆盖率（占全图百分比，越高越坏）
        n_with_mask      至少产生一个掩码像素的图数（0 = 该通路在真实图上完全沉默）
        pred_count_mean  平均区域数（诊断「面积大是因为区域多还是区域大」）
    """
    covs: list = []
    pred_counts: list = []
    for r in (rows or []):
        res = (r.get("results") or {}).get(preset)
        if not res or "error" in res:
            continue
        covs.append(float(res.get("mask_coverage", 0.0)))
        pred_counts.append(int(res.get("pred_count", 0)))
    if not covs:
        return None
    covs_sorted = sorted(covs)
    n = len(covs_sorted)

    # 阶段 186：分位数抽为共用件 `utils.stats.nearest_rank_percentile`
    # （原为本地嵌套 `_pct`；口径逐位保留，见该模块说明）。
    def _pct(q: float) -> float:
        return nearest_rank_percentile(covs_sorted, q)

    return {
        "n": n,
        "mask_coverage_mean": round(sum(covs_sorted) / n, 4),
        "mask_coverage_p90": round(_pct(0.90), 4),
        "mask_coverage_max": round(covs_sorted[-1], 4),
        "n_with_mask": sum(1 for c in covs_sorted if c > 0),
        "pred_count_mean": round(sum(pred_counts) / n, 1),
    }


def _violate(direction: str, value: float, threshold: float) -> bool:
    """按方向判断是否越线（纯函数）。"""
    if direction == "max":
        return value > threshold
    return value < threshold


def _metric_direction(key: str, preset: str) -> str:
    """取某指标的「变坏方向」，**唯一真值来源 = 红线契约**。

    契约未登记该指标时按「越高越好」（`"min"`）处理——与旧行为一致，
    且保证新增监控项不会因为忘记写方向而静默失效。
    """
    entry = (REAL_PRESET_CONTRACT.get(preset) or {}).get(key)
    return entry[0] if entry else "min"


def _drift_worse(direction: str, now: float, base: float, rel_tol: float) -> bool:
    """按方向判定「相对基线是否变坏」（纯函数）。"""
    if base <= 0:
        return False
    if direction == "max":
        return float(now) > float(base) * (1.0 + rel_tol)
    return float(now) < float(base) * (1.0 - rel_tol)


# ---------------------------------------------------------------------------
# 阶段 258 · 红线余量健康监控（余量预警 + 漂移-硬线缓冲体检）
# ---------------------------------------------------------------------------
# 背景（docs/258）：承诺面（25 图）逐条红线算「相对余量」后发现——**mask_recall
#   红线 0.45 已达实测 0.5111 的 88%（余量仅 ~12%）**，是全部红线里最脆弱的一条，
#   却是阶段 175 起的**对外头条口径**；其余红线余量 24%–67%。
# 根因：红线 0.45 锚在阶段 183 的 **10 图**实测 0.5619×0.8，数据集后换 25 图未重锚。
#
# 本监控**不改变任何红线阈值**（阶段 258 决策 A=维持），只做两件机器可判定的事：
#   ① REDLINE_MARGIN_FLOOR：任一红线「余量」跌破地板 → 报出（余量预警）；
#   ② 校验「漂移容差 < 红线余量」：保证实测变坏时**先触发漂移告警、后撞硬红线**，
#      即双层防御有缓冲；若漂移容差 ≥ 余量，两者近乎重合，漂移层形同虚设。
#
# ⚠ 本监控是**只读体检**，绝不因「余量薄」而自动放宽红线——放宽属用户决策（258 选项 B）。

#: 各红线「可接受的最低相对余量」。余量 = 距越线还剩多少（占当前值的比例）。
#: 取值依据（docs/258 实测余量排名）：mask_recall ~12% 已是最脆弱一条；给它 10%
#:   的地板意味着「再退化近 2 个百分点就预警」，而其余红线余量 24%–67% 远高于地板，
#:   不会因数据集正常波动误报。**这是预警阈值，不是红线**——触发只报不失败。
REDLINE_MARGIN_FLOOR: dict = {
    "mask_recall": 0.10,
    "recall": 0.15,
    "precision": 0.15,
    "mean_iou": 0.15,
    "mask_iou": 0.15,
    "mask_coverage": 0.20,
    "coverage_ratio": 0.20,
}


def redline_relative_margin(direction: str, value: float, threshold: float) -> float:
    """红线「相对余量」（纯函数）：距越线还剩多少（占当前值的比例）。

    min 型（越高越好，如 recall）：余量 = (value − thr) / value。
    max 型（越高越坏，如 mask_coverage）：余量 = (thr − value) / value。
    余量 ≤ 0 表示已越线。value<=0 视作无余量信息（返回 0.0，交由上层单独判缺失）。
    """
    v = float(value)
    if v <= 0:
        return 0.0
    if direction == "max":
        return (float(threshold) - v) / v
    return (v - float(threshold)) / v


def check_redline_margin_health(preset: str, metrics: dict) -> list:
    """体检某预设各红线的「余量」与「漂移-硬线缓冲」（纯函数，返回问题列表）。

    两条判定（**只报不改**，任一命中即返回一条人读问题）：
      ① 余量跌破 `REDLINE_MARGIN_FLOOR`（预警地板）→ 「余量过薄」；
      ② 漂移容差 ≥ 该指标余量 → 「漂移告警与硬红线缓冲不足」（双层防御压扁）。
    `metrics` 需含各指标当前实测值（与 run_real_gate 的 per_preset 同源）。
    """
    problems: list = []
    contract = REAL_PRESET_CONTRACT.get(preset) or {}
    for key, (direction, threshold) in contract.items():
        value = metrics.get(key)
        if value is None:
            continue
        margin = redline_relative_margin(direction, value, threshold)
        floor = REDLINE_MARGIN_FLOOR.get(key)
        # ① 余量过薄（预警）
        if floor is not None and margin < floor:
            problems.append(
                f"[余量] {preset}/{key} 余量 {margin:.1%} < 地板 {floor:.0%}"
                f"（实测 {float(value):.4f}，红线 {direction} {threshold}）"
                f"——余量过薄，检测器小幅回退即触碰红线"
            )
        # ② 漂移-硬线缓冲（双层防御）
        rel_tol = REAL_TOLERANCES.get(key)
        if rel_tol is not None:
            # 漂移告警触发所需的相对退化量 vs 距硬红线的余量；前者必须显著小于后者。
            if rel_tol >= margin:
                problems.append(
                    f"[缓冲] {preset}/{key} 漂移容差 {rel_tol:.0%} ≥ 余量 {margin:.1%}"
                    f"（实测 {float(value):.4f}）——漂移告警与硬红线近乎重合，"
                    f"漂移层形同虚设"
                )
    return problems


# ---------------------------------------------------------------------------
# 2. 跑真实门禁（真实计算）
# ---------------------------------------------------------------------------

def _expected_labeled_count(manifest: dict, truth: dict) -> int:
    """manifest ∩ ground_truth 的图数——判定「有没有图被静默跳过」的基准。

    阶段 243：扣除**图像级已知检测缺口**（`config.WATERMARK_KNOWN_GAP_IMAGES`）。
    这些图**被有意排除在聚合/红线之外**（而非被静默跳过——见 `run_all` 的缺口段与
    docs/243），故不参与本计数；否则完整性层会把「有记录的排除」误判成「静默丢图」。
    本函数与 `run_real_benchmark.run_all` 的 `eval_images` 口径**必须一致**。
    """
    import config  # noqa: PLC0415  —— 与文件既有风格一致（函数内延迟导入）
    truth_ids = {e["image_id"] for e in truth.get("entries", [])}
    gap_ids = set(getattr(config, "WATERMARK_KNOWN_GAP_IMAGES", ()) or ())
    return len([im for im in manifest.get("images", [])
                if im.get("id") in truth_ids and im.get("id") not in gap_ids])


#: 清单里表示「未指定具体风格」的 hint 取值。这类取值**不构成对某种风格的具体声明**，
#: 因而既无法被真值证实、也无法被证伪——只能把「标注未覆盖」显式记录下来，
#: 绝不能读成「该图没有那种水印」。
_UNSPECIFIED_HINTS: frozenset = frozenset({"", "mixed", "unknown"})


def annotation_scope(manifest: dict, truth: dict) -> dict:
    """计算真值标注的**覆盖面**与**承诺面**，把「没标」「没有」「不承诺」三者分开。

    （阶段 93 建立覆盖面；阶段 95 加入承诺面。）

    为什么必须有这一层（见 `docs/92` 缺口 S1）：
        本数据集 30 张图的清单**全部**声明 `watermark_style_hint="mixed"`
        （多种风格并存），而真值 60 个标注**全部**是 `corner_logo`。
        未被标注的水印**不会计入漏检**，于是 `recall` 被结构性抬到 1.0000——
        这个 1.0 只代表 `corner_logo` 一类，另外 4 类（faint_tiled / edge_text /
        grid / gradient）的真实召回是**未知**，不是 100%。
        **「没标」被静默读成了「没有」**，这正是本函数要消灭的那种沉默。

    阶段 95 的补法（承诺面收窄，用户决定「只处理角标 LOGO 类图片」）：
        上一阶段把「补齐四类真值」与「把能力声明收敛到已验证范围」并列成两条出路。
        用户选了后者，于是 `config.WATERMARK_TYPE_SCOPE[*]["committed"]` 成为
        **承诺级别的单一真值来源**：`ai_logo` 为 True，其余四类为 False。
        语义随之变成三态：

          · **承诺类** 有真值 → 正常（`committed_labeled`）
          · **承诺类** 无真值 → **硬失败**（`committed_missing`）。
                                承诺了却没有正向证据，这是「承诺比证据多」，
                                必须补图或收回承诺，二者都可执行。
          · **非承诺类**无真值 → **不算缺口**（`uncommitted_missing`）。
                                但必须**显式记录**——留白会被读成「忘了」。
                                注意：它不是「不支持」：预设仍可选用，
                                只是效果不作承诺、且不计入本门禁的指标结论。

        ⚠ 关键区别：`unspecified_images`（清单只声明 catch-all）**不再是缺口**，
        降级为**提示精度诊断**。理由：承诺面已声明为「只做角标」，
        而真值确实覆盖了角标——此时 recall 度量的正好是被承诺的那一类，
        这与承诺面一致。清单写 `mixed` 只是说明「这 30 张图里可能还混有别的样式」，
        那些样式的召回**不在承诺范围内**，因此引用数字时带上范围说明即可，
        不必让它把门禁永久压成红灯。

    返回字段（全部可由脚本判定，不依赖人眼）：
        labeled                真值里实际出现过的 style（**只有这些的召回是被测量的**）
        n_labeled_styles       len(labeled)
        committed              承诺处理的预设名（唯一真值来源 `config`）
        uncommitted            显式声明不承诺的预设名
        committed_labeled      承诺类中已有真值的
        committed_missing      承诺类中**缺真值**的 → 门禁硬失败
        uncommitted_labeled    非承诺类中已有真值的（有证据但未承诺）
        uncommitted_missing    非承诺类中缺真值的 → 记录，不失败
        declared_images        清单给出**具体**风格声明的图数（catch-all 不计）
        unspecified_images     清单只有 catch-all 声明的图数（＝提示精度诊断）
        contradictory          声明了具体风格却在真值里 0 标注 → **数据矛盾**
        unknown_styles         真值出现枚举外的 style → **数据错误**
        complete               无矛盾、无枚举外取值、且承诺类无缺证
                               （只有此时，指标才可被当作「承诺面内」的完整结论引用）

    关于 `config` 的导入：**刻意不做 try/except 兜底**。若承诺面取不到就退回
    「无承诺面」，会让 `committed_missing` 变成空集 → 门禁对着空集合**空转报绿**
    （经验 54）。宁可让它直接抛 ImportError，也不能静默失去判定能力。
    """
    truth_styles = {w.get("style") for e in truth.get("entries", [])
                    for w in (e.get("watermarks") or [])}
    truth_styles.discard(None)

    declared = set()
    declared_images = 0
    unspecified_images = 0
    contradictory: list = []
    for im in manifest.get("images", []):
        hint = (im.get("watermark_style_hint") or "").strip()
        if hint in _UNSPECIFIED_HINTS:
            unspecified_images += 1
            continue
        declared.add(hint)
        declared_images += 1
        if hint not in truth_styles:
            contradictory.append(f"{im.get('id')}: hint={hint} 但真值中该类 0 标注")

    # ---- 承诺面（阶段 95）----
    committed = sorted(config.WATERMARK_TYPE_COMMITTED)
    uncommitted = sorted(set(config.WATERMARK_TYPE_SCOPE) - set(committed))

    # 逐类型统计真值标注条数：**按类型登记的 truth_styles 映射现算**，
    # 不信任任何写死的数字（与 tests 侧的 `_real_label_count` 同口径）。
    style_counts: dict = {}
    for e in truth.get("entries", []):
        for w in (e.get("watermarks") or []):
            s = w.get("style")
            if s is not None:
                style_counts[s] = style_counts.get(s, 0) + 1

    def _count(name: str) -> int:
        styles = set((config.WATERMARK_TYPE_SCOPE.get(name) or {}).get("truth_styles") or ())
        return sum(n for s, n in style_counts.items() if s in styles)

    committed_labeled = [n for n in committed if _count(n) > 0]
    committed_missing = [n for n in committed if _count(n) == 0]
    uncommitted_labeled = [n for n in uncommitted if _count(n) > 0]
    uncommitted_missing = [n for n in uncommitted if _count(n) == 0]

    # 风格枚举与 `build_real_ground_truth.STYLE_ENUM` **同源**（惰性导入，避免
    # scripts 之间的模块级耦合把 core.detector 一并拉进 import 期）。
    try:
        from scripts.build_real_ground_truth import STYLE_ENUM
        enum = set(STYLE_ENUM)
    except Exception:                                     # pragma: no cover - 兜底
        enum = set()
    unknown_styles = sorted(s for s in truth_styles if enum and s not in enum)

    return {
        "labeled": sorted(truth_styles),
        "n_labeled_styles": len(truth_styles),
        "committed": committed,
        "uncommitted": uncommitted,
        "committed_labeled": committed_labeled,
        "committed_missing": committed_missing,
        "uncommitted_labeled": uncommitted_labeled,
        "uncommitted_missing": uncommitted_missing,
        "declared_images": declared_images,
        "unspecified_images": unspecified_images,
        "contradictory": contradictory,
        "unknown_styles": unknown_styles,
        "complete": (not contradictory) and (not unknown_styles) and (not committed_missing),
    }


def render_scope_banner(scope: dict) -> str:
    """把标注覆盖面与承诺面渲染成**必须显眼**的横幅（阶段 93 建立，95 扩写）。

    它是「引用这些数字时的限定条件」，因此同时出现在 stdout 与 `to_markdown` 里，
    让任何把结果贴进文档的人都会连带把限定带过去。

    出现规则（**两种情形必出横幅**）：
      · 承诺面已声明 → **无条件出现**。既然承诺了「只做角标」，
        那么表格里那个 recall 就必须永久带着「仅代表角标」这句话走，
        否则把数字贴出去的人会把它读成全类型结论。
      · 承诺面未声明（旧格式 summary）→ 沿用阶段 93 规则：覆盖面不完整才出现。
        此处「未声明」按**最严理解**处理：既然没说哪些被承诺，
        就当作全部都被承诺看待（`complete=False` 时必须出现）。
    """
    if not scope:
        # 没有覆盖面信息 ≠ 覆盖面不完整：不凭空断言，也不凭空告警。
        return ""

    committed = list(scope.get("committed") or [])
    missing = list(scope.get("committed_missing") or [])
    unspecified = int(scope.get("unspecified_images") or 0)

    # 旧格式（无承诺面）时保持阶段 93 的触发规则。
    if not committed and scope.get("complete") and not unspecified:
        return ""

    labeled = "、".join(scope.get("labeled") or []) or "（无）"
    lines = [
        "",
        f"⚠ 标注覆盖面 {scope.get('n_labeled_styles', 0)} 类：{labeled}",
    ]

    def _fmt(names) -> str:
        """预设名 + 中文类型名。名字来自 `config`，本文件不另写一份。"""
        return "、".join(f"{n}（{config.watermark_type_label(n)}）" for n in names)

    if committed:
        lines.append(
            f"  · **承诺处理**：{_fmt(committed)}"
            f"——缺真值证据将直接判失败（承诺面唯一真值来源 "
            f"`config.WATERMARK_TYPE_COMMITTED`）"
        )
        if missing:
            lines.append(f"  · ✖ 承诺类型缺真值证据：{_fmt(missing)}")
        uncommitted = list(scope.get("uncommitted") or [])
        if uncommitted:
            lines.append(
                f"  · **明确不承诺**：{_fmt(uncommitted)}"
                f"——预设仍可选用，但效果不作承诺，也不参与本门禁结论"
            )
        lines.append(
            "  · 因此本次 recall/precision **仅代表上述承诺类型**；"
            "未承诺类型的真实指标是**未知**，不是 100%"
        )
        if unspecified:
            lines.append(
                f"  · 提示精度：清单中 {unspecified} 张图只声明了 catch-all 风格"
                f"（mixed/unknown）——真值未覆盖其他具体样式。"
                f"这**不构成**承诺面缺口（未承诺类型本就不计入指标），"
                f"但引用数字时须同时说明「仅上述承诺类型」"
            )
    else:
        lines.extend([
            f"  · 清单中 {unspecified} 张图只声明了 catch-all 风格"
            f"（mixed/unknown），真值未覆盖任何具体风格",
            "  · 因此本次 recall/precision **仅代表上述已标注类型**；"
            "其余宣称支持的类型的真实指标是**未知**，不是 100%",
            "  · 补齐方式：给缺的类型各补 3~5 张真值，"
            "或把能力声明收敛到已验证范围（并把该类型在 "
            "`config.WATERMARK_TYPE_SCOPE` 里登记为 `committed`）",
        ])

    if scope.get("contradictory"):
        lines.append(f"  · ✖ 数据矛盾：{'；'.join(scope['contradictory'])}")
    if scope.get("unknown_styles"):
        lines.append(f"  · ✖ 枚举外 style：{'、'.join(scope['unknown_styles'])}")
    return "\n".join(lines)


def run_real_gate(presets=DEFAULT_REAL_PRESETS,
                  area_presets=AREA_ONLY_PRESETS) -> dict:
    """跑真实图门禁，返回结构化 summary（不落盘、不做判定）。

    复用 `run_real_benchmark.run_all` 作为**唯一真值实现**——匹配规则（IoU≥0.3 贪心一对一）
    与聚合口径不在本文件重写，避免「同一语义两套实现」（经验 24/77）。
    阶段 107 起把**面积监控预设**并进同一次 `run_all`（同一批图、同一遍读盘），
    再按用途拆成两块：`per_preset`（有真值，走红线层）与 `area_only`（无真值，只量面积）。

    返回：{"config": {...}, "aggregate": {...}, "raw": <run_all 原始结果>}
    """
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)
    with open(TRUTH_PATH, encoding="utf-8") as f:
        truth = json.load(f)
    expected = _expected_labeled_count(manifest, truth)

    presets = list(presets)
    area_presets = [p for p in dict.fromkeys(area_presets) if p not in presets]

    # 阶段 170：真值存在但**为空**是合法状态（阶段 150 移除伪造标注后即如此）。
    #   此时 `run_all` 会 raise RuntimeError —— 那会让门禁以一段 traceback 崩溃，
    #   而非给出设计好的「承诺面缺真值」判定；且 0/0b 两层（数据完整性 / 承诺面）
    #   与面积监控**全部被跳过**（阶段 150 的注记声称「正确报 committed_missing」，
    #   实际并未走到那里）。此处显式识别该状态：跳过评估循环、返回空聚合，
    #   把裁决交回 `evaluate_real`（committed_missing 硬失败 + 「无预设结果」）。
    if manifest.get("images") and expected == 0:
        raw = {"per_preset_aggregate": {}, "per_image": [], "n_images": 0}
    else:
        raw = run_all(list(presets) + area_presets, manifest, truth)

    per_preset = {}
    for preset, a in raw["per_preset_aggregate"].items():
        if preset in area_presets:
            continue                      # 面积监控预设不进「有真值」表（其 recall 无意义）
        per_preset[preset] = {
            "n": a["n"],
            # 阶段 175：**头条口径 = 掩码级召回**（Σ交集像素/Σ真值像素）。区域级逐项
            # IoU 对「1 真值框 vs N 预测碎片」失真、且与真值框大小强耦合，故降为次级。
            "mask_recall": a.get("mask_recall_micro", 0.0),
            # 阶段 155：红线用 micro（按汇总 TP/FP/FN），与 docstring 的 recall 定义一致；
            # 此前用 macro（per-image 均值）会系统性高估、导致误绿。micro 缺失时退回 0.0。
            "precision": a.get("precision_micro", a["precision_mean"]),
            "recall": a.get("recall_micro", a["recall_mean"]),
            "mean_iou": a["mean_iou_mean"],
            "mask_iou": a["mask_iou_mean"],
            "mask_coverage": a.get("mask_coverage_mean", 0.0),
            "coverage_ratio": a.get("coverage_ratio_mean", 0.0),
        }

    area_only = {}
    for preset in area_presets:
        stats = _area_stats(raw.get("per_image"), preset)
        if stats is not None:
            area_only[preset] = stats

    return {
        "config": {
            "presets": presets,
            "area_presets": area_presets,
            "n_images": raw["n_images"],
            "n_expected": expected,
            "dataset_dir": os.path.basename(os.path.dirname(MANIFEST_PATH)),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "aggregate": {"per_preset": per_preset, "area_only": area_only,
                      "n_images": raw["n_images"]},
        "scope": annotation_scope(manifest, truth),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# 3. 判定（纯函数）
# ---------------------------------------------------------------------------

def evaluate_real(summary: dict, baseline: "dict | None" = None,
                  strict_scope: bool = False,
                  area_skipped: bool = False) -> "tuple[bool, list[str]]":
    """对真实门禁 summary 做三层判定，返回 (是否通过, 违规说明列表)。

    纯函数：不读文件、不跑计算，只吃数据结构——便于单测覆盖各种越线情形。

    `strict_scope`（阶段 93 新增）：把「标注覆盖面不足」从告警升级为失败。
    默认关闭的理由——覆盖面不足是**数据集**的现状而非代码缺陷，若默认失败则门禁
    会永久变红、从而失去「拦住回退」的作用；但这类不足必须**可见且可被 CI 要求**，
    所以提供显式开关，并且无论如何都会打印横幅 + 写入结果 JSON。

    ⚠ 阶段 95 起，「承诺类型缺真值证据」（`committed_missing`）**不受本开关控制**，
    始终硬失败——它的性质与「覆盖面不足」不同，是可执行项且当前天然为绿，
    详见下方 0b 段落。于是 `--strict-scope` 在本项目里第一次可以**长期为绿**：
    承诺面 = 角标一类，而角标有 60 条真值。
    """
    problems: list[str] = []
    cfg = summary.get("config", {})
    agg = summary.get("aggregate", {})
    per_preset = agg.get("per_preset", {})

    # ---- 0) 数据完整性：实际评估图数必须等于标注图数 ----
    n_now, n_exp = cfg.get("n_images"), cfg.get("n_expected")
    if n_now is None or n_exp is None:
        problems.append("[数据] summary 缺少 n_images/n_expected，无法校验数据完整性")
    elif int(n_now) != int(n_exp):
        problems.append(
            f"[数据] 实际评估 {n_now} 张 ≠ 标注 {n_exp} 张——有图被静默跳过"
            f"（读图失败会让聚合均值失真，必须修复而非放行）"
        )

    # ---- 0b) 标注覆盖面 + 承诺面（阶段 93 建立，阶段 95 扩写）----
    #   三类问题的性质不同，刻意分成三档处理：
    #     · 数据矛盾 / 枚举外 style  → **始终硬失败**（那是标注错误，与门槛无关）
    #     · **承诺类型缺真值证据**   → **始终硬失败**（阶段 95 新增）。
    #        理由：承诺面是「我们对外说会处理好哪些」的声明，缺证据即「承诺比证据多」。
    #        它与「覆盖面不足」不同——后者是数据集现状，收拾不了就只能永久红灯；
    #        前者**必然可执行**（补图，或把该类型改成 committed=False），
    #        而且今天它天然为绿（ai_logo 有 60 条标注），不会制造假红。
    #     · 清单只声明 catch-all（`unspecified_images`）→ **默认只告警**，
    #        由 --strict-scope 显式升级。注意阶段 95 之后它已**不再**被计入
    #        `complete`：承诺面声明为「只做角标」且角标有真值时，recall 度量的
    #        正好是被承诺那一类，提示精度只是需要随数字说明的限定。
    scope = summary.get("scope") or {}
    if scope.get("contradictory"):
        problems.append(f"[范围] 清单声明与真值矛盾：{'；'.join(scope['contradictory'])}")
    if scope.get("unknown_styles"):
        problems.append(f"[范围] 真值含枚举外 style：{'、'.join(scope['unknown_styles'])}")
    missing = list(scope.get("committed_missing") or [])
    if missing:
        problems.append(
            f"[范围] 承诺类型缺少真值证据：{'、'.join(missing)}——"
            f"这些类型被声明为「承诺处理」（`config.WATERMARK_TYPE_COMMITTED`），"
            f"但真实基准里没有它们的标注，指标无法代表它们。"
            f"请补 3~5 张真值图，或把它们改回 committed=False"
        )
    if strict_scope and not scope.get("complete", False):
        problems.append(
            f"[范围] 严格模式下标注覆盖面不足：仅 {scope.get('n_labeled_styles', 0)} 类"
            f"（{'、'.join(scope.get('labeled') or []) or '无'}）有真值，"
            f"{scope.get('unspecified_images', 0)} 张图只有 catch-all 声明"
        )

    if not per_preset:
        problems.append("[数据] 没有任何预设结果（presets 为空或全部评估失败）")
        return (False, problems)

    # ---- ① 绝对红线（仅对已登记契约的预设）----
    for preset, contract in REAL_PRESET_CONTRACT.items():
        m = per_preset.get(preset)
        if m is None:
            continue
        for key, (direction, threshold) in contract.items():
            value = m.get(key)
            if value is None:
                continue
            if _violate(direction, float(value), threshold):
                problems.append(
                    f"[红线] {preset}/{key}={value:.4f} 越界（要求 {direction} {threshold}）"
                )

    # ---- ② 基线漂移 ----
    if baseline:
        base_cfg = (baseline or {}).get("config", {})
        base_per = (baseline or {}).get("aggregate", {}).get("per_preset", {})
        # 预设集合与图数必须一致，否则数字不可比（与合成门禁的 size/seed 检查同源）。
        if list(cfg.get("presets", [])) != list(base_cfg.get("presets", [])):
            problems.append(
                f"[配置] 预设集合 {base_cfg.get('presets')} → {cfg.get('presets')} 不一致，"
                f"数字不可比（若有意变更请用 --update 刷新基线）"
            )
        if cfg.get("n_expected") is not None and \
                base_cfg.get("n_expected") is not None and \
                cfg["n_expected"] != base_cfg["n_expected"]:
            problems.append(
                f"[配置] 标注图数 {base_cfg['n_expected']} → {cfg['n_expected']} 变化，"
                f"基线不可比（数据集变更时请用 --update）"
            )
        for preset, m in per_preset.items():
            bm = base_per.get(preset)
            if not bm:
                continue
            for key, rel_tol in REAL_TOLERANCES.items():
                now, base = m.get(key), bm.get(key)
                if now is None or base is None or base <= 0:
                    continue
                direction = _metric_direction(key, preset)
                if _drift_worse(direction, now, base, rel_tol):
                    word = "上升" if direction == "max" else "下滑"
                    delta = abs(float(now) - float(base))
                    problems.append(
                        f"[漂移] {preset}/{key} {base:.4f} → {now:.4f} "
                        f"（{word} {100.0 * delta / base:.1f}%，容差 {100.0 * rel_tol:.0f}%）"
                    )

    # ---- ③ 未承诺预设的「面积监控」（阶段 107；不需要真值）----
    #   补的是「只有 general 有护栏」这个结构性盲区：`ai_faint_tiled` / `ai_grid` 等
    #   通路的劣化（例如把整片纹理当水印、涂满全图）此前**没有任何自动判定会失败**。
    #   判据：面积只守上界（越大越坏）；另设 15% 漂移容差盯「相对基线变胖」。
    # `area_skipped=True`（`--no-area` 提速模式）时整层不做判定——**调用方必须打印警告**，
    # 否则就成了「静默跳过」（本层存在的唯一理由就是"不被静默跳过"）。
    area = agg.get("area_only") or {}
    base_area = ((baseline or {}).get("aggregate", {}) or {}).get("area_only") or {}
    if area_skipped:
        return (len(problems) == 0), problems
    if baseline:
        if list(cfg.get("area_presets") or []) != \
                list((baseline.get("config") or {}).get("area_presets") or []):
            problems.append(
                "[配置] 面积监控预设集合与基线不一致，数字不可比"
                "（若有意变更请用 --update 刷新基线）"
            )
        if "area_only" not in ((baseline.get("aggregate") or {})):
            problems.append(
                "[配置] 基线缺少 `aggregate.area_only`（旧版基线）——"
                "面积监控无法比对，请用 `--update` 刷新一次"
            )
    for preset, contract in REAL_AREA_CONTRACT.items():
        m = area.get(preset)
        if not m:
            continue
        for key, (direction, threshold) in contract.items():
            value = m.get(key)
            if value is None:
                continue
            if _violate(direction, float(value), threshold):
                problems.append(
                    f"[面积] {preset}/{key}={value:.4f} 越界（要求 {direction} {threshold}）"
                )
    for preset, m in area.items():
        bm = base_area.get(preset)
        if not bm:
            continue
        for key in AREA_DRIFT_KEYS:
            now, base = m.get(key), bm.get(key)
            if now is None or base is None or base <= 0:
                continue
            if _drift_worse("max", now, base, AREA_DRIFT_TOLERANCE):
                problems.append(
                    f"[面积漂移] {preset}/{key} {base:.4f} → {now:.4f}"
                    f"（上升 {100.0 * (now - base) / base:.1f}%，"
                    f"容差 {100.0 * AREA_DRIFT_TOLERANCE:.0f}%）"
                )

    return (len(problems) == 0), problems


# ---------------------------------------------------------------------------
# 4. 渲染
# ---------------------------------------------------------------------------

def to_markdown(summary: dict) -> str:
    """把真实门禁结果渲染成 Markdown（可直接贴进文档）。"""
    cfg = summary["config"]
    per_preset = summary["aggregate"]["per_preset"]
    lines = [
        f"真实图门禁 · {cfg['dataset_dir']} · 标注 {cfg['n_expected']} 张 / "
        f"实评 {cfg['n_images']} 张",
        "",
        "| 预设 | n | **掩码召回(头条)** | 精确率 | 召回(区域) | 区域IoU | 掩码IoU | 掩码覆盖率 | 过检倍率 | 契约 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for preset, m in per_preset.items():
        contract = REAL_PRESET_CONTRACT.get(preset)
        tag = "红线" if contract else "监控"
        lines.append(
            f"| {preset} | {m['n']} | **{m.get('mask_recall', 0.0):.4f}** | "
            f"{m['precision']:.4f} | {m['recall']:.4f} | "
            f"{m['mean_iou']:.4f} | {m['mask_iou']:.4f} | "
            f"{m.get('mask_coverage', 0.0):.4f} | {m.get('coverage_ratio', 0.0):.2f}× | {tag} |"
        )
    # 覆盖面限定随表格一起走：任何把结果贴进文档的人都自动带上限定（阶段 93）。
    banner = render_scope_banner(summary.get("scope") or {})
    if banner:
        lines.append("")
        lines.append(banner.lstrip("\n"))
    # 阶段 243：**图像级已知检测缺口**（`config.WATERMARK_KNOWN_GAP_IMAGES`）——
    #   这些图真值完备但检测器尚未能处理（大颗多面体宝石角标），已从上方承诺面
    #   红线聚合中**显式排除**。必须原样列出（含单图指标），否则「被排除」会被读成
    #   「没标注/漏检」——留白即歧义（经验 51/232）。详见 docs/243。
    gap_rows = (summary.get("raw") or {}).get("known_gap_rows") or []
    if gap_rows:
        gp = "general" if any("general" in (r.get("results") or {}) for r in gap_rows) \
            else next(iter(gap_rows[0].get("results") or {}), "")
        lines.append("")
        lines.append(
            f"⚠ **已知检测缺口**（`config.WATERMARK_KNOWN_GAP_IMAGES`，"
            f"{len(gap_rows)} 张）：下列图真值完备，但检测器**尚未**能处理其水印子风格"
            f"（大颗多面体角标），故**不参与上表红线**；非「漏标/漏检」。"
        )
        lines.append("")
        lines.append(f"| 缺口图 | 预设 | 掩码召回 | 掩码覆盖率 | 精确率 |")
        lines.append("|---|---|---|---|---|")
        for r in gap_rows:
            g = (r.get("results") or {}).get(gp) or {}
            lines.append(
                f"| {r.get('image_id')} | {gp} | "
                f"{g.get('mask_recall', 0.0):.4f} | "
                f"{g.get('mask_coverage', 0.0):.4f} | "
                f"{g.get('precision', 0.0):.4f} |"
            )
    # 阶段 107：未承诺预设的**面积监控**表（不需要真值）。
    #   它回答的是另一类问题：「这条通路在真实照片上会不会涂满全图」——
    #   与 precision/recall 无关，故单独成表，且**不参与承诺结论**。
    area = (summary.get("aggregate") or {}).get("area_only") or {}
    if area:
        lines.append("")
        lines.append("| 预设（未承诺） | n | 掩码覆盖率均值 | P90 | 最大 | 有掩码图数 | 平均区域数 | 面积红线 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for preset, m in area.items():
            thr = (REAL_AREA_CONTRACT.get(preset) or {}).get("mask_coverage_mean")
            tag = f"≤{thr[1]}" if thr else "未标定"
            lines.append(
                f"| {preset} | {m['n']} | {m['mask_coverage_mean'] * 100:.2f}% | "
                f"{m['mask_coverage_p90'] * 100:.2f}% | {m['mask_coverage_max'] * 100:.2f}% | "
                f"{m['n_with_mask']}/{m['n']} | {m['pred_count_mean']:.1f} | {tag} |"
            )
        lines.append("")
        lines.append("> 面积监控**不引用真值**，因此不受「标注只覆盖角标」的限定："
                     "它守的是「这条通路在真实照片上不得突然变胖」，不是「认得准不准」。")
    return "\n".join(lines)


def main() -> int:
    # Windows 上 stdout 被重定向到管道时，Python 按 **ANSI 代码页**（简体中文 = GBK）编码输出；
    # 而本脚本的横幅含 `⚠`（U+26A0）与 `✅/❌`——**GBK 不可编码** ⇒ `print(to_markdown(...))`
    # 会抛 `UnicodeEncodeError`，进程退出码 1。
    # 后果很坏：**「输出内容」决定了「门禁退出码」**（CI 读到的"未通过"其实只是打印失败），
    # 而且 `--update` 的写盘发生在打印之后 ⇒ 刷新基线也会一起失败。
    # 故在此显式把 stdout/stderr 切到 UTF-8（`errors="replace"` 兜底，绝不因编码中断判定）。
    # 阶段 170：加固逻辑抽为共用件，全族脚本共用（见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()

    ap = argparse.ArgumentParser(description="真实图质量门禁（真实图门禁分册）")
    ap.add_argument("--update", action="store_true",
                    help="把本次结果写入基线快照（确认变化是有意的时候用）")
    ap.add_argument("--force-update", action="store_true",
                    help="与 --update 同用：跳过「向下刷新护栏」显式覆盖基线（阶段 146）。"
                         "仅在确认劣化是有意为之（换算法口径 / 改评测集）时使用")
    ap.add_argument("--presets", default=",".join(DEFAULT_REAL_PRESETS),
                    help=f"逗号分隔；默认 {','.join(DEFAULT_REAL_PRESETS)}")
    ap.add_argument("--baseline", default=BASELINE_PATH, help="基线快照路径")
    ap.add_argument("--strict-scope", action="store_true",
                    help="把「真值标注覆盖面不足」升级为失败（CI 要求全类验证时用）")
    ap.add_argument("--no-area", action="store_true",
                    help="跳过「未承诺预设的掩码面积」监控（提速用；门禁默认开启）")
    args = ap.parse_args()

    # 数据集缺失 → 跳过（exit 2，与「通过」区分开）。
    for p in (MANIFEST_PATH, TRUTH_PATH):
        if not os.path.exists(p):
            print(f"[跳过] 数据集缺失：{p}", file=sys.stderr)
            print("[跳过] 真实图门禁未运行（exit 2）。"
                  "需先 scripts/collect_real_assets.py scan + build_real_ground_truth.py",
                  file=sys.stderr)
            return 2

    presets = [p.strip() for p in args.presets.split(",") if p.strip()]
    area_presets = () if args.no_area else tuple(
        p for p in AREA_ONLY_PRESETS if p not in presets)

    t0 = time.time()
    summary = run_real_gate(presets=presets, area_presets=area_presets)
    elapsed = time.time() - t0

    print(to_markdown(summary))
    print(f"\n耗时 {elapsed:.1f}s（presets={presets}"
          f"{'，面积监控=' + str(list(area_presets)) if area_presets else '，面积监控已关闭'}）")

    baseline = None
    if os.path.exists(args.baseline):
        try:
            with open(args.baseline, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"[警告] 基线读取失败（{exc}），跳过漂移比对。")

    if args.update:
        # 阶段 146：刷新基线是**受护栏约束的动作**，不再是静默通道。
        #   ① **向下刷新护栏**：本次结果若把某指标合法化到更坏的值（超出漂移层
        #      容差）⇒ 拒绝写入并返回 1；逃生门是显式 `--force-update`。
        #   ② **刷新后继续走 `evaluate_real()`**（对齐 `e2e_real_probe` 的既有裁决）：
        #      否则刷新动作会绕过绝对红线与数据完整性判定 —— 用户看到
        #      「基线已刷新」+ 退出码 0，会以为一切正常。
        if not args.force_update:
            _pk = _refresh_pairs((baseline or {}).get("aggregate"),
                                 summary.get("aggregate"))
            _down = baseline_guard.check_downward_pairs(_pk, _refresh_rules_for(_pk))
            if _down:
                for _line in baseline_guard.render_refusal(
                        _down, baseline_label="真实图门禁基线"):
                    print(_line)
                return 1
        os.makedirs(os.path.dirname(args.baseline), exist_ok=True)
        # 基线与 synthetic 侧同构：只存 config + aggregate（raw 逐图明细过大且非判定所需）。
        # 阶段 93：一并存 `scope`——基线里若不带标注覆盖面，日后看到 recall=1.0
        # 的人就无从知道它只代表一类（限定必须跟着数字走）。
        snapshot = {"config": summary["config"], "aggregate": summary["aggregate"],
                    "scope": summary.get("scope")}
        with open(args.baseline, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        print(f"\n基线已刷新 → {display_path(args.baseline, _ROOT)}")
        print("（刷新后继续按红线、数据完整性与漂移判定：刷新动作不得成为绕过判定的通道）")
        # 用**刚写入的**基线做漂移比对（差值恒为 0），与 e2e 同口径 ——
        # 本次运行据此仍回答「新基线是否满足绝对红线与数据完整性」。
        baseline = snapshot

    ok, problems = evaluate_real(summary, baseline, strict_scope=args.strict_scope,
                                 area_skipped=not area_presets)

    # ---- 阶段 258 · 红线余量健康体检（**advisory：只打印，永不影响 ok/退出码**）----
    #   这层刻意**不进 problems**：若把「余量薄」当失败，mask_recall（余量 12%，
    #   地板 10%）会让门禁**长期红灯**，从而失去「拦住回退」的作用（经验 47）。
    #   余量过薄只作**预警**提示，真正的失败仍由绝对红线 + 漂移层负责。
    _margin_notes: list = []
    for _preset, _contract in REAL_PRESET_CONTRACT.items():
        _m = (summary.get("aggregate", {}).get("per_preset", {}) or {}).get(_preset)
        if _m:
            _margin_notes.extend(check_redline_margin_health(_preset, _m))
    if _margin_notes:
        print("\n[余量体检] 以下为**预警**（不计入门禁判定/退出码，见 docs/258）：")
        for _n in _margin_notes:
            print(f"  · {_n}")
    else:
        print("\n[余量体检] 各红线余量均在可接受范围（预警地板见 REDLINE_MARGIN_FLOOR）。")
    if not area_presets:
        print("\n[警告] 已跳过「未承诺预设的掩码面积」监控（`--no-area`）——"
              "本次退出码**不覆盖**该层（平铺增强 / 网格两条通路在真实图上的劣化不会被发现）。")
    if baseline is None:
        print("\n[提示] 未找到基线快照，本次只做绝对红线与数据完整性判定；"
              f"跑 `python scripts/real_gate.py --update` 建立基线。")

    if ok:
        print("\n✅ 真实图质量门禁通过")
        return 0

    print("\n❌ 真实图质量门禁未通过：")
    for p in problems:
        print(f"  · {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
