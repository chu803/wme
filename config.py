# -*- coding: utf-8 -*-
"""
==============================================================================
 项目名称 : AI 智能水印擦除工作台 (AI Watermark Eraser Workbench)
 文件名称 : config.py
 文件说明 : 全局参数配置中枢。全项目所有可调参数集中在此文件，
            其他任何模块都不允许硬编码路径、阈值、端口号等常量。
 版 本 号 : 1.0.0
 版权声明 : Copyright (c) 2026 AI 智能水印擦除工作台. All Rights Reserved.
------------------------------------------------------------------------------
 【合规强制声明 —— 使用前请务必阅读】
   1. 本工具专用于去除使用者本人生成的 AI 图片上的可见水印 / 署名标识。
   2. 严禁使用本工具清除他人享有著作权作品上的水印、署名或版权标识。
   3. 不建议用于去除 AI 绘图平台的第三方署名水印（如 Midjourney），
      此类行为通常违反对应平台的服务条款，本工具不鼓励该用途。
   4. 依据《中华人民共和国著作权法》第五十三条，未经权利人许可，
      故意删除或者改变作品的权利管理电子信息的，应当承担停止侵害、
      赔偿损失等民事责任；情节严重的，可由主管部门责令停止侵权行为、
      没收违法所得、处以罚款。
   5. 使用者因违规使用本工具产生的一切法律后果，由使用者本人承担，
      与本工具作者无关。
------------------------------------------------------------------------------
 【零基础使用者须知】
   这个文件就像一台机器的「控制面板」。
   你不需要看懂每一行代码，只需要知道：
     · 想改端口号 → 找 SERVER 这一节
     · 想改输出目录 → 找 PATH 这一节
     · 觉得修复效果不满意 → 找 INPAINT 这一节
   每个参数上方都有中文注释，说明它是干什么的、调大调小会发生什么。
   改完保存文件，重启程序即可生效。
------------------------------------------------------------------------------
 【进阶用法：不改代码也能改配置】
   在项目根目录新建一个名为 .env 的文本文件，写入形如：
       WME_SERVER_PORT=8080
       WME_DEVICE_MODE=cpu
   程序启动时会自动读取并覆盖本文件中的默认值。
   这样做的好处是：升级项目代码时，你的个性化配置不会被覆盖掉。
==============================================================================
"""

from __future__ import annotations

# ------------------------------------------------------------------------------
# 标准库导入
# ------------------------------------------------------------------------------
import json                    # 读取/写入用户覆盖配置（阶段 35）
import os                      # 读取环境变量、拼接路径
import sys                     # 判断是否运行在 PyInstaller 打包环境中
from pathlib import Path       # 现代化路径处理，天然兼容中文路径与斜杠方向
from typing import Any, Final  # 类型注解，Final 表示「该常量不应被修改」


# ==============================================================================
# 第 0 节：.env 环境变量文件加载
# ------------------------------------------------------------------------------
# 说明：python-dotenv 是可选依赖。如果用户没装，程序也要能正常启动，
#       因此这里用 try/except 做了容错处理，绝不因为缺一个库就崩溃。
# ==============================================================================
try:
    from dotenv import load_dotenv  # type: ignore

    # 自动查找项目根目录下的 .env 文件并加载。
    # override=False 表示：如果系统里已经设置了同名环境变量，以系统的为准。
    load_dotenv(override=False)
    _DOTENV_LOADED: bool = True
except Exception:
    # 未安装 python-dotenv，或 .env 文件格式有误，都走这里。
    # 这不是致命错误，程序继续使用代码中的默认值即可。
    _DOTENV_LOADED = False


# ==============================================================================
# 第 1 节：环境变量读取辅助函数
# ------------------------------------------------------------------------------
# 说明：用户在 .env 里写的都是字符串，需要转换成对应的 Python 类型。
#       同时要防止用户填错（比如端口填成 "abc"）导致程序崩溃，
#       所以每个函数都做了异常兜底，转换失败就退回默认值。
# ==============================================================================

# 所有环境变量的统一前缀，避免与系统里其他软件的变量重名。
# WME = Watermark Eraser Workbench 的首字母缩写。
ENV_PREFIX: Final[str] = "WME_"


def _env_str(key: str, default: str) -> str:
    """
    从环境变量读取一个字符串配置。

    参数说明：
        key     : 变量名（不含前缀）。例如传入 "SERVER_HOST"，
                  实际读取的是 "WME_SERVER_HOST"。
        default : 读不到时使用的默认值。

    返回值：
        读取到的字符串；若环境变量不存在或为空白，则返回 default。
    """
    raw: str | None = os.getenv(ENV_PREFIX + key)
    # 判断三种情况：变量不存在、变量为空字符串、变量全是空格 —— 都算无效。
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _env_int(key: str, default: int, min_value: int | None = None,
             max_value: int | None = None) -> int:
    """
    从环境变量读取一个整数配置，并做范围限制。

    参数说明：
        key       : 变量名（不含前缀）。
        default   : 读不到或转换失败时使用的默认值。
        min_value : 允许的最小值。传 None 表示不限制下限。
        max_value : 允许的最大值。传 None 表示不限制上限。

    返回值：
        限制在 [min_value, max_value] 区间内的整数。

    设计意图：
        用户可能把端口号填成 999999（超出 65535 上限），
        如果直接使用会导致程序启动失败且报错信息晦涩难懂。
        这里主动把非法值拉回合法区间，保证程序永远能启动。
    """
    raw: str | None = os.getenv(ENV_PREFIX + key)
    if raw is None or raw.strip() == "":
        value: int = default
    else:
        try:
            value = int(raw.strip())
        except ValueError:
            # 用户填了非数字内容（例如 "abc"），静默退回默认值。
            value = default

    # 下限裁剪。
    if min_value is not None and value < min_value:
        value = min_value
    # 上限裁剪。
    if max_value is not None and value > max_value:
        value = max_value
    return value


def _env_float(key: str, default: float, min_value: float | None = None,
               max_value: float | None = None) -> float:
    """
    从环境变量读取一个浮点数配置，并做范围限制。

    参数说明：
        key       : 变量名（不含前缀）。
        default   : 读不到或转换失败时使用的默认值。
        min_value : 允许的最小值。传 None 表示不限制下限。
        max_value : 允许的最大值。传 None 表示不限制上限。

    返回值：
        限制在 [min_value, max_value] 区间内的浮点数。

    阶段 145 修复：**NaN 会穿透钳制**
    ---------------------------------
    ``nan < min_value`` 与 ``nan > max_value`` **都返回 False** ⇒ 下面两段钳制
    对 NaN **完全不生效**，它会原样穿透到业务代码。实测：

        WME_X=nan  →  返回 nan   （既不是 default，也不是任何钳制值）
        WME_X=inf  →  返回 max   （能被正常钳制，故不在本修复范围内）
        WME_X=-inf →  返回 min

    危害链（本阶段实测）：``WME_DETECT_TRANSLUCENT_SENSITIVITY=nan``
    ⇒ ``sensitivity=nan`` ⇒ 检测器返回**零掩码**（静默零检出，经验 142 同族），
    而 ``validate_config`` 报绿、无任何告警 —— 用户以为「设了一个值」，
    实际得到的是「什么都检不出来」。
    修法 = 用 ``value != value``（NaN 是唯一不等于自身的值）识别并回退 default。
    **只拦 NaN**：inf 路径保持既有钳制行为，零行为变更。
    """
    raw: str | None = os.getenv(ENV_PREFIX + key)
    if raw is None or raw.strip() == "":
        value: float = default
    else:
        try:
            value = float(raw.strip())
        except ValueError:
            value = default
        # 阶段 145：NaN 穿透钳制的单点拦截（见 docstring「阶段 145 修复」）。
        if value != value:
            value = default

    if min_value is not None and value < min_value:
        value = min_value
    if max_value is not None and value > max_value:
        value = max_value
    return value


# 布尔字面量识别表（**全项目唯一真值来源**，阶段 80）：
#   环境变量路径（_env_bool）与用户覆盖文件路径（_USER_OVERRIDE_VALIDATORS）
#   必须共用同一套规则——否则会出现「同一份配置、两条入口、两种结果」。
_BOOL_TRUE_LITERALS: frozenset = frozenset({"1", "true", "yes", "y", "on", "是", "开"})
_BOOL_FALSE_LITERALS: frozenset = frozenset({"0", "false", "no", "n", "off", "否", "关"})


def parse_bool(value, default: bool) -> bool:
    """
    把「布尔 / 数字 / 字符串」统一解析为布尔值。

    这是全项目布尔解析的**唯一真值来源**，被两条配置入口共用：
      · 环境变量（`WME_*`）：经 `_env_bool()`；
      · 用户覆盖文件 `config.user_overrides.json`：经 `_USER_OVERRIDE_VALIDATORS`。

    参数说明：
        value   : 待解析的值（bool / int / float / str / 其他类型）。
        default : 无法识别时返回的默认值。

    识别规则（字符串先 strip 再小写）：
        True  ：`"1"` / `"true"` / `"yes"` / `"y"` / `"on"` / `"是"` / `"开"`
        False ：`"0"` / `"false"` / `"no"` / `"n"` / `"off"` / `"否"` / `"关"`
        其他  ：一律返回 `default`——**不猜测、更不静默反转**。

    为什么需要它（阶段 80 修复的缺陷）：
        此前覆盖文件路径用的是 `bool(v)`，而 Python 里 `bool("false") is True`——
        用户把开关写成字符串 `"false"` / `"0"` / `"no"` / `"off"` / `"关"` 时会被
        **解释为「开启」**，与环境变量路径的行为**完全相反**：
        例如 `{"INPAINT_DUAL_RUN": "false"}` 会意外开启双跑择优（耗时翻倍）、
        `{"REAL_SUPERRES_ENABLED": "0"}` 会意外开启真实超分。
    """
    # bool 必须最先判断：Python 中 isinstance(True, int) 为真，顺序写错会把 True 当数字。
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # 数字只接受 0 / 1 这两个明确信号；2 / 3 / NaN 等一律退回默认，不做隐式转换。
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _BOOL_TRUE_LITERALS:
            return True
        if text in _BOOL_FALSE_LITERALS:
            return False
    return default


def _env_bool(key: str, default: bool) -> bool:
    """
    从环境变量读取一个布尔（是/否）配置。

    参数说明：
        key     : 变量名（不含前缀）。
        default : 读不到时使用的默认值。

    返回值：
        True 或 False。

    识别规则（大小写不敏感）：
        认定为 True  的写法：1 / true / yes / y / on  / 是 / 开
        认定为 False 的写法：0 / false / no / n / off / 否 / 关
        其他任何写法一律退回 default，避免歧义。

    说明（阶段 80）：规则实现已抽到 `parse_bool()`，与用户覆盖文件路径**共用同一实现**。
    """
    raw: str | None = os.getenv(ENV_PREFIX + key)
    if raw is None or raw.strip() == "":
        return default
    return parse_bool(raw, default)


def _env_list(key: str, default: list[str]) -> list[str]:
    """
    从环境变量读取一个「逗号分隔的列表」配置（用于插件目录、启用清单等）。

    参数说明：
        key     : 变量名（不含前缀）。
        default : 读不到时使用的默认列表。

    返回值：
        去空白后的字符串列表。空元素会被丢弃。
    """
    raw: str | None = os.getenv(ENV_PREFIX + key)
    if raw is None or raw.strip() == "":
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


def _env_json(key: str, default):
    """
    从环境变量读取一个「JSON 文本」配置（用于结构化配置，如模型槽清单）。

    参数说明：
        key     : 变量名（不含前缀）。
        default : 解析失败或读不到时使用的默认值。

    返回值：
        解析后的 Python 对象（list / dict 等）；失败回退 default。
    """
    import json as _json

    raw: str | None = os.getenv(ENV_PREFIX + key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return _json.loads(raw)
    except Exception:
        # JSON 解析失败时不崩溃，回退默认，避免一个错误的环境变量拖垮启动。
        return default


# ==============================================================================
# 第 2 节：项目根目录定位
# ------------------------------------------------------------------------------
# 说明：这是全项目最容易出 BUG 的地方，必须重点处理。
#       原因在于「直接用 python 运行」和「打包成 EXE 后运行」，
#       程序所在的目录位置完全不同：
#         · 直接运行：根目录 = config.py 所在目录
#         · EXE 运行：Python 解释器与源码被解压到临时目录 sys._MEIPASS，
#                     但用户的图片、模型、日志应该放在 EXE 文件旁边，
#                     而不是那个开机就会被清空的临时目录里。
#       下面的函数就是用来正确区分这两种情况的。
# ==============================================================================

def _detect_project_root() -> Path:
    """
    自动探测项目根目录，兼容源码运行与 PyInstaller 打包运行两种场景。

    返回值：
        项目根目录的绝对路径（Path 对象）。

    判定逻辑：
        1. 如果 sys 模块里存在 frozen 属性且为 True，说明当前跑在打包环境；
           此时取 sys.executable（即 EXE 文件本身）所在的目录作为根目录。
        2. 否则说明是普通源码运行，取本文件（config.py）所在目录作为根目录。
    """
    # getattr 的第三个参数是「取不到时的默认值」，这样写不会因属性不存在而报错。
    is_frozen: bool = bool(getattr(sys, "frozen", False))

    if is_frozen:
        # 打包场景：EXE 文件所在目录。
        # 用户双击 D:\工具\水印擦除.exe，根目录就是 D:\工具\
        return Path(sys.executable).resolve().parent

    # 源码场景：config.py 所在目录就是项目根目录。
    # __file__ 是当前文件的路径，resolve() 把它转成绝对路径并解开软链接。
    return Path(__file__).resolve().parent


# 项目根目录。这是所有其他路径的计算基准，全项目唯一真值来源。
PROJECT_ROOT: Final[Path] = _detect_project_root()


def _resolve_dir(env_key: str, default_name: str) -> Path:
    """
    解析一个目录配置项，支持用户通过环境变量指定绝对路径。

    参数说明：
        env_key      : 环境变量名（不含前缀）。例如 "OUTPUT_DIR"。
        default_name : 默认的子目录名。例如 "outputs"。

    返回值：
        最终生效的目录绝对路径。

    行为规则：
        · 用户未配置 → 使用 项目根目录/default_name
        · 用户配置了绝对路径（如 D:\\我的图片）→ 直接使用该路径
        · 用户配置了相对路径（如 ..\\共享盘）→ 相对项目根目录解析
    """
    custom: str = _env_str(env_key, "")
    if custom == "":
        return PROJECT_ROOT / default_name

    custom_path: Path = Path(custom)
    if custom_path.is_absolute():
        return custom_path.resolve()
    # 相对路径：以项目根目录为基准展开。
    return (PROJECT_ROOT / custom_path).resolve()


# ==============================================================================
# 第 3 节：目录路径配置（PATH）
# ------------------------------------------------------------------------------
# 说明：这一节定义程序运行时要用到的全部目录。
#       零基础用户如果想把输出图片存到 D 盘，只需要在 .env 里写：
#           WME_OUTPUT_DIR=D:\我的修复成果
# ==============================================================================

# 【模型目录】存放 LaMa 模型权重文件 big-lama.pt（约 200MB）。
#   如果你已经在别处下载过这个模型，可以把目录指过去，避免重复下载占用磁盘。
MODEL_DIR: Final[Path] = _resolve_dir("MODEL_DIR", "models")

# 【输出目录】修复完成的成品图片保存位置。
#   程序会在此目录下按日期自动建子文件夹，例如 outputs/2026-08-03/
OUTPUT_DIR: Final[Path] = _resolve_dir("OUTPUT_DIR", "outputs")

# 【导出目录】阶段 6「导出服务」的产物落点：对比图、按格式重编码的成品、
# 历史记录打包 ZIP 等都会先写到这里，与常规修复成品（outputs）区分开，
# 避免把导出中间产物和原图修复结果混在一起。
EXPORT_DIR: Final[Path] = _resolve_dir("EXPORT_DIR", "exports")

# 【缓存目录】存放处理记录、缩略图、任务元数据。
#   删除此目录不会丢失成品图，只会丢失「历史记录」标签页里的列表。
CACHE_DIR: Final[Path] = _resolve_dir("CACHE_DIR", "cache")

# 【日志目录】存放运行日志文件，排查报错时需要提供此目录下的文件。
LOG_DIR: Final[Path] = _resolve_dir("LOG_DIR", "logs")

# 【临时目录】存放推理过程中的中间切片文件，程序会自动清理。
TEMP_DIR: Final[Path] = _resolve_dir("TEMP_DIR", "temp")

# 【静态资源目录】存放界面用的 CSS、图标、示例图片。
ASSETS_DIR: Final[Path] = _resolve_dir("ASSETS_DIR", "assets")


def ensure_directories() -> list[Path]:
    """
    确保上述所有目录都真实存在，不存在则自动创建。

    返回值：
        本次实际新创建的目录列表（已存在的目录不会出现在列表里）。

    调用时机：
        由 main.py 在程序启动最开始处调用一次。
        这样即使用户手滑删了 logs 文件夹，下次启动也能自动恢复，
        而不是抛出「目录不存在」的报错让人摸不着头脑。
    """
    created: list[Path] = []
    # 需要保证存在的目录清单。注意不含 PROJECT_ROOT，它必然已存在。
    targets: tuple[Path, ...] = (
        MODEL_DIR,
        OUTPUT_DIR,
        EXPORT_DIR,
        CACHE_DIR,
        LOG_DIR,
        TEMP_DIR,
        ASSETS_DIR,
    )

    for directory in targets:
        if not directory.exists():
            # parents=True  : 如果上级目录也不存在，一并创建（类似 mkdir -p）
            # exist_ok=True : 万一在多线程下被别人抢先创建了，不要报错
            directory.mkdir(parents=True, exist_ok=True)
            created.append(directory)

    return created


# ==============================================================================
# 第 4 节：LaMa 模型配置（MODEL）
# ------------------------------------------------------------------------------
# 说明：LaMa 全称 Large Mask Inpainting，是目前开源界处理「大面积遮挡修复」
#       效果最好的模型之一。本项目使用其 TorchScript 导出版本（.pt 单文件），
#       优点是无需安装原作者那一大堆训练框架依赖，加载即用。
# ==============================================================================

# 【模型权重文件名】不建议修改，除非你自己训练了新模型。
MODEL_FILENAME: Final[str] = _env_str("MODEL_FILENAME", "big-lama.pt")

# 【模型权重完整路径】由目录 + 文件名拼接而成，供 model_loader 直接使用。
MODEL_PATH: Final[Path] = MODEL_DIR / MODEL_FILENAME

# 【模型下载地址列表】按顺序尝试，前面的失败了自动切换到后面的。
#   这样设计是因为国内网络访问 GitHub 经常超时，需要备用镜像兜底。
MODEL_DOWNLOAD_URLS: Final[tuple[str, ...]] = (
    # 主源：GitHub Release 直链（.pt 格式，与 MODEL_FILENAME 完全一致）
    "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt",
    # 备源 1：GitHub 加速镜像（国内可直连，.pt 格式）
    "https://ghproxy.net/https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt",
    # 备用源：HuggingFace 上的 ONNX 版（仅当你改用 ONNX 推理引擎时才启用，
    #         本工具默认使用 TorchScript .pt，download_model.py 会自动按扩展名筛选）
    "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
)

# 【模型文件预期大小】单位：字节。用于下载完成后校验文件是否完整。
#   约 205MB。若实际下载文件小于该值的 90%，判定为下载不完整需重试。
MODEL_EXPECTED_SIZE: Final[int] = 205_000_000

# 【模型文件 SHA256 校验值】留空表示跳过哈希校验（仅校验文件大小）。
#   填写后可防止下载到被篡改或损坏的权重文件。
MODEL_SHA256: Final[str] = _env_str("MODEL_SHA256", "")

# 【下载超时时间】单位：秒。网络差的用户可调大，例如设为 300。
MODEL_DOWNLOAD_TIMEOUT: Final[int] = _env_int("MODEL_DOWNLOAD_TIMEOUT", 120, 10, 3600)

# 【下载失败重试次数】每个下载源最多重试几次后才切换到下一个源。
MODEL_DOWNLOAD_RETRY: Final[int] = _env_int("MODEL_DOWNLOAD_RETRY", 3, 1, 10)

# 【启动时是否自动下载模型】
#   True  : 检测到模型缺失时自动开始下载（推荐，零基础用户友好）
#   False : 仅提示用户手动执行 download_model.py
MODEL_AUTO_DOWNLOAD: Final[bool] = _env_bool("MODEL_AUTO_DOWNLOAD", True)

# 【启动预热模型】控制程序启动时是否「预先加载一次模型」以给出明确的引擎状态。
#   True  : 启动阶段就把 LaMa 模型加载进内存（或确认走 OpenCV 降级），
#           让用户在浏览器打开前就清楚知道「本次用 LaMa 还是 OpenCV」。
#   False : 延迟到第一次真正处理图片时才加载，启动更快，但首张图会稍慢。
STARTUP_WARMUP_MODEL: Final[bool] = _env_bool("STARTUP_WARMUP_MODEL", True)

# ------------------------------------------------------------------------------
# 第 4.x 节：模型管理增强（MODEL MANAGER，阶段 12 新增）
# ------------------------------------------------------------------------------
# 说明：阶段 11 已支持「插件引擎」（换算法），阶段 12 进一步支持「多模型权重切换」
#       （同一套 LaMa TorchScript 接口，可挂多个 .pt 权重并随时切换）。
#       思路：每个权重文件是一个「模型槽（ModelSlot）」，内置槽 lama 指向
#       config.MODEL_PATH；用户可在 models/ 目录放下其它 .pt 自动被发现，
#       也可在 MODEL_SLOTS 里显式登记。其中一个槽被标记为「当前模型（active）」，
#       LaMa 引擎一律加载 active 槽的权重。详见 docs/12-模型管理增强设计.md。
# ------------------------------------------------------------------------------

# 【当前模型槽名称】程序使用的模型槽 key（默认 lama）。
#   也可用 WME_ACTIVE_MODEL 覆盖；运行时切换会持久化到 MODEL_STATE_FILE。
ACTIVE_MODEL: Final[str] = _env_str("ACTIVE_MODEL", "lama")

# 【额外模型槽清单】结构化配置，每个元素是一个 dict，字段：
#   name        : 槽唯一键（小写），如 "lama_fp32"
#   display_name: 界面显示名，如 "LaMa FP32 版"
#   filename    : 权重文件名（放在 MODEL_DIR 下），如 "big-lama-fp32.pt"
#   expected_size: 预期字节数（可选，用于下载后校验）
#   sha256      : SHA256 校验值（可选）
#   urls        : 下载地址列表（可选）
#   note        : 备注
#   示例（写在 config.py 里）：
#   MODEL_SLOTS = [{"name":"lama_fp32","display_name":"LaMa FP32","filename":"big-lama-fp32.pt","expected_size":205000000}]
#   也可用环境变量覆盖（JSON 文本）：
#   WME_MODEL_SLOTS=[{"name":"lama_fp32","display_name":"LaMa FP32","filename":"big-lama-fp32.pt","expected_size":205000000}]
MODEL_SLOTS: Final[list] = _env_json(
    "MODEL_SLOTS",
    [
        # 默认无额外槽；在 models/ 放 .pt 会自动发现，或在此登记。
    ],
)

# 【当前模型持久化文件】记录运行时切换到的模型槽，重启后仍生效。
#   放在 MODEL_DIR 下，文件名以 . 开头避免与权重混淆。
MODEL_STATE_FILE: Final[Path] = MODEL_DIR / ".active_model.json"

# 【模型备份后缀】下载/覆盖前，先把现有权重备份为「原路径 + 此后缀」，
#   下载校验失败时自动回滚到此备份，保证「升级翻车」也能一键还原。
MODEL_BACKUP_SUFFIX: Final[str] = ".bak"

# 【自动发现模型】是否在 MODEL_DIR 下自动扫描 .pt / .onnx 文件并注册为模型槽。
#   True  : 在 models/ 放下权重即可在「模型管理」页看到，零配置（推荐）。
#   False : 仅识别内置槽与 MODEL_SLOTS 显式登记的槽。
MODEL_AUTO_DISCOVER: Final[bool] = _env_bool("MODEL_AUTO_DISCOVER", True)

# 【未启用 GPU 时的友好提示】
#   True  : 当程序因「没装 CUDA 版 PyTorch」而退回 CPU 时，在控制台打印
#           一条如何开启显卡加速的指引（不影响功能，纯提示）。
STARTUP_GPU_HINT: Final[bool] = _env_bool("STARTUP_GPU_HINT", True)


# ==============================================================================
# 第 5 节：硬件与设备配置（DEVICE）
# ------------------------------------------------------------------------------
# 说明：本项目支持 NVIDIA 显卡加速与纯 CPU 兼容两种运行模式。
#       默认 auto 会自动探测，绝大多数用户不需要改动这一节。
# ==============================================================================

# 【设备模式】三选一：
#   auto : 自动探测。有可用的 NVIDIA 显卡就用显卡，否则退回 CPU（推荐）
#   cuda : 强制使用 NVIDIA 显卡。若显卡不可用，程序会报错并退回 CPU
#   cpu  : 强制使用 CPU。适合显卡显存太小、或显卡驱动有问题的情况
DEVICE_MODE: Final[str] = _env_str("DEVICE_MODE", "auto").lower()

# 【显存安全阈值】单位：MB。
#   显卡可用显存低于该值时，自动降级为 CPU 模式，防止推理途中显存爆掉。
#   LaMa 处理 1024×1024 图片约需 2GB 显存，这里留出 2048MB 作为安全线。
DEVICE_MIN_VRAM_MB: Final[int] = _env_int("DEVICE_MIN_VRAM_MB", 2048, 512, 65536)

# 【CPU 推理线程数】
#   0 表示自动设置为「物理核心数」，这是经验上最快的配置。
#   若你希望跑图时电脑还能流畅做别的事，可手动调小，例如设为 2。
CPU_THREADS: Final[int] = _env_int("CPU_THREADS", 0, 0, 256)

# 【是否启用半精度推理】仅在显卡模式下生效。
#   True  : 使用 float16，显存占用减半、速度更快，画质损失几乎不可见
#   False : 使用 float32，兼容性最好，老显卡（GTX 10 系以前）建议关闭
DEVICE_USE_FP16: Final[bool] = _env_bool("DEVICE_USE_FP16", True)

# 【推理完成后是否立即释放显存】
#   True  : 每张图处理完就清空显存缓存，适合显存小的显卡（4GB 及以下）
#   False : 保留缓存，连续处理多张图时速度更快，但显存占用较高
DEVICE_EMPTY_CACHE_AFTER_RUN: Final[bool] = _env_bool("DEVICE_EMPTY_CACHE_AFTER_RUN", True)


# ==============================================================================
# 第 6 节：图像修复参数配置（INPAINT）
# ------------------------------------------------------------------------------
# 说明：这一节是「修复效果好不好」的关键。界面上的参数调节面板，
#       其滑块的默认值、最小值、最大值全部读取自这里。
#       如果你发现某个参数的调节范围不够用，就在这里改上下限。
# ==============================================================================

# ---------- 6.1 修复引擎选择 ----------
# 【默认修复引擎】三选一：
#   lama   : LaMa 深度学习模型。能凭空补出纹理内容，但在大面积/渐变区域易产生
#            幻觉纹理（阶段 51 benchmark：合成数据 8/8 用例上 ΔPSNR 为负、劣于 OpenCV）
#   opencv : OpenCV 传统算法。速度极快、无需模型、不会幻觉，是大多数水印的稳妥选择
#   auto   : 自动选择。默认偏向 opencv（见 INPAINT_AUTO_PREFER），模型缺失时回退 opencv
INPAINT_ENGINE: Final[str] = _env_str("INPAINT_ENGINE", "auto").lower()

# 【auto 模式下偏好的引擎】字符串，取值 "opencv" / "lama"（阶段 55 新增，默认 opencv）。
#   阶段 51 公平对比（合成数据，同一份检测掩码喂两引擎）结论：OpenCV 在 8/8 用例占优、
#   LaMa 在多数用例起负作用（ΔPSNR 低至 −25.19 dB）。故 auto 默认偏向 opencv，
#   避免「有模型就无脑用 LaMa」导致的静默劣化。若想恢复旧行为（auto 有模型即走 LaMa），
#   设环境变量 WME_INPAINT_AUTO_PREFER=lama 即可；也可始终显式选 engine="lama"。
INPAINT_AUTO_PREFER: Final[str] = _env_str("INPAINT_AUTO_PREFER", "opencv").lower()

# 【双跑择优开关】布尔（阶段 74 新增，默认 False = 部署级放行闸，与阶段 61/67 同范式）。
#   开启后每次修复会用「主引擎 + 副引擎」（见 INPAINT_DUAL_SECONDARY）各跑一次，
#   用无参考质量分（core.quality.no_reference_inpaint_score，阶段 56）择优取结果。
#   何时值得开：真实照片上 LaMa 与 OpenCV 谁强尚未定论（合成数据 OpenCV 8/8 占优，
#   但 LaMa 强项需真实照片才能翻案）——双跑让单张图自动取两者中更自洽的结果。
#   代价：修复耗时约翻倍（副引擎多为 LaMa，CPU 单图 3~4s），故默认关闭。
#   主/副引擎相同、副引擎不可用（缺模型）时自动跳过双跑，退回单引擎。
INPAINT_DUAL_RUN: Final[bool] = _env_bool("INPAINT_DUAL_RUN", False)

# 【双跑择优的副引擎】字符串（阶段 74 新增，默认 lama）。
#   与主引擎不同且可用时才参与双跑；主引擎由 --engine / INPAINT_ENGINE 决定。
INPAINT_DUAL_SECONDARY: Final[str] = _env_str(
    "INPAINT_DUAL_SECONDARY", "lama"
).lower()

# 【双跑择优的平局容差】浮点 ≥0（阶段 74 新增，默认 1e-6）。
#   两引擎无参考分数之差 ≤ 该容差时视为平局，取主引擎（保守：不因噪声换引擎）。
INPAINT_DUAL_EPS: Final[float] = _env_float("INPAINT_DUAL_EPS", 1e-6, 0.0, 1.0)

# 【OpenCV 备选算法】两选一：
#   telea : Alexandru Telea 快速行进算法。速度快，适合线条状水印
#   ns    : Navier-Stokes 流体力学算法。边缘过渡更自然，适合块状水印
OPENCV_INPAINT_METHOD: Final[str] = _env_str("OPENCV_INPAINT_METHOD", "telea").lower()

# 【OpenCV 修复半径】单位：像素。
#   算法会参考水印周围这个半径范围内的像素来「猜」被遮住的内容。
#   调大：过渡更平滑，但容易糊；调小：更锐利，但可能留下痕迹。
OPENCV_INPAINT_RADIUS: Final[int] = _env_int("OPENCV_INPAINT_RADIUS", 5, 1, 50)

# ---------- 6.2 修复力度 ----------
# 【修复力度默认值】取值 0.0 ~ 1.0。
#   这是一个「混合系数」：控制修复结果与原图的融合比例。
#   1.0 = 完全采用模型生成的内容（水印擦得最干净）
#   0.5 = 模型结果与原图各占一半（水印变淡，保留部分原始质感）
#   0.0 = 完全保留原图（等于没处理）
#   建议：半透明水印用 0.85 ~ 1.0，实心 LOGO 用 1.0。
INPAINT_STRENGTH_DEFAULT: Final[float] = _env_float("INPAINT_STRENGTH_DEFAULT", 1.0, 0.0, 1.0)
INPAINT_STRENGTH_MIN: Final[float] = 0.0
INPAINT_STRENGTH_MAX: Final[float] = 1.0
INPAINT_STRENGTH_STEP: Final[float] = 0.05

# ---------- 6.3 掩码扩张 ----------
# 【掩码扩张默认值】单位：像素（阶段 65 标定为 2，**阶段 105 修正为 0**）。
#   把标记的水印区域向外「撑大」若干像素再送去修复，用于吞掉水印边缘的半透明羽化像素。
#
#   ⚠️ 阶段 105 结论：**膨胀是净损失，默认归零**。两条独立证据链：
#
#   (1) 代价（真实照片端到端基准，10 图 × 4 类，opencv；`scripts/e2e_real_probe.py` 口径）
#       外溢（改动落在掩码之外的比例）几乎完全由膨胀决定，羽化贡献 ≤1.1%：
#         dilate 0 → 0.0% / 1 → 17.8% / 2 → 30.0% / 3 → 38.8%（每 +1px ≈ +11~12pp）
#       同时 collateral 1.799→2.376、ΔPSNR −6.69→−7.34 dB、去痕率 25.4%→24.3%
#       —— 三项**同时变差**。膨胀对残留并无收益（`docs/105`）。
#       数学下界：外溢 ≈ D·周长/面积（改动区 ⊆ support(proc_mask) 且引擎不在掩码外改动），
#       故「外溢 ≤5%」与 D≥1 不可兼得 —— 这不是调参问题，是包含关系问题。
#
#   (2) 阶段 65 认定的「细字需要膨胀」是**混杂结论**：当时的扫描只在一维上变档
#       （feather 仅在 d==默认值 时变，从未测过 (d=0, f=0) 这个角），于是把
#       「羽化冲淡细笔画」的锅记到了膨胀头上。补齐 2×2 角后（`docs/105`）：
#         残留（越小越好）D=0,F=5 → 12.72 / 12.64   ← 旧默认思路（D=2,F=5）9.95 / 8.44
#                        D=0,F=0 →  9.09 /  8.48   ← 真因是 F，不是 D
#       阶段 105 已修掉「羽化冲淡掩码内部」（见 `core.inpainter._prepare_mask` 第 4 步），
#       修后 (D=0,F=5) 残留 9.10 / 8.48、过度重绘像素 1498 / 1515（旧默认 3137 / 2889），
#       外溢 0.7% ⇒ **新默认在残留、破坏面积、外溢三项上全面优于 (D=2,F=5)**，唯一解。
#   仍然有用的场景：手绘掩码边缘明显偏小、或某类水印修复后出现零散残留点时，可手动调大。
MASK_DILATE_DEFAULT: Final[int] = _env_int("MASK_DILATE_DEFAULT", 0, 0, 100)
MASK_DILATE_MIN: Final[int] = 0
MASK_DILATE_MAX: Final[int] = 100
MASK_DILATE_STEP: Final[int] = 1

# ---------- 6.4 边缘平滑（羽化）----------
# 【边缘羽化默认值】单位：像素，必须是奇数（高斯核要求）。
#   让「修复区」与「原图区」的接缝处平滑过渡，避免出现生硬的方块边框。
#   0 表示不羽化；数值越大过渡越柔和，但也越容易让边缘发虚。
#   ⚠️ 阶段 105 起语义收窄：羽化**只在掩码外侧**产生软坡，不再冲淡掩码内部
#   （`_prepare_mask` 第 4 步 `max(soft, binary)`）。此前它对细笔画（1~2px）会把融合
#   权重压到约 0.3，造成「修了但只修三成」的残影，并被误记为「细字需要膨胀」。
#   代价上限：核 5 的支撑半径为 2px ⇒ 外溢上界 ≈ 2·周长/面积（实测整批 0.7%）。
MASK_FEATHER_DEFAULT: Final[int] = _env_int("MASK_FEATHER_DEFAULT", 5, 0, 51)
MASK_FEATHER_MIN: Final[int] = 0
MASK_FEATHER_MAX: Final[int] = 51
MASK_FEATHER_STEP: Final[int] = 2

# 【边缘平滑阈值】取值 0 ~ 255。
#   掩码是一张黑白图，这个值决定「灰到什么程度才算需要修复的区域」。
#   调低：更多灰色边缘被纳入修复范围（擦得更彻底，但影响面更大）
#   调高：只修复纯白的核心区域（更保守，可能残留边缘）
MASK_THRESHOLD_DEFAULT: Final[int] = _env_int("MASK_THRESHOLD_DEFAULT", 127, 0, 255)
MASK_THRESHOLD_MIN: Final[int] = 0
MASK_THRESHOLD_MAX: Final[int] = 255
MASK_THRESHOLD_STEP: Final[int] = 1

# ---------- 6.5 降噪参数 ----------
# 【降噪强度默认值】取值 0 ~ 30。
#   修复后对结果做一次「非局部均值降噪」，抹平模型生成内容里的细微噪点。
#   0 表示关闭降噪（速度最快，保留最多细节）
#   建议：照片类图片设 3 ~ 5，插画类图片设 0（避免丢失笔触细节）
DENOISE_STRENGTH_DEFAULT: Final[int] = _env_int("DENOISE_STRENGTH_DEFAULT", 3, 0, 30)
DENOISE_STRENGTH_MIN: Final[int] = 0
DENOISE_STRENGTH_MAX: Final[int] = 30
DENOISE_STRENGTH_STEP: Final[int] = 1

# 【是否只对修复区域降噪】
#   True  : 只处理被修复的那一小块，原图其余部分保持像素级不变（推荐）
#   False : 对整张图降噪，画面更统一，但会牺牲原图未受影响区域的锐度
DENOISE_ONLY_MASKED: Final[bool] = _env_bool("DENOISE_ONLY_MASKED", True)


# ---------- 6.6 交互式掩码精修（阶段 24）----------
# 【精修默认模式】"merge" 或 "manual"：
#   "merge"（合并补漏）：检测掩码 ∪ 用户精修 一起纳入修复。
#                        检测结果预填进编辑框，用户在其上补画漏检 / 擦除误检，
#                        最终掩码 = 编辑框所见（已含检测预填层）。
#   "manual"（仅精修）：只用用户亲手绘制的掩码，自动剥离预填的检测层。
MASK_EDIT_DEFAULT_MODE: Final[str] = _env_str("MASK_EDIT_DEFAULT_MODE", "merge")
MASK_EDIT_ALLOWED_MODES: Final[tuple] = ("merge", "manual")

# 【预填图层颜色】RGB 元组。自动检测后把掩码以该颜色半透明图层预填进
#   ImageEditor，用户一眼就能看到「机器认为哪里需要修」。
MASK_EDIT_LAYER_COLOR: Final[tuple] = (255, 59, 48)  # 番茄红

# 【精修画笔默认尺寸】单位：像素（ImageEditor Brush default_size）。
MASK_EDIT_BRUSH_DEFAULT: Final[int] = _env_int("MASK_EDIT_BRUSH_DEFAULT", 40, 4, 200)


# ---------- 6.7 检测自动预设推荐（阶段 25）----------
# 【总开关】是否启用「自动预设推荐」。仅当用户未显式选择 preset（preset=None）
#   且 auto_preset=True（CLI/服务/界面默认开启）时生效，自动分析图像特征挑预设。
RECOMMEND_ENABLED: Final[bool] = _env_bool("RECOMMEND_ENABLED", True)
# 【默认回退预设】无任何特征命中时返回的预设（ safest 综合预设）。
RECOMMEND_DEFAULT_PRESET: Final[str] = _env_str("RECOMMEND_DEFAULT_PRESET", "general")
# 【渐变判据】平面解释率阈值（与 DETECT_GRADIENT_MIN_EXPLAINED 同源思路）。
#   高于此值且平面幅度足够、残差平滑 → 推荐 ai_gradient。
RECOMMEND_GRADIENT_MIN_EXPLAINED: Final[float] = _env_float(
    "RECOMMEND_GRADIENT_MIN_EXPLAINED", 0.6, 0.0, 1.0
)
RECOMMEND_GRADIENT_MIN_PLANE_MAG: Final[float] = _env_float(
    "RECOMMEND_GRADIENT_MIN_PLANE_MAG", 12.0, 0.0, 255.0
)
# 【网格判据】周期自相关强度阈值 + 标记占比带（与 DETECT_GRID_* 同源）。
#   周期强度高于此值且占比在带内 → 推荐 ai_grid。
RECOMMEND_GRID_MIN_STRENGTH: Final[float] = _env_float(
    "RECOMMEND_GRID_MIN_STRENGTH", 0.35, 0.0, 1.0
)
RECOMMEND_GRID_MIN_COVERAGE: Final[float] = _env_float(
    "RECOMMEND_GRID_MIN_COVERAGE", 0.003, 0.0, 1.0
)
RECOMMEND_GRID_MAX_COVERAGE: Final[float] = _env_float(
    "RECOMMEND_GRID_MAX_COVERAGE", 0.06, 0.0, 1.0
)
# 【平铺判据】平铺（非网格）水印判定：标记占比高于此值即视为重复平铺
#   （区别于稀疏点阵网格，网格占比通常 < 0.15）。低于此值视为「零散半透明」
#   而非「平铺」，回退到 general。
RECOMMEND_TILED_MIN_COVERAGE: Final[float] = _env_float(
    "RECOMMEND_TILED_MIN_COVERAGE", 0.08, 0.0, 1.0
)
# 【极淡平铺残差阈值】平铺水印的「平均残差幅度」低于此值视为极淡 → 推荐
#   ai_faint_tiled（更激进），否则推荐 ai_tiled。
RECOMMEND_FAINT_RESIDUAL: Final[float] = _env_float(
    "RECOMMEND_FAINT_RESIDUAL", 6.0, 0.0, 255.0
)
# 【实体 LOGO 判据】角落实心轮廓的实心率阈值（与 DETECT_LOGO_SOLIDITY 同源）。
#   角落出现实心轮廓（高实心率、宽高比合理）→ 推荐 ai_logo。
RECOMMEND_LOGO_SOLIDITY: Final[float] = _env_float(
    "RECOMMEND_LOGO_SOLIDITY", 0.85, 0.0, 1.0
)
RECOMMEND_LOGO_MAX_ASPECT: Final[float] = _env_float(
    "RECOMMEND_LOGO_MAX_ASPECT", 8.0, 1.0, 50.0
)
# 【文字密度判据】整图笔画（边缘）密度高于此值 → 推荐 ai_text_heavy；
#   仅底部边缘带密度高 → 推荐 ai_signature。
RECOMMEND_TEXT_DENSITY: Final[float] = _env_float(
    "RECOMMEND_TEXT_DENSITY", 0.04, 0.0, 1.0
)
RECOMMEND_SIGNATURE_BAND_DENSITY: Final[float] = _env_float(
    "RECOMMEND_SIGNATURE_BAND_DENSITY", 0.06, 0.0, 1.0
)
# 【平铺规则性判据】频域规则性（_spectral_regularity）阈值。用于把「填充式平铺」
#   与「满图随机文字笔画」区分开：二者边缘密度都高，但平铺在频域呈尖锐晶格峰
#   （regular 高），文字笔画频域能量弥散（regular 低）。高于此值才允许进入平铺分支。
RECOMMEND_REGULARITY_MIN: Final[float] = _env_float(
    "RECOMMEND_REGULARITY_MIN", 0.05, 0.0, 1.0
)

# 【推荐器标记绝对残差下限】0~64（阶段 59 新增，默认 3）。
#   _marks_at 旧实现用「百分位」选像素（q = 100 − 0.85·敏感度），在纹理/照片底图上
#   恒选出 ~46.75% 像素——覆盖/周期/规则性特征全被背景纹理主导（grid 水印被误判为
#   general、faint_tiled 误判为文字），与阶段 49 修掉的「恒定比例选像素」同一反模式。
#   改为稳健统计门限：标记 = (残差 ≥ 绝对下限) ∧ (归一化分数 > 中位数 + k·稳健σ)。
#   实测（640²，默认值）干净 textured/photo 覆盖率 ~0.1%，grid 点阵 0.6~1.1%。
RECOMMEND_MARKS_ABS_MIN: Final[float] = _env_float(
    "RECOMMEND_MARKS_ABS_MIN", 3.0, 0.0, 64.0
)
# 【推荐器标记稳健σ倍数】1.0 ~ 10.0（阶段 59 新增，默认 4.5）。越大标记越稀疏
#   （越保守、误报越低），越小越灵敏（小水印更可能被标记）。
RECOMMEND_MARKS_SIGMA_K: Final[float] = _env_float(
    "RECOMMEND_MARKS_SIGMA_K", 4.5, 1.0, 10.0
)
# 【推荐器角标最小边长占比】0.0 ~ 0.5（阶段 59 新增，默认 0.04）。
#   角落实心轮廓的最小边须 ≥ 该比例 × min(图宽, 图高)，否则视为文字字形而非品牌 LOGO：
#   底部小字首字母的字形轮廓（约 44×18，最小边仅 ~2.8% 图宽）曾满足面积/实心率判据
#   而被误判为 ai_logo → 启用错误的角标预设、漏掉真实的底部文字水印（召回 25.8%→98%）。
RECOMMEND_LOGO_MIN_SIDE_FRACTION: Final[float] = _env_float(
    "RECOMMEND_LOGO_MIN_SIDE_FRACTION", 0.04, 0.0, 0.5
)

# 【角标水印模板匹配信号（阶段 450 新增）】默认开。
#   背景：豆包类「AI 生成」水印是固定在**右下角（部分在中右）**的平台角标板。推荐器刻意
#   不调 OCR（避免无 torch 环境崩溃）。早期曾试过「右下角边缘密度 ÷ 其余三角」不对称性启发
#   式，但实测**不可靠**：角标是半透明/低对比（亮板或暗叠加），边缘密度既抓不全真值
#   （全量阈值扫描最优仅 6/75 命中、漏 69），又误命中自然图（底部纹理右下更密）→ 故废弃。
#   改用**模板匹配**（边缘图归一化互相关 NCC，对亮度极性不敏感）：豆包角标字形在全部图上
#   一致，用 9 张真值图右下 ROI 边缘平均作模板。全量 88 图标定：NCC≥0.5 时精确率 100%
#   （0 自然图误命中）、召回 40%（30/75 角标图命中）；NCC≥0.4 时召回 69%/精确 90%。
#   正式默认取 0.5（零误命中、fail-closed）：只把**高置信**角标图导向 ai_logo（edge=False），
#   跳过 edge 策略的 OCR 空跑；其余角标图维持原推荐（不回归）。需要更高召回可调低阈值
#   （代价是引入少量自然图误命中）。ai_logo 已是 quality_baseline.json 已承诺预设，召回由
#   corner 策略（含 search_bottom）兜底。
RECOMMEND_CORNER_WATERMARK_ENABLED: Final[bool] = _env_bool(
    "RECOMMEND_CORNER_WATERMARK_ENABLED", True
)
# NCC 阈值下限：右下/中右 ROI 边缘图与模板的归一化互相关（含极性翻转）≥ 此值才判为角标。
# 范围 0~1；越高越保守（精确率优先）。默认 0.40 = 零自然图误命中（全量 88 图标定：
# TP=25/FP=0/召回33%/精确100%）。需更高召回可降至 0.30（召回65% 但 FP=5 自然图误命中）。
RECOMMEND_CORNER_WATERMARK_NCC_MIN: Final[float] = _env_float(
    "RECOMMEND_CORNER_WATERMARK_NCC_MIN", 0.4, 0.0, 1.0
)
# ROI 最小平均边缘幅值（sanity floor）：低于此值说明该区域近乎纯色，不值得做匹配。
RECOMMEND_CORNER_WATERMARK_MIN_EDGE: Final[float] = _env_float(
    "RECOMMEND_CORNER_WATERMARK_MIN_EDGE", 5.0, 0.0, 255.0
)


# ==============================================================================
# 第 7 节：智能水印自动检测参数（DETECT）
# ------------------------------------------------------------------------------
# 说明：对应需求中的「智能自动模式」。
#       自动检测本质上是「用一堆图像特征规则去猜哪里是水印」，
#       它不可能 100% 准确，因此界面上必须允许用户预览并手动修正掩码。
# ==============================================================================

# 【是否启用半透明水印检测】
#   原理：半透明水印会让所在区域的局部对比度与饱和度出现异常规律，
#         通过频域分析与局部方差可以圈出可疑区域。
DETECT_TRANSLUCENT_ENABLED: Final[bool] = _env_bool("DETECT_TRANSLUCENT_ENABLED", True)

# 【半透明水印敏感度】取值 0.0 ~ 1.0。
#   调高：能揪出更淡的水印，但误判率上升（可能把天空、渐变背景也圈进去）
#   调低：只检测明显水印，漏检率上升
DETECT_TRANSLUCENT_SENSITIVITY: Final[float] = _env_float(
    "DETECT_TRANSLUCENT_SENSITIVITY", 0.55, 0.0, 1.0
)

# 【是否启用角落 LOGO 检测】
#   原理：绝大多数水印位于图片四角或正中，先按位置划定重点搜索区，
#         再在区内做文字/图形检测，可大幅降低误判。
DETECT_CORNER_LOGO_ENABLED: Final[bool] = _env_bool("DETECT_CORNER_LOGO_ENABLED", True)

# 【角落检测区域占比】取值 0.05 ~ 0.5。
#   表示从每个角落向内取「边长 = 图片宽/高 × 该比例」的正方形作为搜索区。
#   0.25 表示取四个角各 1/4 边长的区域。
DETECT_CORNER_RATIO: Final[float] = _env_float("DETECT_CORNER_RATIO", 0.25, 0.05, 0.5)

# 【角落 MSER 降级通路开关】布尔（阶段 100 新增，**默认 False**）。
#
# 背景（阶段 100 真值审计，docs/100-*）
# ------------------------------------
# `detect_corner_logo` 的候选来源有三条：白牌判据（阶段 86）、OCR、以及
# `_mser_boxes_in_patch`（MSER，OCR 的降级通路）。审计用双基准量化了第三条：
#
#   30 张真实图（general 预设）：MSER 产出 2569 个框，命中（IoU≥0.3）15 个
#     ⇒ 误报率 99.4%；关掉它 → 召回**零变化**，覆盖率 12.66%→7.45%（−41%）、
#     精确率 0.0392→0.1382（3.5×）、mask_iou 0.0699→0.1163（+66%）、
#     区域数 84.8/图→18.9/图。corner_logo 自身随之从「2.4% 精确率」跃升到
#     「32 框 / 30 命中 / 平均 IoU 0.79 / 只占掩码 0.34%」。
#   12 个合成用例：唯 `photo/corner_logo` 变化（精确率 0.620→0.914、
#     ΔPSNR 10.41→15.76），其余 11 例**逐位不变**，召回同样零变化。
#
# 为什么是「加开关」而不是「加判据」
# --------------------------------
# 探针实测（docs/100 §3）**不存在**与内容无关的判据能把它的 15 个真命中与
# 自然纹理块分开：真命中实心度仅 0.25~0.58（`solidity ≥ 0.7` 会杀掉 100%
# 真命中，只削 93.8% 误报），边缘锐度 P90 ≥ 100 只削 6.5% 误报。MSER 找的是
# 「稳定极值区」，而自然照片本身就由大量稳定极值区构成——这是**判据层面的
# 不可分**，不是阈值没调好。
#
# 为什么默认关闭而不是删除
# ------------------------
# 沿用 `DETECT_LOWRES_RESCUE`（阶段 67）确立的同一取舍：按项目铁律「宁可漏检，
# 不可滥杀」（阶段 49），一个双基准零召回贡献、却贡献 99.4% 误报的通路，应当
# 作为**显式 opt-in** 放行，而不是留在默认链里。保留可开启的理由是它并非
# 「错误实现」——在「OCR 不可用 + 角标为深色/彩色非白牌」的组合下它仍可能有用，
# 只是真实基准覆盖不到该组合（30 图真值全为角标且白牌通路已 30/30 命中）。
#
# 与阶段 86 裁决的关系
# -------------------
# 阶段 86 加入白牌通路时写明「两者取并集，并集恒优于替换」（阶段 67 取舍），
# 且其 docstring **已登记** MSER 在 30 图上的最佳 IoU 均值仅 0.042。该裁决
# 针对的是「多一个候选来源」，并未涉及「该来源的净增量是正是负」——本开关
# 补的正是这遗漏的一环，不推翻原裁决：MSER 仍可与白牌通路并集，只是需要
# 显式开启。
DETECT_CORNER_MSER_ENABLED: Final[bool] = _env_bool("DETECT_CORNER_MSER_ENABLED", False)

# ==============================================================================
# 阶段 86：白底圆角板块判据（corner_logo 策略的第二候选来源）
# ------------------------------------------------------------------------------
# 背景（方向 A 真实图审计 F3）：
#   AI 平台最常见的角标形态是「白色圆角牌 + 深色描边 + 深色字形」（左上角
#   `AI` 徽标）。在 30 张真实 1024² AI 图上，该角标此前**完全无人负责**：
#     · `detect_corner_logo` 的 MSER / Otsu 通路对 30 图的最佳 IoU 均值仅 0.042
#       （最大 0.521，且是无对齐的偶发命中）——MSER 找的是「稳定极值区」，
#       而白牌在多数底图上要么不构成极值区（0 个区域），要么被同色背景吞并
#       成整块（如纯色天空底图），**判据与目标形态不匹配**；
#     · 实际命中该角标的是 translucent 残差通路，但它的框随底图对比度漂移：
#       底图对比强时框会「渗漏」成 138×84 / 415×40 的大块（IoU 0.156 / 0.109），
#       导致整张图召回记 0.5。30 图中 3 张（15/20/24）因此被压在 0.5。
#
# 判据（探针标定，见 docs/86）：
#   在角落搜索区内取 **V ≥ 阈值 且 S ≤ 阈值**（HSV 的明度/饱和度）的连通域：
#   白牌是「绝对够亮 + 绝对够灰」的紧凑圆角块，与底图色彩/明暗无关。
#
#   为什么必须是**绝对量**：实测白牌与其周边环的亮度差 ΔV 在亮底图上为负
#   （日出图 −41.8、白墙图 −45.7）、暗底图上为正（+72.0 ~ +77.9），
#   即「牌比周围亮」这个相对判据在亮底图上是**反向**的——这又一次印证了
#   阶段 49/59 的教训「优先绝对量，相对量只兜底」。饱和度则有单向性：
#   牌 S 恒 ≤ 68，环 S 恒 ≥ 21（多数 > 150），故 S 上限是安全判据。
#
#   为什么**不做形态学闭运算**：形状门限曾因 `CLOSE(5,5)` 从 30/30 掉到 16/30——
#   白牌的深色描边本身就是「牌」与「底图」之间的天然分隔带，闭运算会跨过这
#   2px 描边把两者连通，于是「牌」不再是与边界不相交的孤立块而被丢弃。
#   这条经验已写入测试（`test_plate_detection_is_closing_free`）。
#
# 【是否启用白底圆角板块判据】布尔（阶段 86 新增，默认 True）。
DETECT_PLATE_ENABLED: Final[bool] = _env_bool("DETECT_PLATE_ENABLED", True)

# 【白牌判据：明度下限】整数 0~254（阶段 86 新增，默认 140）。
#   实测 30 张真实图上白牌填充区 V 恒 ≥ 140（牌内均值 142~240，含字形），
#   阈值 140 对全部 30 图给出 IoU 0.85 的检出框，**无一漏检**。
#   调高：只认更亮的牌（深色底图上的半透明白牌可能掉出）；调低：底图亮部会大量入选。
DETECT_PLATE_V_MIN: Final[int] = _env_int("DETECT_PLATE_V_MIN", 140, 0, 254)

# 【白牌判据：饱和度上限】整数 0~255（阶段 86 新增，默认 80）。
#   实测白牌填充区 S 恒 ≤ 68（牌内均值 13~84），与阈值有 12 灰阶余量；
#   而牌外环境 S 中位数远高于此（彩色底图常 > 150）。
#   调高：彩色底图上的白牌更易入选，但彩色亮块误报增多；调低：偏色底图上的牌会掉出。
DETECT_PLATE_S_MAX: Final[int] = _env_int("DETECT_PLATE_S_MAX", 80, 0, 255)

# 【白牌判据：实心度下限】浮点 0~1（阶段 86 新增，默认 0.70）。
#   实心度 = 连通域面积 / 外接框面积。白牌是圆角矩形，实测正样本恒 ≥ 0.7698、
#   负样本（其余三角 + 中心共 150 个搜索区）最高仅 0.763 —— 间隔只有 0.007，
#   单靠实心度**没有安全窗**，因此取 0.70（正样本下方留 0.07 余量）并配合
#   下面的墨迹密度判据共同生效。调低：误报上升；调高：牌自身圆角更大会被误杀。
DETECT_PLATE_SOLIDITY: Final[float] = _env_float("DETECT_PLATE_SOLIDITY", 0.70, 0.0, 1.0)

# 【白牌判据：墨迹密度下限】浮点 0~1（阶段 86 新增，默认 0.05）。
#   牌内「比该连通域中位亮度更暗（≤ INK_RATIO × 中位）」的像素占外接框比例。
#   语义是「这块牌上确实有字/图形」——角标/徽标必然内含字形或描边，而天空、
#   白墙等纯净亮块没有。实测正样本 0.179~0.183、负样本 ≥0.45 者居多，
#   取 0.05 作**存在性闸门**（而非精度最大化器）：solid≥0.70 且 ink≥0.05 时
#   30/30 正样本全保留、150 个搜索区只剩 3 个误报（≈0.1 候选/图）。
DETECT_PLATE_INK_MIN: Final[float] = _env_float("DETECT_PLATE_INK_MIN", 0.05, 0.0, 1.0)

# 【白牌判据：墨迹参考比】浮点 0~1（阶段 86 新增，默认 0.55）。
#   判定「某像素是墨迹」的阈值为 `INK_RATIO × 连通域亮度中位数`。
DETECT_PLATE_INK_RATIO: Final[float] = _env_float("DETECT_PLATE_INK_RATIO", 0.55, 0.05, 1.0)

# 【白牌判据：最小面积占比】浮点 0~1（阶段 86 新增，默认 0.0004）。
#   白牌 @1024² 为 45×36 px（占比 0.0015），故下限定在它的约 1/4，
#   以容纳分辨率更低或被平台缩小的输入。与 DETECT_MIN_AREA_RATIO 同锚点体系。
DETECT_PLATE_MIN_AREA_RATIO: Final[float] = _env_float(
    "DETECT_PLATE_MIN_AREA_RATIO", 0.0004, 0.0, 0.1
)

# 【白牌判据：最大面积占比】浮点 0~1（阶段 86 新增，默认 0.02）。
#   白牌 @1024² 占比 0.0015，上限取 0.02（约 13 倍余量）既能容纳大分辨率下的
#   角标，又足以把「整片天空 / 整面白墙」这类大亮块排除在外。
DETECT_PLATE_MAX_AREA_RATIO: Final[float] = _env_float(
    "DETECT_PLATE_MAX_AREA_RATIO", 0.02, 0.0005, 0.5
)

# 【白牌判据：最大宽高比】浮点 1~10（阶段 86 新增，默认 2.5）。
#   白牌实测 45/36 = 1.25（模板固有 1.22~1.36），角标形态多为近方形；
#   取 2.5 允许适度横向的徽标，同时排除长条状结构（栏杆、条纹、门框线）。
DETECT_PLATE_MAX_ASPECT: Final[float] = _env_float(
    "DETECT_PLATE_MAX_ASPECT", 3.2, 1.0, 10.0
)
#   阶段 173：默认由 2.5 放宽到 3.2 —— 真实平台角标（如「豆包AI」徽标）实测
#   足迹 340x128 ⇒ 宽高比 **2.66**，在原上限 2.5 下**恒被拒**（哪怕亮块完整）。

# 【白牌判据：掩码闭运算核】整数 0~40（阶段 173 新增，默认 21）。
#   背景：板上有深色字形 / 头像时，`(V>=V_MIN)&(S<=S_MAX)` 的亮像素被墨迹**切成碎片**
#   ⇒ 连通域 bbox 只覆盖牌的一部分（实测 10 张真实豆包角标图上，检测掩码仅覆盖
#   真值框的 **35%**；只有纯白底那张覆盖 0.95）。闭运算把被墨迹切开的亮块桥接回一块，
#   使 bbox 覆盖整块牌。**按图像长边等比**（0.010 × 长边：2048² → 21px），
#   与既有 `DETECT_PLATE_MIN_SIDE` 的缩放体系一致；0 = 关闭（回到阶段 86 行为）。
DETECT_PLATE_CLOSE_FRAC: Final[float] = _env_float(
    "DETECT_PLATE_CLOSE_FRAC", 0.010, 0.0, 0.05
)

# 【白牌闭运算的触发门】浮点 0~1（阶段 173 新增，默认 0.35）。
#   闭运算在**亮底图**上会把牌与背景一起并入 ⇒ 反而丢框（实测雪景 0.72 → 0.37）。
#   故仅在「亮像素占比不高（即背景不亮、牌确实是块孤立亮区）」时才闭运算：
#   `bin_c.mean() <= 本门限` 才做。1.0 = 无条件（回到朴素闭运算）。
DETECT_PLATE_CLOSE_MAX_FILL: Final[float] = _env_float(
    "DETECT_PLATE_CLOSE_MAX_FILL", 0.35, 0.0, 1.0
)

# 【白牌判据：局部提亮并集源】浮点 0~0.5（阶段 176 新增，默认 0.25）。
#   背景：半透明白牌在中低对比底上「既不够亮也不够低饱和」⇒ 被 V/S 判据漏掉
#   （实测 07 玻璃牌 Smean=109、08 抽象 Smean=110、06 花田 Vmean=126）。
#   局部提亮 `lift = V − blur(V, k)` 对半透明牌天然成立（牌 = 底图的提亮版）。
#   开启时把 `lift > DELTA` 的像素**并集**进白牌亮掩码——
#   whiteness 管亮底、lift 管中低对比底（实测互补：lift 在纯白底上无效，覆盖 0.02）。
#   `k = FRAC × 图像长边`（尺度相对）。0 = 关闭（回到纯 whiteness）。
#   ⚠ 阶段 176 实测**两种并集都不可用**（docs/176，登记防重犯）：
#     · **掩码级并集**（把 lift 并进亮掩码）= **净损失**：造出「混合连通域」把原本能
#       工作的图挤掉，mask_recall 0.562 → **0.388**（neon .85→.44 / fruit .66→.15），
#       且 4 张失败图**毫无改善**；
#     · **候选级并集**（lift 单独跑一遍过滤、只并框——即本杠杆当前实现）= **完全 no-op**：
#       10 张图逐图逐位不变（0.562），说明 **lift 的连通块过不了既有过滤**
#       （min_side / aspect<=3.2 / solidity>=0.7 / ink>=0.05 是按**白色牌**标定的）。
#   ⇒ **默认 0.0（关闭）**。若日后再试：lift 需要**自己的过滤标定**（单独的
#   aspect/solidity/ink 阈值），不能复用 whiteness 的那套。
DETECT_PLATE_LIFT_FRAC: Final[float] = _env_float("DETECT_PLATE_LIFT_FRAC", 0.0, 0.0, 0.5)
# 【局部提亮阈值】整数 1~80（默认 10）。`lift` 超过它才算「比邻域亮」。
DETECT_PLATE_LIFT_DELTA: Final[int] = _env_int("DETECT_PLATE_LIFT_DELTA", 10, 1, 80)

# 【生成器感知牌体补全先验】布尔（阶段 179 新增，默认 False）。
#   背景：阶段 178 归因——4 张低对比底（06 花田/07 玻璃/08 抽象/09 打字机）上
#   `corner_logo`（白牌）通路**完全不触发**（0 区域）；当前那 3~39% 召回 100% 来自
#   `translucent`/`edge_text` 在右下角牌区的碎片命中。根因：**没有任何通路在低对比底上
#   重构出完整的 340x128 牌体**——半透明牌的实心填充在任何亮度/相对量启发式下都不可分。
#   阶段 172-A 已客观证实：豆包角标是固定 **340x128 圆角板**，恒在右下角、随图尺寸等比缩放。
#   本先验利用该不变式做**信号锚定的牌体补全**：
#     当某「角搜索区」内有 `translucent`/`edge_text` 证据（**不是盲猜位置**）但无
#     `corner_logo` 命中时，在该角按规范化足迹补发一个 `corner_logo` 区域
#     （尺寸 = 340x128 × min(h,w)/2048，贴该角外侧）。
#   ⚠ **已知精度留白**：当前基准 10 张全为带水印豆包图、**无干净图**，故「干净图上误发整牌」
#     的误报率**不可测量**。默认关闭，启用即承担该留白；待补齐干净图基准再标定红线。
#   ⚠ 该先验是**生成器感知（豆包特异性）**的，非通用水印判据；落点为独立可逆杠杆。
DETECT_PLATE_COMPLETE_FROM_SIGNAL: Final[bool] = _env_bool(
    "DETECT_PLATE_COMPLETE_FROM_SIGNAL", False
)

# 【牌体补全先验：牌内证据锚定阈值】浮点 0.0~1.0，默认 0.50（阶段 182 新增）。
#   179 先验的收紧守卫：至少一个 translucent/edge_text 证据区域与**牌足迹自身**
#   （340x128 @2048² 贴右下角）重叠 ≥ 该比例 × 区域自身面积，才允许补发整牌
#   ——编码「豆包 logo 证据在牌内」的几何不变式。
#   实测分离面（阶段 182 探针，n=15）：真实豆包牌证据 max fp_ov ≥ 0.54（10/10 张）；
#   干净图右下角文字/招牌证据 max fp_ov ≤ 0.46（180 误报源 syn_02=0.00 / syn_05=0.46）。
#   置 0 关闭守卫（回到 179 原始行为：信号碰右下角搜索区即补）。
DETECT_PLATE_PRIOR_ANCHOR_OVERLAP: Final[float] = _env_float(
    "DETECT_PLATE_PRIOR_ANCHOR_OVERLAP", 0.50, 0.0, 1.0
)

# 【白牌判据：最小边长】整数 2~256，锚点 640（阶段 86 新增，默认 12）。
#   与所有尺度相关门限同一套锚点体系：实际门限 = 本值 × (max(h,w)/DETECT_MIN_AREA_REF)。
#   白牌在 640 等效尺度下短边约 22px，取 12 留约 2 倍余量。
DETECT_PLATE_MIN_SIDE: Final[int] = _env_int("DETECT_PLATE_MIN_SIDE", 12, 2, 256)

# 【是否启用边缘小字检测】
#   针对贴在图片上下边缘的一行小字（如网站域名、拍摄参数条）。
DETECT_EDGE_TEXT_ENABLED: Final[bool] = _env_bool("DETECT_EDGE_TEXT_ENABLED", True)

# 【边缘检测带宽占比】取值 0.02 ~ 0.3。
#   从图片上下左右四条边向内取「厚度 = 短边 × 该比例」的长条作为搜索区。
DETECT_EDGE_BAND_RATIO: Final[float] = _env_float("DETECT_EDGE_BAND_RATIO", 0.12, 0.02, 0.3)

# 【边缘小字绝对残差门限】取值 8 ~ 128 灰阶（阶段 52 新增，默认 42）。
#   阶段 51 诊断发现：底部边缘小字在「类自然纹理 / 照片」底图上的召回仅 14%
#   （gradient 上为 87%），根因是 _projection_boxes 的 Otsu 二值化 + 相对投影阈值
#   在纹理底图上被背景方差淹没——清晰字模的绝对对比度（白字 vs 局部背景）被
#   相对阈值稀释，几乎整行都判不成文字。
#
#   本参数是「绝对残差」通路的闸门：对边缘带做高斯平滑得到局部基线，取
#   |灰度 − 基线| 的残差（即「与周围邻域显著不同」的像素），残差超过本值的
#   视为候选墨迹。清晰字模即便在高纹理背景上也有 100+ 灰阶的绝对对比，而纹理
#   的残差经高斯平滑后被抑制，因此该通路能在不依赖相对阈值的前提下找回字模。
#
#   安全设计：残差二值图经投影后的「沿交叉轴铺满整条带」区域会被既有的
#   max_cross(0.85×长边) 约束丢弃（纹理散点正是这种情况）。实测扫描证实这一点：
#   干净图平均误报率在 abs_delta 全区间恒为 **1.03%**，即第二通路的误报增量为零。
#
#   实测扫描（阶段 52，min_fill=0.04，3 种子均值，见 docs/52）：
#       abs_delta    gradient / textured / photo 的 edge_text 召回
#           0           86.6% / 15.3% / 18.3%   ← 阶段 51 基线（第二通路关闭）
#          25           99.0% / 99.8% / 99.6%
#          35           99.0% / 98.6% / 98.2%
#          45           98.7% / 98.2% / 98.1%   ← 默认
#          60           96.2% / 93.1% / 95.1%
#          80           89.7% / 57.6% / 72.7%
#
#   取 45（而非召回最高的 25）：25~45 是召回平台区（均 ≥98%），60 之后开始下滑、
#   80 崩塌。选 45 是平台区内**精确率最高**的点，符合项目「宁可漏检、不可滥杀」
#   的一贯取舍——它比 35 只低 0.4pp 召回，却换来明显更高的精确率与更小的框。
#
#   默认 45/0.04 的完整质量（3 种子均值）：
#       底图        召回     精确率    IoU
#       gradient   98.7%    35.6%    35.4%   （基线 86.6% / 32.8% / 31.3%）
#       textured   98.2%    52.2%    51.7%   （基线 15.3% / 36.1% / 11.2%）
#       photo      98.1%    57.0%    56.4%   （基线 18.3% / 36.1% / 11.2%）
#   —— 召回与 IoU 大幅提升的同时精确率不降反升，说明框并没有膨胀到把整条底边
#      包住（预测覆盖率仅 1.4%~1.6%，真值覆盖率 0.83%），是「框准了」而非「框大了」。
DETECT_EDGE_ABS_DELTA: Final[float] = _env_float("DETECT_EDGE_ABS_DELTA", 45.0, 8.0, 128.0)

# 【边缘小字绝对残差基线模糊半径】取值 3 ~ 31（阶段 52 新增，默认 11）。
#   求局部基线时使用的高斯模糊 sigma。越大 → 基线越平滑 → 残差越偏向「高频细节」，
#   越能抑制大尺度纹理、只留住字模边缘；过小则纹理本身的高频部分会混入残差造成误报。
#   取 11：能压制 base_scale≈8 的分形纹理（波长 ~80px 远小于 3σ≈33px 的平滑尺度），
#   又足以保留字模（字模笔画宽度 > 1px，残差在笔画处集中且强度高）。
DETECT_EDGE_ABS_BLUR: Final[float] = _env_float("DETECT_EDGE_ABS_BLUR", 11.0, 3.0, 31.0)

# 【边缘小字绝对残差行填充率下限】取值 0.01 ~ 0.5（阶段 52 新增，默认 0.06）。
#   绝对残差通路判定「某一行是不是文字行」的门限：该行被点亮的墨迹像素数 ÷ 行长度。
#
#   为什么用绝对填充率、而不是沿用 Otsu 通路的「均值 + 1.5×标准差」：
#   实测扫描 abs_delta 时，相对判据给出的召回随门限剧烈跳变（14%→42%→14%→51%，
#   毫无单调性）。根因是相对判据把「是否成行」建立在**本带自身的投影统计量**上——
#   二值图稍有变化，均值/标准差随之抖动，整行判定就翻转。这与阶段 49 修掉的
#   「分位数恒定选中固定比例像素」是同一类缺陷：判据随内容自适应，而不是对内容
#   做绝对要求。改用填充率后召回随 abs_delta 单调变化，因而可调、可预测、可复现。
#
#   取值依据：文字笔画在一行内是密集的（同一行中相当比例的像素被点亮），
#   而纹理散点稀疏。调高则只保留很粗很密的字，调低则噪声行也可能入选。
#
#   实测对比（abs_delta=45，3 种子均值，textured / photo 的 edge_text 召回）：
#       min_fill=0.04 → 98.2% / 98.1%，且 25~60 都是 ≥93% 的宽平台（更稳）
#       min_fill=0.06 → 94.1% / —   ，平台更窄（60 已掉到 82.6%）
#       min_fill=0.10 → 81.2% / 82.9%
#   取 0.04：三种取值下干净图误报都恒为 1.03%（填充率不是误报来源），
#   而 0.04 的召回平台最宽、对 abs_delta 的敏感度最低，最不容易在真实图上翻车。
DETECT_EDGE_ABS_ROW_FILL: Final[float] = _env_float(
    "DETECT_EDGE_ABS_ROW_FILL", 0.04, 0.01, 0.5
)

# 【最小水印面积占比】取值 0.0 ~ 0.1。
#   检测出的候选区域，面积小于「整图面积 × 该比例」的一律丢弃，
#   用于过滤掉噪点、灰尘、压缩伪影造成的误判。
DETECT_MIN_AREA_RATIO: Final[float] = _env_float("DETECT_MIN_AREA_RATIO", 0.0002, 0.0, 0.1)

# 【最小面积绝对像素锚点】边长（像素）128 ~ 4096（阶段 57 新增，默认 640）。
#   min_area 若纯按「占比 × 当前面积」计算会随图像变小而趋近 0（256² 上半透明通路
#   门限仅 13px、tiled 通路仅 3px），而纹理噪点是绝对像素尺寸（与分辨率无关），
#   于是小图上噪点成群通过门限 → 干净图误报率随尺寸飙升（textured 底图
#   896² 0.39% → 256² 16.97%，见 docs/57-*）。
#   本常量把门限下限锚定为「边长=该值的图像上、同一占比对应的像素数」：
#      · 输入边长 ≥ 该值时不受影响（相对值已 ≥ 下限）；
#      · 输入边长 < 该值时被钳到同一绝对门限（等效于把图像放大到锚点再按比例过滤）。
#   默认 640 与 benchmark/质量门禁口径一致 → 640 基准行为零变化（逐通路等价），
#   仅收紧小图误报。环境变量可下调（更宽松，接近旧行为）或上调（更严格）。
DETECT_MIN_AREA_REF: Final[int] = _env_int("DETECT_MIN_AREA_REF", 640, 128, 4096)

# 【最大水印面积占比】取值 0.05 ~ 0.9。
#   候选区域面积超过「整图面积 × 该比例」的一律丢弃。
#   目的：防止算法把整张图都当成水印，导致画面被彻底重绘毁掉。
DETECT_MAX_AREA_RATIO: Final[float] = _env_float("DETECT_MAX_AREA_RATIO", 0.35, 0.05, 0.9)

# ==============================================================================
# 阶段 49：绝对证据门限（修复「过度检测」缺陷）
# ------------------------------------------------------------------------------
# 背景：修复前半透明 / 平铺 / 网格三条通路都用「分位数」取阈值——
#       thr = percentile(score, 100×(1−0.85×sensitivity))
#   这意味着无论图上有没有水印，都会恒定选中固定比例的像素
#   （sensitivity=0.55 时恒为 46.8%）。检测器因此丧失了「判定无水印」的能力，
#   在本项目的合成诊断中，干净纹理图的误报率高达 93.9%、干净照片图 88.0%。
#
# 修复思路：把「相对分位」换成「绝对证据」+「相对分位」的交集。
#   一个像素要被判为水印，必须同时满足两个绝对条件：
#     (1) 绝对幅度：中值残差 ≥ DETECT_MIN_DELTA（灰阶）——滤掉平滑图上的数值噪声；
#     (2) 相对显著：归一化残差 ≥ DETECT_SIGMA_K × 稳健噪声σ——滤掉纹理丰富区。
#   σ 用 MAD 稳健估计（1.4826×MAD），对水印污染不敏感（中位数可容忍近 50% 污染）。
#
# 实测效果（scripts/detector_diag.py，三种合成底图 × 四类水印）：
#   干净图平均误报率   69.7% → 0.78%
#   带水印图平均召回率 92.8% → 52.4%（注：修复前的 92.8% 是靠「标满整图」换来的假召回，
#                                    同期平均精确率仅 7.9%，无实际意义）
# ==============================================================================

# 【最小可感知灰度差】浮点 0~64（阶段 49 新增，默认 3.0）。
#   中值残差低于该灰阶值的像素直接排除。
#   作用：平滑图（如纯渐变、纯色背景）的残差理论上为 0，但浮点噪声会让
#   「相对显著」判据失效（σ≈0 时任何微小涨落都算多倍 σ），本参数是此时的兜底闸门。
#   调高：更保守，极淡水印可能漏检；调低：更敏感，平滑图上可能出现噪点误报。
DETECT_MIN_DELTA: Final[float] = _env_float("DETECT_MIN_DELTA", 3.0, 0.0, 64.0)

# 【相对显著倍数】浮点 0.5~8.0（阶段 49 新增，默认 2.5）。
#   归一化残差需达到「稳健噪声标准差 × 该倍数」才算证据充分。
#   扫描结果（同一诊断集）：1.5→误报 8.8%/召回 60.5%；2.0→2.9%/56.2%；
#   2.5→0.78%/52.4%；3.0→0.18%/47.1%；4.0→0.0%/33.1%。
#   取 2.5 为默认：误报率已进入 <1% 的安全带，同时保住约一半召回。
#   调高更保守、调低更激进（用户可在界面/环境变量覆盖）。
DETECT_SIGMA_K: Final[float] = _env_float("DETECT_SIGMA_K", 2.5, 0.5, 8.0)

# 【平铺增强的最小相对显著倍数】浮点 0.5~8.0（阶段 49 新增，默认 2.2）。
#   detect_tiled_translucent（平铺增强通路）的统计门限下限——即使预设把敏感度
#   拉得很高（ai_faint_tiled 的 0.72 会算出 k≈1.95），也不允许低于本值。
#
#   为什么要有这个下限（实测数据，scripts/detector_diag.py 同批评估集）：
#       门限下限   干净图误报   极淡平铺召回
#         1.95       3.81%         7.39%
#         2.20       1.52%         3.13%
#         2.50       0.48%         1.15%
#   即：把门限放松到 1.95，要多付 2.3 个百分点的误报，只换来 4.3 个百分点的
#   召回；而该水印的绝对召回在任何配置下都超不过 7.4%——它的扰动只有约 9 个
#   灰阶（α≈0.09），在高纹理背景上本就低于本特征的检测极限。这是信息论限制，
#   不是调参能解决的问题。
#
#   故取 2.2：保留通路在「低纹理区域」仍能起作用的可能性，同时把误报压在 1.5%
#   量级。默认值的取舍原则是「宁可漏检，不可滥杀」——漏掉的水印用户还能手动
#   补掩码，被重绘毁掉的画面无法挽回。
DETECT_TILED_MIN_SIGMA_K: Final[float] = _env_float(
    "DETECT_TILED_MIN_SIGMA_K", 2.2, 0.5, 8.0
)

# 【平铺增强通路的连通域守卫】布尔（阶段 104 新增，默认开）。
#   背景（阶段 104 归因探针，%TEMP%/s104_attrib.py + s104_merge.py + s104_guard_iso.py）：
#   detect_translucent 在阶段 82/84 加了两道**连通域级**守卫（自然光影 / 纹理稠密，
#   见下方 DETECT_TRANSLUCENT_*_GUARD），专门用于在真实照片上剔除「自然明暗过渡」与
#   「处处高频的自然纹理」。但平铺增强通路（detect_tiled_translucent）**重新取了一遍
#   候选**却**没有接这两道守卫**，于是同一批被基础通路已经否掉的像素，从这条通路
#   重新进入并集。
#
#   实测（10 张真实底图，并集口径 = 预设真实启用集合）：
#       · 仅收紧本通路：面积 9.62% → 2.61%（faint_tiled 预设）；
#         10.19% → 3.20%（ai_grid 预设）；干净底图纯误报 3.21% → 0.87%。
#       · 收紧本通路贡献了真实图过检削减的 **65%**（10.19 → 3.54 中的 6.65 个百分点）。
#       · 归因阶段：本通路的**原始候选门限**本身就占真实照片 13.4%~16.5% 图面
#         （_evidence_gate 之后、连通域过滤之前），后续过滤只把它收到 6%~10%。
#
#   本开关只施加**纹理稠密**一道，**不施加自然光影** —— 单守卫隔离实验见 docs/104 §8：
#       · 真实照片削减率：只自然光影 26.0% / 只纹理稠密 **72.9%** / 两道全接 75.4%
#         （ai_grid 预设同序：21.7% / **68.6%** / 72.0%）⇒ 纹理稠密单独拿到 95%+ 的收益。
#       · 合成 6 场景平铺通路 IoU 合计：无守卫 2.479 / 只自然光影 2.214 /
#         **只纹理稠密 2.319** / 两道全接 1.882。
#       · 机制：本通路门限比基础通路更严（k = DETECT_TILED_MIN_SIGMA_K = 2.2），
#         弱而软的**自然光影**梯度本就够不到门限；能穿过严门限的恰恰是**锐利自然纹理**，
#         正是纹理守卫的设计靶子。自然光影守卫的靶子已由始终生效的基础通路接住，
#         在本通路上重复接它只会把代价压在弱信号档上（把「纹理 + 极淡平铺」整类抹零、
#         把「渐变 + 网格」砍掉 28% 召回）。
#
#   低分辨率兜底臂（docs/67）**不受本开关约束**：兜底臂把长边 <640 的输入用 INTER_CUBIC
#   放大到 640 再检测，残差被「重采样平滑 + 有损编码块效应」主导，正好落在纹理守卫的
#   抑制带上，会把整类抹零、令「救回黑屏」的契约失效（实测 256²/webp75/textured/grid）。
#   兜底臂本身是显式 opt-in（DETECT_LOWRES_RESCUE 默认 False，已声明「宁可滥杀也要救回
#   黑屏」），与守卫取舍方向相反，故在臂内豁免（由 detect_watermark_mask 显式传
#   guard=False）。
#
#   为什么**不**同时给网格通路（detect_grid_watermark）也接守卫 —— 反证见 docs/104：
#       · 网格通路的精确率在合成底图上**已经是 1.000**（阶段 53 的
#         DETECT_GRID_EVIDENCE_AND 早已把假响应收干净），再套守卫只剩召回损失
#         （合成 0.99 → 0.68）；真实图合并面积几乎不动（10.19 → 10.15）。
#       · 网格通路在并集里的**边际**过检只有 0.68 个百分点，却要付 0.194 的召回
#         ⇒ 纯亏本买卖。故本开关**只作用于平铺增强通路**。
#
#   关闭可回退到阶段 103 行为（仅供对比排查 / 注入式反例）。
DETECT_TILED_PATH_GUARD: Final[bool] = _env_bool("DETECT_TILED_PATH_GUARD", True)

# 【形状感知掩码】布尔（阶段 49 新增，默认开）。
#   修复前掩码由「连通域包围盒」填充：一个斜向文字笔画的包围盒可能是它自身面积的
#   数十倍，导致候选密度仅 46.8% 时最终覆盖率却达 93.8%（约 2 倍放大）。
#   开启后改为按连通域真实形状填充（再向外膨胀 pad 像素），精确率显著提升。
#   关闭可回退到修复前行为（仅供对比排查）。
DETECT_SHAPE_AWARE_MASK: Final[bool] = _env_bool("DETECT_SHAPE_AWARE_MASK", True)

# 【框内证据二次确认】布尔（阶段 103 新增，默认开）。
#   背景（阶段 103 归因探针，%TEMP%/align_probe.py）：只有「区域框」而没有形状掩码
#   的策略（角落 LOGO、边缘小字）此前退化为**整框填满**，而文字/标识在框内是稀疏的
#   ⇒ 掩码面积可达实体的数倍。实测（10 张真实底图 × 4 类合成水印，逐策略隔离量测）：
#     · edge_text 通路在 4 类上分别涂 10.0 / 6.8 / 9.0 / 7.0% 图面，且 mask% ≈ bbox%
#       （证明几乎全是矩形填充），精确率只有 0.009~0.166。
#   开启后：这些策略改为在框内只保留「有证据」的像素（复用半透明通路的
#   _residual_fields + _evidence_gate 证据场）。框内完全无证据 ⇒ 整框丢弃。
#   守卫：若整幅图都取不到证据（证据场退化，例如水印比 DETECT_MIN_DELTA 还淡），
#   则**回退整框填充**——避免把「证据判据看不见」误读成「没有水印」而漏检。
#   关闭可回退到修复前行为（仅供对比排查）。
DETECT_BOX_EVIDENCE_REFINE: Final[bool] = _env_bool(
    "DETECT_BOX_EVIDENCE_REFINE", True
)

# 【框内证据二次确认：外形膨胀】整数像素（阶段 103 新增，阶段 106 由 2 收敛为 1）。
#   证据场取到的是水印「核心」像素；抗锯齿与外溢边缘通常再外扩 1~2px。
#   pad 太小会让修复漏掉羽化边缘（残留），太大会回到整框填充的老问题。
#
#   阶段 106 归因（%TEMP%/s106_padF.py，10 张真实底图 × 4 类，含 inpaint 的端到端指标）：
#   `_refine_regions_to_mask` 用 ellipse(2*pad+1) 膨胀证据 ⇒ **pad=2 时每个证据像素
#   变成 5×5 块**，对 1~2px 笔画的文字/网格是约 5× 的面积放大（与阶段 105 的
#   「dilate = 几何放大器」同族）。四种 pad 下的四类端到端均值（pred% / overdraw / removal）：
#     edge_text   pad0 3.03/3.64/0.2382   pad1 3.84/4.61/0.2417   pad2 **4.16/5.00/0.2407**
#     grid        pad0 6.28/5.18/0.5073   pad1 7.20/5.94/0.5053   pad2 **7.53/6.21/0.5026**
#     faint_tiled pad0 5.91/1.08/0.0048   pad1 6.79/1.25/0.0015   pad2 **7.88/1.45/-0.0174**
#     corner_logo 三档**逐位相同**（0.886/0.3555）—— 角落框的证据块够大，1~2px 无关紧要
#   ⇒ 收敛到 1px：四类**每一项都不退化**（overdraw −24%/−14%/−18%/0，removal 持平或转正），
#   且保留 1px 余量以应对真实水印的抗锯齿边缘（pad=0 虽 overdraw 再低 5~12%，但失去该余量）。
DETECT_BOX_REFINE_PAD: Final[int] = _env_int("DETECT_BOX_REFINE_PAD", 1, 0, 8)

# 【框内证据二次确认：相对门限缩放】浮点（阶段 103 新增 0.5，阶段 106 收敛为 0.65）。
#   为什么框内要单独一套门限：**先验不同**。框外任何像素都可能只是噪声，需要高置信；
#   而框内「这里存在水印」这个先验已经由策略建立，判据只需回答「这个像素是否属于该实体」，
#   用全域那套高门限会把**极淡水印的子集**当成非实体剔掉。
#
#   阶段 103 标定（`%TEMP%/refine_sweep.py`，当时 pad 固定 2）：
#     · 1.00：edge_text 面积 −52.7%（最优），但 faint_tiled 只保留 75.1% ⇒ removal 越过红线；
#     · 0.50：edge_text 保留 100%、面积 −32%；faint_tiled 保留 92.1%  ⇒ 取 0.50。
#   阶段 106 在 **pad 收敛为 1** 的新几何下重扫（`%TEMP%/s106_padF.py`）：
#     · 0.50：edge_text ov 4.61 / grid 5.94 / faint 1.25
#     · 0.65：edge_text ov **3.80** / grid **5.36** / faint **1.19**，而 removal **全部持平或更好**
#             （edge 0.2417/0.2415、grid 0.5053/0.5068、faint 0.0015/0.0041）⇒ 取 0.65。
#   注：阶段 103 里「1.0 会伤 faint_tiled」的结论是**在 pad=2 下**测得的（几何放大掩盖了
#   门限效应）；pad 收敛后该耦合消失，故 0.65 不再是「两端取平衡」而是**单侧占优**。
DETECT_BOX_REFINE_K_SCALE: Final[float] = _env_float(
    "DETECT_BOX_REFINE_K_SCALE", 0.65, 0.05, 1.0
)

# 【自然光影守卫】布尔（阶段 82 新增，默认开）。
#   背景（docs/A2 真实图门禁 F1）：translucent 通路在真实照片上把自然光影
#   （水面反光、云层、树叶）判成半透明水印，占真实图误报的 92.5%——
#   「局部对比度残差」判据无法区分「人工半透明叠加」与「自然明暗过渡」。
#   区分特征（30 张真实图 + 合成门禁用例双口径标定，见 docs/82）：
#   **人工叠加的边缘锐利，自然光影过渡平滑**。
#     · edge_sharp：区域 bbox 内残差场 Sobel 梯度 P90（绝对锐度，随振幅走）；
#     · ratio    ：edge_sharp / mean|residual|（相对锐度，与振幅解耦，
#                  专为保护低振幅合成水印——极淡平铺 edge 31~43 但 ratio 9.4~12.8）。
#   保留条件 = edge_sharp ≥ EDGE×scale OR ratio ≥ RATIO。实测（1024 真实图）：
#   真实 TP 保留 100%、FP 砍 78.2%；合成四用例 TP 全保留。未通过者整块抑制。
DETECT_TRANSLUCENT_NATURAL_GUARD: Final[bool] = _env_bool(
    "DETECT_TRANSLUCENT_NATURAL_GUARD", True
)

# 【自然光影守卫：绝对锐度阈值】640 等效尺度（随 max(h,w)/640 缩放），0~4096。
#   标定（v2 统计量：候选像素∪2px 邻域上的梯度 P90，docs/82）：真实 TP
#   （角标碎片）edge P10=287@1024；阈值 150@1024（=94@640）处 TP 保留 100%、
#   FP 保留 61.6%——取 94 留余量（真实 TP P10 折算 179@640，余量近一倍）。
DETECT_NATURAL_GUARD_EDGE: Final[int] = _env_int(
    "DETECT_NATURAL_GUARD_EDGE", 94, 0, 4096
)

# 【自然光影守卫：相对锐度阈值】edge_sharp / bbox 均值|residual|，1.0~64.0。
#   与振幅解耦的第二通路（并集恒优于替换）：真实 FP ratio P90=8.3、
#   合成极淡平铺 ratio P10=9.4——8.0 恰在两者的间隔内（经验 10：阈值放
#   无害/危险区之间，且无害端留余量）。
#   注意分母是 **bbox 全域均值** 而非候选像素均值：bbox 的空隙稀释把
#   稀疏锐利水印（极淡平铺 ratio≈10.8）与稠密光影块区分开；用候选均值
#   会让极淡平铺 ratio 掉到 4.5 被误杀（阶段 82 标定中实测过该反转）。
DETECT_NATURAL_GUARD_RATIO: Final[float] = _env_float(
    "DETECT_NATURAL_GUARD_RATIO", 8.0, 1.0, 64.0
)

# 【纹理稠密守卫】布尔（阶段 84 新增，默认开）。
#   背景（docs/82 剩余缺口 F1 残余）：自然光影守卫按「边缘是否锐利」放行，
#   而真实照片上的**锐利自然纹理**（树叶、枝干、草地、毛衣纹理、砖墙）本身
#   边缘就很锐利 → 整片纹理区照样通过 → 残余误报约占真实 FP 的 40%
#   （每图约 60 个区域，多为「实心大块」）。
#   区分特征（30 张真实图 + 合成双口径标定，docs/84）：**水印是「结构化图形」，
#   纹理是「处处高频」**。对每个候选连通域测残差场梯度分布的**稠密度**：
#     · density = #{像素 : gmag > 0.5 × mean(gmag)} / #像素（域 = 候选 ∪ 2px 邻域）
#   水印笔画内部平坦、只在轮廓处陡变 → 梯度集中在少数像素 → density 低；
#   自然纹理处处都是梯度 → density 高。density 是**比值**，与振幅/尺度/图像尺寸
#   全部解耦（不使用任何绝对量），这是它优于「梯度均值」类判据的关键。
#   实测（30 张真实图 general，**连通域级**——守卫实际作用的层级）：
#   与真值相交的组件 density p75=0.600 / p90=0.705；不相交的组件 p50=0.696。
#   未通过者整块抑制。与自然光影守卫正交（一个管「平不平滑」，一个管「稠不稠密」），
#   两者为**串联**关系（都必须通过）。
DETECT_TRANSLUCENT_TEXTURE_GUARD: Final[bool] = _env_bool(
    "DETECT_TRANSLUCENT_TEXTURE_GUARD", True
)

# 【纹理稠密守卫：梯度稠密度阈值】0.0~1.0，默认 0.65。
#   标定（docs/84，双口径扫描 0.45~0.75 共 15 档）：**安全窗为 (0.62, 0.69)**——
#     · ≤0.62：第 02 图召回被打断（TP 1.00→0.50），出现逐图退化；
#     · ≥0.70：召回回落（0.95→0.9333），且 mask_iou 持续下滑。
#   0.65 取安全窗**中心**（两侧各留约 0.03 余量），而非精度 argmax（0.63）——
#   经验 10：阈值放无害/危险区之间、无害端留余量。实测 0.65：
#   真实图 precision 0.0308→0.0388（+26%）、recall 0.9333→0.9500（+1.8pp）、
#   IoU 0.6523→0.6571、mask_iou 0.0577→0.0617——**四项同时改善、逐图零退化**
#   （29/30 图 precision 提升、0 图下降）；合成四用例逐位零回归。
DETECT_TEXTURE_GUARD_DENSITY: Final[float] = _env_float(
    "DETECT_TEXTURE_GUARD_DENSITY", 0.65, 0.0, 1.0
)

# 【纹理稠密守卫：`过均值`比例常数】固定 0.5（不对用户暴露，仅内部常量）。
#   密度定义为「gmag 超过 mean(gmag) 的 t_share 倍的像素占比」。取 0.5 而非
#   1.0：1.0 在高斯型分布下恒约 0.5（无区分度）；0.5 把「平坦+陡边」与
#   「处处高频」拉开到 0.38 vs 0.65（真实图实测，docs/84）。
DETECT_TEXTURE_GUARD_SHARE: Final[float] = 0.5

# 【全局覆盖率上限】浮点 0.05~1.0（阶段 49 新增；**阶段 99 由 0.30 收敛为 0.15**）。
#   最后一道安全闸：若合并后的掩码覆盖整图超过该比例，说明检测器已失控，
#   按区域面积从大到小丢弃，直到覆盖率回落到上限内。
#   设计依据（产品层面的非对称风险）：把 90% 的画面交给修复算法重绘，
#   结果是照片被彻底毁掉；而漏掉一小块水印，只是残留瑕疵，用户还能手动补。
#   宁可漏检，不可滥杀。
#
#   ---- 阶段 99：0.30 → 0.15 的标定（docs/99 探针 B）----
#   为什么旧的 0.30 形同虚设：30 张真实图实测 `mask_coverage` 均值 0.1618，
#   而阈值 0.30 ⇒ **只有 2/30 触发**（两例裁剪前 30.9% / 33.4%）。
#   把闸整个关掉（=1.00）覆盖率也只从 0.1618 升到 0.1648（+1.9%）——
#   它守的是「单图离群」，不是「系统性过检」。
#   逐档扫描（30 张真实图 general，逐档实测）：
#       cap=1.00 → coverage 0.1648 / ratio 16.92 / mask_iou 0.0634 / recall 1.0000
#       cap=0.30 → coverage 0.1618 / ratio 16.62 / mask_iou 0.0635 / recall 1.0000（旧默认）
#       cap=0.20 → coverage 0.1460 / ratio 14.99 / mask_iou 0.0659 / recall 1.0000
#       cap=0.15 → coverage 0.1266 / ratio 13.00 / mask_iou 0.0699 / recall 1.0000 ← 取此
#       cap=0.10 → coverage 0.0855 / ratio  8.78 / mask_iou 0.0859 / recall 0.9833（开始丢真值）
#       cap=0.08 → coverage 0.0705 / ratio  7.24 / mask_iou 0.0973 / recall 0.9833
#   取 0.15 的三条理由（缺一不可）：
#     ① **完全免费**：recall 1.0000、mean_iou 0.7573 与 0.30 逐位一致，
#        而 coverage −21.8%、mask_iou +10.1%、precision +1.8%；
#     ② **高于全部已登记合成用例**：合成门禁 12 用例在 0.15 下**逐项一字不变**，
#        说明它不会切断任何合法用例（含 ai_faint_tiled / ai_grid / ai_logo）——
#        这是「不误杀」的直接证据，而不是推断；
#     ③ **不顶在断点边缘**：0.10 是召回断点（60 个真值丢 1 个，recall 0.9833），
#        0.15 在其上留 50% 余量（经验 10：阈值放无害/危险区之间、无害端留余量）。
#   ⚠ 仍未达阶段 93 登记的目标（coverage ≤ 0.05 / ratio ≤ 5，见 real_gate 契约表）：
#     剩余过检面积按 kind 归因为 corner_logo **9.2%** + translucent 2.4%
#     （docs/99 探针 A），属 `core/detector` 角标通路自身的判据问题，
#     需独立审计处理，不是本闸能解决的——**本闸只负责兜住失控，不负责提升精度**。
DETECT_MAX_COVERAGE_RATIO: Final[float] = _env_float(
    "DETECT_MAX_COVERAGE_RATIO", 0.15, 0.05, 1.0
)

# 【同 kind 相邻碎片聚合】布尔（阶段 82 新增，默认开）。
#   背景（docs/A2 真实图门禁 F2）：真实 AI 平台图上的「AI生成」深色角标会被
#   translucent 通路打成多个 29~43px 的小碎片，每个碎片与真值框 IoU≈0.10，
#   全部低于 0.3 匹配阈值 → 整个水印计 FN，17/30 图召回被压在 0.5。
#   本开关在「各策略产出区域之后、合并过滤之前」把**同 kind、包围盒间隙小、
#   合并后不超尺寸上限**的碎片做传递性合并（贪心按间隙升序）。
#   实测（30 张真实图，锚定 640 缩放到 1024：gap=24 / cap=160 / fill=2.2）：
#   召回 71.7% → 93.3%、mean IoU 0.596 → 0.648、FP/图 156 → ~112、
#   改写面积代理持平（-1.9%）；合成门禁逐位零回归。
#   **必须有双约束**：实测无约束的间隙合并会发生「链式拖拽」——真值区域通过与
#   它相触的误报区传递性合并，包围盒被拖大后 IoU 掉破匹配阈值（反而丢 TP）；
#   `MAX_SIDE` 挡住拖拽、`FILL` 挡住跨空合并，两者缺一不可（docs/82 有反例）。
DETECT_FRAGMENT_MERGE: Final[bool] = _env_bool("DETECT_FRAGMENT_MERGE", True)

# 【碎片聚合：最大间隙】640 等效像素（随 max(h,w)/640 缩放），0~200。
#   标定：真实图上 8/16/24/32 在有 MAX_SIDE 约束时召回相同（93.3%），
#   取中间偏小值 15（1024 图上等效 24px）：更小的 G 对拖拽更不敏感。
DETECT_FRAGMENT_MERGE_GAP: Final[int] = _env_int("DETECT_FRAGMENT_MERGE_GAP", 15, 0, 200)

# 【碎片聚合：合并后包围盒最长边上限】640 等效像素，16~1024。
#   挡「链式拖拽」的关键约束：真实角标「AI生成」框 140×60（1024 尺度），
#   640 等效 ≈ 88，上限 100（1024 等效 160）既容得下完整角标、又挡住
#   被误报区拖出的大包围盒。标定见 docs/82。
DETECT_FRAGMENT_MERGE_MAX_SIDE: Final[int] = _env_int(
    "DETECT_FRAGMENT_MERGE_MAX_SIDE", 100, 16, 1024
)

# 【碎片聚合：合并后包围盒填充率下限】合并 bbox 面积 / 成员面积之和，1.0~8.0。
#   挡「跨空合并」：两个各自紧凑的碎片群隔着大片空白被拖到一起时，
#   合并 bbox 会包含大量空像素。设 0 或 1.0 关闭该守卫（不推荐）。
DETECT_FRAGMENT_MERGE_FILL: Final[float] = _env_float(
    "DETECT_FRAGMENT_MERGE_FILL", 2.2, 1.0, 8.0
)

# 【是否启用 EasyOCR 增强检测】
#   EasyOCR 是可选依赖。装了它，文字类水印的定位精度会明显提升。
#   若未安装，程序自动降级为纯 OpenCV 的 MSER 文字区域检测，不会报错。
DETECT_USE_OCR: Final[bool] = _env_bool("DETECT_USE_OCR", True)

# 【检测用最大边长】取值 256 ~ 4096。
#   超过该边长的图片，会自动降采样到该尺度后再做检测（水印是低频结构，
#   降采样不影响定位，却能显著提速、降低内存占用），掩码再放大回原图。
#   该参数独立于推理阶段的 INFER_MAX_SIDE（那是修复时的分块阈值）。
DETECT_MAX_SIDE: Final[int] = _env_int("DETECT_MAX_SIDE", 1280, 256, 4096)

# 【低分辨率兜底重检开关】布尔（阶段 67 新增，**默认 False = 部署级放行闸**）。
#   背景：输入被平台缩小（缩略图 / 社交平台再压缩）后，水印的**绝对像素尺寸**随之缩水，
#   而各策略内部的判据（残差绝对幅度、面积门限、周期判据等）都按标定尺度（640）设计
#   ——实测小图上会出现「完全空掩码」：320² JPEG 的底部小字 / 角标、256² 网格召回直接
#   为 0（逐策略归因确认是策略内部判据失败，而非共享面积门限），界面只会显示
#   「未检测到水印」，属**静默漏检**。开启后，长边小于 DETECT_LOWRES_RESCUE_SIDE 的
#   输入会被提升到该尺度再检测一次并与主结果取并集（实测平均召回 73.8% → 91.5%，
#   4 例黑屏全部消除）。
#   **为什么默认关闭**：兜底会把原生小图的纹理噪点一并放大，实测干净图四预设平均误报
#   256² textured 0.80% → 7.98%（其中默认预设 general 4.66%）、320² 1.96% → 5.29%，
#   破坏阶段 57 锁定的「小图误报 ≤ 2%」保证。按项目铁律「宁可漏检，不可滥杀」
#   （阶段 49：误解修复不可逆，漏检只是可见瑕疵），故作为**显式 opt-in** 放行：
#   只有当你确认手上是被平台缩小/压缩过的副本、且"擦不干净"比"多擦一点"更不能接受时，
#   再打开它。两种口径的量化数据见 docs/67-*。
DETECT_LOWRES_RESCUE: Final[bool] = _env_bool("DETECT_LOWRES_RESCUE", False)

# 【低分辨率兜底重检的目标尺度】取值 0 ~ 8192，0 = 关闭（阶段 67 新增，默认 640）。
#   与 DETECT_MIN_AREA_REF 的锚点一致：整套门限都是按「640 等效尺度」标定的，
#   把过小的输入提升回该尺度，即可让同一套判据重新成立。代价是小图多一次检测。
DETECT_LOWRES_RESCUE_SIDE: Final[int] = _env_int(
    "DETECT_LOWRES_RESCUE_SIDE", 640, 0, 8192
)

# 【底部居中搜索区】布尔（阶段 20 新增）。
#   AI 生成图的水印/署名常位于「底部居中条带」（如 AI 平台的署名栏、生成参数条），
#   而经典角落四角+中心搜索区容易漏掉它。开启后，角落 LOGO 检测会额外覆盖底部中央区域。
DETECT_SEARCH_BOTTOM: Final[bool] = _env_bool("DETECT_SEARCH_BOTTOM", True)

# 【平铺半透明增强】布尔（阶段 20 新增，默认关）。
#   针对 AI 生成图常见的「半透明平铺 logo」：单个 tile 单元面积很小、透明度很低，
#   常规阈值会把单个小 tile 滤掉。开启后用更激进阈值 + 更低的 min_area 保留小 tile，
#   再用形态学闭运算把相邻 tile 连成片，整体召回更高（但误报也会上升）。
#   默认关闭，仅在「AI 平铺水印」等检测预设中由程序主动开启，避免普通场景误报增加。
DETECT_ENHANCE_TILED: Final[bool] = _env_bool("DETECT_ENHANCE_TILED", False)

# 【网格平铺水印检测】布尔（阶段 22 新增，默认关）。
#   针对 AI 生成图常见的「网格状平铺水印」（规则排布的小标识，如点阵 / 网格 logo）：
#   开启后用「残差二值图 + 行列投影自相关找周期」标记每一个网格单元。
#   默认关闭，仅在「AI 网格水印」检测预设中由程序主动开启，避免普通场景误报上升。
DETECT_GRID_ENABLED: Final[bool] = _env_bool("DETECT_GRID_ENABLED", False)

# 【全局渐变水印检测】布尔（阶段 22 新增，默认关）。
#   针对 AI 生成图叠加的「全局平滑渐变」（如整图轻微变亮 / 变暗的渐变署名层）：
#   开启后用「粗采样平面拟合 + 方差解释率」判断是否存在全局梯度并标记整图。
#   默认关闭，仅在「AI 渐变水印」检测预设中由程序主动开启；该检测会整图标记，需谨慎。
DETECT_GRADIENT_ENABLED: Final[bool] = _env_bool("DETECT_GRADIENT_ENABLED", False)

# 【Logo 轮廓水印检测】布尔（阶段 23 新增，默认关）。
#   针对 AI 生成图常见的「实体 LOGO 标识」（如角落的品牌图标、实心字母组合）：
#   用 Canny 边缘 + 轮廓实心率（solidity = 轮廓面积 / 凸包面积）判定是否为「实心填充形状」。
#   实心填充的 LOGO 实心率接近 1，而文字/线条/纹理的实心率明显偏低，借此与文字类水印区分。
#   默认关闭，仅由「AI LOGO 水印」检测预设主动开启；该检测对「实心大块物体」也可能误报，需配合面积与宽高比过滤。
DETECT_LOGO_ENABLED: Final[bool] = _env_bool("DETECT_LOGO_ENABLED", False)

# 【Logo 检测最小实心率】浮点 0~1（阶段 23 新增，默认 0.85）。
#   轮廓实心率低于该值的区域一律丢弃（过滤线条、文字、镂空图案等「非实心」结构）。
#   调低：能抓住更多形状（但也更容易把文字/图案当 LOGO）；调高：只认最实心的纯色块。
DETECT_LOGO_SOLIDITY: Final[float] = _env_float("DETECT_LOGO_SOLIDITY", 0.85, 0.0, 1.0)

# 【Logo 最小面积占比】浮点 0~0.1（阶段 23 新增，默认 0.0003）。
#   LOGO 通常比网格单元还小，故默认比通用 DETECT_MIN_AREA_RATIO 更低，保留小 LOGO。
DETECT_LOGO_MIN_AREA_RATIO: Final[float] = _env_float("DETECT_LOGO_MIN_AREA_RATIO", 0.0003, 0.0, 0.1)

# 【Logo 最大面积占比】浮点 0.05~0.9（阶段 23 新增，默认 0.15）。
#   超过整图 15% 的实心块很可能是图片主体（而非水印 LOGO），直接丢弃，降低误报。
DETECT_LOGO_MAX_AREA_RATIO: Final[float] = _env_float("DETECT_LOGO_MAX_AREA_RATIO", 0.15, 0.05, 0.9)

# 【Logo 最大宽高比】浮点（阶段 23 新增，默认 8.0）。
#   包围盒宽高比超过该值的「细长」实心块（如一根栏杆）判为非 LOGO，丢弃。
DETECT_LOGO_MAX_ASPECT: Final[float] = _env_float("DETECT_LOGO_MAX_ASPECT", 8.0, 1.0, 50.0)

# 【角标通路：Canny 双阈值】整数（阶段 69 抽取为常量，默认 50 / 150）。
#   这是角落/实体 LOGO 通路的主通路阈值对，保持阶段 23 以来的标定值不变。
DETECT_LOGO_CANNY_LO: Final[int] = _env_int("DETECT_LOGO_CANNY_LO", 50, 1, 254)
DETECT_LOGO_CANNY_HI: Final[int] = _env_int("DETECT_LOGO_CANNY_HI", 150, 2, 255)

# 【角标通路：自适应阈值补充通路】布尔（阶段 69 新增，默认 True）。
#   动机：Canny 双阈值比较的是**梯度幅度绝对值**，而 WebP 等有损编码与重采样会平滑
#   图像、把梯度整体压低——实测 480² WebP 的角标用例梯度中位数从 18.4 降到 14.0，
#   固定阈值下轮廓碎成 2 块（0 块通过形状过滤）→ 召回 0；而同图 PNG/JPEG 均为 100%。
#   处置：**额外**用一组「噪声锚定的自适应阈值」再取一次轮廓，与主通路**取并集**。
#   为什么是并集而不是替换：实测替换会在别的组合上制造新崩塌（640² textured/WebP
#   由 100% → 0%，阈值降到 32/96 反而让真轮廓不再通过形状过滤）——并集对原本通过的
#   用例零影响（严格 ⊇ 主通路），这正是阶段 67 的教训「并集恒优于替换」。
#   自适应规则（只降不升，上限即主通路值）：
#       lo = clip(round(med + K·1.4826 近似 MAD), DETECT_LOGO_ADAPT_LO_MIN, CANNY_LO)
#       hi = 3 × lo（因此恒 ≤ CANNY_HI）
#   高梯度图像（med + K·MAD ≥ 50）算出的 lo 被上限钳到 50 → **与主通路完全相同，
#   跳过第二次取轮廓，既零开销也零行为变更**。
DETECT_LOGO_CANNY_ADAPT: Final[bool] = _env_bool("DETECT_LOGO_CANNY_ADAPT", True)

# 【角标自适应通路的稳健倍数】浮点 0~10（阶段 69 新增，默认 2.0）。
#   阈值取「梯度中位数 + K × 中位绝对偏差」：噪声越强阈值越高，越不容易把噪声当轮廓。
DETECT_LOGO_ADAPT_SIGMA_K: Final[float] = _env_float(
    "DETECT_LOGO_ADAPT_SIGMA_K", 2.0, 0.0, 10.0
)

# 【角标自适应通路的阈值下限】整数（阶段 69 新增，默认 30）。
#   自适应只允许把阈值降到该值为止：再低会让相邻结构连成一片、轮廓形状失真而
#   不再通过实心率/长宽比过滤（实测 20/60 与 12/36 都让角标召回掉到 0），属**能力边界**。
DETECT_LOGO_ADAPT_LO_MIN: Final[int] = _env_int("DETECT_LOGO_ADAPT_LO_MIN", 30, 1, 254)

# 【网格检测最小自相关强度】浮点 0~1（阶段 23 新增，默认 0.35）。
#   网格判据之一：行/列投影自相关峰值强度需高于该值，才算「规则周期」。
#   调高：只在周期非常明显时判为网格，抑制「偶发条纹/噪声」误报；调低：更敏感。
#   注（阶段 49）：该判据区分度已被实测证伪（详见 DETECT_GRID_MIN_ACF 说明），
#   现仅作辅助参考保留；真正的周期性判定以 DETECT_GRID_MIN_ACF 为准。
DETECT_GRID_MIN_STRENGTH: Final[float] = _env_float("DETECT_GRID_MIN_STRENGTH", 0.35, 0.0, 1.0)

# 【网格检测最小残差自相关峰值】浮点 0~1（阶段 49 新增，默认 0.09）。
#   判定「图上确实存在规则平铺结构」的主判据，基于连续残差场的二维 FFT 自相关。
#
#   为什么需要它：修复前用「阈值化二值图 → 行/列投影 → 一维自相关」找周期。
#   但二值投影对随机散点同样会给出很高的相关值——实测干净纹理图的行/列投影
#   自相关达 0.55~0.60（阈值仅 0.35），于是干净图被判为「有网格」，
#   在纹理/照片底图上造成 13~14% 的误报率。
#
#   改用连续残差场的二维自相关后，同一批评估数据上：
#       干净图（渐变 / 纹理 / 照片）峰值   0.211 / 0.052 / 0.043
#       真实网格水印（渐变 / 纹理 / 照片） 0.833 / 0.119 / 0.188
#   纹理与照片这两档（真实场景的代表）分离度约 2.3~4.4 倍，判据这才真正有效。
#
#   默认值 0.09 取在「干净图上限 0.052」与「网格下限 0.119」之间。
#   调高：只在周期性极强时判为网格（更保守）；调低：更敏感，误报回升。
DETECT_GRID_MIN_ACF: Final[float] = _env_float("DETECT_GRID_MIN_ACF", 0.09, 0.0, 1.0)

# 【网格检测最小覆盖占比】浮点 0~0.6（阶段 23 新增，默认 0.003）。
#   网格标记像素占整图比例需高于该值；太稀疏的「疑似网格」更可能是噪点，丢弃。
DETECT_GRID_MIN_COVERAGE: Final[float] = _env_float("DETECT_GRID_MIN_COVERAGE", 0.003, 0.0, 0.6)

# 【网格检测最大覆盖占比】浮点 0~0.9（阶段 23 新增，默认 0.6）。
#   网格标记占比超过该值（几乎整图都是标记）更可能是全局纹理而非规则平铺水印，丢弃。
DETECT_GRID_MAX_COVERAGE: Final[float] = _env_float("DETECT_GRID_MAX_COVERAGE", 0.6, 0.0, 0.9)

# 【网格掩码与单图证据门限相交】布尔（阶段 53 新增，默认开）。
#   阶段 53 诊断发现：折叠模板在纹理/照片底图上会把「纹理平均残留的假响应」沿
#   周期铺满全图，导致网格掩码精确率暴跌——textured 上仅 11.6%、photo 上 30%、
#   gradient 上 31%（阶段 52 后的数字）。
#
#   根因：折叠模板只用「模板自身的稳健 σ」做门限，纹理噪声经 N 个 tile 平均后
#   残留的伪结构在模板里仍显著，被门限判为网格节点、平铺后铺满全图；而真实网格
#   点在「单图证据门限」(|残差| ≥ 绝对幅度 ∧ |归一化残差| ≥ k·稳健σ) 上一定显著。
#
#   修复：把平铺后的模板掩码与「单图证据门限掩码」取交集（bitwise_and）。
#   纹理假响应在单图证据门限上不显著（纹理本身的高频在单图上是分散的、相对不突出的），
#   于是被滤掉；真实网格点两面都显著，被保留。
#
#   实测（scripts/probe_grid.py + detector_diag.py，阶段 53）：
#       底图      交叉前精确率   交叉后精确率   交叉后召回
#       gradient     21.8%         100.0%        100.0%
#       textured     37.9%          95.5%          87.1%
#       photo        48.7%          96.4%          99.3%
#   精确率从 ≤11.6%(经 _components_mask 膨胀后更低) 拉到 95%+，召回仅 textured 略降
#   到 87%（纹理吞没的个别网格点属信息论极限，阶段 49 已说明），IoU 同步大幅改善。
#
#   设 False 可回退到阶段 52 行为（仅折叠模板门限，不相交），用于回归对照。
DETECT_GRID_EVIDENCE_AND: Final[bool] = _env_bool("DETECT_GRID_EVIDENCE_AND", True)

# 【网格折叠模板门限倍数】浮点 1.0~3.0（阶段 106 新增，默认 1.3）。
#   控制 detect_grid_watermark 里折叠模板的二值门限：`|template| ≥ k × mult × σ_template`。
#
#   为什么需要（阶段 106 归因，10 张真实底图 × 合成网格水印，%TEMP%/s106_gridD.py，
#   复刻件与真实函数**逐字节自校验通过**）：
#     网格掩码 = 折叠模板 ∩ 单图证据门限（阶段 53 的 DETECT_GRID_EVIDENCE_AND），
#     而模板门限此前只乘 `k`、**从不乘任何余量**。实测模板上仍残留「纹理经 N 个 tile
#     平均后未被压干净」的伪结构：这些位置勉强越过 `k·σ_template`，平铺后铺满全图，
#     在真实照片上贡献了网格通路 **82% 的假阳性面积**（FP 0.76% vs 真值 0.63%）。
#     抬高模板门限即可把它们滤掉 —— 因为**真网格节点在折叠后幅度远高于门限**：
#       变体          pred%   tp%    fp%    overdraw  面积召回
#       1.0（原状）   1.39   0.63   0.76   1.15     0.521
#       1.3（本值）   0.81   0.63   0.18   0.67     0.515   ← FP −76%，TP 逐位不变
#       1.6           0.66   0.56   0.10   0.54     0.459   ← 开始掉 TP
#       2.0           0.42   0.42   0.01   0.35     0.345   ← TP −33%
#     ⇒ 取 1.3：在所测区间内**唯一既大幅削 FP 又零 TP 损失**的点（1.6 起 TP 下滑）。
#     同族对照（同一探针）：证据门限放松（×0.7/×0.5）反而更差（overdraw 1.46/1.94），
#     「证据在 tile 域的一致性」硬约束过于激进（TP 0.63→0.42）⇒ 均不采用。
#
#   这不是「拍脑袋抬阈值」：阶段 99/100 否决的抬阈值针对的是**判据与目标语义重叠、
#   不可分**的通路（MSER 找稳定极值区 vs 自然照片处处是稳定极值区）。此处判据
#   （折叠增幅）与目标（周期性重复且幅度显著）同构，且实测显示可分（TP 平坦区）。
#   设 1.0 可回退到阶段 53 行为，用于回归对照。
DETECT_GRID_TMPL_K_MULT: Final[float] = _env_float(
    "DETECT_GRID_TMPL_K_MULT", 1.3, 1.0, 3.0
)

# 【合并阶段收口膨胀半径】整数 0~2（阶段 106 新增，默认 1 = 阶段 49 以来说明的行为）。
#   各策略掩码取并集后，会做一次形态学收口：`dilate(ellipse(2k+1))` + `_fill_holes`。
#   原意是「把水印羽化边缘也包进来，避免修复后残留」，但它是**作用于并集**的几何放大器：
#   当并集由上百个小单元组成时（网格/平铺水印），每块各自 +k 像素，相对涨幅 ≈ 2k/特征宽度。
#   阶段 106 实测（%TEMP%/s106_mergeG.py，10 图 × 4 类，含 inpaint）：
#     · 关掉它确实能再降 overdraw（edge 3.80→2.77、grid 5.02→3.74、corner 0.886→0.669），
#       但**同时吃掉 removal**：corner_logo（唯一承诺类型）0.3555 → **0.2264（−36%）**，
#       越过 e2e 红线 0.25；edge_text 0.2415→0.2178、grid 0.5063→0.4675、faint −0.0041→−0.0061。
#   ⇒ **默认 1 是承重值，不可关闭**：这 1px 覆盖的是水印的抗锯齿/羽化边缘，
#     对「实心填充型」角标正是修复质量的关键；纯几何上看它「多余」，实际不是。
#   保留该配置项仅为标定与回归对照（**不得以「降低 overdraw」为由设为 0**）。
DETECT_MERGE_DILATE: Final[int] = _env_int("DETECT_MERGE_DILATE", 1, 0, 2)

# 【网格掩码膨胀半径】整数 0~3（阶段 53 新增，默认 0）。
#   控制 _components_mask 对网格掩码的膨胀像素数。网格水印是「周期性点/线阵」，
#   节点位置经证据交集后已精确落在格点上，膨胀只会把节点周围的非水印像素
#   裹进来变成假阳性——实测 dilate=1 把隔离精确率从 ~95% 拉低到 ~65%。
#   故默认 0：点阵本身精确，无需膨胀包裹（与半透明/边缘文字需要 dilate=1 不同）。
#   调大：节点边缘更宽松（可能略升召回但精确率下降）；调小：更克制。
DETECT_GRID_MASK_DILATE: Final[int] = _env_int("DETECT_GRID_MASK_DILATE", 0, 0, 3)

# 【平铺半透明掩码膨胀半径】整数 0~3（阶段 54 新增，默认 0）。
#   控制 detect_tiled_translucent 形状感知掩码的膨胀像素数。该策略服务「全图平铺半透明
#   水印」（如极淡平铺文字），靠闭运算（5×5）把稀疏候选连成片即足够；但在「周期性点阵」
#   （网格水印）上 dilate=1 会把网格点的平滑过渡区几何扩张，cov 从 1.2% 放大到 2.5%，
#   精确率仅 48~65%，合并（OR）后把 grid 策略的 100% 精确稀释到约 30%。
#   阶段 54 诊断：dilate=0 可把 grid 上 tiled 精确率拉回 ~90%（几何扩张是主因），
#   合并口径 grid 精确率 textured 23.7%→33.3%、photo 30.0%→42.7%、gradient 30.9%→38.6%；
#   对极淡平铺（已知检测极限，召回本就个位数）与 edge_text/corner_logo 影响在误差内。
#   故默认 0：点阵与半透明通路都不需要额外的膨胀包裹（与阶段 53 网格一致）。
#   若实际遇到「强平铺（非点阵）水印修复后出现零散残留点」，可上调到 1 让相邻单元更宽松地连片。
DETECT_TILED_MASK_DILATE: Final[int] = _env_int("DETECT_TILED_MASK_DILATE", 0, 0, 3)

# 【半透明掩码膨胀半径】整数 0~8（阶段 66 新增，默认 0）。
#   控制 detect_translucent 形状感知掩码的膨胀像素数。这是三条检测通路里**最后一个**
#   未经真值标定的膨胀量：网格（阶段 53）与平铺（阶段 54）都已收敛为 0，唯独此处
#   长期硬编码为 4（`_components_mask(..., dilate=4)`），使 general 预设的掩码比
#   兄弟通路宽 4px。叠加管线 `MASK_DILATE_DEFAULT` 后，general 路径的有效膨胀曾达
#   4+2=6px，而手绘掩码只有 2px（无上游 padding），两条输入路径并不对称。
#   （阶段 105 起管线膨胀已归零，该不对称已消除 —— general 与手绘两条路径都为 0。）
#   阶段 66 真值扫描（7 档 × 双口径）结论——膨胀是净损失：
#     · 检测口径（general 端到端）：dilate 0→4 时精确率 41.9%→35.6%、IoU 28.5%→25.7%，
#       而召回仅 +0.9pp（45.1%→46.0%）；预测覆盖率 1.71%→2.25% 属虚高扩张。
#     · 修复口径：残留几乎完全平坦（textured 35.54~35.67、photo 23.55~23.59，波动属
#       噪声级），而「越界」（真值外 12px 环带被改写）单调上升 0.35→0.49 / 2.59→2.68。
#       即膨胀既擦不掉更多水印，又白白破坏周边正常内容。
#   故默认 0，与网格（阶段 53）、平铺（阶段 54）三条通路口径统一；下游
#   `MASK_DILATE_DEFAULT`（阶段 105 起为 0）不再额外外扩，掩码即实体轮廓。
#   调大：掩码更宽松（精确率与越界受损，召回略升）；调小：更贴近水印真实轮廓。
DETECT_TRANSLUCENT_MASK_DILATE: Final[int] = _env_int(
    "DETECT_TRANSLUCENT_MASK_DILATE", 0, 0, 8
)

# 【渐变检测最小方差解释率】浮点 0~1（阶段 23 新增，默认 0.6）。
#   全局渐变判据：拟合平面解释的灰度低频方差比例需高于该值，才算「占主导的全局渐变」。
#   调高：只在渐变非常「纯净、占主导」时判为渐变水印，抑制普通带渐变照片的误报。
DETECT_GRADIENT_MIN_EXPLAINED: Final[float] = _env_float("DETECT_GRADIENT_MIN_EXPLAINED", 0.6, 0.0, 1.0)

# 【OCR 最小置信度】浮点 0~1（阶段 22 新增，默认 0.25）。
#   EasyOCR 返回的识别框置信度低于该值的直接丢弃，降低误报、提升精度。
DETECT_OCR_MIN_CONFIDENCE: Final[float] = _env_float("DETECT_OCR_MIN_CONFIDENCE", 0.25, 0.0, 1.0)

# 【OCR 预处理放大】布尔（阶段 22 新增，默认开）。
#   小图上的文字对 EasyOCR 较难识别；开启后把搜索块 2x 上采样再识别，提升小字精度。
DETECT_OCR_UPSCALE: Final[bool] = _env_bool("DETECT_OCR_UPSCALE", True)

# 【OCR 角落低置信回退】布尔（阶段审计新增，默认开）。
#   角落搜索区内 EasyOCR 常能「框住」水印文字（检测几何正确），却因文字低对比 /
#   字体风格导致识别失败、给出 ~0.00 置信度，被 DETECT_OCR_MIN_CONFIDENCE 滤掉，
#   进而整段角落水印漏检（实测 q3_2/q4_2/q5_2 因此 mask_recall=0.000）。
#   开启后，当高置信路径在角落 patch 零产出、但存在任意 OCR 候选框时，接受几何
#   「类文本行」（宽高比合理）的候选作定位框——把「识别失败」与「定位能力」解耦。
#   仅作用于角落 patch（_ocr_boxes_in_patch(accept_low_conf=True)），不影响边缘检测通路。
DETECT_OCR_CORNER_FALLBACK: Final[bool] = _env_bool("DETECT_OCR_CORNER_FALLBACK", True)

# 【OCR 单图识别超时护栏】秒（阶段审计新增，默认 30.0）。
#   真实图上偶有图像触发 EasyOCR 内部极慢路径（CPU 模式），单 patch 可卡数分钟，
#   使整批门禁/检测挂起（实测某真实图令 general 全量跑出 918.8s）。开启后，对单个
#   patch 的 OCR 识别套一层 daemon 线程 + join(timeout) 超时，超时仅丢弃该 patch 的
#   OCR 结果、降级走板块/图标通路（mask 召回略降但整体不挂死）。<=0 时关闭护栏、
#   走原生 reader.readtext（无超时）。设为很小值（如 0.01）可用于单元测试注入超时分支。
DETECT_OCR_TIMEOUT: Final[float] = _env_float("DETECT_OCR_TIMEOUT", 30.0, 0.0, 3600.0)

# 【OCR 是否使用 GPU】布尔（阶段 220 新增，默认 True = **跟随设备选择**）。
#   历史：`_get_ocr_reader()` 曾把 `gpu=False` **硬编码**，注释理由是「与本项目默认
#   CPU 一致」。**该前提早已不成立**——`DEVICE_MODE=auto` 下LaMa 推理已在 cuda:0
#   上跑（实测同一条流水线里「LaMa 吃 GPU、OCR 纯 CPU」），OCR 段占全链路 13.9s。
#   实测RTX 5060 上OCR 走 GPU **加速 11~13×**（640×640 0.696s → 0.053s），
#   且**包围盒逐位相同**（掩码不受影响），识别置信度虽有浮点路径差异
#   （实测 0.72 vs 0.37）但**均高于 DETECT_OCR_MIN_CONFIDENCE、判定不翻转**；
#   本项目 OCR 的价值在「定位」而非「识别」，故几何一致性才是硬约束。
#   设 False 可强制 OCR 走 CPU（用于无 GPU 环境的显式声明 / 排查 GPU OCR 异常）。
#   ⚠ 无 GPU 或 GPU 初始化失败时**自动回退 CPU**，绝不因此中断检测。
DETECT_OCR_USE_GPU: Final[bool] = _env_bool("DETECT_OCR_USE_GPU", True)

# 【默认检测预设】字符串（阶段 20 新增）。
#   取值为 core.detection_presets 中注册的预设名（当前 general / ai_tiled /
#   ai_signature / ai_corner / ai_text_heavy / ai_faint_tiled / ai_grid /
#   ai_gradient / ai_logo / ai_multi）。UI 的检测模式下拉、CLI 的 --detect-preset
#   均以它为默认。
DETECT_PRESET_DEFAULT: Final[str] = _env_str("DETECT_PRESET_DEFAULT", "general")


# ==============================================================================
# 第 8 节：图像输入输出配置（IO）
# ------------------------------------------------------------------------------
# 说明：控制程序能读什么格式的图、能存什么格式的图、多大的图会被拦下来。
# ==============================================================================

# 【支持读取的图片格式白名单】
#   只有扩展名在这个清单里的文件才会被程序接受。
#   使用白名单而非黑名单，是出于安全考虑：防止用户误把 .exe 拖进来。
SUPPORTED_INPUT_FORMATS: Final[tuple[str, ...]] = (
    ".png", ".jpg", ".jpeg", ".jpe",
    ".webp", ".bmp", ".tif", ".tiff", ".jfif",
)

# 【支持导出的图片格式】
#   PNG  : 无损压缩，支持透明通道，文件较大（截图、带透明背景的图首选）
#   JPG  : 有损压缩，不支持透明，文件小（照片首选）
#   WEBP : 新一代格式，同画质下比 JPG 小 25%~35%，支持透明（网页用图首选）
#   TIFF : 无损专业格式，支持多层/透明，文件较大（印刷、归档首选）
#   AVIF : 新一代高压缩比格式，画质接近无损而体积极小（长期归档首选）。
#          注意：AVIF 编码依赖运行环境——优先用 OpenCV 编码器，
#          不可用时自动回退到 Pillow（需 pillow-avif-plugin），
#          再不可用时降级为 PNG 并给出告警（绝不因格式崩溃）。
SUPPORTED_OUTPUT_FORMATS: Final[tuple[str, ...]] = ("PNG", "JPG", "WEBP", "TIFF", "AVIF")

# 【默认导出格式】
DEFAULT_OUTPUT_FORMAT: Final[str] = _env_str("DEFAULT_OUTPUT_FORMAT", "PNG").upper()

# 【默认导出画质】取值 1 ~ 100，仅对 JPG 与 WebP 生效。
#   95 是「肉眼几乎看不出损失」与「文件不至于过大」的平衡点。
#   低于 80 会出现明显的块状压缩痕迹。
DEFAULT_OUTPUT_QUALITY: Final[int] = _env_int("DEFAULT_OUTPUT_QUALITY", 95, 1, 100)

# 【PNG 压缩级别】取值 0 ~ 9。
#   0 = 不压缩（存得最快，文件最大）
#   9 = 最大压缩（存得最慢，文件最小，画质完全无损）
#   6 是速度与体积的平衡点。注意：PNG 是无损格式，压缩级别不影响画质。
PNG_COMPRESS_LEVEL: Final[int] = _env_int("PNG_COMPRESS_LEVEL", 6, 0, 9)

# 【是否保留透明通道】布尔（阶段 71 起为**跨格式**主开关）。
#   True  : 目标格式支持透明时保留 alpha（见 OUTPUT_ALPHA_FORMATS，含 PNG/WebP/TIFF）；
#           目标格式不支持透明（如 JPG）时，把图像**合成到 OUTPUT_FLATTEN_BG_COLOR 背景上**。
#   False : 一律合成到背景色（等价于"透明区域填充背景色"）。
#   阶段 71 修复：此前实现只在 PNG 且本开关为真时写 alpha，其余情况**直接丢弃 alpha 并把
#   原始 RGB 原样写出**——而真实 PNG 的透明区域常驻留不可见的 RGB（例如被擦掉的 Logo 原色），
#   于是导出 JPG/WebP/TIFF 后"隐藏内容"会以彩色块形式显形（实测透明块变红块）；且
#   WebP/TIFF 本可携带 alpha（OpenCV 实测支持）却被无谓丢弃。本注释早先声称
#   「False：透明区域统一填充为白色」，实现从未如此——与阶段 70 同族的"声明 ≠ 实现"。
PNG_KEEP_ALPHA: Final[bool] = _env_bool("PNG_KEEP_ALPHA", True)

# 【支持透明通道的导出格式】字符串元组（阶段 71 新增）。
#   实测 OpenCV 的 `.png`/`.webp`/`.tiff` 编码器都能写出 4 通道（alpha 原样保留），
#   故这三者可保留透明；其余格式（JPG，以及多数版本下 cv2 的 AVIF）走背景合成。
OUTPUT_ALPHA_FORMATS: Final[tuple[str, ...]] = ("PNG", "WEBP", "TIFF")

# 【透明合成背景色】RGB 三元组 0~255（阶段 71 新增，默认白色）。
#   当目标格式不支持透明、或用户关闭了保留透明时，透明像素按
#   `out = rgb×a + bg×(1−a)` 合成——这是各家图像编辑器的通行做法，避免暴露隐藏 RGB。
OUTPUT_FLATTEN_BG_COLOR: Final[tuple[int, int, int]] = (255, 255, 255)


# 【TIFF 压缩方式】取值 1 ~ 32946，常用：
#   1   = 不压缩（文件最大、最兼容）
#   5   = LZW 无损压缩（推荐，兼容性好、体积适中）
#   34892 = 无损 WebP（体积最小，但部分老软件打不开）
# 这里默认 5（LZW），在「兼容性」与「体积」之间取得平衡。
TIFF_COMPRESSION: Final[int] = _env_int("TIFF_COMPRESSION", 5, 1, 32946)


# ==============================================================================
# 第 8.5 节：更多输入源配置（PDF 文档 / 视频，阶段 10 新增）
# ------------------------------------------------------------------------------
# 说明：本工具的核心能力是「处理一张图」。要处理 PDF 和视频，思路是把它们
#       拆成一张张图片，逐张去水印，再按原样拼回去：
#
#         PDF  ：整页渲染成图片 → 逐页去水印 → 重新排版成新 PDF
#         视频 ：逐帧读取画面   → 逐帧去水印 → 按原帧率写回新视频
#
#       零基础提示：
#         · 「DPI」是渲染精细度，数字越大越清晰、越慢、文件越大；
#         · 「帧」就是视频里的一张静止画面，30fps 表示 1 秒有 30 张画面；
#         · 视频处理非常慢（一分钟 30fps 视频 = 1800 张图），
#           所以默认设了「最大帧数」和「抽帧步长」两道保险，防止跑到天荒地老。
# ==============================================================================

# 【支持读取的文档格式】目前仅 PDF（依赖 PyMuPDF，见 requirements.txt）。
SUPPORTED_DOC_FORMATS: Final[tuple[str, ...]] = (".pdf",)

# 【支持读取的视频格式】依赖 OpenCV 的解码能力，覆盖最常见的容器格式。
SUPPORTED_VIDEO_FORMATS: Final[tuple[str, ...]] = (
    ".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v",
)

# ------------------------------ PDF 相关 --------------------------------------

# 【PDF 页面渲染 DPI】把 PDF 每一页画成图片时的精细度。
#   72   = 屏幕默认精度（最快，字会糊）
#   150  = 推荐值，清晰度与速度的平衡点（A4 页面约 1240×1754 像素）
#   300  = 印刷级精度（很慢，内存占用高，A4 约 2480×3508 像素）
PDF_RENDER_DPI: Final[int] = _env_int("PDF_RENDER_DPI", 150, 36, 600)

# 【单个 PDF 最多处理页数】超出部分会被跳过并给出提示，防止误传千页文档卡死。
PDF_MAX_PAGES: Final[int] = _env_int("PDF_MAX_PAGES", 100, 1, 5000)

# 【单个 PDF 文件体积上限】单位：MB。超出直接拒绝，避免超大文档把内存/磁盘吃满。
#   默认 500MB：常见图文 PDF 远小于此，扫描件合订本偶尔贴近，留足余量。
MAX_PDF_FILE_MB: Final[int] = _env_int("MAX_PDF_FILE_MB", 500, 1, 10240)

# 【回写 PDF 时页面图片的编码格式】
#   PNG  : 无损，画质最好，但文件体积大（扫描件、图表首选）
#   JPEG : 有损，体积小很多（照片型页面首选）
PDF_PAGE_IMAGE_FMT: Final[str] = _env_str("PDF_PAGE_IMAGE_FMT", "JPEG").upper()

# 【回写 PDF 时 JPEG 的画质】1~100，仅在 PDF_PAGE_IMAGE_FMT=JPEG 时生效。
PDF_JPEG_QUALITY: Final[int] = _env_int("PDF_JPEG_QUALITY", 92, 1, 100)

# 【PDF 处理失败页的策略】
#   True  : 某页处理失败时，保留该页原始内容（推荐，保证输出 PDF 页数不变）
#   False : 处理失败即整个任务报错退出
PDF_KEEP_FAILED_PAGE: Final[bool] = _env_bool("PDF_KEEP_FAILED_PAGE", True)

# ------------------------------ 视频相关 --------------------------------------

# 【单个视频最多处理帧数】默认 900 帧（30fps 下约 30 秒）。
#   这是防呆保险：处理 1 小时的视频需要数小时甚至数天，绝大多数人不需要。
VIDEO_MAX_FRAMES: Final[int] = _env_int("VIDEO_MAX_FRAMES", 900, 1, 100000)

# 【单个视频文件体积上限】单位：MB。超出直接拒绝，避免超大视频把内存/磁盘吃满。
#   默认 2000MB（2GB）：常见短视频远小于此，4K 长视频偶尔贴近，留足余量。
MAX_VIDEO_FILE_MB: Final[int] = _env_int("MAX_VIDEO_FILE_MB", 2000, 1, 102400)

# 【抽帧步长】每隔几帧真正跑一次 AI 修复。
#   1 = 每帧都修（最慢、最准）
#   2 = 隔一帧修一次，中间帧复用上一帧的掩码（约快一倍，适合固定位置水印）
#   注意：步长只影响「重新检测水印位置」的频率，所有帧都会被修复和写入，
#         不会造成丢帧或视频变短。
VIDEO_FRAME_STEP: Final[int] = _env_int("VIDEO_FRAME_STEP", 1, 1, 60)

# 【是否复用上一次的水印掩码】
#   True  : 台标、右下角固定水印等「位置不变」的场景，可大幅提速；
#   False : 每帧独立检测（滚动字幕、飘动水印必须关掉复用）。
VIDEO_REUSE_MASK: Final[bool] = _env_bool("VIDEO_REUSE_MASK", True)

# 【复用掩码的漂移下限 IoU】0.0 ~ 1.0（阶段 64 新增，默认 0.70）。
#   复用掩码（抽帧 + 缓存）只在「水印位置不变」时成立。实测（640×360，角落 LOGO，
#   1px/帧漂移，掩码自带 padding）：
#     · 漂移 0~8px（新旧掩码 IoU ≥ 0.74）→ 旧掩码无害（残留为新掩码的 0.85~1.01 倍，
#       padding 吸收了位移）；
#     · 漂移 12~16px（IoU 0.56~0.64）→ 旧掩码残留骤增到 2.5~3.8 倍，肉眼即"没擦干净"；
#     · 漂移 ≥20px（IoU ≤ 0.28）→ 逐帧检测本身也跟不住（水印移出可检区域），非本护栏职责。
#   故阈值取 0.70，恰好区分"无害区(0.74)"与"危险区(0.64)"：低于它才自动改回逐帧检测
#   （step=1）并告警，避免"护栏误触发导致白白变慢"。静止水印相邻帧 IoU ≈ 0.99，余量充足。
VIDEO_REUSE_MIN_IOU: Final[float] = _env_float("VIDEO_REUSE_MIN_IOU", 0.70, 0.0, 1.0)

# 【输出视频的编码器（FourCC 四字符码）】按顺序尝试，谁先成功用谁。
#   mp4v : 兼容性最好，几乎所有环境的 OpenCV 都自带
#   avc1 : H.264，体积更小画质更好，但需要系统装了对应编解码器
#   XVID : 老牌 MPEG-4，配 .avi 容器时最稳
VIDEO_CODEC_CANDIDATES: Final[tuple[str, ...]] = ("mp4v", "avc1", "XVID")

# 【输出视频扩展名】统一输出 mp4，播放器兼容性最好。
VIDEO_OUTPUT_EXT: Final[str] = _env_str("VIDEO_OUTPUT_EXT", ".mp4")

# 【是否在视频处理时打印每帧日志】
#   False : 只按百分比汇报进度（推荐，日志清爽）
#   True  : 每帧一条日志（调试用，日志会非常长）
VIDEO_VERBOSE_FRAME_LOG: Final[bool] = _env_bool("VIDEO_VERBOSE_FRAME_LOG", False)

# 【视频结果浏览时抽取的样帧数】处理完成后在结果区生成 N 张均匀抽样的缩略帧，
# 便于不播放视频也能快速浏览修复效果；点击任一帧可加载到抽帧对比面板。
VIDEO_COMPARE_FRAMES: Final[int] = _env_int("VIDEO_COMPARE_FRAMES", 6, 1, 48)


# ==============================================================================
# 第 8.6 节：插件与自定义模型配置（PLUGIN，阶段 11 新增）
# ------------------------------------------------------------------------------
# 说明：本项目支持通过「插件」接入第三方修复引擎（例如你自己的 diffusion 模型、
#       其它 TorchScript inpaint 模型，甚至远程推理 API）。
#       插件是符合 core.plugins.InpaintEnginePlugin 接口的 .py 文件，放进插件目录，
#       程序启动时自动发现并注册，随后即可在界面 / 命令行 / API 的「修复引擎」里选用。
#       详见 docs/11-自定义模型与插件体系设计.md 与 plugins/README.md。
# ==============================================================================

# ---------- 8.6.1 插件目录 ----------
# 【插件目录列表】支持多个目录，按先后顺序扫描。
#   默认 ["plugins"]：相对项目根目录的 plugins/ 子目录（已随项目创建）。
#   可用环境变量覆盖，例如 WME_PLUGIN_DIRS=/opt/myplugins,./local_plugins
#   绝对路径直接使用，相对路径以项目根目录为基准解析。
PLUGIN_DIRS: Final[list[str]] = _env_list("PLUGIN_DIRS", ["plugins"])

# ---------- 8.6.2 启用清单 ----------
# 【启用的插件】留空表示「加载插件目录下全部插件」。
#   若只想启用其中几个，填入文件名（不含 .py），逗号分隔，
#   例如 WME_PLUGIN_ENABLED=sample_model_plugin,my_diffusion
#   不在清单内的插件文件会被跳过（便于灰度 / 调试）。
PLUGIN_ENABLED: Final[list[str]] = _env_list("PLUGIN_ENABLED", [])

# ---------- 8.6.3 覆盖保护（阶段 77 新增）----------
# 【是否允许插件覆盖同名引擎】默认 False —— 插件**不可**顶替已注册的引擎
#   （尤其是内建 lama / opencv），重复注册一律拒绝并告警。
#   阶段 77 审计发现：此前插件只要把 name 写成 "lama" 就能静默替换内建引擎，
#   而引擎清单仍显示「LaMa 深度学习」，导致「界面显示」与「实际执行」完全脱节。
#   确需用插件顶替内建引擎时（例如自研 LaMa 加速版）可显式开启：
#     WME_PLUGIN_ALLOW_OVERRIDE=1   或   PLUGIN_ALLOW_OVERRIDE = True
#   开启后每次覆盖仍会逐条 WARNING 记录「谁覆盖了谁」，不做静默处理。
PLUGIN_ALLOW_OVERRIDE: Final[bool] = _env_bool("PLUGIN_ALLOW_OVERRIDE", False)


# ==============================================================================
# 第 3.5 节：批量结果质量评估（阶段 8 新增）
# ------------------------------------------------------------------------------
# 修复完成后，用「结构相似度 SSIM + 变化像素比例」给每张结果打个综合分，
# 帮用户快速筛出「可能没修干净」或「修过头」的图，决定要不要重做。
# 评分逻辑（详见 services/quality_service.py）：
#   · changed_ratio 过低（变化像素太少）：水印大概率还留着 → 标记「需复核」
#   · SSIM 过低且 changed_ratio 过大：大面积被改写 → 标记「可能过处理」
#   · 其余：视为「良好」
# ==============================================================================

# 【变化像素比例下限】低于此值视为「几乎没改动」，怀疑水印没被修掉。
#   取值 0~1。0.0008 约等于 800 万像素图里仅有约 6400 个像素变化。
QUALITY_REVIEW_CHANGED_MIN: Final[float] = _env_float(
    "QUALITY_REVIEW_CHANGED_MIN", 0.0008, 0.0, 1.0
)

# 【变化像素比例上限】高于此值视为「改动过多」，怀疑修复过头（误伤原图）。
#   取值 0~1。一般水印只占整图的很小一部分，超过 25% 就值得警惕。
QUALITY_WARN_CHANGED_MAX: Final[float] = _env_float(
    "QUALITY_WARN_CHANGED_MAX", 0.25, 0.0, 1.0
)

# 【SSIM 告警阈值】结构相似度低于此值（改动很大且结构变化剧烈）给告警。
#   取值 0~1。0.85 以下说明修复区域与周围差异明显，需人工留意。
QUALITY_SSIM_WARN: Final[float] = _env_float("QUALITY_SSIM_WARN", 0.85, 0.0, 1.0)

# 【质量分告警线】综合质量分低于此值（0~100）标记为「需复核」。
QUALITY_SCORE_REVIEW: Final[float] = _env_float("QUALITY_SCORE_REVIEW", 60.0, 0.0, 100.0)

# 【是否保留原图的 EXIF 信息】
#   EXIF 里含有拍摄时间、相机型号，有时还含 GPS 定位坐标。
#   False（默认）会剥离这些信息，更利于隐私保护。
KEEP_EXIF: Final[bool] = _env_bool("KEEP_EXIF", False)

# 【单张图片最大允许像素数】
#   超过此值的图片会被拒绝处理，防止一张 2 亿像素的巨图直接把内存撑爆。
#   默认 8000 万像素，相当于 8944×8944，已远超常规摄影需求。
MAX_IMAGE_PIXELS: Final[int] = _env_int("MAX_IMAGE_PIXELS", 80_000_000, 1_000_000, 1_000_000_000)

# 【单张图片最大文件体积】单位：MB。
MAX_IMAGE_FILE_MB: Final[int] = _env_int("MAX_IMAGE_FILE_MB", 100, 1, 2048)

# 【加载时按 EXIF 方向校正】布尔（阶段 70 新增，默认 True）。
#   手机照片普遍带 EXIF 方向标签（Orientation=6 即"顺时针旋转 90°"）。实测本工程原先的
#   解码路径（读字节 → `cv2.imdecode`）**不应用**该标签：Orientation=6 时加载到的是
#   未旋转的像素（80×120，而查看器显示 120×80），Orientation=3 时内容上下颠倒
#   （maxdiff=197）；而保存端不写 EXIF，于是**导出图相对输入在查看器中会被旋转**，
#   且"角标/底部小字"这类锚定位置的判据会在错误的边上搜索。
#   开启后：加载时把方向烘焙进像素（rgb 与 alpha 同步变换），全链路（检测/修复/保存）
#   都在"用户看到的方向"上工作；Pillow 不可用或读取失败时静默回退为不校正。
IMAGE_APPLY_EXIF_ORIENTATION: Final[bool] = _env_bool(
    "IMAGE_APPLY_EXIF_ORIENTATION", True
)

# 【加载时把非 8 位图规范化为 8 位】布尔（阶段 70 新增，默认 True）。
#   16 位 PNG/TIFF（.tif/.tiff 在输入白名单内）解码后是 uint16，而全链路常量都按 8 位
#   设计：实测修复阶段直接抛 `icvInpaint ... Unsupported format`（16 位 3 通道不支持）
#   ——用户在"检测看起来正常"之后才遇到硬失败。开启后按位深线性映射到 0~255。
IMAGE_NORMALIZE_BIT_DEPTH: Final[bool] = _env_bool(
    "IMAGE_NORMALIZE_BIT_DEPTH", True
)

# 【输出文件名模板】可用占位符：
#   {stem}      原文件名（不含扩展名）
#   {date}      处理日期，格式 20260803
#   {time}      处理时间，格式 181955
#   {index}     批量处理时的序号，从 1 开始
#   {engine}    实际使用的修复引擎名（lama / opencv）
OUTPUT_NAME_TEMPLATE: Final[str] = _env_str("OUTPUT_NAME_TEMPLATE", "{stem}_cleaned_{time}")

# 【是否按日期自动分文件夹】
#   True  : 输出到 outputs/2026-08-03/xxx.png（推荐，文件多了也不乱）
#   False : 全部直接输出到 outputs/ 根目录
OUTPUT_GROUP_BY_DATE: Final[bool] = _env_bool("OUTPUT_GROUP_BY_DATE", True)

# ==============================================================================
# 第 8.5 节：导出服务配置（EXPORT SERVICE，阶段 6 新增）
# ------------------------------------------------------------------------------
# 说明：阶段 6 引入「导出服务」，负责把修复结果按用户指定格式/画质重新编码、
#       生成「原图 vs 修复后」左右对比图、把多张结果打包成 ZIP。
#       这一节的参数控制对比图的样式与默认导出行为。
# ==============================================================================

# 【对比图中间隔像素】单位：像素。
#   生成「原图 | 修复后」对比图时，两张图之间留一条白边，视觉上更清晰。
#   设为 0 则两张图紧挨着。
EXPORT_COMPARE_GAP: Final[int] = _env_int("EXPORT_COMPARE_GAP", 24, 0, 200)

# 【对比图是否标注 Before/After】
#   True  : 在两张图上方分别标注 "Before" / "After"（英文，避免中文字体缺失变方块）
#   False : 不标注，仅并排展示
EXPORT_COMPARE_LABEL: Final[bool] = _env_bool("EXPORT_COMPARE_LABEL", True)


# ==============================================================================
# 第 9 节：性能与大图分块配置（PERFORMANCE）
# ------------------------------------------------------------------------------
# 说明：LaMa 模型对显存/内存的消耗与图片面积成正比。
#       处理大图时若一次性送入模型，极易触发「内存溢出」而崩溃。
#       解决方案是「分块处理」：把大图切成若干小块，逐块修复后再拼回去。
#       这一节的参数在阶段 10「性能深度优化」中会被重点使用。
# ==============================================================================

# 【模型输入尺寸对齐倍数】
#   LaMa 的网络结构要求输入图片的宽高必须是 8 的整数倍，
#   否则下采样/上采样过程中张量尺寸对不上会直接报错。
#   程序会自动把图片补边到最近的 8 的倍数，处理完再裁回原尺寸。
MODEL_SIZE_ALIGN: Final[int] = 8

# 【单次推理最大边长】单位：像素。
#   图片长边超过此值时，触发「先缩小 → 修复 → 再放大回原尺寸」流程，
#   或触发分块处理流程（取决于下面的 ENABLE_TILING 开关）。
INFER_MAX_SIDE: Final[int] = _env_int("INFER_MAX_SIDE", 2048, 256, 8192)

# 【是否启用大图分块处理】
#   True  : 大图切块逐块修复。优点是画质不损失，缺点是速度慢、可能有拼接痕
#   False : 大图整体缩小后修复再放大。优点是快且无拼接痕，缺点是细节略糊
ENABLE_TILING: Final[bool] = _env_bool("ENABLE_TILING", True)

# 【分块尺寸】单位：像素。每一块的边长。
#   512 适合 4GB 显存；768 适合 6GB 显存；1024 适合 8GB 及以上显存。
TILE_SIZE: Final[int] = _env_int("TILE_SIZE", 512, 128, 2048)

# 【分块重叠像素】单位：像素。
#   相邻两块之间要重叠一部分，拼接时在重叠区做渐变融合，才不会看到接缝。
#   经验值：取分块尺寸的 1/8 左右。
TILE_OVERLAP: Final[int] = _env_int("TILE_OVERLAP", 64, 0, 512)

# 【是否跳过不含水印的分块】
#   True  : 掩码在某一块内全黑（没有要修的东西）就直接跳过该块，大幅提速
#   False : 所有块都送进模型（更慢，无实际收益，仅用于调试对比）
TILE_SKIP_EMPTY: Final[bool] = _env_bool("TILE_SKIP_EMPTY", True)

# 【是否裁剪到水印包围盒再修复】
#   掩码占比小且图较大时，只对「水印包围盒 + margin」送引擎推理，跳过整图 forward
#   （LaMa fp32 大图 forward 是 full 管线主瓶颈，实测 2048² 上 ~4-8s）。
#   ⚠ 它**改变输出**：LaMa 对 crop vs 整图有尺寸相关行为（实测 mask 内 in>10 最高 ~63%，
#   且不随 margin 缩小）——属「换速度改水印填充」权衡。但 mask 外（干净区）与 full 逐字节一致
#   （crop 只贴回 mask 区），LaMa 在 mask 外固有轻微扰动两种模式同量级、crop 不更差。
#   默认开：crop_quality_probe 全量 58 张 ≥2048 图判决——安全判据 58/58 通过（干净区零新增风险），
#   速度中位 1.51x、2048² 实测 ~3.2x；质量软门禁未全过（水印填充不同，无干净真值不可判优劣）。
#   如需回退为 full（不裁）设 INFER_CROP_TO_MASK=0。
INFER_CROP_TO_MASK: Final[bool] = _env_bool("INFER_CROP_TO_MASK", True)

# 【裁剪触发：图长边下限】长边低于此值不裁（小图整图推理本就快，裁了无收益）。
INFER_CROP_MIN_SIDE: Final[int] = _env_int("INFER_CROP_MIN_SIDE", 1024, 256, 8192)

# 【裁剪触发：掩码占比上限】掩码像素占图比例超过此值不裁（大掩码的包围盒≈整图，无意义）。
INFER_CROP_MAX_MASK_RATIO: Final[float] = _env_float("INFER_CROP_MAX_MASK_RATIO", 0.25, 0.01, 0.95)

# 【包围盒外扩 margin】单位：像素。给 LaMa 补足水印周边上下文（裁剪改变输出的主因）。
INFER_CROP_MARGIN: Final[int] = _env_int("INFER_CROP_MARGIN", 256, 0, 1024)

# =============================================================================
# 第 8.x 节：性能与内存优化配置（PERF，阶段 14 新增）
# =============================================================================
# 【进度回调节流间隔】单位：秒。
#   批量 / 视频 / 分块推理会高频上报进度，过密的回调会重绘 UI、拖慢交互。
#   两次真实上报之间小于此间隔的回调会被合并丢弃，仅在间隔到期或进度跃变
#   超过阈值时才真正触发上报（详见 utils/progress.py 的 ThrottledProgress）。
PROGRESS_THROTTLE_SEC: Final[float] = _env_float("PROGRESS_THROTTLE_SEC", 0.08, 0.0, 2.0)

# 【空显存释放节流间隔】单位：秒。
#   torch.cuda.empty_cache() 是同步操作，会打断 GPU 流水线、拖慢连续推理。
#   批量 / 视频逐帧场景下每帧都调用 empty_cache 会显著掉帧，因此两次释放之间
#   小于此间隔则跳过（显存压力由 PyTorch 缓存池自身兜底）。
EMPTY_CACHE_MIN_INTERVAL_SEC: Final[float] = _env_float(
    "EMPTY_CACHE_MIN_INTERVAL_SEC", 1.0, 0.0, 30.0
)

# 【批量 / 视频是否预先选定设备并复用】
#   True  : 批量入口在开始时 select_device() 一次，并把句柄传给每张图的修复，
#           省去逐图重复探测设备带来的微小开销（设备缓存本身已能命中）。
#   False : 每次修复各自 select_device()。
PERF_REUSE_DEVICE: Final[bool] = _env_bool("PERF_REUSE_DEVICE", True)

# 【批量处理并发数】
#   同时处理几张图片。注意：显卡模式下建议保持 1，因为显存是瓶颈，
#   开多个并发反而会因为显存争抢而变慢甚至溢出。
#   CPU 模式下可适当调大到 2 ~ 4。
BATCH_CONCURRENCY: Final[int] = _env_int("BATCH_CONCURRENCY", 1, 1, 16)

# =============================================================================
# 第 8.y 节：UI 体验增强配置（阶段 15 新增）
# =============================================================================
# 【全局拖拽上传浮层】布尔。
#   True  : 在任意页签把文件拖到页面任意位置，松手即自动填入「当前激活页签」
#           对应的上传输入（单图编辑框 / 自动上传框 / 批量文件 / PDF / 视频）。
#   False : 关闭该浮层，退回各组件自身的原生拖拽（Gradio 默认行为）。
UI_GLOBAL_DRAGDROP: Final[bool] = _env_bool("UI_GLOBAL_DRAGDROP", True)

# 【键盘快捷键】布尔。
#   True  : 启用全局快捷键（Shift+R 运行/修复、Shift+D 自动检测、Shift+F 刷新历史）。
#            输入框聚焦时自动失效，避免误触。
#   False : 关闭快捷键。
UI_SHORTCUTS_ENABLED: Final[bool] = _env_bool("UI_SHORTCUTS_ENABLED", True)

# 【结果对比视图】布尔。
#   True  : 单图/自动页处理后展示「原图|处理后」拼接对比图；
#           批量/历史页提供「对比视图」开关，切换为 Before/After 拼接缩略图。
#   False : 关闭对比视图（仅展示处理后结果）。
UI_COMPARE_VIEW: Final[bool] = _env_bool("UI_COMPARE_VIEW", True)

# 【对比查看器默认模式】字符串，取值 concat / slider / heatmap（阶段 38 新增）。
#   concat  : 静态左右并排拼接（默认，最稳，任何浏览器都能看）；
#   slider  : 可拖动分割线的滑块揭示对比；
#   heatmap : 差异热图（越亮表示该处像素改动越大）。
#   非法值在服务层会被静默回退为 concat，不会抛错。
UI_COMPARE_DEFAULT_MODE: Final[str] = _env_str("UI_COMPARE_DEFAULT_MODE", "concat")

# 【对比查看器渲染最大边长】整数，128 ~ 4096（阶段 38 新增）。
#   查看器把图片内嵌为 base64 传给浏览器，原图过大时会显著拖慢页面。
#   渲染前把长边等比缩到该值以内（仅影响「看」，导出落盘仍用原始分辨率）。
UI_COMPARE_MAX_SIDE: Final[int] = _env_int("UI_COMPARE_MAX_SIDE", 1280, 128, 4096)

# 【界面语言】字符串，取值见 utils.i18n.available_languages()（当前 zh-CN / en-US）。
#   用户可在「设置与导出」页切换并持久化到缓存目录 JSON；环境变量
#   WME_UI_LANGUAGE 优先级最高。切换后重启程序完全生效（部分静态文案会即时刷新）。
#   非法值会被 i18n.load_language 回退为默认 zh-CN。
UI_LANGUAGE: Final[str] = _env_str("UI_LANGUAGE", "zh-CN")

# 【内存安全水位】取值 0.5 ~ 0.95。
#   系统内存占用率超过该比例时，程序会暂停接收新任务并主动触发垃圾回收，
#   防止把整台电脑拖到卡死。
MEMORY_SAFE_RATIO: Final[float] = _env_float("MEMORY_SAFE_RATIO", 0.85, 0.5, 0.95)

# =============================================================================
# 第 8.z 节：性能与内存再优化配置（PERF2，阶段 16 新增）
# =============================================================================
# 【图片解码内存缓存】布尔。
#   批量处理 / 导出 / 质量评估时，同一对「原图+结果」经常被重复从磁盘解码
#   （例如批量 _on_update 里 evaluate 解码 2 次、对比图又解码 2 次）。
#   开启后，load_image / load_image_with_alpha 会以「路径+修改时间+文件大小」
#   为键缓存解码结果，后续命中直接复用，省去重复的磁盘读取与 cv2 解码开销。
#   缓存键含 mtime/size，文件被覆盖重写后自动失效，不会读到陈旧内容。
IMAGE_DECODE_CACHE_ENABLED: Final[bool] = _env_bool("IMAGE_DECODE_CACHE_ENABLED", True)

# 【解码缓存最大条目数】整数。LRU 淘汰；超出后最久未用的项被丢弃。
#   既要命中率又要防止内存无限增长，默认 128 条，覆盖绝大多数批量/导出场景。
IMAGE_DECODE_CACHE_MAX_ENTRIES: Final[int] = _env_int(
    "IMAGE_DECODE_CACHE_MAX_ENTRIES", 128, 1, 4096
)

# 【单图可缓存的最大像素数】整数。
#   超过该像素的图片不参与缓存，避免一张超大图就把缓存内存撑爆。
#   默认 2500 万像素（约 5000×5000），常规照片远小于此。
IMAGE_DECODE_CACHE_MAX_PIXELS_SINGLE: Final[int] = _env_int(
    "IMAGE_DECODE_CACHE_MAX_PIXELS_SINGLE", 25_000_000, 1_000_000, 200_000_000
)

# 【质量评估降采样上限】整数（边长像素）。
#   evaluate_quality 的 SSIM / 变化比例在大图上非常耗时（2000×2000 约 0.67s）。
#   当图片任一边长超过该值时，先把原图与结果图等比缩放到该上限再做评估，
#   启发式分级结论基本不变，但耗时随面积下降约 4 倍。设为 0 表示始终用原图评估。
QUALITY_MAX_EVAL_SIDE: Final[int] = _env_int("QUALITY_MAX_EVAL_SIDE", 1024, 0, 8192)

# =============================================================================
# 第 8.aa 节：批量对比与报告配置（阶段 17 新增）
# =============================================================================
# 【报告是否含质量评估】布尔。
#   True  : 报告为每条结果计算并展示质量分/级别（依赖 quality_service，
#           超大图自动降采样，见 QUALITY_MAX_EVAL_SIDE）。
#   False : 报告只列原图/结果/参数/时间，不评估质量（更快，适合纯交付）。
REPORT_INCLUDE_QUALITY: Final[bool] = _env_bool("REPORT_INCLUDE_QUALITY", True)

# 【报告内对比图最大边长】整数（像素）。
#   对比图（Before|After）嵌入报告前缩放到该边长以内，避免报告文件过大；
#   0 表示不缩放（用原分辨率）。
REPORT_COMPARE_MAX_SIDE: Final[int] = _env_int("REPORT_COMPARE_MAX_SIDE", 1000, 0, 4096)

# 【PDF 每页记录数】整数（1~8）。PDF 报告每页垂直排布几条记录。
REPORT_PDF_RECORDS_PER_PAGE: Final[int] = _env_int("REPORT_PDF_RECORDS_PER_PAGE", 2, 1, 8)


# =============================================================================
# 第 8.ab 节：结果后处理与画质增强配置（阶段 18 新增）
# =============================================================================
# 【后处理总开关】布尔。
#   True  : 修复（inpaint）完成后，在 services 层自动叠加「锐化 + 色彩还原 +
#           边缘融合」三步增强，显著提升成品观感（可在界面参数面板逐次关闭）。
#   False : 直接返回 inpaint 原始结果，不做任何增强。
POSTPROCESS_ENABLED: Final[bool] = _env_bool("POSTPROCESS_ENABLED", True)

# 【锐化开关】布尔。是否对修复结果做 unsharp mask 锐化，找回 inpaint 损失的细节。
POSTPROCESS_SHARPEN: Final[bool] = _env_bool("POSTPROCESS_SHARPEN", True)

# 【锐化强度】浮点（0~2）。0 等效关闭；越大越清晰，过大会放大噪点。
#   实际高斯模糊半径由该值换算（amount 越大，模糊核越小、细节越多）。
POSTPROCESS_SHARPEN_AMOUNT: Final[float] = _env_float("POSTPROCESS_SHARPEN_AMOUNT", 0.6, 0.0, 2.0)

# 【色彩还原开关】布尔。把被修复区颜色统计向「紧邻掩码的局部环带」对齐，纠正 inpaint
#   轻微色偏。默认关闭（阶段 60 收敛）：对"去水印"而言，修复区颜色一旦被二次移动，
#   在稀疏/条带掩码（网格点阵、文字行）上会引入系统性明暗错位（实测 gradient grid/
#   edge_text 掩码区 PSNR 掉 6~7 dB，见 docs/60-*）；OpenCV/LaMa 修复本身已是局部
#   估计，一般无需全局性颜色对齐。需要时可在界面/参数显式开启（函数语义已修正：
#   只做局部环带均值对齐，不再重缩放对比度）。
POSTPROCESS_COLOR_MATCH: Final[bool] = _env_bool("POSTPROCESS_COLOR_MATCH", False)

# 【色彩还原强度】浮点（0~1）。0=不做；1=完全对齐到环带统计；中间值按比例混合，
#   避免对原本正常的修复结果过度改写。
POSTPROCESS_COLOR_MATCH_STRENGTH: Final[float] = _env_float("POSTPROCESS_COLOR_MATCH_STRENGTH", 0.8, 0.0, 1.0)

# 【边缘融合开关】布尔。在掩码边界做窄带羽化，柔化 inpaint 与原图的接缝台阶，
#   让修复区过渡更自然。仅在提供原图与掩码时生效。
POSTPROCESS_EDGE_BLEND: Final[bool] = _env_bool("POSTPROCESS_EDGE_BLEND", True)

# 【边缘融合带宽】整数（像素，0~20）。羽化带宽度（掩码膨胀半径），越大过渡越柔。
POSTPROCESS_EDGE_BLEND_BAND: Final[int] = _env_int("POSTPROCESS_EDGE_BLEND_BAND", 3, 0, 20)

# =============================================================================
# 【画质增强深化 · 阶段 21】在阶段 18 三步（锐化 / 色彩还原 / 边缘融合）基础上，
#   针对「去 AI 水印」成品观感做三道补充增强。默认全部关闭，确保向后兼容
#   （旧调用方不传这些参数时，行为与阶段 18 完全一致）。
# =============================================================================

# 【Poisson 无缝融合开关】布尔。开启后用 cv2.seamlessClone 把修复区按原图梯度
#   无缝融入周围，是「边缘自然融合」的深化版（比窄带羽化更彻底地消除接缝台阶）。
#   仅在有原图 + 掩码时生效。默认关闭。
POSTPROCESS_POISSON_BLEND: Final[bool] = _env_bool("POSTPROCESS_POISSON_BLEND", False)

# 【Poisson 融合强度】浮点（0~1）。1=完全采用 Poisson 结果；中间值把 Poisson 结果
#   与原始后处理结果按比例混合，便于在「无缝」与「保留修复细节」间取舍。
POSTPROCESS_POISSON_BLEND_STRENGTH: Final[float] = _env_float("POSTPROCESS_POISSON_BLEND_STRENGTH", 1.0, 0.0, 1.0)

# 【纹理细节重建开关】布尔。inpaint 填充区常偏平/糊；开启后把「原图外部区域的高频
#   细节」按强度注入修复区，重建纹理，减少肉眼可见的「修补块」。需原图 + 掩码。
#   默认关闭。
POSTPROCESS_TEXTURE_TRANSFER: Final[bool] = _env_bool("POSTPROCESS_TEXTURE_TRANSFER", False)

# 【纹理重建强度】浮点（0~1）。注入细节的比例，越大纹理越明显（过大会显脏）。
POSTPROCESS_TEXTURE_TRANSFER_AMOUNT: Final[float] = _env_float("POSTPROCESS_TEXTURE_TRANSFER_AMOUNT", 0.5, 0.0, 1.0)

# =============================================================================
# 【可选增强自动回退 · 阶段 61】上面这些「深度增强」（纹理重建 / Poisson / 伪影抑制 /
#   轻量超分）默认关闭，用户可显式开启，但实测存在本质不安全项：Poisson 无缝融合按
#   原图边界梯度重建修复区，会把刚填好的内容整体替换（corner_logo 掩码区 PSNR
#   −10.9 ~ −26.4 dB，见 docs/61-*）。故给「可选增强」统一加一道自动回退：用无参考
#   质量分（core.quality.no_reference_inpaint_score，阶段 56）比较该步前后，若明显
#   变差则撤销该步、回到本步之前的结果。默认开启（保护而非限制选择）。
# =============================================================================
# 【可选增强自动回退开关】布尔。False = 完全信任用户选择、不做任何回退。
POSTPROCESS_AUTO_REVERT: Final[bool] = _env_bool("POSTPROCESS_AUTO_REVERT", True)

# 【纹理重建最小外围纹理】浮点（0~32，灰阶）。外围（掩码外环带）高频细节标准差低于此值
#   时跳过纹理重建——外围本身平滑（纯渐变/天空/纯色背景）时注入外来高频等于往干净填充区
#   撒噪声（实测 gradient corner_logo 掩码区 PSNR −12.6 dB，见 docs/61-*）。
POSTPROCESS_TEXTURE_MIN_DETAIL: Final[float] = _env_float(
    "POSTPROCESS_TEXTURE_MIN_DETAIL", 1.5, 0.0, 32.0
)

# 【纹理重建部署级放行】布尔，默认 False。阶段 61：该增强原为死代码（detail 作用域与
#   权重互斥，乘积恒 0，实测全 12 用例 Δ=0.00），修好后实测**净负收益**——注入的外来高频
#   既无法还原真实底图纹理，还可能把掩膜外残留水印的高频带进修复区（gradient corner_logo
#   −12.6 dB、textured corner_logo −3.3 dB）。故与 Poisson 同策略：需部署层显式放行。
POSTPROCESS_TEXTURE_ALLOW: Final[bool] = _env_bool("POSTPROCESS_TEXTURE_ALLOW", False)
#   梯度重建修复区，实测在去水印场景**极端不稳定**（corner_logo/edge_text 掩码区 PSNR
#   −10.9 ~ −26.4 dB，textured faint_tiled 上又 +15.9 dB），且无参考质量分对此类劣化不
#   敏感（自动回退拦不住）。故即使调用方传入 poisson_blend=True，也需部署层显式放行才生效。
POSTPROCESS_POISSON_ALLOW: Final[bool] = _env_bool("POSTPROCESS_POISSON_ALLOW", False)

# 【自动回退容差】浮点（0~1）。质量分下滑超过该绝对值才回退（避免把细微波动当劣化）。
POSTPROCESS_AUTO_REVERT_TOL: Final[float] = _env_float("POSTPROCESS_AUTO_REVERT_TOL", 0.02, 0.0, 1.0)

# 【伪影抑制开关】布尔。inpaint（尤其 LaMa）有时在填充区留下网格/块状伪影；开启后
#   在掩码区内做边缘保持双边滤波，抑制块状伪影，同时尽量保留已注入的纹理细节。
#   默认关闭。
POSTPROCESS_ARTIFACT_REDUCE: Final[bool] = _env_bool("POSTPROCESS_ARTIFACT_REDUCE", False)

# 【伪影抑制强度】浮点（0~1）。双边滤波强度，越大伪影抑制越强（过大会磨平细节）。
POSTPROCESS_ARTIFACT_REDUCE_AMOUNT: Final[float] = _env_float("POSTPROCESS_ARTIFACT_REDUCE_AMOUNT", 0.5, 0.0, 1.0)

# 【阶段 29 · 轻量超分（细节重建）开关】布尔。无需 AI 模型 / torch，纯 cv2 实现：
#   把原图外部区域的高频细节（拉普拉斯）注入修复区，并叠加 unsharp 强化，抵消 inpaint
#   造成的模糊 / 细节损失。同分辨率（不改变画布尺寸），避免破坏「优化前后同尺寸对比」
#   契约。默认关闭（属画质增强深化可选项）。
POSTPROCESS_SUPERRES_ENABLED: Final[bool] = _env_bool("POSTPROCESS_SUPERRES_ENABLED", False)

# 【阶段 29 · 轻量超分强度】浮点（0~1）。细节注入比例 + 整体锐化强度，
#   越大细节越锐利（过大会引入噪点）。仅 enable 时生效。
POSTPROCESS_SUPERRES_AMOUNT: Final[float] = _env_float("POSTPROCESS_SUPERRES_AMOUNT", 0.6, 0.0, 1.0)

# ==============================================================================
# 第 9.5 节：真·超分（阶段 32，AI 模型 + torch 真实放大）
# ------------------------------------------------------------------------------
# 说明：阶段 29 的「轻量超分」是同分辨率 cv2 细节重建（不改变画布尺寸）。
#       本段提供「真实超分辨率放大」能力（改变画布尺寸，如 4×）——依赖 torch +
#       预训练模型；采用 lazy-import + 优雅降级：缺 torch / 缺权重时自动回退到
#       阶段 29 轻量超分，保证项目在无 torch 环境下仍可完整测试与运行（向后兼容）。
#       真实超分与轻量超分互斥（不叠加），避免细节过锐 / 双重放大。
# ==============================================================================

# 【真·超分总开关】布尔。开启后，后处理阶段优先尝试真实模型放大；
#   后端不可用（缺 torch / 缺权重）时自动回退轻量超分。默认关闭（需用户显式开启）。
REAL_SUPERRES_ENABLED: Final[bool] = _env_bool("REAL_SUPERRES_ENABLED", False)

# 【真·超分放大倍数】整数（2 / 4）。仅真实超分生效；主流 ESRGAN / Real-ESRGAN
#   权重为 4×，自定义权重若为 2× 可改此处。默认 4。
REAL_SUPERRES_SCALE: Final[int] = _env_int("REAL_SUPERRES_SCALE", 4, 1, 8)

# 【真·超分后端】字符串。"auto" 优先 Real-ESRGAN（官方权重）→ 否则 ESRGAN
#   （自定义 .pth）→ 否则 None（回退轻量超分）；也可显式指定 "realesrgan" /
#   "esrgan"。默认 "auto"。
REAL_SUPERRES_BACKEND: Final[str] = _env_str("REAL_SUPERRES_BACKEND", "auto")

# 【真·超分权重路径】字符串。自定义 ESRGAN .pth 权重绝对 / 相对路径；
#   留空则用后端默认的官方权重（realesrgan）或退化为双三次放大（esrgan 无权重时）。
REAL_SUPERRES_MODEL_PATH: Final[str] = _env_str("REAL_SUPERRES_MODEL_PATH", "")

# 【真·超分权重目录】字符串。Real-ESRGAN 等后端自动下载 / 查找权重的目录；
#   默认 "models/superres"（位于项目根）。缺目录时后端按需创建。
REAL_SUPERRES_MODEL_DIR: Final[str] = _env_str("REAL_SUPERRES_MODEL_DIR", "models/superres")


# ==============================================================================
# 第 9.6 节：用户覆盖配置（运行时持久化，阶段 35）
# ------------------------------------------------------------------------------
# 说明：设置页「保存为默认」会把受支持的运行旋钮写入项目根目录下的
#       config.user_overrides.json。目前白名单覆盖三类：
#         · 真实超分三旋钮（总开关 / 放大倍数 / 后端）——阶段 35 引入；
#         · 历史保留策略三旋钮（保留天数 / 最大条数 / 总体积上限）
#           ——阶段 46 引入前两旋钮，阶段 47 引入总体积上限。
#       仅在白名单内的键会被读回，文件缺失 / 损坏 / 含非法值一律静默跳过，
#       绝不拖垮启动。写入采用「合并式」策略：只更新白名单内且校验通过的键，
#       因此保存真实超分默认不会抹掉已保存的保留策略，反之亦然。
#       优先级：环境变量（WME_*）> 用户覆盖 JSON > 代码默认值。
#       （本段代码不依赖 torch，纯标准库，沙箱无 torch 也可完整加载与测试。）
# ==============================================================================

USER_OVERRIDES_PATH: Final[str] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config.user_overrides.json"
)

# 仅允许被用户覆盖持久化的键（白名单），避免任意键被外部 JSON 篡改。
# （阶段 75 扩容：处理偏好四键 + 双跑副引擎——设置页「处理偏好」区可保存为默认。）
_USER_OVERRIDABLE_KEYS: Final[tuple] = (
    "REAL_SUPERRES_ENABLED",
    "REAL_SUPERRES_SCALE",
    "REAL_SUPERRES_BACKEND",
    "CACHE_KEEP_DAYS",
    "CACHE_MAX_RECORDS",
    "CACHE_MAX_BYTES",
    "INPAINT_DUAL_RUN",
    "INPAINT_DUAL_SECONDARY",
    "PNG_KEEP_ALPHA",
    "IMAGE_APPLY_EXIF_ORIENTATION",
    "IMAGE_NORMALIZE_BIT_DEPTH",
)

# 各键的校验器：返回合法值，非法则回退到各自的安全默认。
def _normalize_superres_backend(v) -> str:
    """把超分后端名归一化为小写白名单值；无法识别回退 auto（阶段 80：改为大小写不敏感）。

    修复前的写法是 `str(v) if str(v) in ("auto","esrgan","realesrgan") else "auto"`，
    **不做大小写归一化且不 strip**——用户写 `"ESRGAN"` / `" esrgan "` 会被静默丢弃、
    回退成 "auto"，而同一字典里的 `INPAINT_DUAL_SECONDARY` 却正确地做了 `.lower()`。
    同一份校验表内两种风格属同类不一致，故一并统一。
    """
    text = str(v).strip().lower()
    return text if text in ("auto", "esrgan", "realesrgan") else "auto"


_USER_OVERRIDE_VALIDATORS: Final[dict] = {
    # 布尔项一律走 parse_bool —— 与环境变量路径（_env_bool）共用同一实现。
    # 阶段 80 修复：此前用的是 `bool(v)`，会把字符串 "false"/"0"/"no"/"off"/"关"
    # 全部解释为 **True**，导致用户「关掉」的开关被意外开启（详见 parse_bool 文档）。
    "REAL_SUPERRES_ENABLED": lambda v: parse_bool(v, False),
    "REAL_SUPERRES_SCALE": lambda v: int(v) if int(v) in range(1, 9) else 4,
    "REAL_SUPERRES_BACKEND": _normalize_superres_backend,
    "CACHE_KEEP_DAYS": lambda v: int(v) if int(v) in range(1, 3651) else 30,
    "CACHE_MAX_RECORDS": lambda v: int(v) if int(v) in range(10, 5001) else 200,
    "CACHE_MAX_BYTES": (
        lambda v: int(v)
        if int(v) in range(100 * 1024 * 1024, 100 * 1024 * 1024 * 1024 + 1)
        else 5 * 1024 * 1024 * 1024
    ),
    # ----- 阶段 75：处理偏好 -----
    "INPAINT_DUAL_RUN": lambda v: parse_bool(v, False),
    "INPAINT_DUAL_SECONDARY": (
        lambda v: str(v).strip().lower()
        if str(v).strip().lower() in ("lama", "opencv")
        else "lama"
    ),
    "PNG_KEEP_ALPHA": lambda v: parse_bool(v, True),
    "IMAGE_APPLY_EXIF_ORIENTATION": lambda v: parse_bool(v, True),
    "IMAGE_NORMALIZE_BIT_DEPTH": lambda v: parse_bool(v, True),
}


def load_user_overrides() -> dict:
    """读取并校验用户覆盖配置，返回 {键: 合法值}。缺失/损坏 → 空字典。

    该函数不抛异常：文件不存在、JSON 解析失败、键不在白名单、值非法
    都会被忽略（**但会向 stderr 打印原因**，阶段 80 起），调用方据返回结果决定
    是否覆盖运行时属性。

    说明（阶段 80）：此前非法值被**完全静默**地丢弃，用户遇到「配置写了却不生效」
    时无法自查；现对「校验失败」与「清单缺校验器」两种异常情形打印到 stderr，
    符合「兜底 except 必须留可观测痕迹」的既有经验。文件不存在/JSON 损坏属正常
    缺省场景，仍保持静默。
    """
    try:
        with open(USER_OVERRIDES_PATH, "r", encoding="utf-8") as _f:
            _raw = json.load(_f)
    except Exception:
        return {}
    if not isinstance(_raw, dict):
        return {}
    _out: dict = {}
    for _key in _USER_OVERRIDABLE_KEYS:
        if _key not in _raw:
            continue
        _validator = _USER_OVERRIDE_VALIDATORS.get(_key)
        if _validator is None:
            # 白名单与校验器两份清单分叉时会走到这里 —— 必须留痕，否则该键永远静默失效。
            print(f"[config] 用户覆盖项 {_key} 缺少校验器，已跳过（请检查 "
                  f"_USER_OVERRIDABLE_KEYS 与 _USER_OVERRIDE_VALIDATORS 是否一致）。",
                  file=sys.stderr)
            continue
        try:
            _out[_key] = _validator(_raw[_key])
        except Exception as _exc:  # noqa: BLE001
            print(f"[config] 用户覆盖项 {_key} 的值 {_raw[_key]!r} 校验失败"
                  f"（{type(_exc).__name__}: {_exc}），已忽略。", file=sys.stderr)
            continue
    return _out


def save_user_overrides(patch: dict) -> bool:
    """把用户覆盖配置「合并式」写入项目根 config.user_overrides.json。

    与阶段 35 旧签名 ``save_user_overrides(enabled, scale, backend)`` 不同，
    本函数接收 ``{键: 原始值}`` 字典，并采用「读回已有 -> 仅更新白名单内且
    校验通过的键 -> 写回」的合并策略。因此保存真实超分默认时不会抹掉已保存的
    保留策略，反之亦然；各调用方只需传入自己负责的键。

    参数：
        patch：``{键: 原始值}``；非 dict 或空 dict 直接返回 False。
    返回：
        True 表示写入成功；失败（无写权限等）返回 False，不抛异常。
    """
    if not isinstance(patch, dict) or not patch:
        return False
    # 读回已有覆盖，保证合并而非整体覆盖。
    try:
        with open(USER_OVERRIDES_PATH, "r", encoding="utf-8") as _f:
            _cur = json.load(_f)
        if not isinstance(_cur, dict):
            _cur = {}
    except Exception:
        _cur = {}
    # 仅合并白名单内且校验通过的键；非法值被忽略，绝不污染文件。
    for _key, _val in patch.items():
        if _key not in _USER_OVERRIDABLE_KEYS:
            continue
        _validator = _USER_OVERRIDE_VALIDATORS.get(_key)
        if _validator is None:
            continue
        try:
            _cur[_key] = _validator(_val)
        except Exception:
            continue
    try:
        with open(USER_OVERRIDES_PATH, "w", encoding="utf-8") as _f:
            json.dump(_cur, _f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _apply_user_overrides() -> None:
    """启动时应用持久化的用户覆盖：环境变量优先，其次覆盖 JSON。

    仅对白名单内的键生效；任意异常全部静默吞掉，保证启动不崩。
    """
    try:
        _overrides = load_user_overrides()
    except Exception:
        return
    for _key, _val in _overrides.items():
        # 环境变量（WME_*）显式设置时优先级最高，跳过 JSON 覆盖。
        if os.getenv(ENV_PREFIX + _key) is not None:
            continue
        try:
            globals()[_key] = _val
        except Exception:
            continue


# 模块导入即应用一次用户覆盖（仅覆盖被白名单允许的运行时属性）。
# 注意：本调用被刻意放在 config.py 文件末尾、全部常量定义之后，
#       以确保 CACHE_KEEP_DAYS / CACHE_MAX_RECORDS / CACHE_MAX_BYTES 等晚定义的键
#       在覆盖生效前已存在，避免默认值覆盖掉从 JSON 读回的用户值（阶段 46 加固）。
# （实际调用见文件末尾的 _apply_user_overrides() 调用点。）


# ==============================================================================
# 第 10 节：任务队列与缓存配置（QUEUE & CACHE）
# ==============================================================================

# 【任务队列最大长度】超过此数量的任务会被拒绝入队并提示用户分批处理。
QUEUE_MAX_SIZE: Final[int] = _env_int("QUEUE_MAX_SIZE", 500, 1, 10000)

# 【单个任务失败后的自动重试次数】
#   有些失败是偶发的（例如显存瞬时不足），重试一次往往就能成功。
QUEUE_TASK_RETRY: Final[int] = _env_int("QUEUE_TASK_RETRY", 1, 0, 5)

# 【任务超时时间】单位：秒。单张图片处理超过此时长判定为卡死并终止。
QUEUE_TASK_TIMEOUT: Final[int] = _env_int("QUEUE_TASK_TIMEOUT", 600, 30, 7200)

# 【是否启用处理记录缓存】
#   开启后，同一张图片再次上传时可直接复用上次结果，无需重复计算。
CACHE_ENABLED: Final[bool] = _env_bool("CACHE_ENABLED", True)

# 【缓存记录最大条数】超出后自动删除最旧的记录。
CACHE_MAX_RECORDS: Final[int] = _env_int("CACHE_MAX_RECORDS", 200, 10, 5000)

# 【缓存保留天数】超过此天数的记录在程序启动时自动清理。
CACHE_KEEP_DAYS: Final[int] = _env_int("CACHE_KEEP_DAYS", 30, 1, 3650)

# 【缓存总体积上限（字节）】阶段 47 引入：超过后「按体积清理」从最旧记录开始
#   删除，直至剩余总体积 <= 上限。默认 5 GB；区间 100 MB ~ 100 GB。
#   提示：视频/批量结果往往很大，仅按天数/条数保留可能仍占满磁盘，
#   故增加「体积」这一独立保留维度。
CACHE_MAX_BYTES: Final[int] = _env_int(
    "CACHE_MAX_BYTES", 5 * 1024 * 1024 * 1024, 100 * 1024 * 1024, 100 * 1024 * 1024 * 1024
)

# 【缓存索引文件名】存放在 CACHE_DIR 目录下的 JSON 索引。
CACHE_INDEX_FILE: Final[str] = "history_index.json"

# 【缩略图边长】单位：像素。历史记录列表里显示的小图尺寸。
CACHE_THUMBNAIL_SIZE: Final[int] = _env_int("CACHE_THUMBNAIL_SIZE", 256, 64, 1024)

# 【临时文件自动清理时长】单位：小时。
#   程序启动时会删除 TEMP_DIR 下修改时间早于该时长的文件。
TEMP_CLEAN_HOURS: Final[int] = _env_int("TEMP_CLEAN_HOURS", 24, 1, 720)


# ==============================================================================
# 第 11 节：日志系统配置（LOG）
# ------------------------------------------------------------------------------
# 说明：出问题时，日志是唯一能还原「当时到底发生了什么」的证据。
#       本项目采用「控制台彩色输出 + 文件按天轮转」双通道方案。
# ==============================================================================

# 【日志级别】五选一，从啰嗦到安静依次为：
#   DEBUG    : 打印所有细节，排查疑难问题时使用（日志文件会很大）
#   INFO     : 打印关键流程节点（推荐日常使用）
#   WARNING  : 只打印警告与错误
#   ERROR    : 只打印错误
#   CRITICAL : 只打印导致程序崩溃的致命错误
LOG_LEVEL: Final[str] = _env_str("LOG_LEVEL", "INFO").upper()

# 【控制台日志级别】可与文件日志级别不同。
#   常见配置：控制台只看 INFO，文件里记全量 DEBUG，两不耽误。
LOG_CONSOLE_LEVEL: Final[str] = _env_str("LOG_CONSOLE_LEVEL", "INFO").upper()

# 【主日志文件名】记录全部运行信息。
LOG_FILE_NAME: Final[str] = "app.log"

# 【错误日志文件名】只记录 ERROR 及以上级别，方便快速定位问题。
LOG_ERROR_FILE_NAME: Final[str] = "error.log"

# 【单个日志文件最大体积】单位：MB。超过后自动切分为新文件。
LOG_MAX_SIZE_MB: Final[int] = _env_int("LOG_MAX_SIZE_MB", 10, 1, 500)

# 【日志文件保留份数】超出后自动删除最旧的。
LOG_BACKUP_COUNT: Final[int] = _env_int("LOG_BACKUP_COUNT", 7, 1, 100)

# 【是否在控制台使用彩色输出】
#   Windows 命令行需要 colorama 库支持，未安装时自动降级为无色输出。
LOG_USE_COLOR: Final[bool] = _env_bool("LOG_USE_COLOR", True)

# 【日志格式模板】遵循 Python logging 模块的格式语法。
#   asctime=时间  levelname=级别  name=模块名  lineno=行号  message=内容
LOG_FORMAT: Final[str] = "[%(asctime)s] [%(levelname)-8s] [%(name)s:%(lineno)d] %(message)s"

# 【日志时间格式】
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


# ==============================================================================
# 第 12 节：Gradio 网页服务配置（SERVER）
# ==============================================================================

# 【项目显示名称】显示在浏览器标签页与界面顶部标题栏。
APP_NAME: Final[str] = "AI 智能水印擦除工作台"

# 【项目版本号】
APP_VERSION: Final[str] = "1.0.0"

# 【监听地址】
#   127.0.0.1 : 只有本机能访问（默认，最安全）
#   0.0.0.0   : 局域网内其他设备也能访问（手机、平板可用，但需注意安全）
SERVER_HOST: Final[str] = _env_str("SERVER_HOST", "127.0.0.1")

# 【监听端口】
#   若提示「端口被占用」，改成 7861、8080 等其他数字即可。
SERVER_PORT: Final[int] = _env_int("SERVER_PORT", 7860, 1024, 65535)

# 【端口被占用时是否自动换端口】
#   True  : 自动往后找一个可用端口（推荐，避免零基础用户手足无措）
#   False : 直接报错退出
SERVER_AUTO_PORT: Final[bool] = _env_bool("SERVER_AUTO_PORT", True)

# 【自动换端口的最大尝试次数】
SERVER_PORT_MAX_TRY: Final[int] = _env_int("SERVER_PORT_MAX_TRY", 20, 1, 100)

# 【启动后是否自动打开浏览器】
SERVER_OPEN_BROWSER: Final[bool] = _env_bool("SERVER_OPEN_BROWSER", True)

# 【是否生成 Gradio 公网分享链接】
#   True  : 生成一个 72 小时有效的 xxx.gradio.live 公网地址
#   False : 仅本地访问（默认，强烈推荐）
#   安全警告：开启后任何拿到链接的人都能使用你的电脑处理图片，请谨慎。
SERVER_SHARE: Final[bool] = _env_bool("SERVER_SHARE", False)

# 【界面主题】"brand" = 品牌定制主题（默认；阶段 6 起的统一外观）。
#   也可填 Gradio 内置主题名，临时切换整体外观，例如：
#     soft / default / base / monochrome / glass / origin / citrus / ocean / mario
#   （可用名以当前 Gradio 版本运行时注册表为准，启动日志会打印解析结果）。
#   填其它值：记录 warning 并回退 brand，不会去 HuggingFace 联网下载主题。
#   环境变量：WME_SERVER_THEME
SERVER_THEME: Final[str] = _env_str("SERVER_THEME", "brand")

# 【是否显示 Gradio 底部的 API 文档链接】
#   个人使用场景下关闭可让界面更干净。
SERVER_SHOW_API: Final[bool] = _env_bool("SERVER_SHOW_API", False)

# 【队列最大并发请求数】Gradio 层面的并发控制。
SERVER_MAX_THREADS: Final[int] = _env_int("SERVER_MAX_THREADS", 8, 1, 64)


# ==============================================================================
# 第 12.5 节：REST / 脚本化 API 配置（API）
# ------------------------------------------------------------------------------
# 说明：阶段 9 新增的「零依赖 REST 服务」（server.py）与「程序化库」（api.py）
#       共享以下配置。它们与 Gradio 网页服务相互独立，可单独启用：
#         · 网页界面走 SERVER_HOST / SERVER_PORT（默认 7860）
#         · REST 服务走 API_HOST      / API_PORT   （默认 7861）
#       这样同一台机器上可同时跑「网页界面」和「HTTP 接口」两套服务。
# ==============================================================================

# 【REST 服务监听地址】
#   127.0.0.1 = 仅本机可访问（最安全，默认）；
#   0.0.0.0   = 允许局域网 / 公网访问（有安全风险，务必配合防火墙）。
API_HOST: Final[str] = _env_str("API_HOST", "127.0.0.1")

# 【REST 服务端口】1024~65535。与 Gradio 默认端口 7860 错开，避免冲突。
API_PORT: Final[int] = _env_int("API_PORT", 7861, 1024, 65535)

# 【是否允许启动 REST 服务】
#   True  : 允许 `python cli.py --serve` 或 `python server.py` 启动接口；
#   False : 显式禁用，任何入口启动 REST 都会被拒绝（增强可控性）。
API_ENABLED: Final[bool] = _env_bool("API_ENABLED", True)

# 【REST 返回图片的默认格式】
#   客户端不指定时，接口按此格式返回结果（PNG 无损、兼容好）。
API_DEFAULT_FMT: Final[str] = _env_str("API_DEFAULT_FMT", "PNG").upper()

# 【API 是否要求「全自动擦除」显式确认】（阶段 233 新增，默认 True = 要求确认）
#   背景（阶段 232 审计，真实用户数据驱动）：
#     检测器是**召回优先**设计（红线 precision ≥ 0.050 ⇒ 有意容忍 ≥95% 误检）。
#     这在「AI 照片 + 角落水印」上没问题，但在**文字密集的插画/封面**上会大面积误检：
#     实测某用户封面自动检测出 21 个区域，其中 15 个 `translucent` **全部是误检**
#     （压在标题笔画/标签/人物衣物上），且三条修法（像素判据 / 区域数上限 / 收紧检测）
#     先后被数据否决（详见当日 232 段与 LESSONS #251/#252）。
#     ⇒ 结论：这不是「检测缺陷」，而是**产品预期不符**——检测器的取舍是设计意图。
#     因此修法是**产品层**：让「系统替用户选预设 + 直接擦除」这条**静默**路径
#     不再静默——**默认拒绝**并抛出 `AutoEraseNotConfirmed`，逼调用方二选一：
#       · 显式指定 `preset="..."`（调用方自己做了选择），或
#       · 显式传 `allow_auto_erase=True`（明确承担全自动风险），或
#       · 先调 `detect_watermark_bytes()` 预览掩码、人工确认后再传 `mask`。
#   GUI 不受影响：`ui/` 直接用 `detect_watermark_mask`（本就是「预览→确认→擦除」流程）。
#   置 False 可恢复旧行为（全自动静默擦除），用于兼容既有外部脚本。
API_REQUIRE_AUTO_ERASE_CONFIRM: Final[bool] = _env_bool(
    "API_REQUIRE_AUTO_ERASE_CONFIRM", True)

# 【REST 服务最大并发处理请求数】（阶段 199 · 对应优化清单 D1「并发策略」）
#   为什么需要它：server.py 用 ThreadingHTTPServer，其默认模型是**每个连接开一个线程、
#   不设上限**。而本服务 5 个端点（/api/remove·detect·inpaint·batch）**每一次调用都要跑
#   一次完整推理**（检测 + LaMa 修复）。若无上限，N 个并发请求 = N 个线程同时抢 CPU/显存，
#   单请求时延成倍恶化——这会**直接冲垮阶段 186 立的 P95 性能门禁**（185 清单 D1 原话：
#   「多用户/大图并发时 CPU 争用，可能拖垮 P95 ⇒ 与 A5 互为前提」）。
#   注意：Gradio 侧的 SERVER_MAX_THREADS **不作用于 REST**（那是 Gradio 自己的队列），
#   故 REST 必须有独立的并发闸门，否则就是「配置项名存实亡」的变体。
#   语义：**排队等待**（而非拒绝），保证 P95 可预测；超出后客户端只会变慢，不会报错。
#   取值：0~64 的整数。设 0 = 关闭闸门（恢复无限并发，仅建议受控的本地调试场景）。
#
#   ⚠ 默认值 **4** —— 来自**实测标定**，不是判断值（阶段 207 · RTX 5060 实测）：
#     对 8 张真实基准图**同时**打 /api/remove（全链路：检测 + LaMa 修复），
#     逐档比较吞吐与 P95：
#       · GPU：N=1 → 0.179/s（P95 44.6s）；N=2 → 0.305/s（26.2s）；
#              **N=4 → 0.409/s（19.6s）**；N=8 → 0.395/s（20.2s，已饱和）
#       · CPU：N=1 → 0.099/s（P95 80.7s）；N=2 → 0.141/s（56.8s）；
#              **N=4 → 0.159/s（50.4s）**；N=8 → 0.130/s（61.6s）
#     ⇒ 两种设备模式都指向 **4**：吞吐最高 **且** P95 最低（不存在取舍），
#       相对串行提升 2.28×（GPU）/ 1.61×（CPU）。原默认 2 是阶段 199 的
#       「判断值」——当时只有 CPU torch、无从实测，取保守值合理；现按实测更新。
#     ⚠ 反直觉但重要：**N=1（串行）是最差的**。全链路里 CPU 阶段（检测/后处理）
#       与 GPU 阶段（LaMa）本可**跨请求重叠**，串行把两者强行排成一条线，两边都闲着。
#     ⚠ 该值在 8 GiB 显存（RTX 5060 Laptop）上实测无压力（8 并发亦全部成功）；
#       若你的显存/内存更小、或有交互式界面在同一台机器上争用，可下调此值。
API_MAX_CONCURRENCY: Final[int] = _env_int("API_MAX_CONCURRENCY", 4, 0, 64)

# ------------------------------------------------------------------------------
# 异步作业 worker 池并发数（阶段 D1 / 257 标定）
# ------------------------------------------------------------------------------
# 含义：job_manager.JobManager 的 max_workers——同时「正在执行」的异步长任务
#       （PDF / 视频 / 模型下载）数量上限。这些 worker 线程**在 HTTP 并发闸门之外**
#       （_BoundedThreadingHTTPServer），故不受 API_MAX_CONCURRENCY 约束。
# 标定依据（RTX 5060 Laptop，8123 MiB，2 页 PDF @1200×900，真实跑 detect+inpaint）：
#   k | 墙钟(s) | 成功 | 峰值VRAM(MB) | 单作业均耗时(s) | 聚合吞吐(相对k=1)
#   1 |  12.06  |  1   |    613.8    |     11.86      |  0.166 (1.00×)
#   2 |  10.02  |  2   |    818.2    |      8.62      |  0.399 (2.40×)
#   3 |  13.64  |  3   |   1007.5    |     10.70      |  0.440 (2.65×)
#   4 |  17.09  |  4   |   1272.7    |     12.82      |  0.468 (2.82×)
# 解读：
#   · VRAM 随 k 线性但极轻（k=4 仅占 16%），**非瓶颈**；瓶颈是 GPU 算力争用。
#   · 单请求延迟甜点在 k=2（8.62s，比串行快 27%）；k≥3 起单作业延迟回升
#     （争用），聚合吞吐仍缓增但收益递减（k=3→4 仅 +6% 吞吐、+20% 延迟）。
#   · 本工具定位本地单用户，最相关指标是「我的文件多久好」= 单请求延迟，
#     故取甜点 k=2：延迟最优 + 仅 ~10% VRAM + 视频（逐帧，峰值同量级）仍有大余量。
#   · 若做批量吞吐优先（多用户/队列），可上调至 4（吞吐 +82%、单作业延迟 ~+18%）。
API_ASYNC_MAX_WORKERS: Final[int] = _env_int("API_ASYNC_MAX_WORKERS", 2, 1, 8)

# ------------------------------------------------------------------------------
# API 安全模式（阶段 299 · 你决定：API 鉴权/限流）
# ------------------------------------------------------------------------------
# 总开关：默认关闭 ⇒ 行为与本开关加入前完全一致（仅由 API_MAX_CONCURRENCY 并发闸门
# 保护），既有调用方/测试零影响。置 true 开启「安全模式」：
#   · 所有 POST 端点（remove/detect/inpaint/batch/jobs 提交）+ 作业结果文件下载需 token；
#   · 只读 GET（health/capabilities/models/jobs 状态）豁免鉴权但仍受限流；
#   · 所有请求（GET+POST）受限流保护，防滥用拖垮 P95。
# ⚠ fail-closed：安全模式开启但 API_TOKEN 为空时，start_server 直接拒绝启动
#   （不接收任何请求），避免「开了鉴权却无密钥」的伪安全。
API_AUTH_ENABLED: Final[bool] = _env_bool("API_AUTH_ENABLED", False)

# 【API 共享密钥】安全模式下用于 Bearer 鉴权。强烈建议用环境变量注入，勿硬编码进仓库。
# 为空且 API_AUTH_ENABLED=True ⇒ 服务拒绝启动。
API_TOKEN: Final[str] = _env_str("API_TOKEN", "")

# 【限流 · 每分钟请求数上限】令牌桶「速率」= 本值 / 60 秒；按密钥计（有 token 按 token，
# 否则按客户端 IP）。突发容量见 API_RATE_BURST。
API_RATE_LIMIT_PER_MIN: Final[int] = _env_int("API_RATE_LIMIT_PER_MIN", 60, 1, 100000)

# 【限流 · 突发容量】令牌桶初始/最大令牌数，允许短时突发而不被立即限流。
API_RATE_BURST: Final[int] = _env_int("API_RATE_BURST", 10, 1, 100000)

# ==============================================================================
# 第 13 节：合规声明文本（COMPLIANCE）
# ------------------------------------------------------------------------------
# 说明：这段文本会在程序启动时打印到控制台，并展示在网页界面顶部，
#       属于需求文档中的强制要求项，请勿删除或弱化措辞。
# ==============================================================================

COMPLIANCE_NOTICE: Final[str] = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 合规使用声明 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  本工具专用于去除【使用者本人生成的 AI 图片】上的可见水印 / 署名标识
  （如您自行在图片上添加的水印，或您有权处理的 AI 生成内容）。

  明确禁止的行为：
    · 清除他人享有著作权的作品（摄影、插画、设计稿等）上的水印或署名
    · 去除任何形式的权利管理电子信息用于商用或传播

  不建议的用途：
    · 去除 AI 绘图平台的第三方署名水印（如他人生成内容的平台标识），
      此类行为通常违反对应平台的服务条款，本工具不鼓励该用途。

  法律风险提示：
    依据《中华人民共和国著作权法》第五十三条，未经权利人许可，
    故意删除或者改变作品的权利管理电子信息的，应当承担停止侵害、
    消除影响、赔礼道歉、赔偿损失等民事责任；损害公共利益的，
    可由主管著作权的部门责令停止侵权行为、没收违法所得、
    没收销毁相关设备并处以罚款；构成犯罪的，依法追究刑事责任。

  使用者因违规使用本工具产生的一切法律后果，均由使用者本人承担。
  继续使用即视为您已阅读、理解并同意上述条款。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


# ==============================================================================
# 第 14 节：配置自检与信息导出
# ------------------------------------------------------------------------------
# 说明：提供两个工具函数，供 main.py 在启动时调用，
#       用于在日志里打印一份「当前生效配置快照」，便于用户报错时提供环境信息。
# ==============================================================================

def validate_config() -> list[str]:
    """
    对全部配置项做一次合法性自检。

    返回值：
        问题描述列表。列表为空表示配置全部正常；
        非空则每一项是一句人话描述的警告信息。

    设计意图：
        用户在 .env 里可能填出「逻辑上矛盾」的组合，
        例如分块重叠像素比分块尺寸还大。这种错误不会立刻崩溃，
        但会在运行到一半时抛出难以理解的异常。
        提前自检并给出中文提示，能极大降低排查成本。
    """
    problems: list[str] = []

    # 检查 1：设备模式取值是否合法。
    if DEVICE_MODE not in ("auto", "cuda", "cpu"):
        problems.append(
            f"设备模式 DEVICE_MODE 的值 '{DEVICE_MODE}' 不合法，"
            f"只能是 auto / cuda / cpu 三者之一，程序将按 auto 处理。"
        )

    # 检查 2：修复引擎取值是否合法。
    #   内建引擎：lama / opencv / auto。
    #   其它值视为「自定义插件引擎」：插件在程序启动时加载，
    #   这里无法在导入期确认其是否存在，因此只做软提示，放到运行时再硬性校验。
    if INPAINT_ENGINE not in ("lama", "opencv", "auto"):
        problems.append(
            f"修复引擎 INPAINT_ENGINE 的值 '{INPAINT_ENGINE}' 不是内建选项"
            f"(lama / opencv / auto)。若它是自定义插件引擎，请确认对应插件已放在 "
            f"插件目录（PLUGIN_DIRS）并可被正确发现；否则程序在真正修复时会报错。"
        )

    # 检查 2.5：模型槽清单（MODEL_SLOTS）结构是否合法（仅软提示）。
    #   模型槽在运行时由 model_manager 注册，这里只校验配置结构，
    #   无法确认权重文件是否存在，因此错误仅告警、不阻断启动。
    if isinstance(MODEL_SLOTS, list):
        for _i, _slot in enumerate(MODEL_SLOTS):
            if not isinstance(_slot, dict) or not _slot.get("name"):
                problems.append(
                    f"MODEL_SLOTS 第 {_i} 项不是合法的模型槽（需为含 name 字段的 dict），"
                    f"已忽略该项。"
                )
            elif not isinstance(_slot.get("filename"), str) or not _slot["filename"]:
                problems.append(
                    f"模型槽 '{_slot.get('name')}' 缺少 filename 字段，已忽略。"
                )
    elif MODEL_SLOTS not in (None,):
        problems.append("MODEL_SLOTS 必须是 list[dict]，已忽略。")

    # 检查 3：OpenCV 修复算法取值是否合法。
    if OPENCV_INPAINT_METHOD not in ("telea", "ns"):
        problems.append(
            f"OpenCV 修复算法 OPENCV_INPAINT_METHOD 的值 "
            f"'{OPENCV_INPAINT_METHOD}' 不合法，只能是 telea / ns，"
            f"程序将按 telea 处理。"
        )

    # 检查 4：分块重叠不能大于等于分块尺寸的一半，否则会陷入死循环。
    if TILE_OVERLAP >= TILE_SIZE // 2:
        problems.append(
            f"分块重叠 TILE_OVERLAP({TILE_OVERLAP}) 不应大于等于 "
            f"分块尺寸 TILE_SIZE({TILE_SIZE}) 的一半，"
            f"否则切图会无限循环。建议设为 {TILE_SIZE // 8}。"
        )

    # 检查 5：羽化半径必须是奇数（高斯模糊内核的硬性要求）。
    if MASK_FEATHER_DEFAULT > 0 and MASK_FEATHER_DEFAULT % 2 == 0:
        problems.append(
            f"边缘羽化 MASK_FEATHER_DEFAULT({MASK_FEATHER_DEFAULT}) "
            f"必须是奇数或 0，程序将自动调整为 {MASK_FEATHER_DEFAULT + 1}。"
        )

    # 检查 6：默认输出格式必须在支持清单里。
    if DEFAULT_OUTPUT_FORMAT not in SUPPORTED_OUTPUT_FORMATS:
        problems.append(
            f"默认输出格式 DEFAULT_OUTPUT_FORMAT 的值 "
            f"'{DEFAULT_OUTPUT_FORMAT}' 不受支持，"
            f"可选值为 {', '.join(SUPPORTED_OUTPUT_FORMATS)}，"
            f"程序将按 PNG 处理。"
        )

    # 检查 7：日志级别取值是否合法。
    valid_levels: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    if LOG_LEVEL not in valid_levels:
        problems.append(
            f"日志级别 LOG_LEVEL 的值 '{LOG_LEVEL}' 不合法，"
            f"可选值为 {', '.join(valid_levels)}，程序将按 INFO 处理。"
        )

    # 检查 8：自动检测的最小面积不能大于最大面积。
    if DETECT_MIN_AREA_RATIO >= DETECT_MAX_AREA_RATIO:
        problems.append(
            f"水印检测的最小面积占比({DETECT_MIN_AREA_RATIO}) 不应大于等于 "
            f"最大面积占比({DETECT_MAX_AREA_RATIO})，否则任何区域都会被过滤掉。"
        )

    # 检查 9：显卡模式下并发数过高的性能提醒。
    if DEVICE_MODE == "cuda" and BATCH_CONCURRENCY > 2:
        problems.append(
            f"显卡模式下批量并发数 BATCH_CONCURRENCY({BATCH_CONCURRENCY}) "
            f"设置偏高，显存争抢可能导致速度不升反降甚至溢出，建议设为 1。"
        )

    # 检查 10（阶段 10 新增）：PDF 页面图片格式必须是 PNG 或 JPEG。
    if PDF_PAGE_IMAGE_FMT not in ("PNG", "JPEG", "JPG"):
        problems.append(
            f"PDF 页面图片格式 PDF_PAGE_IMAGE_FMT 的值 "
            f"'{PDF_PAGE_IMAGE_FMT}' 不受支持，只能是 PNG 或 JPEG，"
            f"程序将按 JPEG 处理。"
        )

    # 检查 11（阶段 10 新增）：PDF 渲染 DPI 过高的性能提醒。
    if PDF_RENDER_DPI > 300:
        problems.append(
            f"PDF 渲染精度 PDF_RENDER_DPI({PDF_RENDER_DPI}) 偏高，"
            f"单页图像可能超过 5000 像素，处理会非常慢且吃内存，"
            f"日常使用建议 150。"
        )

    # 检查 12（阶段 10 新增）：视频编码器候选清单不能为空。
    if not VIDEO_CODEC_CANDIDATES:
        problems.append(
            "视频编码器候选 VIDEO_CODEC_CANDIDATES 为空，"
            "视频功能将无法写出文件，请至少保留 'mp4v'。"
        )

    # 检查 13（阶段 10 新增）：抽帧步长 > 1 却关闭掩码复用，属于矛盾配置。
    if VIDEO_FRAME_STEP > 1 and not VIDEO_REUSE_MASK:
        problems.append(
            f"抽帧步长 VIDEO_FRAME_STEP({VIDEO_FRAME_STEP}) 大于 1 时，"
            f"必须开启掩码复用 VIDEO_REUSE_MASK，否则中间帧无掩码可用、"
            f"会被原样输出（水印不会被去掉）。程序将按每帧检测处理。"
        )

    # 检查 14（阶段 49 新增）：相对显著倍数过低会让「过度检测」缺陷复现。
    #   DETECT_SIGMA_K < 1.5 时，合成诊断集上的误报率会重新升到 8% 以上，
    #   等于把整幅纹理丰富的照片标成水印，修复后画面将被大面积重绘。
    if DETECT_SIGMA_K < 1.5:
        problems.append(
            f"相对显著倍数 DETECT_SIGMA_K({DETECT_SIGMA_K}) 设置过低，"
            f"检测器会把大量纹理误判为水印（过度检测），建议不低于 1.5。"
        )

    # 检查 15（阶段 49 新增）：全局覆盖率上限过低会误杀正常的整图水印场景。
    #   ai_gradient 预设按设计会整图标记；上限设到 0.05 以下时它会被几乎清空。
    if DETECT_MAX_COVERAGE_RATIO < 0.10:
        problems.append(
            f"全局覆盖率上限 DETECT_MAX_COVERAGE_RATIO"
            f"({DETECT_MAX_COVERAGE_RATIO}) 设置过低，"
            f"正常的整图水印（如全局渐变）会被裁掉，建议不低于 0.10。"
        )

    # 检查 16（阶段 86 新增）：白底圆角板块判据的取值自洽性。
    #   面积区间反了会让判据恒不产出（静默失效）；明度/饱和度区间组合过窄
    #   会让「够亮且够灰」的像素集合接近空集，同样静默失效。
    if DETECT_PLATE_MIN_AREA_RATIO >= DETECT_PLATE_MAX_AREA_RATIO:
        problems.append(
            f"白牌判据最小面积占比({DETECT_PLATE_MIN_AREA_RATIO}) 不应大于等于 "
            f"最大面积占比({DETECT_PLATE_MAX_AREA_RATIO})，否则白底圆角角标永远"
            f"检不出来（静默漏检）。"
        )
    if DETECT_PLATE_V_MIN > 254:
        problems.append(
            f"白牌判据明度下限 DETECT_PLATE_V_MIN({DETECT_PLATE_V_MIN}) 超过 254，"
            f"HSV 明度取值范围为 0~255，该判据将恒不命中。"
        )
    if DETECT_PLATE_MAX_ASPECT < 1.0:
        problems.append(
            f"白牌判据最大宽高比 DETECT_PLATE_MAX_ASPECT"
            f"({DETECT_PLATE_MAX_ASPECT}) 小于 1.0，任何区域都会被判为过长。"
        )

    # 检查 17（阶段 95 新增）：承诺面非空。
    #   承诺面 `WATERMARK_TYPE_COMMITTED` 由 `WATERMARK_TYPE_SCOPE` 派生，
    #   是真实图门禁「承诺类型必须有真值正向证据」这条判定的**输入**。
    #   若它退化成空集，那条判定会**空转**——没有任何类型被检查，
    #   门禁却照常报绿（典型的「遍历式守卫空转」，经验 54）。
    #   承诺面为空属于结构性错误，必须显式拦住而不是静默通过。
    if not WATERMARK_TYPE_COMMITTED:
        problems.append(
            "承诺面 WATERMARK_TYPE_COMMITTED 为空：WATERMARK_TYPE_SCOPE 里没有任何"
            "条目把 committed 设为 True，真实图门禁的「承诺类型必须有真值证据」"
            "判定将无事可守（门禁会静默变绿）。请至少登记一个 committed 类型。"
        )

    # 检查 18（阶段 145 新增）：**成对约束的自动对账**（元检查）。
    #   背景（阶段 145 元审计）：本函数原有 17 项检查里，只有检查 8（通用面积占比）
    #   与检查 16（白牌面积占比）覆盖了「MIN ≥ MAX ⇒ 判据恒不产出」这一类。
    #   用命名空间**自动枚举**全部成对项后实测：**11 对里只有 2 对被覆盖**，
    #   漏检的 9 对包括网格覆盖率、LOGO 面积、CANNY 双阈值、推荐器网格区间 ——
    #   而 GRID / LOGO 与 PLATE 属**完全同族**的判据（都是「区间反了 ⇒ 静默漏检」），
    #   检查 16 的注释甚至已经把这条道理写明，却**没有推广到同族**。
    #   ⇒ 采用**元检查**而不是逐条补：从本模块命名空间自动发现约束，
    #     因此未来新增任何 `X_MIN/X_MAX`、`X_MIN_Y/X_MAX_Y`、`X_LO/X_HI`
    #     都会**自动纳入**（经验 167：期望值必须从真值来源派生，不能手抄 ——
    #     手抄清单的失效方式是静默的）。
    #   注：与检查 8/16 的报告**可能重叠**。刻意不排除：重复告警是「多」（无害），
    #   漏检是「少」（有害），此处宁可重复。
    _ns = globals()
    _pair_specs: list = []
    for _name in list(_ns):
        if _name.startswith("_"):
            continue
        if _name.endswith("_MIN"):
            _hi = _name[:-4] + "_MAX"
            if _hi in _ns:
                _pair_specs.append((_name, _hi, "下限/上限"))
        if "_MIN_" in _name:
            # `X_MIN_Y` / `X_MAX_Y` 形态（如 DETECT_PLATE_MIN_AREA_RATIO）。
            # 刻意用字符串切分而不是正则 —— 本模块只 import 了 json/os/sys/pathlib/typing，
            # 为一条自检引入 `re` 属不必要的依赖面扩大。
            _pre, _suf = _name.split("_MIN_", 1)
            _hi = f"{_pre}_MAX_{_suf}"
            if _hi in _ns:
                _pair_specs.append((_name, _hi, "下限/上限"))
        if _name.endswith("_LO"):
            _hi = _name[:-3] + "_HI"
            if _hi in _ns:
                _pair_specs.append((_name, _hi, "双阈值"))
    for _lo_name, _hi_name, _kind in _pair_specs:
        _lo, _hi = _ns[_lo_name], _ns[_hi_name]
        if isinstance(_lo, bool) or isinstance(_hi, bool):
            continue
        if not isinstance(_lo, (int, float)) or not isinstance(_hi, (int, float)):
            continue
        if _lo >= _hi:
            problems.append(
                f"配置项 {_lo_name}({_lo}) 不应大于等于 {_hi_name}({_hi})"
                f"（{_kind}区间反了）：依赖该区间的判据将**恒不产出**"
                f"（静默漏检）或滑杆行为未定义，请把它们调回正常顺序。"
            )

    # 检查 19（阶段 145 新增）：滑杆三元组 `X_DEFAULT / X_MIN / X_MAX` 的自洽性。
    #   三者是**三处独立字面量** —— DEFAULT 的钳制区间写在 `_env_*` 调用处
    #   （如 `_env_int("MASK_DILATE_DEFAULT", 0, 0, 100)`），MIN/MAX 常量另写一行
    #   ⇒ 改一处忘另一处时，DEFAULT 会落在 [MIN, MAX] 之外，UI 滑杆初值越界
    #   （Gradio 对越界初值的行为未定义），而本函数**报绿**。
    #   实测本仓库现有 5 组三元组**全部自洽** —— 本条是**防未来漂移**的守卫。
    for _dname in [n for n in _ns if n.endswith("_DEFAULT") and not n.startswith("_")]:
        _base = _dname[:-len("_DEFAULT")]
        _mn, _mx = _base + "_MIN", _base + "_MAX"
        if _mn not in _ns or _mx not in _ns:
            continue
        _dv, _vmn, _vmx = _ns[_dname], _ns[_mn], _ns[_mx]
        if isinstance(_dv, bool) or not isinstance(_dv, (int, float)):
            continue
        if not isinstance(_vmn, (int, float)) or not isinstance(_vmx, (int, float)):
            continue
        if not (_vmn <= _dv <= _vmx):
            problems.append(
                f"默认值 {_dname}({_dv}) 落在其合法区间 "
                f"[{_mn}({_vmn}), {_mx}({_vmx})] 之外：UI 滑杆初值会越界，"
                f"请同步 `_env_*` 的钳制参数与 MIN/MAX 常量（三处字面量需一致）。"
            )

    return problems


def get_config_snapshot() -> dict[str, Any]:
    """
    导出一份当前生效的关键配置快照。

    返回值：
        字典。键为中文说明，值为对应的配置值（已转为字符串便于打印）。

    使用场景：
        · 程序启动时写入日志开头，作为本次运行的环境记录
        · 用户报错时，让其提供这份快照，可快速判断是否为配置问题
    """
    return {
        "项目名称": APP_NAME,
        "项目版本": APP_VERSION,
        "项目根目录": str(PROJECT_ROOT),
        "是否打包运行": str(bool(getattr(sys, "frozen", False))),
        "已加载 .env 文件": str(_DOTENV_LOADED),
        "模型文件路径": str(MODEL_PATH),
        "模型文件是否存在": str(MODEL_PATH.exists()),
        "输出目录": str(OUTPUT_DIR),
        "日志目录": str(LOG_DIR),
        "设备模式": DEVICE_MODE,
        "修复引擎": INPAINT_ENGINE,
        "启用大图分块": str(ENABLE_TILING),
        "分块尺寸": str(TILE_SIZE),
        "推理最大边长": str(INFER_MAX_SIDE),
        "进度节流间隔(秒)": str(PROGRESS_THROTTLE_SEC),
        "空缓存节流间隔(秒)": str(EMPTY_CACHE_MIN_INTERVAL_SEC),
        "设备复用": str(PERF_REUSE_DEVICE),
        "全局拖拽上传": str(UI_GLOBAL_DRAGDROP),
        "键盘快捷键": str(UI_SHORTCUTS_ENABLED),
        "结果对比视图": str(UI_COMPARE_VIEW),
        "对比查看器默认模式": UI_COMPARE_DEFAULT_MODE,
        "对比渲染最大边长": str(UI_COMPARE_MAX_SIDE),
        "界面语言": UI_LANGUAGE,
        "底部居中搜索区": str(DETECT_SEARCH_BOTTOM),
        "平铺半透明增强": str(DETECT_ENHANCE_TILED),
        "掩码精修默认模式": MASK_EDIT_DEFAULT_MODE,
        "精修画笔默认尺寸": str(MASK_EDIT_BRUSH_DEFAULT),
        "网格平铺检测": str(DETECT_GRID_ENABLED),
        "全局渐变检测": str(DETECT_GRADIENT_ENABLED),
        "Logo 轮廓检测": str(DETECT_LOGO_ENABLED),
        "Logo 最小实心率": str(DETECT_LOGO_SOLIDITY),
        "Logo 最大面积占比": str(DETECT_LOGO_MAX_AREA_RATIO),
        # 阶段 100：角落 MSER 降级通路（默认关闭）
        "角落 MSER 降级通路": str(DETECT_CORNER_MSER_ENABLED),
        # 阶段 86：白底圆角板块判据
        "白底圆角板块判据": str(DETECT_PLATE_ENABLED),
        "白牌明度下限": str(DETECT_PLATE_V_MIN),
        "白牌饱和度上限": str(DETECT_PLATE_S_MAX),
        "白牌最小实心度": str(DETECT_PLATE_SOLIDITY),
        "白牌最小墨迹密度": str(DETECT_PLATE_INK_MIN),
        "白牌最大面积占比": str(DETECT_PLATE_MAX_AREA_RATIO),
        "白牌最小边长": str(DETECT_PLATE_MIN_SIDE),
        "网格最小自相关强度": str(DETECT_GRID_MIN_STRENGTH),
        "网格最小覆盖占比": str(DETECT_GRID_MIN_COVERAGE),
        # 阶段 106：折叠模板门限余量倍数（默认 1.3）
        "网格模板门限倍数": str(DETECT_GRID_TMPL_K_MULT),
        "渐变最小方差解释率": str(DETECT_GRADIENT_MIN_EXPLAINED),
        "OCR 最小置信度": str(DETECT_OCR_MIN_CONFIDENCE),
        "OCR 预处理放大": str(DETECT_OCR_UPSCALE),
        "默认检测预设": DETECT_PRESET_DEFAULT,
        # 阶段 25：检测自动预设推荐
        "预设推荐总开关": str(RECOMMEND_ENABLED),
        "推荐默认回退预设": RECOMMEND_DEFAULT_PRESET,
        "推荐渐变最小解释率": str(RECOMMEND_GRADIENT_MIN_EXPLAINED),
        "推荐网格最小自相关强度": str(RECOMMEND_GRID_MIN_STRENGTH),
        "推荐平铺最小覆盖占比": str(RECOMMEND_TILED_MIN_COVERAGE),
        "推荐极淡平铺残差": str(RECOMMEND_FAINT_RESIDUAL),
        "推荐实体Logo最小实心率": str(RECOMMEND_LOGO_SOLIDITY),
        "推荐实体Logo最小边长占比": str(RECOMMEND_LOGO_MIN_SIDE_FRACTION),
        "推荐标记绝对残差下限": str(RECOMMEND_MARKS_ABS_MIN),
        "推荐标记稳健σ倍数": str(RECOMMEND_MARKS_SIGMA_K),
        "推荐文字密度阈值": str(RECOMMEND_TEXT_DENSITY),
        "批量并发数": str(BATCH_CONCURRENCY),
        "默认导出格式": DEFAULT_OUTPUT_FORMAT,
        "默认导出画质": str(DEFAULT_OUTPUT_QUALITY),
        "日志级别": LOG_LEVEL,
        "监听地址": f"{SERVER_HOST}:{SERVER_PORT}",
        "生成公网链接": str(SERVER_SHARE),
        "PDF 渲染精度": f"{PDF_RENDER_DPI} DPI",
        "PDF 最大页数": str(PDF_MAX_PAGES),
        "视频最大帧数": str(VIDEO_MAX_FRAMES),
        "视频抽帧步长": str(VIDEO_FRAME_STEP),
        "插件目录": ", ".join(str(p) for p in PLUGIN_DIRS),
        "已启用插件": ", ".join(PLUGIN_ENABLED) if PLUGIN_ENABLED else "全部",
        "插件覆盖保护": str(not PLUGIN_ALLOW_OVERRIDE),
        "当前模型槽": ACTIVE_MODEL,
        "额外模型槽数": str(len(MODEL_SLOTS) if isinstance(MODEL_SLOTS, list) else 0),
        "自动发现模型": str(MODEL_AUTO_DISCOVER),
        "解码缓存启用": str(IMAGE_DECODE_CACHE_ENABLED),
        "解码缓存最大条目": str(IMAGE_DECODE_CACHE_MAX_ENTRIES),
        "解码缓存单图像素上限": str(IMAGE_DECODE_CACHE_MAX_PIXELS_SINGLE),
        "质量评估降采样边长": str(QUALITY_MAX_EVAL_SIDE),
        "报告含质量评估": str(REPORT_INCLUDE_QUALITY),
        "报告对比图最大边长": str(REPORT_COMPARE_MAX_SIDE),
        "PDF每页记录数": str(REPORT_PDF_RECORDS_PER_PAGE),
        "后处理总开关": str(POSTPROCESS_ENABLED),
        "后处理·锐化": str(POSTPROCESS_SHARPEN),
        "后处理·锐化强度": str(POSTPROCESS_SHARPEN_AMOUNT),
        "后处理·色彩还原": str(POSTPROCESS_COLOR_MATCH),
        "后处理·色彩还原强度": str(POSTPROCESS_COLOR_MATCH_STRENGTH),
        "后处理·边缘融合": str(POSTPROCESS_EDGE_BLEND),
        "后处理·边缘融合带宽": str(POSTPROCESS_EDGE_BLEND_BAND),
        "后处理·Poisson融合": str(POSTPROCESS_POISSON_BLEND),
        "后处理·Poisson融合强度": str(POSTPROCESS_POISSON_BLEND_STRENGTH),
        "后处理·纹理重建": str(POSTPROCESS_TEXTURE_TRANSFER),
        "后处理·纹理重建强度": str(POSTPROCESS_TEXTURE_TRANSFER_AMOUNT),
        "后处理·伪影抑制": str(POSTPROCESS_ARTIFACT_REDUCE),
        "后处理·伪影抑制强度": str(POSTPROCESS_ARTIFACT_REDUCE_AMOUNT),
        "后处理·轻量超分": str(POSTPROCESS_SUPERRES_ENABLED),
        "后处理·轻量超分强度": str(POSTPROCESS_SUPERRES_AMOUNT),
        "真·超分·总开关": str(REAL_SUPERRES_ENABLED),
        "真·超分·放大倍数": str(REAL_SUPERRES_SCALE),
        "真·超分·后端": str(REAL_SUPERRES_BACKEND),
        "真·超分·权重路径": str(REAL_SUPERRES_MODEL_PATH) or "(默认)",
        "真·超分·权重目录": str(REAL_SUPERRES_MODEL_DIR),
    }


# ==============================================================================
# 附录 A：能力声明 × 验证覆盖（阶段 93 · 单一真值来源）
# ------------------------------------------------------------------------------
# 为什么需要这张表（docs/92 缺口 S4）：
#   README 把 `ai_gradient` 列为「5 大支持类型」之一，而 `scripts/real_gate.py`
#   自述它「设计上恒为零输出」，且它**不在** `scripts/quality_gate.py` 的合成用例集
#   （`CASES`）里。**文档承诺与门禁自述互相矛盾**——而矛盾是靠人眼读出来的，
#   没有任何自动化会因此失败。
#
# ⚠ 本表**只登记真正新增的信息**，不复制已有的名字来源：
#   · 合成用例名 / 用例中文名 → 唯一来源 `scripts/make_demo_assets.CASES`
#     （`quality_gate.py` 直接 from-import 它，不另建一份）
#   · 检测模式显示名        → 唯一来源 `core.detection_presets` 的 `display`
#   · **真值 style 名**      → 只有本表登记。这是唯一"两边都缺"的映射：
#                             `faint_tiled` 是**合成用例名**，真值侧对应的是
#                             `translucent`；两者若混用，标注计数会静默恒为 0
#                             （看起来像"没标"，实为"名字写错"）。
#     故本表不重写上面两类名字——测试负责断言**本表键集合**与那两处一致
#     （见 `tests/test_watermark_scope.py`）。
#
# 消费方与守卫（`tests/test_watermark_scope.py`）：
#   ① 本表键 == `make_demo_assets.CASES` 的预设名 ∪ {known_gap}，其中
#      `known_gap = {"ai_gradient"}` 是**显式钉住的已知缺口**：一旦为它补了合成用例，
#      测试失败 → 强制把声明表与 README 一起更新（而不是让文档停留在旧说法）；
#   ② 本表键 ⊆ `core.detection_presets` 注册表（不能声明一个不存在的预设）；
#   ③ README「支持的水印类型」清单 ⊆ 本表键集合（**结构上杜绝承诺比证据多**）；
#   ④ 每个 `truth_styles` 取值都在真值枚举内；真值里出现的 style 都被本表覆盖；
#   ⑤ （阶段 95）`committed` 与 UI 文案 / README 验证状态列一致：
#      README 的「验证状态」列必须与承诺面同进同退，UI 适用范围提示里出现的
#      类型标签集合必须恰好等于承诺面（守卫见 `tests/test_watermark_scope95.py`）。
#
# 结论性事实（2026-09-17 实测，阶段 93；阶段 95 收窄承诺面）：5 类里只有 `ai_logo`
#   同时具备「合成用例 + 真实标注 + 红线」。阶段 95 据此把**承诺面**收敛为
#   `{ai_logo}` 一类，其余四类显式登记为「不承诺」——于是 README 不再需要
#   逐行堆「未验证」来免责，`scripts/real_gate.py --strict-scope` 也从
#   「结构性红灯」变为可长期为绿的有效门槛。
# ==============================================================================

#: 已知缺口：有对外声明、但**没有合成正向用例**的预设。显式列出而非留白——
#: 留白会被读成「忘了」，正面写下才是「已确认没有」。
WATERMARK_TYPE_KNOWN_SYNTHETIC_GAP: Final[frozenset] = frozenset({"ai_gradient"})

#: 五个预设的登记表。每个条目四个字段，各有唯一消费者（**不复制已有名字来源**）：
#:
#:   truth_styles  → 真值 `style` 名映射（只有本表登记，见上方说明）
#:   committed     → **承诺级别**（阶段 95）
#:                     True  = 对外承诺处理该类：`scripts/real_gate.py` 要求它
#:                             **必须有真值正向证据**，缺了直接硬失败
#:                     False = **显式声明不承诺**。这不是「忘了」，也不是「不支持」
#:                             ——预设仍可选用，只是不对效果做任何承诺，
#:                             且不参与「标注覆盖面是否完整」的判定
#:   label_zh/en   → 给人看的类型名。**唯一消费者是 UI 适用范围提示**
#:                     （`ui/components.py:render_scope_note`），文案里出现了哪些
#:                     类型名因此成为**可机器判定的事实**：
#:                     `tests/test_watermark_scope95.py` 断言「文案中出现的标签集合
#:                     == committed 集合的标签集合」，双向都守——漏写承诺类型、
#:                     或把非承诺类型写成承诺，都会让测试失败。
#:                     （这正是经验 51「人读副本」的通解：副本可以存在，
#:                       但必须有一个机器可判定的守卫把它钉回单一真值来源。）
#:
#: ⚠ 承诺面**不在此处另立一份清单**：`WATERMARK_TYPE_COMMITTED` 由本表派生。
#:   若另写一份「承诺了哪些」的集合，就会出现「同一语义两套实现」，
#:   改一处忘一处时门禁与文案会各说各话（经验 24/77）。
WATERMARK_TYPE_SCOPE: Final[dict] = {
    "ai_faint_tiled": {"truth_styles": ("translucent", "overlay"),
                       "committed": False, "label_zh": "平铺文字", "label_en": "tiled text"},
    "ai_logo": {"truth_styles": ("corner_logo", "logo_watermark"),
                "committed": True, "label_zh": "角标 LOGO", "label_en": "corner logo"},
    "ai_text_heavy": {"truth_styles": ("edge_text",),
                      "committed": False, "label_zh": "边缘小字", "label_en": "edge text"},
    "ai_grid": {"truth_styles": ("grid_watermark",),
                "committed": False, "label_zh": "网格点阵", "label_en": "grid"},
    "ai_gradient": {"truth_styles": ("gradient_watermark",),
                    "committed": False, "label_zh": "全局渐变", "label_en": "gradient"},
}

#: **承诺面**（阶段 95）：由 `WATERMARK_TYPE_SCOPE` 派生，刻意不另写一份。
#: 现阶段 = {"ai_logo"}（用户决定：只处理角标 LOGO 类图片）。
WATERMARK_TYPE_COMMITTED: Final[frozenset] = frozenset(
    _n for _n, _e in WATERMARK_TYPE_SCOPE.items() if _e.get("committed")
)


# ==============================================================================
# 阶段 243 · **图像级**已知检测缺口（`corner_logo` 承诺面内的子风格缺口）
# ==============================================================================
# 背景（243 探针实测，详见 docs/243）：
#   `ai_logo`（= 真值 style `corner_logo`）是唯一承诺面，但它的实测样本里混着
#   **两种形态截然不同**的角标：
#     · 「小文字牌」340x128（`ai_logo_*` 10 张 + q 系列 11 张）——检测器**擅长**，
#       逐图 mask_recall 0.14~0.95、剔除大角标后 micro=0.511（达标）；
#     · 「大颗多面体三角宝石角标」340x420~620x510（`a1`~`a4` 4 张）——检测器
#       **几乎完全漏检**（逐图 mask_recall 0.0~0.045），且这 4 张占承诺面真值
#       面积 **52.6%**，把 micro 拉到 0.2461 ⇒ `real_gate --strict-scope` 恒红。
#   根因（探针 6 否决了全部纯像素路线）：宝石角标与彩色自然背景在像素域**不可分**
#     —— `a2`（蓝宝石）**整图**就是一个饱和连通域（背景与角标颜色融为一体）；且
#     去水印原图（clean `a1;.png`~`a4;.png`）的角落连通域与有水印图**几乎一样**
#     ⇒「贴角+饱和+实心」在无水印对照上同样成立。纯色度/实心度/贴角等判据均
#     要么漏检要么吞掉自然景（与阶段 232 教训同构）。
#
# 为什么是**图像级**而非改 `WATERMARK_TYPE_SCOPE`：
#   `a1`~`a4` 的真值 style **确实是 `corner_logo`**（它们就是豆包的多面体角标），
#   改 style 会**篡改真值**。缺口发生在 `corner_logo` **子风格**层，比现有
#   「预设/类型」粒度更细 ⇒ 只能以图像级显式登记。
#
# 处置口径（同 `ai_gradient` 的 KNOWN_GAP 惯例）：**显式钉死缺口**，让能力声明与
# 检测器真实能力对齐，而不是把红线往下移来掩盖。缺口**不静默丢弃**——门禁报告
# 会原样列出这些图及其单图指标（见 `scripts/real_gate.py` 的 known-gap 段）。
#: 已知检测缺口图像（承诺面内、检测器**尚未**能处理的子风格）。
#: ⚠ 这是**诚实的负能力声明**，不是「忘了标注」——留白会被读成漏标。
WATERMARK_KNOWN_GAP_IMAGES: Final[frozenset] = frozenset({"a1", "a2", "a3", "a4"})


def watermark_type_label(name: str, lang: str = "zh-CN") -> str:
    """
    取某个预设**给人看**的类型名（阶段 95）。

    参数：
        name : 预设名（`WATERMARK_TYPE_SCOPE` 的键）。
        lang : 语言代码；以 "en" 开头取英文名，否则取中文名。

    返回值：
        类型名；未登记的白名单外取值**回退为预设名本身**（不抛异常）——
        这样新增预设但忘了补标签时，界面上出现的仍是可检索的预设名，
        而不是空串或异常（与 `utils/i18n.t_in` 的缺失键回退同风格）。
    """
    entry = WATERMARK_TYPE_SCOPE.get(name) or {}
    key = "label_en" if str(lang).lower().startswith("en") else "label_zh"
    return str(entry.get(key) or name)


# ==============================================================================
# 第 15 节：模块自测入口
# ------------------------------------------------------------------------------
# 说明：直接运行 python config.py 时，会打印全部配置并执行自检，
#       这是零基础用户验证「环境是否搭好」最快的方式。
# ==============================================================================

if __name__ == "__main__":
    # 先确保目录存在，顺便验证磁盘写权限是否正常。
    newly_created: list[Path] = ensure_directories()

    print("=" * 78)
    print(f"  {APP_NAME}  v{APP_VERSION}  —— 配置自检工具")
    print("=" * 78)
    print(COMPLIANCE_NOTICE)

    print("【一】当前生效的关键配置")
    print("-" * 78)
    for _key, _value in get_config_snapshot().items():
        # 使用中文对齐宽度 16，让输出整齐易读。
        print(f"  {_key:<16} : {_value}")

    print()
    print("【二】目录创建情况")
    print("-" * 78)
    if newly_created:
        for _dir in newly_created:
            print(f"  [已创建] {_dir}")
    else:
        print("  所有必需目录均已存在，无需创建。")

    print()
    print("【三】配置合法性自检结果")
    print("-" * 78)
    _problems: list[str] = validate_config()
    if not _problems:
        print("  通过。未发现配置问题。")
    else:
        for _index, _problem in enumerate(_problems, start=1):
            print(f"  [{_index}] {_problem}")

    print()
    print("=" * 78)
    print("  自检结束。若第三项全部通过，说明配置无误，可继续后续步骤。")
    print("=" * 78)

    # 阶段 147：**判定结果必须影响退出码**。
    #   本自检此前把 `validate_config()` 的结果**只打印出来**、不改变退出码，
    #   整块 21 条语句里**连一个 `assert` 都没有**。于是 `validate_config` 一旦
    #   开始报问题（例如新增的元检查误报、或成对约束被改坏），本自检**照样以 0 退出**
    #   ⇒ 子进程守卫（`tests/test_legacy_selfcheck.py` 只断 `rc == 0`）**永远绿**。
    #   这是「哑巴自检」最隐蔽的形态：**读了真值来源、做了判定，却没让判定结果
    #   影响退出码** —— 它看起来比「只 print 一句」充实得多，因此更难看穿。
    #   现改为有问题即 `SystemExit(1)`；打印保持原样，用户友好性不受影响。
    if _problems:
        raise SystemExit(
            f"配置自检未通过：{len(_problems)} 项问题（详见上方【三】的清单）。")


# 模块导入即应用一次用户覆盖（仅覆盖被白名单允许的运行时属性）。
# 放在文件末尾：确保所有常量（含 CACHE_KEEP_DAYS / CACHE_MAX_RECORDS 等晚定义键）
# 已就位后再覆盖，避免默认值在覆盖生效前「反客为主」（阶段 46 加固）。
_apply_user_overrides()
