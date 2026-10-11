#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : scripts/audit_selector.py
 文件说明 : 「审计覆盖度荒漠」选点指标（阶段 94 固化为可复现工具）。

 指标
 --------------------------------------------------------------------------
   选点比值 = 模块【代码行】 ÷ 【引用该模块的测试文件中的 `def test_` 总数】

   比值越大 = 测试越薄 = 越该优先审计。分母用「整个测试文件的用例数」而不是
   「touch 该模块的用例数」，因为审计一个模块要读的是该文件里**全部**相关用例。

 为什么必须固化成脚本（阶段 93 复核的教训，经验 5 / 52）
 --------------------------------------------------------------------------
   该排名此前靠**临时探针手算**，于是同一条目在不同阶段被写下互相矛盾的值：
   `auto_pipeline` 记为 `575/10 = 57.5`（"第三荒漠"），2026-09-17 重算实为
   `389/50 = 7.8`（**中游**）——分母差 5 倍，**决策被引向了错误目标**。
   ⇒ 凡用于决策的排序，必须有 ①可运行脚本 ②口径自检断言。

 口径（两个陷阱都在此写死）
 --------------------------------------------------------------------------
   分子：**代码行**——剔除空行、`#` 注释、三引号块（含 docstring / 纯分隔行）。
   分母：**四写法**导入匹配。只用第一种会「**别名导入严重低估**」：
         ① `from layer.name import …`      ② `import layer.name`
         ③ `from layer import …,name…`     ④ **引号内**的字面量 `"layer.name…"`
            （覆盖 monkeypatch.setattr / patch 目标字符串等运行时引用；
             刻意要求带引号，以免把"注释里提到模块名"算成引用）
         顶层模块（config / server / app / cli / api…）无包前缀，按模块名匹配。

 自检（防口径退化）
 --------------------------------------------------------------------------
   `KNOWN_AUDITED` 是阶段 88–91 已审过的模块，它们**必有**测试引用。
   只要有一个变成 0 引用，就说明口径又退化了 ⇒ **非零退出**。
   这条断言不是装饰：阶段 94 的第一版探针正是把它写丢，于是把
   `utils/logger`、`ui/theme`（阶段 90/91 审过）算成了 0 引用。

 观察者效应（META_TEST_EXCLUDES）
 --------------------------------------------------------------------------
   测试**本量具**的文件不计入分母。成因是实测踩到的：`tests/test_audit_selector.py`
   里断言了「`ui/tab_models` 是第一荒漠」，于是它自己被匹配器当成对 `ui/tab_models`
   的引用，把该模块比值从 65.5 稀释到 11.4 —— **量具污染了被测对象**。

 用 法
 --------------------------------------------------------------------------
   python scripts/audit_selector.py                       # 荒漠度排序（前 25）
   python scripts/audit_selector.py --top 40
   python scripts/audit_selector.py --unaudited            # **决策用**：只列未审模块
   python scripts/audit_selector.py --module ui/tab_models # 看该模块命中的测试文件 + 已审状态
   python scripts/audit_selector.py --md                  # 输出可粘贴的 Markdown 表
   python scripts/audit_selector.py --json out.json        # 落盘机读结果
   python scripts/audit_selector.py --check                # 只跑口径自检

 已审登记（阶段 109 新增 AUDITED_SINCE）
 --------------------------------------------------------------------------
   榜单只答「哪里测试薄」，**不答「下一步该审哪里」**——后者还要减掉已审过的模块。
   这个减法此前靠**记忆手工维护**，阶段 109 第一脚就踩空：把 `services/queue_worker`
   （阶段 88 就审过、就在 `KNOWN_AUDITED` 里）当成了"未审计下一站"，
   两份记录自相矛盾。⇒ 决策用的登记必须与量具同源，并由脚本消费（`--unaudited`）。
   自检新增两条：**两份登记必须同源**（KNOWN_AUDITED ⊆ AUDITED_SINCE）、
   **登记里的模块必须真的被扫到**（改名/删除后不同步会立刻报错）。

 退出码
 --------------------------------------------------------------------------
   0 = 正常 / 自检通过
   1 = 自检失败（口径退化）或 --module 查无此模块
   2 = 项目结构异常（找不到 tests/ 或源码层目录）

 依赖：仅标准库（零 gradio / 零 numpy / 零 cv2），可完全离线运行。
==============================================================================
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 关注的源码层（ui → services → core → utils 是本项目的分层顺序）
_LAYERS = ("ui", "services", "core", "utils")

#: 顶层模块（无包前缀）纳入排名的代码行下限——避免把 setup.py 之类小文件算进来
_TOP_MIN_CODE = 300

#: 阶段 88–91 已审计过的模块：(层, 模块名, 首次审计阶段)。**必有测试引用**。
#: 这是口径退化的看门狗，不是"哪些模块重要"的清单。
KNOWN_AUDITED: tuple = (
    ("services", "queue_worker", 88),
    ("core", "image_utils", 89),
    ("core", "model_manager", 89),
    ("utils", "logger", 90),
    ("ui", "theme", 91),
    ("ui", "app", 91),
)

#: **测量装置自身**：这些测试文件不纳入分母。
#:
#: 阶段 94 亲历的"观察者效应"：写完 `tests/test_audit_selector.py`（其中断言
#: `ui/tab_models` 是第一荒漠）之后，该文件**立刻**被自己的匹配器算成对
#: `ui/tab_models` 的"引用"，把它的比值从 **65.5 稀释到 11.4** —— 一个模块
#: 会因为"有人测了量具"而显得被测过了。
#: ⇒ 凡测试**量具本身**的文件，必须显式登记于此（不做隐式名字猜测）。
META_TEST_EXCLUDES: frozenset = frozenset({"test_audit_selector.py"})


#: **全量已审登记**：`{模块 key: 首次审计阶段}`（阶段 109 新增）。
#:
#: 为什么必须进脚本（阶段 109 亲历的错误）：`KNOWN_AUDITED` 只登记 88–91，
#: 于是「下一站是哪」这件事长期靠**记忆手工维护**，结果 109 一开始把
#: `services/queue_worker`（**阶段 88 就审过**，就在本表里）当成了"未审计下一站"——
#: 与 `KNOWN_AUDITED` 直接自相矛盾。⇒ 决策用的登记必须与量具放一起、且由脚本消费
#: （`--unaudited`），否则它迟早又变成"手工维持的结论"（经验 5/52）。
#:
#: 每条的来源（docs/ 里对应的阶段文档，**逐条可查**）：
#:   88 services 层续审（报告汇总口径分裂）→ queue_worker（278 续审：钉死首轮遗漏的成功/边界分支字段不变量，14 例守卫，0 真缺陷）/ report_service（279 续审：钉死首轮遗漏的成功/边界分支不变量——build_entries 字段默认值·status 过滤边界·ts 格式·generate_html 空/质量/推荐分支·export_report fmt 归一与 .pdf→.html 改写，18 例守卫，0 真缺陷）
#:   89 core 设备与模型管理真值审计        → device_manager / model_manager / image_utils（285 续审：**修复 3 处真缺陷**——pad_to_multiple 取模除零 ZeroDivisionError·split_into_tiles 的 overlap>=tile_size 静默退化为逐像素切块(600×600 从 9 块爆到 119025 块)·tensor_to_mask 缺阶段 207 的 NaN 净化；另钉 48 例守卫覆盖掩码后处理/补边往返/分块几何/拼接往返/权重窗/张量转换）（286 续审 model_manager：**0 真缺陷**——钉死首轮遗漏的 sha256 不匹配/不可读两条分支 reason 文案·体积 0.9× 边界·set_active_model 不存在槽→False/清空缓存副作用/persist 写状态文件/persist 写失败仍返回 True·register 默认名与覆盖·_sha256_of_file 缺路径抛异常·rollback copy2 抛→False·blank_verify_result 键集一致·slot_detail sha 计算，18 例守卫）（287 续审 device_manager：**0 真缺陷**——钉死首轮遗漏的缓存复用语义(auto 复用旧决策/模式变化才重探/同模式返回同对象)·select_device(None) 走 config 默认·_detect_cuda 的 torch 缺失/get_device_properties 异常两分支→None·_query_free_vram_mb 的 NVML try/finally(成功/mem 失败/init 失败均 None 且 shutdown 必调用)·apply_cpu_threads 的 n=None(默认0=no-op)/显式 n=2(写 torch 线程+OMP/MKL env)·empty_cache 节流(二次立即调用跳过/force 绕过/配置关闭不调用)·use_fp16 在 config 关闭时返回 False·_DeviceHandle.torch_device 的 cpu/cuda 身份+is_gpu 委托，16 例守卫）
#:   90 utils 日志系统真值审计             → logger（289 续审：钉死首轮遗漏的 _ColorFormatter 不着色两分支(use_color=False/_HAS_COLOR=False)·print_banner 三行包裹且不写日志文件·_ensure_global_config 幂等·_ensure_shared_handlers 复用同一批对象(防 L1 回归)+error handler 恒 ERROR·get_logger 默认名 WME·_resolve_level 非字符串入参安全兜底，7 例守卫，0 真缺陷）
#:   91 ui 层首批真值审计                  → theme / app（288 续审 theme：钉死首轮遗漏的 build_app_theme 双层 try/except 兜底(.set 失败/连 Soft() 都失败→'soft' 字符串)·builtin_theme_names 注册表不可读返回 ()·resolve_theme_name 在 names=() 与空串/纯空白/None 下回退 brand·build_app_css 占位符全替换/调用时读品牌令牌，9 例守卫，0 真缺陷｜**290 续审 app（本链收官站）：0 真缺陷**——钉死首轮遗漏的 _find_free_port 五条语义(通配主机回环探测/空闲/全占用返None/跳位/max_try<=0 退化)·launch_app 全部返回码分支(成功=0/自动端口失败=1 且不启动/OSError=1/Exception=1/Ctrl+C=0/用探测端口)·footer_links 受 SERVER_SHOW_API 控制·**288「soft」兜底字符串经 launch 仍须合法(288×290 跨模块契约，补丁打真实调用点 app_mod.build_app_theme 并双条件防假绿)**·**超分控件注册表不线性累积(阶段 96 加的 clear_superres_widgets 与 U3 同源却零守卫，本轮补 4 例 + 注入 I1)**·overlay CSS 不含单引号/换行(否则注入 JS 浏览器端静默失效)·config 开关真翻转 DRAG/SHORTCUT_ENABLED + elem_id 对账·_on_lang 返回 len(reg)+1 与未知语言降级，36 例守卫，注入反向验证 4/4 捕获)
#:   94 审计选点指标固化与 tab_models 审计（277 续审：钉死首轮遗漏的成功/边界分支路由，33 例守卫，0 真缺陷）→ tab_models
#:   96 ui 层续审（审计 tab_auto）         → tab_auto   （276 续审：钉死首轮遗漏的回调错误路由/工具按钮/历史登记/真超分对比拼接，22 例守卫，0 真缺陷）
#:   97 ui 层续审（审计 tab_batch）        → tab_batch（281 续审：**修复 1 真缺陷**——画廊点选空序列索引 `()`/`[]` 未规整致 `int(())` 抛 TypeError，同根因 3 处一并修；另钉 export_report_from_batch 四分支 + run_batch `_on_update` DONE/FAILED/CANCELLED 子分支，24 例守卫）
#:   98 utils 层续审（审计 recommend_view）→ recommend_view（280 续审：钉死首轮遗漏的边界不变量——_char_bar 钳制两端·bars_html 宽度封顶 100% 防溢出·未知键忽略·仅正分值按序渲染·reason_line/markdown 空态文案·to_dict 抛错防御·三渲染器无效输入返回差异，16 例守卫，0 真缺陷）
#:   100 core-detector 独立审计（角标 MSER）→ detector（**仅角标通路**，见下注）
#:   109 core 层续审（审计 preset_recommender）→ preset_recommender（284 续审：钉死首轮遗漏的决策边界——_ensure_rgb 输入矩阵·_dominant_period 五种退化输入·_marks_at 兼容路径(residual=None)/全NaN/常量MAD=0·_spectral_regularity 零掩码·_gradient_features 均匀图·极小图不崩·confidence==max(scores)·to_dict 深拷贝·兜底 translucent 分支，31 例守卫，0 真缺陷）
#:   110 ui 层续审（审计 tab_single：控件与形参错位 + 参数摘要 i18n）→ tab_single（283 续审：钉死首轮遗漏的九条返回分支返回值不变量——_extract_image 形态矩阵·失败分支保留原图不返回结果·save_fail 成功分支仍回传结果图·UI_COMPARE_VIEW 开关·record 开关与缓存容错·超分放大提示；另修声明漂移(docstring 引用已合并掉的测试文件)，39 例守卫，0 真缺陷）
#:   111 core 层续审（审计 mask_editor：掩码代数正确性 + 未测不变量钉死）→ mask_editor（282 续审：钉死首轮遗漏边界——非 2D 输入 ValueError·浮点半径银行家舍入·apply_rect 浮点坐标截断/负坐标夹取·target_shape 非正维度忽略·mode 缺省与空白回退·逐维取 max 尺寸·画笔圆心贴边·_as_binary 通道分支(1/2 亮度 vs 4 alpha)·布尔/负值掩码，37 例守卫，0 真缺陷）
#:   112 ui 层续审（审计 tab_media：接线契约全绿仅修文档元组误写）→ tab_media
#:   113 services 层续审（审计 video_service：extract_sampled_frames return_indices=False 空操作→剥离 idx）→ video_service
#:   114 入口层续审（审计 cli：--superres 对 PDF 静默失效 + _run_one 展示键自保证）→ cli
#:   115 core 层续审（审计 superres：无权重 esrgan 随机卷积噪声→改纯双三次 + scale 缓存键控）→ core/superres
#:   122 services 层续审（审计 postprocess_service：自检在装 torch/用户开真超分的环境必崩→环境解耦）→ services/postprocess_service
#:   123 core 层续审（审计 mask_generator：dict 图层循环 (H,W,1) 崩溃 + 无 background 静默丢掩码→统一灰度化/尺寸兜底）→ core/mask_generator
#:   124 services 层续审（审计 compare_service：summarize_pair 用「raw结果!=对齐尺寸」判 upscaled，结果更小时误报 True→改比原始尺寸）→ services/compare_service
#:
#: ⚠ **`core/detector` 审计状态（阶段 167 复核更正）**：旧注记称「非角标通路仍未逐条审」已过时。
#:   角标/MSER 确系阶段 100 逐条审；其余子通路后续各有专门套件——网格 `detect_grid_watermark`
#:   （49/53/106，阶段 167 再补契约/不变量守卫）、边缘文字 `detect_edge_text`（52）、半透明
#:   `detect_translucent` / 平铺 `detect_tiled_translucent`（54/82/84/104）。若它日后登上未审榜首，
#:   直接读既有测试即可（阶段 167 已确认各子通路 by design 正确，无需从零审计）。
AUDITED_SINCE: dict = {
    "services/queue_worker": 88,
    "services/report_service": 88,
    "core/device_manager": 89,
    "core/image_utils": 89,
    "core/model_manager": 89,
    "utils/logger": 90,
    "ui/theme": 91,
    "ui/app": 91,
    "ui/tab_models": 94,
    "ui/tab_auto": 96,
    "ui/tab_batch": 97,
    "utils/recommend_view": 98,
    "core/detector": 100,
    "core/preset_recommender": 109,
    "ui/tab_single": 110,
    "core/mask_editor": 111,
    "ui/tab_media": 112,
    "services/video_service": 113,
    "cli": 114,
    "core/superres": 115,
    "services/postprocess_service": 122,
    "core/mask_generator": 123,
    "services/compare_service": 124,
    "api": 125,
    "ui/compare_view": 126,
    "services/auto_pipeline": 127,
    "core/plugins": 128,
    "ui/tab_settings": 129,
    "services/validators": 130,
    "services/pdf_service": 131,
    "services/cache_service": 132,
    "utils/exceptions": 133,
    "services/export_service": 134,
    "services/file_service": 135,
    "services/video_export": 136,
    "utils/i18n": 137,
    "ui/components": 138,
    "ui/tab_history": 139,
    "services/quality_service": 140,
    "utils/brand": 141,
    "core/inpainter": 142,
    "core/detection_presets": 143,
    "utils/path_helper": 144,
    "config": 145,
    # ⚠ 阶段 209 补登：server 在阶段 208 已审计（start_server 签名缺参 UnboundLocalError
    #   修复 + tests/test_server_start.py 4 例回归），但当时漏登 AUDITED_SINCE——导致
    #   `--unaudited` 长期把 server 误列为「下一站」。补登以闭合全 45 模块审计环。
    #   自检（KNOWN_AUDITED ⊆ AUDITED_SINCE / 登记键必须真被扫到）不拦此举，因 server
    #   确系顶层模块且 71 个引用用例已覆盖。
    "server": 208,
}

#: 已审但**只覆盖部分子通路**的模块及其仍未审的部分（供 `--module` / 决策时参考）。
#:
#: ⚠ **`core/detector` 审计状态更正（阶段 167 复核）**：旧注记称「非角标通路从未逐条审」已过时。
#:   角标/MSER 通路确系阶段 100 逐条审；但其余子通路后续各有专门套件：
#:     · 网格 `detect_grid_watermark`：test_detector_v2 / test_detector_overdetection /
#:       test_grid_precision / test_grid_resolution / test_tiled_path_guard（阶段 49/53/106）
#:     · 边缘文字 `detect_edge_text`：test_edge_text_recall / test_detector_box_refine /
#:       test_preset_recommend_accuracy（阶段 52）
#:     · 半透明 `detect_translucent` / 平铺 `detect_tiled_translucent`：
#:       test_tiled_path_guard / test_tiled_precision / test_texture_guard（阶段 54/82/84/104）
#:   ⇒ detector 实际已被逐子通路审计，`--unaudited` 亦已将其排除；复查应直接读既有测试，
#:     本注记仅留作历史口径说明，不再建议「补审」。
AUDIT_SCOPE_NOTES: dict = {
    "core/detector": "阶段 100 审角标/MSER 通路；网格(49/53/106)、边缘文字(52)、"
                     "半透明/平铺(54/82/84/104) 后续各有专门测试套件（阶段 167 复核确认，"
                     "旧『非角标未审』注记已过时）",
}


@dataclass
class Target:
    """一个被评估的模块（或顶层文件）。"""

    key: str                 # "ui/tab_models"；顶层为 "config"
    layer: str               # "ui" / "services" / ...；顶层为 ""
    name: str                # "tab_models"
    path: str
    code: int = 0
    hit_files: list = field(default_factory=list)
    hit_tests: int = 0

    @property
    def ratio(self) -> float:
        """选点比值；无任何引用时为 inf（最荒漠）。"""
        return self.code / self.hit_tests if self.hit_tests else float("inf")


# ------------------------------------------------------------------------------
# 分子：代码行
# ------------------------------------------------------------------------------
def code_lines(path: str) -> int:
    """统计**代码行**：剔除空行、`#` 注释、三引号块内部与文档串起始行。

    判定规则（对每行）：
      · 处于三引号块内 → 只有"关闭行"能退出该状态，该行一律不计；
      · 首个三引号之前的前缀若为空或纯 `#` 注释 → 视为文档串起始行，**不计**；
      · 前缀有真实代码（如 `x = \"\"\"…`）→ 计为代码行；
      · 其余非空、非 `#` 行 → 计。

    实现刻意"粗糙但确定"：三引号只做奇偶配对，不解析嵌套。
    排序用途不需要精确到行——**需要的是同一个口径**（经验 52）。
    """
    n = 0
    in_doc = False
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            s = raw.strip()
            if in_doc:
                if '"""' in s or "'''" in s:
                    in_doc = False
                continue
            if not s or s.startswith("#"):
                continue

            q_pos = len(s)
            for delim in ('"""', "'''"):
                p = s.find(delim)
                if p >= 0:
                    q_pos = min(q_pos, p)
            odd = (s.count('"""') % 2 == 1) or (s.count("'''") % 2 == 1)
            prefix = s[:q_pos].strip()

            if odd:
                in_doc = True
                if prefix and not prefix.startswith("#"):
                    n += 1
                continue
            n += 1
    return n


# ------------------------------------------------------------------------------
# 分母：引用匹配（四写法）
# ------------------------------------------------------------------------------
def _test_files(tests_dir: str, excludes=None) -> list:
    """返回 [(文件名, 源码, `def test_` 个数)]，已剔除测量装置自身。

    `excludes` 默认取 `META_TEST_EXCLUDES`；显式传参便于测试在**不改全局状态**
    的前提下验证剔除行为。
    """
    ex = META_TEST_EXCLUDES if excludes is None else frozenset(excludes)
    out = []
    for fn in sorted(os.listdir(tests_dir)):
        if not (fn.startswith("test_") and fn.endswith(".py")):
            continue
        if fn in ex:
            continue
        p = os.path.join(tests_dir, fn)
        src = io.open(p, encoding="utf-8", errors="replace").read()
        out.append((fn, src, len(re.findall(r"^\s*def test_", src, re.M))))
    return out


def match_refs(tgt: Target, tfiles: list) -> None:
    """就地填充 `tgt.hit_files` / `tgt.hit_tests`。

    精确到四种写法（比"搜子串"严，避免注释里提到模块名造成假阳性）：
      ① `from layer.name import …`
      ② `import layer.name`
      ③ `from layer import …,name…`
      ④ **引号内**的字面量 `"layer.name…"`（monkeypatch.setattr / patch 目标）
    顶层模块无包前缀：① `from name import`、② `import name`、④ `"name.…"`。
    """
    name = re.escape(tgt.name)
    if tgt.layer:
        lay = re.escape(tgt.layer)
        pat_import = re.compile(
            r"^\s*(?:from\s+" + lay + r"\." + name + r"\b"
            r"|import\s+" + lay + r"\." + name + r"\b)", re.M)
        pat_alias = re.compile(r"^\s*from\s+" + lay + r"\s+import\s+([^\n#]*)", re.M)
        pat_literal = re.compile(r"[\"']" + lay + r"\." + name + r"[\.\"']")
    else:
        pat_import = re.compile(
            r"^\s*(?:from\s+" + name + r"\b|import\s+" + name + r"\b)", re.M)
        pat_alias = None
        pat_literal = re.compile(r"[\"']" + name + r"\.")

    name_re = re.compile(r"\b" + name + r"\b")
    for fn, src, nt in tfiles:
        hit = bool(pat_import.search(src)) or bool(pat_literal.search(src))
        if not hit and pat_alias is not None:
            for m in pat_alias.finditer(src):
                if name_re.search(m.group(1)):
                    hit = True
                    break
        if hit:
            tgt.hit_files.append(fn)
            tgt.hit_tests += nt


# ------------------------------------------------------------------------------
# 采集与排序
# ------------------------------------------------------------------------------
def collect(root: str = _ROOT) -> list:
    """扫描源码层，返回按荒漠度降序的 Target 列表。"""
    tests_dir = os.path.join(root, "tests")
    if not os.path.isdir(tests_dir):
        raise SystemExit(2)
    tfiles = _test_files(tests_dir)

    targets = []
    for layer in _LAYERS:
        d = os.path.join(root, layer)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(d, fn)
            cl = code_lines(p)
            if cl >= 80:
                targets.append(Target(f"{layer}/{fn[:-3]}", layer, fn[:-3], p, cl))
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".py"):
            continue
        p = os.path.join(root, fn)
        cl = code_lines(p)
        if cl >= _TOP_MIN_CODE:
            targets.append(Target(fn[:-3], "", fn[:-3], p, cl))

    for t in targets:
        match_refs(t, tfiles)
    targets.sort(key=lambda t: (-t.ratio, t.key))
    return targets


def self_check(targets: list) -> list:
    """返回口径退化的问题列表（空 = 通过）。"""
    problems = []
    index = {t.key: t for t in targets}
    for layer, name, stage in KNOWN_AUDITED:
        key = f"{layer}/{name}"
        t = index.get(key)
        if t is None:
            problems.append(f"[口径] 已审模块 {key} 不在扫描结果内（改过名或阈值？阶段 {stage}）")
            continue
        if t.hit_tests <= 0:
            problems.append(
                f"[口径] 已审模块 {key}（阶段 {stage}）引用数 = 0 —— "
                f"导入匹配退化了（别名/字符串写法漏匹配）"
            )
    # 阶段 109：全量已审登记的一致性（两条都拦"登记烂掉"的常见形态）
    known_keys = {f"{layer}/{name}" for layer, name, _ in KNOWN_AUDITED}
    missing = known_keys - set(AUDITED_SINCE)
    if missing:
        problems.append(
            f"[登记] KNOWN_AUDITED 里有 {sorted(missing)} 未进 AUDITED_SINCE —— "
            f"两份登记必须同源，否则「哪还没审」会算错"
        )
    for key in AUDITED_SINCE:
        if key not in index:
            problems.append(
                f"[登记] AUDITED_SINCE 里的 {key} 不在扫描结果内 —— "
                f"模块改名/删除后必须同步本表，否则未审榜会漏掉真实模块"
            )
    return problems


def unaudited(targets: list) -> list:
    """**决策用**：过滤掉已审模块后按 ratio 降序（`--unaudited` 消费）。

    与 `collect()` 的差别只有一条：剔除 `AUDITED_SINCE`。但这一条是"选点"
    与"看榜单"的分界——榜单只答"哪里测试薄"，本函数答"**下一步该审哪里**"。
    """
    return [t for t in targets if t.key not in AUDITED_SINCE]


# ------------------------------------------------------------------------------
# 输出
# ------------------------------------------------------------------------------
def render_table(targets: list, top: int) -> str:
    lines = [
        f"{'ratio':>8} | {'module':<30} | {'code':>5} | {'tests':>6} | {'files':>5}",
        "-" * 72,
    ]
    for t in targets[:top]:
        r = f"{t.ratio:8.1f}" if t.hit_tests else "     inf"
        lines.append(
            f"{r} | {t.key:<30} | {t.code:5d} | {t.hit_tests:6d} | {len(t.hit_files):5d}"
        )
    return "\n".join(lines)


def render_md(targets: list, top: int) -> str:
    lines = ["| 选点比值 | 模块 | 代码行 | 引用用例 | 引用文件 |", "|---:|---|---:|---:|---:|"]
    for t in targets[:top]:
        r = f"{t.ratio:.1f}" if t.hit_tests else "∞"
        lines.append(f"| {r} | `{t.key}` | {t.code} | {t.hit_tests} | {len(t.hit_files)} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    # 阶段 170：输出编码加固（详见 scripts/console_encoding.py）。
    from scripts.console_encoding import force_utf8_stdio
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="审计覆盖度荒漠选点指标（阶段 94）")
    ap.add_argument("--root", default=_ROOT, help="项目根目录")
    ap.add_argument("--top", type=int, default=25, help="只显示前 N 个（默认 25）")
    ap.add_argument("--module", default=None, help="查看某模块命中的测试文件，如 ui/tab_models")
    ap.add_argument("--md", action="store_true", help="输出 Markdown 表")
    ap.add_argument("--json", default=None, help="把完整结果写入 JSON 文件")
    ap.add_argument("--check", action="store_true", help="只跑口径自检")
    ap.add_argument("--unaudited", action="store_true",
                    help="只列**未审计**模块的荒漠度（决策用；剔除 AUDITED_SINCE）")
    args = ap.parse_args(argv)

    targets = collect(args.root)
    problems = self_check(targets)

    if args.check:
        if problems:
            for p in problems:
                print("✗", p)
            print("✗ 选点口径自检未通过")
            return 1
        print("✓ 选点口径自检通过（已审模块均有引用；两份已审登记同源）")
        return 0

    if args.module:
        hit = [t for t in targets if t.key == args.module]
        if not hit:
            print(f"✗ 未找到模块：{args.module}")
            return 1
        t = hit[0]
        stage = AUDITED_SINCE.get(t.key)
        print(f"[{t.key}]  代码行={t.code}  引用用例={t.hit_tests}  "
              f"引用文件={len(t.hit_files)}  ratio="
              f"{'inf' if not t.hit_tests else round(t.ratio, 1)}")
        print(f"   已审：{'阶段 ' + str(stage) if stage else '**未审**'}")
        if t.key in AUDIT_SCOPE_NOTES:
            print(f"   ⚠ 覆盖范围：{AUDIT_SCOPE_NOTES[t.key]}")
        for fn in t.hit_files:
            print("   -", fn)
        return 0

    listed = unaudited(targets) if args.unaudited else targets
    print(render_md(listed, args.top) if args.md else render_table(listed, args.top))
    if args.unaudited:
        print(f"\n（已按 AUDITED_SINCE 剔除 {len(targets) - len(listed)} 个已审模块；"
              f"登记共 {len(AUDITED_SINCE)} 条）")

    if args.json:
        with io.open(args.json, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "metric": "code_lines / def_test_count_in_referencing_files",
                    "known_audited": [list(k) for k in KNOWN_AUDITED],
                    "audited_since": AUDITED_SINCE,
                    "audit_scope_notes": AUDIT_SCOPE_NOTES,
                    "unaudited_rank": [t.key for t in unaudited(targets)],
                    "targets": [
                        {"key": t.key, "code": t.code, "hit_tests": t.hit_tests,
                         "hit_files": t.hit_files,
                         "audited_stage": AUDITED_SINCE.get(t.key),
                         "ratio": (None if not t.hit_tests else round(t.ratio, 4))}
                        for t in targets
                    ],
                },
                fh, ensure_ascii=False, indent=2,
            )
        print(f"\n[已写出] {args.json}")

    if problems:
        print("\n口径自检：")
        for p in problems:
            print("  ✗", p)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
