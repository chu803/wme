# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : ui/theme.py
 文件说明 : 阶段 6 统一界面主题与样式。
            此前各标签页沿用 Gradio 默认外观，阶段 6 起通过本模块集中定义
            「品牌化主题」与「自定义 CSS」，让整个工作台视觉风格统一、专业。

            设计要点：
            1. 主题对象：基于 Gradio 内置 Soft 主题定制品牌色（蓝系主色 +
               天蓝辅色），并微调背景/卡片/主按钮配色。用 try/except 兜底，
               一旦定制项在当前 Gradio 版本不可用，自动回退到 Soft 默认主题，
               保证 build_app / launch_app 永不因主题参数而崩。
            2. 自定义 CSS：针对标题区、按钮圆角、画廊间距、日志框等做美化，
               纯装饰、不影响功能；所有选择器都限定在 .gradio-container 内，
               不会污染页面其它部分。
            3. Gradio 6.x 适配：主题与 CSS 通过 launch(theme=, css=) 注入
               （已从 Blocks 构造移除），详见 ui/app.py 的 launch_app()。
            4. 品牌色令牌：全部颜色（十六进制 + RGB + rgba）统一来自
               `utils.brand`，本文件与 ui/app.py、services/export_service.py
               一律通过 `brand.XXX` 在**调用时**读取，不再内联任何颜色字面量
               （阶段 91 前主色有 6 处副本、跨 3 个文件，改一处不全局生效且
               没有守卫）。
            5. 主题名解析：`resolve_theme_name()` 把 config.SERVER_THEME 解析为
               「品牌主题」或 Gradio 内置主题名；内置名白名单**从 Gradio 运行时
               注册表枚举**（`gradio.utils.BUILT_IN_THEMES`），不硬编码——硬编码
               会同时「拒绝合法主题」与「放行不存在的主题」，而后者会让
               `launch()` 走到 `Theme.from_hub()` 去 HuggingFace 联网下载。

            本文件属于「界面层」，可 import gradio。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

import sys as _sys
import os as _os

# ------------------------------------------------------------------------------
# 路径加固
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import gradio as gr

from utils import brand
from utils.logger import get_logger

log = get_logger("ui.theme")

#: 品牌定制主题的标识名。config.SERVER_THEME 取该值时走 build_app_theme()。
BRAND_THEME_NAME: str = "brand"


def build_app_theme():
    """
    构造应用主题对象。

    返回值：
        Gradio Theme 实例（定制版；定制失败则回退 Soft 默认主题）。

    说明：
        定制项用 try/except 包裹：不同 Gradio 小版本的主题键名可能略有差异，
        任何一项设置抛异常都不影响整体启动，仅降级为默认外观。
    """
    try:
        theme = gr.themes.Soft(
            primary_hue=gr.themes.colors.blue,
            secondary_hue=gr.themes.colors.sky,
            neutral_hue=gr.themes.colors.gray,
        ).set(
            # 品牌色令牌一律在调用时从 utils.brand 读取（便于哨兵测试替换）。
            body_background_fill=brand.PAGE_BG_HEX,
            body_background_fill_dark="#0f172a",
            block_background_fill="#ffffff",
            block_background_fill_dark=brand.INK_HEX,
            block_border_width="1px",
            block_border_color="#dbe3ee",
            button_primary_background_fill=brand.PRIMARY_HEX,
            button_primary_background_fill_hover=brand.PRIMARY_HOVER_HEX,
            button_primary_text_color="#ffffff",
            button_secondary_background_fill="#e2e8f0",
            button_secondary_background_fill_hover="#cbd5e1",
            border_color_primary="#cbd5e1",
            input_background_fill=brand.SURFACE_HEX,
            link_text_color=brand.ACCENT_HEX,
            slider_color=brand.PRIMARY_HEX,
        )
        return theme
    except Exception as exc:  # noqa: BLE001 - 主题定制不应阻断启动
        # 极个别 Gradio 版本若不支持上述键，安全回退。
        log.warning("自定义主题定制失败，回退默认主题：%s", exc)
        try:
            return gr.themes.Soft()
        except Exception:
            return "soft"


# ------------------------------------------------------------------------------
# 自定义 CSS（装饰性，纯样式）
# ------------------------------------------------------------------------------
# 模板中的 __PRIMARY__ / __ACCENT__ / __PRIMARY_RGBA_*__ 是品牌色占位符，
# 由 build_app_css() 用 utils.brand 的当前值替换。刻意不用 str.format：
# CSS 本身含大量 {} 花括号，format 会把它们当占位符而炸。
APP_CSS_TEMPLATE = """
/* 整体容器：浅灰蓝背景 + 适当留白 */
.gradio-container {
    max-width: 1280px !important;
    margin: 0 auto !important;
    font-family: "Segoe UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
}

/* 顶部标题区：品牌渐变条 + 圆角 */
.gradio-container h1 {
    text-align: center;
    font-weight: 700;
    background: linear-gradient(90deg, __PRIMARY__, __ACCENT__);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 4px;
}

/* 区块卡片：更柔和的阴影与圆角 */
.gradio-container .block {
    border-radius: 14px !important;
    box-shadow: 0 2px 10px rgba(15, 23, 42, 0.06);
}

/* 主按钮：圆角 + 过渡动画 */
.gradio-container button.primary {
    border-radius: 10px !important;
    font-weight: 600;
    transition: transform .08s ease, box-shadow .08s ease;
}
.gradio-container button.primary:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 14px __PRIMARY_RGBA_35__;
}

/* 标签页头：激活态高亮 */
.gradio-container .tabitem {
    border-radius: 0 0 12px 12px !important;
}

/* 画廊：卡片间距与圆角 */
.gradio-container .gallery {
    border-radius: 12px !important;
    gap: 10px !important;
}

/* 日志/详情文本框：等宽字体、浅底，便于阅读长路径 */
.gradio-container textarea {
    font-family: "Cascadia Code", "Consolas", "Menlo", monospace;
    font-size: 13px;
    line-height: 1.5;
}

/* 合规提醒小字：弱化但不丢失 */
.gradio-container .prose blockquote {
    border-left: 3px solid #f59e0b;
    color: #475569;
    background: #fffbeb;
    padding: 8px 12px;
    border-radius: 6px;
}
"""


def build_app_css() -> str:
    """
    生成注入页面的自定义 CSS（品牌色占位符按当前 utils.brand 令牌展开）。

    返回值：
        CSS 字符串，可直接交给 `launch(css=...)`。

    说明：
        每次调用都重新读取 `utils.brand` 的当前值，因此品牌色只有一个真值
        来源；测试可用哨兵替换令牌后调用本函数，断言新值出现、旧值消失。
    """
    return (
        APP_CSS_TEMPLATE
        .replace("__PRIMARY_RGBA_35__", brand.rgba(brand.primary_rgb(), 0.35))
        .replace("__PRIMARY__", brand.PRIMARY_HEX)
        .replace("__ACCENT__", brand.ACCENT_HEX)
    )


#: 兼容别名：模块导入期展开一次（运行期品牌令牌不变化，等价于 build_app_css()）。
APP_CSS: str = build_app_css()


# ------------------------------------------------------------------------------
# 主题名解析（config.SERVER_THEME 的唯一消费入口）
# ------------------------------------------------------------------------------
def builtin_theme_names() -> "tuple[str, ...]":
    """
    返回当前 Gradio 版本支持的内置主题名（小写、已排序）。

    返回值：
        主题名元组；若 Gradio 运行时注册表不可读，返回空元组（调用方会因此
        把任何非品牌主题名判为非法并回退，不会误放行）。

    说明：
        白名单**从 Gradio 运行时枚举**，不硬编码。阶段 91 审计证据：
        硬编码白名单 {"default","soft","gradio-dark","monochrome",
        "huggingface","base"} 中 `gradio-dark` / `huggingface` 在
        Gradio 6.22 中并不存在，而真实存在的 `glass` / `ocean` / `citrus` /
        `origin` / `mario` 反而被拒——既拦不住错、又挡对了人。
        更关键：`gradio.utils.get_theme()` 对未注册名会走
        `Theme.from_hub()` 去 HuggingFace 联网下载，本项目网络不可达，
        会造成启动期延迟后才回退，因此「非法名必须在本地被拦住」。
    """
    try:
        from gradio.utils import BUILT_IN_THEMES  # 运行时注册表
        return tuple(sorted(str(k).lower() for k in BUILT_IN_THEMES.keys()))
    except Exception as exc:  # noqa: BLE001 - 主题白名单不可读不应阻断启动
        log.warning("无法读取 Gradio 内置主题注册表，按「仅品牌主题」处理：%s", exc)
        return ()


def resolve_theme_name(value: "str | None") -> str:
    """
    把 config.SERVER_THEME 的原始值解析为可直接交给 Gradio 的主题标识。

    参数说明：
        value : 配置值（大小写不敏感，允许前后空白）。

    返回值：
        `BRAND_THEME_NAME` 表示使用 build_app_theme() 的品牌定制主题；
        否则为经校验的 Gradio 内置主题名（原样小写字符串）。

    说明：
        非法值 → 记录 warning 并回退品牌主题（不抛异常、不静默采用，
        「回退」与「配置生效」在日志上可区分）。
    """
    name = str(value or "").strip().lower()
    if name == BRAND_THEME_NAME:
        return BRAND_THEME_NAME
    names = builtin_theme_names()
    if name and name in names:
        return name
    log.warning(
        "未识别的界面主题 %r，回退为品牌主题 %r。可选内置主题：%s",
        value, BRAND_THEME_NAME, ", ".join(names) or "(不可用)",
    )
    return BRAND_THEME_NAME


if __name__ == "__main__":
    t = build_app_theme()
    print("主题构建成功：", type(t).__name__ if not isinstance(t, str) else t)
    print("CSS 长度：", len(APP_CSS), "字符")

    # ---- 品牌令牌单一真值来源：CSS 里不得再出现硬编码品牌色 ----
    assert brand.PRIMARY_HEX in APP_CSS, "CSS 丢失主色"
    assert brand.ACCENT_HEX in APP_CSS, "CSS 丢失辅色"
    assert brand.rgba(brand.primary_rgb(), 0.35) in APP_CSS, "CSS 丢失主色 rgba"
    assert APP_CSS == build_app_css(), "APP_CSS 常量与 build_app_css() 不一致"
    # 「CSS 里不再残留硬编码品牌色」的硬守卫在 tests/test_ui_audit91.py
    # （哨兵替换 + 结构性扫描）；此处刻意不写该字面量，避免本文件自己
    # 成为"品牌色字面量回流"的命中点。

    # ---- 主题名解析：白名单必须与 Gradio 运行时一致 ----
    names = builtin_theme_names()
    print("可用内置主题：", ", ".join(names) or "(不可用)")
    assert resolve_theme_name("brand") == BRAND_THEME_NAME
    assert resolve_theme_name("  BRAND  ") == BRAND_THEME_NAME
    assert resolve_theme_name("banana") == BRAND_THEME_NAME   # 非法 -> 回退
    assert resolve_theme_name(None) == BRAND_THEME_NAME
    if names:
        for n in names:
            assert resolve_theme_name(n) == n, f"合法内置主题 {n} 被误拒"
            assert resolve_theme_name(n.upper()) == n
        # 阶段 91 前的硬编码白名单成员：gradio-dark / huggingface 并不存在。
        for bogus in ("gradio-dark", "huggingface"):
            assert bogus not in names, f"Gradio 竟然内置了 {bogus}？白名单假设需复核"
    print("ui/theme.py 自检通过。")
