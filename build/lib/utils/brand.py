# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : utils/brand.py
 文件说明 : 品牌视觉令牌（唯一真值来源）。

            阶段 91 真值审计发现：品牌主色在修复前共有 **6 处字面量、跨 3 个
            文件** 各写一遍，且存在空格变体，改一处不会全局生效：

              · ui/theme.py      ：常量定义（#2563eb / #1d4ed8 / #0ea5e9）
                                   + APP_CSS 内联（linear-gradient / box-shadow）
              · ui/app.py        ：注入 JS 内联（拖拽浮层 border / hint 文字 / 阴影）
              · services/export_service.py：对比图「After」文字色 + AFTER 角标底色
              · utils/recommend_view.py    ：另有 #eef2f7（语义为进度条轨道底色）

            且**没有任何自动化会因此失败**——即「没有守卫」（阶段 83 判据）。

            本模块把这些令牌收敛到单一来源。所有颜色以十六进制为主键，
            RGB 三元组一律**由主键派生**（不并列存储），因此不存在「改了一个
            忘了另一个」的可能。为便于测试用哨兵替换（阶段 88 经验 23），
            消费方必须通过 `brand.XXX` / `brand.xxx_rgb()` 在**调用时**读取，
            而不是 `from utils.brand import PRIMARY_HEX` 冻结绑定。

            分层位置：utils 为叶层，ui 层与 services 层均可安全 import，
            不产生反向依赖（ui→services→core→utils→config）。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
==============================================================================
"""

from __future__ import annotations

__all__ = [
    "PRIMARY_HEX",
    "PRIMARY_HOVER_HEX",
    "ACCENT_HEX",
    "PAGE_BG_HEX",
    "INK_HEX",
    "SURFACE_HEX",
    "HEX_TOKENS",
    "hex_to_rgb",
    "primary_rgb",
    "primary_hover_rgb",
    "accent_rgb",
    "page_bg_rgb",
    "ink_rgb",
    "rgba",
]

# ------------------------------------------------------------------------------
# 品牌色令牌（十六进制为唯一主键）
# ------------------------------------------------------------------------------
# 主色：品牌蓝（按钮主色 / 滑块 / 拖拽浮层边框 / 对比图 After 标记）
PRIMARY_HEX: str = "#2563eb"
# 主色 hover
PRIMARY_HOVER_HEX: str = "#1d4ed8"
# 辅色：天蓝（标题渐变末端 / 链接文字）
ACCENT_HEX: str = "#0ea5e9"
# 页面背景（浅灰蓝）
PAGE_BG_HEX: str = "#eef2f7"
# 深色墨色（暗色模式卡片 / 对比图 Before 标记 / 联系表底色）
INK_HEX: str = "#1e293b"
# 浅色面板底（输入框底色）
SURFACE_HEX: str = "#f8fafc"

    #: 全部品牌令牌的只读视图（供守卫测试遍历，避免测试里再抄一遍字面量）。
#: 注意：本表是**构建期快照**，不随运行时改 `PRIMARY_HEX` 等模块常量而更新；
#:       需要「跟随当前值」请直接读模块属性（`brand.PRIMARY_HEX`）。
HEX_TOKENS: dict = {
    "PRIMARY_HEX": PRIMARY_HEX,
    "PRIMARY_HOVER_HEX": PRIMARY_HOVER_HEX,
    "ACCENT_HEX": ACCENT_HEX,
    "PAGE_BG_HEX": PAGE_BG_HEX,
    "INK_HEX": INK_HEX,
    "SURFACE_HEX": SURFACE_HEX,
}


# ------------------------------------------------------------------------------
# 派生工具（RGB 一律由十六进制主键派生，不并列存储）
# ------------------------------------------------------------------------------
def hex_to_rgb(hex_color: str) -> "tuple[int, int, int]":
    """
    把 `#rrggbb` 解析为 (r, g, b) 三元组（各 0-255）。

    参数说明：
        hex_color : 十六进制颜色字符串，允许省略 `#`，允许 `#rgb` 三位缩写，
                    允许首尾空白（内部空白**不**允许）。

    返回值：
        (r, g, b) 整数三元组。

    异常：
        TypeError  —— 传入非字符串时显式抛出。
        ValueError —— 长度不是 3/6 位、含非十六进制字符、或内部含空白时显式抛出
        （不静默返回黑色：静默兜底会把配色错误伪装成"看起来正常"）。

    阶段 141 修复（P1，「静默伪装」）：
        原实现 `s = str(hex_color).strip().lstrip("#")` 有两处把错误伪装成正常：
          1) `str(...)` 对**非字符串**静默转换 —— `hex_to_rgb(123456)` 会把整数
             变成字符串 "123456" 再当十六进制解析，**静默产出错误颜色 (18,52,86)**
             （正确配色应为 #123456 → (18,52,86) 是巧合；若传的是十进制意图值
             则全错），且报错信息只在长度不符时才出现，类型错本身**从不报错**；
          2) `int(s[i:i+2], 16)` 容忍**内部空白** —— `hex_to_rgb("#12 456")`
             静默得到 (18,4,86)。首尾空白已被 strip 掉，内部空白则制造
             「长度对、字符也没错」的假象。
        两处都属「把调用方 bug 伪装成合法配色」——与本模块 docstring 声明的
        「不静默兜底」原则直接冲突，且**没有任何自动化会因此失败**（阶段 83 判据）。
    """
    if not isinstance(hex_color, str):
        raise TypeError(
            f"颜色必须是字符串（如 '#2563eb'），实际收到 {type(hex_color).__name__}："
            f"{hex_color!r}"
        )
    s = hex_color.strip().lstrip("#")
    if any(ch.isspace() for ch in s):
        raise ValueError(f"非法十六进制颜色（内部含空白字符）：{hex_color!r}")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"非法十六进制颜色（长度应为 3 或 6）：{hex_color!r}")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError as exc:  # 非十六进制字符
        raise ValueError(f"非法十六进制颜色（含非十六进制字符）：{hex_color!r}") from exc


def primary_rgb() -> "tuple[int, int, int]":
    """品牌主色 RGB（每次调用从 `PRIMARY_HEX` 派生）。"""
    return hex_to_rgb(PRIMARY_HEX)


def primary_hover_rgb() -> "tuple[int, int, int]":
    """主色 hover RGB（每次调用从 `PRIMARY_HOVER_HEX` 派生）。"""
    return hex_to_rgb(PRIMARY_HOVER_HEX)


def accent_rgb() -> "tuple[int, int, int]":
    """辅色 RGB（每次调用从 `ACCENT_HEX` 派生）。"""
    return hex_to_rgb(ACCENT_HEX)


def page_bg_rgb() -> "tuple[int, int, int]":
    """页面背景 RGB（每次调用从 `PAGE_BG_HEX` 派生）。"""
    return hex_to_rgb(PAGE_BG_HEX)


def ink_rgb() -> "tuple[int, int, int]":
    """深色墨色 RGB（每次调用从 `INK_HEX` 派生）。"""
    return hex_to_rgb(INK_HEX)


def rgba(rgb, alpha: float) -> str:
    """
    把 (r, g, b) + 透明度拼成 CSS/JS 通用的 `rgba(r,g,b,a)` 字符串。

    参数说明：
        rgb   : (r, g, b) 三元组（通常来自 `primary_rgb()` 等）。
        alpha : 0.0-1.0 的透明度。

    返回值：
        紧凑无空格写法，如 `rgba(37,99,235,0.35)`。

    说明：
        alpha 用 `%g` 格式化，避免 `0.35000000000000003` 这类浮点尾数
        进入 CSS/JS（等价样式却产生不同的字面量，破坏"单一真值来源"的
        可对账性）。
    """
    r, g, b = (int(v) for v in rgb)
    return f"rgba({r},{g},{b},{'%g' % float(alpha)})"


if __name__ == "__main__":
    # 自检：令牌 ↔ 派生值一一对账（任一侧被改坏，这里必须失败）。
    assert hex_to_rgb("#2563eb") == (37, 99, 235)
    assert hex_to_rgb("2563eb") == (37, 99, 235)
    assert hex_to_rgb("#fff") == (255, 255, 255)
    assert primary_rgb() == (37, 99, 235), "主色令牌与 RGB 派生值不一致"
    assert primary_hover_rgb() == (29, 78, 216)
    assert accent_rgb() == (14, 165, 233)
    assert page_bg_rgb() == (238, 242, 247)
    assert ink_rgb() == (30, 41, 59)
    assert rgba(primary_rgb(), 0.35) == "rgba(37,99,235,0.35)"
    assert rgba(primary_rgb(), 0.12) == "rgba(37,99,235,0.12)"
    assert rgba(ink_rgb(), 0.75) == "rgba(30,41,59,0.75)"
    for bad in ("#12345", "#gggggg", ""):
        try:
            hex_to_rgb(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法颜色 {bad!r} 应显式抛 ValueError")
    # 阶段 141：非字符串必须抛 TypeError（不得被 str() 静默转换）。
    for bad in (123456, None, (37, 99, 235), b"2563eb"):
        try:
            hex_to_rgb(bad)
        except TypeError:
            pass
        else:
            raise AssertionError(f"非字符串 {bad!r} 应显式抛 TypeError")
    # 阶段 141：内部空白不得被 int(x,16) 静默容忍（首尾空白仍容忍，见下）。
    for bad in ("#12 456", "#1 2345", " #25 63eb"):
        try:
            hex_to_rgb(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"含内部空白的 {bad!r} 应显式抛 ValueError")
    # 首尾空白仍应被容忍（保持兼容）。
    assert hex_to_rgb("  #2563eb  ") == (37, 99, 235)
    assert hex_to_rgb("\n#2563eb") == (37, 99, 235)
    # 令牌表必须覆盖全部公开令牌（防止新增令牌忘记登记）。
    assert set(HEX_TOKENS) == {
        "PRIMARY_HEX", "PRIMARY_HOVER_HEX", "ACCENT_HEX",
        "PAGE_BG_HEX", "INK_HEX", "SURFACE_HEX",
    }
    print("utils/brand.py 自检通过（令牌与 RGB 派生值对账一致）。")
