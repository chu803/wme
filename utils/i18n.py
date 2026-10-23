# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/i18n.py
 文件说明 : 国际化（i18n）基础模块（阶段 19 核心，工具层）。

            提供：
            1. t(key, **kwargs)        : 取当前语言的译文；点号分层键（app.tab.single）；
                                        缺失键回退到 zh-CN，再回退为 key 本身，
                                        保证「漏翻」也绝不会让界面崩溃。
            1.1 t_in(lang, key, **kwargs) : 取**指定语言**的译文（不改当前语言状态），
                                        与 t() 共用同一实现与守卫。需要「同一时刻取
                                        两种语言的同一键」时必须用它，不得直接下标
                                        TRANSLATIONS（会绕过守卫、构建期抛 KeyError）。
            2. set_language / get_language / available_languages
            3. load_language()         : 按「环境变量 > 持久化文件 > config」决定语言
            4. save_language(code)     : 把语言选择持久化到缓存目录 JSON

            设计约束：
            · 本模块属于「工具层」，严禁 import gradio / ui / services。
            · 语言包数据全部静态内置于本文件（不依赖外部资源文件），离线可用。
            · 实时刷新组件所需的 gr.update 逻辑在 ui 层（ui/i18n_rt.py），
              本模块只负责「文本 <-> 语言」的纯映射。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import json
import os as _os
import re as _re
import sys as _sys

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import config
from utils.logger import get_logger

log = get_logger("utils.i18n")

DEFAULT_LANG = "zh-CN"

# 语言包：键名统一为「层级.子层级.字段」，中英文一一对应。
# 注意：含 {占位符} 的译文必须保证中英文占位符同名，t() 才不致 KeyError。
TRANSLATIONS: dict = {
    "zh-CN": {
        "common": {
            "on": "开启",
            "off": "关闭",
            "restart_to_apply": "（修改后需重启程序方可生效）",
            "unknown_title": "（无标题）",
        },
        "app": {
            "compliance": (
                "> ⚠️ **合规提醒**：本工具专用于去除**您本人生成的 AI 图片**上的可见水印 / 署名标识"
                "（如您自行添加的水印）。请勿用于清除他人作品或 AI 平台第三方署名水印"
                "（通常违反其服务条款）。\n"
                "> 详见控制台启动时的《合规使用声明》。"
            ),
            "tab": {
                "single": "单图处理（手动涂抹）",
                "auto": "智能自动检测",
                "batch": "批量处理队列",
                "media": "文档与视频",
                "history": "历史记录",
                "settings": "设置与导出",
                "models": "模型管理",
            },
        },
        "detect": {
            "preset": {
                "general": {
                    "title": "通用检测",
                    "desc": "综合半透明 / 角落 / 边缘三类策略，适用于大多数水印场景。",
                },
                "ai_tiled": {
                    "title": "AI 平铺水印",
                    "desc": "针对 AI 生成图常见的半透明平铺 logo：开启平铺增强，提升低透明度水印召回。",
                },
                "ai_signature": {
                    "title": "AI 署名栏",
                    "desc": "针对 AI 平台底部居中 / 边角的署名文字栏：开启底部搜索 + OCR 文字识别。",
                },
                "ai_corner": {
                    "title": "AI 角落标识",
                    "desc": "针对固定在图片四角的 AI 生成标识（图标 + 文字组合）。",
                },
                "ai_text_heavy": {
                    "title": "AI 文字密集",
                    "desc": "针对满版图文的 AI 生成说明/提示水印：全策略 + OCR 文字识别 + 底部搜索，最大化文字召回。",
                },
                "ai_faint_tiled": {
                    "title": "AI 极淡平铺",
                    "desc": "针对透明度极低的平铺 logo：开启平铺增强并进一步提高敏感度，专治几乎看不见的 repeated 水印。",
                },
                "ai_grid": {
                    "title": "AI 网格水印",
                    "desc": "针对规则排布的网格状平铺水印（点阵/网格 logo）：开启网格检测 + 平铺增强 + 全策略，专治 repeated lattice 水印。",
                },
                "ai_gradient": {
                    "title": "AI 渐变水印",
                    "desc": "针对整图叠加的全局平滑渐变署名层：开启全局渐变检测并整图标记（仅建议用于确认有渐变水印的图，会整图修复）。",
                },
                "ai_logo": {
                    "title": "AI LOGO 水印",
                    "desc": "针对角落的实体品牌标识（实心图标 / 字母组合）：开启 LOGO 轮廓检测 + 角落 + 半透明三类策略，专治实心填充型 AI 署名标识。",
                },
                "ai_multi": {
                    "title": "AI 多重水印",
                    "desc": "针对同一张图上叠加多种水印（角落 LOGO + 底部文字 / LOGO + 网格点阵等）：开启半透明 + 角落 + 边缘文字 + 网格 + 实体 LOGO + 平铺增强 + 底部搜索的并集，最大化组合召回。",
                },
            },
            "recommend": {
                "btn": "🔍 智能推荐预设",
                "title": "**智能推荐检测预设：`{preset}`**（置信度 {conf:.0%}）",
                "reason": "推荐 {preset}（置信度 {conf:.0%} · 触发 {feats}）",
                "msg": {
                    "no_upload": "请先上传图片，再点「智能推荐预设」。",
                    "bad_image": "图片无效或过大：{exc}",
                    "no_result": "无推荐结果",
                    "fail": "自动预设推荐失败：{exc}（已回退默认预设）",
                },
                # 阶段 98：渲染器专用分隔/行模板（此前硬编码在 utils/recommend_view.py 内，
                # 英文界面会漏出中文标点）。数值 0~1 映射为字符条，故行模板只负责标点与排列。
                "list_sep": "、",
                "score_line": "  - {name}：{bar} **{val}**",
                "scores_none": "（无）",
                "feature": {
                    "gradient": "全局平滑渐变（整图压强变）",
                    "grid": "规则网格 / 点阵平铺",
                    "tiled": "填充式平铺水印",
                    "translucent": "半透明散点覆盖",
                    "solid_logo": "角落实体 LOGO / 标识",
                    "corner_watermark": "右下角平台角标（模板匹配）",
                    "text": "满图文字笔画密集",
                    "signature": "底部署名栏文字密集",
                    "regularity": "频域平铺规则性",
                    "none": "未命中任何强特征，使用通用检测兜底",
                },
                "scores_title": "各特征激活评分（0~1）：",
            },
            "kind": {
                "translucent": "半透明水印",
                "corner_logo": "角落 LOGO/文字",
                "edge_text": "边缘小字",
                "ocr_text": "OCR 文字框",
                "grid_watermark": "网格水印",
                "gradient_watermark": "渐变水印",
                "logo_watermark": "实体 LOGO",
            },
            "stats": {
                "label": "检测命中率",
                "placeholder": "运行检测后将在此显示掩码覆盖率、候选区域数与各类别命中统计。",
                "coverage": "掩码覆盖率：**{coverage}%**（{pixels} 像素）",
                "regions": "候选区域：**{count}** 个",
                "by_kind": "按类别命中：",
                "kind_sep": "，",
                "ocr_on": "OCR 文字识别：已启用（置信度过滤 ≥ {conf}）",
                "ocr_off": "OCR 文字识别：未启用（使用 MSER/Otsu 降级方案）",
                "none": "未检测到水印区域。",
            },
        },
        "params": {
            "accordion": "处理参数",
            # 阶段 110：历史记录里的「修复参数摘要」**唯一真值来源**。
            # 此前 tab_single / tab_batch / tab_auto **各写一份** f-string 且硬编码中文，
            # 而该文案会被 report_service 渲染进 HTML/PDF（「参数：…」）
            # ⇒ 切到 en-US 仍显示中文（i18n 泄漏）。详见 ui.components.repair_params_text。
            "hist_summary": "引擎={engine} 力度={strength} 降噪={denoise} 膨胀={dilate} 平滑={feather} 阈值={threshold}",
            # 历史页「重新处理」只能拿到 3 个参数（引擎/力度/降噪）⇒ 独立模板（含来源标注）。
            "hist_summary_short": "引擎={engine} 力度={strength} 降噪={denoise}（重新处理）",
            "engine_label": "修复引擎",
            "engine_info": "auto=有模型用 LaMa，否则降级 OpenCV；含已加载的插件引擎",
            "dual_run_label": "双跑择优（较慢，效果更稳）",
            "dual_run_info": "主引擎与副引擎（默认 LaMa）各修复一次，按无参考质量分自动取更优结果；耗时约翻倍",
            "sensitivity_label": "自动检测敏感度",
            "sensitivity_info": "调高可揪出更淡的水印，但误判率上升",
            "strength_label": "修复力度",
            "strength_info": "1.0=完全用修复结果，0.5=原图与结果各半",
            "denoise_label": "降噪强度",
            "denoise_info": "平滑模型生成的细微噪点，0=关闭",
            "dilate_label": "掩码膨胀",
            "dilate_info": "把水印区撑大，吞噬半透明羽化边缘",
            "feather_label": "边缘平滑",
            "feather_info": "高斯羽化，柔化修复区硬边界",
            "threshold_label": "掩码阈值",
            "threshold_info": "灰度边缘判定为水印的临界值",
            "postprocess_label": "后处理增强",
            "postprocess_info": "修复后自动锐化 + 色彩还原 + 边缘融合，提升成品观感",
            "sharpen_label": "后处理·锐化强度",
            "sharpen_info": "0 = 关闭锐化；越大越清晰（过大会出现噪点）",
            "poisson_label": "Poisson 无缝融合",
            "poisson_info": "用无缝克隆把修复区按原图梯度融入周围，彻底消除接缝台阶（需原图+掩码）",
            "poisson_strength_label": "Poisson 融合强度",
            "poisson_strength_info": "1=完全采用融合结果；越小越保留原修复细节",
            "texture_label": "纹理细节重建",
            "texture_info": "把原图周围的高频纹理注入修复区，减少「修补块」观感（需原图+掩码）",
            "texture_amount_label": "纹理重建强度",
            "texture_amount_info": "注入细节的比例，越大纹理越明显（过大会显脏）",
            "artifact_label": "伪影抑制",
            "artifact_info": "在修复区内做边缘保持滤波，抑制 inpaint 的网格/块状伪影",
            "artifact_amount_label": "伪影抑制强度",
            "artifact_amount_info": "双边滤波强度，越大伪影抑制越强（过大会磨平细节）",
            "superres_label": "轻量超分（细节重建）",
            "superres_info": "把原图周围高频细节注入修复区并强化锐度，抵消 inpaint 模糊（同分辨率，无需 AI 模型）",
            "superres_amount_label": "超分强度",
            "superres_amount_info": "细节注入与锐化比例，越大越清晰（过大会显噪）",
            "real_superres_label": "真·超分（AI 模型放大）",
            "real_superres_info": "用 torch + 预训练模型把修复结果真实放大（如 4×），显著提升分辨率；缺 torch/权重时自动回退轻量超分",
            "real_scale_label": "放大倍数",
            "real_scale_info": "真实超分输出尺寸倍数（2/4），主流 ESRGAN 权重为 4×",
            "real_backend_label": "超分后端",
            "real_backend_info": "真实超分模型后端：自动（依次尝试 Real-ESRGAN → ESRGAN）/ ESRGAN（纯 torch RRDB，无需 basicsr）/ Real-ESRGAN（官方权重，需 realesrgan+basicsr）",
            "eval_label": "显示后处理质量提升",
            "eval_info": "开启后在状态栏展示「优化前 → 后」质量分变化",
            "export_format_label": "默认导出格式",
            "export_format_info": "PNG 无损支持透明；JPG 体积小；WEBP 兼顾体积与画质",
            "export_quality_label": "默认导出画质",
            "export_quality_info": "仅对 JPG / WebP 生效（1~100，建议 ≥ 90）",
        },
        "settings": {
            "title": "### ⚙️ 设置与导出",
            "subtitle": "查看运行配置、调整导出偏好、把历史成果一键带走、管理缓存。",
            "snapshot_label": "当前配置快照",
            "export_accordion": "导出设置",
            "compare_gap_label": "对比图间隔像素",
            "compare_gap_info": "生成「原图|修复后」对比图时两张图之间的白边宽度",
            "export_zip": "📦 导出全部历史为 ZIP",
            "export_compares": "🖼 导出对比图 ZIP（全部历史）",
            "clear": "🧹 立即清空缓存",
            "prune": "🧼 清理过期记录",
            "refresh": "🔄 刷新配置快照",
            "download_label": "下载导出结果",
            "prefs": {
                "title": "### ⚙️ 处理偏好（Processing Preferences）",
                "dual_label": "双跑择优（较慢，效果更稳）",
                "dual_info": "每次修复用主引擎与副引擎（默认 LaMa）各跑一次，按无参考质量分自动取更优结果；耗时约翻倍",
                "keep_alpha_label": "导出保留透明通道",
                "keep_alpha_info": "PNG/WebP/TIFF 保留 alpha；关闭或不支持的格式（如 JPG）会合成到背景色，不会显形隐藏内容",
                "exif_label": "应用 EXIF 方向校正",
                "exif_info": "手机照片按方向标签自动转正（关闭后导出图可能相对输入看起来被旋转）",
                "bitdepth_label": "非 8 位图位深规范化",
                "bitdepth_info": "16 位/浮点输入自动映射到 8 位（关闭后此类图会在修复阶段报错）",
                "preserve_label": "只动水印区（推荐追求保真时开启）",
                "follow_fmt_label": "输出格式跟随输入",
                "follow_fmt_info": "JPEG 进 → JPEG 出、WebP 进 → WebP 出；实测输出体积从输入 4.65× 降到 1.13×/1.00×。"
                                   "代价是再经历一次有损编码。关闭时一律输出 PNG（无损，体积较大）。",
                "preserve_info": "水印区 + 8px 保护带之外，输出与原图**逐字节一致**；"
                                 "实测「水印外被改动」从约 50% 降到约 1%，不影响擦除效果。"
                                 "关闭时保持整图轻微锐化的现有观感。",
                "save_label": "保存为默认（写入 config.user_overrides.json）",
                "save_info": "与真实超分/保留策略互不影响（合并式写入）",
                "apply": "应用处理偏好",
                "msg_applied": "已应用处理偏好 —— 双跑择优：{dual}；透明通道：{alpha}；EXIF 方向：{exif}；位深规范化：{depth}；只动水印区：{preserve}；输出格式跟随输入：{fmt}",
            },
            "real_superres": {
                "title": "### 🔍 真实超分（Real Super-Resolution）",
                "enabled_label": "开启真实超分",
                "enabled_info": "开启后优先用 AI 模型放大（如 4×）；后端不可用（缺 torch / 缺权重）时自动回退轻量超分。",
                "scale_label": "放大倍数",
                "scale_info": "真实超分输出尺寸倍数（1~8，主流权重 4×）。",
                "backend_label": "超分后端",
                "backend_info": "auto=依次尝试 Real-ESRGAN→ESRGAN；esrgan=纯 torch RRDB；realesrgan=官方权重。",
                "save_label": "💾 保存为默认",
                "save_info": "勾选后，点击「应用」会把当前三旋钮写入 config.user_overrides.json，下次启动自动生效。",
                "apply": "▶️ 应用真实超分设置",
            },
            "cache_policy": {
                "title": "### 🗂 历史保留策略（Cache Retention）",
                "keep_days_label": "保留天数",
                "keep_days_info": "超过此天数的历史记录会在处理新图或重启时自动清理（1~3650 天）。",
                "max_records_label": "最大记录条数",
                "max_records_info": "超出上限后自动删除最旧的记录（10~5000 条）。",
                "max_bytes_label": "总体积上限（GB）",
                "max_bytes_info": "历史结果总占用超过此上限时，从最旧的记录开始自动删除，直至低于上限（1~100 GB）。视频/批量结果往往很大，建议设置合理的体积上限以防占满磁盘。",
                "save_label": "💾 保存为默认",
                "save_info": "勾选后，点击「应用」会把当前保留策略写入 config.user_overrides.json，下次启动自动生效；与真实超分默认互不覆盖。",
                "apply": "▶️ 应用并清理",
            },
            # 阶段 48：孤儿文件扫描与清理（磁盘治理闭环）。
            "disk_clean": {
                "title": "### 🧹 磁盘清理（孤儿文件回收）",
                "hint": "删除历史记录时只清理缩略图、保留成品图，原图副本也从不回收，久而久之缓存目录会堆积大量无人引用的孤儿文件。这里提供「先扫描预览、确认后再删」的安全回收路径；`outputs/` 与 `exports/` 属用户成品目录，绝不纳入清理。",
                "scan_btn": "🔍 扫描孤儿文件",
                "src_thumbs": "清理缩略图（cache/thumbs）",
                "src_uploads": "清理原图副本（cache/uploads）",
                "clean_btn": "🧹 清理所选孤儿文件",
                "confirm_label": "⚠️ 我确认已查看清单，执行清理",
                "line": "- **{label}**：{count} 个文件 · 可释放 {size}",
                "total": "\n**合计**：{count} 个孤儿文件，可释放 **{size}**",
                "none": "✅ 缓存目录很干净，未发现孤儿文件。",
                "need_scan": "ℹ️ 请先点击「扫描孤儿文件」查看清单。",
                "need_select": "⚠️ 请至少勾选一个清理来源，并勾选确认框。",
                "confirm_needed": "⚠️ 请先勾选「我确认已查看清单」再执行清理。",
                "ok": "✅ 已清理 {count} 个孤儿文件，释放 {size}。",
                "partial": "⚠️ 已清理 {count} 个孤儿文件（释放 {size}），另有 {failed} 个因被占用或权限不足而跳过。",
                "fail": "❌ 清理失败：{exc}",
            },
            "snap": {
                "title": "### 📋 当前运行配置",
                "app": "**应用**：{name} v{version}",
                "device": "**设备模式**：`{mode}`（auto=优先 GPU，否则 CPU）",
                "engine": "**修复引擎**：`{engine}`（auto=有模型用 LaMa，否则 OpenCV 降级）",
                "concurrency": "**批量并发数**：`{n}`",
                "cache": "**缓存开关**：`{state}`（保留 {days} 天 / 最多 {max} 条 / 总体积上限 {cap_gb} GB）",
                "format": "**默认导出格式**：`{fmt}`（画质 {q}）",
                "out": "**输出目录**：`{path}`",
                "export_dir": "**导出目录**：`{path}`",
                "cache_dir": "**缓存目录**：`{path}`（删除它只丢历史列表，不丢成品图）",
                "listen": "**监听地址**：`{host}:{port}`",
                "real_superres_title": "### 🔍 真实超分",
                "enabled": "**总开关**：`{state}`",
                "scale": "**放大倍数**：`{scale}×`",
                "backend": "**后端**：`{backend}`",
                "interact_title": "### ⌨️ 交互增强（阶段 15）",
                "drag": "**全局拖拽上传**：`{state}`（把文件拖到页面任意空白处松手，自动填入当前页签上传框）",
                "shortcut": "**键盘快捷键**：`{state}`（Shift+R 修复 / Shift+D 自动检测 / Shift+B 批量 / Shift+C 取消 / Shift+F 刷新历史）",
                "compare": "**结果对比视图**：`{state}`（单图/自动页展示 Before|After 拼接；批量/历史页可切换对比缩略图）",
                "restart": "> 主题、设备模式等需在 `.env` / `config.py` 修改后**重启程序**方可生效。",
                "msg": {
                    "export_zip_ok": "✅ 已导出 {n} 条历史结果为 ZIP：\n`{path}`",
                    "compare_zip_ok": "✅ 已生成 {n} 张对比图并打包：\n`{path}`",
                    "err": "❌ 导出失败：{exc}",
                    "no_compare": "⚠️ 没有可生成对比图的历史记录（需原图与结果都还在）。",
                    "compare_all_fail": "❌ 对比图全部生成失败，请查看日志。",
                    "zip_fail": "❌ 打包失败：{exc}",
                    "clear_ok": "🧹 已清空 {n} 条历史记录（成品图已保留）。",
                    "prune_ok": "🧼 已清理 {n} 条过期/超额记录。",
                    "superres_applied": "✅ 真实超分设置已应用：开关={state}，倍数={scale}×，后端={backend}。",
                    "cache_applied": "✅ 历史保留策略已应用：保留 {days} 天 / 最多 {max} 条 / 总体积上限 {bytes}，已按策略清理 {n} 条记录。",
                },
            },
        },
        "single": {
            "editor_label": "① 上传并涂抹水印（把图片拖到此处，或点击上传）",
            "params_accordion": "② 修复参数调节（可实时微调）",
            "run": "③ 开始擦除水印",
            "result_title": "### ④ 结果对比（点击图片可放大查看细节）",
            "orig_label": "原图",
            "result_label": "处理后",
            "compare_label": "原图 | 处理后 对比拼接（Before | After）",
            "status_label": "处理状态 / 日志",
            "status_placeholder": "处理信息与日志会显示在这里……",
            "download_label": "⑤ 下载处理结果",
            "msg": {
                "no_image": "⚠️ 请先拖拽或上传一张图片到左侧编辑框。",
                "too_large": "⚠️ 图片过大：{exc}",
                "bad_data": "⚠️ 图片数据异常：{exc}",
                "mask_fail": "⚠️ 无法解析涂抹区域：{exc}",
                "no_mask": "⚠️ 未检测到任何涂抹/框选区域。\n请用左侧「画笔」涂抹水印，或用「矩形」框选水印区域后重试。",
                "no_model": "❌ {exc}\n请先运行 download_model.py 下载模型，或把「修复引擎」切换为 opencv。",
                "fail": "❌ 处理失败：{exc}",
                "done": "✅ 处理完成（引擎：{engine}）。\n结果已保存：{path}\n可点击下方「下载处理结果」按钮，或在图片上右键另存。",
                # 阶段 190：单图结果区显示本次耗时（纯本地计时，不涉遥测）。
                # 口径：从「开始修复」到「增强完成」，**不含**涂抹解析与落盘——
                # 措辞必须与口径一致，否则用户会把耗时误读成「整个操作」。
                "elapsed": "\n⏱️ 本次修复 + 增强耗时 {seconds:.2f} 秒",
                "save_fail": "⚠️ 修复成功，但自动保存失败：{exc}\n您可在右侧结果图上右键另存。",
            },
        },
        "auto": {
            "upload_label": "① 上传图片（拖拽或点击）",
            "sample_btn": "🖼 没有图？试一张示例",
            "sample_loaded": "已载入示例图（右下角有「StudioAI」角标）——接着点「② 自动检测水印」，再点「④ 一键修复」即可。",
            "sample_fail": "示例图载入失败（{exc}）；请改为上传自己的图片。",
            "detect_accordion": "② 检测参数（可微调后重新检测）",
            "detect_mode_label": "检测模式",
            "detect_mode_info": "针对 AI 生成图常见水印样式选择预设，一键提升检测成功率（也可在下方手动微调）",
            "sensitivity_label": "半透明检测敏感度",
            "sensitivity_info": "调高可揪出更淡的半透明水印，但误判率上升",
            "en_translucent": "检测半透明水印",
            "en_corner": "检测角落 LOGO",
            "en_edge": "检测边缘小字",
            "detect_btn": "② 自动检测水印",
            "detect_preview_label": "检测结果预览（红=水印区，彩框=检测类别）",
            "editor_label": "③ 手动微调（画笔增涂 / 橡皮擦除水印区）",
            "repair_accordion": "④ 修复参数调节",
            "repair_btn": "④ 一键修复",
            "result_title": "### ⑤ 结果对比（点击图片可放大查看细节）",
            "orig_label": "原图",
            "result_label": "处理后",
            "compare_label": "原图 | 处理后 对比拼接（Before | After）",
            "status_label": "处理状态 / 日志",
            "status_placeholder": "检测与修复信息会显示在这里……",
            "download_label": "⑥ 下载处理结果",
            "msg": {
                "no_upload": "⚠️ 请先上传一张图片（拖拽或点击左侧上传框）。",
                "detect_fail": "❌ 自动检测失败：{exc}\n可切换到「单图处理（手动涂抹）」标签页手动标记。",
                "no_detect": "ℹ️ 未检测到明显水印区域。\n您可以在下方编辑框用「画笔」手动涂抹水印，再点击「④ 一键修复」。",
                "ocr_on": "（已启用 OCR 文字增强）",
                "ocr_off": "（未启用 OCR，使用 OpenCV 原生检测；装 EasyOCR 可提升文字水印精度）",
                "detect_done": "✅ 检测完成：发现 {n} 个疑似水印区域 {ocr}。\n请在下方编辑框中用画笔增涂、或用橡皮擦除误判区域，然后点击「④ 一键修复」。\n（左图为检测预览，红色=水印区，彩色框=检测类别：橙=半透明 / 青=角落LOGO / 紫=边缘小字 / 绿=OCR文字）",
                "no_editor": "⚠️ 请先上传图片并完成检测（或手动涂抹水印区域）。",
                "no_mask": "⚠️ 尚未标记任何水印区域。\n请先点击「② 自动检测水印」，或在编辑框用画笔涂抹水印后重试。",
                "auto_fallback": "\nℹ️ 编辑框为空，已按默认预设自动检测兜底。",
                "refine_stats": "\n· 精修统计：检测命中 {det:,} 像素 / 编辑框 {user:,} 像素 / 你补画 {added:,} 像素。",
                "upscaled": "\n· 已启用真·超分：输出已放大至 {factor}×（分辨率提升，可能已回退轻量超分）。",
            },
        },
        "mask_edit": {
            "mode_label": "掩码精修模式",
            "mode_info": "合并=检测结果 ∪ 你的精修（推荐，补漏+擦除都生效）；仅精修=只修你亲手绘制的区域（自动剥离检测层）",
            "mode_merge": "合并（检测+精修）",
            "mode_manual": "仅精修（手动绘制）",
            "reload_btn": "🔄 重新加载检测掩码",
            "reload_info": "把最近一次自动检测的结果重新预填进编辑框（擦过头 / 清空后一键恢复）",
            "clear_btn": "🧹 清空编辑框",
            "clear_info": "清空所有图层，回到空白画布重新手动涂抹",
        },
        "batch": {
            "import_title": "### ① 导入图片（文件夹 / 多文件）",
            "folder_label": "导入整个文件夹（点击后选择文件夹）",
            "files_label": "或拖入多张图片（Ctrl/Shift 多选）",
            "mode_label": "批量处理模式",
            "mode_info": "自动=逐张智能检测水印并修复；手动=从同名掩码文件夹读取掩码",
            "mode_auto": "智能自动检测",
            "mode_manual": "手工掩码文件夹",
            "detect_mode_label": "检测模式",
            "detect_mode_info": "针对 AI 生成图常见水印样式选择预设，一键提升检测成功率（手动掩码模式下忽略）",
            "auto_preset_label": "智能推荐每张图预设",
            "auto_preset_info": "为批次中的每张图独立分析并选择最合适的检测预设；开启时下方「检测模式」选择将被忽略",
            "log_preset": "    🤖 智能推荐预设：{preset}",
            "log_preset_reason": "    🤖 智能推荐：{reason}",
            "log_preset_dist": "🤖 智能推荐预设分布：{dist}",
            "mask_folder_label": "② 掩码文件夹（仅「手工掩码模式」需要：存放与原图同名的灰度掩码）",
            "params_accordion": "③ 处理参数（本批次统一）",
            "evaluate_label": "完成后质量评估",
            "evaluate_info": "对每张结果打分，画廊文件名标注质量分与告警（⚠️需复核 / 🔍需关注）",
            "compare_label": "结果对比视图",
            "compare_info": "画廊缩略图改为「原图 | 处理后」拼接图，便于一眼对比",
            "run": "④ 开始批量处理",
            "cancel": "取消",
            "progress_title": "### ⑤ 处理进度与结果",
            "status_label": "当前进度",
            "status_init": "🔄 正在处理：**—**  \n完成 0 / 失败 0 / 取消 0 / 共 0",
            "bar_label": "总体进度",
            "log_label": "处理日志（成功 / 失败明细）",
            "log_placeholder": "批量处理开始后，这里会实时滚动每条图片的处理结果……",
            "gallery_label": "处理结果（点击可放大，右键可另存）",
            "report_title": "### ⑥ 导出本次批量报告",
            "report_fmt_label": "报告格式",
            "report_fmt_info": "HTML 单文件自包含（对比图内嵌）；PDF 由 PyMuPDF 生成",
            "report_btn": "📄 导出本次报告",
            "report_file_label": "报告文件（点击下载）",
            "params_accordion": "③ 处理参数（本批次统一）",
            "status_running": "🔄 正在处理：**{current}**  \n完成 {done} / 失败 {failed} / 取消 {cancelled} / 共 {total}",
            "start": "🚀 开始批量处理：共 {total} 张（拒绝 {rejected} 张）。",
            "summary": "🏁 批量结束：成功 {done} / 失败 {failed} / 取消 {cancelled} / 共 {total}。",
            "log_done": "  ✅ {name}：{status}",
            "log_fail": "  ❌ {name}：{error}",
            "log_cancel": "  ⏹ {name}：已取消",
            "task_reading_mask": "已读取图片，正在准备掩码…",
            "task_cache_hit_msg": "命中缓存，已复用",
            "task_cache_hit_status": "✅ 命中缓存，已复用历史结果",
            "task_mask_read": "已读取手工掩码",
            "task_saving": "已保存，正在登记缓存…",
            "task_done": "✅ 处理完成",
            "task_skip": "跳过（{reason}）",
            "task_read_fail": "读取失败：{reason}",
            "task_no_mask_file": "未找到同名掩码文件（{name}）",
            "task_no_region": "未检测到水印区域（自动检测为空，或掩码缺失）",
            "task_no_result": "修复未产出结果",
            "task_detect_done": "自动检测完成（{n} 个区域）",
            "task_detect_done_cov": "自动检测完成（{n} 个区域，覆盖率 {cov}%）",
            "list_sep": "，",
            "progress_queue": "排队 {n} 张",
            "progress_done": "完成 {done}/{total}",
            "caption_quality": "{name} · 质量{score}分{icon}",
            "log_quality": "    🔎 质量评估：{name} 分{score}（{level}）— {detail}",
            "report_ok": "✅ 报告已导出：{path}",
            "export_err": "❌ 导出失败：{exc}",
            "msg": {
                "no_files": "⚠️ 未选择任何图片文件（请导入文件夹或拖入多张图片）。",
                "need_mask": "⚠️ 手动掩码模式需要指定「掩码文件夹」。",
                "no_tasks": "⚠️ 没有可处理的图片（{rejected} 个被拒绝）。",
                "cancel_done": "⏹ 已发送取消信号，未开始的任务将不再处理（进行中的任务会安全结束）。",
                "cancel_none": "ℹ️ 当前没有正在运行的批量任务。",
                "report_empty": "⚠️ 本次还没有可导出的批量结果（请先运行一次批量处理）。",
                "elapsed": "⏱️ 本次批量处理耗时 {seconds:.2f} 秒",
            },
        },
        "history": {
            "title": "### 📁 处理记录缓存（近期处理自动归档，免重复上传）",
            "compare_label": "结果对比视图",
            "compare_info": "缩略图改为「原图 | 处理后」拼接图，便于一眼对比（切换后点刷新生效）",
            "refresh": "🔄 刷新",
            "clear": "🧹 清空全部",
            "report_fmt_label": "报告格式",
            "report_fmt_info": "HTML 单文件自包含（对比图内嵌）；PDF 由 PyMuPDF 生成",
            "report_btn": "📄 导出修复报告",
            "report_file_label": "报告文件（点击下载）",
            "gallery_label": "历史缩略图（点击查看详情）",
            "detail_label": "记录详情",
            "video_preview_label": "视频预览",
            "download_label": "下载该结果",
            "reprocess_accordion": "重新处理参数",
            "reprocess_btn": "♻️ 重新处理所选",
            "delete_btn": "🗑 删除所选",
            "delete_filtered_btn": "🗑🗑 批量删除当前筛选结果",
            "delete_filtered_confirm": "⚠️ 我已确认，允许删除（勾选后点击下方按钮生效）",
            "info_empty": "（暂无记录，去批量或单图页处理几张吧）",
            "count": "共 {n} 条记录。",
            "tip_select": "点击缩略图查看详情。",
            "not_found": "未找到该历史记录（可能已被清理）。",
            "reprocess_done": "✅ 已重新处理 {name}（{status}）。",
            "reprocess_no_result": "⚠️ 重新处理未产生结果。",
            "filter_label": "类型筛选",
            "filter_info": "按结果类型过滤历史记录（全部 / 图片 / 视频 / PDF）",
            "filter_all": "全部",
            "filter_image": "图片",
            "filter_video": "视频",
            "filter_pdf": "PDF",
            "export_filtered_btn": "📦 导出筛选结果（ZIP）",
            "export_filtered_file_label": "筛选结果压缩包（点击下载）",
            "export_filtered_ok": "✅ 已按筛选打包：{path}",
            "export_filtered_empty": "⚠️ 当前筛选（{kind}）没有可导出的成功结果。",
            "search_label": "🔍 搜索文件名",
            "search_placeholder": "输入关键词，按文件名模糊匹配（不区分大小写）…",
            "search_info": "在历史记录中按文件名筛选；与上方「类型筛选」叠加生效。",
            "filter_active": "\n\n🔎 当前筛选：类型 `{kind}` · 关键词 `{q}` · 命中 {n} 条",
            "clear_ok": "🧹 已清空 {n} 条历史记录（成品图已保留）。",
            "delete_filtered_ok": "✅ 已删除当前筛选下的 {n} 条记录。",
            "delete_filtered_empty": "⚠️ 当前筛选下没有可删除的记录。",
            "delete_filtered_warn": "⚠️ 请先勾选「我已确认」复选框，再点击删除。",
            "prune_policy_btn": "🧹 按策略清理过期记录",
            "prune_policy_confirm": "⚠️ 我已确认，允许按保留策略清理（勾选后点击下方按钮生效）",
            "prune_policy_ok": "✅ 已按策略清理 {n} 条过期/超额记录。",
            "prune_policy_none": "ℹ️ 当前没有需要按策略清理的过期/超额记录。",
            "disk_usage": {
                "title": "### 💽 历史结果磁盘占用",
                "total": "**总占用**：{total}（{count} 条记录）｜已配置上限 {cap}",
                "kind": "- {kind}：{count} 条 · {size}",
            },
            "prune_size_btn": "💽 按体积上限清理",
            "prune_size_ok": "✅ 已按体积上限清理 {n} 条记录。",
            "prune_size_none": "ℹ️ 当前总占用未超过上限，无需按体积清理。",
            "msg": {
                "no_select": "请先点击选择一条记录。",
                "deleted": "✅ 已删除该记录。",
                "delete_fail": "⚠️ 删除失败或记录不存在。",
                "confirm_needed": "⚠️ 请先勾选「我已确认」复选框，再点击删除。",
                "no_original": "⚠️ 原图已丢失，无法重新处理（请重新导入图片）。",
                "reprocess_no_mask": "⚠️ 重新检测未找到水印区域，无法修复。",
                "reprocess_fail": "❌ 重新处理失败：{exc}",
            },
            "detail": {
                "title": "### {icon} {name}",
                "id": "**记录 ID**：`{id}`",
                "time": "**处理时间**：{time}",
                "mode_engine": "**模式 / 引擎**：{mode} / {engine}",
                "status": "**状态**：{status}",
                "params": "**参数**：{params}",
                "result": "**结果路径**：{result}",
                "size": "**结果体积**：{size}",
                "video": "**视频信息**：{w}×{h} · {fps:.0f}fps · {frames} 帧 · 时长 {dur} · {upscaled}",
                "video_hint": "> 🎬 视频记录仅支持预览与下载；如需重新处理请回到「🎬 视频」页重新上传。",
                "pdf": "**PDF 信息**：共 {pages} 页 · 结果体积 {size_mb} MB",
                "pdf_hint": "> 📄 PDF 记录仅支持预览与下载；如需重新处理请回到「📄 PDF 文档」页重新上传。",
            },
        },
        "media": {
            "title": (
                "### 📄🎬 处理 PDF 文档与视频\n"
                "原理：把文档的**每一页**、视频的**每一帧**都当成一张图片，"
                "逐张做智能去水印，再按原样拼回去。\n"
                "**这两类任务都比处理单张图片慢得多，建议先用小文件试水。**"
            ),
            "pdf": {
                "tab": "📄 PDF 文档",
                "file_label": "① 上传 PDF 文档",
                "info_init": "_尚未上传文档。_",
                "pages_label": "② 页码范围",
                "pages_placeholder": "留空 = 全部页；示例：1-5 或 1,3,7 或 2-4,9",
                "pages_info": "页码从 1 开始数。只处理需要的页，能省大量时间",
                "dpi_label": "③ 渲染清晰度（DPI）",
                "dpi_info": "150=推荐；72=快但糊；300=印刷级但很慢",
                "params_accordion": "④ 修复参数（本文档统一）",
                "detect_mode_label": "检测模式",
                "detect_mode_info": "针对 AI 生成图常见水印样式选择预设，一键提升检测成功率",
                "auto_preset_label": "🤖 智能推荐每页预设",
                "auto_preset_info": "开启后整份 PDF 的每一页独立挑选最契合的检测预设（推荐；关闭则整份共用上方下拉）",
                "page_preset": "· 推荐预设：{preset}",
                "preset_dist": "🤖 智能推荐预设分布：{dist}",
                "btn": "⑤ 开始处理 PDF",
                "result_title": "### 处理结果",
                "log_label": "处理日志（逐页明细）",
                "log_placeholder": "点击「开始处理 PDF」后，这里会显示每一页的处理结果……",
                "out_label": "结果文档（点击下载）",
                "note": (
                    "> **关于输出 PDF 的两点说明**\n"
                    "> 1. 被处理过的页面会变成图片，其中的文字**不能再被选中或搜索**；\n"
                    "> 2. 未选中的页、以及没检出水印的页，都会**原样保留**，画质零损失。"
                ),
                "no_fitz": (
                    "❌ **当前环境未安装 PyMuPDF，PDF 功能不可用。**\n\n"
                    "请在命令行执行：`pip install PyMuPDF` 后重启本程序。"
                ),
                "read_fail": "❌ 读取失败：{error}",
                "read_exc": "❌ 读取文档信息时出错：{exc}",
                "progress_desc": "准备处理 PDF……",
                "no_file": "⚠️ 请先上传一份 PDF 文档。",
                "invalid": "❌ 文件校验未通过：{exc}",
                "loaded": (
                    "✅ **已载入：** {name}\n\n"
                    "- 总页数：**{pages} 页**\n"
                    "- 页面尺寸：{size}\n"
                    "- 文档标题：{title}\n"
                    "- 文件大小：{mb} MB\n"
                    "- 全部处理预计耗时：约 {est}（仅供参考，取决于电脑性能）\n\n"
                    "> 单次最多处理 {max} 页，超出部分会原样保留。"
                ),
                "done": "✅ 处理完成：{name}",
                "page_detail_ok": "（水印像素 {pixels}）",
                "page_detail_err": "（{error}）",
                "per_page": "逐页明细：",
                "tip": (
                    "\n💡 提示：输出 PDF 的页面已变为图像，文字不可再被选中或搜索，"
                    "   这是「整页重绘」方案的固有代价。若只需处理个别页，请填写页码范围。"
                ),
                "fail": "❌ 处理失败：{error}",
                "exc": "❌ 处理过程中发生异常：{type}: {exc}",
            },
            "video": {
                "tab": "🎬 视频",
                "file_label": "① 上传视频",
                "info_init": "_尚未上传视频。_",
                "step_label": "② 抽帧步长",
                "step_info": "每隔几帧重新找一次水印位置。台标类固定水印设 3~5 可提速数倍",
                "max_label": "③ 最多处理帧数",
                "max_info": "防呆上限。30fps 视频：300 帧 ≈ 10 秒画面",
                "reuse_label": "复用上一帧的水印位置（推荐）",
                "reuse_info": "固定位置水印务必勾选；滚动字幕、飘动水印请取消勾选并把步长设为 1",
                "params_accordion": "④ 修复参数（本视频统一）",
                "detect_mode_label": "检测模式",
                "detect_mode_info": "针对 AI 生成图常见水印样式选择预设，一键提升检测成功率",
                "auto_preset_label": "🤖 智能推荐每帧预设",
                "auto_preset_info": "开启后视频的每个检测帧独立挑选最契合的检测预设（推荐；勾选「复用水印位置」时自动关闭）",
                "preset_dist": "🤖 智能推荐预设分布：{dist}",
                "recommend_conf": "🤖 智能推荐平均置信度：{conf:.0%}",
                "upscaled": "✅ 已启用真实超分，输出分辨率放大为 **{scale}×**（{w}×{h}）",
                "compare_title": "### 🔍 抽帧对比查看",
                "compare_frame_label": "抽帧序号",
                "compare_frame_info": "选择要对比的原始 / 结果帧（0 = 首帧；超出长度将自动取末帧）",
                "compare_no_video": "⚠️ 请先处理视频生成结果，再进行抽帧对比。",
                "compare_no_frame": "⚠️ 无法读取该帧（可能超出视频长度）。",
                "browse_title": "### 🎞️ 结果帧浏览",
                "browse_info": "处理完成后自动抽样若干帧，便于不播放视频也能快速浏览修复效果；点击任一帧即可加载到下方对比面板。",
                "browse_empty": "⚠️ 处理视频后这里会显示抽样帧。",
                "export_frame_label": "⑦ 导出对比帧图",
                "export_sheet_label": "⑧ 导出帧拼图",
                "export_no_video": "⚠️ 请先处理视频生成结果，再导出。",
                "export_done": "✅ 已导出：{path}",
                "btn": "⑤ 开始处理视频",
                "result_title": "### 处理结果",
                "log_label": "处理日志",
                "log_placeholder": "点击「开始处理视频」后，这里会显示处理统计……",
                "out_label": "结果视频（可在线预览 / 下载）",
                "ffmpeg_label": "🔊 想把原声加回去？复制这条命令到命令行执行（需先装 ffmpeg）",
                "ffmpeg_placeholder": "处理完成后这里会给出现成命令……",
                "note": (
                    "> **视频处理三点须知**\n"
                    "> 1. **很慢**：一帧就是一张图，300 帧相当于处理 300 张图片，请耐心等待；\n"
                    "> 2. **无声**：输出视频不含音轨，需要原声请用上方给出的 ffmpeg 命令合并；\n"
                    "> 3. **抽帧步长**不会让视频丢帧变短——所有帧都会被写入，"
                    "只是中间帧复用了上一次找到的水印位置。"
                ),
                "no_file": "⚠️ 请先上传一段视频。",
                "invalid": "❌ 文件校验未通过：{exc}",
                "read_fail": "❌ 读取失败：{error}",
                "read_exc": "❌ 读取视频信息时出错：{exc}",
                "progress_desc": "准备处理视频……",
                "loaded": (
                    "✅ **已载入：** {name}\n\n"
                    "- 分辨率：**{w} × {h}**\n"
                    "- 帧率：{fps} fps\n"
                    "- 总帧数：**{frames} 帧**（时长 {dur} 秒）\n"
                    "- 文件大小：{mb} MB\n"
                    "- 本次预计处理：{plan} 帧，约 {est}（仅供参考，开启抽帧复用可显著缩短）{warn}\n\n"
                    "> 🔇 输出视频**没有声音**（OpenCV 不处理音频），"
                    "如需保留原声请按下方提示用 ffmpeg 合并。"
                ),
                "warn": (
                    "\n\n> ⚠️ 视频共 {frames} 帧，超过单次上限 {max} 帧，"
                    "**只会处理前 {max} 帧**，输出视频会比原视频短。"
                ),
                "done": "✅ 处理完成：{name}",
                "spec": "输出规格：{w}×{h} · {fps}fps · 编码器 {codec}",
                "warn_title": "注意事项：",
                "fail": "❌ 处理失败：{error}",
                "exc": "❌ 处理过程中发生异常：{type}: {exc}",
            },
            "msg": {
                "elapsed": "⏱️ 本次处理耗时 {seconds:.2f} 秒",
            },
        },
        "models": {
            "title": "### 🧠 模型管理",
            "subtitle": (
                "管理修复模型权重：切换当前模型、下载新权重、校验完整性、回滚到备份。"
                "同一套 LaMa 接口可挂多个 .pt 权重，在 `models/` 目录放下文件即自动出现。"
            ),
            "list_label": "模型槽清单",
            "avail": "✅ 已就位",
            "missing": "❌ 缺失",
            "size_unknown": "大小未知",
            "expected_mb": "预期 {mb} MB",
            "loaded_tag": "（已加载）",
            "slot_line": "-{mark} **{name}** —— {display} ｜ {avail} ｜ {size}{loaded}",
            "select_label": "选择模型槽",
            "select_info": "下拉选择要操作的模型（也可直接点「设为当前模型」切换）",
            "refresh": "🔄 刷新列表",
            "set_active": "⭐ 设为当前模型",
            "download": "⬇️ 下载选中模型",
            "download_start": "准备下载模型：{target}",
            "download_progress": "{done} / {total} MB ｜ {kbps} KB/s",
            "verify": "🔍 校验完整性",
            "verify_pass": "通过 ✅",
            "verify_fail": "未通过 ❌",
            "rollback": "↩️ 回滚到备份",
            "read_fail": "⚠️ 读取模型列表失败：{exc}",
            "op_fail": "⚠️ 操作失败：{exc}",
            "empty": "当前没有任何模型槽。",
            "list_title": "### 🧠 模型槽清单（* 为当前模型）",
            "active": "当前模型（LaMa 引擎使用）：**{name}**",
            "refresh_done": "🔄 已刷新模型列表。",
            "no_slot": "⚠️ 请先选择一个模型槽。",
            "set_active_done": "✅ 已将当前模型切换为：**{name}**（LaMa 引擎将使用其权重，已持久化）。",
            "set_active_fail": "⚠️ 未找到模型槽：{name}",
            "download_done": "✅ 模型 **{target}** 已下载并校验通过：\n`{path}`",
            "download_fail": "❌ 下载失败：{exc}\n若之前存在可用权重，已尝试自动回滚到备份；也可手动放置权重到 models/ 目录。",
            "verify_done": "🔍 校验 [{name}]：**{status}**\n\n> {reason}",
            "rollback_done": "↩️ 已将模型 **{target}** 回滚到上一版备份权重。",
            "rollback_fail": "⚠️ 模型 **{target}** 没有可用备份（可能从未覆盖过，或备份已被清理）。",
            "status_title": "### 📊 运行状态",
            "status_device": "推理设备：{name}（{kind}）",
            "status_device_gpu": "🚀 GPU 加速",
            "status_device_cpu": "💻 CPU 推理",
            "status_device_line": "- {line} ｜ {label}",
            "status_vram": "显存：{vram} MB",
            "status_fp16": "半精度（FP16）：{state}",
            "status_fp16_on": "已开启",
            "status_fp16_off": "未开启",
            "status_active_path": "当前权重路径：`{path}`",
            "status_loaded": "内存缓存：已加载 {n} 个模型（当前模型 {state}）",
            "status_loaded_yes": "已在内存",
            "status_loaded_no": "未载入内存",
            "reload": "🔄 清空缓存并重新加载",
            "reload_done": "✅ 已清空模型缓存并重新加载当前模型 **{name}**（已在内存：{state}）。",
            "reload_loaded_yes": "是",
            "reload_loaded_no": "否",
            "reload_fail": "❌ 重新加载失败：{error}",
            "detail": "📄 模型详情",
            "detail_done": (
                "📄 详情 [{name}]：\n\n"
                "> 路径：`{path}`\n"
                "> 大小：{size} MB\n"
                "> 修改时间：{mtime}\n"
                "> 预期 SHA256：`{sha}`\n"
                "> 实际 SHA256：`{sha_c}`\n"
                "> 状态：**{status}**\n\n"
                "> {reason}"
            ),
            "detail_missing": "⚠️ 模型 **{name}** 权重文件不存在，无法查看详情。",
        },
        "compare": {
            "enh_title": "### 🔬 增强对比视图",
            "enh_mode_label": "对比模式",
            "enh_mode_info": "拼接（静态并排） / 滑块（拖动揭示） / 热图（差异高亮）",
            "mode_concat": "拼接对比",
            "mode_slider": "拖动滑块",
            "mode_heatmap": "差异热图",
            "enh_btn": "🔄 生成 / 刷新增强对比",
            "no_result": "⚠️ 请先执行修复生成结果，再生成增强对比。",
            "slider_label": "拖动分割线对比原图与结果（左：原图 / 右：结果）",
            "heatmap_label": "差异热图：越亮表示该处像素改动越大",
            # 阶段 38：批量页 / 历史页共用的「前后对比查看器」。
            "viewer": {
                "title": "### 🔍 前后对比查看器",
                "hint": "在上方画廊点选任意一项，即可在此查看该条目的「原图 vs 结果」对比。",
                "mode_label": "对比模式",
                "mode_info": "拼接（静态并排） / 滑块（拖动揭示） / 热图（差异高亮）",
                "btn": "🔄 生成 / 刷新对比",
                "export_btn": "⬇ 导出对比图（原始分辨率）",
                "file_label": "对比图下载",
                "no_select": "ℹ️ 尚未选择条目：请先在画廊中点选一张结果图。",
                "no_pair": "⚠️ 该条目缺少原图或结果文件，无法生成对比（文件可能已被移动或删除）。",
                "render_fail": "⚠️ 对比渲染失败，可切换为「拼接对比」模式重试（详见日志）。",
                "export_ok": "✅ 对比图已导出：\n`{path}`",
                "export_fail": "❌ 对比图导出失败：原图或结果文件缺失。",
                "selected": "**当前条目**：{name}",
                "summary": (
                    "尺寸 `{w}×{h}`｜改动像素 `{ratio}`｜"
                    "平均差异 `{mean}`｜峰值 `{mx}`"
                ),
                "summary_upscaled": "｜结果已放大至 `{rw}×{rh}`",
                "summary_none": "（无差异统计）",
                "video_hint": "🎬 视频记录请到「媒体与文档」页使用抽帧对比。",
                "pdf_hint": "📄 PDF 记录请到「媒体与文档」页的「📄 PDF 文档」子页预览与下载。",
            },
        },
        "lang": {
            "label": "界面语言",
            "info": "切换界面语言（重启程序后完全生效；切换后自动刷新当前页面）",
            "zh": "中文（简体）",
            "en": "English",
            "applied": "✅ 界面语言已切换为：{name}（部分静态文案重启后彻底生效）。",
        },
        # 阶段 95：适用范围提示。**模板里刻意不写任何类型名 / 预设名**——
        # 类型名与承诺面全部由 `config` 派生、由 `ui/components.py:render_scope_note`
        # 注入，于是「文案里承诺了哪些类型」永远等于
        # `config.WATERMARK_TYPE_COMMITTED`，不会随人工编辑走偏。
        # 守卫：tests/test_watermark_scope95.py 断言本模板不含任何已登记类型名。
        "scope": {
            "note": (
                "⚠️ **适用范围**：本工具**承诺处理** {types}；"
                "**明确不承诺** {others}等样式——后者对应预设仍可选用，"
                "但效果不保证，也不计入质量门禁的结论。"
            ),
        },
        # 阶段 169：报告类交付物（HTML/PDF 修复报告）文案双语化；
        # report_service.py 通过 t() 跟随全局语言取用，避免报告单语泄漏。
        "report": {
            "title": "{app_name} 修复报告",
            "summary_total": "共 <b>{n}</b> 条",
            "summary_avg": " · 平均分 <b>{avg}</b>",
            "note_no_quality": "本报告未包含质量评估（可在设置中开启 REPORT_INCLUDE_QUALITY）。",
            "quality_good": "良好",
            "quality_warn": "告警",
            "quality_review": "待复核",
            "quality_unassessed": "未评估",
            "unassessed_quality": "未评估质量",
            "alt_compare": "对比图",
            "quality_score": "质量分 {score} · {level}",
            "lbl_mode_engine": "模式/引擎：",
            "lbl_time": "时间：",
            "lbl_params": "参数：",
            "lbl_name": "名称：",
            "lbl_recommend": "智能推荐：",
            "pdf_gen_time": "生成时间：",
            "pdf_total": "共 {n} 条处理结果",
            "pdf_quality_overview": "质量概况：",
            "pdf_avg_score": "平均质量分：",
            "empty_records": "暂无可导出的成功记录。",
            "err_png_encode": "PNG 编码失败",
            "err_no_records": "没有可导出的成功记录（请先处理几张图片）。",
            "log_html_done": "HTML 报告已生成：{path}（{n} 条）",
            "log_pdf_done": "PDF 报告已生成：{path}（{n} 条）",
            "log_compare_fail": "报告对比图生成失败，跳过该记录：{exc}",
            "log_quality_fail": "报告质量评估失败，跳过该条质量：{exc}",
            "log_pdf_img_fail": "PDF 插入图片失败：{exc}",
            "log_pdf_fallback": "未安装 PyMuPDF，PDF 报告回退为 HTML。",
        },
    },
    "en-US": {
        "common": {
            "on": "On",
            "off": "Off",
            "restart_to_apply": "(restart required to take effect)",
            "unknown_title": "(Untitled)",
        },
        "app": {
            "compliance": (
                "> ⚠️ **Compliance Notice**: This tool is designed to remove visible watermarks / "
                "attribution marks from **AI images you generated yourself** (e.g. your own watermarks). "
                "Do not use it to remove others' works or third-party AI platform signatures "
                "(usually against their Terms of Service).\n"
                "> See the Compliance Statement printed in the console at startup."
            ),
            "tab": {
                "single": "Single Image (Manual)",
                "auto": "Smart Auto-Detect",
                "batch": "Batch Queue",
                "media": "Docs & Video",
                "history": "History",
                "settings": "Settings & Export",
                "models": "Model Manager",
            },
        },
        "detect": {
            "preset": {
                "general": {
                    "title": "General Detection",
                    "desc": "Combines translucent / corner / edge strategies; fits most watermark scenarios.",
                },
                "ai_tiled": {
                    "title": "AI Tiled Watermark",
                    "desc": "For the semi-transparent tiled logos common on AI-generated images; enables tiled enhancement to catch fainter repeats.",
                },
                "ai_signature": {
                    "title": "AI Signature Bar",
                    "desc": "For the centered-bottom / corner signature bars of AI platforms; enables bottom search + OCR text recognition.",
                },
                "ai_corner": {
                    "title": "AI Corner Mark",
                    "desc": "For fixed AI-generated marks at the image corners (icon + text combo).",
                },
                "ai_text_heavy": {
                    "title": "AI Text-Heavy",
                    "desc": "For AI-generated captions/hints laid across the image; all strategies + OCR + bottom search to maximize text recall.",
                },
                "ai_faint_tiled": {
                    "title": "AI Faint Tiled",
                    "desc": "For very low-opacity tiled logos; enables tiled enhancement with higher sensitivity to catch nearly invisible repeats.",
                },
                "ai_grid": {
                    "title": "AI Grid Watermark",
                    "desc": "For regularly tiled grid-like watermarks (dot/logo lattices): enables grid detection + tiled enhancement + all strategies to catch repeated lattices.",
                },
                "ai_gradient": {
                    "title": "AI Gradient Watermark",
                    "desc": "For a full-image global smooth gradient signature layer: enables global gradient detection and marks the whole image (only for images confirmed to have a gradient watermark; will inpaint the whole image).",
                },
                "ai_logo": {
                    "title": "AI LOGO Watermark",
                    "desc": "For solid brand marks in corners (filled icons / letter combos): enables LOGO contour detection + corner + semi-transparent strategies to catch solid-filled AI signature marks.",
                },
                "ai_multi": {
                    "title": "AI Multi-Watermark",
                    "desc": "For images carrying several watermarks at once (corner LOGO + bottom caption, LOGO + dot lattice, ...): enables the union of semi-transparent + corner + edge text + grid + solid LOGO + tiled enhancement + bottom search to maximize combined recall.",
                },
            },
            "recommend": {
                "btn": "🔍 Auto-Recommended Preset",
                "title": "**Recommended detection preset: `{preset}`** (confidence {conf:.0%})",
                "reason": "Recommended {preset} (confidence {conf:.0%} · triggered {feats})",
                "msg": {
                    "no_upload": "Upload an image first, then click 'Auto-Recommended Preset'.",
                    "bad_image": "Image invalid or too large: {exc}",
                    "no_result": "no recommendation result",
                    "fail": "Auto preset recommendation failed: {exc} (fell back to default)",
                },
                # Stage 98: renderer-only separators / line template (previously hardcoded
                # in utils/recommend_view.py, so the CJK punctuation leaked into EN UI).
                "list_sep": ", ",
                "score_line": "  - {name}: {bar} **{val}**",
                "scores_none": "(none)",
                "feature": {
                    "gradient": "Global smooth gradient (whole-image pressure change)",
                    "grid": "Regular grid / dot lattice tiling",
                    "tiled": "Filled tiled watermark",
                    "translucent": "Semi-transparent scattered coverage",
                    "solid_logo": "Solid corner LOGO / mark",
                    "corner_watermark": "Bottom-right platform corner mark (template match)",
                    "text": "Dense full-image text strokes",
                    "signature": "Dense bottom signature-bar text",
                    "regularity": "Frequency-domain tiling regularity",
                    "none": "No strong feature matched; using general detection fallback",
                },
                "scores_title": "Per-feature activation scores (0~1):",
            },
            "kind": {
                "translucent": "Semi-transparent",
                "corner_logo": "Corner LOGO/Text",
                "edge_text": "Edge text",
                "ocr_text": "OCR text box",
                "grid_watermark": "Grid watermark",
                "gradient_watermark": "Gradient watermark",
                "logo_watermark": "Solid LOGO",
            },
            "stats": {
                "label": "Detection Hit-Rate",
                "placeholder": "After running detection, mask coverage, candidate region count and per-kind hit stats will show here.",
                "coverage": "Mask coverage: **{coverage}%** ({pixels} px)",
                "regions": "Candidate regions: **{count}**",
                "by_kind": "Hits by kind:",
                "kind_sep": ", ",
                "ocr_on": "OCR text recognition: enabled (confidence filter ≥ {conf})",
                "ocr_off": "OCR text recognition: disabled (MSER/Otsu fallback)",
                "none": "No watermark regions detected.",
            },
        },
        "params": {
            "accordion": "Repair Parameters",
            "hist_summary": "engine={engine} strength={strength} denoise={denoise} dilate={dilate} feather={feather} threshold={threshold}",
            "hist_summary_short": "engine={engine} strength={strength} denoise={denoise} (reprocessed)",
            "engine_label": "Repair Engine",
            "engine_info": "auto=LaMa if model present, else OpenCV fallback; includes loaded plugin engines",
            "dual_run_label": "Dual-run best-pick (slower, more robust)",
            "dual_run_info": "Runs primary + secondary engine (default LaMa) and keeps the better result by no-reference quality score; ~2x runtime",
            "sensitivity_label": "Detect Sensitivity",
            "sensitivity_info": "Higher = catches fainter watermarks, but more false positives",
            "strength_label": "Repair Strength",
            "strength_info": "1.0=fully use result, 0.5=half original + half result",
            "denoise_label": "Denoise",
            "denoise_info": "Smooth minor model noise; 0=off",
            "dilate_label": "Mask Dilate",
            "dilate_info": "Grow the watermark area to swallow semi-transparent feathered edges",
            "feather_label": "Edge Feather",
            "feather_info": "Gaussian feather to soften hard repair boundaries",
            "threshold_label": "Mask Threshold",
            "threshold_info": "Gray edge threshold for judging watermark",
            "postprocess_label": "Post-Process",
            "postprocess_info": "Auto sharpen + color match + edge blend after repair for better look",
            "sharpen_label": "Post-Process · Sharpen",
            "sharpen_info": "0 = off; higher = sharper (too high adds noise)",
            "poisson_label": "Poisson Seamless Blend",
            "poisson_info": "Seamlessly clones the repaired region into surroundings by gradient (needs original+mask)",
            "poisson_strength_label": "Poisson Blend Strength",
            "poisson_strength_info": "1=fully use blended result; lower keeps more of the original repair",
            "texture_label": "Texture Detail Reconstruction",
            "texture_info": "Injects surrounding high-freq texture into the repaired area to reduce the 'patched' look (needs original+mask)",
            "texture_amount_label": "Texture Amount",
            "texture_amount_info": "Proportion of injected detail; higher=more texture (too high looks dirty)",
            "artifact_label": "Artifact Reduction",
            "artifact_info": "Edge-preserving filter inside the repaired area to suppress inpaint grid/block artifacts",
            "artifact_amount_label": "Artifact Reduction Strength",
            "artifact_amount_info": "Bilateral filter strength; higher=stronger clean (too high blurs detail)",
            "superres_label": "Lightweight Super-Res (Detail Recovery)",
            "superres_info": "Injects surrounding high-freq detail into the repaired area and sharpens, countering inpaint blur (same resolution, no AI model)",
            "superres_amount_label": "Super-Res Strength",
            "superres_amount_info": "Detail injection & sharpen ratio; higher=sharper (too high adds noise)",
            "real_superres_label": "Real Super-Res (AI Model Upscale)",
            "real_superres_info": "Uses torch + a pretrained model to genuinely upscale the repaired result (e.g. 4×), greatly boosting resolution; auto-falls back to lightweight SR when torch/weights are missing",
            "real_scale_label": "Upscale Factor",
            "real_scale_info": "Output size multiplier (2/4); mainstream ESRGAN weights are 4×",
            "real_backend_label": "Super-Res Backend",
            "real_backend_info": "Real super-res model backend: Auto (tries Real-ESRGAN → ESRGAN in order) / ESRGAN (pure torch RRDB, no basicsr) / Real-ESRGAN (official weights, requires realesrgan+basicsr)",
            "eval_label": "Show Post-Process Quality Gain",
            "eval_info": "When on, shows the before → after quality score change in the status bar",
            "export_format_label": "Default Export Format",
            "export_format_info": "PNG lossless with alpha; JPG small; WEBP balanced",
            "export_quality_label": "Default Export Quality",
            "export_quality_info": "Only for JPG / WebP (1~100, recommend >= 90)",
        },
        "settings": {
            "title": "### ⚙️ Settings & Export",
            "subtitle": "View runtime config, adjust export preferences, take history results with you, manage cache.",
            "snapshot_label": "Config Snapshot",
            "export_accordion": "Export Settings",
            "compare_gap_label": "Compare Gap (px)",
            "compare_gap_info": "White gap between original | result in the comparison image",
            "export_zip": "📦 Export All History as ZIP",
            "export_compares": "🖼 Export Compare Images ZIP (All History)",
            "clear": "🧹 Clear Cache Now",
            "prune": "🧼 Prune Expired Records",
            "refresh": "🔄 Refresh Snapshot",
            "download_label": "Download Export",
            "prefs": {
                "title": "### ⚙️ Processing Preferences",
                "dual_label": "Dual-run best-pick (slower, more robust)",
                "dual_info": "Runs primary + secondary engine (default LaMa) per repair and keeps the better result by no-reference quality score; ~2x runtime",
                "keep_alpha_label": "Keep transparency on export",
                "keep_alpha_info": "PNG/WebP/TIFF keep alpha; unsupported formats (e.g. JPG) flatten onto background without revealing hidden content",
                "exif_label": "Apply EXIF orientation",
                "exif_info": "Phone photos are auto-rotated per orientation tag (otherwise exports may look rotated vs input)",
                "bitdepth_label": "Normalize non-8-bit depth",
                "bitdepth_info": "16-bit/float inputs are mapped to 8-bit (otherwise inpaint would fail on them)",
                "preserve_label": "Touch watermark area only (recommended for fidelity)",
                "follow_fmt_label": "Output format follows input",
                "follow_fmt_info": "JPEG in → JPEG out, WebP in → WebP out; measured output size drops from "
                                   "4.65x input to 1.13x/1.00x. Costs one extra lossy encode. "
                                   "Off = always PNG (lossless, larger).",
                "preserve_info": "Outside the watermark mask (plus an 8px guard band) the output is "
                                 "**byte-identical** to the input; measured collateral dropped from "
                                 "~50% to ~1% of pixels with no loss in removal. Off keeps the "
                                 "current whole-image light-sharpen look.",
                "save_label": "Save as default (writes config.user_overrides.json)",
                "save_info": "Merged write; does not affect superres/retention keys",
                "apply": "Apply preferences",
                "msg_applied": "Preferences applied — dual-run: {dual}; alpha: {alpha}; EXIF: {exif}; bit-depth: {depth}; watermark-area-only: {preserve}; format follows input: {fmt}",
            }, "real_superres": {
                "title": "### 🔍 Real Super-Resolution",
                "title": "### 🔍 Real Super-Resolution",
                "enabled_label": "Enable Real Super-Res",
                "enabled_info": "Prefer AI model upscaling (e.g. 4×) when on; auto-falls back to light super-res if backend unavailable (no torch / no weights).",
                "scale_label": "Upscale Factor",
                "scale_info": "Output size multiplier for real super-res (1~8, mainstream weights 4×).",
                "backend_label": "Super-Res Backend",
                "backend_info": "auto=try Real-ESRGAN→ESRGAN; esrgan=pure torch RRDB; realesrgan=official weights.",
                "save_label": "💾 Save as Default",
                "save_info": "When checked, clicking Apply writes the three knobs to config.user_overrides.json so they take effect next launch.",
                "apply": "▶️ Apply Real Super-Res",
            },
            "cache_policy": {
                "title": "### 🗂 History Retention Policy (Cache Retention)",
                "keep_days_label": "Keep Days",
                "keep_days_info": "Records older than this are auto-pruned when processing new images or on restart (1~3650 days).",
                "max_records_label": "Max Records",
                "max_records_info": "Oldest records are auto-deleted once the cap is exceeded (10~5000 records).",
                "max_bytes_label": "Total Size Cap (GB)",
                "max_bytes_info": "When total history size exceeds this cap, oldest records are auto-deleted until below it (1~100 GB). Video / batch results are often huge, so a sensible size cap prevents filling the disk.",
                "save_label": "💾 Save as Default",
                "save_info": "When checked, clicking Apply writes the retention policy to config.user_overrides.json so it takes effect next launch; does not overwrite the real super-res default.",
                "apply": "▶️ Apply & Prune",
            },
            # Stage 48: orphan file scan & purge (disk governance closure).
            "disk_clean": {
                "title": "### 🧹 Disk Cleanup (Orphan File Reclaim)",
                "hint": "Deleting a history record only removes its thumbnail while keeping the finished image, and uploaded originals are never reclaimed — so the cache directory slowly fills with unreferenced orphan files. This panel offers a safe \"scan first, confirm, then delete\" path. `outputs/` and `exports/` are user-facing result directories and are never touched.",
                "scan_btn": "🔍 Scan for Orphan Files",
                "src_thumbs": "Clean thumbnails (cache/thumbs)",
                "src_uploads": "Clean original copies (cache/uploads)",
                "clean_btn": "🧹 Purge Selected Orphans",
                "confirm_label": "⚠️ I have reviewed the list and confirm purging",
                "line": "- **{label}**: {count} file(s) · reclaimable {size}",
                "total": "\n**Total**: {count} orphan file(s), reclaimable **{size}**",
                "none": "✅ Cache directory is clean — no orphan files found.",
                "need_scan": "ℹ️ Please click \"Scan for Orphan Files\" to view the list first.",
                "need_select": "⚠️ Select at least one cleanup source and tick the confirmation box.",
                "confirm_needed": "⚠️ Please tick \"I have reviewed the list\" before purging.",
                "ok": "✅ Purged {count} orphan file(s), reclaimed {size}.",
                "partial": "⚠️ Purged {count} orphan file(s) (reclaimed {size}); {failed} skipped because they are in use or permission was denied.",
                "fail": "❌ Purge failed: {exc}",
            },
            "snap": {
                "title": "### 📋 Runtime Configuration",
                "app": "**App**: {name} v{version}",
                "device": "**Device Mode**: `{mode}` (auto=prefer GPU, else CPU)",
                "engine": "**Repair Engine**: `{engine}` (auto=LaMa if model, else OpenCV)",
                "concurrency": "**Batch Concurrency**: `{n}`",
                "cache": "**Cache**: `{state}` (keep {days} days / max {max} records / total cap {cap_gb} GB)",
                "format": "**Default Export**: `{fmt}` (quality {q})",
                "out": "**Output Dir**: `{path}`",
                "export_dir": "**Export Dir**: `{path}`",
                "cache_dir": "**Cache Dir**: `{path}` (deleting it only loses history list, not results)",
                "listen": "**Listen**: `{host}:{port}`",
                "real_superres_title": "### 🔍 Real Super-Res",
                "enabled": "**Enabled**: `{state}`",
                "scale": "**Upscale**: `{scale}×`",
                "backend": "**Backend**: `{backend}`",
                "interact_title": "### ⌨️ Interaction Enhancements (Stage 15)",
                "drag": "**Global Drag-Drop**: `{state}` (drop a file anywhere to fill current tab's upload)",
                "shortcut": "**Shortcuts**: `{state}` (Shift+R repair / Shift+D detect / Shift+B batch / Shift+C cancel / Shift+F refresh)",
                "compare": "**Compare View**: `{state}` (single/auto show Before|After; batch/history toggle thumbnails)",
                "restart": "> Theme / device mode require editing `.env` / `config.py` then **restart**.",
                "msg": {
                    "export_zip_ok": "✅ Exported {n} history result(s) as ZIP:\n`{path}`",
                    "compare_zip_ok": "✅ Generated and packaged {n} comparison image(s):\n`{path}`",
                    "err": "❌ Export failed: {exc}",
                    "no_compare": "⚠️ No history records available for comparison images (both original and result must exist).",
                    "compare_all_fail": "❌ All comparison images failed to generate; see the log.",
                    "zip_fail": "❌ Packaging failed: {exc}",
                    "clear_ok": "🧹 Cleared {n} history record(s) (result images kept).",
                    "prune_ok": "🧼 Pruned {n} expired/over-limit record(s).",
                    "superres_applied": "✅ Real super-res applied: enabled={state}, scale={scale}×, backend={backend}.",
                    "cache_applied": "✅ Retention policy applied: keep {days} days / max {max} records / total cap {bytes}; pruned {n} record(s) by policy.",
                },
            },
        },
        "single": {
            "editor_label": "① Upload & Paint Watermark (drag here, or click to upload)",
            "params_accordion": "② Repair Parameters (tweak live)",
            "run": "③ Start Removing Watermark",
            "result_title": "### ④ Result Comparison (click to zoom)",
            "orig_label": "Original",
            "result_label": "Result",
            "compare_label": "Original | Result (Before | After)",
            "status_label": "Status / Log",
            "status_placeholder": "Processing info and logs will appear here……",
            "download_label": "⑤ Download Result",
            "msg": {
                "no_image": "⚠️ Please drag or upload an image to the editor on the left.",
                "too_large": "⚠️ Image too large: {exc}",
                "bad_data": "⚠️ Invalid image data: {exc}",
                "mask_fail": "⚠️ Cannot parse the painted region: {exc}",
                "no_mask": "⚠️ No painted/selected region detected.\nUse the brush to paint the watermark, or select it with the rectangle tool, then retry.",
                "no_model": "❌ {exc}\nRun download_model.py first, or switch the engine to opencv.",
                "fail": "❌ Processing failed: {exc}",
                "done": "✅ Done (engine: {engine}).\nSaved: {path}\nClick 'Download Result' below, or right-click the image to save.",
                # 阶段 190：与 zh-CN 同口径（修复 + 增强；不含涂抹解析与落盘）。
                "elapsed": "\n⏱️ Repair + enhancement took {seconds:.2f} s",
                "save_fail": "⚠️ Repaired successfully, but auto-save failed: {exc}\nYou can right-click the result to save it.",
            },
        },
        "auto": {
            "upload_label": "① Upload Image (drag or click)",
            "sample_btn": "🖼 No image? Try a sample",
            "sample_loaded": "Sample loaded (has a \"StudioAI\" badge in the bottom-right corner) — "
                             "next click \"② Detect Watermark\", then \"④ One-click Repair\".",
            "sample_fail": "Failed to load the sample ({exc}); please upload your own image instead.",
            "detect_accordion": "② Detect Parameters (tweak then re-detect)",
            "detect_mode_label": "Detect Mode",
            "detect_mode_info": "Pick a preset tuned for common AI-generated watermark styles to boost detection at one click (manual tweaks below still apply)",
            "sensitivity_label": "Translucent Detect Sensitivity",
            "sensitivity_info": "Higher = catches fainter translucent watermarks, but more false positives",
            "en_translucent": "Detect Translucent Watermarks",
            "en_corner": "Detect Corner Logos",
            "en_edge": "Detect Edge Text",
            "detect_btn": "② Auto-Detect Watermark",
            "detect_preview_label": "Detection Preview (red=watermark, box=category)",
            "editor_label": "③ Manual Tuning (brush to add / eraser to remove)",
            "repair_accordion": "④ Repair Parameters",
            "repair_btn": "④ One-Click Repair",
            "result_title": "### ⑤ Result Comparison (click to zoom)",
            "orig_label": "Original",
            "result_label": "Result",
            "compare_label": "Original | Result (Before | After)",
            "status_label": "Status / Log",
            "status_placeholder": "Detection and repair info will appear here……",
            "download_label": "⑥ Download Result",
            "msg": {
                "no_upload": "⚠️ Please upload an image first (drag or click the upload box).",
                "detect_fail": "❌ Auto-detection failed: {exc}\nSwitch to the 'Single Image' tab to mark manually.",
                "no_detect": "ℹ️ No obvious watermark detected.\nPaint the watermark with the brush in the editor below, then click '④ One-Click Repair'.",
                "ocr_on": "(OCR text enhancement enabled)",
                "ocr_off": "(OCR off, using OpenCV native detection; install EasyOCR for better text watermarks)",
                "detect_done": "✅ Detection done: found {n} suspected region(s) {ocr}.\nAdd/erase in the editor below, then click '④ One-Click Repair'.\n(Left: preview, red=watermark, box=category: orange=translucent / cyan=corner logo / purple=edge text / green=OCR text)",
                "no_editor": "⚠️ Please upload an image and run detection (or paint the watermark) first.",
                "no_mask": "⚠️ No watermark region marked yet.\nClick '② Auto-Detect' first, or paint the watermark in the editor, then retry.",
                "auto_fallback": "\nℹ️ Editor is empty; ran auto-detection with default preset as fallback.",
                "refine_stats": "\n· Refine stats: detection {det:,} px / editor {user:,} px / you added {added:,} px.",
                "upscaled": "\n· Real super-res enabled: output upscaled to {factor}× (resolution boosted, lightweight SR may have been used as fallback).",
            },
        },
        "mask_edit": {
            "mode_label": "Mask Refine Mode",
            "mode_info": "Merge=detection ∪ your refinement (recommended: covers misses & false alarms); Manual=only your own strokes (auto-strips the detection layer)",
            "mode_merge": "Merge (detection + refine)",
            "mode_manual": "Manual (paint only)",
            "reload_btn": "🔄 Reload Detection Mask",
            "reload_info": "Re-fill the editor with the latest detection mask (restore after over-erasing / clearing)",
            "clear_btn": "🧹 Clear Editor",
            "clear_info": "Remove all layers and start painting from a blank canvas",
        },
        "batch": {
            "import_title": "### ① Import Images (folder / multiple files)",
            "folder_label": "Import Whole Folder (click to choose folder)",
            "files_label": "Or drop multiple images (Ctrl/Shift to multi-select)",
            "mode_label": "Batch Mode",
            "mode_info": "Auto=detect & repair each; Manual=read masks from a same-name mask folder",
            "mode_auto": "Smart Auto-Detect",
            "mode_manual": "Manual Mask Folder",
            "detect_mode_label": "Detect Mode",
            "detect_mode_info": "Pick a preset tuned for common AI-generated watermark styles to boost detection (ignored in manual mask mode)",
            "auto_preset_label": "Smart Preset Per Image",
            "auto_preset_info": "Analyze and pick the best detect preset for each image independently; when ON, the 'Detect Mode' dropdown below is ignored",
            "log_preset": "    🤖 Recommended preset: {preset}",
            "log_preset_reason": "    🤖 Recommended: {reason}",
            "log_preset_dist": "🤖 Recommended preset distribution: {dist}",
            "mask_folder_label": "② Mask Folder (only for Manual mode: same-name grayscale masks)",
            "params_accordion": "③ Repair Parameters (shared by this batch)",
            "evaluate_label": "Quality Evaluation After",
            "evaluate_info": "Score each result; gallery captions show score & warnings (⚠️ review / 🔍 attention)",
            "compare_label": "Compare View",
            "compare_info": "Gallery thumbnails become original | result merges for quick comparison",
            "run": "④ Start Batch",
            "cancel": "Cancel",
            "progress_title": "### ⑤ Progress & Results",
            "status_label": "Current Progress",
            "status_init": "🔄 Processing:**—**  \nDone 0 / Failed 0 / Cancelled 0 / Total 0",
            "bar_label": "Overall Progress",
            "log_label": "Processing Log (success / failure details)",
            "log_placeholder": "Logs for each image will scroll here once batch starts……",
            "gallery_label": "Results (click to zoom, right-click to save)",
            "report_title": "### ⑥ Export This Batch Report",
            "report_fmt_label": "Report Format",
            "report_fmt_info": "HTML self-contained (images embedded); PDF by PyMuPDF",
            "report_btn": "📄 Export This Report",
            "report_file_label": "Report File (click to download)",
            "params_accordion": "③ Repair Parameters (shared by this batch)",
            "status_running": "🔄 Processing: **{current}**  \nDone {done} / Failed {failed} / Cancelled {cancelled} / Total {total}",
            "start": "🚀 Batch started: {total} image(s) (rejected {rejected}).",
            "summary": "🏁 Batch finished: success {done} / failed {failed} / cancelled {cancelled} / total {total}.",
            "log_done": "  ✅ {name}: {status}",
            "log_fail": "  ❌ {name}: {error}",
            "log_cancel": "  ⏹ {name}: cancelled",
            "task_reading_mask": "Image loaded; preparing mask…",
            "task_cache_hit_msg": "Cache hit; reused",
            "task_cache_hit_status": "✅ Cache hit; reused a previous result",
            "task_mask_read": "Manual mask loaded",
            "task_saving": "Saved; registering cache…",
            "task_done": "✅ Done",
            "task_skip": "Skipped ({reason})",
            "task_read_fail": "Read failed: {reason}",
            "task_no_mask_file": "No same-name mask file found ({name})",
            "task_no_region": "No watermark region detected (empty auto-detect, or mask missing)",
            "task_no_result": "Repair produced no result",
            "task_detect_done": "Auto-detect done ({n} regions)",
            "task_detect_done_cov": "Auto-detect done ({n} regions, coverage {cov}%)",
            "list_sep": ", ",
            "progress_queue": "Queued {n} images",
            "progress_done": "Done {done}/{total}",
            "caption_quality": "{name} · Quality {score}{icon}",
            "log_quality": "    🔎 Quality: {name} score {score} ({level}) — {detail}",
            "report_ok": "✅ Report exported: {path}",
            "export_err": "❌ Export failed: {exc}",
            "msg": {
                "no_files": "⚠️ No image files selected (import a folder or drop images).",
                "need_mask": "⚠️ Manual mask mode requires a 'Mask Folder'.",
                "no_tasks": "⚠️ No processable images ({rejected} rejected).",
                "cancel_done": "⏹ Cancel signal sent; unstarted tasks will be skipped (in-flight tasks finish safely).",
                "cancel_none": "ℹ️ No batch is currently running.",
                "report_empty": "⚠️ No batch results to export yet (run a batch first).",
                "elapsed": "⏱️ Batch took {seconds:.2f} s",
            },
        },
        "history": {
            "title": "### 📁 Processing History (auto-archived, no re-upload needed)",
            "compare_label": "Compare View",
            "compare_info": "Thumbnails become original | result merges (click refresh after toggling)",
            "refresh": "🔄 Refresh",
            "clear": "🧹 Clear All",
            "report_fmt_label": "Report Format",
            "report_fmt_info": "HTML self-contained (images embedded); PDF by PyMuPDF",
            "report_btn": "📄 Export Repair Report",
            "report_file_label": "Report File (click to download)",
            "gallery_label": "History Thumbnails (click for details)",
            "detail_label": "Record Details",
            "video_preview_label": "Video Preview",
            "download_label": "Download This Result",
            "reprocess_accordion": "Reprocess Parameters",
            "reprocess_btn": "♻️ Reprocess Selected",
            "delete_btn": "🗑 Delete Selected",
            "delete_filtered_btn": "🗑🗑 Delete Filtered Results",
            "delete_filtered_confirm": "⚠️ I confirm deletion (tick, then click the button)",
            "info_empty": "(no records yet — process a few in batch or single tab)",
            "count": "{n} record(s).",
            "tip_select": " Click a thumbnail for details.",
            "not_found": "History record not found (it may have been cleaned up).",
            "reprocess_done": "✅ Reprocessed {name} ({status}).",
            "reprocess_no_result": "⚠️ Reprocessing produced no result.",
            "filter_label": "Type Filter",
            "filter_info": "Filter history by result type (All / Image / Video / PDF)",
            "filter_all": "All",
            "filter_image": "Image",
            "filter_video": "Video",
            "filter_pdf": "PDF",
            "export_filtered_btn": "📦 Export Filtered (ZIP)",
            "export_filtered_file_label": "Filtered Archive (click to download)",
            "export_filtered_ok": "✅ Packed by filter: {path}",
            "export_filtered_empty": "⚠️ No exportable success results for filter ({kind}).",
            "search_label": "🔍 Search by Filename",
            "search_placeholder": "Type a keyword to fuzzy-match filenames (case-insensitive)…",
            "search_info": "Filter history by filename; stacks with the Type Filter above.",
            "filter_active": "\n\n🔎 Active filter: type `{kind}` · keyword `{q}` · {n} match(es)",
            "clear_ok": "🧹 Cleared {n} history record(s) (result images kept).",
            "delete_filtered_ok": "✅ Deleted {n} record(s) under the current filter.",
            "delete_filtered_empty": "⚠️ No deletable records under the current filter.",
            "delete_filtered_warn": "⚠️ Tick the \"I confirm\" checkbox first, then click delete.",
            "prune_policy_btn": "🧹 Prune Expired by Policy",
            "prune_policy_confirm": "⚠️ I confirm pruning by retention policy (tick, then click the button)",
            "prune_policy_ok": "✅ Pruned {n} expired/over-limit record(s) by policy.",
            "prune_policy_none": "ℹ️ No expired/over-limit records to prune by policy right now.",
            "disk_usage": {
                "title": "### 💽 History Disk Usage",
                "total": "**Total**: {total} ({count} record(s)) | cap {cap}",
                "kind": "- {kind}: {count} record(s) · {size}",
            },
            "prune_size_btn": "💽 Prune by Size Cap",
            "prune_size_ok": "✅ Pruned {n} record(s) by size cap.",
            "prune_size_none": "ℹ️ Total usage is under the cap; no size-based pruning needed.",
            "msg": {
                "no_select": "Please click to select a record first.",
                "deleted": "✅ Record deleted.",
                "delete_fail": "⚠️ Delete failed or record not found.",
                "confirm_needed": "⚠️ Tick the \"I confirm\" checkbox first, then click delete.",
                "no_original": "⚠️ Original lost, cannot reprocess (re-import the image).",
                "reprocess_no_mask": "⚠️ Re-detection found no watermark, cannot repair.",
                "reprocess_fail": "❌ Reprocess failed: {exc}",
            },
            "detail": {
                "title": "### {icon} {name}",
                "id": "**Record ID**: `{id}`",
                "time": "**Time**: {time}",
                "mode_engine": "**Mode / Engine**: {mode} / {engine}",
                "status": "**Status**: {status}",
                "params": "**Params**: {params}",
                "result": "**Result Path**: {result}",
                "size": "**Result Size**: {size}",
                "video": "**Video**: {w}×{h} · {fps:.0f}fps · {frames} frames · {dur} · {upscaled}",
                "video_hint": "> 🎬 Video records support preview & download only; to reprocess, re-upload on the 🎬 Video tab.",
                "pdf": "**PDF**: {pages} pages · result size {size_mb} MB",
                "pdf_hint": "> 📄 PDF records support preview & download only; to reprocess, re-upload on the 📄 PDF tab.",
            },
        },
        "media": {
            "title": (
                "### 📄🎬 Process PDF Documents & Videos\n"
                "How it works: treat **every page** of a document and **every frame** of a "
                "video as a single image, remove watermarks per-image, then stitch back.\n"
                "**Both are much slower than a single image — try a small file first.**"
            ),
            "pdf": {
                "tab": "📄 PDF Document",
                "file_label": "① Upload PDF Document",
                "info_init": "_No document uploaded._",
                "pages_label": "② Page Range",
                "pages_placeholder": "Empty = all pages; e.g. 1-5 or 1,3,7 or 2-4,9",
                "pages_info": "Pages counted from 1. Process only what you need to save time",
                "dpi_label": "③ Render DPI",
                "dpi_info": "150=recommended; 72=fast but blurry; 300=print but slow",
                "params_accordion": "④ Repair Parameters (shared by this doc)",
                "detect_mode_label": "Detect Mode",
                "detect_mode_info": "Pick a preset tuned for common AI-generated watermark styles to boost detection",
                "auto_preset_label": "🤖 Smart Preset Per Page",
                "auto_preset_info": "When on, every page of the PDF independently picks the best detection preset (recommended; off = whole doc uses the dropdown above)",
                "page_preset": "· Recommended: {preset}",
                "preset_dist": "🤖 Smart preset distribution: {dist}",
                "btn": "⑤ Start Processing PDF",
                "result_title": "### Result",
                "log_label": "Processing Log (per-page details)",
                "log_placeholder": "Each page's result shows here after clicking 'Start Processing PDF'……",
                "out_label": "Result Document (click to download)",
                "note": (
                    "> **Two notes about the output PDF**\n"
                    "> 1. Processed pages become images — text can no longer be selected or searched;\n"
                    "> 2. Unselected pages, and pages with no watermark found, are kept **as-is** with zero quality loss."
                ),
                "no_fitz": (
                    "❌ **PyMuPDF is not installed in this environment, PDF is unavailable.**\n\n"
                    "Run `pip install PyMuPDF` in the terminal, then restart this app."
                ),
                "read_fail": "❌ Read failed: {error}",
                "read_exc": "❌ Error reading document info: {exc}",
                "progress_desc": "Preparing to process PDF……",
                "no_file": "⚠️ Please upload a PDF document first.",
                "invalid": "❌ File validation failed: {exc}",
                "loaded": (
                    "✅ **Loaded:** {name}\n\n"
                    "- Total pages: **{pages}**\n"
                    "- Page size: {size}\n"
                    "- Title: {title}\n"
                    "- Size: {mb} MB\n"
                    "- Estimated time: about {est} (reference only, depends on your PC)\n\n"
                    "> At most {max} pages per run; the rest are kept as-is."
                ),
                "done": "✅ Done: {name}",
                "page_detail_ok": "(watermark pixels {pixels})",
                "page_detail_err": "({error})",
                "per_page": "Per-page details:",
                "tip": (
                    "\n💡 Tip: output PDF pages are now images (text not selectable) — "
                    "the inherent cost of 'full-page redraw'. Use a page range for a few pages."
                ),
                "fail": "❌ Processing failed: {error}",
                "exc": "❌ Exception during processing: {type}: {exc}",
            },
            "video": {
                "tab": "🎬 Video",
                "file_label": "① Upload Video",
                "info_init": "_No video uploaded._",
                "step_label": "② Frame Step",
                "step_info": "Re-detect watermark every N frames. Fixed logos (3~5) speed up a lot",
                "max_label": "③ Max Frames",
                "max_info": "Safety cap. 30fps video: 300 frames ≈ 10s",
                "reuse_label": "Reuse Last Frame's Position (recommended)",
                "reuse_info": "Check for fixed watermarks; uncheck for scrolling/crawling text and set step to 1",
                "params_accordion": "④ Repair Parameters (shared by this video)",
                "detect_mode_label": "Detect Mode",
                "detect_mode_info": "Pick a preset tuned for common AI-generated watermark styles to boost detection",
                "auto_preset_label": "🤖 Smart Preset Per Frame",
                "auto_preset_info": "When on, every detect-frame independently picks the best preset (recommended; auto-disabled when 'reuse position' is checked)",
                "preset_dist": "🤖 Smart preset distribution: {dist}",
                "recommend_conf": "🤖 Smart preset average confidence: {conf:.0%}",
                "upscaled": "✅ Real super-res enabled — output resolution upscaled **{scale}×** ({w}×{h})",
                "compare_title": "### 🔍 Frame Compare View",
                "compare_frame_label": "Frame index",
                "compare_frame_info": "Pick the original / result frame to compare (0 = first; clamped to video length)",
                "compare_no_video": "⚠️ Process a video first to generate a result, then run frame compare.",
                "compare_no_frame": "⚠️ Cannot read that frame (may exceed video length).",
                "browse_title": "### 🎞️ Frame Browser",
                "browse_info": "After processing, a few frames are sampled automatically so you can browse the result without playing the video; click any frame to load it into the compare panel below.",
                "browse_empty": "⚠️ Sampled frames appear here after processing a video.",
                "export_frame_label": "⑦ Export Frame Compare",
                "export_sheet_label": "⑧ Export Contact Sheet",
                "export_no_video": "⚠️ Process a video first to generate a result, then export.",
                "export_done": "✅ Exported: {path}",
                "btn": "⑤ Start Processing Video",
                "result_title": "### Result",
                "log_label": "Processing Log",
                "log_placeholder": "Processing stats show here after clicking 'Start Processing Video'……",
                "out_label": "Result Video (preview / download)",
                "ffmpeg_label": "🔊 Want the original audio back? Copy this command to the terminal (ffmpeg required)",
                "ffmpeg_placeholder": "A ready command appears here after processing……",
                "note": (
                    "> **Three notes about video**\n"
                    "> 1. **Slow**: one frame is one image; 300 frames = 300 images, please wait;\n"
                    "> 2. **No sound**: output has no audio track; merge with the ffmpeg command above;\n"
                    "> 3. **Frame step** does NOT drop frames — all frames are written, "
                    "only middle frames reuse the last found position."
                ),
                "no_file": "⚠️ Please upload a video first.",
                "invalid": "❌ File validation failed: {exc}",
                "read_fail": "❌ Read failed: {error}",
                "read_exc": "❌ Error reading video info: {exc}",
                "progress_desc": "Preparing to process video……",
                "loaded": (
                    "✅ **Loaded:** {name}\n\n"
                    "- Resolution: **{w} × {h}**\n"
                    "- FPS: {fps} fps\n"
                    "- Total frames: **{frames}** ({dur}s)\n"
                    "- Size: {mb} MB\n"
                    "- This run: {plan} frames, about {est} (reference; frame reuse shortens it){warn}\n\n"
                    "> 🔇 Output video has **no sound** (OpenCV ignores audio); "
                    "merge with ffmpeg below if needed."
                ),
                "warn": (
                    "\n\n> ⚠️ Video has {frames} frames, exceeding the {max} cap; "
                    "**only the first {max} frames** are processed, so output is shorter."
                ),
                "done": "✅ Done: {name}",
                "spec": "Output: {w}x{h} · {fps}fps · codec {codec}",
                "warn_title": "Notes:",
                "fail": "❌ Processing failed: {error}",
                "exc": "❌ Exception during processing: {type}: {exc}",
            },
            "msg": {
                "elapsed": "⏱️ Processing took {seconds:.2f} s",
            },
        },
        "models": {
            "title": "### 🧠 Model Manager",
            "subtitle": (
                "Manage repair model weights: switch active model, download weights, verify "
                "integrity, roll back to backup. Multiple .pt weights share the LaMa interface; "
                "drop a file in `models/` and it appears automatically."
            ),
            "list_label": "Model Slots",
            "avail": "✅ Ready",
            "missing": "❌ Missing",
            "size_unknown": "Size unknown",
            "expected_mb": "Expected {mb} MB",
            "loaded_tag": " (loaded)",
            "slot_line": "-{mark} **{name}** —— {display} | {avail} | {size}{loaded}",
            "select_label": "Select Model Slot",
            "select_info": "Pick the model to operate on (or click 'Set Active' to switch)",
            "refresh": "🔄 Refresh List",
            "set_active": "⭐ Set Active",
            "download": "⬇️ Download Selected",
            "download_start": "Preparing to download model: {target}",
            "download_progress": "{done} / {total} MB | {kbps} KB/s",
            "verify": "🔍 Verify Integrity",
            "verify_pass": "Passed ✅",
            "verify_fail": "Failed ❌",
            "rollback": "↩️ Roll Back to Backup",
            "read_fail": "⚠️ Failed to read model list: {exc}",
            "op_fail": "⚠️ Operation failed: {exc}",
            "empty": "No model slots available.",
            "list_title": "### 🧠 Model Slots (* = active)",
            "active": "Active model (used by LaMa engine): **{name}**",
            "refresh_done": "🔄 Model list refreshed.",
            "no_slot": "⚠️ Please select a model slot first.",
            "set_active_done": "✅ Active model switched to: **{name}** (LaMa engine will use its weights, persisted).",
            "set_active_fail": "⚠️ Model slot not found: {name}",
            "download_done": "✅ Model **{target}** downloaded and verified:\n`{path}`",
            "download_fail": "❌ Download failed: {exc}\nIf usable weights existed, auto-rolled back to backup; or place weights manually in models/.",
            "verify_done": "🔍 Verify [{name}]: **{status}**\n\n> {reason}",
            "rollback_done": "↩️ Model **{target}** rolled back to previous backup weights.",
            "rollback_fail": "⚠️ Model **{target}** has no usable backup (never overwritten, or backup cleaned).",
            "status_title": "### 📊 Runtime Status",
            "status_device": "Inference device: {name} ({kind})",
            "status_device_gpu": "🚀 GPU acceleration",
            "status_device_cpu": "💻 CPU inference",
            "status_device_line": "- {line} | {label}",
            "status_vram": "VRAM: {vram} MB",
            "status_fp16": "Half precision (FP16): {state}",
            "status_fp16_on": "Enabled",
            "status_fp16_off": "Disabled",
            "status_active_path": "Active weights path: `{path}`",
            "status_loaded": "Memory cache: {n} model(s) loaded (current model {state})",
            "status_loaded_yes": "in memory",
            "status_loaded_no": "not loaded",
            "reload": "🔄 Clear Cache & Reload",
            "reload_done": "✅ Cleared model cache and reloaded current model **{name}** (in memory: {state}).",
            "reload_loaded_yes": "yes",
            "reload_loaded_no": "no",
            "reload_fail": "❌ Reload failed: {error}",
            "detail": "📄 Model Detail",
            "detail_done": (
                "📄 Detail [{name}]:\n\n"
                "> Path: `{path}`\n"
                "> Size: {size} MB\n"
                "> Modified: {mtime}\n"
                "> Expected SHA256: `{sha}`\n"
                "> Actual SHA256: `{sha_c}`\n"
                "> Status: **{status}**\n\n"
                "> {reason}"
            ),
            "detail_missing": "⚠️ Model **{name}** weights file does not exist; cannot show detail.",
        },
        "compare": {
            "enh_title": "### 🔬 Enhanced Compare View",
            "enh_mode_label": "Compare mode",
            "enh_mode_info": "Concat (static side-by-side) / Slider (drag reveal) / Heatmap (diff highlight)",
            "mode_concat": "Concat",
            "mode_slider": "Drag Slider",
            "mode_heatmap": "Diff Heatmap",
            "enh_btn": "🔄 Generate / Refresh Enhanced Compare",
            "no_result": "⚠️ Run a repair first to generate a result, then refresh the enhanced compare.",
            "slider_label": "Drag the divider to compare original (left) vs result (right)",
            "heatmap_label": "Difference heatmap: brighter = larger pixel change",
            # Stage 38: shared before/after viewer for batch & history tabs.
            "viewer": {
                "title": "### 🔍 Before / After Viewer",
                "hint": "Click any item in the gallery above to inspect its original vs result here.",
                "mode_label": "Compare mode",
                "mode_info": "Concat (side-by-side) / Slider (drag reveal) / Heatmap (diff highlight)",
                "btn": "🔄 Generate / Refresh Compare",
                "export_btn": "⬇ Export Compare Image (full resolution)",
                "file_label": "Compare image download",
                "no_select": "ℹ️ Nothing selected yet: click a result in the gallery first.",
                "no_pair": "⚠️ Original or result file is missing for this item (moved or deleted?).",
                "render_fail": "⚠️ Compare rendering failed; try the Concat mode (see log for details).",
                "export_ok": "✅ Compare image exported:\n`{path}`",
                "export_fail": "❌ Export failed: original or result file is missing.",
                "selected": "**Selected**: {name}",
                "summary": (
                    "Size `{w}×{h}` | Changed pixels `{ratio}` | "
                    "Mean diff `{mean}` | Peak `{mx}`"
                ),
                "summary_upscaled": " | Result upscaled to `{rw}×{rh}`",
                "summary_none": "(no diff statistics)",
                "video_hint": "🎬 For video records, use frame compare on the Media & Docs tab.",
                "pdf_hint": "📄 For PDF records, preview & download on the 📄 PDF tab of Media & Docs.",
            },
        },
        "lang": {
            "label": "UI Language",
            "info": "Switch UI language (fully applied after restart; current page refreshes on switch)",
            "zh": "中文（简体）",
            "en": "English",
            "applied": "✅ UI language switched to: {name} (some static text applies fully after restart).",
        },
        "scope": {
            "note": (
                "⚠️ **Scope**: this tool **commits to** {types}; "
                "it is **not committed to** {others} and similar styles — "
                "their presets remain selectable, but results are not guaranteed "
                "and are excluded from the quality gate conclusions."
            ),
        },
        # Stage 169: report deliverable (HTML/PDF repair report) strings, bilingual.
        # report_service.py resolves these via t() following the global language.
        "report": {
            "title": "{app_name} Repair Report",
            "summary_total": "Total <b>{n}</b> record(s)",
            "summary_avg": " · Avg. score <b>{avg}</b>",
            "note_no_quality": "This report does not include quality assessment (enable REPORT_INCLUDE_QUALITY in settings).",
            "quality_good": "Good",
            "quality_warn": "Warning",
            "quality_review": "Needs review",
            "quality_unassessed": "Not assessed",
            "unassessed_quality": "Quality not assessed",
            "alt_compare": "Comparison image",
            "quality_score": "Quality {score} · {level}",
            "lbl_mode_engine": "Mode/Engine: ",
            "lbl_time": "Time: ",
            "lbl_params": "Parameters: ",
            "lbl_name": "Name: ",
            "lbl_recommend": "AI Recommendation: ",
            "pdf_gen_time": "Generated: ",
            "pdf_total": "Total {n} record(s) processed",
            "pdf_quality_overview": "Quality overview: ",
            "pdf_avg_score": "Average quality score: ",
            "empty_records": "No successful records to export.",
            "err_png_encode": "PNG encoding failed",
            "err_no_records": "No exportable successful records (process some images first).",
            "log_html_done": "HTML report generated: {path} ({n} records)",
            "log_pdf_done": "PDF report generated: {path} ({n} records)",
            "log_compare_fail": "Report comparison image failed, skipping record: {exc}",
            "log_quality_fail": "Report quality evaluation failed, skipping item: {exc}",
            "log_pdf_img_fail": "PDF image insertion failed: {exc}",
            "log_pdf_fallback": "PyMuPDF not installed; PDF report fell back to HTML.",
        },
    },
}


# ------------------------------------------------------------------------------
# 运行时语言状态（模块级全局，进程内有效；持久化到磁盘 JSON）
# ------------------------------------------------------------------------------
_LANG: "str | None" = None
_STATE_FILE: "str | None" = None


def _state_path() -> str:
    """语言持久化文件路径（落在缓存目录下）。

    阶段 249 修**派生路径被全局缓存**缺陷：原实现把「由 config.CACHE_DIR 派生
    的路径」**永久缓存**进模块全局 `_STATE_FILE` ⇒ 一旦 CACHE_DIR 在运行中被改变
    （测试夹具即如此；任何「运行中改缓存目录」的场景亦然），后续读写会继续指向
    **旧**目录——状态文件写进随后被清理的位置且永不自愈（248 运行期检测器实测：
    批末 `_STATE_FILE` 残留第一个用例的 tmp 路径）。

    现语义：`_STATE_FILE` 只是**显式覆盖钩子**（默认 None = 跟随 CACHE_DIR 现算；
    测试/自检可显式赋值以重定向，monkeypatch 会自动还原）。派生路径现算成本
    可忽略（一次字符串拼接），缓存收益为零。
    """
    global _STATE_FILE
    if _STATE_FILE is not None:
        return _STATE_FILE       # 显式覆盖优先（测试 / 自检重定向用）
    d = getattr(config, "CACHE_DIR", None)
    if d is None:
        d = _os.path.join(_ROOT, "cache")
    try:
        _os.makedirs(d, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return _os.path.join(str(d), "i18n_state.json")


def available_languages() -> list:
    """返回所有支持的语言代码列表。"""
    return list(TRANSLATIONS.keys())


def get_language() -> str:
    """返回当前语言代码（未初始化则按优先级加载）。"""
    if _LANG is None:
        load_language()
    return _LANG or DEFAULT_LANG


def set_language(code: str) -> str:
    """设置当前语言代码（非法值忽略，保持原值）。"""
    global _LANG
    if code in TRANSLATIONS:
        _LANG = code
    else:
        log.warning("未知语言代码 '%s'，忽略。", code)
    return _LANG or DEFAULT_LANG


def load_language() -> str:
    """
    按优先级决定语言：环境变量 WME_UI_LANGUAGE > 持久化文件 > config.UI_LANGUAGE > 默认。

    返回值：最终生效的语言代码。
    """
    global _LANG
    env = _os.environ.get("WME_UI_LANGUAGE")
    if env in TRANSLATIONS:
        _LANG = env
        return _LANG
    try:
        p = _state_path()
        if _os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                saved = json.load(f).get("lang")
            if saved in TRANSLATIONS:
                _LANG = saved
                return _LANG
    except Exception:  # noqa: BLE001
        pass
    cfg = getattr(config, "UI_LANGUAGE", None)
    if cfg in TRANSLATIONS:
        _LANG = cfg
        return _LANG
    _LANG = DEFAULT_LANG
    return _LANG


def save_language(code: "str | None" = None) -> None:
    """把语言选择持久化到磁盘（下次启动自动生效）。

    阶段 137：非法语言代码拒绝落盘——此前 'fr-FR' 这类垃圾值会直接写进
    状态文件（load 侧虽有守卫不会生效，但垃圾文件永久残留且无任何告警）。
    """
    code = code or get_language()
    if code not in TRANSLATIONS:
        log.warning("拒绝持久化未知语言代码 '%s'（不写盘）。", code)
        return
    try:
        p = _state_path()
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"lang": code}, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        log.warning("保存语言偏好失败：%s", exc)


def t(key: str, **kwargs) -> str:
    """
    取**当前语言**的译文。

    参数：
        key  : 点号分层键，如 "app.tab.single"。
        kwargs: 可选格式化占位符（{name} 等）。

    返回值：译文字符串。缺失键时回退到 zh-CN，再回退为 key 本身。

    说明：
        等价于 `t_in(get_language(), key, **kwargs)`——两者**共用同一实现**，
        保证「取指定语言词」与「取当前语言词」拥有同一套缺失回退与占位符守卫
        （阶段 91：此前只有 t() 有守卫，直接下标 TRANSLATIONS 的调用点没有）。
    """
    return t_in(get_language(), key, **kwargs)


def t_in(lang: str, key: str, **kwargs) -> str:
    """
    取**指定语言**的译文（不改变当前语言状态）。

    参数：
        lang  : 语言代码（如 "zh-CN" / "en-US"）；未知语言自动回退 DEFAULT_LANG。
        key   : 点号分层键。
        kwargs: 可选格式化占位符。

    返回值：
        译文字符串；缺失时先回退 DEFAULT_LANG，再回退为 **key 本身**（不抛异常）。

    说明：
        阶段 91 新增。ui/app.py 构建「标签页标题映射」时需要在同一时刻取两种
        语言的同一键，此前直接下标 `TRANSLATIONS[lang][...][key]`，绕过了本
        函数的守卫——键一旦改名会在界面**构建期**抛 KeyError 使整个界面起不来，
        而不是降级为键名。
    """
    val = _lookup(TRANSLATIONS.get(lang, {}), key)
    if val is None:
        val = _lookup(TRANSLATIONS.get(DEFAULT_LANG, {}), key)
    if val is None:
        return key
    if isinstance(val, dict):
        # 命中的是结构节点（中间层字典）而非译文：按缺失处理返回 key，
        # 不把 dict 的 repr 泄漏给界面（阶段 137）。
        return key
    if not isinstance(val, str):
        return str(val)
    if kwargs:
        try:
            return val.format(**kwargs)
        except (KeyError, IndexError, ValueError) as exc:
            log.warning("翻译键 '%s' 格式化失败（%s），返回原文。", key, exc)
            return val
    return val


def _lookup(table: dict, key: str):
    """按点号键在嵌套字典中取值；不存在返回 None。"""
    cur = table
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _leaves(table: dict, prefix: str = "") -> dict:
    """展平嵌套语言包为 {点号键: 叶子值}。"""
    out: dict = {}
    for k, v in table.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_leaves(v, key))
        else:
            out[key] = v
    return out


def validate_i18n_parity() -> list:
    """
    语言包结构对称性校验（阶段 137）：把模块契约「中英文一一对应 + 占位符
    同名」变成机器可判定的构建期守卫——阶段 98 那类「EN 界面静默回退中文」
    此前只能靠零散断言，键一旦漂移会无声回归。

    返回问题列表（空 = 通过），三类：
      1. 仅单侧存在的叶子键（某语言界面会回退 / 另一侧无对应）；
      2. 叶子类型不一致（str vs dict 等）；
      3. {占位符} 集合不一致（缺失一侧 format 时 KeyError，虽被 t() 守卫
         捕获但译文会退化为原文）。
    新增语言自动纳入与首个语言的差集校验；供 __main__ 自检与守卫测试调用。
    """
    problems: list = []
    per_lang = {code: _leaves(tbl) for code, tbl in TRANSLATIONS.items()}
    codes = list(per_lang)
    if len(codes) < 2:
        return problems
    base = codes[0]
    base_map = per_lang[base]
    base_keys = set(base_map)
    for code in codes[1:]:
        cmap = per_lang[code]
        keys = set(cmap)
        for k in sorted(base_keys - keys):
            problems.append(f"[{code}] 缺失叶子键 {k}（{base} 正常、{code} 界面会静默回退）")
        for k in sorted(keys - base_keys):
            problems.append(f"[{base}] 缺失叶子键 {k}（仅 {code} 存在）")
        for k in sorted(base_keys & keys):
            vb, vc = base_map[k], cmap[k]
            if type(vb).__name__ != type(vc).__name__:
                problems.append(f"[{code}] {k} 类型不一致：{type(vb).__name__} vs {type(vc).__name__}")
            elif isinstance(vb, str):
                pb = set(_re.findall(r"\{([a-zA-Z_]\w*)\}", vb))
                pc = set(_re.findall(r"\{([a-zA-Z_]\w*)\}", vc))
                if pb != pc:
                    problems.append(
                        f"[{code}] {k} 占位符不一致：{base}={sorted(pb)} vs {code}={sorted(pc)}"
                    )
    return problems


if __name__ == "__main__":
    # 自检前显式锁定 zh-CN，避免受持久化语言状态影响（与运行期默认一致）。
    set_language("zh-CN")
    assert t("app.tab.single") == "单图处理（手动涂抹）"
    set_language("en-US")
    assert t("app.tab.single") == "Single Image (Manual)"
    assert t("not.exist.key") == "not.exist.key"
    assert t("single.msg.too_large", exc="x") == "⚠️ Image too large: x"
    set_language("fr-FR")
    assert get_language() == "en-US"
    set_language("zh-CN")
    # 阶段 20：检测模式 / 预设键。
    assert t("auto.detect_mode_label") == "检测模式"
    assert t("auto.detect_mode_info").startswith("针对 AI 生成图")
    # 阶段 21：批量页 / 媒体页检测模式键。
    for sec in ("batch", "media.pdf", "media.video"):
        assert t(f"{sec}.detect_mode_label") == "检测模式"
        assert t(f"{sec}.detect_mode_info").startswith("针对 AI 生成图")
    for name in ("general", "ai_tiled", "ai_signature", "ai_corner",
                 "ai_text_heavy", "ai_faint_tiled", "ai_grid", "ai_gradient",
                 "ai_logo", "ai_multi"):
        assert t(f"detect.preset.{name}.title") != f"detect.preset.{name}.title"
        assert t(f"detect.preset.{name}.desc") != f"detect.preset.{name}.desc"
    # 阶段 22：命中率展示键。
    for k in ("label", "placeholder", "coverage", "regions", "by_kind",
              "kind_sep", "ocr_on", "ocr_off", "none"):
        assert t(f"detect.stats.{k}") != f"detect.stats.{k}"
    for k in ("translucent", "corner_logo", "edge_text", "ocr_text",
              "grid_watermark", "gradient_watermark", "logo_watermark"):
        assert t(f"detect.kind.{k}") != f"detect.kind.{k}"
    # 阶段 24：掩码精修键（mask_edit.* + auto.msg 新增）。
    for k in ("mode_label", "mode_info", "mode_merge", "mode_manual",
              "reload_btn", "clear_btn"):
        assert t(f"mask_edit.{k}") != f"mask_edit.{k}", f"mask_edit.{k} 缺失翻译"
    assert t("mask_edit.mode_merge") == "合并（检测+精修）"
    assert t("auto.msg.auto_fallback").startswith("\nℹ️")
    assert "added" in t("auto.msg.refine_stats")
    # 阶段 25：自动预设推荐键（中英文）。
    for k in ("btn", "title", "msg.no_upload", "msg.bad_image", "msg.no_result",
              "msg.fail", "list_sep", "score_line", "scores_none",
              "feature.gradient", "feature.grid", "feature.tiled",
              "feature.translucent", "feature.solid_logo",
              "feature.corner_watermark", "feature.text",
              "feature.signature", "feature.regularity", "feature.none",
              "scores_title"):
        assert t(f"detect.recommend.{k}") != f"detect.recommend.{k}", \
            f"detect.recommend.{k} 缺失翻译（zh-CN）"
    assert t("detect.recommend.btn") == "🔍 智能推荐预设"
    assert t("detect.recommend.reason", preset="AI 平铺水印", conf=0.71, feats="平铺") == \
        "推荐 AI 平铺水印（置信度 71% · 触发 平铺）"
    # 阶段 98：渲染器行模板（英文侧不得残留中文标点，故两侧分开断言）。
    assert t("detect.recommend.score_line", name="平铺", bar="██", val="0.71") == \
        "  - 平铺：██ **0.71**"
    assert t("detect.recommend.list_sep") == "、"
    # 阶段 26：批量智能推荐键。
    for k in ("auto_preset_label", "auto_preset_info", "log_preset", "log_preset_dist"):
        assert t(f"batch.{k}") != f"batch.{k}", f"batch.{k} 缺失翻译（zh-CN）"
    assert t("batch.auto_preset_label") == "智能推荐每张图预设"
    # 阶段 27：媒体页逐页/逐帧智能推荐键。
    for k in ("auto_preset_label", "auto_preset_info", "page_preset", "preset_dist"):
        assert t(f"media.pdf.{k}") != f"media.pdf.{k}", f"media.pdf.{k} 缺失翻译（zh-CN）"
    for k in ("auto_preset_label", "auto_preset_info", "preset_dist", "recommend_conf",
              "upscaled", "compare_title", "compare_frame_label", "compare_frame_info",
              "compare_no_video", "compare_no_frame"):
        assert t(f"media.video.{k}") != f"media.video.{k}", f"media.video.{k} 缺失翻译（zh-CN）"
    set_language("en-US")
    assert t("detect.recommend.btn") == "🔍 Auto-Recommended Preset"
    assert t("detect.recommend.feature.text") == "Dense full-image text strokes"
    assert t("detect.recommend.reason", preset="AI Tiled Watermark", conf=0.71, feats="Tiled") == \
        "Recommended AI Tiled Watermark (confidence 71% · triggered Tiled)"
    # 阶段 98：英文侧同样必须有这 4 个渲染器键（否则 EN 界面回退到 zh 译文）。
    for k in ("msg.no_result", "list_sep", "score_line", "scores_none"):
        assert t(f"detect.recommend.{k}") != f"detect.recommend.{k}", \
            f"detect.recommend.{k} 缺失翻译（en-US）"
    # 阶段 137：结构对称性 + 占位符一致性（构建期守卫，562 叶子全量扫）。
    _problems = validate_i18n_parity()
    assert not _problems, "语言包结构校验失败：\n" + "\n".join(_problems[:10])
    # dict 中间层按缺失处理（不把 repr 泄漏给界面）。
    # 阶段 140 勘误：此处原用「app.tab」作探针键（阶段 137 引入），但
    #   `tests/test_i18n_coverage._scan_static_keys()` 会用正则扫描**整个**
    #   utils/i18n.py 里的静态 t() 调用（**注释同样会被扫到**），把自检探针误判成
    #   「生产代码引用了未定义键」→ 全量 pytest 红。该测试的 `_ALLOWED_UNKNOWN`
    #   早已为 i18n 自检登记了专用探针键，故改用它——语义等价（同样验证
    #   「不存在的键原样返回」，且 dict 中间层节点走的也是这条回退路径）。
    assert t("not.exist.key") == "not.exist.key"
    # 非法语言代码拒绝落盘（临时状态文件，不碰真实偏好；结束即删）。
    import shutil as _shutil
    import tempfile as _tempfile
    _tmp_state = _os.path.join(_tempfile.mkdtemp(prefix="i18n_selfcheck_"), "state.json")
    _STATE_FILE = _tmp_state  # 模块作用域赋值 = 重绑定全局，_state_path 读全局
    save_language("fr-FR")
    assert not _os.path.exists(_tmp_state), "非法语言代码不应落盘"
    save_language("en-US")
    assert _os.path.exists(_tmp_state), "合法语言代码应落盘"
    _shutil.rmtree(_os.path.dirname(_tmp_state), ignore_errors=True)
    _STATE_FILE = None
    print("i18n.py 自检全部通过。")
    assert t("detect.recommend.list_sep") == ", "
    assert t("detect.recommend.score_line", name="Tiled", bar="██", val="0.71") == \
        "  - Tiled: ██ **0.71**"
    assert t("detect.recommend.scores_none") == "(none)"
    set_language("zh-CN")
    # 阶段 21：画质增强深化键。
    for k in ("poisson_label", "poisson_strength_label", "texture_label",
              "texture_amount_label", "artifact_label", "artifact_amount_label",
              "superres_label", "superres_amount_label", "real_superres_label",
              "real_scale_label", "real_backend_label", "real_backend_info",
              "eval_label", "eval_info"):
        assert t(f"params.{k}") != f"params.{k}"
    assert t("params.superres_label") == "轻量超分（细节重建）"
    assert t("params.real_superres_label") == "真·超分（AI 模型放大）"
    assert t("params.real_scale_label") == "放大倍数"
    assert t("params.real_backend_label") == "超分后端"
    assert t("params.eval_label") == "显示后处理质量提升"
    set_language("en-US")
    assert t("auto.detect_mode_label") == "Detect Mode"
    for sec in ("batch", "media.pdf", "media.video"):
        assert t(f"{sec}.detect_mode_label") == "Detect Mode"
    assert t("batch.auto_preset_label") == "Smart Preset Per Image"
    assert t("batch.log_preset_dist") == "🤖 Recommended preset distribution: {dist}"
    for k in ("auto_preset_label", "auto_preset_info", "page_preset", "preset_dist"):
        assert t(f"media.pdf.{k}") != f"media.pdf.{k}", f"media.pdf.{k} 缺失翻译（en-US）"
    for k in ("auto_preset_label", "auto_preset_info", "preset_dist", "recommend_conf",
              "upscaled", "compare_title", "compare_frame_label", "compare_frame_info",
              "compare_no_video", "compare_no_frame"):
        assert t(f"media.video.{k}") != f"media.video.{k}", f"media.video.{k} 缺失翻译（en-US）"
    assert t("media.pdf.auto_preset_label") == "🤖 Smart Preset Per Page"
    assert t("media.video.preset_dist") == "🤖 Smart preset distribution: {dist}"
    assert t("detect.preset.ai_signature.title") == "AI Signature Bar"
    assert t("mask_edit.mode_merge") == "Merge (detection + refine)"
    assert t("mask_edit.reload_btn") == "🔄 Reload Detection Mask"
    assert t("auto.msg.refine_stats").startswith("\n· Refine stats:")
    # 阶段 30：模型管理 UI 增强键（状态面板 / 缓存热重载 / 详情）。
    set_language("zh-CN")
    for k in ("status_title", "status_device", "status_device_gpu", "status_device_cpu",
              "status_vram", "status_fp16", "status_fp16_on", "status_fp16_off",
              "status_active_path", "status_loaded", "status_loaded_yes", "status_loaded_no",
              "reload", "reload_done", "reload_loaded_yes", "reload_loaded_no", "reload_fail",
              "detail", "detail_done", "detail_missing",
              # 阶段 94：UI 异常统一入口（tab_models 六个 handler 共用）
              "op_fail"):
        assert t(f"models.{k}") != f"models.{k}", f"models.{k} 缺失翻译（zh-CN）"
    assert t("models.status_title") == "### 📊 运行状态"
    assert t("models.op_fail", exc="E") == "⚠️ 操作失败：E"
    assert t("models.reload") == "🔄 清空缓存并重新加载"
    assert t("models.detail_missing", name="x") == "⚠️ 模型 **x** 权重文件不存在，无法查看详情。"
    set_language("en-US")
    assert t("models.status_title") == "### 📊 Runtime Status"
    assert t("models.reload") == "🔄 Clear Cache & Reload"
    assert t("models.detail_missing", name="x") == \
        "⚠️ Model **x** weights file does not exist; cannot show detail."
    # 阶段 31：增强对比视图键（中英）。
    set_language("zh-CN")
    for k in ("enh_title", "enh_mode_label", "enh_mode_info", "mode_concat",
              "mode_slider", "mode_heatmap", "enh_btn", "no_result",
              "slider_label", "heatmap_label"):
        assert t(f"compare.{k}") != f"compare.{k}", f"compare.{k} 缺失翻译（zh-CN）"
    assert t("compare.enh_title") == "### 🔬 增强对比视图"
    assert t("compare.mode_slider") == "拖动滑块"
    # 阶段 38：前后对比查看器键（zh-CN）。
    for k in ("title", "hint", "mode_label", "mode_info", "btn", "export_btn",
              "file_label", "no_select", "no_pair", "render_fail", "export_ok",
              "export_fail", "selected", "summary", "summary_upscaled",
              "summary_none", "video_hint", "pdf_hint"):
        assert t(f"compare.viewer.{k}") != f"compare.viewer.{k}", \
            f"compare.viewer.{k} 缺失翻译（zh-CN）"
    assert t("compare.viewer.title") == "### 🔍 前后对比查看器"
    assert t("compare.viewer.selected", name="图.png") == "**当前条目**：图.png"
    assert "`/tmp/a.png`" in t("compare.viewer.export_ok", path="/tmp/a.png")
    # 阶段 35：设置页真实超分键（zh-CN）。
    assert t("settings.real_superres.title").startswith("### 🔍 真实超分")
    assert t("settings.real_superres.enabled_label") == "开启真实超分"
    assert t("settings.real_superres.scale_label") == "放大倍数"
    assert t("settings.real_superres.backend_label") == "超分后端"
    assert t("settings.real_superres.save_label") == "💾 保存为默认"
    assert t("settings.real_superres.apply") == "▶️ 应用真实超分设置"
    assert t("settings.snap.real_superres_title").startswith("### 🔍 真实超分")
    assert t("settings.snap.enabled").startswith("**总开关**")
    assert t("settings.snap.scale").startswith("**放大倍数**")
    assert t("settings.snap.backend").startswith("**后端**")
    assert t("settings.snap.msg.superres_applied").startswith("✅ 真实超分设置已应用")
    # 阶段 46/47：设置页历史保留策略键（zh-CN）。
    for k in ("title", "keep_days_label", "keep_days_info", "max_records_label",
              "max_records_info", "max_bytes_label", "max_bytes_info",
              "save_label", "save_info", "apply"):
        assert t(f"settings.cache_policy.{k}") != f"settings.cache_policy.{k}", \
            f"settings.cache_policy.{k} 缺失翻译（zh-CN）"
    assert t("settings.cache_policy.title").startswith("### 🗂 历史保留策略")
    assert t("settings.cache_policy.apply") == "▶️ 应用并清理"
    assert t("settings.snap.msg.cache_applied").startswith("✅ 历史保留策略已应用")
    assert "GB" in t("settings.snap.cache", state="on", days=30, max=200, cap_gb=5)
    # 阶段 48：设置页「磁盘清理（孤儿文件）」键（zh-CN）。
    for k in ("title", "hint", "scan_btn", "src_thumbs", "src_uploads", "clean_btn",
              "confirm_label", "line", "total", "none", "need_scan", "need_select",
              "confirm_needed", "ok", "partial", "fail"):
        assert t(f"settings.disk_clean.{k}") != f"settings.disk_clean.{k}", \
            f"settings.disk_clean.{k} 缺失翻译（zh-CN）"
    assert t("settings.disk_clean.title").startswith("### 🧹 磁盘清理")
    assert "3" in t("settings.disk_clean.line", label="缩略图", count=3, size="1.2 MB")
    assert "5" in t("settings.disk_clean.ok", count=5, size="2.0 MB")
    set_language("en-US")
    assert t("compare.enh_title") == "### 🔬 Enhanced Compare View"
    assert t("compare.mode_slider") == "Drag Slider"
    # 阶段 38：前后对比查看器键（en-US）。
    for k in ("title", "hint", "mode_label", "mode_info", "btn", "export_btn",
              "file_label", "no_select", "no_pair", "render_fail", "export_ok",
              "export_fail", "selected", "summary", "summary_upscaled",
              "summary_none", "video_hint", "pdf_hint"):
        assert t(f"compare.viewer.{k}") != f"compare.viewer.{k}", \
            f"compare.viewer.{k} 缺失翻译（en-US）"
    assert t("compare.viewer.title") == "### 🔍 Before / After Viewer"
    assert t("compare.viewer.selected", name="a.png") == "**Selected**: a.png"
    # 阶段 35：设置页真实超分键（en-US）。
    assert t("settings.real_superres.title").startswith("### 🔍 Real Super-Res")
    assert t("settings.real_superres.enabled_label") == "Enable Real Super-Res"
    assert t("settings.real_superres.scale_label") == "Upscale Factor"
    assert t("settings.real_superres.backend_label") == "Super-Res Backend"
    assert t("settings.real_superres.save_label") == "💾 Save as Default"
    assert t("settings.real_superres.apply") == "▶️ Apply Real Super-Res"
    assert t("settings.snap.real_superres_title").startswith("### 🔍 Real Super-Res")
    assert t("settings.snap.enabled").startswith("**Enabled**")
    assert t("settings.snap.scale").startswith("**Upscale**")
    assert t("settings.snap.backend").startswith("**Backend**")
    assert t("settings.snap.msg.superres_applied").startswith("✅ Real super-res applied")
    # 阶段 46/47：设置页历史保留策略键（en-US）。
    for k in ("title", "keep_days_label", "keep_days_info", "max_records_label",
              "max_records_info", "max_bytes_label", "max_bytes_info",
              "save_label", "save_info", "apply"):
        assert t(f"settings.cache_policy.{k}") != f"settings.cache_policy.{k}", \
            f"settings.cache_policy.{k} 缺失翻译（en-US）"
    assert t("settings.cache_policy.title").startswith("### 🗂 History Retention Policy")
    assert t("settings.cache_policy.apply") == "▶️ Apply & Prune"
    assert t("settings.snap.msg.cache_applied").startswith("✅ Retention policy applied")
    assert "GB" in t("settings.snap.cache", state="on", days=30, max=200, cap_gb=5)
    # Stage 48: settings "Disk Cleanup (orphan files)" keys (en-US).
    for k in ("title", "hint", "scan_btn", "src_thumbs", "src_uploads", "clean_btn",
              "confirm_label", "line", "total", "none", "need_scan", "need_select",
              "confirm_needed", "ok", "partial", "fail"):
        assert t(f"settings.disk_clean.{k}") != f"settings.disk_clean.{k}", \
            f"settings.disk_clean.{k} 缺失翻译（en-US）"
    assert t("settings.disk_clean.title").startswith("### 🧹 Disk Cleanup")
    assert "3" in t("settings.disk_clean.line", label="Thumbnails", count=3, size="1.2 MB")
    assert "5" in t("settings.disk_clean.ok", count=5, size="2.0 MB")
    # 阶段 43：历史记录批量删除 + 二次确认键（中英）。
    set_language("zh-CN")
    for k in ("delete_filtered_btn", "delete_filtered_confirm", "delete_filtered_ok",
              "delete_filtered_empty", "delete_filtered_warn"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（zh-CN）"
    assert t("history.msg.confirm_needed") != "history.msg.confirm_needed"
    # 阶段 46：历史页「按策略清理」键（zh-CN）。
    for k in ("prune_policy_btn", "prune_policy_confirm", "prune_policy_ok",
              "prune_policy_none", "prune_size_btn", "prune_size_ok", "prune_size_none"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（zh-CN）"
    # 阶段 47：历史页「磁盘占用」键（zh-CN）。
    for k in ("disk_usage.title", "disk_usage.total", "disk_usage.kind"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（zh-CN）"
    assert "MB" in t("history.detail.size", size="1.2 MB")
    set_language("en-US")
    for k in ("delete_filtered_btn", "delete_filtered_confirm", "delete_filtered_ok",
              "delete_filtered_empty", "delete_filtered_warn"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（en-US）"
    assert t("history.msg.confirm_needed") != "history.msg.confirm_needed"
    # 阶段 46：历史页「按策略清理」键（en-US）。
    for k in ("prune_policy_btn", "prune_policy_confirm", "prune_policy_ok",
              "prune_policy_none", "prune_size_btn", "prune_size_ok", "prune_size_none"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（en-US）"
    # 阶段 47：历史页「磁盘占用」键（en-US）。
    for k in ("disk_usage.title", "disk_usage.total", "disk_usage.kind"):
        assert t(f"history.{k}") != f"history.{k}", f"history.{k} 缺失翻译（en-US）"
    assert "MB" in t("history.detail.size", size="1.2 MB")
    set_language("zh-CN")
    # 阶段 79：修正「键名层级错位 / 缺失」后补齐的键（此前 14 处引用未定义键，
    # t() 静默返回键名 → 用户在设置页/历史页/对比模式选择处直接看到原始键名）。
    for _lang in ("zh-CN", "en-US"):
        set_language(_lang)
        for _k in ("settings.snap.msg.clear_ok", "settings.snap.msg.err",
                   "compare.mode_slider", "compare.mode_heatmap",
                   "history.count", "history.tip_select", "history.not_found",
                   "history.reprocess_done", "history.reprocess_no_result",
                   "auto.sensitivity_info"):
            assert t(_k, n=1, name="a", status="s", exc="e") != _k, \
                f"{_k} 缺失翻译（{_lang}）"
    set_language("zh-CN")
    # 阶段 95：适用范围提示。模板**必须**含 {types}/{others} 两个占位符——
    # 类型名由 config 注入，模板里出现字面类型名就等于又造了一份人读副本
    # （经验 51）。此处只守「占位符在」；「注入后说了什么」由
    # tests/test_watermark_scope95.py 双向断言。
    for _lang in ("zh-CN", "en-US"):
        set_language(_lang)
        _tpl = t("scope.note")
        assert _tpl != "scope.note", f"scope.note 缺失翻译（{_lang}）"
        for _ph in ("{types}", "{others}"):
            assert _ph in _tpl, f"scope.note（{_lang}）缺占位符 {_ph}：{_tpl}"
        _filled = t("scope.note", types="A（`x`）", others="B（`y`）")
        assert "A（`x`）" in _filled and "B（`y`）" in _filled, \
            f"scope.note（{_lang}）占位符未被替换：{_filled}"
    set_language("zh-CN")
    print("utils/i18n.py 自检通过。")
