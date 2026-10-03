# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : core/plugins.py
 文件说明 : 插件与「自定义修复引擎」注册框架（阶段 11 新增）。

 设计目标
 --------------------------------------------------------------------------
 本工作台原生只内置 LaMa / OpenCV 两种修复引擎。为了支持「接入用户自己的
 inpaint 模型」（例如 diffusion 模型、其它 TorchScript 模型、甚至云端 API），
 我们提供一个极简的插件框架：

   1. 用户编写一个继承自 InpaintEnginePlugin 的类，实现 inpaint() 方法；
   2. 在模块顶层调用 register_engine(我的引擎) 完成注册；
   3. 把该 .py 文件丢进 plugins/ 目录，程序启动时自动发现并加载；
   4. 之后即可在界面 / 命令行 / API 的「修复引擎」下拉里选到它。

 关键约定
 --------------------------------------------------------------------------
 · 插件只替换「核心填充」这一步：输入是已预处理好的 RGB 原图 + 二值掩码，
   输出是填充后的 RGB 图。掩码后处理、降噪、力度融合仍由 inpainter.py 统一完成，
   因此插件作者无需关心这些细节，接口极简。
 · 本模块属于 core 层，严禁 import gradio / 任何界面组件。
 · 插件发现是「惰性」的：首次查询引擎列表或分派引擎时才扫描插件目录，
   因此无论走 GUI / CLI / 库 / REST 哪条入口，插件都会被正确加载。
 · 插件加载失败只告警、不崩溃，保证一个烂插件不会拖垮整个程序。

 阶段 77 加固（真值审计后修复六项缺陷）
 --------------------------------------------------------------------------
 审计发现框架此前存在六处「声明 ≠ 实现」的偏差，本阶段逐一收口：

   P1 插件可**静默覆盖内建引擎**，而 list_engines() 把内建描述写死，导致
      「清单显示 LaMa、实际跑的是插件」——显示与执行不一致（具有欺骗性）。
      修复：引擎清单改为**以注册表为唯一真值来源**，内建身份由实例类型判定，
      不再硬编码描述；覆盖发生后清单如实反映覆盖者的名称与来源。
   P2 name 校验形同虚设：`'bad name!'`、纯空格、含点/中文/大写均被注册。
      修复：注册前 `strip().lower()` 归一化 + 正则 `^[a-z][a-z0-9_]{0,31}$`
      校验，不合法一律拒绝并给出明确原因。
   P3 load_plugins() 返回值语义不一致（首次=文件数、二次=注册表规模）。
      修复：统一为「本次实际成功加载的插件文件数」，重复调用返回 0。
   P4 插件加载失败后 `sys.modules` 中残留半初始化模块。
      修复：exec_module 失败时立即从 sys.modules 摘除。
   P5 插件目录首次不存在即被永久标记为「已扫描」，之后创建目录不再被发现。
      修复：只有**确实扫描到至少一个存在的目录**才置完成位；否则下次重试。
   P6 同名插件按文件名字母序静默覆盖，无任何告警。
      修复：默认**拒绝重复注册**并告警；内建引擎不可被插件覆盖，除非显式
      打开 `PLUGIN_ALLOW_OVERRIDE`（逃生舱），开启后仍逐条记录 WARNING。

 · 新增 `reload_plugins()`（开发期热加载）与 `get_plugin_report()`（结构化
   扫描结果：loaded / failed / skipped），便于界面与集成方观测插件状态。
==============================================================================
"""

from __future__ import annotations

import importlib.util
import os as _os
import re
import sys as _sys
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional

# ------------------------------------------------------------------------------
# 路径加固：确保项目根目录在 sys.path，使 `import config` 在所有调用方式下可用。
# ------------------------------------------------------------------------------
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

import numpy as np

import config
from utils.logger import get_logger

log = get_logger("core.plugins")

# 进度回调类型：callable(progress: 0~1, message: str) -> None
ProgressCallback = Callable[[float, str], None]


# ==============================================================================
# 第 1 节：引擎元信息与插件基类
# ==============================================================================
class EngineInfo:
    """引擎元信息，供 UI / CLI / API 展示。

    阶段 77：新增 `source` 字段（插件来源文件路径；内建引擎为空串）——
    用于回答「这个引擎是谁提供的」，是同名覆盖排查与插件治理的必要信息。
    """

    __slots__ = ("name", "display_name", "description", "builtin", "requires_model", "source")

    def __init__(
        self,
        name: str,
        display_name: str,
        description: str,
        builtin: bool = False,
        requires_model: bool = False,
        source: str = "",
    ):
        self.name = name
        self.display_name = display_name
        self.description = description
        self.builtin = builtin
        self.requires_model = requires_model
        self.source = source

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "builtin": self.builtin,
            "requires_model": self.requires_model,
            "source": self.source,
        }


class InpaintEnginePlugin(ABC):
    """
    自定义修复引擎插件基类。子类必须：
      1. 覆盖类属性 name / display_name / description / requires_model；
      2. 实现 inpaint() 方法。

    可选覆盖：
      setup()：懒加载权重 / 建立连接等一次性初始化，默认空实现。

    接口约定（务必遵守）：
      inpaint(image, mask, *, device=None, progress=None) -> np.ndarray
        image : H×W×3 uint8 RGB 原图（整图，含掩码外的像素）
        mask  : H×W uint8，值域 0/255 的二值掩码（已膨胀/阈值/羽化后二值化）
        device: 设备句柄（可为 None，插件自行决定如何使用）
        progress: 进度回调，插件可阶段性上报（如 progress(0.5, "修复中...")）
        返回  : H×W×3 uint8 RGB，掩码区域已被填充，其余区域应与原图一致
    """

    # 子类必须覆盖下面这些类属性。
    name: str = ""                 # 引擎唯一键（小写），例如 "my_diffusion"
    display_name: str = ""         # 界面显示名，例如 "我的 Diffusion 模型"
    description: str = ""          # 一句话简介
    requires_model: bool = False   # 是否需要外部模型文件（决定是否先调 setup()）

    # 阶段 77：内建引擎标记。插件不应设置本属性（由框架内建类置 True），
    # 清单据此判定「来源=内建」并据此实施覆盖保护。
    is_builtin: bool = False

    # 阶段 77：插件来源文件路径（由加载器在注册时回填，便于追溯与同名排查）。
    source: str = ""

    def setup(self) -> None:
        """可选：懒加载资源。默认空实现。"""
        return None

    @abstractmethod
    def inpaint(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        *,
        device=None,
        progress: Optional[ProgressCallback] = None,
    ) -> np.ndarray:
        """核心修复接口，由子类实现。"""
        raise NotImplementedError


# ==============================================================================
# 第 2 节：引擎注册表（进程内单例）
# ==============================================================================
# 引擎名（小写） -> 插件实例。
_REGISTRY: dict[str, InpaintEnginePlugin] = {}
# 已扫描过的插件目录（绝对路径字符串）；避免重复导入同一目录。
_SCANNED_DIRS: set[str] = set()
# 是否已完成插件扫描（「至少成功扫描到一个存在的目录」才置位，见阶段 77 P5）。
_LOADED: bool = False
# 最近一次扫描的结构化结果，供 get_plugin_report() 与界面观测。
_LAST_REPORT: dict = {"loaded": [], "failed": [], "skipped": [], "overridden": []}
# 当前正在加载的插件文件路径（由 load_plugins 维护），供 register_engine 回填
# 到实例的 `source` 属性，从而让「这个引擎来自哪个文件」可追溯（阶段 77）。
_CURRENT_PLUGIN_FILE: str = ""

# 引擎名合法字符：小写字母开头，仅小写字母/数字/下划线，长度 1~32。
#   阶段 77 P2：此前只校验「非空」，导致含空格/点号/中文/大写的名字都能注册——
#   含空格的键在 UI 下拉里表现为空选项、在 CLI/REST 里难以传参；含点号的键与
#   i18n 的路径分隔符冲突（阶段 75 教训）。现统一正则约束 + 归一化。
_ENGINE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# 阶段 128：**保留引擎名**。"auto" 是自动选择器、从不进入 _REGISTRY（见 docs/11），
# 因而没有「同名冲突」可用来拦截插件占用该名；若不显式拒绝，插件名叫 "auto" 会被写进
# _REGISTRY["auto"]，而 list_engines() 又硬编码跳过 auto ⇒ 注册表与清单脱节、引擎被隐藏
# （P1 家族缺陷）。内建键 lama/opencv 已由「同名冲突」保护，无需列入。
_RESERVED_ENGINE_NAMES = frozenset({"auto"})


# ------------------------------------------------------------------------------
# 内建引擎插件：把原生 LaMa / OpenCV 也注册进来，使「注册表」成为唯一真值来源。
# 关键：这里只做「声明 + 注册」，真正的修复逻辑通过延迟导入 core.inpainter 调用，
#       从而彻底避免 core.plugins <-> core.inpainter 的循环依赖。
# ------------------------------------------------------------------------------
class _BuiltinLama(InpaintEnginePlugin):
    name = "lama"
    display_name = "LaMa 深度学习"
    description = "效果最好，能凭空补全纹理内容（推荐）"
    requires_model = True
    is_builtin = True

    def inpaint(self, image, mask, *, device=None, progress=None):
        from core.device_manager import select_device
        from core.inpainter import _inpaint_lama_full, is_model_available

        if not is_model_available():
            raise FileNotFoundError(
                "指定的 LaMa 引擎缺少模型文件，请先运行 download_model.py 下载。"
            )
        dev = device or select_device()
        # 空显存释放由 inpainter.inpaint() 在整次修复结束后统一执行
        # （分块 / 批量 / 视频场景里只释放一次，避免每帧同步 empty_cache 掉帧）。
        return _inpaint_lama_full(image, mask, dev)


class _BuiltinOpenCV(InpaintEnginePlugin):
    name = "opencv"
    display_name = "OpenCV 传统算法"
    description = "速度极快、无需模型，适合细小水印"
    requires_model = False
    is_builtin = True

    def inpaint(self, image, mask, *, device=None, progress=None):
        from core.inpainter import _inpaint_opencv

        return _inpaint_opencv(image, mask)


# 模块加载即注册内建引擎（此时不会触发 core.inpainter 的导入，零循环依赖风险）。
# 真正的 register_engine 调用放在函数定义之后，见文件下方「内建引擎注册」一节。


# 阶段 128：内建身份**由实例的具体类型判定**（与本文档上方「内建身份由实例类型判定」
# 一致），不再依赖 `is_builtin` 类属性——该属性可被插件在自己的类里自设为 True，从而
# 在引擎清单里伪装成内建（显示欺骗），并让 reload_plugins() 永不卸载它（无法热加载）。
_BUILTIN_ENGINE_TYPES = (_BuiltinLama, _BuiltinOpenCV)


def _is_builtin_engine(inst) -> bool:
    """实例是否为框架内建引擎（按类型判定，抗 `is_builtin` 属性伪造）。"""
    return isinstance(inst, _BUILTIN_ENGINE_TYPES)


def _allow_override() -> bool:
    """是否允许插件覆盖已注册的同名引擎（默认 False，阶段 77 P1/P6）。

    开启方式：`WME_PLUGIN_ALLOW_OVERRIDE=1` / `config.PLUGIN_ALLOW_OVERRIDE = True`。
    即便开启，每次覆盖都会逐条 WARNING 记录「被谁覆盖」，避免静默。
    """
    return bool(getattr(config, "PLUGIN_ALLOW_OVERRIDE", False))


def register_engine(plugin, *, allow_override: Optional[bool] = None) -> bool:
    """
    注册一个引擎插件。plugin 可以是插件类（会被实例化）或实例。

    参数说明：
        plugin         : InpaintEnginePlugin 的子类或实例。
        allow_override : 是否允许覆盖同名引擎；None 时取 config.PLUGIN_ALLOW_OVERRIDE。

    返回值：
        True  = 已注册；False = 被拒绝（名称非法 / 重名且未开启覆盖）。

    说明（阶段 77 加固）：
        · 名称先做 `strip().lower()` 归一化，再按 `^[a-z][a-z0-9_]{0,31}$` 校验；
          不合法一律拒绝并告警（此前只校验非空，空格/点号/中文名都能混进注册表）。
        · **内建引擎（lama/opencv）不可被插件覆盖**，除非显式打开
          `PLUGIN_ALLOW_OVERRIDE`——此前插件只要叫 "lama" 就能静默顶掉内建引擎，
          而引擎清单仍显示「LaMa 深度学习」，**显示与执行完全脱节**。
        · 同名插件重复注册默认拒绝并告警（此前按字母序静默覆盖）。
        · 注册发生在插件模块被导入时（模块顶层调用本函数），
          因此无需手动登记，放进 plugins/ 目录即可。
    """
    inst = plugin() if isinstance(plugin, type) else plugin
    raw_name = getattr(inst, "name", "") or ""
    name = str(raw_name).strip().lower()

    if not name:
        log.warning("插件 %s 缺少 name 属性，跳过注册。", type(inst).__name__)
        return False

    if str(raw_name) != name:
        log.warning("插件名 %r 非规范（须小写），已归一化为 %r。", str(raw_name), name)

    if not _ENGINE_NAME_RE.match(name):
        log.warning(
            "插件 %s 的 name=%r 不合法：须为小写字母开头、仅含小写字母/数字/下划线、"
            "长度 1~32，已拒绝注册。",
            type(inst).__name__, name,
        )
        return False

    if name in _RESERVED_ENGINE_NAMES:
        log.warning(
            "插件 %s 的 name=%r 为保留名（%s），拒绝注册。",
            type(inst).__name__, name, "、".join(sorted(_RESERVED_ENGINE_NAMES)),
        )
        return False

    # 回填来源文件（框架已知真相优先；插件未显式声明时使用）。
    try:
        inst.source = _CURRENT_PLUGIN_FILE or getattr(inst, "source", "") or ""
    except Exception:  # noqa: BLE001
        pass

    existing = _REGISTRY.get(name)
    if existing is not None:
        allow = _allow_override() if allow_override is None else bool(allow_override)
        old_src = getattr(existing, "source", "") or type(existing).__name__
        new_src = getattr(inst, "source", "") or type(inst).__name__
        if not allow:
            log.warning(
                "引擎名 %r 已被占用（现有来源：%s），拒绝重复注册（来源：%s）。"
                "如需强制覆盖，请设 WME_PLUGIN_ALLOW_OVERRIDE=1。",
                name, old_src, new_src,
            )
            return False
        log.warning(
            "引擎 %r 被覆盖：%s -> %s（已开启 PLUGIN_ALLOW_OVERRIDE）。",
            name, old_src, new_src,
        )
        if name not in _LAST_REPORT["overridden"]:
            _LAST_REPORT["overridden"].append(name)

    _REGISTRY[name] = inst
    log.info("已注册自定义修复引擎：%s（显示名：%s）", name, inst.display_name or name)
    return True


# 模块加载即注册内建引擎（此时不会触发 core.inpainter 的导入，零循环依赖风险）。
# 注意：必须在 register_engine 定义之后调用。
register_engine(_BuiltinLama)
register_engine(_BuiltinOpenCV)


def get_engine(name: str) -> Optional[InpaintEnginePlugin]:
    """按名称取引擎实例（自动触发插件加载）。找不到返回 None。"""
    _ensure_loaded()
    if not name:
        return None
    return _REGISTRY.get(name.lower())


def has_engine(name: str) -> bool:
    """判断是否存在名为 name 的引擎（含内建；"auto" 恒为 True）。"""
    if not name:
        return False
    n = name.lower()
    if n == "auto":
        # "auto" 是自动选择器，永远可用（实际引擎在分派时再解析）。
        return True
    _ensure_loaded()
    return n in _REGISTRY


# ==============================================================================
# 第 3 节：引擎清单（内建 + 插件），供 UI / CLI / API 展示
# ==============================================================================
def list_engines() -> list[EngineInfo]:
    """
    列出全部可用引擎（内建 + 已加载插件）。

    返回值：
        EngineInfo 列表，第一个永远是 "auto"（自动选择）。

    阶段 77 关键修复（P1）：**以注册表为唯一真值来源**。
      此前内建引擎的名称/描述被硬编码在本函数里，且以内建键名 `continue` 跳过注册表
      实例——一旦插件覆盖了 `lama`，`get_engine("lama")` 返回插件、而清单仍显示
      「LaMa 深度学习 / builtin=True」，**显示与执行脱节**。现在：
        · 「是否内建」由实例的**具体类型**判定（`_is_builtin_engine`），不再由键名判定，
          也不依赖可被插件伪造的 `is_builtin` 属性；
        · 名称/描述一律取自注册表中的实际实例；
        · 覆盖发生后，清单如实呈现覆盖者的显示名、描述与来源文件。
    """
    _ensure_loaded()
    infos = [
        EngineInfo(
            "auto", "自动选择",
            "由配置的引擎策略自动选择（阶段 55：默认 OpenCV，LaMa 走 opt-in）",
            builtin=True,
        ),
    ]
    # 内建键位固定前置（保持历史顺序），但元信息取自注册表实例本身。
    for key in ("lama", "opencv"):
        inst = _REGISTRY.get(key)
        if inst is None:
            continue
        infos.append(
            EngineInfo(
                key,
                inst.display_name or key,
                inst.description or "",
                builtin=_is_builtin_engine(inst),
                requires_model=bool(inst.requires_model),
                source=getattr(inst, "source", "") or "",
            )
        )
    # 其余（插件）按注册顺序追加。
    for key, inst in _REGISTRY.items():
        if key in ("lama", "opencv", "auto"):
            continue
        infos.append(
            EngineInfo(
                key,
                inst.display_name or inst.name,
                inst.description or "",
                builtin=_is_builtin_engine(inst),
                requires_model=bool(inst.requires_model),
                source=getattr(inst, "source", "") or "",
            )
        )
    return infos


def get_engine_choices() -> list[str]:
    """供 UI / CLI 使用的引擎选项列表（含 auto + 内建 + 插件）。"""
    return [e.name for e in list_engines()]


def get_engine_detail() -> list[dict]:
    """引擎清单的字典形式，供 API 的 get_capabilities 使用。"""
    return [e.to_dict() for e in list_engines()]


# ==============================================================================
# 第 4 节：插件发现与加载
# ==============================================================================
def _resolve_plugin_dirs() -> list[Path]:
    """
    解析插件目录列表。

    优先读取 config.PLUGIN_DIRS（可以是相对 PROJECT_ROOT 的路径或绝对路径），
    若配置缺失则回退到 <项目根>/plugins。
    """
    raw = getattr(config, "PLUGIN_DIRS", None)
    dirs: list[Path] = []
    if raw:
        for d in raw:
            p = Path(d)
            if not p.is_absolute():
                p = config.PROJECT_ROOT / p
            dirs.append(p)
    if not dirs:
        dirs.append(config.PROJECT_ROOT / "plugins")
    return dirs


def _enabled_filter() -> set[str]:
    """
    返回需启用的插件文件名集合（不含 .py 后缀）。

    空集合表示「全部启用」。对应 config.PLUGIN_ENABLED（逗号分隔）。
    """
    raw = getattr(config, "PLUGIN_ENABLED", None)
    if not raw:
        return set()
    return {x.strip().lower() for x in raw if x.strip()}


def load_plugins(*, force: bool = False) -> int:
    """
    扫描插件目录，导入其中的 .py 模块以触发 register_engine。

    参数说明：
        force : True 时忽略「已扫描」记录，强制重扫全部目录（不清空注册表）。

    返回值：
        **本次实际成功加载的插件文件数**（阶段 77 P3 语义统一）。
        注意：重复调用（无新目录、无 force）返回 0，表示「本次没有加载任何文件」，
        而不是「注册表里有几个引擎」——此前的实现两种语义混用，会误导调用方
        （`main.py` 用它打印“已加载 N 个插件”）。

    说明：
        · 以下划线开头的文件（如 __init__.py）会被跳过；
        · 加载失败仅记录警告并继续，不影响其它插件与程序启动；
        · 通过 PLUGIN_ENABLED 可限制只加载指定插件；
        · 仅当**确实扫描到至少一个存在的目录**时才置「已完成」位（阶段 77 P5）：
          若首次调用时插件目录尚不存在（自定义 PLUGIN_DIRS 指向未创建目录），
          之后创建目录仍能在下次扫描时被发现，而不会永久放弃。
        · 失败模块会立即从 sys.modules 摘除，不留半初始化残留（阶段 77 P4）。
    """
    global _LOADED, _CURRENT_PLUGIN_FILE
    if force:
        _SCANNED_DIRS.clear()
        _LOADED = False
    elif _LOADED:
        return 0

    count = 0
    enabled = _enabled_filter()
    report: dict = {"loaded": [], "failed": [], "skipped": [], "overridden": []}
    scanned_any_dir = False

    for pdir in _resolve_plugin_dirs():
        if not pdir.exists() or not pdir.is_dir():
            log.debug("插件目录不存在，跳过：%s", pdir)
            continue
        scanned_any_dir = True
        dir_key = str(pdir)
        if dir_key in _SCANNED_DIRS:
            continue
        _SCANNED_DIRS.add(dir_key)

        for fname in sorted(_os.listdir(pdir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            stem = fname[:-3].lower()
            if enabled and stem not in enabled:
                log.debug("插件 %s 不在启用清单，跳过。", fname)
                report["skipped"].append({"file": str(pdir / fname), "reason": "不在启用清单"})
                continue
            mod_path = pdir / fname
            spec_name = f"wme_plugins.{stem}"
            try:
                spec = importlib.util.spec_from_file_location(spec_name, mod_path)
                if spec is None or spec.loader is None:
                    log.warning("无法解析插件模块：%s", mod_path)
                    report["failed"].append({"file": str(mod_path), "error": "spec 解析失败"})
                    continue
                module = importlib.util.module_from_spec(spec)
                # 注册到 sys.modules，避免插件内部相对导入时找不到自己。
                _sys.modules[spec_name] = module
                _CURRENT_PLUGIN_FILE = str(mod_path)
                try:
                    # 阶段 77：直接取源码编译执行，**绕开 .pyc 字节码缓存**。
                    #   `spec.loader.exec_module()` 会读写 __pycache__，而其缓存命中
                    #   判据是「源文件 mtime（秒级）+ 字节大小」——同一秒内修改且长度
                    #   不变（常见的改字/改常量）会命中陈旧字节码，表现为「改了插件不
                    #   生效」。插件目录是用户频繁手改的位置，语义上必须始终执行最新
                    #   源码，故此处显式 get_source + compile，不做缓存。
                    source = spec.loader.get_source(spec_name)
                    code = compile(source, str(mod_path), "exec")
                    exec(code, module.__dict__)  # noqa: S102 —— 插件机制的既定语义
                except Exception:
                    # 阶段 77 P4：导入/执行失败必须摘除残留，避免半初始化模块污染进程。
                    _sys.modules.pop(spec_name, None)
                    raise
                finally:
                    _CURRENT_PLUGIN_FILE = ""
                count += 1
                report["loaded"].append(str(mod_path))
                log.info("已加载插件文件：%s", mod_path)
            except Exception as exc:  # noqa: BLE001
                log.warning("插件加载失败 %s：%s", mod_path, exc)
                log.debug(traceback.format_exc())
                report["failed"].append({"file": str(mod_path), "error": str(exc)})

    if scanned_any_dir:
        _LOADED = True

    # 就地更新最近一次扫描结果；`overridden` 由 register_engine 跨批次累积，不在此重置。
    _LAST_REPORT["loaded"] = report["loaded"]
    _LAST_REPORT["failed"] = report["failed"]
    _LAST_REPORT["skipped"] = report["skipped"]
    return count


def _ensure_loaded(force: bool = False) -> None:
    """惰性保证插件已扫描（首次查询时触发；目录缺失时下次会重试）。"""
    if force or not _LOADED:
        load_plugins(force=force)


def reload_plugins() -> int:
    """
    重新扫描插件目录（开发期热加载用，阶段 77 新增）。

    行为：先注销全部**非内建**引擎，再清空扫描记录并重扫。
    内建引擎（lama/opencv）不受影响。返回本次成功加载的插件文件数。
    """
    for key in list(_REGISTRY):
        if not _is_builtin_engine(_REGISTRY[key]):
            _REGISTRY.pop(key, None)
    _LAST_REPORT["overridden"] = []
    log.info("已注销全部非内建引擎，准备重新扫描插件目录。")
    return load_plugins(force=True)


def get_plugin_report() -> dict:
    """
    返回最近一次插件扫描的结构化结果（阶段 77 新增，供界面 / CLI / API 观测）。

    返回值：
        {
          "loaded":     [文件路径, ...],              # 成功加载
          "failed":     [{"file":..., "error":...}],  # 加载失败（含原因）
          "skipped":    [{"file":..., "reason":...}], # 跳过（如不在启用清单）
          "overridden": [引擎名, ...],                # 被覆盖的引擎名（如有）
          "engines":    [引擎清单字典, ...],           # 等价 get_engine_detail()
        }
    """
    _ensure_loaded()
    return {
        "loaded": list(_LAST_REPORT["loaded"]),
        "failed": list(_LAST_REPORT["failed"]),
        "skipped": list(_LAST_REPORT["skipped"]),
        "overridden": list(_LAST_REPORT["overridden"]),
        "engines": get_engine_detail(),
    }


# ==============================================================================
# 第 5 节：自检
# ==============================================================================
if __name__ == "__main__":
    # 验证框架本身：注册一个临时引擎、查询、再清除。
    class _TestPlugin(InpaintEnginePlugin):
        name = "test_engine"
        display_name = "测试引擎"
        description = "自检临时引擎"

        def inpaint(self, image, mask, *, device=None, progress=None):
            return image

    class _BadNamePlugin(InpaintEnginePlugin):
        name = "Bad Name!"
        display_name = "非法名"

        def inpaint(self, image, mask, *, device=None, progress=None):
            return image

    class _FakeLama(InpaintEnginePlugin):
        name = "lama"
        display_name = "伪装引擎"

        def inpaint(self, image, mask, *, device=None, progress=None):
            return image

    assert register_engine(_TestPlugin) is True, "合法引擎应注册成功"
    assert has_engine("test_engine"), "注册后应能查到引擎"
    assert "test_engine" in get_engine_choices(), "动态选项应包含插件引擎"
    info = get_engine("test_engine")
    assert info is not None and info.name == "test_engine", "get_engine 应返回实例"
    # 内建引擎始终存在。
    assert has_engine("lama") and has_engine("opencv") and has_engine("auto")
    # 阶段 77：非法名拒绝；内建不可被静默覆盖；清单与实际一致。
    assert register_engine(_BadNamePlugin) is False, "非法名应被拒绝"
    assert register_engine(_FakeLama) is False, "默认不允许覆盖内建引擎"
    lama_info = next(e for e in list_engines() if e.name == "lama")
    assert lama_info.builtin is True, "lama 仍应为内建"
    assert lama_info.display_name == "LaMa 深度学习", "清单应与注册表实例一致"

    # ---- 阶段 128 加固：保留名 + 内建身份按类型判定 ----
    class _AutoName(InpaintEnginePlugin):
        name = "auto"
        display_name = "偷名 auto"

        def inpaint(self, image, mask, *, device=None, progress=None):
            return image

    class _Masq(InpaintEnginePlugin):
        name = "masq_check"
        display_name = "伪装内建"
        is_builtin = True  # 插件不应设置；框架须按类型判定、忽略之

        def inpaint(self, image, mask, *, device=None, progress=None):
            return image

    assert register_engine(_AutoName) is False, "保留名 auto 应被拒绝注册"
    assert "auto" not in _REGISTRY, "auto 不得进入注册表"
    assert register_engine(_Masq) is True
    _mi = next(e for e in list_engines() if e.name == "masq_check")
    assert _mi.builtin is False, "插件不得靠 is_builtin 属性伪装为内建"
    assert _is_builtin_engine(_Masq()) is False, "内建身份须按实例类型判定"
    assert _is_builtin_engine(_REGISTRY["lama"]) is True

    # 清理测试引擎，避免污染真实注册表。
    _REGISTRY.pop("test_engine", None)
    _REGISTRY.pop("masq_check", None)
    print("core/plugins.py 自检通过：注册表 + 动态选项 + 内建引擎 + 名称校验 + "
          "覆盖保护 + 保留名 + 内建身份按类型判定 均正常。")
