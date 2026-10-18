# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/detection_presets.py
 文件说明 : 阶段 20 —— AI 水印专项检测 · 检测预设体系（算法层 / core）。

            本模块定义「检测预设（DetectionPreset）」：把一组针对某类 AI 水印
            的检测参数（敏感度、启用哪些策略、是否开启平铺增强/底部搜索、是否用
            OCR）打包成一个可命名的预设，让用户一键切换「检测模式」，而不必逐项
            手调滑块与勾选框。

            内置预设（覆盖 AI 生成图最常见的几类水印）：
              · general       : 通用检测（综合三类策略，默认）
              · ai_tiled      : AI 平铺半透明水印（开启平铺增强，提升低透明召回）
              · ai_signature  : AI 署名栏（底部居中 + 角落 + OCR 文字识别）
              · ai_corner     : AI 角落标识（固定角落的 AI 生成标识）
              · ai_text_heavy : AI 文字密集水印（全策略 + OCR + 底部搜索，最大化文字召回）
              · ai_faint_tiled: AI 极淡平铺水印（平铺增强 + 更高敏感度，专治几乎看不见的 repeated 水印）
              · ai_grid       : AI 网格水印（网格检测 + 平铺增强 + 全策略，专治点阵/网格 lattice 水印）
              · ai_gradient   : AI 渐变水印（全局渐变检测，整图标记，仅用于确认有渐变水印的图）
              · ai_logo       : AI LOGO 水印（实体 LOGO 轮廓检测 + 角落 + 半透明，专治实心填充型标识）
              · ai_multi      : AI 多重水印（半透明 + 角落 + 边缘 + 网格 + LOGO + 平铺 + 底部搜索的并集）

            ⚠ 阶段 143 订正：上面这份清单**曾漏列 `ai_logo` 与 `ai_multi`**（阶段 23 / 61
            新增预设时只加了 `_BUILTIN_PRESETS` 条目、没同步本清单）。真值来源永远是
            `_BUILTIN_PRESETS`；本清单只作导览，其完整性由
            `tests/test_detection_presets_audit143.py` 对账守护。

            设计要点：
              1. 纯算法层，仅依赖 numpy + config，零 gradio / torch / opencv 硬依赖，
                 可独立测试（与 detector.py 同层约束一致）。
              2. 注册表进程内单例；内置预设惰性加载（首次访问时注册），支持
                 register_preset() 动态扩展（与 core.plugins 风格一致）。
              3. 预设只「覆盖非 None 字段」，未设置的字段交给 detect_watermark_mask
                 走其各自的 config 默认值，保证向后兼容。

 版 本 号 : 1.0.0 (阶段 20)
==============================================================================
"""

from __future__ import annotations

import os as _os
import sys as _sys

# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import config

log = None  # 延迟导入 logger，避免在无 logging 配置的单测里报缺失。


def _get_log():
    """延迟获取日志器（避免在模块导入顶层创建，保持轻量）。"""
    global log
    if log is None:
        from utils.logger import get_logger
        log = get_logger("core.detection_presets")
    return log


@dataclass
class DetectionPreset:
    """
    单个检测预设。

    字段说明：
        name        : 预设唯一标识（用于 CLI / API / 代码引用），英文小写+下划线。
        display     : 界面向用户展示的名称（将被 i18n 键覆盖显示，见 ui 层）。
        description : 预设用途简述（界面向导/下拉说明用）。
        sensitivity : 半透明检测敏感度（0~1）；None 表示沿用 config 默认。
        enable_translucent / enable_corner / enable_edge : 三类策略开关；None 沿用默认。
        enable_grid / enable_gradient : 网格 / 渐变检测开关（阶段 22）；None 沿用默认。
        enable_logo   : 实体 LOGO 轮廓检测开关（阶段 23）；None 沿用默认。
        enhance_tiled : 平铺半透明增强开关；None 沿用 config 默认。
        search_bottom : 底部居中搜索区开关；None 沿用默认。
        use_ocr       : 是否启用 OCR 增强；None 沿用默认。
    """

    name: str
    display: str
    description: str
    sensitivity: Optional[float] = None
    enable_translucent: Optional[bool] = None
    enable_corner: Optional[bool] = None
    enable_edge: Optional[bool] = None
    enable_grid: Optional[bool] = None
    enable_gradient: Optional[bool] = None
    enable_logo: Optional[bool] = None
    enhance_tiled: Optional[bool] = None
    search_bottom: Optional[bool] = None
    use_ocr: Optional[bool] = None
    # 阶段 P1：全幅平铺水印是否合成「全帧区域」（仅平铺类预设开启，general 保持 None）。
    fullframe: Optional[bool] = None

    def resolve(self, base_sensitivity: float) -> Dict[str, object]:
        """
        把预设解析成 detect_watermark_mask 的关键字参数字典。

        输出规则（阶段 143 订正 —— 原文只说「只输出非 None 字段」，与
        `sensitivity` 的实际行为矛盾，属「声明 ≠ 实现」）：
          · **除 `sensitivity` 外**：只输出「本预设显式设置（非 None）」的字段；
            未设置的字段**不出现**，由 detect_watermark_mask 用自身默认值补齐。
          · **`sensitivity` 是唯一例外，总是输出**：
              预设自带 → 用预设值；预设未设置（None）→ 用 `base_sensitivity` 兜底。
            理由：`detect_watermark_mask(sensitivity=None)` 会回头去读
            `config.DETECT_TRANSLUCENT_SENSITIVITY`，而预设解析的职责正是
            「替调用方决定本次检测的敏感度」；若此键缺席，用户在上层显式拖动的
            敏感度（经 `base_sensitivity` 传入）就**无法到达**下游。
            因此 `general` 这类「全 None」预设返回的是 `{"sensitivity": base}` 而
            不是 `{}` —— 这是**有意的**，不是遗漏。
            调用方若想「完全不干预敏感度」，应直接不调用本函数。

        参数说明：
            base_sensitivity : 当预设未设置 sensitivity 时使用的兜底值（通常来自 config）。
                               **必填且必须可转 float**（类型标注为 `float`）；
                               `name` 可空而本参数不可空 —— 两者可选性不对称，
                               传 None 会由 `float()` 抛 TypeError（显式失败，非静默）。

        返回值：
            关键字参数字典（键名与 detect_watermark_mask 形参一致）。
        """
        kw: Dict[str, object] = {}
        if self.sensitivity is not None:
            kw["sensitivity"] = float(self.sensitivity)
        else:
            kw["sensitivity"] = float(base_sensitivity)
        if self.enable_translucent is not None:
            kw["enable_translucent"] = bool(self.enable_translucent)
        if self.enable_corner is not None:
            kw["enable_corner"] = bool(self.enable_corner)
        if self.enable_edge is not None:
            kw["enable_edge"] = bool(self.enable_edge)
        if self.enable_grid is not None:
            kw["enable_grid"] = bool(self.enable_grid)
        if self.enable_gradient is not None:
            kw["enable_gradient"] = bool(self.enable_gradient)
        if self.enable_logo is not None:
            kw["enable_logo"] = bool(self.enable_logo)
        if self.enhance_tiled is not None:
            kw["enhance_tiled"] = bool(self.enhance_tiled)
        if self.search_bottom is not None:
            kw["search_bottom"] = bool(self.search_bottom)
        if self.use_ocr is not None:
            kw["use_ocr"] = bool(self.use_ocr)
        if self.fullframe is not None:
            kw["enable_fullframe"] = bool(self.fullframe)
        return kw


# ==============================================================================
# 第 1 节：内置预设定义
# ------------------------------------------------------------------------------
# 注意：display 仅作代码内兜底名；UI 层会优先用 i18n 键 detect.preset.<name> 展示。
# ==============================================================================

_BUILTIN_PRESETS: Tuple[DetectionPreset, ...] = (
    DetectionPreset(
        name="general",
        display="通用检测",
        description="综合半透明 / 角落 / 边缘三类策略，适用于大多数水印场景。",
        sensitivity=None,
        enable_translucent=None,
        enable_corner=None,
        enable_edge=None,
        enhance_tiled=None,
        search_bottom=None,
        use_ocr=None,
    ),
    DetectionPreset(
        name="ai_tiled",
        display="AI 平铺水印",
        description="针对 AI 生成图常见的半透明平铺 logo：开启平铺增强，提升低透明度水印召回。",
        sensitivity=0.62,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=True,
        enhance_tiled=True,
        search_bottom=False,
        use_ocr=None,
        fullframe=True,
    ),
    DetectionPreset(
        name="ai_signature",
        display="AI 署名栏",
        description="针对 AI 平台底部居中 / 边角的署名文字栏：开启底部搜索 + OCR 文字识别。",
        sensitivity=0.5,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=True,
        enhance_tiled=False,
        search_bottom=True,
        use_ocr=True,
    ),
    DetectionPreset(
        name="ai_corner",
        display="AI 角落标识",
        description="针对固定在图片四角的 AI 生成标识（图标 + 文字组合）。",
        sensitivity=0.55,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=False,
        enhance_tiled=False,
        search_bottom=False,
        use_ocr=None,
    ),
    DetectionPreset(
        name="ai_text_heavy",
        display="AI 文字密集",
        description="针对满版图文的 AI 生成说明/提示水印：全策略 + OCR 文字识别 + 底部搜索，最大化文字召回。",
        sensitivity=0.52,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=True,
        enhance_tiled=False,
        search_bottom=True,
        use_ocr=True,
    ),
    DetectionPreset(
        name="ai_faint_tiled",
        display="AI 极淡平铺",
        description="针对透明度极低的平铺 logo：开启平铺增强并进一步提高敏感度，专治几乎看不见的 repeated 水印。",
        sensitivity=0.72,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=True,
        enhance_tiled=True,
        search_bottom=False,
        use_ocr=None,
        fullframe=True,
    ),
    DetectionPreset(
        name="ai_grid",
        display="AI 网格水印",
        description="针对规则排布的网格状平铺水印（点阵/网格 logo）：开启网格检测 + 平铺增强 + 全策略，专治 repeated lattice 水印。",
        sensitivity=0.6,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=True,
        enhance_tiled=True,
        search_bottom=False,
        use_ocr=None,
        enable_grid=True,
        fullframe=True,
    ),
    DetectionPreset(
        name="ai_gradient",
        display="AI 渐变水印",
        description="针对整图叠加的全局平滑渐变署名层：开启全局渐变检测并整图标记（仅建议用于确认有渐变水印的图，会整图修复）。",
        sensitivity=None,
        enable_translucent=False,
        enable_corner=False,
        enable_edge=False,
        enhance_tiled=False,
        search_bottom=False,
        use_ocr=None,
        enable_gradient=True,
    ),
    DetectionPreset(
        name="ai_logo",
        display="AI LOGO 水印",
        description="针对角落的实体品牌标识（实心图标 / 字母组合）：开启 LOGO 轮廓检测 + 角落 + 半透明三类策略，专治实心填充型 AI 署名标识。若图上同时还有文字/网格等水印，请改用「AI 多重水印」。",
        sensitivity=0.55,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=False,
        enhance_tiled=False,
        search_bottom=True,
        use_ocr=None,
        enable_logo=True,
    ),
    DetectionPreset(
        name="ai_multi",
        display="AI 多重水印",
        description="针对同一张图上叠加多种水印（如角落 LOGO + 底部文字、LOGO + 网格点阵）：开启半透明 + 角落 + 边缘文字 + 网格 + 实体 LOGO + 平铺增强 + 底部搜索的并集，专治组合水印（阶段 61：单信号预设会漏掉未被启用策略的那一类水印）。",
        sensitivity=0.55,
        enable_translucent=True,
        enable_corner=True,
        enable_edge=True,
        enhance_tiled=True,
        search_bottom=True,
        use_ocr=None,
        enable_grid=True,
        enable_logo=True,
    ),
)


# ==============================================================================
# 第 2 节：注册表（进程内单例 + 惰性加载内置预设）
# ==============================================================================

_REGISTRY: Dict[str, DetectionPreset] = {}
_LOADED = False


def register_preset(preset: DetectionPreset) -> None:
    """
    注册（覆盖）一个检测预设。

    参数说明：
        preset : DetectionPreset 实例。name 作为唯一键，重复注册会覆盖。

    异常（阶段 143 订正）：
        TypeError  : 传入的不是 `DetectionPreset` 实例（错误信息含**真实类型**）。
        ValueError : 是 DetectionPreset 但 `name` 为空。

    阶段 143 修的是什么
    -------------------
    原实现只有一行 `if not preset or not preset.name:`，有两个问题：
      1. **`not preset` 是死条件** —— `DetectionPreset` 是普通 dataclass，
         既无 `__bool__` 也无 `__len__`，任何实例恒为真，该半句永远不触发；
      2. 于是「类型错」被**伪装成「值错」**：`register_preset(None)` 与
         `register_preset({})` 都落到 `not preset.name` 报出
         「预设必须提供非空 name。」——**指错了原因**（真实原因是类型不对）；
         而 `register_preset("abc")` / `register_preset(42)` 抛的是
         `AttributeError: 'str' object has no attribute 'name'` ——
         **同一个「类型不合法」的输入族给出了两种不同异常类型**。
    判据见经验 157：类型错与值错必须抛**不同异常类型**，否则调用方无法区分，
    且错误文案会把后来者带偏（去查 name 而不是去查类型）。
    """
    if not isinstance(preset, DetectionPreset):
        raise TypeError(
            f"register_preset 需要 DetectionPreset 实例，收到 {type(preset).__name__}。"
        )
    if not preset.name:
        raise ValueError("预设必须提供非空 name。")
    _REGISTRY[preset.name] = preset
    _get_log().debug("注册检测预设：%s", preset.name)


def _ensure_builtin() -> None:
    """首次访问时把内置预设注册进 _REGISTRY（仅一次）。"""
    global _LOADED
    if _LOADED:
        return
    for p in _BUILTIN_PRESETS:
        # 不覆盖用户已注册的同名预设。
        if p.name not in _REGISTRY:
            _REGISTRY[p.name] = p
    _LOADED = True


# ==============================================================================
# 第 3 节：对外查询 API
# ==============================================================================

def list_presets() -> List[DetectionPreset]:
    """返回全部已注册预设（含内置）。"""
    _ensure_builtin()
    return list(_REGISTRY.values())


def get_preset(name: str) -> Optional[DetectionPreset]:
    """按名取预设；不存在返回 None。"""
    _ensure_builtin()
    return _REGISTRY.get(name)


def get_preset_choices() -> List[Tuple[str, str]]:
    """
    返回 UI 下拉框用的 (展示名, 值) 列表。

    注意：展示名用预设内置 display 作兜底；UI 层会再经 i18n 映射成本地语言。
    """
    _ensure_builtin()
    return [(p.display, p.name) for p in _REGISTRY.values()]


def get_preset_names() -> List[str]:
    """返回全部预设名（用于 CLI choices 校验）。"""
    _ensure_builtin()
    return list(_REGISTRY.keys())


def preset_display(name: str) -> str:
    """
    把内部预设名映射成可读展示标签（阶段 26/27 日志与分布汇总用）。

    优先取 UI 层的 i18n 标题（detect.preset.<name>.title），缺失时回退预设内置 display。
    两者皆缺失则原样返回 name。
    """
    preset = get_preset(name)
    if preset is None:
        return name
    try:
        from utils.i18n import t
        label = t(f"detect.preset.{name}.title")
        if not label.startswith("detect.preset."):
            return label
    except Exception:  # noqa: BLE001 - i18n 未初始化等
        pass
    return preset.display


def resolve_preset(name: Optional[str], base_sensitivity: float) -> Dict[str, object]:
    """
    便捷封装：按名解析预设为 detect_watermark_mask 参数字典。

    参数说明：
        name             : 预设名；None / 空串 / **纯空白串** / 未知名时，
                           均返回仅含 base_sensitivity 的字典。
        base_sensitivity : 兜底敏感度（通常 config.DETECT_TRANSLUCENT_SENSITIVITY）。

    返回值：
        关键字参数字典（键名与 detect_watermark_mask 形参一致）。

    阶段 143 订正：**空白串口径统一**
    ---------------------------------
    原实现用 `if not name:` 判「未指定」，Python 里 `""` 为假而 `"   "` 为真 ⇒
    前者**静默**返回兜底，后者继续走 `get_preset("   ")` 拿到 None，于是
    **多发一条「未知检测预设」告警**（实测：`resolve_preset("", x)` 不告警、
    `resolve_preset("   ", x)` 告警）。同一类「用户没填/填了空格」的输入被
    分成两种行为，与经验 158（空白容忍口径必须全路径一致）同族。
    现统一为 `not str(name).strip()`：空白串与空串同语义（都视为未指定，
    都不告警），而**真正拼错的非空名**仍会告警（那才是需要提醒的情况）。
    """
    if name is None or not str(name).strip():
        return {"sensitivity": float(base_sensitivity)}
    preset = get_preset(name)
    if preset is None:
        _get_log().warning("未知检测预设 %s，已忽略，使用默认参数。", name)
        return {"sensitivity": float(base_sensitivity)}
    return preset.resolve(base_sensitivity)


# ==============================================================================
# 第 4 节：模块自检
# ==============================================================================
if __name__ == "__main__":
    print("开始 detection_presets.py 自检……")
    presets = list_presets()
    names = {p.name for p in presets}

    # 阶段 143 订正：原自检把预设名**硬编码**成一份清单并用 `in` 判定，
    #   于是「清单漏了某个内置预设」时**静默通过**（实测漏了 `ai_multi`：
    #   内置 10 个、清单只有 9 个，自检照跑不误）—— 与阶段 138/139 修过的
    #   「哑巴自检」同形。现改为与 `_BUILTIN_PRESETS` **集合对账**：
    #   任何新增/删除内置预设而忘记同步声明，都会立刻变红。
    expected = {p.name for p in _BUILTIN_PRESETS}
    missing = expected - names
    assert not missing, f"缺少内置预设：{sorted(missing)}"
    # 反向：注册表里不应出现声明之外的内置预设（用户动态注册的另算）。
    assert len(presets) >= len(expected), f"预设数 {len(presets)} < 内置数 {len(expected)}"
    print(f"  内置预设数 = {len(expected)}，注册表 = {len(presets)}，名称 = {sorted(names)}")

    # choices 结构正确，且数量与内置一致（防止 choices 丢项）。
    choices = get_preset_choices()
    assert all(isinstance(c, tuple) and len(c) == 2 for c in choices), "choices 结构错误"
    assert len(choices) == len(presets), f"choices({len(choices)}) 与预设数({len(presets)}) 不一致"
    print(f"  get_preset_choices 返回 {len(choices)} 项，示例 = {choices[0]}")

    # 每个内置预设都能被 name 取回，且 name/display 均非空。
    for p in _BUILTIN_PRESETS:
        got = get_preset(p.name)
        assert got is p, f"内置预设 {p.name} 取回的不是同一对象"
        assert p.display and p.description, f"内置预设 {p.name} 缺少 display/description"

    # general 除 sensitivity 外不覆盖任何字段（阶段 143：这是**有意**的例外，见 resolve docstring）。
    g = resolve_preset("general", 0.55)
    assert set(g.keys()) == {"sensitivity"}, f"general 不应覆盖其他字段：{g}"
    assert g["sensitivity"] == 0.55

    # ai_tiled 应覆盖 enhance_tiled=True 且 sensitivity=0.62。
    t = resolve_preset("ai_tiled", 0.55)
    assert t.get("enhance_tiled") is True, "ai_tiled 应开启平铺增强"
    assert t.get("sensitivity") == 0.62

    # 阶段 22 新增：ai_grid 应开启网格检测 + 平铺增强；ai_gradient 应开启渐变检测且隔离其它策略。
    gpre = resolve_preset("ai_grid", 0.55)
    assert gpre.get("enable_grid") is True, "ai_grid 应开启网格检测"
    assert gpre.get("enhance_tiled") is True, "ai_grid 应开启平铺增强"
    ggrad = resolve_preset("ai_gradient", 0.55)
    assert ggrad.get("enable_gradient") is True, "ai_gradient 应开启渐变检测"
    assert ggrad.get("enable_translucent") is False, "ai_gradient 应隔离半透明检测"
    assert ggrad.get("enable_corner") is False, "ai_gradient 应隔离角落检测"

    # 阶段 23 新增：ai_logo 应开启 LOGO 轮廓检测 + 角落 + 半透明 + 底部搜索。
    lpre = resolve_preset("ai_logo", 0.55)
    assert lpre.get("enable_logo") is True, "ai_logo 应开启 LOGO 轮廓检测"
    assert lpre.get("enable_corner") is True, "ai_logo 应开启角落检测"
    assert lpre.get("search_bottom") is True, "ai_logo 应开启底部搜索"

    # 阶段 61 新增：ai_multi 应是「并集」——同时开启网格与 LOGO（阶段 143 补，此前自检漏测它）。
    mpre = resolve_preset("ai_multi", 0.55)
    for k in ("enable_grid", "enable_logo", "enhance_tiled", "search_bottom",
              "enable_translucent", "enable_corner", "enable_edge"):
        assert mpre.get(k) is True, f"ai_multi 应开启 {k}（并集语义）"

    # 未知预设回退。
    u = resolve_preset("not_exist", 0.7)
    assert u == {"sensitivity": 0.7}, "未知预设应回退到 base_sensitivity"

    # 阶段 143：空白串与空串同语义（都视为未指定，都不告警）。
    for blank in ("", "   ", "\t\n"):
        assert resolve_preset(blank, 0.7) == {"sensitivity": 0.7}, f"空白串 {blank!r} 应视为未指定"
    assert resolve_preset(None, 0.7) == {"sensitivity": 0.7}

    # 阶段 143：register_preset 的类型错与值错必须抛**不同**异常类型。
    for bad in (None, {}, "abc", 42):
        try:
            register_preset(bad)
        except TypeError:
            pass
        else:
            raise AssertionError(f"register_preset({bad!r}) 应抛 TypeError（类型错）")
    try:
        register_preset(DetectionPreset(name="", display="d", description="c"))
    except ValueError as exc:
        assert "name" in str(exc)
    else:
        raise AssertionError("空 name 应抛 ValueError（值错）")

    # 动态注册 + 清理。
    register_preset(DetectionPreset(name="__tmp__", display="临时", description="x",
                                    sensitivity=0.3))
    assert get_preset("__tmp__") is not None
    del _REGISTRY["__tmp__"]  # 清理，避免污染

    print("detection_presets.py 自检通过：内置预设对账、choices、resolve、"
          "空白口径、注册校验、动态注册均正常。")
